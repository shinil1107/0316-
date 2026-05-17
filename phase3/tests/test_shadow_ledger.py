"""Regression tests for ``phase3/shadow_ledger.py``.

Covers:
    * Pure helpers (``_safe_float``, ``_safe_int``, ``_sanitize_label``,
      ``_date_from_run_id``, ``_regime_top_n``, ``_load_scores``,
      ``_score_signature``, ``_resolve_initial_path``).
    * Stateful in-memory helpers (``BuyGraceState``, ``_clone_portfolio``,
      ``_mark_to_market``).
    * Comparison / metric builders (``_metrics``, ``_build_nav_compare``,
      ``_build_holdings_compare``).
    * Pair discovery (``_discover_pairs`` — label filter / date range /
      duplicate-score dedup).
    * End-to-end smoke for ``_run_replay_job`` with
      ``generate_recommendations`` and ``build_engine_cfg`` monkey-patched
      so the test stays decoupled from the heavy live-engine config.

The smoke test deliberately stubs the recommendation generator to
return an empty DataFrame, so no synthetic trades fire. That keeps the
fixture small while still exercising the full pair discovery → ledger
fork → output writer pipeline. NAV preservation under "no-trade" is
itself a useful invariant.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

# Make the phase3 package importable both as a flat module
# (``shadow_ledger``, ``simulator``) and as a sub-package
# (``phase3.tests.test_shadow_ledger``) regardless of how unittest
# discovery enters us.
_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
_ROOT = _PHASE3.parent
for _p in (str(_PHASE3), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-shadow-tests")

import shadow_ledger as sl  # noqa: E402
from simulator import SimPortfolio  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────
class TestSafeCoerce(unittest.TestCase):
    def test_safe_float_handles_invalid(self):
        self.assertEqual(sl._safe_float("1.5"), 1.5)
        self.assertEqual(sl._safe_float(None, default=0.0), 0.0)
        self.assertEqual(sl._safe_float("abc", default=-1.0), -1.0)
        self.assertEqual(sl._safe_float(float("nan"), default=7.0), 7.0)
        self.assertEqual(sl._safe_float(float("inf"), default=7.0), 7.0)

    def test_safe_int_handles_invalid(self):
        self.assertEqual(sl._safe_int("3"), 3)
        self.assertEqual(sl._safe_int(2.9), 2)
        self.assertEqual(sl._safe_int(None, default=0), 0)
        self.assertEqual(sl._safe_int("abc", default=-1), -1)
        self.assertEqual(sl._safe_int(pd.NA, default=5), 5)


class TestSanitizeAndLabels(unittest.TestCase):
    def test_sanitize_label_replaces_specials(self):
        self.assertEqual(sl._sanitize_label("P11/FUNDB ANCHOR!"), "P11_FUNDB_ANCHOR")
        self.assertEqual(sl._sanitize_label("A.B-C_d"), "A.B-C_d")
        self.assertEqual(sl._sanitize_label("   ___   "), "shadow")
        self.assertEqual(sl._sanitize_label(""), "shadow")

    def test_date_from_run_id(self):
        self.assertEqual(sl._date_from_run_id("20260517_153012_shadow"), "2026-05-17")
        self.assertEqual(sl._date_from_run_id("20260517"), "2026-05-17")
        # Unparseable falls back to the raw 8-char prefix.
        self.assertEqual(sl._date_from_run_id("nope1234"), "nope1234")


class TestRegimeTopN(unittest.TestCase):
    def test_regime_top_n_pulls_engine_cfg_attrs(self):
        cfg = SimpleNamespace(regime_bull_top_n=42, regime_defensive_top_n=8,
                              regime_side_top_n=17)
        self.assertEqual(sl._regime_top_n(cfg, "BULL"), 42)
        self.assertEqual(sl._regime_top_n(cfg, "DEFENSIVE"), 8)
        self.assertEqual(sl._regime_top_n(cfg, "CRASH"), 8)
        self.assertEqual(sl._regime_top_n(cfg, "BEAR"), 8)
        self.assertEqual(sl._regime_top_n(cfg, "SIDE"), 17)
        self.assertEqual(sl._regime_top_n(cfg, "UNKNOWN"), 17)

    def test_regime_top_n_falls_back_when_attr_missing(self):
        cfg = SimpleNamespace()
        self.assertEqual(sl._regime_top_n(cfg, "BULL"), 20)
        self.assertEqual(sl._regime_top_n(cfg, "DEFENSIVE"), 10)
        self.assertEqual(sl._regime_top_n(cfg, "SIDE"), 15)


# ──────────────────────────────────────────────────────────────────────
# Scores I/O + signature
# ──────────────────────────────────────────────────────────────────────
class TestLoadScores(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with TemporaryDirectory() as tmp:
            df = sl._load_scores(Path(tmp) / "missing.csv")
            self.assertTrue(df.empty)
            self.assertListEqual(list(df.columns), ["Ticker", "Score", "Price", "Regime"])

    def test_invalid_prices_filtered_and_sorted(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "scores.csv"
            pd.DataFrame([
                {"Ticker": "AAA", "Score": 0.5, "Price": 10.0, "Regime": "SIDE"},
                {"Ticker": "BBB", "Score": 0.9, "Price": -1.0, "Regime": "SIDE"},
                {"Ticker": "CCC", "Score": np.nan, "Price": 5.0, "Regime": "SIDE"},
                {"Ticker": "DDD", "Score": 0.8, "Price": 12.0, "Regime": "SIDE"},
            ]).to_csv(p, index=False)
            df = sl._load_scores(p)
            # BBB filtered out by Price<=0, CCC by Score=NaN.
            self.assertListEqual(df["Ticker"].tolist(), ["DDD", "AAA"])


class TestScoreSignature(unittest.TestCase):
    def test_signature_is_deterministic_and_empty_safe(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.csv"
            self.assertEqual(sl._score_signature(missing), "")

            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            df = pd.DataFrame([
                {"Ticker": "AAA", "Score": 0.5, "Price": 10.0, "Regime": "SIDE"},
                {"Ticker": "BBB", "Score": 0.4, "Price": 11.0, "Regime": "SIDE"},
            ])
            df.to_csv(a, index=False)
            df.to_csv(b, index=False)
            self.assertEqual(sl._score_signature(a), sl._score_signature(b))

    def test_signature_changes_when_scores_change(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            pd.DataFrame([{"Ticker": "AAA", "Score": 0.5, "Price": 10.0, "Regime": "SIDE"}]).to_csv(a, index=False)
            pd.DataFrame([{"Ticker": "AAA", "Score": 0.6, "Price": 10.0, "Regime": "SIDE"}]).to_csv(b, index=False)
            self.assertNotEqual(sl._score_signature(a), sl._score_signature(b))


# ──────────────────────────────────────────────────────────────────────
# Portfolio cloning + buy-grace
# ──────────────────────────────────────────────────────────────────────
class TestClonePortfolio(unittest.TestCase):
    def test_clone_is_independent(self):
        p = SimPortfolio(10_000.0)
        p.holdings["AAA"] = {
            "shares": 5, "avg_cost": 100.0, "current_price": 110.0,
            "entry_date": "2026-05-01", "entry_price": 100.0,
            "entry_score": 0.5, "entry_rank": 1, "entry_regime": "SIDE",
            "peak_price": 110.0, "last_score": 0.5,
            "profit_targets_hit": {0.10},
        }
        clone = sl._clone_portfolio(p)

        # Mutating the clone must not bleed back.
        clone.cash = 0.0
        clone.holdings["AAA"]["shares"] = 0
        clone.holdings["AAA"]["profit_targets_hit"].add(0.20)
        clone.holdings["BBB"] = {"shares": 1, "avg_cost": 5.0,
                                 "current_price": 6.0, "entry_date": "",
                                 "entry_price": 5.0, "entry_score": 0.0,
                                 "entry_rank": -1, "entry_regime": "",
                                 "peak_price": 6.0, "last_score": 0.0,
                                 "profit_targets_hit": set()}

        self.assertEqual(p.cash, 10_000.0)
        self.assertEqual(p.holdings["AAA"]["shares"], 5)
        self.assertEqual(p.holdings["AAA"]["profit_targets_hit"], {0.10})
        self.assertNotIn("BBB", p.holdings)


class TestBuyGraceState(unittest.TestCase):
    def _scores(self, tickers):
        return pd.DataFrame({
            "Ticker": tickers,
            "Score": [1.0 - i * 0.01 for i in range(len(tickers))],
            "Price": [10.0 + i for i in range(len(tickers))],
            "Regime": ["SIDE"] * len(tickers),
        })

    def test_grace_zero_means_no_block(self):
        state = sl.BuyGraceState()
        scores = self._scores(["A", "B", "C", "D"])
        cfg = SimpleNamespace(regime_side_top_n=2)
        blocked = state.blocked_tickers(scores, SimPortfolio(0.0), cfg, "SIDE",
                                        strategy_conf={"buy_grace_days": 0})
        self.assertEqual(blocked, set())

    def test_grace_filters_non_persistent_ticker(self):
        state = sl.BuyGraceState()
        cfg = SimpleNamespace(regime_side_top_n=2)
        strat = {"buy_grace_days": 2}

        # Day 1 — empty history → guard ``len(history) >= grace_days`` (2)
        # fails → no block; top-2 = {A, B} is appended.
        b1 = state.blocked_tickers(self._scores(["A", "B"]),
                                    SimPortfolio(0.0), cfg, "SIDE", strat)
        self.assertEqual(b1, set())

        # Day 2 — history length 1 < grace_days 2 → still no block; top-2 of
        # the new scores ({A, C}) is appended.
        b2 = state.blocked_tickers(self._scores(["A", "C", "X"]),
                                    SimPortfolio(0.0), cfg, "SIDE", strat)
        self.assertEqual(b2, set())

        # Day 3 — history now length 2 == grace_days. Intersection of the
        # last 2 daily top-2 sets is {A}. All scored = {A, D, X}. Blocked
        # = scored - persistent = {D, X}; A is grace-protected.
        b3 = state.blocked_tickers(self._scores(["A", "D", "X"]),
                                    SimPortfolio(0.0), cfg, "SIDE", strat)
        self.assertIn("D", b3)
        self.assertIn("X", b3)
        self.assertNotIn("A", b3)
        self.assertGreater(state.filtered_total, 0)
        self.assertEqual(state.filter_days, 1)

    def test_history_capped_at_64(self):
        state = sl.BuyGraceState()
        cfg = SimpleNamespace(regime_side_top_n=2)
        scores = self._scores(["A", "B"])
        for _ in range(80):
            state.blocked_tickers(scores, SimPortfolio(0.0), cfg, "SIDE",
                                  strategy_conf={"buy_grace_days": 1})
        self.assertEqual(len(state.history), 64)


# ──────────────────────────────────────────────────────────────────────
# Metrics + compare builders
# ──────────────────────────────────────────────────────────────────────
class TestMetrics(unittest.TestCase):
    def test_empty_metrics_returns_zeros(self):
        m = sl._metrics(pd.DataFrame())
        self.assertEqual(m["final_nav"], 0.0)
        self.assertEqual(m["total_return_pct"], 0.0)
        self.assertEqual(m["max_drawdown_pct"], 0.0)
        self.assertEqual(m["trade_count"], 0)

    def test_metrics_return_and_drawdown(self):
        df = pd.DataFrame([
            {"Date": "2026-05-08", "NAV": 100.0, "PositionCount": 0,
             "BuyCount": 0, "SellCount": 0, "TrimCount": 0,
             "TradeCount": 0, "TurnoverPct": 0.0},
            {"Date": "2026-05-09", "NAV": 120.0, "PositionCount": 1,
             "BuyCount": 1, "SellCount": 0, "TrimCount": 0,
             "TradeCount": 1, "TurnoverPct": 5.0},
            {"Date": "2026-05-10", "NAV": 90.0, "PositionCount": 1,
             "BuyCount": 0, "SellCount": 0, "TrimCount": 0,
             "TradeCount": 0, "TurnoverPct": 0.0},
            {"Date": "2026-05-11", "NAV": 110.0, "PositionCount": 1,
             "BuyCount": 0, "SellCount": 0, "TrimCount": 0,
             "TradeCount": 0, "TurnoverPct": 0.0},
        ])
        m = sl._metrics(df)
        self.assertEqual(m["start_nav"], 100.0)
        self.assertEqual(m["final_nav"], 110.0)
        self.assertAlmostEqual(m["total_return_pct"], 10.0, places=4)
        # Peak hit 120 on day 2; trough 90 on day 3 → DD = 90/120-1 = -25%.
        self.assertAlmostEqual(m["max_drawdown_pct"], -25.0, places=4)
        self.assertEqual(m["trade_count"], 1)
        self.assertEqual(m["buy_count"], 1)
        self.assertEqual(m["final_position_count"], 1)


class TestNavCompare(unittest.TestCase):
    def _daily(self, dates, navs):
        return pd.DataFrame([
            {"Date": d, "NAV": n, "DailyReturn": 0.0, "PositionCount": 1,
             "TradeCount": 0, "TurnoverPct": 0.0}
            for d, n in zip(dates, navs)
        ])

    def test_empty_inputs_return_canonical_empty_frame(self):
        out = sl._build_nav_compare(pd.DataFrame(), pd.DataFrame())
        self.assertTrue(out.empty)
        self.assertIn("NavDelta", out.columns)
        self.assertIn("PositionDelta", out.columns)

    def test_outer_join_and_deltas(self):
        b = self._daily(["2026-05-08", "2026-05-09"], [100.0, 110.0])
        s = self._daily(["2026-05-08", "2026-05-09", "2026-05-10"], [100.0, 115.0, 120.0])
        out = sl._build_nav_compare(b, s)
        self.assertEqual(len(out), 3)
        d09 = out[out["Date"] == "2026-05-09"].iloc[0]
        self.assertAlmostEqual(float(d09["NavDelta"]), 5.0)
        d10 = out[out["Date"] == "2026-05-10"].iloc[0]
        self.assertTrue(np.isnan(d10["BaselineNAV"]))


class TestHoldingsCompare(unittest.TestCase):
    def _holdings(self, rows):
        return pd.DataFrame(rows, columns=["Ticker", "Shares", "MarketValue", "Weight"])

    def test_holdings_compare_overlap_and_outer(self):
        b = self._holdings([("AAA", 5, 500.0, 0.5), ("BBB", 3, 300.0, 0.3)])
        s = self._holdings([("AAA", 2, 200.0, 0.4), ("CCC", 1, 100.0, 0.2)])
        out = sl._build_holdings_compare(b, s)
        tickers = set(out["Ticker"].tolist())
        self.assertEqual(tickers, {"AAA", "BBB", "CCC"})
        bbb = out[out["Ticker"] == "BBB"].iloc[0]
        self.assertEqual(int(bbb["BaselineShares"]), 3)
        self.assertEqual(int(bbb["ShadowShares"]), 0)
        ccc = out[out["Ticker"] == "CCC"].iloc[0]
        self.assertEqual(int(ccc["BaselineShares"]), 0)
        self.assertEqual(int(ccc["ShadowShares"]), 1)
        aaa = out[out["Ticker"] == "AAA"].iloc[0]
        self.assertEqual(int(aaa["ShareDelta"]), -3)

    def test_holdings_compare_handles_both_empty(self):
        out = sl._build_holdings_compare(pd.DataFrame(), pd.DataFrame())
        self.assertTrue(out.empty)
        for col in ("Ticker", "BaselineShares", "ShadowShares",
                    "MarketValueDelta", "WeightDelta"):
            self.assertIn(col, out.columns)


# ──────────────────────────────────────────────────────────────────────
# Initial portfolio seeding + initial-path resolution
# ──────────────────────────────────────────────────────────────────────
class TestSeedPortfolio(unittest.TestCase):
    def test_seed_from_portfolio_before(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "portfolio_before.csv"
            pd.DataFrame([
                {"Ticker": "AAA", "Shares": 10, "BuyPrice": 100.0,
                 "CurrentPrice": 120.0, "MarketValue": 1200.0,
                 "BuyDate": "2026-04-01", "EntryScore": 0.5,
                 "EntryRank": 1, "EntryRegime": "BULL", "PeakPrice": 130.0,
                 "LastScore": 0.45, "ProfitTargetsHit": "[0.05, 0.10]"},
                {"Ticker": "BBB", "Shares": 0, "BuyPrice": 50.0,
                 "CurrentPrice": 55.0, "MarketValue": 0.0,
                 "BuyDate": "", "EntryScore": 0.0, "EntryRank": -1,
                 "EntryRegime": "", "PeakPrice": 0.0,
                 "LastScore": 0.0, "ProfitTargetsHit": ""},
            ]).to_csv(p, index=False)

            port = sl._seed_portfolio_from_current(p, total_capital=10_000.0)
            self.assertIn("AAA", port.holdings)
            self.assertNotIn("BBB", port.holdings)
            self.assertEqual(port.holdings["AAA"]["shares"], 10)
            self.assertEqual(port.holdings["AAA"]["profit_targets_hit"],
                             {0.05, 0.10})
            self.assertEqual(port.cash, 10_000.0 - 1200.0)

    def test_seed_respects_override_cash(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "portfolio_before.csv"
            pd.DataFrame(columns=["Ticker", "Shares", "BuyPrice"]).to_csv(p, index=False)
            port = sl._seed_portfolio_from_current(p, total_capital=10_000.0,
                                                   override_cash=2_500.0)
            self.assertEqual(port.cash, 2_500.0)

    def test_seed_missing_file_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                sl._seed_portfolio_from_current(Path(tmp) / "missing.csv",
                                                 total_capital=10_000.0)


class TestResolveInitialPath(unittest.TestCase):
    def test_absolute_path_returned_as_is(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "init.csv"
            p.touch()
            pair = sl.ArtifactPair(
                run_id="x", run_date="2026-05-08", baseline_dir=Path(tmp),
                shadow_dir=Path(tmp), label="L", regime="SIDE",
                vix_close=20.0, signature="",
            )
            out = sl._resolve_initial_path(str(p), pair, Path(tmp))
            self.assertEqual(out, p)

    def test_no_override_uses_first_pair_baseline(self):
        with TemporaryDirectory() as tmp:
            pair = sl.ArtifactPair(
                run_id="x", run_date="2026-05-08", baseline_dir=Path(tmp),
                shadow_dir=Path(tmp), label="L", regime="SIDE",
                vix_close=20.0, signature="",
            )
            out = sl._resolve_initial_path(None, pair, Path(tmp))
            self.assertEqual(out, Path(tmp) / "portfolio_before.csv")


# ──────────────────────────────────────────────────────────────────────
# Pair discovery
# ──────────────────────────────────────────────────────────────────────
class TestDiscoverPairs(unittest.TestCase):
    def _make_pair(self, root: Path, run_id: str, label: str, date: str,
                   scores_payload, with_diff: bool = True):
        baseline = root / f"{run_id}_daily"
        shadow = root / f"{run_id}_shadow"
        baseline.mkdir(parents=True, exist_ok=True)
        shadow.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(scores_payload).to_csv(baseline / "scores.csv", index=False)
        pd.DataFrame(scores_payload).to_csv(shadow / "scores.csv", index=False)
        if with_diff:
            (shadow / "shadow_diff_summary.json").write_text(
                json.dumps({"label": label, "date": date}), encoding="utf-8")

    def test_label_filter_and_date_range(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_pair(root, "20260508_120000", "L1", "2026-05-08",
                            [{"Ticker": "A", "Score": 1.0, "Price": 10.0, "Regime": "SIDE"}])
            self._make_pair(root, "20260509_120000", "L1", "2026-05-09",
                            [{"Ticker": "A", "Score": 1.0, "Price": 11.0, "Regime": "SIDE"}])
            self._make_pair(root, "20260510_120000", "OTHER", "2026-05-10",
                            [{"Ticker": "B", "Score": 0.9, "Price": 9.0, "Regime": "SIDE"}])

            out = sl._discover_pairs(root, "L1", "2026-05-09", "2026-05-09",
                                      include_duplicate_scores=False)
            self.assertEqual(len(out), 1)
            # ``run_id`` mirrors the shadow directory name (``*_shadow``).
            self.assertEqual(out[0].run_id, "20260509_120000_shadow")
            self.assertEqual(out[0].run_date, "2026-05-09")

    def test_dedup_duplicate_score_signatures(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = [{"Ticker": "A", "Score": 1.0, "Price": 10.0, "Regime": "SIDE"}]
            self._make_pair(root, "20260508_120000", "L1", "2026-05-08", payload)
            self._make_pair(root, "20260509_120000", "L1", "2026-05-09", payload)

            dedup = sl._discover_pairs(root, "L1", None, None,
                                        include_duplicate_scores=False)
            kept = sl._discover_pairs(root, "L1", None, None,
                                       include_duplicate_scores=True)
            self.assertEqual(len(dedup), 1)
            self.assertEqual(len(kept), 2)

    def test_skips_when_baseline_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "20260508_120000_shadow"
            shadow.mkdir(parents=True)
            pd.DataFrame([{"Ticker": "A", "Score": 1.0, "Price": 10.0, "Regime": "SIDE"}]).to_csv(
                shadow / "scores.csv", index=False)
            (shadow / "shadow_diff_summary.json").write_text(
                json.dumps({"label": "L1", "date": "2026-05-08"}), encoding="utf-8")
            out = sl._discover_pairs(root, "L1", None, None,
                                      include_duplicate_scores=False)
            self.assertEqual(out, [])


# ──────────────────────────────────────────────────────────────────────
# End-to-end smoke for _run_replay_job (recos stubbed → NAV preserved)
# ──────────────────────────────────────────────────────────────────────
class TestRunReplayJobSmoke(unittest.TestCase):
    """End-to-end pipeline through fake artifacts, with the recommendation
    engine stubbed out to return no actions. With no trades, both ledgers
    should track NAV = initial_cash exactly across all days, which lets us
    assert the replay writer and pair-discovery layer without depending on
    the heavy live ``generate_recommendations`` config surface."""

    def _scaffold(self, root: Path, run_id: str, label: str, date: str,
                  prices: dict) -> None:
        baseline = root / f"{run_id}_daily"
        shadow = root / f"{run_id}_shadow"
        baseline.mkdir(parents=True, exist_ok=True)
        shadow.mkdir(parents=True, exist_ok=True)
        scores = pd.DataFrame([
            {"Ticker": t, "Score": s, "Price": p, "Regime": "SIDE"}
            for t, (s, p) in prices.items()
        ])
        scores.to_csv(baseline / "scores.csv", index=False)
        scores.to_csv(shadow / "scores.csv", index=False)
        (shadow / "shadow_diff_summary.json").write_text(
            json.dumps({"label": label, "date": date}), encoding="utf-8")
        (baseline / "run_meta.json").write_text(
            json.dumps({"total_capital": 10000.0, "cash_balance": 10000.0,
                        "regime": "SIDE", "vix_close": 18.0}),
            encoding="utf-8")
        # Empty starting portfolio_before.csv (all-cash seed).
        pd.DataFrame(columns=["Ticker", "Shares", "BuyPrice", "CurrentPrice",
                              "MarketValue"]).to_csv(
            baseline / "portfolio_before.csv", index=False)

    def test_end_to_end_no_trade_preserves_nav(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_runs = root / "daily_runs"
            daily_runs.mkdir()
            output_root = root / "shadow_ledgers"

            self._scaffold(daily_runs, "20260508_120000", "L1", "2026-05-08",
                           {"A": (1.0, 10.0), "B": (0.9, 20.0)})
            self._scaffold(daily_runs, "20260509_120000", "L1", "2026-05-09",
                           {"A": (1.0, 11.0), "B": (0.9, 22.0)})

            config_path = root / "config.yaml"
            config_path.write_text(yaml.safe_dump({
                "paths": {"output_dir": str(root)},
                "portfolio": {"total_capital": 10000.0, "daily_buy_limit": 1000.0},
                "strategy": {"buy_grace_days": 0},
                "shadow": {"label": "L1", "start_date": "2026-05-08"},
            }), encoding="utf-8")

            # Stub the heavy engine surface.
            stub_cfg = SimpleNamespace(
                regime_bull_top_n=10, regime_defensive_top_n=5,
                regime_side_top_n=8,
            )
            calls = {"recos": 0}

            def stub_recos(*_args, **_kwargs):
                calls["recos"] += 1
                return pd.DataFrame()

            orig_build = sl.build_engine_cfg
            orig_recos = sl.generate_recommendations
            sl.build_engine_cfg = lambda _conf: stub_cfg
            sl.generate_recommendations = stub_recos
            try:
                argv = [
                    "update-latest",
                    "--config", str(config_path),
                    "--daily-runs-dir", str(daily_runs),
                    "--output-root", str(output_root),
                    "--min-runs", "1",
                    "--commission-bps", "10",
                    "--slippage-bps", "5",
                    "--shadow-label", "L1",
                ]
                args = sl.build_parser().parse_args(argv)
                rc = sl.update_latest(args)
            finally:
                sl.build_engine_cfg = orig_build
                sl.generate_recommendations = orig_recos

            self.assertEqual(rc, 0)
            self.assertGreaterEqual(calls["recos"], 2)  # 1 per ledger × 2 days.

            pointer_path = output_root / "L1" / "latest_pointer.json"
            self.assertTrue(pointer_path.exists())
            ptr = json.loads(pointer_path.read_text(encoding="utf-8"))
            self.assertEqual(ptr["shadow_label"], "L1")
            self.assertEqual(ptr["latest_date"], "2026-05-09")
            self.assertAlmostEqual(ptr["baseline"]["final_nav"], 10000.0, places=2)
            self.assertAlmostEqual(ptr["shadow"]["final_nav"], 10000.0, places=2)
            # No trades → NAV delta exactly zero.
            self.assertEqual(ptr["comparison"]["shadow_minus_baseline_final_nav"], 0.0)

            replay_dir = Path(ptr["latest_replay_dir"])
            for name in ("baseline_daily_nav.csv", "shadow_daily_nav.csv",
                         "nav_compare.csv", "compare_summary.json",
                         "compare_summary.md", "holdings_compare_final.csv"):
                self.assertTrue((replay_dir / name).exists(), f"missing {name}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
