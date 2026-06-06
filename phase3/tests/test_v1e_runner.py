"""V1-E.2 — Headless V1 runner tests.

We do not spawn real subprocesses; every external boundary is
injectable so the orchestrator can be exercised hermetically:

* T7 subprocess     → injected via ``t7_subprocess_run``
* paper_submit/apply subprocess → injected via ``subprocess_run``
* SMTP dispatch     → ``send_mail=False`` skips it
* arm token         → injected ``runtime_dir`` + ``arm_now``
* env gates         → ``env=`` mapping

Pinned invariants:

* Default-safe: ``require_arm_token=True`` skips with rc=0 when no
  token exists (launchd retries only on non-zero).
* Env-gate halt produces rc=2 with a clear reason; no T7 invocation.
* T7 failure halts before generate_intents.
* Successful T7 → generate_intents → paper_submit subprocess chain
  hands the freshly-detected run_id all the way through.
* ``--skip-t7`` without a run_id is rejected with rc=2.
* CLI subcommand routing matches the documented surface.
"""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Mapping, Optional, Sequence
from unittest.mock import patch

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_arm, v1_runner


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _ok_env() -> Dict[str, str]:
    return {
        "KIS_ENV": "paper",
        v1_runner.SUBMIT_ENV_GATE: "true",
        v1_runner.CANCEL_ENV_GATE: "true",
        v1_runner.APPLY_ENV_GATE: "true",
    }


def _write_yaml(td: Path, output_dir: Path) -> Path:
    cfg = td / "config_real.yaml"
    cfg.write_text(
        f"paths:\n  output_dir: {output_dir}\n",
        encoding="utf-8",
    )
    return cfg


def _arm_today(runtime_dir: Path,
               now: datetime) -> v1_arm.ArmToken:
    return v1_arm.write_arm_token(
        runtime_dir=runtime_dir, now=now, armed_by="test")


def _make_t7_fake(
    *,
    rc: int = 0,
    new_run_id: str = "20260526_120000_daily",
    recs: int = 3,
    daily_runs_dir: Optional[Path] = None,
):
    """Fake subprocess for ``t7_runner.run_t7_generate``."""
    calls: List[Dict[str, Any]] = []

    def fake(argv: Sequence[str], *, env: Mapping[str, str],
             timeout: Optional[float] = None
             ) -> subprocess.CompletedProcess:
        calls.append({"argv": list(argv), "env": dict(env)})
        if daily_runs_dir is not None and rc == 0 and new_run_id:
            rd = daily_runs_dir / new_run_id
            rd.mkdir(parents=True, exist_ok=True)
            if recs > 0:
                df = pd.DataFrame({
                    "RecRowId": list(range(1, recs + 1)),
                    "Action": ["BUY_NEW"] * recs,
                    "Ticker": [f"T{i}" for i in range(recs)],
                    "Shares": [1] * recs,
                    "Price": [10.0 + i for i in range(recs)],
                })
                df.to_csv(rd / "recommendations.csv", index=False)
        return subprocess.CompletedProcess(
            args=list(argv), returncode=rc,
            stdout="", stderr="")
    fake._calls = calls  # type: ignore[attr-defined]
    return fake


def _make_submit_fake(rc: int = 0):
    calls: List[Dict[str, Any]] = []

    def fake(argv: Sequence[str], *, env: Mapping[str, str],
             timeout: Optional[float] = None
             ) -> subprocess.CompletedProcess:
        calls.append({"argv": list(argv), "env": dict(env)})
        return subprocess.CompletedProcess(
            args=list(argv), returncode=rc,
            stdout="ok", stderr="")
    fake._calls = calls  # type: ignore[attr-defined]
    return fake


