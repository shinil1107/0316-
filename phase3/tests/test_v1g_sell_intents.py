"""V1-G — Unit tests for the SELL intent path.

V1-G lifts the BUY-only invariant baked into ``intents_io`` /
``daily_runner`` so the unattended pipeline can submit STOP_LOSS /
SELL / TRIM intents from T7. The tests below pin every contract
that combination must satisfy so a future refactor doesn't
silently re-introduce the BUY-only block:

* ``_validate_row`` accepts side ∈ {BUY, SELL} and rejects others.
* ``build_intent_client_order_id`` carries the side marker
  (``-B-`` vs ``-S-``) through to the cid string.
* ``make_sell_intent_row`` writes ``side="SELL"`` into the canonical
  row shape and trips _validate_row on the same failure paths
  that ``make_buy_intent_row`` does.
* ``load_sell_candidates`` extracts STOP_LOSS / SELL / TRIM rows
  out of a synthetic ``recommendations.csv`` and **does NOT**
  include SELL_GRACE (warning-only state).
* ``sell_candidates_to_intent_rows`` prices the limit OFF THE BID
  (not the ask) and pads DOWN (not up) so the resulting SELL is
  marketable.
* ``run_generate_intents`` produces a merged BUY+SELL
  submitted_intents.json and returns rc=0 when at least one side
  has rows — including the "zero BUYs but one STOP_LOSS" case
  that is a normal forced-exit-only trading day.
* ``daily_runner.default_intents_loader`` preserves the row's
  ``side`` instead of forcing BUY (the last layer of the old
  BUY-only invariant).
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import intents_io  # noqa: E402
from phase3.autotrade import v1_runner   # noqa: E402


_RECS_FIELDS = (
    "RunId", "Date", "Actionable", "RecRowId", "ScoringDate",
    "Ticker", "Action", "TargetPct", "ActualPct", "GapPct",
    "Score", "Price", "Shares", "Capital", "Regime",
    "GraceCount", "Rank",
)


def _write_recs(
    run_dir: Path,
    rows: List[dict],
) -> Path:
    """Helper — write a minimal but schema-compliant recommendations.csv
    that ``load_buy_candidates`` / ``load_sell_candidates`` can read."""
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "recommendations.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_RECS_FIELDS)
        w.writeheader()
        for r in rows:
            full = {k: r.get(k, "") for k in _RECS_FIELDS}
            w.writerow(full)
    return p


def _reco_row(
    run_id: str, row_id: int, ticker: str, action: str,
    shares: int, price: float, rank: int = 1,
) -> dict:
    return {
        "RunId": run_id, "Date": "2026-05-28", "Actionable": "True",
        "RecRowId": str(row_id), "ScoringDate": "2026-05-27",
        "Ticker": ticker, "Action": action,
        "TargetPct": "0.0", "ActualPct": "0.0", "GapPct": "0.0",
        "Score": "0.5", "Price": f"{price:.4f}", "Shares": str(shares),
        "Capital": "0.0", "Regime": "BULL", "GraceCount": "0",
        "Rank": str(rank),
    }


# ──────────────────────────────────────────────────────────────────
# 1. _validate_row + make_sell_intent_row
# ──────────────────────────────────────────────────────────────────
class TestSideValidation(unittest.TestCase):
    """The BUY-only invariant pin moves from `side == 'BUY'` to
    `side in {'BUY', 'SELL'}`. These tests lock the new contract."""

    def _row(self, **overrides: Any) -> dict:
        base = {
            "client_order_id": "co-test-1-S-2-COR",
            "symbol": "COR", "market": "NASD", "side": "SELL",
            "qty": 2, "ord_type": "LIMIT", "limit_price": 266.5,
        }
        base.update(overrides)
        return base

    def test_sell_row_passes_validation(self):
        ok, why = intents_io._validate_row(self._row(side="SELL"))
        self.assertTrue(ok, f"SELL should validate; got {why!r}")

    def test_buy_row_still_passes(self):
        ok, _ = intents_io._validate_row(self._row(side="BUY"))
        self.assertTrue(ok)

    def test_invalid_side_rejected(self):
        for side in ("STOP_LOSS", "TRIM", "", "buy_more", "  "):
            ok, why = intents_io._validate_row(self._row(side=side))
            self.assertFalse(ok, f"side={side!r} should be rejected")
            self.assertIn("side", why.lower())

    def test_lowercase_sell_passes(self):
        """``_validate_row`` upper-cases before comparing — defensive
        against operators hand-editing files with mixed case."""
        ok, _ = intents_io._validate_row(self._row(side="sell"))
        self.assertTrue(ok)

    def test_market_order_still_rejected_on_sell(self):
        """Lifting BUY-only must NOT also lift the LIMIT-only pin."""
        ok, why = intents_io._validate_row(
            self._row(side="SELL", ord_type="MARKET"))
        self.assertFalse(ok)
        self.assertIn("LIMIT", why)


class TestMakeSellIntentRow(unittest.TestCase):
    """Construction helper — same shape as BUY, just side='SELL'."""

    def test_happy_path(self):
        row = intents_io.make_sell_intent_row(
            client_order_id="co-r1-3-S-5-AAPL",
            symbol="aapl", qty=5, limit_price=189.50,
        )
        self.assertEqual(row["side"], "SELL")
        self.assertEqual(row["symbol"], "AAPL")  # upper-cased
        self.assertEqual(row["qty"], 5)
        self.assertEqual(row["limit_price"], 189.50)
        self.assertEqual(row["ord_type"], "LIMIT")

    def test_rec_row_id_round_trip_from_S_cid(self):
        """``rec_row_id_from_client_order_id`` already walks back from
        either 'B' or 'S'; this asserts the SELL cid is parsable."""
        rid = intents_io.rec_row_id_from_client_order_id(
            "co-run01-42-S-2-COR")
        self.assertEqual(rid, 42)

    def test_validates_via_same_path_as_buy(self):
        with self.assertRaises(ValueError):
            intents_io.make_sell_intent_row(
                client_order_id="co-x-1-S-0-COR",
                symbol="COR", qty=0,        # qty=0 trips _validate_row
                limit_price=1.0,
            )


class TestBuildIntentClientOrderId(unittest.TestCase):
    def test_buy_marker(self):
        cid = intents_io.build_intent_client_order_id(
            run_id="r1", rec_row_id=3, ticker="AAPL", qty=5,
            side="BUY")
        self.assertIn("-B-", cid)
        self.assertNotIn("-S-", cid)

    def test_sell_marker(self):
        cid = intents_io.build_intent_client_order_id(
            run_id="r1", rec_row_id=3, ticker="COR", qty=2,
            side="SELL")
        self.assertIn("-S-", cid)
        self.assertNotIn("-B-", cid)

    def test_default_is_buy_for_back_compat(self):
        """All pre-V1-G call sites omit the side kwarg; default must
        stay BUY so existing tests / scripts don't drift."""
        cid = intents_io.build_intent_client_order_id(
            run_id="r1", rec_row_id=3, ticker="AAPL", qty=5)
        self.assertIn("-B-", cid)

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            intents_io.build_intent_client_order_id(
                run_id="r1", rec_row_id=3, ticker="X", qty=1,
                side="SHORT")


