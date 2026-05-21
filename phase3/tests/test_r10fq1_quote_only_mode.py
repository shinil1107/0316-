"""R10F-Q1 — quote-only limit mode (drop reco-close floor).

R10D-3 introduced live-quote refresh but kept ``reco_close`` as a price
floor via ``limit = max(reco_padded, quote_padded)``. The 2026-05-20
acceptance run showed this floor turns into a *gap-down hazard*: when
today's broker quote is well below yesterday's close, the reco floor
keeps the BUY limit at yesterday's level, so the operator pays the
high price even though the market just dropped.

``quote_only=True`` removes the floor when the live quote pipeline
succeeds. The reco close is still used as the fallback when
``quote_fn`` raises, returns ``None``, or returns a non-positive price
— those branches are unchanged from R10D-3.

Surfaces covered:

1. ``quote_only=True`` uses the quote_padded limit even when it is
   below the reco_padded floor (gap-down case).
2. ``quote_only=True`` still uses the quote_padded limit when it is
   above the reco close (gap-up case unchanged).
3. ``quote_only=True`` falls back to ``reco_close`` on quote_fn
   exception, ``None`` return, or non-positive ref price, with the
   correct ``_quote_source`` tag and warning rows preserved.
4. ``quote_only=False`` (or default) preserves R10D-3 floor behaviour
   so the legacy path is still callable.
5. The file-write wrapper threads ``quote_only`` through to disk.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
        run_id="20260520_test", rec_row_id=rec_row_id, ticker=ticker,
        action="BUY_NEW", reco_shares=reco_shares, reco_price=reco_price,
        rank=1, regime="", market="NASD", actionable=True, raw_row={},
    )


# ──────────────────────────────────────────────────────────────────────
# Pure-helper behaviour
# ──────────────────────────────────────────────────────────────────────
class TestQuoteOnlyMode(unittest.TestCase):

    def test_quote_only_uses_quote_padded_when_below_reco(self):
        """Gap-down case: reco_close=100 (yesterday), live ask=95
        (today's open). Legacy mode would have stuck at 100; quote-only
        drops the floor and lets the limit settle at 95*1.001=95.095."""
        cand = _candidate(ticker="GAPDOWN", rec_row_id=10, reco_price=100.0)
        quote = _FakeQuote(
            symbol="GAPDOWN", market="NASD",
            last=95.0, bid=94.95, ask=95.0,
            asof="2026-05-20T14:00:00+00:00",
        )

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.0,
            quote_fn=lambda s, m: quote,
            quote_pad_pct=0.1, quote_only=True,
        )

        self.assertEqual(rows[0]["_quote_source"], "quote_only")
        self.assertAlmostEqual(rows[0]["limit_price"], 95.095, places=4)
        self.assertAlmostEqual(rows[0]["_quote_ref_price"], 95.0, places=4)

    def test_legacy_floor_mode_keeps_reco_above_below_quote(self):
        """Same gap-down inputs as above, but quote_only=False — the
        floor remains and the limit stays at reco_padded=100."""
        cand = _candidate(ticker="GAPDOWN", rec_row_id=10, reco_price=100.0)
        quote = _FakeQuote(
            symbol="GAPDOWN", market="NASD",
            last=95.0, bid=94.95, ask=95.0,
            asof="2026-05-20T14:00:00+00:00",
        )

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.0,
            quote_fn=lambda s, m: quote,
            quote_pad_pct=0.1, quote_only=False,
        )

        self.assertEqual(
            rows[0]["_quote_source"], "quote_refreshed_below_reco")
        self.assertAlmostEqual(rows[0]["limit_price"], 100.0, places=4)

    def test_quote_only_gap_up_still_follows_quote(self):
        """Gap-up case unchanged: reco_close=100, live ask=105 → limit
        becomes 105*1.001=105.105 regardless of mode."""
        cand = _candidate(ticker="GAPUP", rec_row_id=11, reco_price=100.0)
        quote = _FakeQuote(
            symbol="GAPUP", market="NASD",
            last=105.0, bid=104.95, ask=105.0,
            asof="2026-05-20T14:00:00+00:00",
        )

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.0,
            quote_fn=lambda s, m: quote,
            quote_pad_pct=0.1, quote_only=True,
        )

        self.assertEqual(rows[0]["_quote_source"], "quote_only")
        self.assertAlmostEqual(rows[0]["limit_price"], 105.105, places=4)

    def test_quote_only_falls_back_to_reco_on_exception(self):
        cand = _candidate(ticker="ERR", rec_row_id=12, reco_price=50.0)
        warnings = []

        def _bad_quote(symbol, market):
            raise RuntimeError("kis 500")

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.5,
            quote_fn=_bad_quote, quote_pad_pct=0.1,
            quote_only=True, warnings_out=warnings,
        )

        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        # reco_padded = 50 * 1.005 = 50.25
        self.assertAlmostEqual(rows[0]["limit_price"], 50.25, places=4)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].ticker, "ERR")
        self.assertIn("kis 500", warnings[0].reason)

    def test_quote_only_falls_back_to_reco_on_none_quote(self):
        cand = _candidate(ticker="NONE", rec_row_id=13, reco_price=20.0)
        warnings = []

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.0,
            quote_fn=lambda s, m: None,
            quote_pad_pct=0.1, quote_only=True,
            warnings_out=warnings,
        )

        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_fail")
        self.assertAlmostEqual(rows[0]["limit_price"], 20.0, places=4)
        self.assertEqual(len(warnings), 1)
        self.assertIn("None", warnings[0].reason)

    def test_quote_only_falls_back_to_reco_on_zero_quote(self):
        cand = _candidate(ticker="ZERO", rec_row_id=14, reco_price=30.0)
        quote = _FakeQuote(
            symbol="ZERO", market="NASD",
            last=0.0, bid=0.0, ask=0.0,
            asof="2026-05-20T14:00:00+00:00",
        )
        warnings = []

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=1.0,
            quote_fn=lambda s, m: quote,
            quote_pad_pct=0.1, quote_only=True,
            warnings_out=warnings,
        )

        self.assertEqual(rows[0]["_quote_source"], "fallback_quote_zero")
        self.assertAlmostEqual(rows[0]["limit_price"], 30.3, places=4)
        self.assertEqual(len(warnings), 1)
        self.assertIn("non-positive", warnings[0].reason)

    def test_default_kwarg_is_legacy_floor_mode(self):
        """Existing call sites that omit ``quote_only`` must keep the
        R10D-3 floor mode for backwards compatibility."""
        cand = _candidate(ticker="LEGACY", rec_row_id=20, reco_price=100.0)
        quote = _FakeQuote(
            symbol="LEGACY", market="NASD",
            last=95.0, bid=94.5, ask=95.0,
            asof="2026-05-20T14:00:00+00:00",
        )

        rows = candidates_to_intent_rows(
            [cand], limit_pad_pct=0.0,
            quote_fn=lambda s, m: quote, quote_pad_pct=0.1,
        )

        self.assertEqual(
            rows[0]["_quote_source"], "quote_refreshed_below_reco")
        self.assertAlmostEqual(rows[0]["limit_price"], 100.0, places=4)


# ──────────────────────────────────────────────────────────────────────
# File-write wrapper round-trip
# ──────────────────────────────────────────────────────────────────────
class TestQuoteOnlyFileWrite(unittest.TestCase):

    def test_write_threads_quote_only_through(self):
        cand = _candidate(ticker="GAPDOWN", rec_row_id=42, reco_price=100.0)
        quote = _FakeQuote(
            symbol="GAPDOWN", market="NASD",
            last=95.0, bid=94.95, ask=95.0,
            asof="2026-05-20T14:00:00+00:00",
        )
        warnings = []

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "20260520_test_daily"
            run_dir.mkdir(parents=True)
            written = write_intent_file_from_candidates(
                run_dir, [cand],
                limit_pad_pct=0.0,
                quote_fn=lambda s, m: quote,
                quote_pad_pct=0.1,
                quote_only=True,
                warnings_out=warnings,
                run_id="20260520_test_daily",
            )
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["intents"]), 1)
            row = payload["intents"][0]
            self.assertEqual(row["_quote_source"], "quote_only")
            self.assertAlmostEqual(row["limit_price"], 95.095, places=4)
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