# ──────────────────────────────────────────────────────────────────────
# arm-token gate
# ──────────────────────────────────────────────────────────────────────
class TestArmTokenGate(unittest.TestCase):

    def test_unarmed_skips_with_rc0(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=True,
                runtime_dir=td / "rt",
                env=_ok_env(),
                send_mail=False,
                t7_subprocess_run=_make_t7_fake(
                    daily_runs_dir=out / "daily_runs"),
                subprocess_run=_make_submit_fake(),
            )
            self.assertEqual(r.rc, 0, "safe-skip must be rc=0")
            self.assertFalse(r.arm_ok)
            self.assertIn("no arm token", r.halt_reason or "")
            self.assertEqual(r.stages, [],
                "no stages should run when arm gate fails")

    def test_armed_proceeds(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            _arm_today(td / "rt", now)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=True,
                runtime_dir=td / "rt", arm_now=now,
                env=_ok_env(),
                send_mail=False,
                t7_subprocess_run=_make_t7_fake(
                    daily_runs_dir=out / "daily_runs"),
                subprocess_run=_make_submit_fake(),
            )
            self.assertTrue(r.arm_ok)
            self.assertEqual(r.rc, 0)
            keys = [s["key"] for s in r.stages]
            self.assertEqual(keys, [
                "t7_generate", "generate_intents",
                "paper_submit_and_apply"])

    def test_no_arm_bypass_works(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(),
                send_mail=False,
                t7_subprocess_run=_make_t7_fake(
                    daily_runs_dir=out / "daily_runs"),
                subprocess_run=_make_submit_fake(),
            )
            self.assertEqual(r.rc, 0)
            self.assertTrue(r.arm_ok)


# ──────────────────────────────────────────────────────────────────────
# env-gate validator
# ──────────────────────────────────────────────────────────────────────
class TestCheckEnvGates(unittest.TestCase):

    def test_all_set(self):
        self.assertIsNone(v1_runner.check_env_gates(_ok_env()))

    def test_missing_paper(self):
        e = _ok_env()
        e["KIS_ENV"] = "live"
        reason = v1_runner.check_env_gates(e)
        self.assertIn("KIS_ENV", reason)
        self.assertIn("paper", reason)

    def test_missing_submit_gate(self):
        e = _ok_env()
        e.pop(v1_runner.SUBMIT_ENV_GATE)
        reason = v1_runner.check_env_gates(e)
        self.assertIn(v1_runner.SUBMIT_ENV_GATE, reason)


# ──────────────────────────────────────────────────────────────────────
# pipeline halt paths
# ──────────────────────────────────────────────────────────────────────
class TestPipelineHaltPaths(unittest.TestCase):

    def test_env_gate_halt_does_not_run_t7(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            t7_fake = _make_t7_fake(
                daily_runs_dir=out / "daily_runs")
            env = _ok_env()
            env.pop(v1_runner.APPLY_ENV_GATE)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=env, send_mail=False,
                t7_subprocess_run=t7_fake,
                subprocess_run=_make_submit_fake(),
            )
            self.assertEqual(r.rc, 2)
            self.assertIn(v1_runner.APPLY_ENV_GATE, r.halt_reason)
            self.assertEqual(t7_fake._calls, [],
                "T7 must not be invoked when env gates fail")

    def test_t7_failure_halts_before_generate_intents(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            t7_fake = _make_t7_fake(rc=2)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
                t7_subprocess_run=t7_fake,
                subprocess_run=_make_submit_fake(),
            )
            self.assertEqual(r.rc, 2)
            self.assertIn("T7", r.halt_reason)
            self.assertEqual([s["key"] for s in r.stages],
                             ["t7_generate"])
            self.assertIsNotNone(r.t7_payload)
            self.assertFalse(r.t7_payload["ok"])

    def test_skip_t7_requires_run_id(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                skip_t7=True, run_id_override="",
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
            )
            self.assertEqual(r.rc, 2)
            self.assertIn("--skip-t7 requires", r.halt_reason)

    def test_generate_intents_failure_halts_before_submit(self):
        """If recommendations.csv exists but has 0 BUY rows, the
        generate_intents stage must halt before any submit call."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            t7_fake = _make_t7_fake(
                recs=0,    # T7 writes a header-only file
                daily_runs_dir=out / "daily_runs",
            )
            submit_fake = _make_submit_fake()
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
                t7_subprocess_run=t7_fake,
                subprocess_run=submit_fake,
            )
            # t7_runner itself halts on 0-row recs.csv; we don't
            # even reach generate_intents. The halt key is t7.
            self.assertEqual(r.rc, 2)
            self.assertEqual(submit_fake._calls, [],
                "submit subprocess must not be invoked")


# ──────────────────────────────────────────────────────────────────────
# Happy path — end-to-end stage list + run_id propagation
# ──────────────────────────────────────────────────────────────────────
class TestHappyPath(unittest.TestCase):

    def test_full_chain_passes_run_id_to_submit(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            new_id = "20260526_223714_daily"
            t7_fake = _make_t7_fake(
                new_run_id=new_id, recs=4,
                daily_runs_dir=out / "daily_runs",
            )
            submit_fake = _make_submit_fake(rc=0)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
                t7_subprocess_run=t7_fake,
                subprocess_run=submit_fake,
            )
            self.assertEqual(r.rc, 0, r.halt_reason)
            self.assertEqual(r.run_id, new_id)

            # T7 + generate_intents + paper_submit_and_apply
            self.assertEqual(
                [s["key"] for s in r.stages],
                ["t7_generate", "generate_intents",
                 "paper_submit_and_apply"])

            # submit subprocess must have received the T7-detected
            # run_id, not whatever was in run_id_override.
            self.assertEqual(len(submit_fake._calls), 1)
            argv = submit_fake._calls[0]["argv"]
            self.assertIn("--run-id", argv)
            self.assertEqual(argv[argv.index("--run-id") + 1], new_id)
            self.assertIn("--paper-submit", argv)
            self.assertIn("--apply-t10", argv)

            # T7 payload exposed for the R11B mail body.
            self.assertIsNotNone(r.t7_payload)
            self.assertTrue(r.t7_payload["ok"])
            self.assertEqual(
                r.t7_payload["recommendations_count"], 4)
            self.assertTrue(r.t7_payload["suppressed_mail"])

    def test_apply_t10_false_drops_apply_flag_and_renames_stage(self):
        """``apply_t10=False`` is the **test-fire safety**: orders
        submitted minutes before US market open sit OPEN and would
        cause ``t10_applicator`` to policy-abort. The CLI flag
        ``--no-apply`` is wired to this path; the submit subprocess
        must NOT see ``--apply-t10`` and the stage key must be
        ``paper_submit_only`` so logs/mail clearly distinguish a
        plumbing test from a real fire."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out)
            new_id = "20260527_132110_daily"
            t7_fake = _make_t7_fake(
                new_run_id=new_id, recs=3,
                daily_runs_dir=out / "daily_runs",
            )
            submit_fake = _make_submit_fake(rc=0)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
                t7_subprocess_run=t7_fake,
                subprocess_run=submit_fake,
                apply_t10=False,
            )
            self.assertEqual(r.rc, 0, r.halt_reason)
            self.assertEqual(
                [s["key"] for s in r.stages],
                ["t7_generate", "generate_intents",
                 "paper_submit_only"])
            argv = submit_fake._calls[0]["argv"]
            self.assertIn("--paper-submit", argv)
            self.assertNotIn(
                "--apply-t10", argv,
                "apply_t10=False must drop the apply flag — this "
                "is the whole point of --no-apply pre-market.")