# ──────────────────────────────────────────────────────────────────
# 2. load_sell_candidates
# ──────────────────────────────────────────────────────────────────
class TestLoadSellCandidates(unittest.TestCase):
    """Recommendation-CSV → SellCandidate filter."""

    def test_picks_stop_loss_sell_trim(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "COR",  "STOP_LOSS", 2, 267.01,
                          rank=1),
                _reco_row("r1", 2, "AAPL", "SELL",      4, 180.00,
                          rank=2),
                _reco_row("r1", 3, "MSFT", "TRIM",      1, 400.00,
                          rank=3),
            ])
            cands = intents_io.load_sell_candidates(rd)
        self.assertEqual(
            sorted(c.ticker for c in cands),
            ["AAPL", "COR", "MSFT"])

    def test_excludes_sell_grace(self):
        """SELL_GRACE is a warning state — never an actionable sell."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "TPL",  "SELL_GRACE", 7, 406.09),
                _reco_row("r1", 2, "EIX",  "SELL_GRACE", 9, 71.66),
                _reco_row("r1", 3, "AAPL", "SELL",       1, 180.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
        self.assertEqual([c.ticker for c in cands], ["AAPL"])

    def test_excludes_buys(self):
        """Cross-contamination guard — load_sell never returns BUYs."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "NVDA", "BUY_NEW",   3, 120.0),
                _reco_row("r1", 2, "COR",  "STOP_LOSS", 2, 267.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
        self.assertEqual([c.ticker for c in cands], ["COR"])

    def test_zero_shares_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "FOO", "STOP_LOSS", 0, 10.0),
                _reco_row("r1", 2, "BAR", "SELL",      0, 20.0),
                _reco_row("r1", 3, "BAZ", "TRIM",      1, 30.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
        self.assertEqual([c.ticker for c in cands], ["BAZ"])

    def test_priority_order_stoploss_first(self):
        """STOP_LOSS comes before SELL before TRIM in the returned
        list so the downstream submitter executes the most urgent
        exits first (paper-broker submits sequentially)."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 99, "TRIM_T",  "TRIM",      1, 10.0,
                          rank=99),
                _reco_row("r1", 5,  "SELL_T",  "SELL",      1, 20.0,
                          rank=5),
                _reco_row("r1", 50, "STOP_T",  "STOP_LOSS", 1, 30.0,
                          rank=50),
            ])
            cands = intents_io.load_sell_candidates(rd)
        self.assertEqual([c.action for c in cands],
                         ["STOP_LOSS", "SELL", "TRIM"])

    def test_missing_csv_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            cands = intents_io.load_sell_candidates(Path(td) / "x")
        self.assertEqual(cands, [])


# ──────────────────────────────────────────────────────────────────
# 3. sell_candidates_to_intent_rows pricing
# ──────────────────────────────────────────────────────────────────
@dataclass
class _FakeQuote:
    last: float
    bid: Optional[float]
    ask: Optional[float]
    asof: str = "2026-05-28T22:35:01"


class TestSellPricing(unittest.TestCase):

    def _cand(self, ticker="COR", shares=2, price=267.01,
              action="STOP_LOSS") -> intents_io.SellCandidate:
        return intents_io.SellCandidate(
            run_id="r1", rec_row_id=1, ticker=ticker,
            action=action, reco_shares=shares, reco_price=price,
        )

    def test_no_quote_uses_reco_close(self):
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand()],
            limit_pad_pct=0.0)
        self.assertEqual(rows[0]["limit_price"], 267.01)
        self.assertEqual(rows[0]["_quote_source"], "reco_close")

    def test_positive_pad_LOWERS_limit_on_sell(self):
        """Sanity check: pad direction flips for SELL (more
        aggressive = lower limit)."""
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand(price=100.0)],
            limit_pad_pct=1.0)  # -1% from reco
        self.assertAlmostEqual(rows[0]["limit_price"], 99.0)

    def test_uses_bid_when_quote_provided(self):
        q = _FakeQuote(last=270.0, bid=268.5, ask=271.0)
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand(price=267.01)],
            quote_fn=lambda s, m: q,
            quote_pad_pct=0.1,        # -0.1% from bid
            quote_only=True)
        # 268.5 * (1 - 0.001) = 268.2315 → rounded to 4dp
        self.assertAlmostEqual(rows[0]["limit_price"], 268.2315,
                                places=4)
        self.assertEqual(rows[0]["_quote_source"], "quote_only")
        self.assertEqual(rows[0]["_quote_ref_price"], 268.5)

    def test_falls_back_to_last_when_no_bid(self):
        q = _FakeQuote(last=270.0, bid=None, ask=271.0)
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand()], quote_fn=lambda s, m: q,
            quote_only=True)
        self.assertEqual(rows[0]["_quote_ref_price"], 270.0)

    def test_falls_back_to_reco_on_quote_failure(self):
        def boom(s, m):
            raise RuntimeError("quote service down")
        warns: List[intents_io.IntentBuildWarning] = []
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand(price=267.01)], quote_fn=boom,
            warnings_out=warns, quote_only=True)
        self.assertEqual(rows[0]["limit_price"], 267.01)
        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        self.assertTrue(warns)
        self.assertIn("quote", warns[0].reason.lower())

    def test_side_is_SELL_and_qty_matches_reco(self):
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand(shares=5)])
        self.assertEqual(rows[0]["side"], "SELL")
        self.assertEqual(rows[0]["qty"], 5)

    def test_t7_action_preserved_in_row(self):
        """R11B mail bodies group by the originating T7 action — pin
        the field so the audit trail stays exact."""
        rows = intents_io.sell_candidates_to_intent_rows(
            [self._cand(action="TRIM")])
        self.assertEqual(rows[0]["_t7_action"], "TRIM")


# ──────────────────────────────────────────────────────────────────
# 4. run_generate_intents — BUY + SELL merged file
# ──────────────────────────────────────────────────────────────────
class TestRunGenerateIntentsMerge(unittest.TestCase):

    def test_buy_only_run_unchanged(self):
        """Backwards compat: a recs.csv with only BUY rows yields a
        BUY-only intents file just like V1-F did."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "NVDA", "BUY_NEW",  3, 120.0),
                _reco_row("r1", 2, "AAPL", "BUY_MORE", 1, 180.0),
            ])
            res = v1_runner.run_generate_intents(
                run_dir=rd, run_id="r1", use_quote=False)
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.buy_count, 2)
        self.assertEqual(res.sell_count, 0)
        self.assertEqual(res.rows_written, 2)

    def test_buy_plus_sell_merged(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "NVDA", "BUY_NEW",   3, 120.0),
                _reco_row("r1", 2, "COR",  "STOP_LOSS", 2, 267.0),
                _reco_row("r1", 3, "AAPL", "SELL",      4, 180.0),
                _reco_row("r1", 4, "TPL",  "SELL_GRACE",1, 100.0),
            ])
            res = v1_runner.run_generate_intents(
                run_dir=rd, run_id="r1", use_quote=False)
            self.assertEqual(res.rc, 0)
            self.assertEqual(res.buy_count, 1)
            self.assertEqual(res.sell_count, 2)        # SELL_GRACE excluded
            self.assertEqual(res.rows_written, 3)
            self.assertEqual(
                res.sell_action_breakdown,
                {"STOP_LOSS": 1, "SELL": 1})

            # File on disk should round-trip through the validator at
            # "ok" state and carry the BUY/SELL split intact.
            status = intents_io.validate_submitted_intents(rd)
            self.assertEqual(status.state, "ok")
            sides = sorted(r["side"] for r in status.rows)
            self.assertEqual(sides, ["BUY", "SELL", "SELL"])

    def test_sell_only_day_is_rc0(self):
        """A trading day with forced exits and no new buys (e.g.
        regime turning DEFENSIVE) is a valid run — STOP_LOSS still
        needs to fire even though buy_count=0."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "COR", "STOP_LOSS", 2, 267.0),
            ])
            res = v1_runner.run_generate_intents(
                run_dir=rd, run_id="r1", use_quote=False)
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.buy_count, 0)
        self.assertEqual(res.sell_count, 1)

    def test_empty_recs_fails(self):
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                # Only HOLD / DEFERRED / SELL_GRACE — no actionable
                # BUY or SELL.
                _reco_row("r1", 1, "AAPL", "HOLD",       1, 180.0),
                _reco_row("r1", 2, "MSFT", "DEFERRED",   1, 400.0),
                _reco_row("r1", 3, "TPL",  "SELL_GRACE", 1, 100.0),
            ])
            res = v1_runner.run_generate_intents(
                run_dir=rd, run_id="r1", use_quote=False)
        self.assertEqual(res.rc, 2)
        self.assertIn("no BUY or SELL", res.error)

    def test_include_sells_kill_switch(self):
        """``include_sells=False`` reproduces the pre-V1-G behavior
        for emergency rollback / A-B test."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "NVDA", "BUY_NEW",   3, 120.0),
                _reco_row("r1", 2, "COR",  "STOP_LOSS", 2, 267.0),
            ])
            res = v1_runner.run_generate_intents(
                run_dir=rd, run_id="r1", use_quote=False,
                include_sells=False)
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.sell_count, 0)
        self.assertEqual(res.buy_count, 1)


