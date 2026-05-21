"""R10F-S — explicit operator-cleared event for stuck client_order_ids.

Background
----------

R5B-P1.1 hardened ``OrderStore.is_already_active`` to scan the *full*
event history for a given cid — any ACTIVE-class transition ever seen
permanently blocks future submits, even after the cid has been
cancelled cleanly and the broker has nothing live. That stance is the
right default (it's why a transient ccnl disconnect can never
double-submit), but it had no escape hatch other than manually moving
the JSONL aside.

R10F-S adds one: ``OrderStore.log_operator_cleared`` writes a single
``operator_cleared`` event for a cid; ``is_already_active`` now walks
the cid's events in append order and lets a cleared row neutralise
ACTIVE evidence that *predates* it, while a fresh ACTIVE row after a
clear re-arms the guard.

Surfaces under test:

1. ``log_operator_cleared`` writes a well-formed event.
2. ``is_already_active`` flips False once a cleared event lands after
   any combination of ACTIVE / blocking-UNKNOWN rows.
3. A new ACTIVE row after the clear re-arms the guard.
4. The clearing event is cid-scoped (does not touch sibling cids).
5. ``find_latest_blocking_by_client_id`` returns ``None`` after clear,
   then returns the *post-clear* ACTIVE row when one lands.
6. ``find_stuck_client_order_ids`` enumerates exactly the cids that
   ``is_already_active`` would return True for.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.order_state import OrderState, StatusSource
from phase3.autotrade.order_store import OrderStore


def _new_store(tmp_root: Path) -> OrderStore:
    return OrderStore(tmp_root / "autotrade_orders.jsonl")


def _add_active(store: OrderStore, *, cid: str, state: OrderState,
                broker_order_id: str = "0000035200",
                ticker: str = "CIEN") -> None:
    """Helper: write one transition with an ACTIVE-ish state."""
    store.log_transition(
        autotrade_run_id="at-test",
        mode="paper_submit",
        run_id="20260520_test",
        rec_row_id=74,
        ticker=ticker,
        market="NASD",
        side="BUY",
        qty_intended=1,
        limit_price=561.0,
        client_order_id=cid,
        state=state,
        status_source=StatusSource.PLACE_ORDER_ACK,
        broker_order_id=broker_order_id,
        note="r10fs test active row",
    )


def _add_cancelled(store: OrderStore, *, cid: str) -> None:
    store.log_transition(
        autotrade_run_id="at-test",
        mode="paper_submit",
        run_id="20260520_test",
        rec_row_id=74,
        ticker="CIEN", market="NASD", side="BUY",
        qty_intended=1, limit_price=561.0,
        client_order_id=cid,
        state=OrderState.CANCELLED,
        status_source=StatusSource.CANCEL_ACK,
        broker_order_id="0000035200",
        note="cancel confirmed",
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Writer
# ──────────────────────────────────────────────────────────────────────
class TestLogOperatorCleared(unittest.TestCase):

    def test_writes_well_formed_event(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            ev = store.log_operator_cleared(
                autotrade_run_id="at-recover-001",
                run_id="20260521_test",
                client_order_id="co-20260520_x-74-B-1-CIEN",
                broker_state_at_clear="cancelled",
                operator_note="manual broker probe confirmed cancelled",
                broker_probe={"odno": "0000035200", "nccs_qty": "0"},
            )
            self.assertEqual(ev.event_kind, "operator_cleared")
            self.assertEqual(
                ev.raw["client_order_id"],
                "co-20260520_x-74-B-1-CIEN")
            self.assertEqual(ev.raw["broker_state_at_clear"], "cancelled")
            self.assertIn("event_id", ev.raw)
            self.assertIn("event_ts", ev.raw)

    def test_event_persists_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            store.log_operator_cleared(
                autotrade_run_id="at-r-1",
                run_id="20260521_test",
                client_order_id="co-X-1",
                broker_state_at_clear="rejected",
            )
            # Re-open from disk
            store2 = _new_store(Path(td))
            events = list(store2.read_events())
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_kind, "operator_cleared")


# ──────────────────────────────────────────────────────────────────────
# 2. is_already_active flips False after a clear
# ──────────────────────────────────────────────────────────────────────
class TestIsAlreadyActiveWithClear(unittest.TestCase):

    def test_clear_unblocks_after_submitted_then_cancelled(self):
        """Today's CIEN sequence: SUBMITTED → OPEN_OR_PENDING →
        CANCELLED. Without R10F-S, is_already_active=True forever.
        With R10F-S clear → False."""
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            cid = "co-cien-1"
            _add_active(store, cid=cid, state=OrderState.SUBMITTED)
            _add_active(store, cid=cid, state=OrderState.OPEN_OR_PENDING)
            _add_cancelled(store, cid=cid)
            self.assertTrue(store.is_already_active(cid))

            store.log_operator_cleared(
                autotrade_run_id="at-recover",
                run_id="20260521",
                client_order_id=cid,
                broker_state_at_clear="cancelled",
            )
            self.assertFalse(store.is_already_active(cid))

    def test_clear_unblocks_blocking_unknown(self):
        """UNKNOWN rows with broker_order_id present are blocking via
        ``_unknown_is_blocking``. A clear should still neutralise them."""
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            cid = "co-unk-1"
            store.log_transition(
                autotrade_run_id="at-test", mode="paper_submit",
                run_id="20260520", rec_row_id=10, ticker="CIEN",
                market="NASD", side="BUY", qty_intended=1, limit_price=100.0,
                client_order_id=cid,
                state=OrderState.UNKNOWN,
                status_source=StatusSource.UNKNOWN,
                broker_order_id="0000035200",
                note="ccnl poll retry — transient",
            )
            self.assertTrue(store.is_already_active(cid))

            store.log_operator_cleared(
                autotrade_run_id="at-recover", run_id="20260521",
                client_order_id=cid, broker_state_at_clear="cancelled",
            )
            self.assertFalse(store.is_already_active(cid))

    def test_clear_then_new_active_rearms_guard(self):
        """A fresh paper-submit (same deterministic cid) after the
        clear must re-arm the duplicate guard. Otherwise the operator
        could clear once and accidentally submit twice."""
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            cid = "co-rearm-1"
            _add_active(store, cid=cid, state=OrderState.SUBMITTED)
            _add_cancelled(store, cid=cid)
            store.log_operator_cleared(
                autotrade_run_id="at-r1", run_id="20260521",
                client_order_id=cid, broker_state_at_clear="cancelled",
            )
            self.assertFalse(store.is_already_active(cid))

            # New attempt reuses the deterministic cid.
            _add_active(store, cid=cid, state=OrderState.SUBMITTED)
            self.assertTrue(store.is_already_active(cid))

    def test_clear_is_cid_scoped(self):
        """A clear for cid A must not unblock cid B."""
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            _add_active(store, cid="co-A", state=OrderState.SUBMITTED)
            _add_active(store, cid="co-B", state=OrderState.SUBMITTED)
            store.log_operator_cleared(
                autotrade_run_id="at-r", run_id="20260521",
                client_order_id="co-A", broker_state_at_clear="cancelled",
            )
            self.assertFalse(store.is_already_active("co-A"))
            self.assertTrue(store.is_already_active("co-B"))

    def test_no_history_is_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            self.assertFalse(store.is_already_active("co-nope"))


# ──────────────────────────────────────────────────────────────────────
# 3. find_latest_blocking_by_client_id respects clear
# ──────────────────────────────────────────────────────────────────────
class TestFindLatestBlocking(unittest.TestCase):

    def test_returns_none_after_clear(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            cid = "co-fl-1"
            _add_active(store, cid=cid, state=OrderState.SUBMITTED)
            _add_cancelled(store, cid=cid)
            self.assertIsNotNone(store.find_latest_blocking_by_client_id(cid))

            store.log_operator_cleared(
                autotrade_run_id="at-r", run_id="20260521",
                client_order_id=cid, broker_state_at_clear="cancelled",
            )
            self.assertIsNone(store.find_latest_blocking_by_client_id(cid))

    def test_returns_post_clear_active_row(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            cid = "co-fl-2"
            _add_active(store, cid=cid, state=OrderState.SUBMITTED,
                        broker_order_id="0000035200")
            store.log_operator_cleared(
                autotrade_run_id="at-r", run_id="20260521",
                client_order_id=cid, broker_state_at_clear="cancelled",
            )
            _add_active(store, cid=cid, state=OrderState.SUBMITTED,
                        broker_order_id="0000035700")
            found = store.find_latest_blocking_by_client_id(cid)
            self.assertIsNotNone(found)
            self.assertEqual(found.broker_order_id, "0000035700")


# ──────────────────────────────────────────────────────────────────────
# 4. find_stuck_client_order_ids
# ──────────────────────────────────────────────────────────────────────
class TestFindStuckClientOrderIds(unittest.TestCase):

    def test_empty_store_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            self.assertEqual(store.find_stuck_client_order_ids(), [])

    def test_lists_stuck_cids_in_first_appearance_order(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            _add_active(store, cid="co-A", state=OrderState.SUBMITTED)
            _add_active(store, cid="co-B", state=OrderState.OPEN_OR_PENDING)
            _add_cancelled(store, cid="co-B")
            _add_active(store, cid="co-C", state=OrderState.FILLED)
            self.assertEqual(
                store.find_stuck_client_order_ids(),
                ["co-A", "co-B", "co-C"],
            )

    def test_cleared_cids_are_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            store = _new_store(Path(td))
            _add_active(store, cid="co-A", state=OrderState.SUBMITTED)
            _add_active(store, cid="co-B", state=OrderState.SUBMITTED)
            store.log_operator_cleared(
                autotrade_run_id="at-r", run_id="20260521",
                client_order_id="co-A", broker_state_at_clear="cancelled",
            )
            self.assertEqual(
                store.find_stuck_client_order_ids(),
                ["co-B"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
