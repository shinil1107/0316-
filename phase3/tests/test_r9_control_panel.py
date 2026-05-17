"""R9-C — control panel unit tests (headless).

We deliberately do NOT exercise the Tkinter mainloop here. The
correctness-critical surface of the control panel is in three pure
helpers + one subprocess wrapper:

  - _latest_awaiting_execution_run_id  : scans run_meta.json statuses
  - _load_run_meta                     : safe json read
  - _build_dry_run_argv                : exact CLI contract to daily_runner
  - _write_halt_flag / _clear_halt_flag: global_halt round-trip
  - run_dry_run                        : subprocess wrapper (with fake
                                           subprocess.run injected)

If any of these break the GUI is meaningless, so they must stay
covered.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp
from phase3.autotrade import global_halt as gh


def _make_run(base: Path, run_id: str, status: str, *, ts_offset: int = 0):
    rd = base / "daily_runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    meta = {"schema_version": "artifact/v1",
             "run_id": run_id, "status": status}
    p = rd / "run_meta.json"
    p.write_text(json.dumps(meta, indent=2))
    # Adjust mtime so we can deterministically test "newest first".
    import os
    if ts_offset:
        import time
        os.utime(p, (time.time() + ts_offset, time.time() + ts_offset))
    return rd


class TestLatestAwaitingExecution(unittest.TestCase):
    def test_picks_newest_awaiting_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _make_run(base, "20260514_001", "applied", ts_offset=-100)
            _make_run(base, "20260515_002", "awaiting_execution", ts_offset=-50)
            _make_run(base, "20260516_003", "awaiting_execution", ts_offset=0)
            _make_run(base, "20260517_dispatched_only", "dispatched",
                       ts_offset=5)
            picked = cp._latest_awaiting_execution_run_id(base)
            self.assertEqual(picked, "20260516_003")

    def test_returns_none_when_no_awaiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _make_run(base, "20260514_001", "applied")
            _make_run(base, "20260515_002", "dispatched")
            self.assertIsNone(cp._latest_awaiting_execution_run_id(base))

    def test_skips_unparseable_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = base / "daily_runs" / "garbage"
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "run_meta.json").write_text("not-json{")
            _make_run(base, "20260516_good", "awaiting_execution")
            self.assertEqual(
                cp._latest_awaiting_execution_run_id(base),
                "20260516_good",
            )

    def test_missing_daily_runs_dir_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cp._latest_awaiting_execution_run_id(Path(tmp)))


class TestBuildDryRunArgv(unittest.TestCase):
    def test_argv_shape(self) -> None:
        argv = cp._build_dry_run_argv("20260516_001")
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:3], ["-m", "phase3.autotrade.daily_runner"])
        self.assertIn("--dry-run", argv)
        self.assertIn("--run-id", argv)
        self.assertIn("20260516_001", argv)
        self.assertIn("--profile", argv)
        self.assertIn("paper", argv)

    def test_blank_run_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cp._build_dry_run_argv("")
        with self.assertRaises(ValueError):
            cp._build_dry_run_argv("   ")

    def test_paper_only_profile(self) -> None:
        """R9 is paper-only. The default must stay 'paper'."""
        argv = cp._build_dry_run_argv("20260516_001")
        idx = argv.index("--profile")
        self.assertEqual(argv[idx + 1], "paper")


class TestHaltFlagRoundTrip(unittest.TestCase):
    def test_write_halt_blocks_until_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            halt_path = Path(tmp) / "global_halt.json"
            # Initially: not halted.
            self.assertFalse(gh.is_halted(halt_path))

            written = cp._write_halt_flag(path=halt_path)
            self.assertEqual(written, halt_path)
            state = gh.read_halt(halt_path)
            self.assertTrue(state.halted)
            self.assertEqual(state.reason, "operator_pressed_stop")
            with self.assertRaises(gh.GlobalHaltError):
                gh.assert_not_halted(where="test", path=halt_path)

            cleared = cp._clear_halt_flag(path=halt_path)
            self.assertEqual(cleared, halt_path)
            self.assertFalse(gh.is_halted(halt_path))
            # assert_not_halted no longer raises.
            gh.assert_not_halted(where="test", path=halt_path)

    def test_unparseable_halt_file_does_not_block(self) -> None:
        """A broken halt file must NOT silently freeze trading. The
        operator must write a well-formed payload to halt."""
        with tempfile.TemporaryDirectory() as tmp:
            halt_path = Path(tmp) / "global_halt.json"
            halt_path.write_text("not-json{")
            self.assertFalse(gh.is_halted(halt_path))


class TestRunDryRunSubprocess(unittest.TestCase):
    def test_run_dry_run_invokes_correct_argv_and_captures(self) -> None:
        captured: List[List[str]] = []

        class _FakeProc:
            def __init__(self, rc=0, out="report.md\n", err=""):
                self.returncode = rc
                self.stdout = out
                self.stderr = err

        def fake_run(argv, *, capture_output, text, cwd, timeout):
            captured.append(list(argv))
            return _FakeProc(rc=0, out="[daily_runner] rc=0\n", err="")

        res = cp.run_dry_run("20260516_X", subprocess_run=fake_run)
        self.assertEqual(res.rc, 0)
        self.assertIn("[daily_runner] rc=0", res.stdout)
        self.assertEqual(len(captured), 1)
        self.assertIn("--dry-run", captured[0])
        self.assertIn("20260516_X", captured[0])

    def test_run_dry_run_surfaces_nonzero_rc(self) -> None:
        class _FakeProc:
            returncode = 3
            stdout = "[daily_runner] rc=3\n"
            stderr = "[daily_runner] hard_stop@preflight: recovery\n"

        def fake_run(argv, *, capture_output, text, cwd, timeout):
            return _FakeProc()

        res = cp.run_dry_run("20260516_Y", subprocess_run=fake_run)
        self.assertEqual(res.rc, 3)
        self.assertIn("hard_stop@preflight", res.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