# ──────────────────────────────────────────────────────────────────
# 5. daily_runner.default_intents_loader preserves side
# ──────────────────────────────────────────────────────────────────
class TestDefaultIntentsLoaderPreservesSide(unittest.TestCase):
    """The very last layer of the old BUY-only invariant was a
    hard-coded ``side='BUY'`` in default_intents_loader. V1-G
    removes it; this test pins the new behavior."""

    def test_sell_row_round_trip_preserves_side(self):
        from phase3.autotrade import daily_runner as autotrade_dr

        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            sell_row = intents_io.make_sell_intent_row(
                client_order_id="co-r1-1-S-2-COR",
                symbol="COR", qty=2, limit_price=266.5,
            )
            buy_row = intents_io.make_buy_intent_row(
                client_order_id="co-r1-2-B-3-NVDA",
                symbol="NVDA", qty=3, limit_price=120.0,
            )
            intents_io.write_submitted_intents(
                rd, [buy_row, sell_row], run_id="r1")

            @dataclass
            class _Ctx:
                run_dir: Path
                dry_run: bool = False

            intents = autotrade_dr.default_intents_loader(
                _Ctx(run_dir=rd))
            sides = sorted(i.side for i in intents)
        self.assertEqual(sides, ["BUY", "SELL"])


# ──────────────────────────────────────────────────────────────────
# 6. KIS_ALLOW_SELL env gate parity
# ──────────────────────────────────────────────────────────────────
class TestStrategyParity(unittest.TestCase):
    """V1-G.2 — autotrade must mirror the backtest simulator's
    dispatch sets *exactly*.  If these tests fail, live execution
    has drifted from backtest assumptions and any reported
    backtest performance is no longer a fair proxy for live."""

    def test_buy_actions_match_simulator(self):
        """Simulator dispatches BUY only on
        ``action in ("BUY_NEW", "BUY_MORE")`` (simulator.py:190).
        The intents loader filter MUST be the same tuple — adding
        a phantom ``"BUY"`` would silently make the live filter
        more permissive than the backtest."""
        self.assertEqual(
            sorted(intents_io._BUY_ACTIONS_DEFAULT),
            sorted(("BUY_NEW", "BUY_MORE")))

    def test_sell_actions_equal_FULL_CLOSE_plus_PARTIAL_CLOSE(self):
        """The canonical dispatch sets in ``exits.RecosAction`` are
        the source of truth used by both ``SimPortfolio.apply_actions``
        and ``t10_applicator._checkable_actions``. The intent loader
        MUST be a strict equality (not subset) so a future new
        trigger variant added to ``RecosAction`` is auto-picked-up
        by all three call sites in lock-step.

        SELL_GRACE is explicitly NOT in either canonical set (it's
        in ``RecosAction.NO_OP``) so it stays out by construction —
        no special-case needed."""
        from exits import RecosAction as _RA
        expected = set(_RA.FULL_CLOSE) | set(_RA.PARTIAL_CLOSE)
        actual = set(intents_io._SELL_ACTIONS_DEFAULT)
        self.assertEqual(actual, expected,
            f"missing: {expected - actual!r}  extra: {actual - expected!r}")

    def test_sell_grace_excluded_from_intent_set(self):
        """Grace is a warning state — never an actionable sell."""
        self.assertNotIn(
            "SELL_GRACE", intents_io._SELL_ACTIONS_DEFAULT)
        self.assertNotIn(
            "SELL_GRACE", intents_io._BUY_ACTIONS_DEFAULT)

    def test_trim_grace_is_loaded_as_sell(self):
        """V1-G.2 regression: TRIM_GRACE is the **step-1 partial
        sell** in the multi-step grace policy (daily_runner.py:992
        — partial_pct shares sold while the ticker is still in
        the grace window).  Dropping it would mean live keeps a
        full position when backtest had trimmed it — a real
        strategy-divergence bug."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "AAPL", "TRIM_GRACE", 5, 180.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].action, "TRIM_GRACE")

    def test_trim_profit_is_loaded_as_sell(self):
        """User's current config.yaml has a ``profit_target``
        trigger for SIDE / DEF regimes that emits ``TRIM_PROFIT``
        (daily_runner partial_close family).  Must reach the
        broker as a SELL."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "NVDA", "TRIM_PROFIT", 3, 120.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].action, "TRIM_PROFIT")

    def test_d2_sell_variants_are_loaded_as_sell(self):
        """Sanity-check a D2 trigger variant (SELL_PEAK_DD) so a
        future regime that turns on D2 doesn't silently miss the
        exit."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "X1", "SELL_PEAK_DD",   1, 10.0),
                _reco_row("r1", 2, "X2", "SELL_TREND_BREAK", 1, 11.0),
                _reco_row("r1", 3, "X3", "TRIM_RANK_VEL", 1, 12.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
            self.assertEqual(
                sorted(c.action for c in cands),
                sorted(["SELL_PEAK_DD", "SELL_TREND_BREAK",
                         "TRIM_RANK_VEL"]))

    def test_sell_grace_still_excluded_in_mixed_csv(self):
        """End-to-end pin: a recommendations.csv with a mix of
        SELL_GRACE + actionable rows must produce an intent set
        that drops the SELL_GRACE row."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "TPL",  "SELL_GRACE",  7, 406.0),
                _reco_row("r1", 2, "AAPL", "TRIM_GRACE",  3, 180.0),
                _reco_row("r1", 3, "COR",  "STOP_LOSS",   2, 267.0),
                _reco_row("r1", 4, "MSFT", "TRIM_PROFIT", 1, 400.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
            tickers = sorted(c.ticker for c in cands)
            self.assertEqual(tickers, ["AAPL", "COR", "MSFT"])

    def test_priority_stop_loss_full_close_partial_close(self):
        """Submission order must be STOP_LOSS first, then other
        FULL_CLOSE variants, then PARTIAL_CLOSE variants.  Important
        because the paper broker submits sequentially and we don't
        want a TRIM eating into account capital before a STOP_LOSS
        has freed shares."""
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td) / "run"
            _write_recs(rd, [
                _reco_row("r1", 1, "TRIM_T",   "TRIM",          1, 10.0),
                _reco_row("r1", 2, "SELL_T",   "SELL",          1, 20.0),
                _reco_row("r1", 3, "TRIMPR_T", "TRIM_PROFIT",   1, 30.0),
                _reco_row("r1", 4, "STOP_T",   "STOP_LOSS",     1, 40.0),
                _reco_row("r1", 5, "SDECAY_T", "SELL_SCORE_DECAY", 1, 50.0),
            ])
            cands = intents_io.load_sell_candidates(rd)
        # Tier 0: STOP_LOSS, Tier 1: SELL + SELL_SCORE_DECAY,
        # Tier 2: TRIM + TRIM_PROFIT.  Within tier, stable by RecRowId.
        actions = [c.action for c in cands]
        self.assertEqual(actions[0], "STOP_LOSS")
        self.assertEqual(set(actions[1:3]),
                          {"SELL", "SELL_SCORE_DECAY"})
        self.assertEqual(set(actions[3:5]),
                          {"TRIM", "TRIM_PROFIT"})


