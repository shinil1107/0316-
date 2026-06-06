"""R10-2b — Button enablement matrix (R10 §2.2) tests.

Covers exactly the 9 acceptance cases R10 §6 names:

    missing_submitted_intents_disables_submit
    zero_intents_disables_submit
    dry_run_rc_zero_enables_submit_when_env_gates_true
    global_halt_disables_submit
    submit_button_requires_confirmation
    t10_apply_button_requires_latest_submit_success
    t10_apply_button_blocks_unknown_outcome
    command_preview_matches_executed_command
    launcher_command_contains_no_secrets

…and a few extras that lock the matrix end-to-end so future edits
to ``compute_button_gates`` can't silently flip a precondition.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp
from phase3.autotrade import intents_io
from phase3.autotrade import global_halt as gh


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _good_intent_file(rd: Path):
    rd.mkdir(parents=True, exist_ok=True)
    intents_io.write_submitted_intents(
        rd,
        [intents_io.make_buy_intent_row(
            client_order_id="co-test-1", symbol="APA",
            qty=1, limit_price=18.85,
        )],
        run_id=rd.name, overwrite=True,
    )


def _run_meta(rd: Path, status: str = "awaiting_execution"):
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "run_meta.json").write_text(json.dumps(
        {"schema_version": "artifact/v1", "run_id": rd.name,
         "status": status}, indent=2))


def _all_gates_env() -> Dict[str, str]:
    return {
        "KIS_ENV": "paper",
        cp.SUBMIT_GATE: "true",
        cp.CANCEL_GATE: "true",
        cp.APPLY_GATE: "true",
    }


def _build_state(*, run_id: str = "20260516_R10", base: Path,
                  env: Dict[str, str], halt_path: Path) -> cp.PanelState:
    return cp.compute_panel_state(
        output_dir=base, run_id=run_id, env=env, halt_path=halt_path,
    )


def _setup_run(base: Path, run_id: str = "20260516_R10", *,
                with_intents: bool = True,
                with_recs: bool = False,
                artifact_status: str = "awaiting_execution") -> Path:
    rd = base / "daily_runs" / run_id
    _run_meta(rd, status=artifact_status)
    if with_intents:
        _good_intent_file(rd)
    if with_recs:
        _write_recommendations_csv(rd)
    return rd


def _write_recommendations_csv(rd: Path) -> None:
    """Minimal recommendations.csv with one BUY row — only used by
    R11A full-paper-run gate tests that need the recommendations
    precondition to pass. Column names match ``load_buy_candidates``
    (Action / Ticker / Shares / Price / RecRowId)."""
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "recommendations.csv").write_text(
        "Action,Ticker,Shares,Price,RecRowId\n"
        # V1-G.2: T7 emits BUY_NEW / BUY_MORE; the legacy bare "BUY"
        # action is no longer in ``intents_io._BUY_ACTIONS_DEFAULT``,
        # so the fixture must match what production T7 writes.
        "BUY_NEW,APA,1,18.85,1\n",
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────────────
# §6 acceptance cases
# ──────────────────────────────────────────────────────────────────────
class TestSubmitButtonGate(unittest.TestCase):
    def test_missing_submitted_intents_disables_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, with_intents=False)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(),
                                  halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True,
                confirm_submit_checked=True,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn("submitted_intents.json", gates["paper_submit"].reason)

    def test_zero_intents_disables_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base, with_intents=False)
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": []}))
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state, dry_run_rc_clean=True, confirm_submit_checked=True,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn("empty", gates["paper_submit"].reason.lower())

    def test_dry_run_rc_zero_enables_submit_when_env_gates_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True,
                confirm_submit_checked=True,
            )
            self.assertTrue(
                gates["paper_submit"].enabled,
                msg=f"reason={gates['paper_submit'].reason}",
            )

    def test_global_halt_disables_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            gh.write_halt(halt=True, reason="test_stop", path=halt)
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state, dry_run_rc_clean=True, confirm_submit_checked=True,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn("global_halt", gates["paper_submit"].reason)

    def test_submit_button_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            # All preconditions met except the confirmation checkbox.
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True,
                confirm_submit_checked=False,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn("authorize paper submit", gates["paper_submit"].reason)

    def test_submit_button_requires_env_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            env = _all_gates_env()
            env.pop(cp.SUBMIT_GATE)
            state = _build_state(base=base, env=env, halt_path=halt)
            gates = cp.compute_button_gates(
                state, dry_run_rc_clean=True, confirm_submit_checked=True,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn(cp.SUBMIT_GATE, gates["paper_submit"].reason)

    def test_submit_button_requires_kis_env_paper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            env = _all_gates_env()
            env["KIS_ENV"] = "live"
            state = _build_state(base=base, env=env, halt_path=halt)
            gates = cp.compute_button_gates(
                state, dry_run_rc_clean=True, confirm_submit_checked=True,
            )
            self.assertFalse(gates["paper_submit"].enabled)
            self.assertIn("KIS_ENV", gates["paper_submit"].reason)


def _write_submit_report(rd: Path, *, rc: int = 0,
                          counts: Dict[str, int] = None,
                          hard_stop=None):
    """Write a fake autotrade_daily_report.json so submit_outcome_is_clean
    has something to read."""
    rd.mkdir(parents=True, exist_ok=True)
    body = {
        "rc": rc,
        "outcome_counts": counts or {
            "filled": 1, "partially_filled": 0, "open_or_pending": 0,
            "cancel_requested": 0, "cancelled": 0, "rejected": 0, "unknown": 0,
        },
        "hard_stop": hard_stop,
    }
    (rd / "autotrade_daily_report.json").write_text(json.dumps(body, indent=2))


class TestT10ApplyButtonGate(unittest.TestCase):
    def test_t10_apply_button_requires_latest_submit_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            # No submit report → submit_outcome_is_clean is False.
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                submit_outcome_clean=False,
                confirm_apply_checked=True,
            )
            self.assertFalse(gates["t10_apply"].enabled)
            self.assertIn("clean", gates["t10_apply"].reason)

    def test_t10_apply_button_blocks_unknown_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            _write_submit_report(rd, rc=0, counts={
                "filled": 0, "unknown": 1, "partially_filled": 0,
                "open_or_pending": 0, "cancel_requested": 0,
                "cancelled": 0, "rejected": 0,
            })
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertFalse(clean)
            self.assertIn("unknown", why.lower())

    def test_t10_apply_button_clean_after_filled_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            _write_submit_report(rd, rc=0)
            clean, why = cp.submit_outcome_is_clean(rd)
            self.assertTrue(clean, msg=f"why={why}")

    def test_t10_apply_button_requires_apply_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            _write_submit_report(rd, rc=0)
            halt = base / "halt.json"
            env = _all_gates_env()
            env.pop(cp.APPLY_GATE)
            state = _build_state(base=base, env=env, halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                submit_outcome_clean=True,
                confirm_apply_checked=True,
            )
            self.assertFalse(gates["t10_apply"].enabled)
            self.assertIn(cp.APPLY_GATE, gates["t10_apply"].reason)

    def test_t10_apply_button_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _setup_run(base)
            _write_submit_report(rd, rc=0)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                submit_outcome_clean=True,
                confirm_apply_checked=False,
            )
            self.assertFalse(gates["t10_apply"].enabled)
            self.assertIn("authorize T10 apply", gates["t10_apply"].reason)


class TestCommandPreviewContract(unittest.TestCase):
    def test_command_preview_matches_executed_command(self) -> None:
        """Preview text and the actual subprocess argv must agree on the
        argv tail. Whitespace/quoting in the preview is for display only,
        but every literal flag must appear in both."""
        run_id = "20260516_R10_preview"
        dry_argv = cp._build_dry_run_argv(run_id)
        submit_argv = cp._build_paper_submit_argv(run_id)
        t10_argv = cp._build_t10_argv(run_id, apply_mode=True)

        dry_preview = cp.build_command_preview("dry_run", run_id=run_id)
        submit_preview = cp.build_command_preview("paper_submit", run_id=run_id)
        apply_preview = cp.build_command_preview("t10_apply", run_id=run_id)
        full_preview = cp.build_command_preview("full_paper_run", run_id=run_id)

        for flag in ("--profile", "paper", "--run-id", run_id, "--dry-run"):
            self.assertIn(flag, dry_argv)
            self.assertIn(flag, dry_preview)
        for flag in ("--profile", "paper", "--run-id", run_id, "--paper-submit"):
            self.assertIn(flag, submit_argv)
            self.assertIn(flag, submit_preview)
        for flag in ("--profile", "paper", "--run-id", run_id, "--apply"):
            self.assertIn(flag, t10_argv)
            self.assertIn(flag, apply_preview)
        # Full paper run preview shows BOTH paper-submit and apply-t10.
        self.assertIn("--paper-submit", full_preview)
        self.assertIn("--apply-t10", full_preview)
        # Env gate names must show up as =true placeholders.
        self.assertIn(f"{cp.SUBMIT_GATE}=true", submit_preview)
        self.assertIn(f"{cp.CANCEL_GATE}=true", submit_preview)
        self.assertIn(f"{cp.APPLY_GATE}=true", apply_preview)

    def test_command_preview_does_not_leak_secret_values(self) -> None:
        """Even when the operator's shell already has KIS_APPKEY etc. set,
        the preview must never echo them."""
        saved = os.environ.get("KIS_APPKEY")
        os.environ["KIS_APPKEY"] = "SUPERSECRET-PSaIzWkpRq"
        try:
            preview = cp.build_command_preview(
                "paper_submit", run_id="20260516_R10_secret",
            )
        finally:
            if saved is None:
                os.environ.pop("KIS_APPKEY", None)
            else:
                os.environ["KIS_APPKEY"] = saved
        self.assertNotIn("SUPERSECRET", preview)
        self.assertNotIn("PSaIzWkpRq", preview)


class TestLauncherFileContract(unittest.TestCase):
    LAUNCHER = _REPO_ROOT / "scripts" / "run_autotrade_control_panel.command"

    def test_launcher_exists_and_is_executable(self) -> None:
        self.assertTrue(self.LAUNCHER.exists(),
                         f"missing launcher: {self.LAUNCHER}")
        mode = self.LAUNCHER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR,
                         f"not executable by user: oct={oct(mode)}")

    def test_launcher_command_contains_no_secrets(self) -> None:
        """The launcher must never *set* secrets or danger gates. Comment
        lines that mention these names as operator guidance ("export
        KIS_PAPER_SUBMIT_OK=true in your shell before relaunching") are
        explicitly allowed — they document the manual contract instead
        of bypassing it. The test therefore strips comment-only lines
        before fingerprint-matching."""
        raw = self.LAUNCHER.read_text(encoding="utf-8")
        non_comment = "\n".join(
            line for line in raw.splitlines()
            if not line.lstrip().startswith("#")
        )
        forbidden = [
            "KIS_APPKEY=", "KIS_APPSECRET=",
            cp.SUBMIT_GATE + "=", cp.CANCEL_GATE + "=",
            cp.APPLY_GATE + "=",
            # KIS demo key snippet from the project context, no real
            # secret should ever leak through review either.
            "PSaIzWkpRqB1JxUX9PrG4a8tkzvGwl28akQ",
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, non_comment,
                msg=f"launcher must not embed {needle!r} outside a comment: "
                    f"file={self.LAUNCHER}",
            )
        # Belt-and-suspenders: the demo secret must NEVER appear, even in
        # a comment. The danger gate names *may* appear in comments to
        # document the manual export contract.
        self.assertNotIn("PSaIzWkpRqB1JxUX9PrG4a8tkzvGwl28akQ", raw)

    def test_launcher_runs_control_panel_module(self) -> None:
        text = self.LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("phase3.autotrade.control_panel", text)
        self.assertIn("PYTHONPATH", text)


# ──────────────────────────────────────────────────────────────────────
# Extras — full matrix lock so future edits can't silently regress
# ──────────────────────────────────────────────────────────────────────
class TestButtonMatrixFullCoverage(unittest.TestCase):
    def test_all_button_ids_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(state)
            for bid in ("dry_run", "paper_submit", "t10_dry", "t10_apply",
                         "full_paper_run", "stop", "clear_halt"):
                self.assertIn(bid, gates)

    def test_full_paper_run_is_disabled_without_authorize_checkbox(self) -> None:
        """R11A: with every gate armed but the one-click authorize box
        unchecked, the Full Paper Run button must stay disabled. This
        replaces the R10-era ``test_full_paper_run_is_disabled_in_r10``,
        which asserted the unconditional disable that R11A relaxes."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, with_recs=True)
            _write_submit_report(base / "daily_runs" / "20260516_R10", rc=0)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True, submit_outcome_clean=True,
                confirm_submit_checked=True, confirm_apply_checked=True,
                oneclick_authorized_checked=False,
            )
            self.assertFalse(gates["full_paper_run"].enabled)
            self.assertIn("authorize", gates["full_paper_run"].reason.lower())

    def test_full_paper_run_is_enabled_when_authorize_checked(self) -> None:
        """R11A: with every gate armed AND the authorize checkbox
        ticked, the Full Paper Run button is lit. This is the success
        path the panel must allow on a real KIS paper-open morning."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, with_recs=True)
            _write_submit_report(base / "daily_runs" / "20260516_R10", rc=0)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True, submit_outcome_clean=True,
                confirm_submit_checked=True, confirm_apply_checked=True,
                oneclick_authorized_checked=True,
            )
            self.assertTrue(
                gates["full_paper_run"].enabled,
                f"Expected enabled, got reason={gates['full_paper_run'].reason}",
            )

    def test_full_paper_run_disabled_when_kis_env_is_live(self) -> None:
        """R11A: defence in depth. Even with everything else green, a
        non-paper KIS_ENV must veto the one-click run because the
        whole pipeline is paper-only by design."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, with_recs=True)
            _write_submit_report(base / "daily_runs" / "20260516_R10", rc=0)
            halt = base / "halt.json"
            env = dict(_all_gates_env())
            env["KIS_ENV"] = "live"
            state = _build_state(base=base, env=env, halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True, submit_outcome_clean=True,
                confirm_submit_checked=True, confirm_apply_checked=True,
                oneclick_authorized_checked=True,
            )
            self.assertFalse(gates["full_paper_run"].enabled)
            self.assertIn("KIS_ENV", gates["full_paper_run"].reason)

    def test_full_paper_run_disabled_when_recommendations_missing(self) -> None:
        """R11A: the plan starts at generate_intents which depends on
        recommendations.csv. Without it the gate must refuse — the
        coordinator would otherwise burn a button click only to halt
        at the first pre-check."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base, with_recs=False)
            halt = base / "halt.json"
            state = _build_state(base=base, env=_all_gates_env(), halt_path=halt)
            gates = cp.compute_button_gates(
                state,
                dry_run_rc_clean=True, submit_outcome_clean=True,
                confirm_submit_checked=True, confirm_apply_checked=True,
                oneclick_authorized_checked=True,
            )
            self.assertFalse(gates["full_paper_run"].enabled)
            self.assertIn("recommendations.csv",
                          gates["full_paper_run"].reason)

    def test_dry_run_button_enabled_with_only_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _setup_run(base)
            halt = base / "halt.json"
            state = _build_state(base=base, env={}, halt_path=halt)
            gates = cp.compute_button_gates(state)
            self.assertTrue(gates["dry_run"].enabled)

    def test_stop_and_clear_halt_always_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            halt = base / "halt.json"
            state = _build_state(run_id="", base=base, env={}, halt_path=halt)
            gates = cp.compute_button_gates(state)
            self.assertTrue(gates["stop"].enabled)
            self.assertTrue(gates["clear_halt"].enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