# ──────────────────────────────────────────────────────────────────────
# CLI surface
# ──────────────────────────────────────────────────────────────────────
class TestCli(unittest.TestCase):

    def test_arm_today_subcommand_writes_token(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = v1_runner.main([
                    "arm-today",
                    "--runtime-dir", str(td),
                    "--note", "CLI test",
                    "--date-kst", "2026-05-26",
                ])
            self.assertEqual(rc, 0)
            files = list(td.iterdir())
            names = sorted(p.name for p in files)
            self.assertEqual(names,
                             ["v1_armed_2026-05-26.json"])

    def test_status_subcommand_returns_zero_when_armed(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            # Arm today
            v1_arm.write_arm_token(runtime_dir=td)
            buf = io.StringIO()
            with patch("sys.stdout", buf), \
                 patch.dict("os.environ", _ok_env(), clear=False):
                rc = v1_runner.main([
                    "status", "--runtime-dir", str(td),
                ])
            self.assertEqual(rc, 0)

    def test_status_subcommand_returns_nonzero_when_unarmed(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf), \
                 patch.dict("os.environ", _ok_env(), clear=False):
                rc = v1_runner.main([
                    "status", "--runtime-dir", str(td),
                ])
            self.assertEqual(rc, 1)

    def test_run_subcommand_routes_to_pipeline(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf), \
                 patch.dict("os.environ", _ok_env(), clear=False):
                rc = v1_runner.main([
                    "run", "--no-arm", "--no-mail",
                    "--skip-t7", "--run-id", "",
                    "--runtime-dir", str(td),
                ])
            # Empty run_id with --skip-t7 → rc=2
            self.assertEqual(rc, 2)

    def test_dotenv_hydrate_fills_missing_keys_not_present(self):
        """The launchd 22:35 fire's gui session has none of the
        operator's ``~/.zshrc`` exports. ``_hydrate_env_from_dotenv``
        is the only thing that makes ``FMP_API_KEY`` (and KIS creds)
        reach T7 and the broker. Lock the merge semantics:

        * Keys present in os.environ are NOT overwritten (operator
          shell override > .env).
        * Keys absent in os.environ but present in .env are added.
        * Quoted values are unquoted (matches kis_broker_adapter
          parser).
        * Comments and blank lines are skipped.
        """
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"
            dotenv.write_text(
                "# comment\n"
                "\n"
                "FMP_API_KEY=fmpkey123\n"
                "KIS_APP_KEY='kis-quoted'\n"
                'KIS_APP_SECRET="kis-dq"\n'
                "EXISTING=should_not_override\n",
                encoding="utf-8",
            )
            target: Dict[str, str] = {"EXISTING": "from_environ"}
            added = v1_runner._hydrate_env_from_dotenv(
                dotenv_path=dotenv, target=target)
            self.assertEqual(
                sorted(added),
                ["FMP_API_KEY", "KIS_APP_KEY", "KIS_APP_SECRET"])
            self.assertEqual(target["FMP_API_KEY"], "fmpkey123")
            self.assertEqual(target["KIS_APP_KEY"], "kis-quoted")
            self.assertEqual(target["KIS_APP_SECRET"], "kis-dq")
            self.assertEqual(target["EXISTING"], "from_environ",
                "os.environ MUST win over .env to allow shell "
                "overrides for debugging.")

    def test_dotenv_hydrate_no_file_is_noop(self):
        """Missing .env is NOT a failure — operators may rely on
        shell exports + plist EnvironmentVariables only. The
        function must return an empty list and not raise."""
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"  # does not exist
            target: Dict[str, str] = {}
            added = v1_runner._hydrate_env_from_dotenv(
                dotenv_path=dotenv, target=target)
            self.assertEqual(added, [])
            self.assertEqual(target, {})

    def test_no_apply_flag_is_accepted_by_cli_parser(self):
        """The control panel test-fire button passes ``--no-apply``.
        This guards against accidental removal of that CLI surface.
        We use ``--skip-t7`` with empty run_id to bail before any
        real work — we only care that argparse accepts the flag."""
        with TemporaryDirectory() as td:
            td = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf), \
                 patch.dict("os.environ", _ok_env(), clear=False):
                rc = v1_runner.main([
                    "run", "--no-arm", "--no-mail", "--no-apply",
                    "--skip-t7", "--run-id", "",
                    "--runtime-dir", str(td),
                ])
            self.assertEqual(rc, 2)


# ──────────────────────────────────────────────────────────────────────
# Output-dir resolution
# ──────────────────────────────────────────────────────────────────────
class TestOutputDirResolution(unittest.TestCase):

    def test_yaml_path_picked_up(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            out.mkdir()
            cfg = _write_yaml(td, out)
            self.assertEqual(
                v1_runner._resolve_output_dir(cfg), out)

    def test_explicit_override_wins_when_skip_t7(self):
        """``--output-dir`` overrides the yaml-resolved path for the
        downstream generate_intents step. We exercise this in the
        ``--skip-t7`` path (operator already has a run_dir on disk
        somewhere non-canonical and wants to drive R11A only)."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out_a = td / "a"
            out_b = td / "b"
            cfg = _write_yaml(td, out_a)
            (out_a / "daily_runs").mkdir(parents=True)
            new_id = "20260526_120000_daily"
            rd = out_b / "daily_runs" / new_id
            rd.mkdir(parents=True)
            (rd / "recommendations.csv").write_text(
                "Action,Ticker,Shares,Price,RecRowId\n"
                "BUY,APA,1,18.85,1\n", encoding="utf-8")
            # V1-F.4 — pass an explicit ``runtime_dir`` so the
            # pipeline's status.json writer routes to the temp dir
            # instead of clobbering the real
            # ``phase3/autotrade/runtime/v1_status.json``.
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                skip_t7=True, run_id_override=new_id,
                output_dir_override=out_b,
                require_arm_token=False,
                runtime_dir=td / "rt",
                env=_ok_env(), send_mail=False,
                subprocess_run=_make_submit_fake(rc=0),
            )
            self.assertEqual(r.run_dir,
                             out_b / "daily_runs" / new_id,
                             f"halt_reason={r.halt_reason}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
