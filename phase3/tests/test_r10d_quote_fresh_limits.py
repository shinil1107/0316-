"""R10D-3 — quote-fresh limit price generation.

R10C showed `reco_price = previous business-day close` is too stale
for gap-prone tickers like APA. ``candidates_to_intent_rows`` and
``write_intent_file_from_candidates`` now accept an optional
``quote_fn(symbol, market)`` that returns a Quote-like object with
``ask`` / ``last`` / ``asof`` attributes; when provided, each BUY
limit is lifted to ``max(reco_padded, quote_ref * (1+quote_pad))``.

Surfaces under test:

1. Pure limit-pick logic (which path is taken: reco_close,
   quote_refreshed, quote_refreshed_below_reco, fallback_quote_fail,
   fallback_quote_zero) and its ``_quote_source`` metadata footprint.
2. ``IntentBuildWarning`` collection for every fallback case.
3. The file-write wrapper round-trips the metadata through
   ``submitted_intents.json`` and the validator still accepts the
   resulting file.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import intents_io
from phase3.autotrade.intents_io import (
    IntentBuildWarning,
    candidates_to_intent_rows,
    write_intent_file_from_candidates,
)


@dataclass
class _FakeQuote:
    symbol: str
    market: str
    last: float
    bid: Optional[float]
    ask: Optional[float]
    asof: str


def _candidate(*, ticker, rec_row_id, reco_price, reco_shares=2):
    return intents_io.BuyCandidate(
        run_id="20260519_test", rec_row_id=rec_row_id, ticker=ticker,
        action="BUY_NEW", reco_shares=reco_shares, reco_price=reco_price,
        rank=1, regime="", market="NASD", actionable=True, raw_row={},
    )


# ──────────────────────────────────────────────────────────────────────
# candidates_to_intent_rows — limit picking rule
# ──────────────────────────────────────────────────────────────────────
class TestQuoteFreshLimitRule(unittest.TestCase):

    def test_no_quote_fn_marks_rows_as_reco_close(self):
        rows = candidates_to_intent_rows(
            [_candidate(ticker="APA", rec_row_id=1, reco_price=38.98)],
            limit_pad_pct=0.5,
        )
        self.assertEqual(rows[0]["_quote_source"], "reco_close")
        self.assertNotIn("_quote_ref_price", rows[0])
        # reco_padded = 38.98 * 1.005 = 39.1749 (R10C run-2 limit)
        self.assertAlmostEqual(rows[0]["limit_price"], 39.1749, places=4)

    def test_ask_above_reco_lifts_limit_with_quote_pad(self):
        """The classic gap-up case (APA on 2026-05-18): yesterday's
        close was 38.98 but Monday's ask is 40.10. Limit should jump
        to ``ask * (1+quote_pad)`` rather than yesterday's close."""
        c = _candidate(ticker="APA", rec_row_id=1, reco_price=38.98)
        def _qfn(sym, mkt):
            return _FakeQuote(
                symbol=sym, market=mkt, last=40.05, bid=40.05,
                ask=40.10, asof="2026-05-19T13:30:00+00:00",
            )
        warnings: List[IntentBuildWarning] = []
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.5, quote_fn=_qfn,
            quote_pad_pct=0.1, warnings_out=warnings,
        )
        self.assertEqual(rows[0]["_quote_source"], "quote_refreshed")
        # quote_ref = ask = 40.10, padded by +0.1% = 40.1401
        self.assertAlmostEqual(
            rows[0]["limit_price"], 40.1401, places=4)
        self.assertEqual(rows[0]["_quote_ref_price"], 40.10)
        self.assertEqual(
            rows[0]["_quote_asof"], "2026-05-19T13:30:00+00:00")
        # No warnings when the path is fully clean.
        self.assertEqual(warnings, [])

    def test_ask_below_reco_keeps_reco_limit(self):
        """If the broker's ask is already below reco_padded, the reco
        path is binding and we mark ``quote_refreshed_below_reco`` so
        the operator can see in the snapshot that we did try."""
        c = _candidate(ticker="MRNA", rec_row_id=2, reco_price=49.04)
        def _qfn(sym, mkt):
            return _FakeQuote(
                symbol=sym, market=mkt, last=48.30, bid=48.30,
                ask=48.35, asof="2026-05-19T13:30:00+00:00",
            )
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.5, quote_fn=_qfn, quote_pad_pct=0.1)
        # reco_padded = 49.04 * 1.005 = 49.2852 (R10C run-2 limit)
        self.assertAlmostEqual(rows[0]["limit_price"], 49.2852, places=4)
        self.assertEqual(rows[0]["_quote_source"], "quote_refreshed_below_reco")

    def test_falls_back_to_last_when_ask_is_none(self):
        c = _candidate(ticker="AAPL", rec_row_id=3, reco_price=190.0)
        def _qfn(sym, mkt):
            return _FakeQuote(
                symbol=sym, market=mkt, last=195.0, bid=None, ask=None,
                asof="2026-05-19T13:30:00+00:00",
            )
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.0, quote_fn=_qfn, quote_pad_pct=0.1)
        self.assertEqual(rows[0]["_quote_source"], "quote_refreshed")
        self.assertEqual(rows[0]["_quote_ref_price"], 195.0)
        self.assertAlmostEqual(rows[0]["limit_price"], 195.195, places=4)

    def test_quote_fn_exception_falls_back_with_warning(self):
        c = _candidate(ticker="BOOM", rec_row_id=4, reco_price=10.0)
        def _qfn(sym, mkt):
            raise ConnectionError("simulated KIS quote timeout")
        warnings: List[IntentBuildWarning] = []
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=1.0, quote_fn=_qfn,
            quote_pad_pct=0.1, warnings_out=warnings)
        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        # reco_padded = 10.0 * 1.01 = 10.1
        self.assertAlmostEqual(rows[0]["limit_price"], 10.1, places=4)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].ticker, "BOOM")
        self.assertIn("ConnectionError", warnings[0].reason)

    def test_quote_zero_price_falls_back_with_warning(self):
        """A Quote whose ask/last are both 0 should not crash and
        must NOT replace reco_price with 0 — that would be a
        catastrophic limit-to-zero on a BUY."""
        c = _candidate(ticker="DEAD", rec_row_id=5, reco_price=12.5)
        def _qfn(sym, mkt):
            return _FakeQuote(
                symbol=sym, market=mkt, last=0.0, bid=0.0, ask=0.0,
                asof="2026-05-19T13:30:00+00:00",
            )
        warnings: List[IntentBuildWarning] = []
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.0, quote_fn=_qfn,
            quote_pad_pct=0.1, warnings_out=warnings)
        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_zero")
        self.assertAlmostEqual(rows[0]["limit_price"], 12.5, places=4)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].ticker, "DEAD")

    def test_quote_returning_none_falls_back_with_warning(self):
        c = _candidate(ticker="NULL", rec_row_id=6, reco_price=5.0)
        def _qfn(sym, mkt):
            return None
        warnings: List[IntentBuildWarning] = []
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.0, quote_fn=_qfn,
            quote_pad_pct=0.1, warnings_out=warnings)
        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        self.assertAlmostEqual(rows[0]["limit_price"], 5.0, places=4)
        self.assertEqual(len(warnings), 1)
        # quote_fn returned None — counted as failure for warning.
        self.assertIn("failed", warnings[0].reason.lower())

    def test_mixed_batch_each_row_records_its_own_source(self):
        """One quote OK, one quote failing — both rows are written;
        each carries its own ``_quote_source`` and ``warnings_out``
        accumulates only the failing rows."""
        cands = [
            _candidate(ticker="APA",  rec_row_id=1, reco_price=38.98),
            _candidate(ticker="BOOM", rec_row_id=2, reco_price=10.0),
            _candidate(ticker="MRNA", rec_row_id=3, reco_price=49.04),
        ]
        def _qfn(sym, mkt):
            if sym == "BOOM":
                raise RuntimeError("simulated")
            if sym == "APA":
                return _FakeQuote(sym, mkt, 40.0, 40.0, 40.10,
                                   "2026-05-19T13:30:00+00:00")
            return _FakeQuote(sym, mkt, 48.30, 48.30, 48.35,
                               "2026-05-19T13:30:00+00:00")
        warnings: List[IntentBuildWarning] = []
        rows = candidates_to_intent_rows(
            cands, limit_pad_pct=0.5, quote_fn=_qfn,
            quote_pad_pct=0.1, warnings_out=warnings)
        sources = {r["symbol"]: r["_quote_source"] for r in rows}
        self.assertEqual(sources, {
            "APA": "quote_refreshed",
            "BOOM": "fallback_quote_fail",
            "MRNA": "quote_refreshed_below_reco",
        })
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].ticker, "BOOM")

    def test_quote_pad_pct_zero_uses_raw_ask(self):
        c = _candidate(ticker="X", rec_row_id=1, reco_price=10.0)
        def _qfn(sym, mkt):
            return _FakeQuote(sym, mkt, 20.0, 20.0, 20.0,
                               "2026-05-19T13:30:00+00:00")
        rows = candidates_to_intent_rows(
            [c], limit_pad_pct=0.0, quote_fn=_qfn, quote_pad_pct=0.0)
        self.assertEqual(rows[0]["limit_price"], 20.0)


