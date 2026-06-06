"""V1-A — Unit tests for ``phase3.autotrade.t7_runner``.

We never spawn a real T7 subprocess here (engine init is too heavy
and side-effect-prone). Tests inject a fake ``subprocess_run`` that
simulates ``run_daily`` behaviour by:

1. emitting a configurable rc / stdout / stderr
2. creating (or NOT creating) one or more ``*_daily`` directories
   inside the temp ``daily_runs/`` tree
3. (optionally) writing a ``recommendations.csv`` with N rows
4. respecting injected delays for clock-related assertions

Pinned invariants:

* OK path: rc=0 + exactly one new ``*_daily`` dir + non-empty
  ``recommendations.csv`` → ``ok=True``, run_id detected
* rc!=0: returned verbatim, no run_id even if a dir got created
* zero new dirs: ``ok=False`` with "no new *_daily run_dir" error
* >1 new dirs: picks latest, surfaces warning, still ``ok=True``
* recommendations missing → ``ok=False`` with file-level error
* recommendations empty (0 rows) → ``ok=False`` with semantic error
* timeout: surfaces as rc=124 with timeout message
* subprocess_run crash: rc=255 with crash type
* config not found: rc=2, no subprocess invocation
* env contains ``AUTOTRADE_V1_SUPPRESS_T7_MAIL=true`` when suppressed
* env does NOT carry the suppress key when suppress_mail=False
* shadow / dryrun sibling dirs are ignored when detecting new run_id
* T7 stdout is tail-truncated to ~50 lines (panel log + mail body
  must not pull in a 10 000-line console dump)
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Mapping, Optional, Sequence
from unittest.mock import patch

import pandas as pd

from phase3.autotrade import t7_runner as t7


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _write_yaml(td: Path, output_dir: Path) -> Path:
    """Write a minimal T7 yaml that the runner can parse for
    ``paths.output_dir`` (we exercise both the yaml-lib path and
    the hand-parser fallback elsewhere)."""
    cfg = td / "config_real.yaml"
    cfg.write_text(
        "paths:\n"
        f"  output_dir: {output_dir}\n"
        "email:\n  enabled: false\n",
        encoding="utf-8",
    )
    return cfg


def _seed_daily_runs(daily_runs: Path,
                     existing: Sequence[str] = ()) -> None:
    daily_runs.mkdir(parents=True, exist_ok=True)
    for name in existing:
        (daily_runs / name).mkdir(parents=True, exist_ok=True)


def _make_fake_run(
    *,
    rc: int = 0,
    stdout: str = "",
    stderr: str = "",
    create_dirs: Sequence[str] = (),
    write_recs: Optional[Dict[str, int]] = None,
    raise_exc: Optional[BaseException] = None,
    timeout: bool = False,
    daily_runs_dir: Optional[Path] = None,
):
    """Build a fake ``subprocess_run`` that simulates T7 side effects.

    * ``create_dirs``: list of run_id names to create under
      ``daily_runs_dir`` when T7 "runs"
    * ``write_recs``: ``{run_id: nrows}`` — write a CSV with N rows
      so the recommendations.csv check passes / fails on purpose
    """
    calls: List[Dict[str, Any]] = []

    def fake(argv: Sequence[str], *, env: Mapping[str, str],
             timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        calls.append({"argv": list(argv), "env": dict(env),
                      "timeout": timeout})
        if raise_exc is not None:
            raise raise_exc
        if timeout_:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        if daily_runs_dir is not None:
            for name in create_dirs:
                d = daily_runs_dir / name
                d.mkdir(parents=True, exist_ok=True)
                if write_recs and name in write_recs:
                    n = write_recs[name]
                    df = pd.DataFrame({
                        "RecRowId": list(range(1, n + 1)),
                        "Action": ["BUY_NEW"] * n,
                        "Ticker": [f"T{i}" for i in range(n)],
                        "Shares": [1] * n,
                        "Price": [10.0 + i for i in range(n)],
                    })
                    df.to_csv(d / "recommendations.csv", index=False)
        return subprocess.CompletedProcess(
            args=list(argv), returncode=rc,
            stdout=stdout, stderr=stderr,
        )

    timeout_ = bool(timeout)
    fake._calls = calls  # type: ignore[attr-defined]
    return fake


# ──────────────────────────────────────────────────────────────────────
# Happy-path coverage
# ──────────────────────────────────────────────────────────────────────
class TestRunT7Happy(unittest.TestCase):

    def test_clean_run_returns_new_run_id_and_rec_count(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            _seed_daily_runs(daily_runs, ["20260101_120000_daily"])
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_223714_daily"
            fake = _make_fake_run(
                rc=0,
                stdout="…artifact written…",
                create_dirs=[new_id],
                write_recs={new_id: 12},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg,
                subprocess_run=fake,
                base_env={},
            )
            self.assertTrue(r.ok, f"expected ok; error={r.error!r}")
            self.assertEqual(r.rc, 0)
            self.assertEqual(r.run_id, new_id)
            self.assertEqual(r.run_dir, daily_runs / new_id)
            self.assertEqual(r.recommendations_count, 12)
            self.assertEqual(r.error, "")

    def test_argv_calls_phase3_daily_runner_module(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            (out_dir / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            fake = _make_fake_run(
                rc=0,
                create_dirs=["20260526_120000_daily"],
                write_recs={"20260526_120000_daily": 1},
                daily_runs_dir=out_dir / "daily_runs",
            )
            t7.run_t7_generate(config_path=cfg, subprocess_run=fake,
                               base_env={})
            argv = fake._calls[0]["argv"]
            self.assertIn("phase3.daily_runner", argv)
            self.assertIn("--force-rebalance", argv)
            self.assertIn("--config", argv)
            self.assertIn(str(cfg), argv)
            # Must NOT pass --dry-run; V1 wants a real artifact.
            self.assertNotIn("--dry-run", argv)

    def test_progress_callback_invoked(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            (out_dir / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_120000_daily"
            fake = _make_fake_run(
                rc=0, create_dirs=[new_id], write_recs={new_id: 1},
                daily_runs_dir=out_dir / "daily_runs",
            )
            lines: List[str] = []
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={}, on_progress=lines.append,
            )
            self.assertTrue(r.ok)
            self.assertTrue(any("starting:" in l for l in lines))
            self.assertTrue(any(f"run_id={new_id}" in l for l in lines))


# ──────────────────────────────────────────────────────────────────────
# Suppress-mail env contract
# ──────────────────────────────────────────────────────────────────────
class TestSuppressMailEnv(unittest.TestCase):

    def _setup(self, td: Path):
        out_dir = td / "output"
        (out_dir / "daily_runs").mkdir(parents=True)
        cfg = _write_yaml(td, out_dir)
        new_id = "20260526_120000_daily"
        fake = _make_fake_run(
            rc=0, create_dirs=[new_id], write_recs={new_id: 1},
            daily_runs_dir=out_dir / "daily_runs",
        )
        return cfg, fake

    def test_suppress_true_sets_env(self):
        with TemporaryDirectory() as td:
            cfg, fake = self._setup(Path(td))
            t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={"FMP_API_KEY": "x"},
                suppress_mail=True,
            )
            env = fake._calls[0]["env"]
            self.assertEqual(env.get(t7.SUPPRESS_T7_MAIL_ENV), "true")
            # base_env must still be carried through.
            self.assertEqual(env.get("FMP_API_KEY"), "x")

    def test_suppress_false_removes_env(self):
        with TemporaryDirectory() as td:
            cfg, fake = self._setup(Path(td))
            t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={t7.SUPPRESS_T7_MAIL_ENV: "true"},
                suppress_mail=False,
            )
            env = fake._calls[0]["env"]
            self.assertNotIn(t7.SUPPRESS_T7_MAIL_ENV, env,
                "must scrub stale suppress flag when caller "
                "explicitly turns it off")

    def test_extra_env_merged(self):
        with TemporaryDirectory() as td:
            cfg, fake = self._setup(Path(td))
            t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={},
                extra_env={"FOO": "bar"},
            )
            self.assertEqual(fake._calls[0]["env"].get("FOO"), "bar")


# ──────────────────────────────────────────────────────────────────────
# Failure paths
# ──────────────────────────────────────────────────────────────────────
class TestFailures(unittest.TestCase):

    def test_nonzero_rc(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            (out_dir / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            fake = _make_fake_run(rc=2, stdout="boom",
                                  stderr="bad")
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertFalse(r.ok)
            self.assertEqual(r.rc, 2)
            self.assertEqual(r.run_id, "")
            self.assertIn("rc=2", r.error)
            self.assertEqual(r.stderr_tail.strip(), "bad")

    def test_no_new_run_dir(self):
        """rc=0 but T7 silently exited without writing a *_daily dir."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            _seed_daily_runs(out_dir / "daily_runs",
                             ["20260101_120000_daily"])
            cfg = _write_yaml(td, out_dir)
            fake = _make_fake_run(rc=0)  # creates nothing
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertFalse(r.ok)
            self.assertIn("no new *_daily", r.error)

    def test_multiple_new_dirs_picks_latest_with_warning(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            older = "20260526_120000_daily"
            newer = "20260526_140000_daily"
            fake = _make_fake_run(
                rc=0,
                create_dirs=[older, newer],
                write_recs={newer: 5, older: 5},
                daily_runs_dir=daily_runs,
            )
            warnings: List[str] = []
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={}, on_progress=warnings.append,
            )
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.run_id, newer)
            self.assertTrue(any("multiple new" in m
                                for m in warnings),
                            f"warning expected; got {warnings}")

    def test_recommendations_csv_missing(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_120000_daily"
            # create_dirs writes the dir but write_recs is None ⇒
            # no recommendations.csv inside.
            fake = _make_fake_run(
                rc=0, create_dirs=[new_id],
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertFalse(r.ok)
            self.assertEqual(r.run_id, new_id)
            self.assertIn("recommendations.csv missing", r.error)

    def test_recommendations_csv_empty(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_120000_daily"
            fake = _make_fake_run(
                rc=0, create_dirs=[new_id],
                write_recs={new_id: 0},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertFalse(r.ok)
            self.assertIn("zero rows", r.error)

    def test_config_not_found(self):
        with TemporaryDirectory() as td:
            cfg = Path(td) / "missing.yaml"
            r = t7.run_t7_generate(
                config_path=cfg, base_env={},
                subprocess_run=lambda *a, **k: self.fail(
                    "subprocess must not be called if config missing"),
            )
            self.assertFalse(r.ok)
            self.assertEqual(r.rc, 2)
            self.assertIn("config not found", r.error)

    def test_timeout(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            (out_dir / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            fake = _make_fake_run(timeout=True)
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={}, timeout_sec=5,
            )
            self.assertFalse(r.ok)
            self.assertEqual(r.rc, 124)
            self.assertIn("timed out", r.error)

    def test_subprocess_crash(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            (out_dir / "daily_runs").mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            fake = _make_fake_run(
                raise_exc=OSError("fork failed"))
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertFalse(r.ok)
            self.assertEqual(r.rc, 255)
            self.assertIn("OSError", r.error)


# ──────────────────────────────────────────────────────────────────────
# Snapshot suffix isolation
# ──────────────────────────────────────────────────────────────────────
class TestSuffixIsolation(unittest.TestCase):

    def test_shadow_and_dryrun_dirs_ignored(self):
        """T7 writes ``*_daily``, ``*_dryrun`` (when dry-run), and
        ``*_shadow`` (shadow pass) siblings. Only ``*_daily`` is the
        canonical artifact R11A consumes — V1 must never pick up the
        shadow run_id by mistake."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            _seed_daily_runs(daily_runs, [
                "20260526_120000_shadow",
                "20260526_120000_dryrun",
            ])
            cfg = _write_yaml(td, out_dir)
            new_daily = "20260526_120100_daily"
            new_shadow = "20260526_120100_shadow"
            fake = _make_fake_run(
                rc=0,
                create_dirs=[new_daily, new_shadow],
                write_recs={new_daily: 3, new_shadow: 99},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.run_id, new_daily,
                "shadow dir must not be returned as the V1 run_id")
            # recommendations_count must come from the daily dir,
            # not the shadow's 99-row sidecar.
            self.assertEqual(r.recommendations_count, 3)


# ──────────────────────────────────────────────────────────────────────
# V2 — non-trading-day dry-run preview
# ──────────────────────────────────────────────────────────────────────
class TestDryRunPreview(unittest.TestCase):
    """On weekends/holidays the prefetch is demoted to ``--dry-run``:
    daily_runner writes a ``*_dryrun`` run_dir (no persistent state) and
    is told to still send the recommendation mail."""

    def test_dry_run_argv_and_mail_env(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260606_072000_dryrun"
            fake = _make_fake_run(
                rc=0, create_dirs=[new_id], write_recs={new_id: 7},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={}, suppress_mail=False, dry_run=True,
            )
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.run_id, new_id)
            self.assertEqual(r.recommendations_count, 7)
            argv = fake._calls[0]["argv"]
            self.assertIn("--dry-run", argv)
            env = fake._calls[0]["env"]
            # dry-run preview must force the mail despite daily_runner's
            # ``not dry_run`` gate, and must NOT suppress the T7 mail.
            self.assertEqual(env.get("AUTOTRADE_DRYRUN_SEND_MAIL"), "true")
            self.assertNotIn(t7.SUPPRESS_T7_MAIL_ENV, env)

    def test_dry_run_ignores_daily_dirs(self):
        """When dry-run, a freshly created ``*_daily`` sibling must NOT be
        mistaken for the preview run — detection keys on ``*_dryrun``."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            preview_id = "20260606_072000_dryrun"
            stray_daily = "20260606_072000_daily"
            fake = _make_fake_run(
                rc=0, create_dirs=[preview_id, stray_daily],
                write_recs={preview_id: 4, stray_daily: 99},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={}, dry_run=True,
            )
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.run_id, preview_id)
            self.assertEqual(r.recommendations_count, 4)

    def test_default_run_has_no_dryrun_mail_env(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_120000_daily"
            fake = _make_fake_run(
                rc=0, create_dirs=[new_id], write_recs={new_id: 1},
                daily_runs_dir=daily_runs,
            )
            t7.run_t7_generate(config_path=cfg, subprocess_run=fake,
                               base_env={})
            env = fake._calls[0]["env"]
            self.assertNotIn("AUTOTRADE_DRYRUN_SEND_MAIL", env)


# ──────────────────────────────────────────────────────────────────────
# stdout/stderr tail truncation
# ──────────────────────────────────────────────────────────────────────
class TestTailTruncation(unittest.TestCase):

    def test_long_stdout_truncated(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            daily_runs = out_dir / "daily_runs"
            daily_runs.mkdir(parents=True)
            cfg = _write_yaml(td, out_dir)
            new_id = "20260526_120000_daily"
            long_stdout = "\n".join(f"line {i}" for i in range(500))
            fake = _make_fake_run(
                rc=0, stdout=long_stdout,
                create_dirs=[new_id], write_recs={new_id: 1},
                daily_runs_dir=daily_runs,
            )
            r = t7.run_t7_generate(
                config_path=cfg, subprocess_run=fake,
                base_env={})
            tail_lines = r.stdout_tail.splitlines()
            self.assertLessEqual(len(tail_lines), 50)
            # Tail keeps the LAST lines (not the first).
            self.assertEqual(tail_lines[-1], "line 499")


# ──────────────────────────────────────────────────────────────────────
# config parsing fallback
# ──────────────────────────────────────────────────────────────────────
class TestConfigParsing(unittest.TestCase):

    def test_output_dir_extracted(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            out_dir = td / "output"
            cfg = _write_yaml(td, out_dir)
            self.assertEqual(
                t7._daily_runs_dir_from_config(cfg),
                out_dir / "daily_runs",
            )

    def test_missing_output_dir_raises(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "config_real.yaml"
            cfg.write_text("paths:\n  holdings_log: /tmp/h.xlsx\n",
                           encoding="utf-8")
            with self.assertRaises(RuntimeError):
                t7._daily_runs_dir_from_config(cfg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
