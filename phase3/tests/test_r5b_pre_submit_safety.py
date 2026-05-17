"""Round 5B pre-submit safety acceptance — verifies the two P1 fixes
and the one P2 fix from
`CURSOR_HANDOFF_AUTOTRADING_V0_R5B_PRE_SUBMIT_SAFETY.md`.

Tests
-----
P1-1 (history-aware duplicate guard):
  1. `submitted → unknown(duplicate guard) → retry` does NOT call
     `place_order` even though the latest event is UNKNOWN.
  2. `submitted → unknown(post-submit exception, broker_order_id
     present) → retry` does NOT call `place_order`.
  3. `dry_run → retry` is NOT blocked (intentional negative case).
  4. `rejected (no broker_order_id, no error) → retry` is NOT blocked.

P1-2 (`had_error` reaches `cmd_run`):
  5. When `_process_intent` returns `had_error=True`, `cmd_run` must
     end with `finalize_status="completed_with_errors"`, exit rc=1,
     and still produce summary.json / report.* (durable finalization).

P2-3 (ccnl zero-fill conflict):
  6. ccnl filled_qty=0 + qty_delta=0 → OPEN_OR_PENDING (regression).
  7. ccnl filled_qty=0 + qty_delta>0 (paper) → UNKNOWN with
     `"conflict"` in note + cash_delta-derived fill_price.
  8. ccnl filled_qty=0 + qty_delta>0 (live) → UNKNOWN with
     `"conflict"` in note and no fill_price.

Run from the repo root:

    PYTHONPATH=. python3 -m phase3.tests.test_r5b_pre_submit_safety
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

HERE = Path(__file__).resolve().parent
PHASE3_DIR = HERE.parent
REPO_ROOT = PHASE3_DIR.parent
for _p in (str(REPO_ROOT), str(PHASE3_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phase3.autotrade import orchestrator as orc  # noqa: E402
from phase3.autotrade.fill_resolver import (  # noqa: E402
    FillResolution,
    resolve_fill_state,
)
from phase3.autotrade.intents import ResolvedIntent  # noqa: E402
from phase3.autotrade.kis_broker_adapter import (  # noqa: E402
    CashBalance,
    EnvConfig,
    OrderIntent,
    PlacedOrder,
    Position,
)
from phase3.autotrade.order_state import OrderState, StatusSource  # noqa: E402
from phase3.autotrade.order_store import (  # noqa: E402
    OrderStore,
    build_client_order_id,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers (copies kept small so this file is self-contained)
# ──────────────────────────────────────────────────────────────────────
def _make_resolved_intent(
    *,
    run_id: str = "20260515_r5b_test_daily",
    rec_row_id: int = 99,
    ticker: str = "FAKE",
    qty: int = 1,
    limit_price: float = 10.00,
) -> ResolvedIntent:
    client_id = build_client_order_id(
        run_id=run_id, rec_row_id=rec_row_id, side="BUY", qty=qty,
    )
    return ResolvedIntent(
        run_id=run_id, rec_row_id=rec_row_id, ticker=ticker,
        action="BUY_NEW", side="BUY", qty=qty,
        artifact_price=limit_price, quote_last=limit_price,
        quote_bid=None, quote_ask=None,
        limit_price=limit_price, limit_source="test",
        risk_flags=[],
        payload={"OVRS_EXCG_CD": "NASD", "PDNO": ticker, "ORD_QTY": str(qty)},
        headers={"tr_id": "VTTT1002U", "custtype": "P"},
        client_order_id=client_id, note="",
    )


class _FakeAdapter:
    """Minimal stand-in for KisBrokerAdapter."""

    def __init__(self, *, place_order_behavior: Optional[Any] = None,
                 cash_available: float = 1_000_000.0):
        self.place_order_behavior = place_order_behavior
        self.cash_available = cash_available
        self.place_order_calls: List[Dict[str, Any]] = []

    def get_positions(self, *, market: str = "NASD", max_pages: int = 50):
        return []

    def get_cash(self, *, market: str = "NASD",
                 ref_symbol: str = "AAPL",
                 ref_price: Optional[float] = None):
        return CashBalance(base_ccy="USD", total=self.cash_available,
                           available=self.cash_available, asof="test")

    def place_order(self, oi: OrderIntent, *, dry_run: bool = True):
        self.place_order_calls.append({
            "client_order_id": oi.client_order_id,
            "symbol": oi.symbol, "qty": oi.qty, "dry_run": dry_run,
        })
        if self.place_order_behavior is not None:
            return self.place_order_behavior(oi, dry_run)
        return PlacedOrder(
            client_order_id=oi.client_order_id,
            broker_order_id=None if dry_run else "0000999888",
            status="dry_run" if dry_run else "submitted",
            intent=oi,
            submitted_at="2026-05-15T00:00:00.000+00:00",
            raw_response_summary={"rt_cd": "0",
                                  "ODNO": None if dry_run else "0000999888"},
        )


# ──────────────────────────────────────────────────────────────────────
# P1-1: history-aware duplicate guard
# ──────────────────────────────────────────────────────────────────────
class TestHistoryAwareDuplicateGuard(unittest.TestCase):
    """Codex R5B-P1.1: a SUBMITTED followed by an UNKNOWN must still
    block the next retry. The previous (latest-event-only) guard would
    have allowed it."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="r5b-dup-"))
        self.jsonl = self.tmpdir / "autotrade_orders.jsonl"
        self.store = OrderStore(self.jsonl)
        self.intent = _make_resolved_intent()
        self.client_id = self.intent.client_order_id

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed_submitted(self):
        self.store.log_transition(
            autotrade_run_id="at-run-A", mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker, market="NASD", side="BUY",
            qty_intended=self.intent.qty, limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            broker_order_id="0000049999",
            state=OrderState.SUBMITTED,
            status_source=StatusSource.PLACE_ORDER_ACK,
        )

    def _seed_duplicate_guard_unknown(self):
        """Simulate what R5A duplicate-guard wrote on retry."""
        self.store.log_transition(
            autotrade_run_id="at-run-B", mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker, market="NASD", side="BUY",
            qty_intended=self.intent.qty, limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            broker_order_id="0000049999",
            state=OrderState.UNKNOWN,
            status_source=StatusSource.LOCAL_INTENT,
            note="duplicate guard: prior state=submitted; not re-submitting",
        )

    def _seed_postsubmit_exception_unknown(self):
        """Simulate post-submit echo_poll exception path."""
        self.store.log_transition(
            autotrade_run_id="at-run-B", mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker, market="NASD", side="BUY",
            qty_intended=self.intent.qty, limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            broker_order_id="0000049999",
            state=OrderState.UNKNOWN,
            status_source=StatusSource.UNKNOWN,
            error="RuntimeError: simulated network error",
            note=("exception during paper_submit path; ODNO present — "
                  "verify before retry"),
        )

    def test_submitted_then_dup_guard_unknown_still_blocks_retry(self):
        self._seed_submitted()
        self._seed_duplicate_guard_unknown()

        # Sanity: the *latest* event is UNKNOWN.
        latest = self.store.find_latest_by_client_id(self.client_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.state, OrderState.UNKNOWN)

        # The history-aware guard must still see the prior SUBMITTED.
        self.assertTrue(self.store.is_already_active(self.client_id))

        # And _process_intent must skip the place_order call.
        fake = _FakeAdapter()
        result = orc._process_intent(
            adapter=fake, store=self.store, intent=self.intent,
            artifact_run_id=self.intent.run_id,
            autotrade_run_id="at-run-C",
            mode="paper_submit",
            echo_polls=1, echo_interval_sec=0.0,
        )
        self.assertEqual(len(fake.place_order_calls), 0,
                         "history-aware guard failed: place_order was called")
        self.assertTrue(result["duplicate_skipped"])
        self.assertFalse(result.get("had_error"),
                         "duplicate-skipped is NOT an error")

    def test_submitted_then_postsubmit_exception_unknown_still_blocks_retry(self):
        self._seed_submitted()
        self._seed_postsubmit_exception_unknown()

        # blocked because the UNKNOWN row carries broker_order_id + the
        # canonical 'verify before retry' note + an error field.
        self.assertTrue(self.store.is_already_active(self.client_id))

        # find_latest_blocking_by_client_id should also return the right row
        bl = self.store.find_latest_blocking_by_client_id(self.client_id)
        self.assertIsNotNone(bl)
        # latest blocking should be the UNKNOWN (it's newer)
        self.assertEqual(bl.state, OrderState.UNKNOWN)

        fake = _FakeAdapter()
        result = orc._process_intent(
            adapter=fake, store=self.store, intent=self.intent,
            artifact_run_id=self.intent.run_id,
            autotrade_run_id="at-run-C",
            mode="paper_submit",
            echo_polls=1, echo_interval_sec=0.0,
        )
        self.assertEqual(len(fake.place_order_calls), 0)
        self.assertTrue(result["duplicate_skipped"])

    def test_dry_run_only_does_not_block_future_submit(self):
        """A history of DRY_RUN events must NOT block. Dry-runs are
        intentionally idempotent and may be repeated."""
        for run in ("at-dry-1", "at-dry-2"):
            self.store.log_transition(
                autotrade_run_id=run, mode="dry_run",
                run_id=self.intent.run_id,
                rec_row_id=self.intent.rec_row_id,
                ticker=self.intent.ticker, market="NASD", side="BUY",
                qty_intended=self.intent.qty,
                limit_price=self.intent.limit_price,
                client_order_id=self.client_id,
                state=OrderState.DRY_RUN,
                status_source=StatusSource.LOCAL_INTENT,
                note="orchestrator dry-run: no transmission",
            )
        self.assertFalse(self.store.is_already_active(self.client_id),
                         "dry-run history must not block real submit")

    def test_rejected_without_broker_id_does_not_block(self):
        """A clean REJECTED with no broker_order_id and no error fields
        (e.g. risk_flags pre-trade rejection) must not block a future
        retry — the order never reached the broker."""
        self.store.log_transition(
            autotrade_run_id="at-reject", mode="paper_submit",
            run_id=self.intent.run_id,
            rec_row_id=self.intent.rec_row_id,
            ticker=self.intent.ticker, market="NASD", side="BUY",
            qty_intended=self.intent.qty,
            limit_price=self.intent.limit_price,
            client_order_id=self.client_id,
            state=OrderState.REJECTED,
            status_source=StatusSource.LOCAL_INTENT,
            # no broker_order_id, no error → not blocking
        )
        self.assertFalse(self.store.is_already_active(self.client_id))


