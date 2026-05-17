"""R10-2a — PanelState snapshot tests.

These complement the R9 control panel tests (which still cover the
older R9 surface area: `_latest_awaiting_execution_run_id`,
`_build_dry_run_argv`, halt round-trip, subprocess wrapper). The R10
suite focuses on the new dashboard data layer: ``compute_panel_state``,
``T10JournalStatus``, ``LastReport``, ``submit_outcome_is_clean``.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp
from phase3.autotrade import global_halt as gh
from phase3.autotrade import t10_apply_journal as tj


def _setup_run(base: Path, run_id: str = "20260516_RP", *,
                status: str = "awaiting_execution",
                with_intents: bool = True) -> Path:
    rd = base / "daily_runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "run_meta.json").write_text(json.dumps(
        {"schema_version": "artifact/v1",
         "run_id": run_id, "status": status}, indent=2))
    if with_intents:
        from phase3.autotrade import intents_io
        intents_io.write_submitted_intents(
            rd, [intents_io.make_buy_intent_row(
                client_order_id="co-t-1", symbol="APA",
                qty=1, limit_price=18.85,
            )], run_id=run_id, overwrite=True,
        )
    return rd


class TestComputePanelState(unittest.TestCase):
    def test_no_run_id_yields_placeholder_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            halt = base / "halt.json"
            ps = cp.compute_panel_state(
                output_dir=base, run_id="", env={}, halt_path=halt,
            )
            self.assertEqual(ps.run_id, "")
            self.assertIn("no run_id", ps.artifact_status.lower())
            self.assertFalse(ps.intents.is_ok)
            self.assertFalse(ps.last_report.exists)
            self.assertFalse(ps.halt.halted)

    def test_happy_state_with_intents_and_clean_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            env = {
                "KIS_ENV": "paper",
                cp.SUBMIT_GATE: "true",
                cp.CANCEL_GATE: "true",
                cp.APPLY_GATE: "true",
            }
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP", env=env, halt_path=halt,
            )
            self.assertEqual(ps.artifact_status, "awaiting_execution")
            self.assertTrue(ps.intents.is_ok)
            self.assertEqual(ps.intents.buy_count, 1)
            self.assertTrue(ps.submit_gate_on)
            self.assertTrue(ps.cancel_gate_on)
            self.assertTrue(ps.apply_gate_on)
            self.assertTrue(ps.t10_journal.is_clean)

    def test_artifact_dispatched_status_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, status="dispatched")
            halt = base / "halt.json"
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=halt,
            )
            self.assertEqual(ps.artifact_status, "dispatched")

    def test_halt_surfaces_via_panel_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            gh.write_halt(halt=True, reason="test_stop", path=halt)
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=halt,
            )
            self.assertTrue(ps.halt.halted)
            self.assertEqual(ps.halt.reason, "test_stop")

    def test_t10_journal_open_started_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            tj.write_marker(
                rd, batch_id="batch-X", status="started",
                run_id="20260516_RP",
                applicable_rec_ids=[1],
            )
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=base / "halt.json",
            )
            self.assertFalse(ps.t10_journal.is_clean)
            self.assertTrue(ps.t10_journal.has_open_started)
            self.assertIn("batch-X", ps.t10_journal.open_started_batches)

    def test_t10_journal_recovery_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            tj.write_marker(
                rd, batch_id="batch-Y", status="recovery",
                run_id="20260516_RP",
                applicable_rec_ids=[1],
                reason="test",
            )
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=base / "halt.json",
            )
            self.assertFalse(ps.t10_journal.is_clean)
            self.assertTrue(ps.t10_journal.has_recovery)
            self.assertIn("batch-Y", ps.t10_journal.recovery_batches)


class TestLastReportRendering(unittest.TestCase):
    def test_last_report_picks_up_existing_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            (rd / "autotrade_daily_report.json").write_text(json.dumps({
                "rc": 0,
                "outcome_counts": {"filled": 1, "unknown": 0,
                                    "partially_filled": 0,
                                    "open_or_pending": 0,
                                    "cancel_requested": 0,
                                    "cancelled": 0, "rejected": 0},
            }))
            (rd / "autotrade_daily_report.md").write_text("# rc=0\n")
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=base / "halt.json",
            )
            self.assertTrue(ps.last_report.exists)
            self.assertEqual(ps.last_report.rc, 0)
            self.assertIn("rc=0", ps.last_report.summary)

    def test_last_report_hard_stop_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            (rd / "autotrade_daily_report.json").write_text(json.dumps({
                "rc": 2,
                "hard_stop": {"where": "manage_loop",
                              "reason": "1 UNKNOWN", "rc": 2},
                "outcome_counts": {"filled": 0, "unknown": 1,
                                    "partially_filled": 0,
                                    "open_or_pending": 0,
                                    "cancel_requested": 0,
                                    "cancelled": 0, "rejected": 0},
            }))
            ps = cp.compute_panel_state(
                output_dir=base, run_id="20260516_RP",
                env={}, halt_path=base / "halt.json",
            )
            self.assertEqual(ps.last_report.rc, 2)
            self.assertIn("hard_stop@manage_loop", ps.last_report.summary)


class TestSubmitOutcomeIsClean(unittest.TestCase):
    def _write(self, rd: Path, **kw):
        rd.mkdir(parents=True, exist_ok=True)
        body = {
            "rc": kw.pop("rc", 0),
            "outcome_counts": kw.pop("counts", {
                "filled": 1, "partially_filled": 0, "open_or_pending": 0,
                "cancel_requested": 0, "cancelled": 0, "rejected": 0,
                "unknown": 0,
            }),
            "hard_stop": kw.pop("hard_stop", None),
        }
        (rd / "autotrade_daily_report.json").write_text(json.dumps(body))

    def test_filled_only_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            self._write(rd)
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertTrue(clean, msg=why)

    def test_unknown_present_not_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            self._write(rd, counts={
                "filled": 0, "unknown": 1, "partially_filled": 0,
                "open_or_pending": 0, "cancel_requested": 0,
                "cancelled": 0, "rejected": 0,
            })
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("unknown", why.lower())

    def test_open_pending_not_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            self._write(rd, counts={
                "filled": 0, "unknown": 0, "partially_filled": 0,
                "open_or_pending": 1, "cancel_requested": 0,
                "cancelled": 0, "rejected": 0,
            })
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)

    def test_partial_fill_not_clean_in_r10(self) -> None:
        """R10 §1: T10 apply is only allowed on FILLED-only outcomes;
        partial fills require explicit operator allowance which the
        UI does not surface in R10."""
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            self._write(rd, counts={
                "filled": 1, "unknown": 0, "partially_filled": 1,
                "open_or_pending": 0, "cancel_requested": 0,
                "cancelled": 0, "rejected": 0,
            })
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("partially_filled", why.lower())

    def test_missing_report_not_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("no daily report", why)

    def test_hard_stop_in_report_not_clean(self) -> None:
        """``submit_outcome_is_clean`` checks rc first then hard_stop;
        either failure must produce a non-clean result. We test both
        the rc=2 short-circuit and the rc=0 + hard_stop edge case."""
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            # rc != 0 → caught by the rc gate.
            self._write(rd, rc=2,
                         hard_stop={"where": "manage_loop", "reason": "x", "rc": 2})
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("rc=", why.lower())

    def test_rc_zero_with_hard_stop_not_clean(self) -> None:
        """Defense-in-depth: even if rc was somehow 0 with a hard_stop
        record present, we still bail. (run_daily never produces this
        shape today, but ``submit_outcome_is_clean`` is a final gate.)"""
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            self._write(rd, rc=0,
                         hard_stop={"where": "preflight",
                                     "reason": "should not happen", "rc": 0})
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("hard_stop", why.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
