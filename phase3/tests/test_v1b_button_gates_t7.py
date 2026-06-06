"""V1-B — button-gate behaviour when ``t7_will_generate=True``.

When the operator un-ticks "Skip T7", the Full Paper Run button must
stay enabled even if no run_id is picked yet AND
``recommendations.csv`` doesn't exist anywhere on disk — because T7
will produce both as the first stage of the coordinator run.

The env-level gates (KIS_ENV, submit/cancel/apply, halt,
t10_journal, authorize checkbox) MUST still apply because they cannot
be created by T7 mid-flight; the operator must arm them up front.

Skip-T7 mode (``t7_will_generate=False``, current R11A v0 behaviour)
must keep the existing per-stage strictness so we cannot regress.
"""

from __future__ import annotations

import json
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


# ──────────────────────────────────────────────────────────────────────
# Helpers — minimal disk-backed fixtures (same idiom as
# ``test_r10_ui_button_gates``)
# ──────────────────────────────────────────────────────────────────────
def _all_gates_env() -> Dict[str, str]:
    return {
        "KIS_ENV": "paper",
        cp.SUBMIT_GATE: "true",
        cp.CANCEL_GATE: "true",
        cp.APPLY_GATE: "true",
    }


def _seed_run(base: Path, run_id: str = "20260526_120000_daily",
              *, with_recs: bool = False, status: str = "unknown") -> Path:
    """Create a minimal run_dir; optionally with recommendations.csv."""
    rd = base / "daily_runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    if status != "unknown":
        (rd / "run_meta.json").write_text(json.dumps(
            {"schema_version": "artifact/v1", "run_id": run_id,
             "status": status}))
    if with_recs:
        (rd / "recommendations.csv").write_text(
            "Action,Ticker,Shares,Price,RecRowId\n"
            # V1-G.2: T7 emits BUY_NEW / BUY_MORE — the legacy bare
            # "BUY" action is no longer accepted by ``intents_io``.
            "BUY_NEW,APA,1,18.85,1\n", encoding="utf-8")
    return rd


def _state(base: Path, run_id: str, env: Dict[str, str]) -> cp.PanelState:
    return cp.compute_panel_state(
        output_dir=base, run_id=run_id, env=env,
        halt_path=base / "halt.json",
    )


# ──────────────────────────────────────────────────────────────────────
# t7_will_generate=True  (V1 default — "Skip T7" un-ticked)
# ──────────────────────────────────────────────────────────────────────
class TestFullPaperRunGate_T7WillGenerate(unittest.TestCase):

    def test_v1_mode_enables_without_run_id(self):
        """T7 will create a fresh run_id — the empty run_id must not
        block the gate."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            state = _state(base, run_id="", env=_all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertTrue(
                g.enabled,
                f"V1 mode must enable with empty run_id; "
                f"reason={g.reason!r}")

    def test_v1_mode_enables_without_recommendations_csv(self):
        """T7 will write recommendations.csv. The gate must NOT
        require it to exist beforehand."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _seed_run(base, with_recs=False, status="unknown")
            state = _state(base, "20260526_120000_daily",
                           _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertTrue(
                g.enabled,
                f"V1 mode must enable without recs.csv; reason={g.reason!r}")

    def test_v1_mode_blocks_when_kis_env_not_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            env = _all_gates_env()
            env["KIS_ENV"] = "live"
            state = _state(base, "", env)
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn("KIS_ENV", g.reason)

    def test_v1_mode_blocks_when_submit_gate_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            env = _all_gates_env()
            env[cp.SUBMIT_GATE] = "false"
            state = _state(base, "", env)
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn(cp.SUBMIT_GATE, g.reason)

    def test_v1_mode_blocks_when_apply_gate_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            env = _all_gates_env()
            env[cp.APPLY_GATE] = "false"
            state = _state(base, "", env)
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn(cp.APPLY_GATE, g.reason)

    def test_v1_mode_requires_authorize_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            state = _state(base, "", _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=True,
                oneclick_authorized_checked=False,    # un-ticked
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn("authorize", g.reason)


# ──────────────────────────────────────────────────────────────────────
# t7_will_generate=False  (Skip-T7 — R11A v0 regression)
# ──────────────────────────────────────────────────────────────────────
class TestFullPaperRunGate_SkipT7Regression(unittest.TestCase):

    def test_skip_t7_still_requires_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            state = _state(base, "", _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=False,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn("no run_id", g.reason)

    def test_skip_t7_still_requires_recommendations_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _seed_run(base, with_recs=False, status="awaiting_execution")
            state = _state(base, "20260526_120000_daily",
                           _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=False,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn("recommendations.csv", g.reason)

    def test_skip_t7_still_requires_awaiting_execution_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _seed_run(base, with_recs=True, status="executed")
            state = _state(base, "20260526_120000_daily",
                           _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=False,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertFalse(g.enabled)
            self.assertIn("awaiting_execution", g.reason)

    def test_skip_t7_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _seed_run(base, with_recs=True, status="awaiting_execution")
            state = _state(base, "20260526_120000_daily",
                           _all_gates_env())
            g = cp.compute_button_gates(
                state, t7_will_generate=False,
                oneclick_authorized_checked=True,
            )["full_paper_run"]
            self.assertTrue(g.enabled, f"reason={g.reason!r}")


# ──────────────────────────────────────────────────────────────────────
# revalidate_danger_action — V1 / Skip-T7 split
# ──────────────────────────────────────────────────────────────────────
class TestRevalidateDangerAction_T7(unittest.TestCase):

    def test_v1_mode_accepts_empty_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            cp.revalidate_danger_action(
                action="full_paper_run",
                output_dir=base,
                run_id="",
                env=_all_gates_env(),
                oneclick_authorized_checked=True,
                t7_will_generate=True,
            )

    def test_skip_t7_still_rejects_empty_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            with self.assertRaises(cp.DangerActionDenied) as ctx:
                cp.revalidate_danger_action(
                    action="full_paper_run",
                    output_dir=base, run_id="",
                    env=_all_gates_env(),
                    oneclick_authorized_checked=True,
                    t7_will_generate=False,
                )
            self.assertIn("no run_id", ctx.exception.reason)

    def test_v1_mode_still_denies_when_env_gates_off(self):
        """V1 relaxes run_id / recs-csv but NOT env gates."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "daily_runs").mkdir()
            env = _all_gates_env()
            env[cp.APPLY_GATE] = "false"
            with self.assertRaises(cp.DangerActionDenied) as ctx:
                cp.revalidate_danger_action(
                    action="full_paper_run",
                    output_dir=base, run_id="",
                    env=env,
                    oneclick_authorized_checked=True,
                    t7_will_generate=True,
                )
            self.assertIn(cp.APPLY_GATE, ctx.exception.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
