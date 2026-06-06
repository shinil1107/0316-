"""V1-A — Headless T7 (recommendation generation) runner.

Background
----------

The T7 button in ``phase3/launcher.py`` is the manual entry point an
operator uses to:

1. Refresh OHLCV / financial caches
2. Score the universe with the frozen signal
3. Produce ``recommendations.csv``  +  ``run_meta.json``  +  …
4. Send the T7 recommendation email

That logic lives in ``phase3.daily_runner.run_daily()``. The R11A
one-click coordinator (``oneclick_coordinator``) consumes the
``recommendations.csv`` artifact and drives paper trading on top.
Today the operator must bridge them by hand:

* click T7
* note the freshly-created ``YYYYMMDD_HHMMSS_daily`` run_id
* open ``control_panel``
* paste that run_id
* tick the gates
* click Full Paper Run

V1 collapses this into a single click / single CLI. ``t7_runner``
is the headless wrapper:

* invokes ``phase3.daily_runner`` as a **subprocess** (Q3: isolation
  — engine init is heavy and stateful; running T7 inline with the
  panel risks cross-contamination between consecutive runs)
* detects the new ``*_daily`` run_dir by diffing the
  ``daily_runs/`` listing before vs after the run
* validates the artifact (``recommendations.csv`` exists and
  parses as a non-empty CSV)
* optionally suppresses the T7 email so V1 can fold the recommendation
  payload into the single R11B EOD digest (Q2: merge)

The R11A coordinator then receives the run_id and proceeds with
``generate_intents → dry_run → paper_submit → t10_apply``.

Why subprocess and not in-process
---------------------------------

``run_daily()`` imports the engine (heavy: pandas, signal arrays,
S&P 500 universe TTL cache, …) and mutates a number of module-level
caches (``engine_loader._ENGINE_MODULE`` etc.). Calling it twice in
the same Python process is supported by T7 today (``importlib.reload``
inside ``launcher._t7_live_run``) but the control_panel process
already imports ``smtp_mailer``, ``intents_io``, … and would have to
manage that reload too. Subprocess isolation keeps the panel UI
responsive and guarantees no half-finished T7 state lingers when the
operator re-runs.

Why not just parse stdout for run_id
------------------------------------

``run_daily()`` does print ``Artifact Run ID — <id>`` (line 1758 in
daily_runner.py at the time of writing), but parsing log lines is a
brittle contract that drifts the moment someone tweaks the print.
Directory-diffing the ``daily_runs/`` tree is the source of truth
the panel/R11A already trust — same path, same naming convention,
no string parsing surface.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple,
)


_LOG = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_PHASE3_ROOT = _HERE.parent                  # /Users/.../0316-/phase3
_REPO_ROOT = _PHASE3_ROOT.parent             # /Users/.../0316-
# T7 MUST write into the SAME ``output_dir`` the autotrade panel +
# v1_runner later read from. The panel and v1_runner both hardcode
# ``--profile paper`` which maps to ``phase3/config.yaml`` (see
# ``reconcile._PROFILE_CONFIG``). If T7 here used config_real.yaml
# instead, T7 would write the artifact under ``output_real/`` and
# paper_submit would then halt at preflight with
# ``artifact run_dir does not exist`` — that was the live failure
# at 13:14:57 KST on 2026-05-27 (one cycle after the env-var typo
# was fixed: T7 + generate_intents succeeded, then daily_runner
# couldn't find the run_id under its own output_dir).
_DEFAULT_T7_CONFIG = _PHASE3_ROOT / "config.yaml"


# Environment variable the T7 process reads to suppress its own mail
# (so V1 can deliver a single EOD digest from R11B). This is the
# *only* T7-side knob V1 introduces — kept here as a named constant
# so the daily_runner patch and the t7_runner contract reference
# the same key.
SUPPRESS_T7_MAIL_ENV: str = "AUTOTRADE_V1_SUPPRESS_T7_MAIL"


@dataclass(frozen=True)
class T7RunResult:
    """Outcome of one T7 invocation.

    * ``ok``            — True iff rc==0 AND new run_dir was detected
                          AND recommendations.csv exists & is non-empty
    * ``rc``            — subprocess return code (0 = clean exit)
    * ``run_id``        — the freshly-created ``YYYYMMDD_HHMMSS_daily``
                          ID, or ``""`` if detection failed
    * ``run_dir``       — full path to the new run dir, or ``None``
    * ``recommendations_count`` — rows in ``recommendations.csv``
    * ``error``         — short human-readable failure reason
    * ``duration_sec``  — wall time, useful for the panel log
    * ``stdout_tail``   — last ~50 lines of T7 output for surfacing
                          to the R11B email body and the panel log
    """
    ok: bool
    rc: int
    run_id: str
    run_dir: Optional[Path]
    recommendations_count: int
    error: str = ""
    duration_sec: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _daily_runs_dir_from_config(config_path: Path) -> Path:
    """Read ``paths.output_dir`` from the T7 yaml (same key
    ``daily_runner.load_config`` consumes) and return the
    ``daily_runs/`` subtree. We use a minimal parser so this module
    does not pull yaml just for one key."""
    try:
        import yaml  # type: ignore
        with open(config_path, "r", encoding="utf-8") as f:
            conf = yaml.safe_load(f) or {}
        out = (conf.get("paths") or {}).get("output_dir", "")
        if out:
            return Path(out).expanduser() / "daily_runs"
    except ImportError:
        # Pure-python fallback: scan for the one line we need.
        in_paths = False
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("paths:"):
                    in_paths = True
                    continue
                if in_paths and not (line.startswith(" ")
                                     or line.startswith("\t")):
                    in_paths = False
                if in_paths and "output_dir" in line:
                    _, _, v = line.partition(":")
                    v = v.strip().strip("\"'")
                    if v:
                        return Path(v).expanduser() / "daily_runs"
    raise RuntimeError(
        f"could not locate paths.output_dir in {config_path}")


def _snapshot_run_ids(daily_runs_dir: Path, *,
                      suffix: str = "_daily") -> Set[str]:
    """Return the set of existing ``*_daily`` run_ids under
    ``daily_runs_dir``. Used to diff before/after T7.

    We intentionally filter by suffix ``_daily``: T7 also writes
    ``*_shadow`` and (in dry-run mode) ``*_dryrun`` siblings which
    we MUST NOT pick up as the "main" run_id.
    """
    if not daily_runs_dir.exists():
        return set()
    out: Set[str] = set()
    for p in daily_runs_dir.iterdir():
        if not p.is_dir():
            continue
        if p.name.endswith(suffix):
            out.add(p.name)
    return out


def _detect_new_run_id(
    before: Set[str],
    after: Set[str],
) -> Tuple[Optional[str], str]:
    """Return the single newly-created ``*_daily`` run_id, or
    ``(None, why)`` if the diff is ambiguous.

    The expected case is exactly one new entry. We are explicit
    about the failure modes:

    * zero new entries → T7 silently exited without writing
    * >1 new entries  → race / leftover dir / lost wall-clock
    """
    new = after - before
    if not new:
        return None, "no new *_daily run_dir created by T7"
    if len(new) > 1:
        # If multiple appeared, pick the lexicographically-latest
        # (timestamps sort correctly under ``YYYYMMDD_HHMMSS_…``)
        # but flag it loudly — the operator should investigate.
        latest = sorted(new)[-1]
        return latest, (f"multiple new *_daily run_dirs detected "
                        f"({sorted(new)}); picking latest {latest!r}")
    return next(iter(new)), ""


def _count_recommendations(run_dir: Path) -> Tuple[int, str]:
    """Read ``recommendations.csv`` row count; return ``(n, err)``."""
    rec_path = run_dir / "recommendations.csv"
    if not rec_path.exists():
        return 0, f"recommendations.csv missing in {run_dir}"
    try:
        import pandas as pd  # type: ignore
        df = pd.read_csv(rec_path)
        return int(len(df)), ""
    except Exception as e:  # noqa: BLE001
        return 0, (f"recommendations.csv unreadable: "
                   f"{type(e).__name__}: {e}")


def _tail(text: str, max_lines: int = 50) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


# ──────────────────────────────────────────────────────────────────────
# Subprocess wiring (test-injectable)
# ──────────────────────────────────────────────────────────────────────
SubprocessRunFn = Callable[..., subprocess.CompletedProcess]
"""``(argv, *, env, capture_output, text, timeout) -> CompletedProcess``"""


def _default_subprocess_run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _build_t7_argv(config_path: Path, *, dry_run: bool = False) -> List[str]:
    """Compose the CLI call that exists today at the bottom of
    ``phase3/daily_runner.py``:

        python -m phase3.daily_runner --force-rebalance --config <yaml>

    On a trading day V1 wants the real recommendation artifact written,
    so ``--dry-run`` is omitted. On a NON-trading day the prefetch is
    demoted to ``--dry-run`` (V2 preview): the recommendation email still
    goes out (see ``AUTOTRADE_DRYRUN_SEND_MAIL``) but no persistent state
    (recommendations archive / grace counters / daily log) is mutated, so
    weekend/holiday reruns cannot pollute grace parity."""
    argv = [
        sys.executable, "-m", "phase3.daily_runner",
        "--force-rebalance",
        "--config", str(config_path),
    ]
    if dry_run:
        argv.append("--dry-run")
    return argv


def _build_t7_env(
    *,
    base_env: Mapping[str, str],
    suppress_mail: bool,
    extra_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build the subprocess environment.

    * inherits ``base_env`` (operator's shell)
    * optionally sets ``SUPPRESS_T7_MAIL_ENV`` so daily_runner skips
      its own ``send_daily_email`` call (V1 merges payloads into the
      single R11B EOD digest)
    * ``extra_env`` lets the caller plumb extra vars (used by tests
      and by future v1-D customisation)
    """
    env = dict(base_env)
    if suppress_mail:
        env[SUPPRESS_T7_MAIL_ENV] = "true"
    else:
        env.pop(SUPPRESS_T7_MAIL_ENV, None)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    return env


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────
def run_t7_generate(
    *,
    config_path: Optional[Path] = None,
    suppress_mail: bool = True,
    dry_run: bool = False,
    timeout_sec: Optional[float] = 60 * 30,   # 30 min hard ceiling
    extra_env: Optional[Mapping[str, str]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    subprocess_run: Optional[SubprocessRunFn] = None,
    base_env: Optional[Mapping[str, str]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> T7RunResult:
    """Run T7 (recommendation generation) and return the new run_id.

    Parameters
    ----------
    config_path
        T7 yaml. Defaults to ``phase3/config_real.yaml``.
    suppress_mail
        If True (V1 default), ``daily_runner`` is told to skip its
        own recommendation email via ``$AUTOTRADE_V1_SUPPRESS_T7_MAIL``.
        R11B will fold the payload into the EOD digest instead.
    timeout_sec
        Hard ceiling on the subprocess wall time. The default of 30
        minutes is generous — a typical T7 run completes in <2 min,
        and we want to avoid SIGKILL'ing a slow-but-honest run that
        was just hit by a cache refresh.
    on_progress
        Optional logger taking one string at a time (panel log /
        CLI ``print``).
    subprocess_run, clock
        Test injection points.

    Returns
    -------
    A ``T7RunResult`` summarising what happened. The function NEVER
    raises — failures are recorded in ``ok=False`` + ``error``. This
    matches the coordinator's contract: any stage runner that
    returns a non-zero rc halts the chain cleanly.
    """
    cfg = Path(config_path) if config_path else _DEFAULT_T7_CONFIG
    log = on_progress or (lambda m: _LOG.info(m))
    clock = clock or time.monotonic
    runner = subprocess_run or _default_subprocess_run
    base = dict(base_env if base_env is not None else os.environ)

    if not cfg.exists():
        return T7RunResult(
            ok=False, rc=2, run_id="", run_dir=None,
            recommendations_count=0,
            error=f"T7 config not found: {cfg}",
        )

    try:
        daily_runs_dir = _daily_runs_dir_from_config(cfg)
    except Exception as e:  # noqa: BLE001
        return T7RunResult(
            ok=False, rc=2, run_id="", run_dir=None,
            recommendations_count=0,
            error=(f"could not resolve daily_runs/ from {cfg.name}: "
                   f"{type(e).__name__}: {e}"),
        )

    # On a non-trading day the prefetch is demoted to a dry-run preview:
    # daily_runner writes a ``*_dryrun`` run_dir (not ``*_daily``) and mutates
    # no persistent state, so we diff/detect against the ``_dryrun`` suffix and
    # ask the subprocess to still send the recommendation mail.
    run_suffix = "_dryrun" if dry_run else "_daily"
    before = _snapshot_run_ids(daily_runs_dir, suffix=run_suffix)
    log(f"[t7] config={cfg}")
    log(f"[t7] daily_runs_dir={daily_runs_dir}")
    log(f"[t7] pre-snapshot: {len(before)} *{run_suffix} dirs")
    log(f"[t7] suppress_mail={suppress_mail} dry_run={dry_run}")

    argv = _build_t7_argv(cfg, dry_run=dry_run)
    dryrun_extra = dict(extra_env or {})
    if dry_run:
        # Let the dry-run subprocess send the recommendation email despite the
        # ``not dry_run`` gate in ``daily_runner.run_daily``.
        dryrun_extra["AUTOTRADE_DRYRUN_SEND_MAIL"] = "true"
    env = _build_t7_env(
        base_env=base, suppress_mail=suppress_mail,
        extra_env=dryrun_extra,
    )

    t0 = clock()
    log(f"[t7] starting: {' '.join(argv)}")
    try:
        cp = runner(argv, env=env, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        elapsed = clock() - t0
        return T7RunResult(
            ok=False, rc=124, run_id="", run_dir=None,
            recommendations_count=0,
            error=f"T7 subprocess timed out after {timeout_sec:.0f}s",
            duration_sec=elapsed,
        )
    except Exception as e:  # noqa: BLE001
        elapsed = clock() - t0
        return T7RunResult(
            ok=False, rc=255, run_id="", run_dir=None,
            recommendations_count=0,
            error=f"T7 subprocess crashed: {type(e).__name__}: {e}",
            duration_sec=elapsed,
        )

    elapsed = clock() - t0
    stdout = getattr(cp, "stdout", "") or ""
    stderr = getattr(cp, "stderr", "") or ""
    stdout_tail = _tail(stdout)
    stderr_tail = _tail(stderr)
    log(f"[t7] finished rc={cp.returncode} in {elapsed:.1f}s")

    if cp.returncode != 0:
        return T7RunResult(
            ok=False, rc=int(cp.returncode), run_id="", run_dir=None,
            recommendations_count=0,
            error=(f"T7 subprocess returned rc={cp.returncode} "
                   f"(see stdout_tail / stderr_tail)"),
            duration_sec=elapsed,
            stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        )

    after = _snapshot_run_ids(daily_runs_dir, suffix=run_suffix)
    run_id, why = _detect_new_run_id(before, after)
    if run_id is None:
        return T7RunResult(
            ok=False, rc=cp.returncode, run_id="", run_dir=None,
            recommendations_count=0,
            error=why or f"no new *{run_suffix} run_dir detected",
            duration_sec=elapsed,
            stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        )
    if why:
        # Multi-new-dir warning — surface but proceed.
        log(f"[t7][WARN] {why}")

    run_dir = daily_runs_dir / run_id
    n, rec_err = _count_recommendations(run_dir)
    if rec_err:
        return T7RunResult(
            ok=False, rc=cp.returncode, run_id=run_id, run_dir=run_dir,
            recommendations_count=0,
            error=rec_err, duration_sec=elapsed,
            stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        )
    if n == 0:
        return T7RunResult(
            ok=False, rc=cp.returncode, run_id=run_id, run_dir=run_dir,
            recommendations_count=0,
            error="recommendations.csv has zero rows — nothing to trade",
            duration_sec=elapsed,
            stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        )

    log(f"[t7] OK run_id={run_id} recs={n}")
    return T7RunResult(
        ok=True, rc=cp.returncode, run_id=run_id, run_dir=run_dir,
        recommendations_count=n, error="",
        duration_sec=elapsed,
        stdout_tail=stdout_tail, stderr_tail=stderr_tail,
    )
