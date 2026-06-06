"""V1-F.F1 — Unit tests for the ``t7-prefetch`` runner mode.

The 09:00 KST launchd fire calls ``v1_runner.run_t7_prefetch``. That
function is intentionally narrower than ``run_v1_pipeline``:

* No arm-token gate (the morning recommendation email is
  informational; arming gates trading, not preview).
* Only the ``KIS_ENV=paper`` env check — submit/cancel/apply are
  not required because T7 never calls the broker.
* ``suppress_mail=False`` — T7's existing recommendation mailer
  delivers the picks; the R11B trading digest is sent later at
  the 22:35 fire.
* status.json is written so the panel can show "Last fire" for
  T7 prefetch separately from trade fires.

Pinned invariants
-----------------

* env-gate halt: missing/wrong KIS_ENV → rc=2, no T7 subprocess
* env-gate halt: SUBMIT/CANCEL/APPLY NOT required (defended-against
  regression: V1-E ran these checks on T7 too, which broke ad-hoc
  daytime ``t7-prefetch`` CLI use)
* happy path: T7 invoked with ``suppress_mail=False`` so its own
  mailer fires; rc=0; result.run_id populated
* T7 failure: rc=2 + halt_reason; status.json finishes terminal
* status.json reflects each stage transition
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Mapping, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_runner, v1_status   # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Helpers — mock T7 subprocess that records env (so we can assert
# suppress_mail wiring) and creates the artefacts T7 normally would.
# ──────────────────────────────────────────────────────────────────────
def _write_yaml(td: Path, output_dir: Path) -> Path:
    cfg = td / "config_t7.yaml"
    cfg.write_text(
        "paths:\n"
        f"  output_dir: {output_dir}\n"
        "email:\n  enabled: false\n",
        encoding="utf-8",
    )
    return cfg


def _make_t7_fake(
    *,
    rc: int = 0,
    new_run_id: str = "20260528_090015_daily",
    recs: int = 5,
    daily_runs_dir: Optional[Path] = None,
):
    """Create a fake subprocess runner that mimics T7 side-effects.
    Captures env so the test can assert ``AUTOTRADE_V1_SUPPRESS_T7_MAIL``
    behaviour."""
    calls: List[Dict[str, Any]] = []

    def fake(argv: Sequence[str], *,
             env: Mapping[str, str],
             timeout: Optional[float] = None,
             ) -> subprocess.CompletedProcess:
        calls.append({"argv": list(argv), "env": dict(env),
                       "timeout": timeout})
        if daily_runs_dir is not None and rc == 0:
            d = daily_runs_dir / new_run_id
            d.mkdir(parents=True, exist_ok=True)
            csv = d / "recommendations.csv"
            csv.write_text(
                "Action,Ticker,Shares,Price,RecRowId\n"
                + "\n".join(
                    f"BUY,SYM{i},1,1.0,{i}" for i in range(recs))
                + "\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(
            argv, rc, stdout="t7 fake stdout\n", stderr="")

    fake._calls = calls  # type: ignore[attr-defined]
    return fake


def _t7_env_ok() -> Dict[str, str]:
    """Env that satisfies ``check_t7_env_gates`` (KIS_ENV=paper
    only). Deliberately omits SUBMIT/CANCEL/APPLY to guard against
    accidental re-coupling of T7 to broker gates."""
    return {"KIS_ENV": "paper"}


# ──────────────────────────────────────────────────────────────────────
# T7-only env-gate semantics
# ──────────────────────────────────────────────────────────────────────
class TestT7EnvGate(unittest.TestCase):

    def test_missing_kis_env_blocks(self):
        e = {}
        self.assertIsNotNone(v1_runner.check_t7_env_gates(e))

    def test_kis_env_paper_passes(self):
        self.assertIsNone(
            v1_runner.check_t7_env_gates({"KIS_ENV": "paper"}))

    def test_kis_env_live_blocked(self):
        """Defence in depth: t7-prefetch never calls the broker,
        but if someone mistakenly switched the t7 plist to live,
        we want the gate to refuse before any FMP call burns API
        quota under a non-paper context."""
        e = {"KIS_ENV": "live"}
        msg = v1_runner.check_t7_env_gates(e)
        self.assertIsNotNone(msg)
        self.assertIn("paper", msg)

    def test_submit_cancel_apply_NOT_required(self):
        """V1-F: the T7 plist intentionally omits SUBMIT/CANCEL/APPLY
        gates. If ``check_t7_env_gates`` started requiring them we'd
        have a regression where the 09:00 fire halts on an empty
        env — the entire point of the narrower gate is that the
        T7 prefetch is broker-free and so doesn't need those vars.
        """
        e = {"KIS_ENV": "paper"}
        self.assertIsNone(v1_runner.check_t7_env_gates(e))


# ──────────────────────────────────────────────────────────────────────
# run_t7_prefetch — happy path
# ──────────────────────────────────────────────────────────────────────
class TestRunT7Prefetch(unittest.TestCase):

    def test_happy_path_writes_status_and_recs(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            fake = _make_t7_fake(
                new_run_id="20260528_090005_daily", recs=3,
                daily_runs_dir=out / "daily_runs",
            )
            status_path = td / "v1_status.json"
            r = v1_runner.run_t7_prefetch(
                config_path=cfg,
                env=_t7_env_ok(),
                t7_subprocess_run=fake,
                status_path=status_path,
            )
            self.assertEqual(r.rc, 0, r.halt_reason)
            self.assertEqual(r.run_id, "20260528_090005_daily")
            # Status snapshot reflects t7_prefetch + terminal rc=0.
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.fire_label, "t7_prefetch")
            self.assertEqual(snap.final_rc, 0)
            self.assertEqual(snap.run_id, "20260528_090005_daily")
            self.assertFalse(snap.in_progress)

    def test_suppress_mail_is_false_on_t7_subprocess(self):
        """V1-F's reason-for-being: T7 prefetch sends its OWN email.
        That hinges on ``suppress_mail=False`` reaching t7_runner →
        daily_runner subprocess env. Lock the wiring here."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            fake = _make_t7_fake(
                new_run_id="20260528_090015_daily", recs=2,
                daily_runs_dir=out / "daily_runs",
            )
            v1_runner.run_t7_prefetch(
                config_path=cfg,
                env=_t7_env_ok(),
                t7_subprocess_run=fake,
                status_path=td / "v1_status.json",
            )
            # T7 subprocess invoked exactly once.
            self.assertEqual(len(fake._calls), 1)
            env = fake._calls[0]["env"]
            # The suppress key must NOT be true — that would silence
            # the T7 email, defeating V1-F.
            v = env.get("AUTOTRADE_V1_SUPPRESS_T7_MAIL", "")
            self.assertNotIn(
                str(v).strip().lower(), {"1", "true", "yes", "on"},
                "T7 prefetch must NOT suppress its own email")

    def test_t7_failure_returns_rc2_and_status_terminal(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            # rc=2 + don't create the run_dir so t7_runner classifies
            # it as a failure
            fake = _make_t7_fake(rc=2, daily_runs_dir=None)
            status_path = td / "v1_status.json"
            r = v1_runner.run_t7_prefetch(
                config_path=cfg,
                env=_t7_env_ok(),
                t7_subprocess_run=fake,
                status_path=status_path,
            )
            self.assertEqual(r.rc, 2)
            self.assertTrue(r.halt_reason)
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.final_rc, 2)
            self.assertFalse(snap.in_progress)

    def test_env_gate_halt_skips_t7_subprocess(self):
        """If KIS_ENV is wrong we MUST NOT spawn the T7 subprocess —
        that would burn FMP API quota and write a partial run_dir
        to disk."""
        with TemporaryDirectory() as td:
            td = Path(td)
            cfg = _write_yaml(td, td / "out")
            fake = _make_t7_fake(daily_runs_dir=None)
            r = v1_runner.run_t7_prefetch(
                config_path=cfg,
                env={"KIS_ENV": ""},
                t7_subprocess_run=fake,
                status_path=td / "v1_status.json",
            )
            self.assertEqual(r.rc, 2)
            self.assertEqual(len(fake._calls), 0,
                             "T7 subprocess must NOT be spawned "
                             "when env-gate halts")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