class TestAllowSellEnvGateParity(unittest.TestCase):
    """``LAUNCHD_ENV`` MUST carry KIS_ALLOW_SELL=true and the trade
    plist hand-edited XML MUST match — same parity contract as the
    SUBMIT / CANCEL / APPLY gates."""

    def test_launchd_env_constant_carries_allow_sell(self):
        self.assertEqual(
            v1_runner.LAUNCHD_ENV.get("KIS_ALLOW_SELL"), "true")

    def test_t7_env_constant_does_NOT_carry_allow_sell(self):
        """T7 prefetch never submits — keep its env minimal."""
        self.assertNotIn("KIS_ALLOW_SELL", v1_runner.T7_LAUNCHD_ENV)

    def test_trade_plist_has_allow_sell_block(self):
        plist_path = (_PHASE3 / "launchd"
                      / "com.autotrade.v1.daily.plist.template")
        text = plist_path.read_text(encoding="utf-8")
        self.assertIn("KIS_ALLOW_SELL", text)
        self.assertIn(">true<", text)


class TestT10ApplicatorAllowlistParity(unittest.TestCase):
    """V1-G.3 — Pin the t10_applicator apply allowlist.

    On 26-05-28 the 22:35 KST fire submitted+filled a STOP_LOSS for
    COR at the broker, but t10_applicator aborted at the apply step
    because its allowlist still hard-coded BUY-only. Result: broker
    truth (COR sold) drifted from local holdings_log (COR held).
    These tests pin the contract that APPLY_ACTIONS spans the
    canonical FULL_CLOSE + PARTIAL_CLOSE + BUY set so the bug
    cannot regress."""

    def test_apply_actions_includes_buy_and_sell(self):
        from phase3.autotrade.t10_applicator import (
            APPLY_ACTIONS, BUY_ACTIONS,
        )
        from exits import RecosAction as _RA

        self.assertIn("BUY_NEW", APPLY_ACTIONS)
        self.assertIn("BUY_MORE", APPLY_ACTIONS)
        self.assertIn("STOP_LOSS", APPLY_ACTIONS)
        self.assertIn("SELL", APPLY_ACTIONS)
        self.assertIn("TRIM", APPLY_ACTIONS)
        for a in _RA.FULL_CLOSE:
            self.assertIn(
                a, APPLY_ACTIONS,
                f"FULL_CLOSE action {a!r} missing from APPLY_ACTIONS")
        for a in _RA.PARTIAL_CLOSE:
            self.assertIn(
                a, APPLY_ACTIONS,
                f"PARTIAL_CLOSE action {a!r} missing from APPLY_ACTIONS")
        for a in BUY_ACTIONS:
            self.assertIn(a, APPLY_ACTIONS)

    def test_apply_actions_excludes_legacy_buy_and_warning_states(self):
        from phase3.autotrade.t10_applicator import APPLY_ACTIONS

        self.assertNotIn(
            "BUY", APPLY_ACTIONS,
            "Legacy 'BUY' action should not be in APPLY_ACTIONS — "
            "current T7 emits BUY_NEW / BUY_MORE only.")
        self.assertNotIn(
            "HOLD", APPLY_ACTIONS,
            "HOLD is a no-op and must never reach apply.")
        self.assertNotIn(
            "DEFERRED", APPLY_ACTIONS,
            "DEFERRED is a no-op and must never reach apply.")

    def test_apply_allowlist_matches_intents_io_sell_set(self):
        """Apply allowlist must accept exactly what intents_io
        emits — anything else creates submitted-but-cannot-apply
        rows, which is what bit us on 26-05-28."""
        from phase3.autotrade.t10_applicator import APPLY_ACTIONS
        from phase3.autotrade.intents_io import (
            _SELL_ACTIONS_DEFAULT, _BUY_ACTIONS_DEFAULT,
        )

        for a in _BUY_ACTIONS_DEFAULT:
            self.assertIn(
                a, APPLY_ACTIONS,
                f"BUY action {a!r} emitted by intents_io but rejected "
                f"by t10_applicator allowlist")
        for a in _SELL_ACTIONS_DEFAULT:
            self.assertIn(
                a, APPLY_ACTIONS,
                f"SELL action {a!r} emitted by intents_io but rejected "
                f"by t10_applicator allowlist")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
