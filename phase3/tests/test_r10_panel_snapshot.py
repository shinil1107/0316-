"""R10 — UI copy / panel snapshot regression tests.

Two surfaces are covered:

1. ``build_panel_snapshot_text`` — the pure, Tk-free helper that turns
   ``PanelState`` (+ the last subprocess invocation) into a plain-text
   block the operator can paste into a bug report. The R10 contract is
   that the snapshot contains everything visible in the dashboard *and*
   nothing that isn't already exposed there (no raw secret values).
2. ``default_manage_loop_fn`` constructs ``OrderStore`` with the right
   signature. The historic regression we're locking down: the function
   used to pass ``OrderStore(run_dir=...)`` which the
   ``OrderStore(jsonl_path)`` ctor doesn't accept, so the very first
   paper-submit click crashed with
   ``TypeError: unexpected keyword argument "run_dir"``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp  # noqa: E402
from phase3.autotrade import daily_runner as dr   # noqa: E402
from phase3.autotrade import global_halt          # noqa: E402
from phase3.autotrade import intents_io           # noqa: E402
from phase3.autotrade import order_state          # noqa: E402
from phase3.autotrade import order_store          # noqa: E402
from phase3.autotrade.kis_broker_adapter import OrderIntent  # noqa: E402


def _make_panel_state(tmp_dir: Path) -> cp.PanelState:
    run_dir = tmp_dir / "20260518_120000"
    run_dir.mkdir(parents=True, exist_ok=True)
    return cp.PanelState(
        output_dir=tmp_dir,
        run_id="20260518_120000",
        run_dir=run_dir,
        artifact_status="awaiting_execution",
        intents=intents_io.IntentFileStatus(
            state="ok",
            reason="",
            path=str(run_dir / "submitted_intents.json"),
            intent_count=3,
            buy_count=3,
            rows=[],
        ),
        last_report=cp.LastReport(
            md_path=None, json_path=None, rc=None,
            summary="(no report yet)",
        ),
        gates=[
            cp.GateStatus(name="KIS_ENV", value="paper", ok=True),
            cp.GateStatus(
                name="KIS_PAPER_SUBMIT_OK", value="true", ok=True),
            cp.GateStatus(
                name="KIS_PAPER_CANCEL_OK", value="(unset/false)",
                ok=False, note="Required for cancel/reprice path."),
            cp.GateStatus(
                name="AUTOTRADE_T10_APPLY_OK", value="(unset/false)",
                ok=False, note="Required for T10 real apply."),
        ],
        halt=global_halt.HaltState(
            halted=False, reason="", ts="",
            raw_path=str(tmp_dir / "halt.json")),
        t10_journal=cp.T10JournalStatus(
            has_open_started=False, has_recovery=False),
        recommendations_csv_exists=True,
        recommendations_buy_count=5,
    )


# ──────────────────────────────────────────────────────────────────────
# build_panel_snapshot_text
# ──────────────────────────────────────────────────────────────────────
class TestBuildPanelSnapshotText(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.ps = _make_panel_state(self.tmp)

    def test_includes_run_id_run_dir_and_artifact_status(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000",
            generated_at="2026-05-18 12:00:00 UTC",
        )
        self.assertIn("run_id       : 20260518_120000", snap)
        self.assertIn(str(self.ps.run_dir), snap)
        self.assertIn("awaiting_execution", snap)
        self.assertIn("2026-05-18 12:00:00 UTC", snap)

    def test_intents_line_uses_state_and_counts(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000")
        self.assertIn("state=ok", snap)
        self.assertIn("intents=3", snap)
        self.assertIn("buys=3", snap)

    def test_renders_all_gates_with_ok_block_flag(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000")
        # OK gates
        self.assertIn("KIS_ENV", snap)
        self.assertIn("KIS_PAPER_SUBMIT_OK", snap)
        self.assertIn("[OK]", snap)
        # BLOCK gates
        self.assertIn("KIS_PAPER_CANCEL_OK", snap)
        self.assertIn("AUTOTRADE_T10_APPLY_OK", snap)
        self.assertIn("[BLOCK]", snap)

    def test_halt_and_clean_t10_journal_lines(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000")
        self.assertIn("halted=False", snap)
        self.assertIn("reason=(none)", snap)
        self.assertIn("t10_journal              clean", snap)

    def test_recommendations_summary(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000")
        self.assertIn("recommendations.csv", snap)
        self.assertIn("buy_candidates=5", snap)

    def test_last_argv_and_output_tail_included(self):
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000",
            last_argv=["python", "-m", "phase3.autotrade.daily_runner",
                       "--profile", "paper",
                       "--run-id", "20260518_120000",
                       "--paper-submit"],
            output_tail="hello world\n[stderr]\nboom",
        )
        self.assertIn("--paper-submit", snap)
        self.assertIn("Last command", snap)
        self.assertIn("hello world", snap)
        self.assertIn("[stderr]", snap)

    def test_snapshot_does_not_leak_raw_gate_values(self):
        """Snapshot must never include literal secret/credential values.
        Only the GateStatus.value strings (already shown in the UI) are
        allowed. The dashboard never puts ``KIS_APP_KEY`` etc. into a
        GateStatus, so a stray substring like ``APP_KEY`` is a regression."""
        snap = cp.build_panel_snapshot_text(
            self.ps, run_id="20260518_120000",
            output_tail="(dry-run output)\n",
        )
        for bad in ("APP_KEY", "APP_SECRET", "ACCESS_TOKEN"):
            self.assertNotIn(bad, snap.upper(),
                              f"snapshot leaked {bad!r}: {snap!r}")

    def test_missing_intents_state_propagates(self):
        ps = cp.PanelState(
            output_dir=self.ps.output_dir,
            run_id=self.ps.run_id,
            run_dir=self.ps.run_dir,
            artifact_status="awaiting_execution",
            intents=intents_io.IntentFileStatus(
                state="missing",
                reason="submitted_intents.json missing",
                path="",
                intent_count=0, buy_count=0, rows=[],
            ),
            last_report=self.ps.last_report,
            gates=self.ps.gates,
            halt=self.ps.halt,
            t10_journal=self.ps.t10_journal,
        )
        snap = cp.build_panel_snapshot_text(
            ps, run_id="20260518_120000")
        self.assertIn("state=missing", snap)
        self.assertIn("submitted_intents.json missing", snap)


# ──────────────────────────────────────────────────────────────────────
# default_manage_loop_fn: OrderStore signature regression
# ──────────────────────────────────────────────────────────────────────
class TestDefaultManageLoopOrderStoreSignature(unittest.TestCase):

    def test_order_store_is_constructed_with_jsonl_path(self):
        """Regression: the loop used to call ``OrderStore(run_dir=...)``
        which raises ``TypeError: unexpected keyword argument`` at the
        very first paper-submit click. The right shape is
        ``OrderStore(<run_dir>/autotrade_orders.jsonl)`` to match
        ``orchestrator.py`` and ``t10_applicator.py``."""
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "20260518_120000"
            run_dir.mkdir()
            ctx = mock.MagicMock()
            ctx.run_dir = run_dir
            ctx.run_id = "20260518_120000"
            ctx.autotrade_run_id = "20260518_120000_autotrade"

            intent = OrderIntent(
                client_order_id="cid-1",
                symbol="AAPL",
                market="NASD",
                side="BUY",
                qty=1,
                order_type="LIMIT",
                limit_price=100.0,
            )

            env_cfg = mock.MagicMock()
            env_cfg.env_name = "paper"

            captured = {}

            def _fake_order_store(*args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                # Return a real OrderStore so manage_order has somewhere
                # to write events to.
                return order_store.OrderStore(args[0])

            with mock.patch.object(
                dr, "OrderStore", side_effect=_fake_order_store,
            ), mock.patch(
                "phase3.autotrade.kis_broker_adapter.load_env_config",
                return_value=env_cfg,
            ), mock.patch(
                "phase3.autotrade.kis_broker_adapter.KisBrokerAdapter",
                return_value=mock.MagicMock(),
            ), mock.patch.object(
                dr, "manage_order",
                return_value=mock.MagicMock(
                    final_state=order_state.OrderState.FILLED),
            ):
                outcomes = dr.default_manage_loop_fn(ctx, [intent])

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(captured["kwargs"], {})
            self.assertEqual(len(captured["args"]), 1)
            self.assertEqual(
                captured["args"][0],
                run_dir / "autotrade_orders.jsonl",
            )

    def test_default_manage_loop_refuses_non_paper_env(self):
        """Paper-only safety contract still holds even after the
        OrderStore call shape change."""
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            ctx = mock.MagicMock()
            ctx.run_dir = run_dir
            intent = OrderIntent(
                client_order_id="cid-1", symbol="AAPL", market="NASD",
                side="BUY", qty=1, order_type="LIMIT", limit_price=100.0,
            )
            env_cfg = mock.MagicMock()
            env_cfg.env_name = "real"
            with mock.patch(
                "phase3.autotrade.kis_broker_adapter.load_env_config",
                return_value=env_cfg,
            ):
                with self.assertRaises(RuntimeError) as cm:
                    dr.default_manage_loop_fn(ctx, [intent])
            self.assertIn("paper-only", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
