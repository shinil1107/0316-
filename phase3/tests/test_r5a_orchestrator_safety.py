"""Round 5A safety acceptance — verifies the three P1 fixes from
`CURSOR_HANDOFF_AUTOTRADING_V0_R4_CODEX_REVIEW_R2.md`.

Tests
-----
1. `--submit` without `--run-id` must abort with rc=2 and refuse to
   touch broker state. (Codex R2 P1-2)
2. With a prior `submitted` event for the same deterministic
   `client_order_id` in the JSONL, a second `_process_intent` call
   must NOT call `adapter.place_order` and must log a UNKNOWN /
   duplicate-skip event. (Codex R2 P1-1)
3. If `echo_poll` raises after `place_order` succeeds, the
   `broker_order_id` must be preserved on a UNKNOWN transition event,
   `out["submitted"]` must remain True, and `_process_intent` must
   return cleanly (no exception bubbles up). (Codex R2 P1-3 inner guard)

These tests do not hit any network. Adapter is mocked. KIS_ENV in the
existing `.env` is `paper`, which is fine because test 1 never reaches
the broker (it aborts on the new --run-id preflight before that).

Run from the repo root:

    PYTHONPATH=. python3 -m phase3.tests.test_r5a_orchestrator_safety
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
PHASE3_DIR = HERE.parent
REPO_ROOT = PHASE3_DIR.parent
for _p in (str(REPO_ROOT), str(PHASE3_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Imports after path injection
from phase3.autotrade import orchestrator as orc  # noqa: E402
from phase3.autotrade.fill_resolver import FillResolution  # noqa: F401  E402
from phase3.autotrade.intents import ResolvedIntent  # noqa: E402
from phase3.autotrade.kis_broker_adapter import (  # noqa: E402
    CashBalance,
    OrderIntent,
    PlacedOrder,
    Position,
)
from phase3.autotrade.order_state import OrderState, StatusSource  # noqa: E402
from phase3.autotrade.order_store import (  # noqa: E402
    OrderStore,
    build_client_order_id,
    new_autotrade_run_id,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _make_resolved_intent(
    *,
    run_id: str = "20260515_test_daily",
    rec_row_id: int = 99,
    ticker: str = "FAKE",
    qty: int = 1,
    limit_price: float = 10.00,
) -> ResolvedIntent:
    """Build a ResolvedIntent that does not need quote/position data."""
    client_id = build_client_order_id(
        run_id=run_id, rec_row_id=rec_row_id, side="BUY", qty=qty,
    )
    return ResolvedIntent(
        run_id=run_id,
        rec_row_id=rec_row_id,
        ticker=ticker,
        action="BUY_NEW",
        side="BUY",
        qty=qty,
        artifact_price=limit_price,
        quote_last=limit_price,
        quote_bid=None,
        quote_ask=None,
        limit_price=limit_price,
        limit_source="test",
        risk_flags=[],
        payload={"OVRS_EXCG_CD": "NASD", "PDNO": ticker, "ORD_QTY": str(qty)},
        headers={"tr_id": "VTTT1002U", "custtype": "P"},
        client_order_id=client_id,
        note="",
    )


class _FakeAdapter:
    """Minimal stand-in for KisBrokerAdapter.

    Tracks calls so the test can assert place_order was (or was not)
    invoked. `place_order_behavior` is a callable so tests can switch
    between "return SUBMITTED" and "raise" without subclassing.
    """

    def __init__(
        self,
        *,
        positions: Optional[List[Position]] = None,
        cash_available: float = 1_000_000.0,
        place_order_behavior: Optional[Any] = None,
    ):
        self.positions = positions or []
        self.cash_available = cash_available
        self.place_order_behavior = place_order_behavior
        self.place_order_calls: List[Dict[str, Any]] = []
        self.get_positions_calls = 0
        self.get_cash_calls = 0

    def get_positions(self, *, market: str = "NASD", max_pages: int = 50):
        self.get_positions_calls += 1
        return list(self.positions)

    def get_cash(self, *, market: str = "NASD",
                 ref_symbol: str = "AAPL",
                 ref_price: Optional[float] = None):
        self.get_cash_calls += 1
        return CashBalance(
            base_ccy="USD", total=self.cash_available,
            available=self.cash_available, asof="test",
        )

    def place_order(self, oi: OrderIntent, *, dry_run: bool = True):
        self.place_order_calls.append({
            "client_order_id": oi.client_order_id,
            "symbol": oi.symbol, "qty": oi.qty,
            "dry_run": dry_run,
        })
        if self.place_order_behavior is not None:
            return self.place_order_behavior(oi, dry_run)
        # default: dry_run echo
        return PlacedOrder(
            client_order_id=oi.client_order_id,
            broker_order_id=None if dry_run else "0000999888",
            status="dry_run" if dry_run else "submitted",
            intent=oi,
            submitted_at="2026-05-15T00:00:00.000+00:00",
            raw_response_summary={"rt_cd": "0", "ODNO": "0000999888" if not dry_run else None},
        )


# ──────────────────────────────────────────────────────────────────────
# Test cases
# ──────────────────────────────────────────────────────────────────────
class TestSubmitRequiresRunId(unittest.TestCase):
    """Codex R5A-P1.2: `--submit` without `--run-id` must abort."""

    def test_submit_without_run_id_aborts(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = orc.main(["run", "--paper", "--submit"])
        out = buf.getvalue()
        self.assertEqual(
            rc, 2,
            f"expected abort rc=2; got rc={rc}\noutput:\n{out}",
        )
        self.assertIn(
            "--run-id is required for submit mode", out,
            f"missing the explicit abort message. output:\n{out}",
        )

    def test_dry_run_without_run_id_is_allowed(self):
        """Sanity: dry-run discovery mode is still allowed (only --submit
        is restricted). We don't run cmd_run end-to-end here — we only
        verify the preflight does not abort early on this combination."""
        # We can't easily run cmd_run end-to-end without a real artifact,
        # but we can confirm the args.submit==False path skips the new
        # guard. argparse is sufficient.
        ap_main = orc.main
        # Patch cmd_run to a sentinel so the test stops at preflight.
        called = {"hit": False}

        def fake_cmd_run(args):
            called["hit"] = True
            # immediately bail; we just want to confirm we reached cmd_run.
            return 99

        orig = orc.cmd_run
        orc.cmd_run = fake_cmd_run
        try:
            rc = ap_main(["run", "--paper"])
        finally:
            orc.cmd_run = orig
        # We reached cmd_run; rc=99 from the sentinel.
        self.assertTrue(called["hit"])
        self.assertEqual(rc, 99)


class TestDuplicateGuardOrdering(unittest.TestCase):
    """Codex R5A-P1.1: a prior SUBMITTED event must block re-submission
    even after the orchestrator adds a fresh INTENT_CREATED log line."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="r5a-dup-"))
        self.jsonl = self.tmpdir / "autotrade_orders.jsonl"
        self.store = OrderStore(self.jsonl)
        self.intent = _make_resolved_intent()
        self.client_id = self.intent.client_order_id

        # Pre-seed JSONL with a prior SUBMITTED event for the same client_id.
        # This simulates: yesterday's --submit run wrote SUBMITTED + FILLED
        # before a crash. Today's restart should not re-submit.
        self.store.log_transition(
            autotrade_run_id="at-prior-run",
            mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker,
            market="NASD",
            side="BUY",
            qty_intended=self.intent.qty,
            limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            broker_order_id="0000049999",
            state=OrderState.SUBMITTED,
            status_source=StatusSource.PLACE_ORDER_ACK,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_prior_submitted_blocks_resubmission(self):
        fake = _FakeAdapter()
        result = orc._process_intent(
            adapter=fake,
            store=self.store,
            intent=self.intent,
            artifact_run_id=self.intent.run_id,
            autotrade_run_id="at-new-run",
            mode="paper_submit",
            echo_polls=1,
            echo_interval_sec=0.0,
        )

        self.assertEqual(
            len(fake.place_order_calls), 0,
            f"duplicate guard failed: place_order called "
            f"{len(fake.place_order_calls)} times when it should have been 0",
        )
        self.assertTrue(result["duplicate_skipped"])
        self.assertEqual(result["final_state"], "duplicate_skipped")

        # JSONL must have one extra UNKNOWN transition with the
        # duplicate-guard note, and NOT have any new SUBMITTED or
        # INTENT_CREATED for this client_id in the new run.
        events = list(self.store.read_events())
        transitions = [e for e in events if e.event_kind == "transition"]
        # Prior SUBMITTED (1) + new UNKNOWN (1) = 2 transitions total.
        self.assertEqual(len(transitions), 2)
        new_unknown = [
            e for e in transitions
            if e.raw.get("autotrade_run_id") == "at-new-run"
            and e.raw.get("state") == "unknown"
        ]
        self.assertEqual(len(new_unknown), 1)
        self.assertIn("duplicate guard", new_unknown[0].raw.get("note") or "")

        # And specifically: no INTENT_CREATED event from the new run.
        new_intent_created = [
            e for e in transitions
            if e.raw.get("autotrade_run_id") == "at-new-run"
            and e.raw.get("state") == "intent_created"
        ]
        self.assertEqual(
            len(new_intent_created), 0,
            "INTENT_CREATED must NOT be written when the duplicate guard "
            "fires (otherwise it would shadow prior active state)",
        )

    def test_dry_run_with_prior_filled_also_blocks(self):
        """Even in dry_run mode, a prior FILLED should short-circuit."""
        # Replace prior SUBMITTED with FILLED to cover that branch.
        self.store.log_transition(
            autotrade_run_id="at-prior-run",
            mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker,
            market="NASD",
            side="BUY",
            qty_intended=self.intent.qty,
            qty_filled=self.intent.qty,
            qty_remaining=0,
            limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            broker_order_id="0000049999",
            state=OrderState.FILLED,
            status_source=StatusSource.POSITION_DELTA,
            fill_price=10.05,
            fill_price_source="paper_cash_delta",
        )
        fake = _FakeAdapter()
        result = orc._process_intent(
            adapter=fake,
            store=self.store,
            intent=self.intent,
            artifact_run_id=self.intent.run_id,
            autotrade_run_id="at-dryrun-new",
            mode="dry_run",
            echo_polls=1,
            echo_interval_sec=0.0,
        )
        self.assertEqual(len(fake.place_order_calls), 0)
        self.assertEqual(result["final_state"], "duplicate_skipped")


class TestPostSubmitExceptionFinalization(unittest.TestCase):
    """Codex R5A-P1.3: post-submit exceptions must produce a durable
    UNKNOWN event with the broker_order_id preserved, and
    `_process_intent` must return cleanly."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="r5a-finalize-"))
        self.jsonl = self.tmpdir / "autotrade_orders.jsonl"
        self.store = OrderStore(self.jsonl)
        self.intent = _make_resolved_intent(rec_row_id=101, ticker="OOPS")

        # Adapter where place_order succeeds with an ODNO.
        def _ok_place(oi: OrderIntent, dry_run: bool):
            return PlacedOrder(
                client_order_id=oi.client_order_id,
                broker_order_id="0000777666",
                status="submitted",
                intent=oi,
                submitted_at="2026-05-15T00:00:00.000+00:00",
                raw_response_summary={"rt_cd": "0", "ODNO": "0000777666"},
            )
        self.fake = _FakeAdapter(place_order_behavior=_ok_place)

        # Monkey-patch echo_poll on the orchestrator module to raise.
        self._orig_echo = orc.echo_poll
        orc.echo_poll = self._raising_echo_poll

    def tearDown(self):
        orc.echo_poll = self._orig_echo
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _raising_echo_poll(*args, **kwargs):
        raise RuntimeError("simulated transient network outage during echo")

    def test_exception_after_submitted_logged_with_odno(self):
        result = orc._process_intent(
            adapter=self.fake,
            store=self.store,
            intent=self.intent,
            artifact_run_id=self.intent.run_id,
            autotrade_run_id="at-fail-run",
            mode="paper_submit",
            echo_polls=1,
            echo_interval_sec=0.0,
        )

        # The function must NOT propagate the exception.
        self.assertEqual(result["final_state"], "unknown")
        self.assertTrue(
            result["submitted"],
            "ODNO came back from place_order, so submitted must remain True "
            "for accurate run-level accounting",
        )
        # place_order was called exactly once.
        self.assertEqual(len(self.fake.place_order_calls), 1)

        events = list(self.store.read_events())
        transitions = [e for e in events if e.event_kind == "transition"]

        # Expect: intent_created → submitted → unknown
        states = [t.raw.get("state") for t in transitions]
        self.assertEqual(
            states, ["intent_created", "submitted", "unknown"],
            f"unexpected state sequence: {states}",
        )

        # Final UNKNOWN must carry the broker_order_id.
        last = transitions[-1]
        self.assertEqual(last.raw.get("state"), "unknown")
        self.assertEqual(last.raw.get("broker_order_id"), "0000777666")
        self.assertIsNotNone(last.raw.get("error"))
        self.assertIn("RuntimeError", last.raw.get("error") or "")
        self.assertIn(
            "exception during paper_submit path", last.raw.get("note") or "",
        )


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Make sure CWD does not affect imports
    os.chdir(str(REPO_ROOT))
    unittest.main(verbosity=2)
