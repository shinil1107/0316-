"""R10D / P0-3 — callback-level danger-gate re-validation.

The R10C UI relied on the button matrix to disable the
Paper-Submit / T10-Apply buttons until Arm toggles armed the
corresponding env vars. That was correct for normal use, but:

  - the previous callbacks injected ``KIS_PAPER_SUBMIT_OK=true`` /
    ``AUTOTRADE_T10_APPLY_OK=true`` into the child env *regardless*
    of Arm state, so any caller that bypassed the UI (programmatic
    invocation, future Full Paper Run coordinator, test harness)
    would have side-stepped the safety contract.
  - the Tk-disabled state has a small race window between a stale
    panel state and a fast click.

``revalidate_danger_action`` closes both gaps by recomputing
``compute_panel_state + compute_button_gates`` at callback entry
and refusing if the action is disabled. The Arm toggle remains the
only way to set the danger env vars.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp
from phase3.autotrade import intents_io


def _make_run_dir_with_intents(tmp: Path, run_id: str) -> Path:
    """Create a minimal awaiting_execution run directory with one
    valid BUY intent + recommendations.csv so the button matrix sees
    a green precondition set."""
    run_dir = tmp / "daily_runs" / run_id
    run_dir.mkdir(parents=True)
    # submitted_intents.json
    (run_dir / "submitted_intents.json").write_text(
        '{"schema_version":"intents/v1","run_id":"' + run_id + '",'
        '"generated_at":"2026-05-19T00:00:00+00:00","intents":[{'
        '"client_order_id":"co-' + run_id + '-1-B-1-AAPL",'
        '"symbol":"AAPL","market":"NASD","side":"BUY","qty":1,'
        '"ord_type":"LIMIT","limit_price":190.0}]}',
        encoding="utf-8",
    )
    # run_meta.json with awaiting_execution status
    (run_dir / "run_meta.json").write_text(
        '{"schema_version":"artifact/v1","run_id":"' + run_id + '",'
        '"status":"awaiting_execution"}',
        encoding="utf-8",
    )
    # recommendations.csv with a single BUY row
    (run_dir / "recommendations.csv").write_text(
        "RunId,ScoringDate,Actionable,RecRowId,Date,Ticker,Action,Score,"
        "TargetPct,ActualPct,GapPct,Price,Shares,Capital,Regime,GraceCount,Rank\n"
        f"{run_id},2026-05-15,True,1,2026-05-19,AAPL,BUY_NEW,80.0,5.0,0.0,"
        f"5.0,190.0,1,190.0,SIDE,0,1\n",
        encoding="utf-8",
    )
    return run_dir


def _armed_paper_env() -> Dict[str, str]:
    return {
        "KIS_ENV": "paper",
        "KIS_PAPER_SUBMIT_OK": "true",
        "KIS_PAPER_CANCEL_OK": "true",
    }


def _armed_t10_env() -> Dict[str, str]:
    return {
        "KIS_ENV": "paper",
        "KIS_PAPER_SUBMIT_OK": "true",
        "KIS_PAPER_CANCEL_OK": "true",
        "AUTOTRADE_T10_APPLY_OK": "true",
    }


class TestRevalidateDangerAction(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.run_id = "20260519_acceptance"
        self.run_dir = _make_run_dir_with_intents(self.tmp, self.run_id)

    # ── paper_submit ──────────────────────────────────────────────
    def test_paper_submit_passes_with_armed_env(self):
        # Arm + confirm + clean dry-run.
        env = _armed_paper_env()
        ps = cp.revalidate_danger_action(
            action="paper_submit",
            output_dir=self.tmp,
            run_id=self.run_id,
            env=env,
            confirm_submit_checked=True,
            dry_run_rc_clean=True,
        )
        self.assertEqual(ps.run_id, self.run_id)
        self.assertTrue(ps.submit_gate_on)

    def test_paper_submit_refused_without_arm(self):
        env = {"KIS_ENV": "paper"}  # gates not armed
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=self.tmp,
                run_id=self.run_id,
                env=env,
                confirm_submit_checked=True,
                dry_run_rc_clean=True,
            )
        self.assertIn("KIS_PAPER_SUBMIT_OK", cm.exception.reason)

    def test_paper_submit_refused_without_confirm_checkbox(self):
        env = _armed_paper_env()
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=self.tmp,
                run_id=self.run_id,
                env=env,
                confirm_submit_checked=False,  # the killer
                dry_run_rc_clean=True,
            )
        # The matrix phrases this as "tick the 'authorize paper submit'
        # checkbox" — just check for "authorize".
        self.assertIn("authorize", cm.exception.reason.lower())

    def test_paper_submit_refused_without_clean_dry_run(self):
        env = _armed_paper_env()
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=self.tmp,
                run_id=self.run_id,
                env=env,
                confirm_submit_checked=True,
                dry_run_rc_clean=False,  # dry-run not run / failed
            )
        self.assertIn("dry-run", cm.exception.reason.lower())

    def test_paper_submit_refused_on_blank_run_id(self):
        env = _armed_paper_env()
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=self.tmp,
                run_id="",
                env=env,
                confirm_submit_checked=True,
                dry_run_rc_clean=True,
            )
        self.assertIn("run_id", cm.exception.reason.lower())

    def test_paper_submit_refused_when_output_dir_unset(self):
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=None,
                run_id=self.run_id,
                env=_armed_paper_env(),
                confirm_submit_checked=True,
                dry_run_rc_clean=True,
            )
        self.assertIn("config", cm.exception.reason.lower())

    # ── t10_apply ─────────────────────────────────────────────────
    def test_t10_apply_passes_with_clean_submit_and_armed_t10(self):
        env = _armed_t10_env()
        ps = cp.revalidate_danger_action(
            action="t10_apply",
            output_dir=self.tmp,
            run_id=self.run_id,
            env=env,
            confirm_apply_checked=True,
            submit_outcome_clean=True,
        )
        self.assertEqual(ps.run_id, self.run_id)
        self.assertTrue(ps.apply_gate_on)

    def test_t10_apply_refused_without_apply_gate_armed(self):
        env = _armed_paper_env()  # T10 not armed
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="t10_apply",
                output_dir=self.tmp,
                run_id=self.run_id,
                env=env,
                confirm_apply_checked=True,
                submit_outcome_clean=True,
            )
        self.assertIn("AUTOTRADE_T10_APPLY_OK", cm.exception.reason)

    def test_t10_apply_refused_when_submit_outcome_not_clean(self):
        env = _armed_t10_env()
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="t10_apply",
                output_dir=self.tmp,
                run_id=self.run_id,
                env=env,
                confirm_apply_checked=True,
                submit_outcome_clean=False,  # unknown / open / unmatched
            )
        reason = cm.exception.reason.lower()
        # The matrix is allowed to phrase this in a few ways; we just
        # check that something about the prior submit outcome shows up.
        self.assertTrue(
            "submit" in reason or "filled" in reason or "report" in reason,
            f"unexpected denial reason: {cm.exception.reason!r}",
        )

    # ── misc / API contract ───────────────────────────────────────
    def test_unknown_action_raises_valueerror(self):
        with self.assertRaises(ValueError):
            cp.revalidate_danger_action(
                action="dry_run",  # not a danger action
                output_dir=self.tmp,
                run_id=self.run_id,
                env=_armed_paper_env(),
            )

    def test_danger_action_denied_carries_structured_fields(self):
        with self.assertRaises(cp.DangerActionDenied) as cm:
            cp.revalidate_danger_action(
                action="paper_submit",
                output_dir=self.tmp,
                run_id=self.run_id,
                env={"KIS_ENV": "paper"},
                confirm_submit_checked=True,
                dry_run_rc_clean=True,
            )
        self.assertEqual(cm.exception.action, "paper_submit")
        self.assertTrue(cm.exception.reason)


if __name__ == "__main__":
    unittest.main()