# ──────────────────────────────────────────────────────────────────────
# write_intent_file_from_candidates — metadata round-trip
# ──────────────────────────────────────────────────────────────────────
class TestWriteIntentFileFromCandidatesQuoteFresh(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.run_dir = Path(self._td.name)

    def test_quote_source_persists_to_disk(self):
        cands = [_candidate(ticker="APA", rec_row_id=75, reco_price=38.98)]
        def _qfn(sym, mkt):
            return _FakeQuote(sym, mkt, 40.05, 40.05, 40.10,
                               "2026-05-19T13:30:00+00:00")
        out = write_intent_file_from_candidates(
            self.run_dir, cands, limit_pad_pct=0.5,
            quote_fn=_qfn, quote_pad_pct=0.1, run_id="rid-r10d")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data["intents"]), 1)
        row = data["intents"][0]
        self.assertEqual(row["_quote_source"], "quote_refreshed")
        self.assertEqual(row["_quote_ref_price"], 40.10)
        self.assertAlmostEqual(row["limit_price"], 40.1401, places=4)

    def test_validator_still_accepts_rows_with_quote_metadata(self):
        cands = [_candidate(ticker="AAPL", rec_row_id=1, reco_price=190.0)]
        def _qfn(sym, mkt):
            return _FakeQuote(sym, mkt, 195.0, 195.0, 195.10,
                               "2026-05-19T13:30:00+00:00")
        write_intent_file_from_candidates(
            self.run_dir, cands, limit_pad_pct=0.0,
            quote_fn=_qfn, quote_pad_pct=0.1, run_id="rid-r10d")
        st = intents_io.validate_submitted_intents(self.run_dir)
        self.assertTrue(st.is_ok, f"validator rejected quote-refreshed row: {st}")
        self.assertEqual(st.buy_count, 1)

    def test_warnings_out_propagates_through_file_writer(self):
        cands = [_candidate(ticker="BOOM", rec_row_id=1, reco_price=10.0)]
        def _qfn(sym, mkt):
            raise RuntimeError("simulated")
        warnings: List[IntentBuildWarning] = []
        write_intent_file_from_candidates(
            self.run_dir, cands, limit_pad_pct=0.0,
            quote_fn=_qfn, warnings_out=warnings, run_id="rid-r10d")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].ticker, "BOOM")


if __name__ == "__main__":
    unittest.main()
