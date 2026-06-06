"""Tests for the paper-account reset helper.

These exercise the archive + reseed logic against a TEMP holdings_log /
output_dir so the operator's real state is never touched. The
``global_halt`` safety gate is covered at the ``main`` level via the
dry-run path (which never needs the gate).
"""

from __future__ import annotations

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

from phase3.autotrade import reset_paper_state as rps  # noqa: E402
from phase3.holdings_manager import HoldingsManager  # noqa: E402


class TestPlanActions(unittest.TestCase):

    def test_plan_lists_move_and_create(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            (out / "daily_runs" / "20260601_x").mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            hl.write_text("stub")
            actions = rps.plan_actions(
                holdings_log=hl, output_dir=out,
                archive_dir=out / "_archive" / "reset_x",
                keep_daily_runs=False)
            joined = "\n".join(actions)
            self.assertIn("MOVE", joined)
            self.assertIn("daily_runs", joined)
            self.assertIn("CREATE", joined)

    def test_plan_respects_keep_daily_runs(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            (out / "daily_runs").mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            hl.write_text("stub")
            actions = rps.plan_actions(
                holdings_log=hl, output_dir=out,
                archive_dir=out / "_archive" / "reset_x",
                keep_daily_runs=True)
            self.assertTrue(any("KEEP" in a for a in actions))


class TestExecuteReset(unittest.TestCase):

    def test_archives_and_reseeds_cash(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            # Seed a "pre-reset" holdings_log with non-trivial cash so we
            # can prove the reset replaced it.
            HoldingsManager(str(hl)).initialize_cash(55555.0)
            self.assertAlmostEqual(
                HoldingsManager(str(hl)).get_cash_balance(), 55555.0, places=2)
            (out / "daily_runs" / "20260601_x").mkdir(parents=True)

            archive = out / "_archive" / "reset_test"
            # Point fire-history at a temp runtime so the test can NEVER
            # touch the operator's real v1_fire_history.jsonl (regression:
            # an unparameterized default once archived+destroyed live
            # lockout history during a routine test run).
            rt = Path(td) / "runtime"
            new_bal = rps.execute_reset(
                holdings_log=hl, output_dir=out, archive_dir=archive,
                initial_cash=100000.0, keep_daily_runs=False,
                runtime_dir=rt)

            # New INIT balance is the requested initial cash.
            self.assertAlmostEqual(new_bal, 100000.0, places=2)
            self.assertAlmostEqual(
                HoldingsManager(str(hl)).get_cash_balance(), 100000.0,
                places=2)
            # Old file + daily_runs are archived (moved), not destroyed.
            self.assertTrue((archive / "holdings_log.xlsx").exists())
            self.assertTrue((archive / "daily_runs" / "20260601_x").exists())
            # daily_runs no longer in the live output dir.
            self.assertFalse((out / "daily_runs").exists())

    def test_keep_daily_runs_leaves_them_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            HoldingsManager(str(hl)).initialize_cash(1.0)
            (out / "daily_runs" / "keep_me").mkdir(parents=True)
            archive = out / "_archive" / "reset_test"
            rps.execute_reset(
                holdings_log=hl, output_dir=out, archive_dir=archive,
                initial_cash=100000.0, keep_daily_runs=True,
                runtime_dir=Path(td) / "runtime")
            self.assertTrue((out / "daily_runs" / "keep_me").exists())
            self.assertFalse((archive / "daily_runs").exists())


class TestMainSafetyGate(unittest.TestCase):

    def test_dry_run_makes_no_changes(self):
        """Default (no --yes) is a dry-run: prints the plan, touches
        nothing, returns 0 — and does NOT require a halt."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            HoldingsManager(str(hl)).initialize_cash(42.0)

            orig = rps._resolve_paths
            rps._resolve_paths = lambda cfg: (hl, out)  # type: ignore
            try:
                rc = rps.main(["--initial-cash", "100000"])
            finally:
                rps._resolve_paths = orig
            self.assertEqual(rc, 0)
            # Unchanged.
            self.assertAlmostEqual(
                HoldingsManager(str(hl)).get_cash_balance(), 42.0, places=2)

    def test_refuses_when_not_halted_and_not_forced(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            HoldingsManager(str(hl)).initialize_cash(42.0)

            orig_paths = rps._resolve_paths
            orig_halt = rps._is_halted
            rps._resolve_paths = lambda cfg: (hl, out)  # type: ignore
            rps._is_halted = lambda: False  # type: ignore
            try:
                rc = rps.main(["--initial-cash", "100000", "--yes"])
            finally:
                rps._resolve_paths = orig_paths
                rps._is_halted = orig_halt
            self.assertEqual(rc, 2)  # refused
            self.assertAlmostEqual(
                HoldingsManager(str(hl)).get_cash_balance(), 42.0, places=2)

    def test_yes_requires_initial_cash(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir(parents=True)
            hl = out / "holdings_log.xlsx"
            HoldingsManager(str(hl)).initialize_cash(42.0)

            orig_paths = rps._resolve_paths
            orig_halt = rps._is_halted
            rps._resolve_paths = lambda cfg: (hl, out)  # type: ignore
            rps._is_halted = lambda: True  # halted, so gate passes
            try:
                rc = rps.main(["--yes"])  # no --initial-cash
            finally:
                rps._resolve_paths = orig_paths
                rps._is_halted = orig_halt
            self.assertEqual(rc, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