# ──────────────────────────────────────────────────────────────────────
# P1-2: had_error reaches cmd_run finalize / rc
# ──────────────────────────────────────────────────────────────────────
class TestHadErrorPropagatesToFinalize(unittest.TestCase):
    """Codex R5B-P1.2: a per-intent runtime failure (had_error=True)
    must drive `cmd_run` to `finalize_status="completed_with_errors"`
    and `rc=1`, while still producing summary/report artifacts."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="r5b-finalize-"))
        # Build a stand-in artifact directory on disk so cmd_run can
        # write its JSONL / summary / report there.
        self.run_dir = self.tmpdir / "20260515_r5b_test_daily"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run_meta.json").write_text(
            json.dumps({"run_id": "20260515_r5b_test_daily",
                        "status": "executed"})  # executed → dry-run allowed
        )
        (self.run_dir / "recommendations.csv").write_text(
            "Ticker,Action,Shares,Price\nFAKE,BUY_NEW,1,10.00\n"
        )

        self._orig = {
            "load_artifact": orc.load_artifact,
            "resolve_intents": orc.resolve_intents,
            "_process_intent": orc._process_intent,
            "load_env_config": orc.load_env_config,
        }

        fake_intent = _make_resolved_intent()

        def fake_load_artifact(output_dir, *, run_id=None):
            return (
                self.run_dir,
                {"run_id": "20260515_r5b_test_daily", "status": "executed"},
                pd.read_csv(self.run_dir / "recommendations.csv"),
            )

        def fake_resolve_intents(**_kw):
            return ([fake_intent], [])

        def fake_process_intent(**kwargs):
            return {
                "rec_row_id": kwargs["intent"].rec_row_id,
                "ticker": kwargs["intent"].ticker,
                "final_state": "unknown",
                "duplicate_skipped": False,
                "risk_blocked": False,
                "submitted": True,
                "had_error": True,
                "error": "simulated post-submit failure",
                "cash_delta_usd": 0.0,
                "position_delta": 0,
            }

        # Build an EnvConfig the orchestrator preflight accepts.
        fake_env = EnvConfig(
            app_key="x", app_secret="y", account_no="50182047-01",
            account_product_code="01",
            env_name="paper", confirm_live=False, paper_submit_ok=False,
            token_cache_path=Path("/tmp/_r5b_token.json"),
            log_dir=self.tmpdir / "_audit",
        )
        (self.tmpdir / "_audit").mkdir(exist_ok=True)

        orc.load_artifact = fake_load_artifact
        orc.resolve_intents = fake_resolve_intents
        orc._process_intent = fake_process_intent
        orc.load_env_config = lambda: fake_env

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(orc, k, v)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_had_error_makes_rc1_and_completed_with_errors(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = orc.main(["run", "--paper", "--run-id",
                           "20260515_r5b_test_daily", "--quiet"])
        out = buf.getvalue()

        self.assertEqual(
            rc, 1,
            f"expected rc=1 because had_error=True; got rc={rc}\nout:\n{out}",
        )
        self.assertIn(
            "completed_with_errors", out,
            f"completed_with_errors not in output:\n{out}",
        )

        # summary.json must exist and reflect the failure status.
        summary_path = self.run_dir / "autotrade_summary.json"
        self.assertTrue(summary_path.exists(),
                        "finalize-on-error didn't write summary.json")
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["finalize_status"], "completed_with_errors")

        # report.md must also exist (full artifact triple).
        self.assertTrue((self.run_dir / "autotrade_execution_report.md").exists())
        self.assertTrue((self.run_dir / "autotrade_execution_report.json").exists())
        self.assertTrue((self.run_dir / "autotrade_execution_report.csv").exists())


# ──────────────────────────────────────────────────────────────────────
# P2-3: ccnl zero-fill conflict
# ──────────────────────────────────────────────────────────────────────
class TestCcnlZeroFillConflict(unittest.TestCase):
    """Codex R5B-P2.3: ccnl row claiming 0 fills must NOT silently
    overrule position movement. Conflict → UNKNOWN with a clear note."""

    @staticmethod
    def _echo_ccnl(filled_qty: float, *, ordered_qty: float = 1.0,
                   price: float = 10.0, odno: str = "0000777666") -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "odno": odno,
            "ft_ccld_qty3": str(int(filled_qty)),
            "ft_ord_qty3": str(int(ordered_qty)),
            "ft_ccld_unpr3": f"{price:.4f}",
        }
        return {
            "matched": True,
            "source": "ccnl",
            "broker_order_id": odno,
            "matched_row": row,
            "attempts": [],
        }

    def test_zero_fill_no_position_move_is_open_or_pending(self):
        echo = self._echo_ccnl(filled_qty=0, ordered_qty=1)
        res = resolve_fill_state(
            mode="paper", echo=echo,
            pre_position_qty=0, post_position_qty=0,
            pre_cash_available=100.0, post_cash_available=100.0,
            qty_intended=1, limit_price=10.0,
        )
        self.assertEqual(res.state, OrderState.OPEN_OR_PENDING)
        self.assertEqual(res.status_source, StatusSource.CCNL_ECHO)
        self.assertIsNone(res.fill_price)

    def test_zero_fill_with_position_move_paper_is_conflict_unknown(self):
        echo = self._echo_ccnl(filled_qty=0, ordered_qty=1)
        res = resolve_fill_state(
            mode="paper", echo=echo,
            pre_position_qty=0, post_position_qty=1,
            pre_cash_available=100.0, post_cash_available=90.0,
            qty_intended=1, limit_price=10.0,
        )
        self.assertEqual(res.state, OrderState.UNKNOWN)
        self.assertEqual(res.status_source, StatusSource.CCNL_ECHO)
        self.assertIn("conflict", (res.note or "").lower())
        # paper conflict still surfaces a cash_delta-derived estimate
        self.assertIsNotNone(res.fill_price)
        self.assertEqual(res.fill_price_source, "paper_cash_delta")
        self.assertEqual(res.qty_filled, 1.0)

    def test_zero_fill_with_position_move_live_is_conflict_unknown_no_price(self):
        echo = self._echo_ccnl(filled_qty=0, ordered_qty=1)
        res = resolve_fill_state(
            mode="live", echo=echo,
            pre_position_qty=0, post_position_qty=1,
            pre_cash_available=100.0, post_cash_available=90.0,
            qty_intended=1, limit_price=10.0,
        )
        self.assertEqual(res.state, OrderState.UNKNOWN)
        self.assertEqual(res.status_source, StatusSource.CCNL_ECHO)
        self.assertIn("conflict", (res.note or "").lower())
        # Live mode: cash_delta is NOT authoritative — no fill_price
        self.assertIsNone(res.fill_price)
        self.assertIsNone(res.fill_price_source)

    def test_partial_fill_still_partial(self):
        """Regression: a ccnl row with positive filled_qty < intended
        must still classify as PARTIALLY_FILLED, not get caught by the
        new zero-fill guard."""
        echo = self._echo_ccnl(filled_qty=3, ordered_qty=6, price=10.5)
        res = resolve_fill_state(
            mode="paper", echo=echo,
            pre_position_qty=0, post_position_qty=3,
            pre_cash_available=100.0, post_cash_available=68.50,
            qty_intended=6, limit_price=10.5,
        )
        self.assertEqual(res.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(res.qty_filled, 3.0)
        self.assertEqual(res.qty_remaining, 3.0)
        self.assertEqual(res.fill_price, 10.5)
        self.assertEqual(res.fill_price_source, "broker_ccnl")


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.chdir(str(REPO_ROOT))
    unittest.main(verbosity=2)
