"""V1-I — live-quote BUY pricing (start slightly below the live ask,
then chase) + gap filter (drop BUYs that gapped up past the cap).

These tests pin the pure pricing/filter behaviour in
``intents_io.candidates_to_intent_rows`` and the env-driven plumbing in
``v1_runner`` (LAUNCHD_ENV + ``_env_float``), without touching the live
broker. The design rationale is in
``phase3/docs/V1I_PREMARKET_GAP_HANDLING_DESIGN_20260530.md`` §1.4 / §5-A.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import intents_io  # noqa: E402
from phase3.autotrade import v1_runner  # noqa: E402


@dataclass
class _FakeQuote:
    """Duck-typed stand-in for kis_broker_adapter.Quote."""
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    asof: str = "2026-06-01T13:35:00+00:00"


def _buy(ticker: str, reco_price: float, *, shares: int = 1,
         rec_row_id: int = 1) -> intents_io.BuyCandidate:
    return intents_io.BuyCandidate(
        run_id="20260601_072008_daily",
        rec_row_id=rec_row_id,
        ticker=ticker,
        action="BUY_NEW",
        reco_shares=shares,
        reco_price=reco_price,
        market="NASD",
    )


# ──────────────────────────────────────────────────────────────────────
# Start-below-ask pricing (negative quote_pad on BUY)
# ──────────────────────────────────────────────────────────────────────
class TestStartBelowAsk(unittest.TestCase):

    def test_negative_pad_prices_below_ask(self):
        """A negative ``quote_pad_pct`` on BUY must land the limit BELOW
        the live ask (the 'start passive then chase' entry)."""
        cands = [_buy("HPE", 38.21)]
        rows = intents_io.candidates_to_intent_rows(
            cands,
            quote_fn=lambda s, m: _FakeQuote(last=45.0, ask=45.0, bid=44.9),
            quote_pad_pct=-0.2,   # 0.2% below ask
            quote_only=True,
        )
        self.assertEqual(len(rows), 1)
        # 45.0 * (1 + (-0.2)/100) = 45.0 * 0.998 = 44.91
        self.assertAlmostEqual(rows[0]["limit_price"], 44.91, places=4)
        # Below the ask, and well above the stale reco close.
        self.assertLess(rows[0]["limit_price"], 45.0)
        self.assertGreater(rows[0]["limit_price"], 38.21)
        self.assertEqual(rows[0]["_quote_source"], "quote_only")

    def test_positive_pad_still_marketable_above_ask(self):
        """Back-compat: a positive pad keeps the legacy 'above ask'
        marketable behaviour."""
        rows = intents_io.candidates_to_intent_rows(
            [_buy("X", 100.0)],
            quote_fn=lambda s, m: _FakeQuote(last=100.0, ask=100.0),
            quote_pad_pct=0.5,
            quote_only=True,
        )
        self.assertAlmostEqual(rows[0]["limit_price"], 100.5, places=4)


# ──────────────────────────────────────────────────────────────────────
# Gap filter
# ──────────────────────────────────────────────────────────────────────
class TestGapFilter(unittest.TestCase):

    def test_gap_up_beyond_cap_is_dropped(self):
        """A name that gapped up past the cap must NOT produce a row and
        must emit a gap_filter warning."""
        warns: list = []
        cands = [_buy("HPE", 38.21)]   # reco close
        rows = intents_io.candidates_to_intent_rows(
            cands,
            # 38.21 -> 45.0 is +17.8%, over the 15% cap.
            quote_fn=lambda s, m: _FakeQuote(last=45.0, ask=45.0),
            quote_pad_pct=-0.2,
            quote_only=True,
            gap_filter_max_pct=15.0,
            warnings_out=warns,
        )
        self.assertEqual(rows, [])
        self.assertEqual(len(warns), 1)
        self.assertTrue(warns[0].reason.startswith(
            intents_io._GAP_FILTER_WARN_PREFIX))
        self.assertEqual(warns[0].ticker, "HPE")

    def test_gap_up_within_cap_is_kept(self):
        """A gap within the cap trades normally (priced off the ask)."""
        warns: list = []
        cands = [_buy("FTNT", 138.0)]
        rows = intents_io.candidates_to_intent_rows(
            cands,
            # 138 -> 141.7 is +2.7%, under the cap.
            quote_fn=lambda s, m: _FakeQuote(last=141.7, ask=141.7),
            quote_pad_pct=-0.2,
            quote_only=True,
            gap_filter_max_pct=15.0,
            warnings_out=warns,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(warns, [])

    def test_gap_down_is_never_filtered(self):
        """Gap-DOWN (cheaper than reco) is fine for a BUY — never drop."""
        cands = [_buy("Z", 100.0)]
        rows = intents_io.candidates_to_intent_rows(
            cands,
            quote_fn=lambda s, m: _FakeQuote(last=70.0, ask=70.0),  # -30%
            quote_pad_pct=-0.2,
            quote_only=True,
            gap_filter_max_pct=15.0,
        )
        self.assertEqual(len(rows), 1)

    def test_quote_failure_keeps_candidate_at_reco_fallback(self):
        """If the quote pipeline fails we cannot compute a gap, so the
        candidate is KEPT (priced at the reco-close fallback) rather
        than silently dropped."""
        warns: list = []
        rows = intents_io.candidates_to_intent_rows(
            [_buy("Q", 50.0)],
            quote_fn=lambda s, m: None,   # quote miss
            quote_pad_pct=-0.2,
            quote_only=True,
            gap_filter_max_pct=15.0,
            warnings_out=warns,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        # 50.0 * (1 + 0/100) reco pad default = 50.0
        self.assertAlmostEqual(rows[0]["limit_price"], 50.0, places=4)
        # No gap_filter warning (we never had a quote to measure).
        self.assertFalse(any(
            w.reason.startswith(intents_io._GAP_FILTER_WARN_PREFIX)
            for w in warns))

    def test_no_filter_when_cap_is_none(self):
        """``gap_filter_max_pct=None`` disables the filter entirely
        (legacy behaviour)."""
        rows = intents_io.candidates_to_intent_rows(
            [_buy("HPE", 38.21)],
            quote_fn=lambda s, m: _FakeQuote(last=99.0, ask=99.0),  # +159%
            quote_pad_pct=-0.2,
            quote_only=True,
            gap_filter_max_pct=None,
        )
        self.assertEqual(len(rows), 1)


# ──────────────────────────────────────────────────────────────────────
# Reporting — gap drops must surface on the PARTIAL-drop success path
# ──────────────────────────────────────────────────────────────────────
class TestGapDropReporting(unittest.TestCase):
    """Regression for the 2026-06-02 HPE 'silent disappearance' bug:
    when SOME (not all) BUYs gap past the cap, ``run_generate_intents``
    still returns rc=0 with rows, and the gap-drop tally MUST be carried
    on the result so the log + R11B mail can explain the missing ticker.
    """

    def _seed_run(self, td: Path):
        import csv as _csv
        run_dir = td / "daily_runs" / "20260602_072008_daily"
        run_dir.mkdir(parents=True)
        cols = ["RunId", "ScoringDate", "Actionable", "RecRowId", "Date",
                "Ticker", "Action", "Score", "TargetPct", "ActualPct",
                "GapPct", "Price", "Shares", "Capital", "Regime",
                "GraceCount", "Rank"]
        rows = [
            # HPE will gap up past the cap; NTAP stays within it.
            ["20260602_072008_daily", "2026-06-01", "True", "74",
             "2026-06-02", "HPE", "BUY_MORE", "95", "5", "0.1", "4",
             "47.0", "1", "47.0", "BULL", "0", "7"],
            ["20260602_072008_daily", "2026-06-01", "True", "75",
             "2026-06-02", "NTAP", "BUY_NEW", "92", "4.9", "0", "4.9",
             "179.7", "3", "539.1", "BULL", "0", "16"],
        ]
        with (run_dir / "recommendations.csv").open(
                "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)
        return run_dir

    def test_partial_gap_drop_is_reported(self):
        """End-to-end through ``run_generate_intents``: HPE gap-dropped,
        NTAP kept → rc=0, buy_count=1, AND the gap tally is carried on the
        result (the bug was that the success-path return omitted it).

        We monkeypatch the builder so the test stays offline (no live
        KIS adapter): it drops HPE with a gap_filter warning exactly as
        the real ``candidates_to_intent_rows`` would, and keeps NTAP.
        ``use_quote=False`` keeps ``run_generate_intents`` from spinning
        up the broker adapter.
        """
        import tempfile

        def _fake_builder(cands, *, warnings_out=None, **kw):
            kept = []
            for c in cands:
                if c.ticker == "HPE":
                    if warnings_out is not None:
                        warnings_out.append(intents_io.IntentBuildWarning(
                            ticker="HPE",
                            reason=(f"{intents_io._GAP_FILTER_WARN_PREFIX}: "
                                    f"+27.6% > 15% cap — dropped"),
                        ))
                    continue
                kept.append(intents_io.candidate_to_intent_row(
                    c, limit_price=float(c.reco_price)))
            return kept

        with tempfile.TemporaryDirectory() as td:
            run_dir = self._seed_run(Path(td))
            saved = intents_io.candidates_to_intent_rows
            intents_io.candidates_to_intent_rows = _fake_builder
            try:
                res = v1_runner.run_generate_intents(
                    run_dir=run_dir,
                    run_id="20260602_072008_daily",
                    use_quote=False,
                    gap_filter_max_pct=15.0,
                    include_sells=False,
                )
            finally:
                intents_io.candidates_to_intent_rows = saved

            self.assertEqual(res.rc, 0)
            self.assertEqual(res.buy_count, 1)          # NTAP only
            self.assertEqual(res.dropped_by_gap, 1)     # <-- the fix
            self.assertEqual(res.dropped_gap_tickers, ["HPE"])


# ──────────────────────────────────────────────────────────────────────
# Env plumbing
# ──────────────────────────────────────────────────────────────────────
class TestEnvPlumbing(unittest.TestCase):

    def test_launchd_env_ships_v1i_keys(self):
        env = v1_runner.LAUNCHD_ENV
        self.assertEqual(env[v1_runner.USE_QUOTE_ENV], "true")
        self.assertEqual(env[v1_runner.QUOTE_ONLY_ENV], "true")
        self.assertEqual(env[v1_runner.BUY_QUOTE_PAD_ENV], "-0.2")
        self.assertEqual(env[v1_runner.GAP_FILTER_ENV], "15")

    def test_v1i_retuned_v1h_ceiling(self):
        """V1-I shrank the V1-H slippage ceiling now that we start at the
        live ask (the wide ceiling was only needed to climb from the
        stale close)."""
        env = v1_runner.LAUNCHD_ENV
        self.assertEqual(env["AUTOTRADE_MAX_SLIPPAGE_BPS"], "300")
        self.assertEqual(env["AUTOTRADE_MAX_REPRICE_ATTEMPTS"], "6")
        self.assertEqual(env["AUTOTRADE_CONTINUE_ON_UNFILLED"], "true")

    def test_env_float_parses_negative_and_blank(self):
        self.assertEqual(
            v1_runner._env_float({"K": "-0.2"}, "K"), -0.2)
        self.assertEqual(
            v1_runner._env_float({"K": "15"}, "K"), 15.0)
        self.assertIsNone(v1_runner._env_float({}, "K"))
        self.assertIsNone(v1_runner._env_float({"K": ""}, "K"))
        # Malformed -> None (treat as unset, never crash the fire).
        self.assertIsNone(v1_runner._env_float({"K": "abc"}, "K"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
