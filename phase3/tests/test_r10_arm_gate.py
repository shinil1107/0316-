"""R10-ARM — In-UI gate activation tests.

The R10 safety contract used to depend on the operator typing
``export KIS_PAPER_SUBMIT_OK=true`` in a shell. That contract is now
expressible inside the dashboard via two ``Arm`` checkboxes that each
guard a confirmation dialog before flipping a single-process env var.

These tests cover the *pure* helpers that back those checkboxes:

  - ``arm_gate_vars`` / ``disarm_gate_vars`` produce the right env diff.
  - ``gate_is_armed`` round-trips through arm/disarm.
  - The submit-button gate inside ``compute_button_gates`` flips OK as
    soon as the env returned by ``arm_gate_vars`` is applied — i.e. the
    Arm toggle and the Submit button gating share the same view of the
    danger gates.

The Tkinter checkbox + confirmation-dialog wiring is intentionally NOT
covered here — that path is interactive and lives behind ``messagebox``.
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

from phase3.autotrade import control_panel as cp
from phase3.autotrade import intents_io
from phase3.autotrade import global_halt as gh


class TestArmGateHelpers(unittest.TestCase):
    def test_arm_paper_sets_submit_and_cancel_true(self):
        env = {"KIS_ENV": "paper"}
        armed = cp.arm_gate_vars(env, cp.ARM_PAPER_GATE_VARS)
        self.assertEqual(armed[cp.SUBMIT_GATE], "true")
        self.assertEqual(armed[cp.CANCEL_GATE], "true")
        # Original mapping is not mutated.
        self.assertNotIn(cp.SUBMIT_GATE, env)
        self.assertNotIn(cp.CANCEL_GATE, env)

    def test_arm_t10_sets_only_apply_gate(self):
        env = {"KIS_ENV": "paper"}
        armed = cp.arm_gate_vars(env, cp.ARM_T10_GATE_VARS)
        self.assertEqual(armed[cp.APPLY_GATE], "true")
        self.assertNotIn(cp.SUBMIT_GATE, armed)
        self.assertNotIn(cp.CANCEL_GATE, armed)

    def test_disarm_drops_target_vars_only(self):
        env = {
            "KIS_ENV": "paper",
            cp.SUBMIT_GATE: "true",
            cp.CANCEL_GATE: "true",
            cp.APPLY_GATE:  "true",
        }
        disarmed = cp.disarm_gate_vars(env, cp.ARM_PAPER_GATE_VARS)
        self.assertNotIn(cp.SUBMIT_GATE, disarmed)
        self.assertNotIn(cp.CANCEL_GATE, disarmed)
        # T10 gate is unaffected by disarming paper.
        self.assertEqual(disarmed[cp.APPLY_GATE], "true")
        self.assertEqual(disarmed["KIS_ENV"], "paper")

    def test_gate_is_armed_requires_all_vars_true(self):
        env = {cp.SUBMIT_GATE: "true"}
        # Cancel gate missing → not fully armed.
        self.assertFalse(cp.gate_is_armed(env, cp.ARM_PAPER_GATE_VARS))
        env[cp.CANCEL_GATE] = "true"
        self.assertTrue(cp.gate_is_armed(env, cp.ARM_PAPER_GATE_VARS))
        env[cp.CANCEL_GATE] = "True"  # case-insensitive
        self.assertTrue(cp.gate_is_armed(env, cp.ARM_PAPER_GATE_VARS))
        env[cp.CANCEL_GATE] = "yes"   # not the literal "true"
        self.assertFalse(cp.gate_is_armed(env, cp.ARM_PAPER_GATE_VARS))

    def test_arm_then_disarm_roundtrips(self):
        env = {"KIS_ENV": "paper"}
        armed = cp.arm_gate_vars(env, cp.ARM_PAPER_GATE_VARS)
        self.assertTrue(cp.gate_is_armed(armed, cp.ARM_PAPER_GATE_VARS))
        disarmed = cp.disarm_gate_vars(armed, cp.ARM_PAPER_GATE_VARS)
        self.assertFalse(cp.gate_is_armed(disarmed, cp.ARM_PAPER_GATE_VARS))


def _setup_run(base: Path):
    """Minimal fixture: one awaiting_execution run with a valid intent
    and a clean dry-run report so the Submit button only depends on
    the danger-gate env."""
    rid = "20260518_ARMTEST"
    run_dir = base / "daily_runs" / rid
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        '{"schema_version": "artifact/v1", "run_id": "' + rid +
        '", "status": "awaiting_execution"}',
        encoding="utf-8",
    )
    intents_io.write_submitted_intents(
        run_dir,
        [intents_io.make_buy_intent_row(
            client_order_id=f"co-{rid}-B-1-arm",
            symbol="APA", qty=1, limit_price=39.00,
        )],
        run_id=rid,
    )
    (run_dir / "autotrade_daily_report.json").write_text(
        '{"rc": 0, "mode": "dry_run", "hard_stop": null, '
        '"outcome_counts": {"filled": 0, "partially_filled": 0, '
        '"open_or_pending": 0, "cancel_requested": 0, "cancelled": 0, '
        '"rejected": 0, "unknown": 0}}',
        encoding="utf-8",
    )
    return rid


class TestArmFlipsSubmitGate(unittest.TestCase):
    """End-to-end on the button matrix: arming the paper gate flips the
    'paper_submit' button's blocker list so the run is one tick + one
    click away from a real submission. Without arming, the same
    PanelState yields a disabled Submit button. This is the *whole
    point* of the toggle — it's the in-UI equivalent of the shell
    export, and the matrix must respect it identically."""

    def test_paper_submit_gate_blocks_without_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid = _setup_run(base)
            env = {"KIS_ENV": "paper"}  # explicitly NOT armed
            state = cp.compute_panel_state(
                output_dir=base, run_id=rid, env=env,
                halt_path=base / "halt.json",
            )
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True,
                submit_outcome_clean=False,
                confirm_submit_checked=True,   # operator ticked authorize
                confirm_apply_checked=False,
                overwrite_intents_checked=False,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn(cp.SUBMIT_GATE, gates["paper_submit"].reason)

    def test_paper_submit_gate_opens_after_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid = _setup_run(base)
            env = cp.arm_gate_vars({"KIS_ENV": "paper"},
                                    cp.ARM_PAPER_GATE_VARS)
            state = cp.compute_panel_state(
                output_dir=base, run_id=rid, env=env,
                halt_path=base / "halt.json",
            )
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True,
                submit_outcome_clean=False,
                confirm_submit_checked=True,
                confirm_apply_checked=False,
                overwrite_intents_checked=False,
            )
            self.assertTrue(gates["paper_submit"].enabled, gates["paper_submit"].reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
