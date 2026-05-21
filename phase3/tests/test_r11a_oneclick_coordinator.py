"""R11A — one-click paper-run coordinator.

This is the headless contract test for ``oneclick_coordinator.run_oneclick``.

What we lock in:

1. Happy path: every stage's runner returns 0 → ``overall_rc=0``,
   ``halt_reason=None``, every stage outcome carries the runner-
   reported rc, the marker is updated after every transition + once
   at the end.
2. A pre-check that returns a reason halts the run BEFORE the runner
   fires (runner.call_count must stay 0 for that stage and every
   subsequent stage).
3. A non-zero rc halts the run, ``overall_rc`` propagates the rc,
   subsequent stages are recorded as ``skipped=True`` with rc=-1.
4. A runner exception is caught, surfaced as rc=99, and the
   ``halt_reason`` carries the exception class+message.
5. A post-check that returns a reason halts AFTER the runner ran
   (runner.call_count for the stage stays at 1, subsequent stages
   are skipped).
6. The marker file is written atomically (rename-based) and the
   final marker reflects ``current_stage=None`` and the final
   ``halt_reason``.
7. ``on_stage_start`` / ``on_stage_end`` / ``on_halt`` callbacks are
   invoked in the right order, and exceptions from them never break
   the run.
8. The clock is fully injected: with a fake ``now_fn`` we get
   deterministic ``started_at``/``ended_at`` strings.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.oneclick_coordinator import (
    MARKER_FILENAME,
    StageOutcome,
    StageSpec,
    read_oneclick_marker,
    run_oneclick,
)


# ──────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────
class _CountingRunner:
    """Test double for a stage runner. Configurable rc, optional
    exception. Tracks how many times it was invoked."""

    def __init__(self, rc: int = 0, *, raises: Optional[Exception] = None):
        self.rc = rc
        self.raises = raises
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.rc


class _FakeClock:
    """Returns a strictly monotonically advancing UTC datetime — one
    tick per call. The first call returns ``base``, then base+1s,
    base+2s, ... so durations come out deterministic."""

    def __init__(self, base: Optional[datetime] = None,
                 step_sec: float = 1.0):
        self.base = base or datetime(2026, 5, 21, 13, 30, 0,
                                     tzinfo=timezone.utc)
        self.step_sec = step_sec
        self.calls = 0

    def __call__(self) -> datetime:
        from datetime import timedelta
        t = self.base + timedelta(seconds=self.calls * self.step_sec)
        self.calls += 1
        return t


def _stages(
    plan: List[tuple],   # list of (key, runner, pre, post)
) -> List[StageSpec]:
    out = []
    for key, runner, pre, post in plan:
        out.append(StageSpec(
            key=key,
            label=key.replace("_", " ").title(),
            runner=runner,
            pre_check=pre,
            post_check=post,
        ))
    return out


def _marker_path(tmpdir: str) -> Path:
    return Path(tmpdir) / MARKER_FILENAME


# ──────────────────────────────────────────────────────────────────────
# 1. Happy path
# ──────────────────────────────────────────────────────────────────────
class TestHappyPath(unittest.TestCase):

    def test_all_stages_succeed(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = _CountingRunner(rc=0)
            r2 = _CountingRunner(rc=0)
            r3 = _CountingRunner(rc=0)
            r4 = _CountingRunner(rc=0)
            stages = _stages([
                ("generate_intents", r1, None, None),
                ("dry_run",          r2, None, None),
                ("paper_submit",     r3, None, None),
                ("t10_apply",        r4, None, None),
            ])
            result = run_oneclick(
                run_id="20260521_test",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 0)
            self.assertIsNone(result.halt_reason)
            self.assertTrue(result.all_clean)
            self.assertEqual([o.key for o in result.stages],
                             ["generate_intents", "dry_run",
                              "paper_submit", "t10_apply"])
            for r in (r1, r2, r3, r4):
                self.assertEqual(r.calls, 1)
            for o in result.stages:
                self.assertEqual(o.rc, 0)
                self.assertFalse(o.skipped)
                self.assertIsNone(o.halt_reason)

    def test_marker_final_state(self):
        with tempfile.TemporaryDirectory() as td:
            mpath = _marker_path(td)
            stages = _stages([
                ("dry_run", _CountingRunner(0), None, None),
                ("paper_submit", _CountingRunner(0), None, None),
            ])
            run_oneclick(
                run_id="20260521_marker",
                stages=stages,
                marker_path=mpath,
                now_fn=_FakeClock(),
            )
            payload = read_oneclick_marker(mpath)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["run_id"], "20260521_marker")
            self.assertEqual(payload["stages_planned"],
                             ["dry_run", "paper_submit"])
            self.assertIsNone(payload["current_stage"])
            self.assertIsNone(payload["halt_reason"])
            self.assertEqual(len(payload["stages_completed"]), 2)
            self.assertEqual(payload["stages_completed"][0]["rc"], 0)


# ──────────────────────────────────────────────────────────────────────
# 2. Pre-check halt
# ──────────────────────────────────────────────────────────────────────
class TestPreCheckHalt(unittest.TestCase):

    def test_pre_check_halts_before_runner(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = _CountingRunner(0)
            r2 = _CountingRunner(0)
            r3 = _CountingRunner(0)
            stages = _stages([
                ("generate_intents", r1, None, None),
                ("dry_run",          r2,
                 lambda: "gates not armed",  # pre-check fails
                 None),
                ("paper_submit",     r3, None, None),
            ])
            result = run_oneclick(
                run_id="20260521_pre",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 2)
            self.assertIn("gates not armed",
                          result.halt_reason or "")
            self.assertEqual(r1.calls, 1)
            self.assertEqual(r2.calls, 0)   # never ran
            self.assertEqual(r3.calls, 0)
            # outcomes: stage 0 ok, stage 1 halted, stage 2 skipped
            self.assertEqual(result.stages[0].rc, 0)
            self.assertEqual(result.stages[1].rc, 2)
            self.assertTrue(result.stages[2].skipped)
            self.assertEqual(result.stages[2].rc, -1)


# ──────────────────────────────────────────────────────────────────────
# 3. Non-zero rc halt
# ──────────────────────────────────────────────────────────────────────
class TestNonZeroRcHalt(unittest.TestCase):

    def test_rc_propagates_and_subsequent_stages_skip(self):
        with tempfile.TemporaryDirectory() as td:
            r_dry  = _CountingRunner(0)
            r_sub  = _CountingRunner(2)  # paper-submit hard stop
            r_t10  = _CountingRunner(0)
            stages = _stages([
                ("dry_run",       r_dry, None, None),
                ("paper_submit",  r_sub, None, None),
                ("t10_apply",     r_t10, None, None),
            ])
            result = run_oneclick(
                run_id="20260521_rc",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 2)
            self.assertIn("rc=2", result.halt_reason or "")
            self.assertEqual(r_dry.calls, 1)
            self.assertEqual(r_sub.calls, 1)
            self.assertEqual(r_t10.calls, 0)
            self.assertTrue(result.stages[2].skipped)


# ──────────────────────────────────────────────────────────────────────
# 4. Runner exception
# ──────────────────────────────────────────────────────────────────────
class TestRunnerException(unittest.TestCase):

    def test_exception_becomes_rc99_and_halts(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = _CountingRunner(0)
            r2 = _CountingRunner(0,
                                 raises=RuntimeError("subprocess died"))
            r3 = _CountingRunner(0)
            stages = _stages([
                ("generate_intents", r1, None, None),
                ("dry_run",          r2, None, None),
                ("paper_submit",     r3, None, None),
            ])
            result = run_oneclick(
                run_id="20260521_exc",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 99)
            self.assertIn("RuntimeError", result.halt_reason or "")
            self.assertIn("subprocess died", result.halt_reason or "")
            self.assertEqual(result.stages[1].rc, 99)
            self.assertTrue(result.stages[2].skipped)


# ──────────────────────────────────────────────────────────────────────
# 5. Post-check halt
# ──────────────────────────────────────────────────────────────────────
class TestPostCheckHalt(unittest.TestCase):

    def test_post_check_halts_after_runner(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = _CountingRunner(0)
            r2 = _CountingRunner(0)   # rc=0 but post-check fails
            r3 = _CountingRunner(0)
            stages = _stages([
                ("dry_run",      r1, None, None),
                ("paper_submit", r2, None, lambda: "submit_outcome dirty"),
                ("t10_apply",    r3, None, None),
            ])
            result = run_oneclick(
                run_id="20260521_post",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 1)
            self.assertIn("submit_outcome dirty",
                          result.halt_reason or "")
            self.assertEqual(r2.calls, 1)
            self.assertEqual(r3.calls, 0)
            self.assertEqual(result.stages[1].rc, 0)
            self.assertIsNotNone(result.stages[1].halt_reason)
            self.assertTrue(result.stages[2].skipped)


# ──────────────────────────────────────────────────────────────────────
# 6. Marker on halt
# ──────────────────────────────────────────────────────────────────────
class TestMarkerOnHalt(unittest.TestCase):

    def test_marker_reflects_halt(self):
        with tempfile.TemporaryDirectory() as td:
            mpath = _marker_path(td)
            stages = _stages([
                ("dry_run",      _CountingRunner(0), None, None),
                ("paper_submit", _CountingRunner(2), None, None),
                ("t10_apply",    _CountingRunner(0), None, None),
            ])
            run_oneclick(
                run_id="20260521_marker_halt",
                stages=stages,
                marker_path=mpath,
                now_fn=_FakeClock(),
            )
            payload = read_oneclick_marker(mpath)
            self.assertIsNotNone(payload)
            self.assertIsNone(payload["current_stage"])
            self.assertIn("rc=2", payload["halt_reason"] or "")
            # All three stages present (the third as skipped).
            self.assertEqual(len(payload["stages_completed"]), 3)
            self.assertTrue(payload["stages_completed"][2]["skipped"])


# ──────────────────────────────────────────────────────────────────────
# 7. Callbacks
# ──────────────────────────────────────────────────────────────────────
class TestCallbacks(unittest.TestCase):

    def test_start_end_halt_callbacks_called_in_order(self):
        events: List[str] = []

        def on_start(spec: StageSpec) -> None:
            events.append(f"start:{spec.key}")

        def on_end(outcome: StageOutcome) -> None:
            events.append(f"end:{outcome.key}:rc={outcome.rc}")

        def on_halt(outcome: StageOutcome) -> None:
            events.append(f"halt:{outcome.key}")

        with tempfile.TemporaryDirectory() as td:
            stages = _stages([
                ("dry_run",      _CountingRunner(0), None, None),
                ("paper_submit", _CountingRunner(2), None, None),
                ("t10_apply",    _CountingRunner(0), None, None),
            ])
            run_oneclick(
                run_id="20260521_cb",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
                on_stage_start=on_start,
                on_stage_end=on_end,
                on_halt=on_halt,
            )
            # Expected sequence:
            #   start dry_run, end dry_run rc=0,
            #   start paper_submit, end paper_submit rc=2,
            #   halt paper_submit  (then t10_apply is skipped → no
            #   start/end callback).
            self.assertEqual(events, [
                "start:dry_run", "end:dry_run:rc=0",
                "start:paper_submit", "end:paper_submit:rc=2",
                "halt:paper_submit",
            ])

    def test_callback_exceptions_do_not_break_run(self):
        with tempfile.TemporaryDirectory() as td:
            def bad_start(spec: StageSpec) -> None:
                raise RuntimeError("UI is on fire")

            stages = _stages([
                ("dry_run", _CountingRunner(0), None, None),
            ])
            result = run_oneclick(
                run_id="20260521_badcb",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
                on_stage_start=bad_start,
            )
            self.assertEqual(result.overall_rc, 0)
            self.assertIsNone(result.halt_reason)


# ──────────────────────────────────────────────────────────────────────
# 8. Clock injection
# ──────────────────────────────────────────────────────────────────────
class TestClockInjection(unittest.TestCase):

    def test_deterministic_timestamps(self):
        with tempfile.TemporaryDirectory() as td:
            base = datetime(2026, 5, 21, 13, 30, 0, tzinfo=timezone.utc)
            clock = _FakeClock(base=base, step_sec=2.0)
            stages = _stages([
                ("dry_run",      _CountingRunner(0), None, None),
                ("paper_submit", _CountingRunner(0), None, None),
            ])
            result = run_oneclick(
                run_id="20260521_clock",
                stages=stages,
                marker_path=_marker_path(td),
                now_fn=clock,
            )
            self.assertTrue(result.started_at.startswith("2026-05-21T13:30:00"))
            self.assertGreater(result.duration_sec, 0)


# ──────────────────────────────────────────────────────────────────────
# 9. Empty plan
# ──────────────────────────────────────────────────────────────────────
class TestEmptyPlan(unittest.TestCase):

    def test_empty_plan_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_oneclick(
                run_id="20260521_empty",
                stages=[],
                marker_path=_marker_path(td),
                now_fn=_FakeClock(),
            )
            self.assertEqual(result.overall_rc, 0)
            self.assertIsNone(result.halt_reason)
            self.assertEqual(result.stages, ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
