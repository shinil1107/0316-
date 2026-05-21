"""R11A — plan builder for the panel's Full Paper Run button.

``build_full_paper_run_plan`` is the seam between the panel's Tk
internals and the headless coordinator (``oneclick_coordinator``).
It must:

1. Return the canonical 4 stages in the canonical order, with the
   canonical labels (so the marker file and the audit story stay
   stable across releases).
2. Wire ``generate_intents`` to the in-process callable the UI
   passes, NOT to a subprocess argv.
3. Wire ``dry_run``, ``paper_submit``, ``t10_apply`` to the
   ``stream_argv_blocking`` runner with their respective argv lists.
4. Apply the ``paper_submit_env_extra`` overrides only to the
   ``paper_submit`` stage (other stages must inherit os.environ
   unchanged via the runner).
5. Attach the ``submit_outcome_check_fn`` only to ``paper_submit``
   (so a clean dry_run rc=0 does not get halted by an unrelated
   submit-outcome check).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.control_panel import build_full_paper_run_plan
from phase3.autotrade.oneclick_coordinator import StageSpec


class _StreamRecorder:
    """Test double for ``stream_argv_blocking``. Records every call
    (argv + env_extra) so the test can assert what each stage's
    runner would invoke."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.rc: int = 0

    def __call__(self, argv: Sequence[str], *,
                 env_extra: Optional[Dict[str, str]] = None,
                 label: Optional[str] = None) -> int:
        self.calls.append({
            "argv": list(argv),
            "env_extra": dict(env_extra) if env_extra else None,
            "label": label,
        })
        return self.rc


class TestStageShape(unittest.TestCase):

    def test_four_stages_in_canonical_order(self):
        rec = _StreamRecorder()
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=["python", "-m", "phase3.autotrade.daily_runner",
                          "--profile", "paper", "--run-id", "20260521_t",
                          "--dry-run"],
            paper_submit_argv=["python", "-m", "phase3.autotrade.daily_runner",
                                "--profile", "paper", "--run-id", "20260521_t"],
            paper_submit_env_extra={"AUTOTRADE_REPRICE_STEP_BPS": "30"},
            t10_apply_argv=["python", "-m", "phase3.autotrade.t10_applicator",
                             "--profile", "paper", "--run-id", "20260521_t",
                             "--apply"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        self.assertEqual(len(plan), 4)
        self.assertEqual([s.key for s in plan],
                         ["generate_intents", "dry_run",
                          "paper_submit", "t10_apply"])
        for s in plan:
            self.assertIsInstance(s, StageSpec)

    def test_post_check_only_on_paper_submit(self):
        rec = _StreamRecorder()
        post = lambda: "dirty"  # noqa: E731
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=["dry"], paper_submit_argv=["sub"],
            paper_submit_env_extra={},
            t10_apply_argv=["t10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=post,
        )
        by_key = {s.key: s for s in plan}
        self.assertIsNone(by_key["generate_intents"].post_check)
        self.assertIsNone(by_key["dry_run"].post_check)
        self.assertIs(by_key["paper_submit"].post_check, post)
        self.assertIsNone(by_key["t10_apply"].post_check)


class TestRunnersWireCorrectArgv(unittest.TestCase):

    def test_dry_run_invokes_stream_with_dry_run_argv(self):
        rec = _StreamRecorder()
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=["DRY"],
            paper_submit_argv=["SUB"],
            paper_submit_env_extra={},
            t10_apply_argv=["T10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        rc = plan[1].runner()
        self.assertEqual(rc, 0)
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(rec.calls[0]["argv"], ["DRY"])
        self.assertIsNone(rec.calls[0]["env_extra"])

    def test_paper_submit_invokes_stream_with_env_extra(self):
        """The R10F-Q2 management overrides MUST flow through to the
        paper_submit runner and ONLY to it."""
        rec = _StreamRecorder()
        env_extra = {"AUTOTRADE_REPRICE_STEP_BPS": "30",
                     "AUTOTRADE_MAX_REPRICE_ATTEMPTS": "4"}
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=["DRY"],
            paper_submit_argv=["SUB"],
            paper_submit_env_extra=env_extra,
            t10_apply_argv=["T10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        plan[1].runner()  # dry_run
        plan[2].runner()  # paper_submit
        plan[3].runner()  # t10_apply
        # dry_run / t10_apply must NOT receive env_extra.
        self.assertIsNone(rec.calls[0]["env_extra"])
        self.assertEqual(rec.calls[1]["env_extra"], env_extra)
        self.assertIsNone(rec.calls[2]["env_extra"])

    def test_generate_intents_runner_is_caller_provided(self):
        """``generate_intents_fn`` runs in-process; the recorder must
        be untouched by it."""
        rec = _StreamRecorder()
        calls = {"n": 0}

        def _gen():
            calls["n"] += 1
            return 0

        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=_gen,
            dry_run_argv=["DRY"],
            paper_submit_argv=["SUB"],
            paper_submit_env_extra={},
            t10_apply_argv=["T10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        rc = plan[0].runner()
        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(rec.calls, [])

    def test_runner_propagates_non_zero_rc(self):
        rec = _StreamRecorder()
        rec.rc = 2
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=["DRY"],
            paper_submit_argv=["SUB"],
            paper_submit_env_extra={},
            t10_apply_argv=["T10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        rc = plan[2].runner()
        self.assertEqual(rc, 2)


class TestArgvSnapshotIsolation(unittest.TestCase):

    def test_caller_mutation_does_not_leak(self):
        """If the caller mutates the argv list AFTER plan construction
        the stored runner must still call with the original argv —
        otherwise a slow run could see argv silently change between
        the dry-run preview and the actual call."""
        rec = _StreamRecorder()
        argv = ["DRY"]
        plan = build_full_paper_run_plan(
            run_id="20260521_t",
            generate_intents_fn=lambda: 0,
            dry_run_argv=argv,
            paper_submit_argv=["SUB"],
            paper_submit_env_extra={},
            t10_apply_argv=["T10"],
            stream_argv_blocking=rec,
            submit_outcome_check_fn=lambda: None,
        )
        argv.append("--BAD")  # caller-side mutation
        plan[1].runner()
        self.assertEqual(rec.calls[0]["argv"], ["DRY"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
