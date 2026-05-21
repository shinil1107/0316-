"""R10F-Q3 — reprice quote chase.

R8 / R10F-Q2 reprice walks a deterministic ladder (current_limit *
(1 + step_bps/10000)). When the market jumps faster than the ladder,
every reprice stays below the new market and the order dies unfilled
(observed 2026-05-20: CIEN gapped +1.5 % overnight).

R10F-Q3 calls ``quote_fn(symbol, market)`` at each reprice and picks
``new_limit = min(max(step_limit, quote_chase), ceiling)``. We never
overrun the slippage cap, but the bid can leapfrog the ladder when
the market has moved more than ``reprice_step_bps`` since submit.

This file covers the pure decision surface (no real broker), the
audit-log emission (so an operator can see *which* path chose the
new limit), and the failure-mode contract: any exception from
quote_fn must NOT block reprice — we fall back to step-only.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import order_manager
from phase3.autotrade.order_manager import (
    OrderManagementPolicy,
    _extract_quote_ref,
    manage_order,
)
from phase3.autotrade.order_state import OrderState
from phase3.autotrade.order_store import OrderStore

from phase3.tests.test_r8_order_manager import (
    FakeBroker, FakeClock, _intent,
)


@dataclass
class _FakeQuote:
    ask: Optional[float] = None
    last: Optional[float] = None


def _new_store() -> OrderStore:
    tmp = tempfile.mkdtemp(prefix="r10fq3_store_")
    return OrderStore(Path(tmp) / "autotrade_orders.jsonl")


def _short_policy(**overrides) -> OrderManagementPolicy:
    """Test policy chosen so the maths is easy to inspect by hand:
    * original=100, step=10 % per reprice (10000/100 = 100 bps)
    * ceiling=20 % above original (2000 bps)
    * 2 reprice attempts so each test can step through both rungs.
    """
    base = dict(
        poll_interval_sec=1.0,
        max_wait_sec=2.0,
        max_reprice_attempts=2,
        reprice_step_bps=1000.0,   # +10 % per reprice
        max_total_slippage_bps=2000.0,  # +20 % ceiling
        cancel_before_reprice=True,
        cancel_confirm_wait_sec=1.0,
        allow_market_order=False,
    )
    base.update(overrides)
    return OrderManagementPolicy(**base)


# ──────────────────────────────────────────────────────────────────────
# _extract_quote_ref
# ──────────────────────────────────────────────────────────────────────
class TestExtractQuoteRef(unittest.TestCase):

    def test_prefers_ask_when_positive(self):
        q = _FakeQuote(ask=101.5, last=99.0)
        self.assertEqual(_extract_quote_ref(q), 101.5)

    def test_falls_through_to_last_when_ask_zero(self):
        q = _FakeQuote(ask=0.0, last=99.0)
        self.assertEqual(_extract_quote_ref(q), 99.0)

    def test_falls_through_to_last_when_ask_none(self):
        q = _FakeQuote(ask=None, last=99.0)
        self.assertEqual(_extract_quote_ref(q), 99.0)

    def test_returns_none_when_both_unusable(self):
        q = _FakeQuote(ask=0.0, last=0.0)
        self.assertIsNone(_extract_quote_ref(q))

    def test_none_quote_yields_none(self):
        self.assertIsNone(_extract_quote_ref(None))


# ──────────────────────────────────────────────────────────────────────
# End-to-end reprice path with FakeBroker + FakeClock
# ──────────────────────────────────────────────────────────────────────
class TestRepriceQuoteChaseE2E(unittest.TestCase):
    """Drive ``manage_order`` through one reprice and assert the
    chosen limit picks the quote chase when it's above the ladder,
    the ladder when the quote chase is below, and the ceiling when
    the quote chase would overrun the slippage cap."""

    def _setup(self, *, market_ask: Optional[float], pad_pct: float = 0.0):
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        # Intent at 100; ladder step = 10 (100 → 110 on first reprice).
        intent = _intent(symbol="APA", qty=1, limit=100.0)

        if market_ask is not None:
            def quote_fn(symbol, market):
                return _FakeQuote(ask=market_ask, last=market_ask)
        else:
            quote_fn = None

        return broker, clock, store, intent, quote_fn

    def _run_one_reprice(self, broker, clock, store, intent, quote_fn,
                         pad_pct=0.0):
        outcome = manage_order(
            intent,
            adapter=broker, store=store, policy=_short_policy(),
            autotrade_run_id="at-fq3", run_id="20260520_fq3",
            rec_row_id=1,
            time_provider=clock.time, sleep_fn=clock.sleep,
            quote_fn=quote_fn, quote_pad_pct=pad_pct,
        )
        return outcome

    def test_quote_chase_leapfrogs_ladder(self):
        """Market ask=115 > ladder rung 110. Reprice picks 115."""
        broker, clock, store, intent, quote_fn = self._setup(market_ask=115.0)
        outcome = self._run_one_reprice(
            broker, clock, store, intent, quote_fn, pad_pct=0.0)
        # Loop ends in cancellation because nothing ever filled, but
        # the audit log must show reprice #1 used 115.0.
        self.assertEqual(outcome.final_state, OrderState.CANCELLED)
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        self.assertGreaterEqual(len(rp_events), 1)
        rp1 = rp_events[0]
        self.assertAlmostEqual(rp1.raw["limit_price"], 115.0, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertEqual(extra.get("quote_source"), "quote_chase")
        self.assertAlmostEqual(extra.get("quote_chase_limit"), 115.0, places=4)
        self.assertAlmostEqual(extra.get("step_limit"), 110.0, places=4)

    def test_ladder_wins_when_quote_chase_below(self):
        """Market ask=105 < ladder rung 110. Reprice picks 110."""
        broker, clock, store, intent, quote_fn = self._setup(market_ask=105.0)
        outcome = self._run_one_reprice(
            broker, clock, store, intent, quote_fn, pad_pct=0.0)
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        self.assertGreaterEqual(len(rp_events), 1)
        rp1 = rp_events[0]
        self.assertAlmostEqual(rp1.raw["limit_price"], 110.0, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertEqual(extra.get("quote_source"), "step_only")

    def test_ceiling_clamps_runaway_quote(self):
        """Market ask=200 (≫ ceiling=120). Reprice clamps to 120."""
        broker, clock, store, intent, quote_fn = self._setup(market_ask=200.0)
        outcome = self._run_one_reprice(
            broker, clock, store, intent, quote_fn, pad_pct=0.0)
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        self.assertGreaterEqual(len(rp_events), 1)
        rp1 = rp_events[0]
        # ceiling = 100 * (1 + 2000/10000) = 120
        self.assertAlmostEqual(rp1.raw["limit_price"], 120.0, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertEqual(extra.get("quote_source"), "quote_chase")
        # Quote_chase_limit is the unclamped pad'd ask = 200; the final
        # limit (120) reflects the ceiling clamp.
        self.assertAlmostEqual(extra.get("quote_chase_limit"), 200.0, places=4)

    def test_quote_pad_adds_buffer_on_top_of_ask(self):
        """Market ask=113, pad=1 % → quote_chase = 113 * 1.01 = 114.13.
        Ladder is 110, so quote_chase wins."""
        broker, clock, store, intent, quote_fn = self._setup(market_ask=113.0)
        outcome = self._run_one_reprice(
            broker, clock, store, intent, quote_fn, pad_pct=1.0)
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        rp1 = rp_events[0]
        self.assertAlmostEqual(rp1.raw["limit_price"], 114.13, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertAlmostEqual(extra.get("quote_chase_limit"), 114.13, places=4)
        self.assertAlmostEqual(extra.get("quote_chase_ref_price"), 113.0, places=4)

    def test_quote_fn_exception_falls_back_to_ladder(self):
        """quote_fn raises → manage_order keeps walking. Reprice #1
        uses the ladder rung 110 with quote_source='step_only'."""
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(symbol="APA", qty=1, limit=100.0)

        def bad_quote(symbol, market):
            raise ConnectionError("kis disconnect")

        outcome = manage_order(
            intent,
            adapter=broker, store=store, policy=_short_policy(),
            autotrade_run_id="at-fq3", run_id="20260520_fq3",
            rec_row_id=1,
            time_provider=clock.time, sleep_fn=clock.sleep,
            quote_fn=bad_quote, quote_pad_pct=0.0,
        )
        self.assertEqual(outcome.final_state, OrderState.CANCELLED)
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        self.assertGreaterEqual(len(rp_events), 1)
        rp1 = rp_events[0]
        self.assertAlmostEqual(rp1.raw["limit_price"], 110.0, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertEqual(extra.get("quote_source"), "step_only")

    def test_no_quote_fn_keeps_legacy_step_only(self):
        """quote_fn=None → step_only path, identical to pre-R10F-Q3."""
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(symbol="APA", qty=1, limit=100.0)

        outcome = manage_order(
            intent,
            adapter=broker, store=store, policy=_short_policy(),
            autotrade_run_id="at-fq3", run_id="20260520_fq3",
            rec_row_id=1,
            time_provider=clock.time, sleep_fn=clock.sleep,
            quote_fn=None,
        )
        events = list(store.read_events())
        rp_events = [
            e for e in events
            if "reprice #" in (e.raw.get("note") or "")
        ]
        self.assertGreaterEqual(len(rp_events), 1)
        rp1 = rp_events[0]
        self.assertAlmostEqual(rp1.raw["limit_price"], 110.0, places=4)
        extra = (rp1.raw["raw_broker_row"] or {}).get("_r8_extra", {})
        self.assertEqual(extra.get("quote_source"), "step_only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
