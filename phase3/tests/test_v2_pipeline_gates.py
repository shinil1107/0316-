"""V2 — pipeline gate wiring (calendar → halt → arm) + lockout recording.

These exercise ``run_v1_pipeline``'s early gates and the
``record_fire_and_evaluate_lockout`` integration without running the real
engine: the gates return BEFORE any T7 / broker work, and the lockout
recorder is a pure-ish helper driven by a hand-built ``V1RunResult``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_runner  # noqa: E402
from phase3.autotrade import v1_arm     # noqa: E402
from phase3.autotrade import auto_halt   # noqa: E402
from phase3.autotrade import global_halt  # noqa: E402

# UTC instants that map to specific KST session dates.
_SAT = datetime(2026, 6, 6, 3, 0, tzinfo=timezone.utc)    # KST Sat 6/6
_HOLIDAY = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)  # KST 7/3 (Jul4 obs)
_TUE = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)   # KST Tue 6/2


class _HaltEnv:
    """Point global_halt at a temp file via AUTOTRADE_HALT_FILE."""

    def __init__(self, td: Path):
        self.path = td / "global_halt.json"
        self._prev = None

    def __enter__(self):
        self._prev = os.environ.get("AUTOTRADE_HALT_FILE")
        os.environ["AUTOTRADE_HALT_FILE"] = str(self.path)
        return self

    def __exit__(self, *a):
        if self._prev is None:
            os.environ.pop("AUTOTRADE_HALT_FILE", None)
        else:
            os.environ["AUTOTRADE_HALT_FILE"] = self._prev


class TestCalendarGate(unittest.TestCase):

    def test_weekend_clean_skip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            with _HaltEnv(rt):
                r = v1_runner.run_v1_pipeline(
                    require_arm_token=True, arm_now=_SAT,
                    runtime_dir=rt, send_mail=False, apply_t10=False,
                    log=lambda *_: None)
            self.assertEqual(r.rc, 0)
            self.assertTrue(r.skipped_by_gate)
            self.assertIn("not a NYSE trading day", r.halt_reason)

    def test_holiday_clean_skip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            with _HaltEnv(rt):
                r = v1_runner.run_v1_pipeline(
                    require_arm_token=True, arm_now=_HOLIDAY,
                    runtime_dir=rt, send_mail=False, apply_t10=False,
                    log=lambda *_: None)
            self.assertEqual(r.rc, 0)
            self.assertTrue(r.skipped_by_gate)


class TestHaltGate(unittest.TestCase):

    def test_halt_clean_skip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            with _HaltEnv(rt) as he:
                global_halt.write_halt(halt=True, reason="test",
                                       path=he.path)
                r = v1_runner.run_v1_pipeline(
                    require_arm_token=True, arm_now=_TUE,
                    runtime_dir=rt, send_mail=False, apply_t10=False,
                    log=lambda *_: None)
            self.assertEqual(r.rc, 0)
            self.assertTrue(r.skipped_by_gate)
            self.assertIn("global_halt", r.halt_reason)


class TestArmGate(unittest.TestCase):

    def test_not_armed_clean_skip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            with _HaltEnv(rt):
                r = v1_runner.run_v1_pipeline(
                    require_arm_token=True, arm_now=_TUE,
                    runtime_dir=rt, send_mail=False, apply_t10=False,
                    log=lambda *_: None)
            self.assertEqual(r.rc, 0)
            self.assertTrue(r.skipped_by_gate)
            self.assertFalse(r.arm_ok)

    def test_standing_arm_passes_gates_then_hits_env_gate(self):
        # Trading day + no halt + standing arm -> passes calendar/halt/arm,
        # then halts at env_gates (env={} is not paper) with rc=2. Proves
        # the gate ordering and that standing arm satisfied the arm gate.
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            with _HaltEnv(rt):
                r = v1_runner.run_v1_pipeline(
                    require_arm_token=True, arm_now=_TUE,
                    runtime_dir=rt, env={}, send_mail=False,
                    apply_t10=False, log=lambda *_: None)
            self.assertEqual(r.rc, 2)
            self.assertFalse(r.skipped_by_gate)


class TestLockoutRecording(unittest.TestCase):

    def test_three_failed_fires_latch_halt(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            with _HaltEnv(rt) as he:
                for i in range(3):
                    res = v1_runner.V1RunResult(rc=2, run_id=f"r{i}",
                                                halt_reason="boom")
                    v1_runner.record_fire_and_evaluate_lockout(
                        res, runtime_dir=rt, arm_now=_TUE,
                        log=lambda *_: None)
                self.assertTrue(global_halt.is_halted(he.path))
            hist = auto_halt.read_history(runtime_dir=rt)
            self.assertEqual(len(hist), 3)
            self.assertTrue(all(h["kind"] == "trade" for h in hist))

    def test_two_failures_do_not_latch(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            with _HaltEnv(rt) as he:
                for i in range(2):
                    res = v1_runner.V1RunResult(rc=2, run_id=f"r{i}")
                    v1_runner.record_fire_and_evaluate_lockout(
                        res, runtime_dir=rt, arm_now=_TUE,
                        log=lambda *_: None)
                self.assertFalse(global_halt.is_halted(he.path))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
