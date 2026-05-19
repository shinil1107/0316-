"""R10D-1 — ccnl polling transient absorption.

R10C observed KIS paper's ``inquire-ccnl`` randomly raising
``ConnectionError('Connection aborted.', RemoteDisconnected(...))``
30–80 s after a successful broker accept. That single transient
exception was enough to flip ``manage_order`` into UNKNOWN ->
rc=2 hard_stop, blocking T10 apply for the whole batch.

This test surface covers:

1. The pure ``_now_classify_with_retry`` helper: identity-equality of
   the success path, retry counting, audit-log invocation per failed
   attempt, exhausted-budget re-raise.
2. End-to-end ``manage_order`` flow: ccnl raises once then succeeds
   → FILLED (no UNKNOWN); ccnl raises N+1 times → UNKNOWN with
   distinguishing note; OrderStore audit rows present.

The test uses the existing ``FakeBroker`` + ``FakeClock`` fixtures
from ``test_r8_order_manager`` for E2E coverage of the new behaviour.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import order_manager
from phase3.autotrade.order_manager import (
    OrderManagementPolicy,
    _now_classify_with_retry,
    manage_order,
)
from phase3.autotrade.order_state import (
    BrokerOrderState,
    OrderState,
)
from phase3.autotrade.order_store import OrderStore

# Reuse the heavyweight fixtures already proved out in R8.
from phase3.tests.test_r8_order_manager import (
    FakeBroker, FakeClock, _intent, _new_store, _tight_policy,
)


# ──────────────────────────────────────────────────────────────────────
# Pure helper: _now_classify_with_retry
# ──────────────────────────────────────────────────────────────────────
class _FlakyAdapter:
    """Adapter whose ``get_order_history`` raises N times then returns
    a synthetic ccnl row set. ``classify_from_full_ccnl`` is patched
    out so we can assert deterministically without crafting realistic
    KIS rows here."""

    def __init__(self, *, fail_n_times: int, exc_factory=None):
        self._remaining_failures = int(fail_n_times)
        self._exc_factory = exc_factory or (lambda i: ConnectionError(
            f"simulated remote disconnect #{i}"))
        self.call_count = 0

    def get_order_history(self, *args, **kwargs) -> List[Dict[str, Any]]:
        self.call_count += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise self._exc_factory(self.call_count)
        return [{"odno": "0000040195", "ft_ccld_qty": "1", "ft_ord_qty": "1"}]


def _fake_classify_filled(rows, *, target_odno, position_delta):
    return BrokerOrderState(
        broker_order_id=target_odno or "",
        normalized_odno=(target_odno or "").lstrip("0"),
        symbol="APA",
        side="매수",
        ordered_qty=1.0, filled_qty=1.0, remaining_qty=0.0,
        avg_fill_price=100.0,
        limit_price=100.0,
        state=OrderState.FILLED,
    )


class TestNowClassifyWithRetry(unittest.TestCase):

    def test_zero_retries_is_one_shot(self):
        """retry_count=0 reproduces pre-R10D behaviour: one call,
        exception propagates immediately, log_attempt called once
        (for the failed attempt itself)."""
        adapter = _FlakyAdapter(fail_n_times=1)
        log_calls: List[tuple] = []
        sleeps: List[float] = []
        with self.assertRaises(ConnectionError):
            _now_classify_with_retry(
                adapter,
                broker_order_id="0000040195",
                position_delta=0.0,
                retry_count=0,
                backoff_sec=2.0,
                sleep_fn=sleeps.append,
                log_attempt=lambda a, m, e: log_calls.append((a, m, type(e).__name__)),
            )
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(len(log_calls), 1)
        self.assertEqual(log_calls[0], (0, 1, "ConnectionError"))
        self.assertEqual(sleeps, [])

    def test_second_call_succeeds_after_one_transient(self):
        adapter = _FlakyAdapter(fail_n_times=1)
        log_calls: List[tuple] = []
        sleeps: List[float] = []
        with mock.patch.object(
            order_manager, "classify_from_full_ccnl",
            side_effect=_fake_classify_filled,
        ):
            bs = _now_classify_with_retry(
                adapter,
                broker_order_id="0000040195",
                position_delta=0.0,
                retry_count=2,
                backoff_sec=0.1,
                sleep_fn=sleeps.append,
                log_attempt=lambda a, m, e: log_calls.append((a, m, type(e).__name__)),
            )
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual(bs.state, OrderState.FILLED)
        self.assertEqual(len(log_calls), 1)
        self.assertEqual(log_calls[0], (0, 3, "ConnectionError"))
        # Exactly one backoff sleep between the failed attempt and the
        # successful retry.
        self.assertEqual(sleeps, [0.1])

    def test_all_retries_fail_re_raises_last_exception(self):
        adapter = _FlakyAdapter(fail_n_times=5)  # > budget
        log_calls: List[tuple] = []
        sleeps: List[float] = []
        with self.assertRaises(ConnectionError):
            _now_classify_with_retry(
                adapter,
                broker_order_id="0000040195",
                position_delta=0.0,
                retry_count=2,
                backoff_sec=0.1,
                sleep_fn=sleeps.append,
                log_attempt=lambda a, m, e: log_calls.append((a, m, type(e).__name__)),
            )
        # Budget = 1 + 2 = 3 attempts → 3 failures → 3 log rows.
        self.assertEqual(adapter.call_count, 3)
        self.assertEqual(len(log_calls), 3)
        self.assertEqual([c[0] for c in log_calls], [0, 1, 2])
        # 2 backoffs between attempts (the final attempt does NOT sleep
        # again because there's no further retry).
        self.assertEqual(sleeps, [0.1, 0.1])

    def test_log_attempt_callback_failure_is_swallowed(self):
        """A broken audit logger must not mask the original ccnl
        exception path."""
        adapter = _FlakyAdapter(fail_n_times=3)

        def _bad_logger(*args, **kwargs):
            raise RuntimeError("audit logger broken")
        with self.assertRaises(ConnectionError):
            _now_classify_with_retry(
                adapter,
                broker_order_id="0000040195",
                position_delta=0.0,
                retry_count=2,
                backoff_sec=0.0,
                sleep_fn=lambda *_: None,
                log_attempt=_bad_logger,
            )
        # All retries still consumed despite the broken logger.
        self.assertEqual(adapter.call_count, 3)

    def test_negative_retry_count_is_clamped_to_zero(self):
        adapter = _FlakyAdapter(fail_n_times=1)
        with self.assertRaises(ConnectionError):
            _now_classify_with_retry(
                adapter,
                broker_order_id="x",
                position_delta=0.0,
                retry_count=-5,
                backoff_sec=1.0,
                sleep_fn=lambda *_: None,
                log_attempt=None,
            )
        self.assertEqual(adapter.call_count, 1)


# ──────────────────────────────────────────────────────────────────────
# E2E: manage_order absorbs a single transient ccnl exception
# ──────────────────────────────────────────────────────────────────────
class _FlakyBroker(FakeBroker):
    """FakeBroker variant whose ``get_order_history`` raises on the
    first ``trip_n_times`` calls AFTER a place_order has been made.
    All other interactions delegate to FakeBroker so cancel/reprice
    bookkeeping stays correct.

    Crucially: the bug we're modelling is that the broker had
    already accepted the order (place_order returned a PlacedOrder)
    and then the *poll* fails — i.e. the broker side IS holding the
    order, we just can't read it back. So once trip_n_times is
    exhausted, the existing rows (already appended by ``place_order``)
    become visible to the classifier."""

    def __init__(self, *, trip_n_times: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._remaining_trips = int(trip_n_times)

    def get_order_history(self, *args, **kwargs):
        if self._remaining_trips > 0:
            self._remaining_trips -= 1
            raise ConnectionError(
                "Connection aborted.', RemoteDisconnected('Remote end "
                "closed connection without response')")
        return super().get_order_history(*args, **kwargs)


class TestManageOrderAbsorbsTransientCcnl(unittest.TestCase):

    def test_one_transient_then_fill_returns_FILLED(self):
        """Pre-R10D: this scenario produced UNKNOWN + rc=2 hard_stop.
        Post-R10D-1: the single transient is absorbed, the second
        ccnl call sees the fill, manage_order returns FILLED."""
        broker = _FlakyBroker(trip_n_times=1)
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=38.98)
        policy = _tight_policy(
            poll_interval_sec=0.5,
            max_wait_sec=5.0,
            ccnl_poll_retry_count=2,
            ccnl_poll_retry_backoff_sec=0.1,
        )

        def _do_fill():
            assert broker.placed
            broker.fill_now(broker.placed[-1].broker_order_id,
                            filled=1, price=38.95)
        clock.schedule_at(0.25, _do_fill)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-r10d-1",
            run_id="20260519_001_daily", rec_row_id=75,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.FILLED)
        self.assertEqual(outcome.qty_filled, 1.0)
        notes = [str(ev.raw.get("note") or "") for ev in store.read_events()
                  if ev.event_kind == "transition"]
        self.assertTrue(
            any("ccnl poll retry 1/3" in n for n in notes),
            f"expected a 'ccnl poll retry 1/3' audit row in {notes!r}",
        )
        # Should still see the SUBMITTED + FILLED transitions.
        states = {ev.state for ev in store.read_events()
                  if ev.event_kind == "transition"}
        self.assertIn(OrderState.SUBMITTED, states)
        self.assertIn(OrderState.FILLED, states)

    def test_all_retries_fail_returns_UNKNOWN(self):
        """When retries are exhausted the conservative UNKNOWN path
        still fires. Hard_stop downstream is unchanged."""
        broker = _FlakyBroker(trip_n_times=99)  # never succeeds
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=38.98)
        policy = _tight_policy(
            poll_interval_sec=0.5,
            max_wait_sec=5.0,
            ccnl_poll_retry_count=2,
            ccnl_poll_retry_backoff_sec=0.1,
        )

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-r10d-1-exhaust",
            run_id="20260519_001_daily", rec_row_id=75,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        retry_rows = [ev for ev in store.read_events()
                       if ev.event_kind == "transition"
                       and "ccnl poll retry" in str(ev.raw.get("note") or "")]
        self.assertEqual(
            len(retry_rows), 3,
            f"expected 3 retry audit rows, got {len(retry_rows)}: "
            f"{[ev.raw.get('note') for ev in retry_rows]}")
        unknown_rows = [ev for ev in store.read_events()
                         if ev.event_kind == "transition"
                         and ev.state == OrderState.UNKNOWN
                         and "after 2 retries" in str(ev.raw.get("note") or "")]
        self.assertEqual(
            len(unknown_rows), 1,
            f"expected one terminal 'after 2 retries' UNKNOWN row, got: "
            f"{[ev.raw.get('note') for ev in unknown_rows]}")

    def test_retry_count_zero_is_backwards_compatible(self):
        """retry_count=0 must reproduce the R10C-and-earlier behaviour
        — single transient = immediate UNKNOWN."""
        broker = _FlakyBroker(trip_n_times=1)
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=38.98)
        policy = _tight_policy(
            poll_interval_sec=0.5,
            max_wait_sec=5.0,
            ccnl_poll_retry_count=0,
        )

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-r10d-1-compat",
            run_id="20260519_001_daily", rec_row_id=75,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        unknown_rows = [ev for ev in store.read_events()
                         if ev.event_kind == "transition"
                         and ev.state == OrderState.UNKNOWN
                         and "after 0 retries" in str(ev.raw.get("note") or "")]
        self.assertEqual(len(unknown_rows), 1)


if __name__ == "__main__":
    unittest.main()
