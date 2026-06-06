"""V1-F.F3 — Unit tests for ``phase3.autotrade.v1_status``.

This module is the bridge that makes launchd-fired progress visible
in the panel. The contract:

* ``Writer`` atomically writes a JSON snapshot at every stage
  boundary. ``read_status`` returns the freshest snapshot, or
  ``None`` if no file / unreadable.
* The schema captures fire_label, started_at, current_stage,
  stages_done, run_id, finished_at, final_rc, halt_reason.
* ``render_panel_lines`` produces 1-2 short strings the panel can
  drop into its existing V1-E LabelFrame.

Pinned invariants
-----------------

* round-trip: write → read returns equal fields
* atomic_write: a flush in flight never produces a partial JSON
  observable by ``read_status``
* in_progress correctly toggles between "started but not finished"
  and terminal states
* run_id is promoted onto the snapshot when a stage carries it
* missing file → read_status returns None (panel handles)
* malformed JSON → read_status returns None (panel handles)
* render_panel_lines covers: no-status, in-progress, finished-ok,
  finished-halt cases with the prefix the panel expects
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_status   # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Round-trip + schema
# ──────────────────────────────────────────────────────────────────────
class TestWriterReadRoundTrip(unittest.TestCase):

    def test_write_then_read_basic(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "v1_status.json"
            w = v1_status.Writer(path=p)
            w.start(fire_label="trade",
                    started_at_utc="2026-05-28T13:35:00+00:00")
            w.set_stage("env_gates")
            w.complete_stage("env_gates", rc=0)
            w.set_stage("discover_t7_run")
            w.complete_stage("discover_t7_run", rc=0,
                              run_id="20260528_090015_daily")
            w.set_stage("paper_submit_and_apply")
            w.complete_stage("paper_submit_and_apply", rc=0)
            w.finish(final_rc=0,
                      ended_at_utc="2026-05-28T13:35:42+00:00")

            snap = v1_status.read_status(path=p)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.fire_label, "trade")
            self.assertEqual(snap.final_rc, 0)
            self.assertEqual(snap.run_id, "20260528_090015_daily")
            self.assertFalse(snap.in_progress)
            self.assertEqual(len(snap.stages_done), 3)
            self.assertEqual(snap.stages_done[1]["key"],
                              "discover_t7_run")

    def test_halt_branch_populates_halt_reason(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "v1_status.json"
            w = v1_status.Writer(path=p)
            w.start(fire_label="trade",
                    started_at_utc="2026-05-28T13:35:00+00:00")
            w.set_stage("discover_t7_run")
            w.complete_stage("discover_t7_run", rc=2,
                              halt_reason="no T7 prefetch for today")
            w.finish(final_rc=2,
                      halt_reason="T7 prefetch missing",
                      ended_at_utc="2026-05-28T13:35:01+00:00")
            snap = v1_status.read_status(path=p)
            self.assertEqual(snap.final_rc, 2)
            self.assertIn("T7 prefetch missing", snap.halt_reason or "")
            self.assertEqual(snap.stages_done[0]["halt_reason"],
                              "no T7 prefetch for today")


# ──────────────────────────────────────────────────────────────────────
# Atomicity — readers must never see a partial file
# ──────────────────────────────────────────────────────────────────────
class TestAtomicWrite(unittest.TestCase):

    def test_concurrent_reads_during_write_never_see_partial_json(self):
        """Hammer the writer in one thread while a reader thread
        checks every snapshot parses. A non-atomic write would
        occasionally surface ``json.JSONDecodeError`` here — the
        ``os.replace`` semantics in Writer must prevent that.
        """
        with TemporaryDirectory() as td:
            p = Path(td) / "v1_status.json"
            w = v1_status.Writer(path=p)
            w.start(fire_label="trade",
                    started_at_utc="2026-05-28T13:35:00+00:00")

            errors = []
            stop = threading.Event()

            def writer():
                try:
                    for i in range(200):
                        if stop.is_set():
                            break
                        w.set_stage(f"stage_{i}")
                        w.complete_stage(f"stage_{i}", rc=0)
                finally:
                    w.finish(final_rc=0,
                              ended_at_utc="2026-05-28T13:36:00+00:00")

            def reader():
                while not stop.is_set():
                    snap = v1_status.read_status(path=p)
                    # read_status swallows JSONDecodeError to None;
                    # if we got None even though the file exists,
                    # it means a partial write got through.
                    if p.exists() and snap is None:
                        # Re-read raw and try to parse; if THAT also
                        # fails the writer wasn't atomic.
                        try:
                            raw = p.read_text(encoding="utf-8")
                            if raw.strip():
                                json.loads(raw)
                        except json.JSONDecodeError as e:
                            errors.append(("partial json", str(e)))
                            return

            t_w = threading.Thread(target=writer)
            t_r = threading.Thread(target=reader)
            t_w.start()
            t_r.start()
            t_w.join(timeout=10)
            stop.set()
            t_r.join(timeout=5)
            self.assertEqual(errors, [],
                             f"observed non-atomic write: {errors}")


# ──────────────────────────────────────────────────────────────────────
# read_status edge cases
# ──────────────────────────────────────────────────────────────────────
class TestReadStatusEdgeCases(unittest.TestCase):

    def test_missing_file_returns_none(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "absent.json"
            self.assertIsNone(v1_status.read_status(path=p))

    def test_malformed_json_returns_none(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "malformed.json"
            p.write_text("not json at all{", encoding="utf-8")
            self.assertIsNone(v1_status.read_status(path=p))

    def test_empty_file_returns_none(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "empty.json"
            p.write_text("", encoding="utf-8")
            self.assertIsNone(v1_status.read_status(path=p))


# ──────────────────────────────────────────────────────────────────────
# render_panel_lines — what the panel actually renders
# ──────────────────────────────────────────────────────────────────────
class TestPanelRendering(unittest.TestCase):

    def test_no_status_renders_none_marker(self):
        lines = v1_status.render_panel_lines(None)
        self.assertEqual(lines, ["Last fire: (none)"])

    def test_finished_ok_one_line(self):
        snap = v1_status.StatusSnapshot(
            fire_label="trade",
            started_at_utc="2026-05-28T13:35:00+00:00",
            finished_at_utc="2026-05-28T13:35:42+00:00",
            final_rc=0,
            stages_done=[{"key": "env_gates", "rc": 0},
                          {"key": "discover_t7_run", "rc": 0},
                          {"key": "generate_intents", "rc": 0}],
        )
        lines = v1_status.render_panel_lines(snap)
        self.assertEqual(len(lines), 1)
        self.assertIn("rc=0", lines[0])
        self.assertIn("trade", lines[0])
        self.assertIn("13:35:42", lines[0])
        self.assertIn("3 stages", lines[0])

    def test_finished_halt_shows_rc_2(self):
        snap = v1_status.StatusSnapshot(
            fire_label="trade",
            started_at_utc="2026-05-28T13:35:00+00:00",
            finished_at_utc="2026-05-28T13:35:01+00:00",
            final_rc=2, halt_reason="T7 prefetch missing",
            stages_done=[{"key": "discover_t7_run", "rc": 2}],
        )
        lines = v1_status.render_panel_lines(snap)
        self.assertIn("rc=2", lines[0])

    def test_in_progress_renders_two_lines(self):
        snap = v1_status.StatusSnapshot(
            fire_label="trade",
            started_at_utc="2026-05-28T13:35:00+00:00",
            current_stage="paper_submit_and_apply",
            run_id="20260528_090015_daily",
            stages_done=[{"key": "env_gates", "rc": 0}],
        )
        lines = v1_status.render_panel_lines(
            snap,
            now=datetime(2026, 5, 28, 13, 35, 18,
                          tzinfo=timezone.utc),
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("STARTED 13:35:00", lines[0])
        self.assertIn("paper_submit_and_apply", lines[1])
        # Elapsed approximation appears.
        self.assertIn("(", lines[1])
        self.assertIn(")", lines[1])

    def test_in_progress_when_started_but_no_finish(self):
        snap = v1_status.StatusSnapshot(
            fire_label="t7_prefetch",
            started_at_utc="2026-05-28T00:00:01+00:00",
            current_stage="t7_generate",
        )
        self.assertTrue(snap.in_progress)

    def test_not_in_progress_after_finish(self):
        snap = v1_status.StatusSnapshot(
            fire_label="trade",
            started_at_utc="2026-05-28T13:35:00+00:00",
            finished_at_utc="2026-05-28T13:35:42+00:00",
            final_rc=0,
        )
        self.assertFalse(snap.in_progress)


class TestLogTailPointer(unittest.TestCase):
    """V1-F.2 — status.json must record the launchd StandardOutPath
    plus the byte offset at which THIS fire began appending. The
    panel slices [log_start_offset, current_eof] out of the shared
    log file so a fresh fire never shows yesterday's bytes."""

    def test_writer_captures_existing_log_size_as_start_offset(self):
        with TemporaryDirectory() as td:
            status_path = Path(td) / "v1_status.json"
            log_path = Path(td) / "v1_t7_launchd.out.log"
            # Simulate launchd having already appended yesterday's
            # run to the same file.
            yesterday = b"[v1] yesterday fire bytes\n" * 100
            log_path.write_bytes(yesterday)
            w = v1_status.Writer(path=status_path)
            w.start(fire_label="t7_prefetch", log_path=log_path)
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.log_path, str(log_path))
            self.assertEqual(snap.log_start_offset, len(yesterday))

    def test_writer_handles_missing_log_path_gracefully(self):
        with TemporaryDirectory() as td:
            status_path = Path(td) / "v1_status.json"
            log_path = Path(td) / "does_not_exist_yet.log"
            w = v1_status.Writer(path=status_path)
            w.start(fire_label="trade", log_path=log_path)
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.log_path, str(log_path))
            self.assertEqual(snap.log_start_offset, 0)

    def test_writer_without_log_path_omits_fields(self):
        """Manual ``run`` / test-fire path goes through Popen, so the
        panel already has stdout — no need to record a log_path. The
        snapshot's log_path stays empty so the panel skips tailing."""
        with TemporaryDirectory() as td:
            status_path = Path(td) / "v1_status.json"
            w = v1_status.Writer(path=status_path)
            w.start(fire_label="test-fire")
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.log_path, "")
            self.assertEqual(snap.log_start_offset, 0)

    def test_round_trip_with_log_pointer(self):
        with TemporaryDirectory() as td:
            status_path = Path(td) / "v1_status.json"
            log_path = Path(td) / "fake.log"
            log_path.write_bytes(b"prior\n" * 10)
            w = v1_status.Writer(path=status_path)
            w.start(fire_label="trade", log_path=log_path)
            w.set_stage("env_gates")
            w.complete_stage("env_gates", rc=0)
            w.finish(final_rc=0)
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.fire_label, "trade")
            self.assertEqual(snap.log_path, str(log_path))
            self.assertEqual(snap.log_start_offset, len(b"prior\n" * 10))
            self.assertEqual(snap.final_rc, 0)


class TestRunnerLogPathConvention(unittest.TestCase):
    """The runner derives the StandardOutPath from fire_label using
    the LAUNCHD_LOG_BY_FIRE map. Test files / future fires must keep
    this map in lock-step with the plist templates."""

    def test_t7_prefetch_maps_to_t7_launchd_out_log(self):
        from phase3.autotrade import v1_runner
        p = v1_runner._launchd_log_for("t7_prefetch")
        self.assertIsNotNone(p)
        self.assertTrue(str(p).endswith("v1_t7_launchd.out.log"))

    def test_trade_maps_to_v1_launchd_out_log(self):
        from phase3.autotrade import v1_runner
        p = v1_runner._launchd_log_for("trade")
        self.assertIsNotNone(p)
        self.assertTrue(str(p).endswith("v1_launchd.out.log"))
        # And NOT the T7 one — easy to swap by accident.
        self.assertNotIn("v1_t7_launchd", str(p))

    def test_non_launchd_fires_return_none(self):
        from phase3.autotrade import v1_runner
        self.assertIsNone(v1_runner._launchd_log_for("test-fire"))
        self.assertIsNone(v1_runner._launchd_log_for("run"))
        self.assertIsNone(v1_runner._launchd_log_for(""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
