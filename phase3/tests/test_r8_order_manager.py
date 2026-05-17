"""R8-D — `phase3.autotrade.order_manager.manage_order` acceptance tests.

Covers R8 §6 acceptance list:

  - immediate full fill              -> no cancel/reprice
  - open until timeout               -> cancel requested
  - open -> cancel confirmed         -> reprice submitted
  - partial fill                     -> cancel remaining by default
  - unknown state                    -> no retry, blocking UNKNOWN logged
  - cancel unconfirmed               -> no reprice
  - max reprice attempts reached     -> stop with unresolved_open / cancelled state

Plus a duplicate-guard test (`OrderStore.is_already_active`) and an
end-to-end "reprice fills at new limit" replay.

Everything is driven against a programmable ``FakeBroker`` with an
injected monotonic clock — the suite runs in milliseconds.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.kis_broker_adapter import (
    CancelResult,
    OrderIntent,
    PlacedOrder,
)
from phase3.autotrade.order_ids import normalize_odno
from phase3.autotrade.order_state import OrderState, StatusSource
from phase3.autotrade.order_store import OrderStore, build_client_order_id
from phase3.autotrade.order_manager import (
    OrderManagementPolicy,
    ManagedOrderOutcome,
    manage_order,
    reprice_limit_buy,
)


# ──────────────────────────────────────────────────────────────────────
# Fake broker + clock
# ──────────────────────────────────────────────────────────────────────
class FakeClock:
    """Monotonic clock that advances by `sleep(dt)` calls. Tests inject
    schedules that mutate FakeBroker state at specific times."""

    def __init__(self):
        self.now = 0.0
        # list of (when, fn) pairs; sleep advances time and runs all
        # callbacks whose `when` is <= the new now.
        self._scheduled: List = []

    def time(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += float(dt)
        while self._scheduled and self._scheduled[0][0] <= self.now:
            _, fn = self._scheduled.pop(0)
            fn()

    def schedule_at(self, when: float, fn) -> None:
        self._scheduled.append((float(when), fn))
        self._scheduled.sort(key=lambda x: x[0])


class FakeBroker:
    """Minimal stand-in for KisBrokerAdapter that supports just enough
    surface for order_manager. Records every interaction so the tests
    can assert on submit / cancel sequencing.
    """

    def __init__(self, *, accept_cancel: bool = True,
                 reject_place: bool = False):
        self.rows: List[Dict[str, Any]] = []
        self.placed: List[PlacedOrder] = []
        self.cancels: List[CancelResult] = []
        self._next_odno_int = 41000
        self.accept_cancel = accept_cancel
        self.reject_place = reject_place

    def _next_odno(self) -> str:
        n = self._next_odno_int
        self._next_odno_int += 1
        return f"{n:010d}"

    # ── KisBrokerAdapter-compatible surface ──────────────────────
    def place_order(self, intent: OrderIntent, *, dry_run: bool = True) -> PlacedOrder:
        if self.reject_place:
            po = PlacedOrder(
                client_order_id=intent.client_order_id,
                broker_order_id=None, status="rejected",
                intent=intent,
                submitted_at="2026-05-16T00:00:00.000+00:00",
                raw_response_summary={"error": "fake_reject"},
            )
            self.placed.append(po)
            return po

        odno_padded = self._next_odno()
        po = PlacedOrder(
            client_order_id=intent.client_order_id,
            broker_order_id=odno_padded, status="submitted",
            intent=intent,
            submitted_at="2026-05-16T00:00:00.000+00:00",
            raw_response_summary={"ODNO": odno_padded, "tr_id": "VTTT1002U"},
        )
        self.placed.append(po)
        # Append an open ccnl row using the stripped surface form (mirrors
        # real KIS overseas behaviour observed in R6 / R7-B).
        self.rows.append({
            "odno": str(int(odno_padded)),
            "orgn_odno": "",
            "pdno": intent.symbol,
            "sll_buy_dvsn_cd_name": "매수" if intent.side == "BUY" else "매도",
            "ft_ord_qty": str(int(intent.qty)),
            "ft_ccld_qty": "0",
            "nccs_qty": str(int(intent.qty)),
            "ft_ord_unpr3": f"{float(intent.limit_price):.8f}",
            "ft_ccld_unpr3": "0.00000000",
            "rvse_cncl_dvsn": "00",
            "rvse_cncl_dvsn_name": "보통",
            "prcs_stat_name": "",
            "rjct_rson_name": "",
        })
        return po

    def get_order_history(self, *args, **kwargs) -> List[Dict[str, Any]]:
        # Return shallow copies so callers cannot mutate broker state.
        return [dict(r) for r in self.rows]

    def cancel_order(self, *, broker_order_id, symbol, market="NASD",
                     qty, dry_run=True, note: str = "") -> CancelResult:
        if dry_run:
            res = CancelResult(
                broker_order_id=broker_order_id, cancel_order_id=None,
                accepted=True, dry_run=True,
                symbol=symbol, market=market, qty=int(qty),
                submitted_at="2026-05-16T00:00:00.000+00:00",
                payload={}, raw_response_summary={"mode": "dry_run"},
                note="fake dry-run",
            )
            self.cancels.append(res)
            return res

        if not self.accept_cancel:
            res = CancelResult(
                broker_order_id=broker_order_id, cancel_order_id=None,
                accepted=False, dry_run=False,
                symbol=symbol, market=market, qty=int(qty),
                submitted_at="2026-05-16T00:00:00.000+00:00",
                payload={}, raw_response_summary={"error": "fake_reject"},
                note="fake cancel reject",
            )
            self.cancels.append(res)
            return res

        cancel_odno_padded = self._next_odno()
        # Drop the original row's nccs_qty to 0 and append the sibling cancel row.
        norm_target = normalize_odno(broker_order_id)
        for r in self.rows:
            if normalize_odno(r.get("odno")) == norm_target:
                r["nccs_qty"] = "0"
                break
        side_name = "매수취소"
        for p in reversed(self.placed):
            if p.broker_order_id == broker_order_id:
                side_name = "매도취소" if p.intent.side == "SELL" else "매수취소"
                break
        self.rows.append({
            "odno": str(int(cancel_odno_padded)),
            "orgn_odno": norm_target,
            "pdno": symbol,
            "sll_buy_dvsn_cd_name": side_name,
            "ft_ord_qty": str(int(qty)),
            "ft_ccld_qty": "0",
            "nccs_qty": "0",
            "ft_ord_unpr3": "0.00000000",
            "ft_ccld_unpr3": "0.00000000",
            "rvse_cncl_dvsn": "02",
            "rvse_cncl_dvsn_name": "취소",
        })
        res = CancelResult(
            broker_order_id=broker_order_id,
            cancel_order_id=cancel_odno_padded,
            accepted=True, dry_run=False,
            symbol=symbol, market=market, qty=int(qty),
            submitted_at="2026-05-16T00:00:00.000+00:00",
            payload={}, raw_response_summary={"ODNO": cancel_odno_padded},
            note="fake cancel ok",
        )
        self.cancels.append(res)
        return res

    # ── helpers that tests use to drive the broker state ─────────
    def fill_now(self, broker_order_id: str, *, filled: int, price: float) -> None:
        norm = normalize_odno(broker_order_id)
        for r in self.rows:
            if normalize_odno(r.get("odno")) == norm:
                ordered = int(float(r.get("ft_ord_qty") or 0))
                r["ft_ccld_qty"] = str(int(filled))
                r["nccs_qty"] = str(max(ordered - int(filled), 0))
                r["ft_ccld_unpr3"] = f"{float(price):.8f}"
                break

    def reject_now(self, broker_order_id: str, *, reason: str = "잔고부족") -> None:
        norm = normalize_odno(broker_order_id)
        for r in self.rows:
            if normalize_odno(r.get("odno")) == norm:
                r["rjct_rson_name"] = reason
                break


# ──────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────
def _intent(symbol="APA", qty=1, limit=18.85, side="BUY", run_id="20260516_001_daily",
            rec_row_id=42) -> OrderIntent:
    cid = build_client_order_id(run_id=run_id, rec_row_id=rec_row_id,
                                side=side, qty=qty)
    return OrderIntent(
        symbol=symbol, market="NASD", side=side, qty=qty,
        order_type="LIMIT", limit_price=limit, client_order_id=cid,
        note="r8d test",
    )


def _tight_policy(**overrides) -> OrderManagementPolicy:
    """Tests need millisecond-scale timing so we never sleep for real."""
    base = dict(
        poll_interval_sec=1.0,
        max_wait_sec=5.0,
        max_reprice_attempts=2,
        reprice_step_bps=10.0,           # +10 bps per reprice
        max_total_slippage_bps=35.0,
        cancel_before_reprice=True,
        cancel_confirm_wait_sec=1.0,
        allow_market_order=False,
    )
    base.update(overrides)
    return OrderManagementPolicy(**base)


def _new_store() -> OrderStore:
    tmp = tempfile.mkdtemp(prefix="r8d_store_")
    return OrderStore(Path(tmp) / "autotrade_orders.jsonl")


# ──────────────────────────────────────────────────────────────────────
# Acceptance tests
# ──────────────────────────────────────────────────────────────────────
class TestImmediateFullFill(unittest.TestCase):
    def test_fills_on_first_poll_no_cancel_no_reprice(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=4, limit=37.82)

        # Schedule: at t=0.5 (before the first 1s poll), mark order filled.
        def _do_fill():
            assert broker.placed, "expected place_order to have been called"
            broker.fill_now(broker.placed[-1].broker_order_id,
                            filled=4, price=37.815)

        clock.schedule_at(0.5, _do_fill)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=_tight_policy(),
            autotrade_run_id="at-test-fill",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.FILLED)
        self.assertEqual(outcome.qty_filled, 4.0)
        self.assertEqual(outcome.cancel_attempts, 0)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertEqual(outcome.avg_fill_price, 37.815)
        self.assertEqual(len(broker.cancels), 0)
        # JSONL: should contain SUBMITTED + FILLED transitions.
        states = [ev.state for ev in store.read_events()
                  if ev.event_kind == "transition"]
        self.assertIn(OrderState.SUBMITTED, states)
        self.assertIn(OrderState.FILLED, states)


class TestTimeoutTriggersCancelAndReprice(unittest.TestCase):
    def test_open_timeout_cancel_confirm_reprice_then_fill(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy(max_wait_sec=3.0,
                                cancel_confirm_wait_sec=1.0,
                                max_reprice_attempts=2)

        # First attempt: never fills, will time out and be cancelled.
        # Second attempt (reprice, +10 bps to 18.8689): fills at 0.5s into
        # the second attempt's poll loop. We schedule against absolute
        # clock time, not the attempt-relative time, so we wait until
        # the reprice broker_order_id appears in `broker.placed`.
        def _fill_after_reprice():
            # If there are 2 placements, fill the second one.
            if len(broker.placed) >= 2:
                broker.fill_now(broker.placed[-1].broker_order_id,
                                filled=1, price=18.8689)

        # Schedule a few opportunistic fill checks after the cancel/reprice
        # window opens (~4-5s of simulated time).
        for t in (5.0, 5.5, 6.0, 6.5, 7.0, 8.0):
            clock.schedule_at(t, _fill_after_reprice)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-reprice",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.FILLED,
                         msg=f"got state={outcome.final_state} note={outcome.note}")
        self.assertEqual(outcome.qty_filled, 1.0)
        self.assertEqual(outcome.cancel_attempts, 1)
        self.assertEqual(outcome.reprice_attempts, 1)
        # The last broker_order_id should be the reprice submission.
        self.assertEqual(outcome.last_broker_order_id,
                         broker.placed[-1].broker_order_id)
        # JSONL: should contain SUBMITTED + OPEN_OR_PENDING + CANCEL_REQUESTED +
        # CANCELLED + REPLACE_REQUESTED + SUBMITTED + FILLED.
        states = [ev.state for ev in store.read_events()
                  if ev.event_kind == "transition"]
        for required in (
            OrderState.SUBMITTED, OrderState.OPEN_OR_PENDING,
            OrderState.CANCEL_REQUESTED, OrderState.CANCELLED,
            OrderState.REPLACE_REQUESTED, OrderState.FILLED,
        ):
            self.assertIn(required, states,
                          msg=f"missing {required.value} in {[s.value for s in states]}")


class TestTimeoutWithoutFillReachesMaxReprice(unittest.TestCase):
    def test_three_timeouts_ends_in_cancelled_state(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy(max_wait_sec=2.0,
                                cancel_confirm_wait_sec=0.5,
                                max_reprice_attempts=2)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-maxreprice",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        # We never fill. After original + 2 reprices, the manager stops.
        self.assertEqual(outcome.final_state, OrderState.CANCELLED)
        self.assertEqual(outcome.qty_filled, 0.0)
        self.assertEqual(outcome.cancel_attempts, 3,
                         msg=f"cancels={outcome.cancel_attempts}")
        self.assertEqual(outcome.reprice_attempts, 2)
        self.assertIn("max_reprice_attempts", outcome.note)


class TestPartialFillCancelsRemainder(unittest.TestCase):
    def test_partial_then_cancel_terminal_partial(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=5, limit=10.00)
        policy = _tight_policy(max_wait_sec=5.0,
                                cancel_confirm_wait_sec=0.5)

        # Partial-fill 2 of 5 at t=0.5 (before first poll at t=1.0).
        clock.schedule_at(0.5, lambda: broker.fill_now(
            broker.placed[-1].broker_order_id, filled=2, price=10.00))

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-partial",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(outcome.qty_filled, 2.0)
        self.assertEqual(outcome.qty_remaining, 3.0)
        self.assertEqual(outcome.cancel_attempts, 1)
        self.assertEqual(outcome.reprice_attempts, 0)  # no reprice on partial
        self.assertEqual(len(broker.cancels), 1)
        # Cancel was for the remainder (3 shares).
        self.assertEqual(broker.cancels[0].qty, 3)


class TestUnknownStopsAndLogsBlocking(unittest.TestCase):
    def test_position_moved_with_zero_fill_is_unknown(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy()

        # Position lookup returns +1 from baseline → ccnl-zero-vs-pos-move
        # conflict → UNKNOWN at first poll. We never call cancel.
        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-unknown",
            run_id="20260516_001_daily", rec_row_id=42,
            pre_position_qty=0.0,
            position_lookup=lambda sym, mkt: 1.0,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        self.assertEqual(len(broker.cancels), 0)
        # Store must have a blocking UNKNOWN row.
        self.assertTrue(store.is_already_active(intent.client_order_id))


class TestCancelUnconfirmedNoReprice(unittest.TestCase):
    def test_cancel_dry_run_returns_unconfirmed_no_reprice(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy(max_wait_sec=2.0)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-cancel-unconfirmed",
            run_id="20260516_001_daily", rec_row_id=42,
            cancel_dry_run=True,                 # never actually mutates broker
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.CANCEL_REQUESTED)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertEqual(outcome.cancel_attempts, 1)
        self.assertIn("cancel_dry_run", outcome.note)

    def test_cancel_rejected_by_broker_is_unknown_no_reprice(self) -> None:
        broker = FakeBroker(accept_cancel=False)
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy(max_wait_sec=2.0)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-test-cancel-rejected",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertTrue(store.is_already_active(intent.client_order_id))


class TestRejectAtSubmit(unittest.TestCase):
    def test_place_order_rejected_no_poll_no_cancel(self) -> None:
        broker = FakeBroker(reject_place=True)
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=_tight_policy(),
            autotrade_run_id="at-test-reject",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.REJECTED)
        self.assertEqual(outcome.cancel_attempts, 0)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertEqual(len(broker.cancels), 0)


class TestDuplicateGuardBlocksSubmit(unittest.TestCase):
    def test_existing_active_event_blocks_resubmit(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)

        # Pre-populate the store with an active SUBMITTED row for this cid.
        store.log_transition(
            autotrade_run_id="at-prior", mode="paper_submit",
            run_id="20260516_001_daily", rec_row_id=42,
            ticker=intent.symbol, market=intent.market, side=intent.side,
            qty_intended=intent.qty, limit_price=intent.limit_price,
            client_order_id=intent.client_order_id,
            state=OrderState.SUBMITTED, status_source=StatusSource.PLACE_ORDER_ACK,
            broker_order_id="0000099999",
        )
        self.assertTrue(store.is_already_active(intent.client_order_id))

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=_tight_policy(),
            autotrade_run_id="at-test-dup",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        self.assertEqual(len(broker.placed), 0,
                         msg="duplicate guard must prevent place_order")
        self.assertIn("duplicate guard", outcome.note)


class TestRepriceMath(unittest.TestCase):
    """Pure math — no broker / clock needed."""

    def test_step_under_ceiling_uses_step(self) -> None:
        p = OrderManagementPolicy(reprice_step_bps=10.0, max_total_slippage_bps=35.0)
        # 100 * 1.001 = 100.1
        self.assertEqual(reprice_limit_buy(original_limit=100.0,
                                            current_limit=100.0, policy=p), 100.1)

    def test_step_above_ceiling_clamps_to_ceiling(self) -> None:
        p = OrderManagementPolicy(reprice_step_bps=200.0, max_total_slippage_bps=35.0)
        # step = 100 * 1.02 = 102.0 ; ceiling = 100 * 1.0035 = 100.35
        self.assertEqual(reprice_limit_buy(original_limit=100.0,
                                            current_limit=100.0, policy=p), 100.35)

    def test_compound_step(self) -> None:
        p = OrderManagementPolicy(reprice_step_bps=10.0, max_total_slippage_bps=35.0)
        first = reprice_limit_buy(original_limit=100.0,
                                   current_limit=100.0, policy=p)
        second = reprice_limit_buy(original_limit=100.0,
                                    current_limit=first, policy=p)
        self.assertGreater(second, first)
        self.assertLessEqual(second, 100.35)


# ──────────────────────────────────────────────────────────────────────
# R9-A1 — cancel-race outcomes
# ──────────────────────────────────────────────────────────────────────
class TestCancelRaceOutcomes(unittest.TestCase):
    """R9 §3 acceptance list: cancel-race fill / partial / clean cancel /
    everything-else-is-UNKNOWN, plus the post-fix property that the
    duplicate guard never sees a CANCELLED log for a filled order."""

    def _setup_and_timeout(self, *, broker: FakeBroker, on_cancel):
        """Run manage_order with a tight 1-shot timeout and inject
        ``on_cancel`` to mutate broker state between cancel send and
        the post-cancel ccnl re-poll."""
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=1, limit=18.85)
        policy = _tight_policy(
            max_wait_sec=2.0, cancel_confirm_wait_sec=1.0,
            max_reprice_attempts=2,
        )

        # Patch the broker's cancel_order so we can run `on_cancel`
        # AFTER the manager has sent the cancel but BEFORE the ccnl
        # re-poll runs (sleep(cancel_confirm_wait_sec) below).
        orig_cancel = broker.cancel_order

        def _instrumented_cancel(**kwargs):
            res = orig_cancel(**kwargs)
            on_cancel(broker, kwargs)
            return res

        broker.cancel_order = _instrumented_cancel  # type: ignore[assignment]

        return manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-r9a1",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        ), store

    def test_cancel_race_full_fill_returns_filled_no_reprice(self) -> None:
        """Broker fills the original order during the cancel race.
        The fixed manager must return FILLED, never log CANCELLED, and
        never submit a reprice child BUY."""
        broker = FakeBroker()

        def on_cancel(b: FakeBroker, _kwargs):
            # Race: the cancel landed at the broker, but ALSO the
            # broker filled the original order at the same time. Real
            # KIS would report the fill in the original row but the
            # cancel sibling never actually became authoritative
            # because there is no remaining qty to cancel. We mimic
            # that by *removing* the sibling row our FakeBroker
            # would otherwise have appended, and marking the original
            # row as fully filled.
            cancel_target = _kwargs["broker_order_id"]
            # Drop the sibling cancel row our fake auto-appended on
            # the real cancel call.
            b.rows = [r for r in b.rows
                      if not (r.get("rvse_cncl_dvsn") == "02"
                              and normalize_odno(r.get("orgn_odno")) == normalize_odno(cancel_target))]
            b.fill_now(cancel_target, filled=1, price=18.86)

        outcome, store = self._setup_and_timeout(broker=broker, on_cancel=on_cancel)
        self.assertEqual(outcome.final_state, OrderState.FILLED)
        self.assertEqual(outcome.qty_filled, 1.0)
        self.assertEqual(outcome.qty_remaining, 0.0)
        self.assertEqual(outcome.reprice_attempts, 0)
        # Critical: only ONE place_order call (the original). No
        # reprice child intent was ever submitted.
        self.assertEqual(len(broker.placed), 1)
        # And the JSONL must NOT contain a CANCELLED transition for
        # this client_order_id — that was the P1 mis-classification.
        states = [
            ev.state for ev in store.read_events()
            if ev.event_kind == "transition"
        ]
        self.assertNotIn(OrderState.CANCELLED, states)
        self.assertIn(OrderState.FILLED, states)

    def test_cancel_race_partial_fill_with_cancel_sibling_returns_partial_no_reprice(self) -> None:
        """Broker partial-filled before the cancel landed. Sibling
        cancel row IS present for the remainder. Must return
        PARTIALLY_FILLED + cancel_row_odno, never reprice."""
        broker = FakeBroker()

        def on_cancel(b: FakeBroker, _kwargs):
            # Race: 0 < filled < ordered, sibling cancel row already
            # appended by FakeBroker.cancel_order for the remainder.
            cancel_target = _kwargs["broker_order_id"]
            # We need ordered>1 for this scenario.
            b.fill_now(cancel_target, filled=1, price=18.86)

        # Override the intent in the helper: this scenario needs qty>=2.
        clock = FakeClock()
        store = _new_store()
        intent = _intent(qty=3, limit=18.85)
        policy = _tight_policy(max_wait_sec=2.0, cancel_confirm_wait_sec=1.0)
        orig_cancel = broker.cancel_order

        def _instrumented_cancel(**kwargs):
            res = orig_cancel(**kwargs)
            on_cancel(broker, kwargs)
            return res

        broker.cancel_order = _instrumented_cancel  # type: ignore[assignment]
        outcome = manage_order(
            intent, adapter=broker, store=store, policy=policy,
            autotrade_run_id="at-r9a1-partial",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(outcome.qty_filled, 1.0)
        self.assertEqual(outcome.qty_remaining, 2.0)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertEqual(len(broker.placed), 1)
        # No CANCELLED transition for the original order (the partial
        # outcome itself is the terminal note).
        states = [
            ev.state for ev in store.read_events()
            if ev.event_kind == "transition"
        ]
        self.assertNotIn(OrderState.CANCELLED, states)
        self.assertIn(OrderState.PARTIALLY_FILLED, states)

    def test_cancelled_after_timeout_can_reprice(self) -> None:
        """Plain CANCELLED (zero fill + cancel sibling) — manager
        should be eligible to submit a reprice child BUY. This is the
        case the R9 fix must NOT regress."""
        broker = FakeBroker()
        # No `on_cancel` instrumentation; FakeBroker.cancel_order
        # already adds the sibling row + zeros nccs_qty, which is
        # exactly a clean CANCELLED.
        outcome, store = self._setup_and_timeout(
            broker=broker, on_cancel=lambda *_a, **_k: None,
        )
        # We expect: original timeout → cancel → CANCELLED → reprice
        # (which never fills because broker still has no fill schedule)
        # → reach max_reprice_attempts. So the final state is CANCELLED
        # and we DID issue reprices.
        self.assertEqual(outcome.final_state, OrderState.CANCELLED)
        self.assertGreater(outcome.reprice_attempts, 0)
        self.assertEqual(outcome.qty_filled, 0.0)
        states = [
            ev.state for ev in store.read_events()
            if ev.event_kind == "transition"
        ]
        # Reprice path must show CANCELLED + REPLACE_REQUESTED.
        self.assertIn(OrderState.CANCELLED, states)
        self.assertIn(OrderState.REPLACE_REQUESTED, states)

    def test_cancel_unconfirmed_open_state_never_reprices(self) -> None:
        """Post-cancel re-poll shows OPEN (cancel hasn't landed yet)
        → manager must abort to UNKNOWN, never reprice."""
        broker = FakeBroker()

        def on_cancel(b: FakeBroker, _kwargs):
            # Race the opposite way: drop the sibling cancel row, leave
            # the original row still OPEN. Now post-cancel re-poll will
            # see OPEN_OR_PENDING and the new R9-A1 branch must abort
            # to UNKNOWN.
            cancel_target = _kwargs["broker_order_id"]
            b.rows = [r for r in b.rows
                      if not (r.get("rvse_cncl_dvsn") == "02"
                              and normalize_odno(r.get("orgn_odno")) == normalize_odno(cancel_target))]
            # Also un-zero nccs_qty so the row classifies OPEN, not UNKNOWN.
            for r in b.rows:
                if normalize_odno(r.get("odno")) == normalize_odno(cancel_target):
                    r["nccs_qty"] = r.get("ft_ord_qty", "1")
                    break

        outcome, store = self._setup_and_timeout(broker=broker, on_cancel=on_cancel)
        self.assertEqual(outcome.final_state, OrderState.UNKNOWN)
        self.assertEqual(outcome.reprice_attempts, 0)
        self.assertEqual(len(broker.placed), 1)
        # The duplicate guard MUST see this as blocking so a naive
        # retry can't resubmit.
        self.assertTrue(store.is_already_active(outcome.intent.client_order_id))


# ──────────────────────────────────────────────────────────────────────
# R9-C global_halt — entry-gate
# ──────────────────────────────────────────────────────────────────────
class TestGlobalHaltBlocksManageOrder(unittest.TestCase):
    """The R9-C halt flag MUST stop a new order from reaching the
    broker. This is enforced at manage_order entry, BEFORE the
    duplicate guard / market-order check, so it can short-circuit
    even in tests with an otherwise-clean store."""

    def test_halt_set_returns_rejected_with_note(self) -> None:
        import tempfile, json
        from phase3.autotrade import global_halt as gh
        with tempfile.TemporaryDirectory() as tmp:
            halt_path = Path(tmp) / "halt.json"
            gh.write_halt(halt=True, reason="test_pressed_stop", path=halt_path)
            os.environ["AUTOTRADE_HALT_FILE"] = str(halt_path)
            try:
                broker = FakeBroker()
                store = _new_store()
                intent = _intent()
                outcome = manage_order(
                    intent, adapter=broker, store=store,
                    policy=_tight_policy(),
                    autotrade_run_id="at-halt-test",
                    run_id="20260516_001_daily", rec_row_id=99,
                    time_provider=FakeClock().time,
                    sleep_fn=FakeClock().sleep,
                )
            finally:
                os.environ.pop("AUTOTRADE_HALT_FILE", None)
            self.assertEqual(outcome.final_state, OrderState.REJECTED)
            self.assertIn("global_halt", outcome.note)
            # No order was ever submitted.
            self.assertEqual(len(broker.placed), 0)

    def test_halt_cleared_allows_submit(self) -> None:
        import tempfile
        from phase3.autotrade import global_halt as gh
        with tempfile.TemporaryDirectory() as tmp:
            halt_path = Path(tmp) / "halt.json"
            gh.write_halt(halt=False, reason="cleared", path=halt_path)
            os.environ["AUTOTRADE_HALT_FILE"] = str(halt_path)
            try:
                broker = FakeBroker()
                clock = FakeClock()
                store = _new_store()
                intent = _intent(qty=1, limit=18.85)

                def _do_fill():
                    broker.fill_now(broker.placed[-1].broker_order_id,
                                     filled=1, price=18.86)
                clock.schedule_at(0.5, _do_fill)

                outcome = manage_order(
                    intent, adapter=broker, store=store,
                    policy=_tight_policy(),
                    autotrade_run_id="at-halt-cleared",
                    run_id="20260516_001_daily", rec_row_id=99,
                    time_provider=clock.time, sleep_fn=clock.sleep,
                )
            finally:
                os.environ.pop("AUTOTRADE_HALT_FILE", None)
            self.assertEqual(outcome.final_state, OrderState.FILLED)
            self.assertEqual(len(broker.placed), 1)


class TestMarketOrderRefused(unittest.TestCase):
    def test_market_intent_is_refused(self) -> None:
        broker = FakeBroker()
        clock = FakeClock()
        store = _new_store()
        intent = OrderIntent(symbol="APA", market="NASD", side="BUY", qty=1,
                              order_type="MARKET", limit_price=None,
                              client_order_id="co-test-mk")

        outcome = manage_order(
            intent, adapter=broker, store=store, policy=_tight_policy(),
            autotrade_run_id="at-test-market",
            run_id="20260516_001_daily", rec_row_id=42,
            time_provider=clock.time, sleep_fn=clock.sleep,
        )

        self.assertEqual(outcome.final_state, OrderState.REJECTED)
        self.assertEqual(len(broker.placed), 0)
        self.assertIn("non-LIMIT", outcome.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
