"""R10C — batch intent generation + streaming subprocess runner.

Two surfaces:

1. ``intents_io.candidates_to_intent_rows`` /
   ``intents_io.write_intent_file_from_candidates`` — the pure batch
   path that takes every BUY candidate and produces one
   ``submitted_intents.json`` in a single call, replacing the
   one-candidate-at-a-time UI workflow with an "all in" option.

2. ``control_panel.run_subprocess_streaming`` — the Popen-based
   line-streaming replacement for the blocking ``subprocess.run``
   wrappers. The UI used to freeze for the entire daily_runner
   invocation; this contract guarantees lines are pumped to
   ``on_line`` as they arrive and ``on_done`` fires exactly once.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp  # noqa: E402
from phase3.autotrade import intents_io           # noqa: E402


def _mk_candidate(
    *, run_id="20260518_test", rec_row_id=1, ticker="AAPL",
    reco_shares=2, reco_price=100.0, action="BUY",
) -> intents_io.BuyCandidate:
    return intents_io.BuyCandidate(
        run_id=run_id, rec_row_id=rec_row_id, ticker=ticker,
        action=action, reco_shares=reco_shares, reco_price=reco_price,
        rank=1, regime="", market="NASD", actionable=True, raw_row={},
    )


# ──────────────────────────────────────────────────────────────────────
# candidates_to_intent_rows
# ──────────────────────────────────────────────────────────────────────
class TestCandidatesToIntentRows(unittest.TestCase):

    def test_uses_reco_shares_and_reco_price_by_default(self):
        cands = [
            _mk_candidate(ticker="AAPL", rec_row_id=1, reco_shares=3, reco_price=190.0),
            _mk_candidate(ticker="MSFT", rec_row_id=2, reco_shares=1, reco_price=420.5),
        ]
        rows = intents_io.candidates_to_intent_rows(cands)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["qty"], 3)
        self.assertEqual(rows[0]["limit_price"], 190.0)
        self.assertEqual(rows[1]["symbol"], "MSFT")
        self.assertEqual(rows[1]["qty"], 1)
        self.assertEqual(rows[1]["limit_price"], 420.5)

    def test_limit_pad_pct_bumps_every_row(self):
        cands = [
            _mk_candidate(ticker="AAPL", reco_price=100.0),
            _mk_candidate(ticker="MSFT", reco_price=50.0),
        ]
        rows = intents_io.candidates_to_intent_rows(cands, limit_pad_pct=1.0)
        # +1% above reco_price each (rounded to 4 dp)
        self.assertAlmostEqual(rows[0]["limit_price"], 101.0, places=4)
        self.assertAlmostEqual(rows[1]["limit_price"], 50.5, places=4)

    def test_qty_override_applies_to_every_row(self):
        cands = [
            _mk_candidate(ticker="AAPL", reco_shares=3),
            _mk_candidate(ticker="MSFT", reco_shares=10),
        ]
        rows = intents_io.candidates_to_intent_rows(cands, qty_override=1)
        self.assertEqual([r["qty"] for r in rows], [1, 1])

    def test_empty_candidates_returns_empty_list(self):
        self.assertEqual(intents_io.candidates_to_intent_rows([]), [])

    def test_client_order_ids_are_unique_within_batch(self):
        cands = [
            _mk_candidate(ticker="AAPL", rec_row_id=1),
            _mk_candidate(ticker="MSFT", rec_row_id=2),
            _mk_candidate(ticker="AMD",  rec_row_id=3),
        ]
        rows = intents_io.candidates_to_intent_rows(cands)
        cids = [r["client_order_id"] for r in rows]
        self.assertEqual(len(cids), len(set(cids)),
                          f"duplicate client_order_ids in batch: {cids}")


# ──────────────────────────────────────────────────────────────────────
# write_intent_file_from_candidates
# ──────────────────────────────────────────────────────────────────────
class TestWriteIntentFileFromCandidates(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.run_dir = Path(self._td.name)

    def test_writes_canonical_json_with_all_rows(self):
        cands = [
            _mk_candidate(ticker="AAPL", rec_row_id=1, reco_shares=2, reco_price=190.0),
            _mk_candidate(ticker="MSFT", rec_row_id=2, reco_shares=1, reco_price=420.0),
        ]
        out = intents_io.write_intent_file_from_candidates(
            self.run_dir, cands, run_id="rid-1")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "rid-1")
        self.assertEqual(len(data["intents"]), 2)
        symbols = sorted(r["symbol"] for r in data["intents"])
        self.assertEqual(symbols, ["AAPL", "MSFT"])

    def test_refuses_empty_candidates(self):
        with self.assertRaises(ValueError):
            intents_io.write_intent_file_from_candidates(
                self.run_dir, [], run_id="rid-1")

    def test_refuses_overwrite_unless_explicit(self):
        cands = [_mk_candidate(ticker="AAPL")]
        intents_io.write_intent_file_from_candidates(
            self.run_dir, cands, run_id="rid-1")
        with self.assertRaises(FileExistsError):
            intents_io.write_intent_file_from_candidates(
                self.run_dir, cands, run_id="rid-1")
        # Overwrite=True must succeed.
        intents_io.write_intent_file_from_candidates(
            self.run_dir, cands, run_id="rid-1", overwrite=True)

    def test_validate_round_trip(self):
        cands = [
            _mk_candidate(ticker="AAPL", rec_row_id=1),
            _mk_candidate(ticker="MSFT", rec_row_id=2),
        ]
        intents_io.write_intent_file_from_candidates(
            self.run_dir, cands, run_id="rid-1")
        st = intents_io.validate_submitted_intents(self.run_dir)
        self.assertTrue(st.is_ok)
        self.assertEqual(st.buy_count, 2)


# ──────────────────────────────────────────────────────────────────────
# run_subprocess_streaming — pure helper
# ──────────────────────────────────────────────────────────────────────
class _FakeProc:
    """Minimal duck-typed Popen replacement for the streaming runner.

    Feeds ``stdout_lines`` / ``stderr_lines`` through StringIO. Since
    the runner uses ``iter(fh.readline, "")`` we close each side with
    an empty string. ``wait()`` returns ``returncode`` immediately so
    the helper sees the child as exited the moment both pipes drain.
    """
    def __init__(
        self,
        *,
        stdout_lines: List[str],
        stderr_lines: List[str],
        returncode: int = 0,
        wait_delay: float = 0.0,
    ):
        self.stdout = io.StringIO("".join(line + "\n" for line in stdout_lines))
        self.stderr = io.StringIO("".join(line + "\n" for line in stderr_lines))
        self.returncode = returncode
        self._wait_delay = wait_delay

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._wait_delay:
            time.sleep(self._wait_delay)
        return self.returncode


class TestRunSubprocessStreaming(unittest.TestCase):

    def test_streams_stdout_lines_in_order(self):
        proc = _FakeProc(
            stdout_lines=["alpha", "beta", "gamma"],
            stderr_lines=[],
            returncode=0,
        )
        captured: List[tuple] = []
        done = threading.Event()
        rc_box: Dict[str, int] = {}

        cp.run_subprocess_streaming(
            ["dummy"],
            on_line=lambda s, l: captured.append((s, l)),
            on_done=lambda rc: (rc_box.__setitem__("rc", rc), done.set()),
            popen=lambda *a, **kw: proc,
        )
        self.assertTrue(done.wait(timeout=2.0), "on_done never fired")
        self.assertEqual(rc_box["rc"], 0)
        self.assertEqual(
            [l for s, l in captured if s == "stdout"],
            ["alpha", "beta", "gamma"],
        )

    def test_streams_stderr_under_stderr_label(self):
        proc = _FakeProc(
            stdout_lines=["ok"], stderr_lines=["boom"], returncode=1)
        captured: List[tuple] = []
        done = threading.Event()
        rc_box: Dict[str, int] = {}
        cp.run_subprocess_streaming(
            ["dummy"],
            on_line=lambda s, l: captured.append((s, l)),
            on_done=lambda rc: (rc_box.__setitem__("rc", rc), done.set()),
            popen=lambda *a, **kw: proc,
        )
        self.assertTrue(done.wait(timeout=2.0))
        self.assertEqual(rc_box["rc"], 1)
        streams_seen = {s for s, _ in captured}
        self.assertEqual(streams_seen, {"stdout", "stderr"})
        err_lines = [l for s, l in captured if s == "stderr"]
        self.assertEqual(err_lines, ["boom"])

    def test_on_done_fires_exactly_once(self):
        proc = _FakeProc(
            stdout_lines=["a", "b"], stderr_lines=["c"], returncode=0)
        done_counter = {"n": 0}
        evt = threading.Event()

        def _done(rc):
            done_counter["n"] += 1
            evt.set()
        cp.run_subprocess_streaming(
            ["dummy"], on_line=lambda *a, **kw: None,
            on_done=_done, popen=lambda *a, **kw: proc,
        )
        self.assertTrue(evt.wait(timeout=2.0))
        # Give the threads a moment to (incorrectly) re-fire on_done.
        time.sleep(0.05)
        self.assertEqual(done_counter["n"], 1)

    def test_returns_proc_object_immediately(self):
        proc = _FakeProc(
            stdout_lines=["x"], stderr_lines=[], returncode=0,
            wait_delay=0.1,
        )
        returned = cp.run_subprocess_streaming(
            ["dummy"], on_line=lambda *a, **kw: None,
            on_done=lambda rc: None,
            popen=lambda *a, **kw: proc,
        )
        # The streamer returns the Popen handle so callers can stash it
        # (e.g. to PID-display or terminate). Identity equality is the
        # contract.
        self.assertIs(returned, proc)

    def test_pythonunbuffered_is_injected_when_env_is_none(self):
        """Without PYTHONUNBUFFERED=1, CPython block-buffers stdout
        when the parent captures via PIPE, which would silently kill
        the live-progress UI. The streamer must inject the env var
        when the caller does not pre-set it."""
        captured: Dict[str, Any] = {}

        def _fake_popen(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc(stdout_lines=[], stderr_lines=[], returncode=0)
        done = threading.Event()
        cp.run_subprocess_streaming(
            ["dummy"], on_line=lambda *a, **kw: None,
            on_done=lambda rc: done.set(),
            popen=_fake_popen,
        )
        done.wait(timeout=2.0)
        self.assertIsNotNone(captured["env"])
        self.assertEqual(captured["env"].get("PYTHONUNBUFFERED"), "1")

    def test_caller_env_pythonunbuffered_is_respected(self):
        """A caller can pre-set PYTHONUNBUFFERED — the helper must not
        clobber it."""
        captured: Dict[str, Any] = {}

        def _fake_popen(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc(stdout_lines=[], stderr_lines=[], returncode=0)
        done = threading.Event()
        cp.run_subprocess_streaming(
            ["dummy"], env={"PYTHONUNBUFFERED": "0", "FOO": "bar"},
            on_line=lambda *a, **kw: None,
            on_done=lambda rc: done.set(),
            popen=_fake_popen,
        )
        done.wait(timeout=2.0)
        self.assertEqual(captured["env"]["PYTHONUNBUFFERED"], "0")
        self.assertEqual(captured["env"]["FOO"], "bar")


# ──────────────────────────────────────────────────────────────────────
# Button-gate parity for the new generate_all button
# ──────────────────────────────────────────────────────────────────────
class TestGenerateAllButtonGate(unittest.TestCase):

    def _state(
        self, *, run_id="20260518_test",
        artifact_status="awaiting_execution",
        recs_exists=True, buy_count=3, intents_state="missing",
        intents_buy_count=0, halted=False, overwrite=False,
    ):
        from phase3.autotrade import global_halt
        return cp.PanelState(
            output_dir=Path("/tmp"),
            run_id=run_id,
            run_dir=Path("/tmp") / run_id,
            artifact_status=artifact_status,
            intents=intents_io.IntentFileStatus(
                state=intents_state, reason="", path="",
                intent_count=intents_buy_count,
                buy_count=intents_buy_count, rows=[]),
            last_report=cp.LastReport(
                md_path=None, json_path=None, rc=None,
                summary="(no report yet)"),
            gates=[
                cp.GateStatus(name="KIS_ENV", value="paper", ok=True),
                cp.GateStatus(name="KIS_PAPER_SUBMIT_OK", value="true", ok=True),
                cp.GateStatus(name="KIS_PAPER_CANCEL_OK", value="true", ok=True),
                cp.GateStatus(name="AUTOTRADE_T10_APPLY_OK",
                              value="(unset/false)", ok=False),
            ],
            halt=global_halt.HaltState(
                halted=halted, reason="", ts="", raw_path=""),
            t10_journal=cp.T10JournalStatus(
                has_open_started=False, has_recovery=False),
            recommendations_csv_exists=recs_exists,
            recommendations_buy_count=buy_count,
        )

    def test_enabled_when_recs_present_and_no_existing_intents(self):
        ps = self._state()
        gates = cp.compute_button_gates(
            ps, dry_run_rc_clean=False, submit_outcome_clean=False,
            confirm_submit_checked=False, confirm_apply_checked=False,
            overwrite_intents_checked=False)
        self.assertTrue(gates["generate_all"].enabled,
                         f"generate_all should be enabled: {gates['generate_all']}")

    def test_disabled_when_no_buy_candidates(self):
        ps = self._state(buy_count=0)
        gates = cp.compute_button_gates(
            ps, dry_run_rc_clean=False, submit_outcome_clean=False,
            confirm_submit_checked=False, confirm_apply_checked=False,
            overwrite_intents_checked=False)
        self.assertFalse(gates["generate_all"].enabled)
        self.assertIn("BUY", gates["generate_all"].reason)

    def test_disabled_when_intents_exist_and_overwrite_unchecked(self):
        ps = self._state(intents_state="ok", intents_buy_count=2)
        gates = cp.compute_button_gates(
            ps, dry_run_rc_clean=False, submit_outcome_clean=False,
            confirm_submit_checked=False, confirm_apply_checked=False,
            overwrite_intents_checked=False)
        self.assertFalse(gates["generate_all"].enabled)
        gates = cp.compute_button_gates(
            ps, dry_run_rc_clean=False, submit_outcome_clean=False,
            confirm_submit_checked=False, confirm_apply_checked=False,
            overwrite_intents_checked=True)
        self.assertTrue(gates["generate_all"].enabled)

    def test_disabled_when_halt_on(self):
        ps = self._state(halted=True)
        gates = cp.compute_button_gates(
            ps, dry_run_rc_clean=False, submit_outcome_clean=False,
            confirm_submit_checked=False, confirm_apply_checked=False,
            overwrite_intents_checked=False)
        self.assertFalse(gates["generate_all"].enabled)


if __name__ == "__main__":
    unittest.main()
