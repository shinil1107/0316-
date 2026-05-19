"""R10E — multi-exchange quote fallback.

In 20260519_220825_daily, JBL and DOW (both NYSE-listed) generated
``fallback_quote_zero`` because the autotrade intent rows hard-code
``market="NASD"`` and ``KisBrokerAdapter.get_quote`` returned a
zero-priced Quote when asked under EXCD=NAS. R10D-3 then fell back
to yesterday's close — which became the limit, which became an
overpriced fill.

``get_quote_with_exchange_fallback`` probes NASD → NYSE → AMEX and
returns the first Quote whose ``ask`` or ``last`` is positive. Only
when EVERY exchange returns a zero quote or raises does the helper
return ``None`` (so the caller's reco_close fallback is reached).

We test the helper in isolation against a fake adapter whose
``get_quote`` is programmable.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter,
    Quote,
)


def _q(symbol, ex, *, last=0.0, ask=None):
    return Quote(
        symbol=symbol, market=ex, last=last, bid=None, ask=ask,
        asof="2026-05-19T13:30:00+00:00",
    )


class _FakeAdapter:
    """Stand-in for KisBrokerAdapter that records every ``get_quote``
    call and returns whatever is queued in ``responses[symbol][ex]``.

    A response of ``"raise"`` raises ConnectionError to simulate the
    KIS endpoint refusing the EXCD outright.
    """

    def __init__(self, responses: Dict[str, Dict[str, object]]):
        self.responses = responses
        self.calls: List[tuple] = []

    def _normalize_market(self, m: str) -> str:
        return m.upper()

    def get_quote(self, symbol: str, *, market: str = "NASD") -> Quote:
        self.calls.append((symbol, market))
        bucket = self.responses.get(symbol, {})
        resp = bucket.get(market)
        if resp == "raise":
            raise ConnectionError(f"simulated EXCD={market} reject")
        if resp is None:
            # Default: KIS-style zero-priced quote (paper behaviour for
            # symbols outside this exchange).
            return _q(symbol, market, last=0.0, ask=0.0)
        return resp  # type: ignore[return-value]


def _bind(adapter):
    """Bind the unbound method to a fake adapter so we can call
    ``get_quote_with_exchange_fallback`` without instantiating a real
    KisBrokerAdapter (which would try to load auth)."""
    return KisBrokerAdapter.get_quote_with_exchange_fallback.__get__(
        adapter, type(adapter))


class TestQuoteExchangeFallback(unittest.TestCase):

    def test_nasd_hit_returns_immediately(self):
        """Apple lives on NASD → first call wins, no NYSE/AMEX probe."""
        adapter = _FakeAdapter({
            "AAPL": {"NASD": _q("AAPL", "NASD", last=195.0, ask=195.10)},
        })
        fn = _bind(adapter)
        q = fn("AAPL", preferred_market="NASD")
        self.assertIsNotNone(q)
        self.assertEqual(q.market, "NASD")
        self.assertEqual(q.ask, 195.10)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0], ("AAPL", "NASD"))

    def test_nyse_listed_symbol_falls_through_to_nyse(self):
        """JBL is NYSE-listed: NASD returns zero, NYSE returns a real
        quote, helper returns the NYSE quote."""
        adapter = _FakeAdapter({
            "JBL": {
                "NASD": _q("JBL", "NASD", last=0.0, ask=0.0),
                "NYSE": _q("JBL", "NYSE", last=329.0, ask=329.10),
            },
        })
        fn = _bind(adapter)
        q = fn("JBL", preferred_market="NASD")
        self.assertIsNotNone(q)
        self.assertEqual(q.market, "NYSE")
        self.assertEqual(q.ask, 329.10)
        # Exactly two calls — NASD then NYSE. AMEX never reached.
        self.assertEqual(adapter.calls, [("JBL", "NASD"), ("JBL", "NYSE")])

    def test_dow_path_nyse_after_nasd_zero(self):
        adapter = _FakeAdapter({
            "DOW": {
                "NASD": _q("DOW", "NASD", last=0.0, ask=0.0),
                "NYSE": _q("DOW", "NYSE", last=37.85, ask=37.90),
            },
        })
        fn = _bind(adapter)
        q = fn("DOW", preferred_market="NASD")
        self.assertIsNotNone(q)
        self.assertEqual(q.market, "NYSE")

    def test_nasd_exception_does_not_block_nyse_probe(self):
        """A raise on NASD is treated as "try the next exchange",
        not as a fatal error."""
        adapter = _FakeAdapter({
            "JBL": {
                "NASD": "raise",
                "NYSE": _q("JBL", "NYSE", last=329.0, ask=329.10),
            },
        })
        fn = _bind(adapter)
        q = fn("JBL", preferred_market="NASD")
        self.assertIsNotNone(q)
        self.assertEqual(q.market, "NYSE")

    def test_all_zero_returns_none_so_caller_can_fall_back(self):
        adapter = _FakeAdapter({
            "GHOST": {
                "NASD": _q("GHOST", "NASD", last=0.0, ask=0.0),
                "NYSE": _q("GHOST", "NYSE", last=0.0, ask=0.0),
                "AMEX": _q("GHOST", "AMEX", last=0.0, ask=0.0),
            },
        })
        fn = _bind(adapter)
        q = fn("GHOST", preferred_market="NASD")
        self.assertIsNone(q)
        # All three exchanges were tried.
        self.assertEqual(
            [c[1] for c in adapter.calls], ["NASD", "NYSE", "AMEX"])

    def test_all_raise_returns_none(self):
        adapter = _FakeAdapter({
            "X": {"NASD": "raise", "NYSE": "raise", "AMEX": "raise"},
        })
        fn = _bind(adapter)
        q = fn("X", preferred_market="NASD")
        self.assertIsNone(q)

    def test_last_only_quote_is_acceptable(self):
        """KIS sometimes returns ask=None but last>0 (low-volume
        names off-hours). The helper should still accept that as a
        usable quote rather than walking past it."""
        adapter = _FakeAdapter({
            "QUIET": {
                "NASD": _q("QUIET", "NASD", last=12.5, ask=None),
            },
        })
        fn = _bind(adapter)
        q = fn("QUIET", preferred_market="NASD")
        self.assertIsNotNone(q)
        self.assertEqual(q.last, 12.5)
        self.assertIsNone(q.ask)

    def test_preferred_market_nyse_starts_with_nyse(self):
        """A symbol whose intent already says NYSE should be tried
        on NYSE first — we should NOT pay an extra NASD round trip."""
        adapter = _FakeAdapter({
            "JBL": {
                "NYSE": _q("JBL", "NYSE", last=329.0, ask=329.10),
            },
        })
        fn = _bind(adapter)
        q = fn("JBL", preferred_market="NYSE")
        self.assertIsNotNone(q)
        self.assertEqual(adapter.calls[0], ("JBL", "NYSE"))


# ──────────────────────────────────────────────────────────────────────
# End-to-end: R10D-3 helper consumes the fallback quote_fn and the
# JBL/DOW limits no longer fall back to reco_close.
# ──────────────────────────────────────────────────────────────────────
class TestQuoteFallbackEndToEndAgainstR10D3(unittest.TestCase):

    def test_jbl_dow_no_longer_quote_zero(self):
        from phase3.autotrade import intents_io
        from phase3.autotrade.intents_io import IntentBuildWarning

        adapter = _FakeAdapter({
            "MRNA": {"NASD": _q("MRNA", "NASD", last=46.0, ask=46.18)},
            "JBL":  {"NASD": _q("JBL",  "NASD", last=0.0, ask=0.0),
                     "NYSE": _q("JBL",  "NYSE", last=329.0, ask=329.10)},
            "DOW":  {"NASD": _q("DOW",  "NASD", last=0.0, ask=0.0),
                     "NYSE": _q("DOW",  "NYSE", last=37.85, ask=37.90)},
        })
        fn_method = _bind(adapter)
        def _quote_fn(symbol, market):
            return fn_method(symbol, preferred_market=market)

        cands = [
            intents_io.BuyCandidate(
                run_id="rid", rec_row_id=73, ticker="MRNA",
                action="BUY_MORE", reco_shares=2, reco_price=48.11,
                rank=2, regime="SIDE", market="NASD",
                actionable=True, raw_row={}),
            intents_io.BuyCandidate(
                run_id="rid", rec_row_id=75, ticker="JBL",
                action="BUY_MORE", reco_shares=2, reco_price=338.73,
                rank=12, regime="SIDE", market="NASD",
                actionable=True, raw_row={}),
            intents_io.BuyCandidate(
                run_id="rid", rec_row_id=74, ticker="DOW",
                action="BUY_NEW", reco_shares=3, reco_price=38.56,
                rank=15, regime="SIDE", market="NASD",
                actionable=True, raw_row={}),
        ]
        warnings: List[IntentBuildWarning] = []
        rows = intents_io.candidates_to_intent_rows(
            cands, limit_pad_pct=0.0, quote_fn=_quote_fn,
            quote_pad_pct=0.1, warnings_out=warnings)
        sources = {r["symbol"]: r["_quote_source"] for r in rows}
        # All three should successfully refresh, none should fall back.
        self.assertEqual(sources["MRNA"], "quote_refreshed_below_reco")
        self.assertEqual(sources["JBL"], "quote_refreshed_below_reco")
        self.assertEqual(sources["DOW"], "quote_refreshed_below_reco")
        # No fallback warnings.
        self.assertEqual(warnings, [])
        # JBL/DOW now carry their NYSE quote ref price.
        ref_by_t = {r["symbol"]: r.get("_quote_ref_price") for r in rows}
        self.assertEqual(ref_by_t["JBL"], 329.10)
        self.assertEqual(ref_by_t["DOW"], 37.90)


if __name__ == "__main__":
    unittest.main()
