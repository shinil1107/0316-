"""Full-Paper-Run one-click coordinator (R11A).

Background
----------

Up through R10F-Q the operator drove the paper trading pipeline
button-by-button from ``control_panel``:

    Generate Intents → Dry Run → Paper Submit → T10 Apply

Every step required them to manually re-check the previous outcome
(was rc 0? was the submit clean? did the gates auto-disarm?). For an
empty schedule that works, but the moment we wired the live test
loop in 20260520 it became clear that the operator's attention is the
bottleneck — not the gates.

R11A keeps the gates 100 % intact and adds a sequencer on top:

1. The operator arms the danger gates exactly the way they would for
   a manual run (Arm Submit, Arm Apply, confirm checkboxes, ...).
2. They tick a *single* one-click authorise box and press
   "Full Paper Run".
3. ``run_oneclick`` walks the stages in order, calls a caller-
   provided ``StageSpec.runner`` for each, writes a marker file after
   each transition, and stops the moment ANY stage returns a non-zero
   rc, raises, or its post-check signals trouble.

The coordinator is intentionally:

* **Stateless** — every call is independent; resumption is the
  caller's responsibility (R11C may add it).
* **IO-free in core** — ``runner`` is provided as a callable so the
  UI can wire the existing ``run_subprocess_streaming`` plumbing
  while tests can stub everything with simple closures.
* **Marker-first** — every stage transition writes
  ``autotrade_oneclick_marker.json`` *before* returning, so a crashed
  panel still leaves a forensic trail.

Halt semantics
--------------

A stage halts the run iff any of:

* ``StageSpec.runner`` returned a non-zero rc, OR
* ``StageSpec.runner`` raised (rc=99, halt_reason carries the
  exception class+message), OR
* a configured post-check returned a non-None reason string, OR
* a configured pre-check returned a non-None reason string (this is
  how we keep `revalidate_danger_action` and the env presence checks
  in the loop without entangling them in Tk).

``overall_rc`` is 0 iff every planned stage ran cleanly to completion.
Otherwise it is the rc of the halting stage (or 1 if the runner
exception path took over).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple,
)


_LOG = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Stage spec / outcome / result
# ──────────────────────────────────────────────────────────────────────
StageRunnerFn = Callable[[], int]
"""Runs the underlying CLI / in-process step. Returns an exit code
where 0 means success. Anything else halts the one-click run.
Raising is allowed and is converted to rc=99 by the coordinator."""


StageCheckFn = Callable[[], Optional[str]]
"""Returns ``None`` to greenlight the transition, or a short human-
facing string to halt with. Used for pre-checks (gate revalidation,
env-var presence) and post-checks (submit_outcome_clean, ...)."""


@dataclass(frozen=True)
class StageSpec:
    """One stage in the one-click plan.

    * ``key`` — canonical short name, matches the marker payload
      (``"generate_intents"``, ``"dry_run"``, ``"paper_submit"``,
      ``"t10_apply"``, etc.). Used for tests and audit.
    * ``label`` — human-readable label for the marker / log lines.
    * ``runner`` — caller-provided callable; see ``StageRunnerFn``.
    * ``pre_check`` — optional callable; runs BEFORE ``runner`` and
      can halt without invoking the runner at all.
    * ``post_check`` — optional callable; runs AFTER a successful
      runner (rc=0). Lets us enforce "paper-submit must have a clean
      outcome" without smuggling that logic into ``runner`` itself.
    """
    key: str
    label: str
    runner: StageRunnerFn
    pre_check: Optional[StageCheckFn] = None
    post_check: Optional[StageCheckFn] = None


@dataclass(frozen=True)
class StageOutcome:
    """Per-stage record written into the result and the marker file."""
    key: str
    label: str
    rc: int
    started_at: str       # ISO-8601 UTC
    ended_at: str         # ISO-8601 UTC
    duration_sec: float
    halt_reason: Optional[str]
    # ``skipped`` is true for stages that never ran (e.g. because a
    # prior stage halted). They appear in ``OneClickResult.stages``
    # so the UI / report can render the full plan, but their rc is
    # -1 and started_at == ended_at == "".
    skipped: bool = False


@dataclass(frozen=True)
class OneClickResult:
    run_id: str
    started_at: str
    ended_at: str
    duration_sec: float
    stages: Tuple[StageOutcome, ...]
    halt_reason: Optional[str]
    overall_rc: int

    @property
    def all_clean(self) -> bool:
        return self.overall_rc == 0 and self.halt_reason is None


# ──────────────────────────────────────────────────────────────────────
# Marker helpers
# ──────────────────────────────────────────────────────────────────────
MARKER_FILENAME = "autotrade_oneclick_marker.json"
MARKER_SCHEMA_VERSION = "autotrade_oneclick_marker/v1"


def write_oneclick_marker(
    marker_path: Path,
    *,
    run_id: str,
    started_at: str,
    last_updated: str,
    stages_planned: Sequence[str],
    stages_completed: Sequence[StageOutcome],
    current_stage: Optional[str],
    halt_reason: Optional[str],
) -> None:
    """Atomically (write→fsync→rename) refresh the marker file.

    We use a rename so a crash mid-write can never leave the marker
    half-written; the rest of the panel reads the JSONified marker on
    every refresh and a partial file would break it."""
    payload: Dict[str, Any] = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "last_updated": last_updated,
        "stages_planned": list(stages_planned),
        "stages_completed": [
            {
                "key": o.key,
                "label": o.label,
                "rc": int(o.rc),
                "started_at": o.started_at,
                "ended_at": o.ended_at,
                "duration_sec": float(o.duration_sec),
                "halt_reason": o.halt_reason,
                "skipped": bool(o.skipped),
            }
            for o in stages_completed
        ],
        "current_stage": current_stage,
        "halt_reason": halt_reason,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker_path.with_suffix(marker_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(marker_path)


def read_oneclick_marker(marker_path: Path) -> Optional[Dict[str, Any]]:
    """Read the marker, or return None if it does not exist / is
    malformed (a torn write would normally leave the .tmp behind, but
    we still defend against a hand-edited file)."""
    if not marker_path.exists():
        return None
    try:
        return json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ──────────────────────────────────────────────────────────────────────
# Coordinator
# ──────────────────────────────────────────────────────────────────────
def _now_utc(now_fn: Callable[[], datetime]) -> datetime:
    """Wrap a caller-injected clock so tests can pin time. Falls back
    to UTC ``datetime.now`` when the caller passes the default."""
    t = now_fn()
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def run_oneclick(
    *,
    run_id: str,
    stages: Sequence[StageSpec],
    marker_path: Path,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_stage_start: Optional[Callable[[StageSpec], None]] = None,
    on_stage_end: Optional[Callable[[StageOutcome], None]] = None,
    on_halt: Optional[Callable[[StageOutcome], None]] = None,
) -> OneClickResult:
    """Execute ``stages`` in order, halting on the first failure.

    Order of operations per stage:

    1. ``pre_check`` (if any) — non-None reason halts BEFORE runner.
    2. ``runner`` — caller-provided closure; exceptions are caught
       and reported as rc=99 + ``halt_reason='runner_exception:...'``.
    3. ``post_check`` (if any, and rc==0) — non-None reason halts
       AFTER runner (e.g. submit_outcome was dirty even though
       paper-submit itself exited 0).

    Any stages after the halting one are recorded as ``skipped=True``
    in the result, with rc=-1.

    Marker is written:

    * Once at the very top (no stages completed yet).
    * After every stage transition (success or halt).
    * Once at the very end (current_stage=None, run is done).

    The marker is the only side effect on disk from this function;
    actual artifact writes live inside ``runner``.
    """
    started_dt = _now_utc(now_fn)
    started_at = _iso(started_dt)
    planned_keys = [s.key for s in stages]

    completed: List[StageOutcome] = []
    halt_reason: Optional[str] = None
    overall_rc = 0

    write_oneclick_marker(
        marker_path,
        run_id=run_id,
        started_at=started_at,
        last_updated=started_at,
        stages_planned=planned_keys,
        stages_completed=[],
        current_stage=stages[0].key if stages else None,
        halt_reason=None,
    )

    for idx, stage in enumerate(stages):
        stage_started_dt = _now_utc(now_fn)
        stage_started_at = _iso(stage_started_dt)

        if on_stage_start is not None:
            try:
                on_stage_start(stage)
            except Exception as e:  # noqa: BLE001
                _LOG.warning(
                    "on_stage_start callback raised for %s: %s",
                    stage.key, e)

        # 1) pre-check
        pre_reason: Optional[str] = None
        if stage.pre_check is not None:
            try:
                pre_reason = stage.pre_check()
            except Exception as e:  # noqa: BLE001
                pre_reason = f"pre_check_exception: {type(e).__name__}: {e}"

        if pre_reason is not None:
            stage_ended_dt = _now_utc(now_fn)
            outcome = StageOutcome(
                key=stage.key, label=stage.label,
                rc=2,
                started_at=stage_started_at,
                ended_at=_iso(stage_ended_dt),
                duration_sec=(stage_ended_dt - stage_started_dt).total_seconds(),
                halt_reason=f"pre_check: {pre_reason}",
            )
            completed.append(outcome)
            halt_reason = outcome.halt_reason
            overall_rc = 2
            if on_stage_end is not None:
                _safe_callback(on_stage_end, outcome)
            if on_halt is not None:
                _safe_callback(on_halt, outcome)
            break

        # 2) runner
        rc: int
        runner_reason: Optional[str] = None
        try:
            rc = int(stage.runner())
        except Exception as e:  # noqa: BLE001
            rc = 99
            runner_reason = f"runner_exception: {type(e).__name__}: {e}"

        stage_ended_dt = _now_utc(now_fn)

        # 3) post-check (only when runner reported success).
        post_reason: Optional[str] = None
        if rc == 0 and stage.post_check is not None and runner_reason is None:
            try:
                post_reason = stage.post_check()
            except Exception as e:  # noqa: BLE001
                post_reason = (
                    f"post_check_exception: {type(e).__name__}: {e}")

        if runner_reason is not None:
            stage_halt_reason: Optional[str] = runner_reason
        elif rc != 0:
            stage_halt_reason = f"non_zero_rc: rc={rc}"
        elif post_reason is not None:
            stage_halt_reason = f"post_check: {post_reason}"
        else:
            stage_halt_reason = None

        outcome = StageOutcome(
            key=stage.key, label=stage.label,
            rc=rc,
            started_at=stage_started_at,
            ended_at=_iso(stage_ended_dt),
            duration_sec=(stage_ended_dt - stage_started_dt).total_seconds(),
            halt_reason=stage_halt_reason,
        )
        completed.append(outcome)

        # Marker after every transition.
        write_oneclick_marker(
            marker_path,
            run_id=run_id,
            started_at=started_at,
            last_updated=outcome.ended_at,
            stages_planned=planned_keys,
            stages_completed=completed,
            current_stage=(
                stages[idx + 1].key if (idx + 1 < len(stages)
                                        and stage_halt_reason is None)
                else None
            ),
            halt_reason=stage_halt_reason if stage_halt_reason else None,
        )

        if on_stage_end is not None:
            _safe_callback(on_stage_end, outcome)

        if stage_halt_reason is not None:
            halt_reason = stage_halt_reason
            overall_rc = rc if rc != 0 else 1
            if on_halt is not None:
                _safe_callback(on_halt, outcome)
            break

    # Record skipped stages (if any).
    last_completed_idx = len(completed) - 1
    if halt_reason is not None and last_completed_idx + 1 < len(stages):
        for stage in stages[last_completed_idx + 1:]:
            completed.append(StageOutcome(
                key=stage.key, label=stage.label,
                rc=-1,
                started_at="", ended_at="",
                duration_sec=0.0,
                halt_reason="skipped_due_to_prior_halt",
                skipped=True,
            ))

    ended_dt = _now_utc(now_fn)
    ended_at = _iso(ended_dt)

    # Final marker.
    write_oneclick_marker(
        marker_path,
        run_id=run_id,
        started_at=started_at,
        last_updated=ended_at,
        stages_planned=planned_keys,
        stages_completed=completed,
        current_stage=None,
        halt_reason=halt_reason,
    )

    return OneClickResult(
        run_id=run_id,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=(ended_dt - started_dt).total_seconds(),
        stages=tuple(completed),
        halt_reason=halt_reason,
        overall_rc=overall_rc,
    )


def _safe_callback(fn: Callable[[StageOutcome], None],
                   outcome: StageOutcome) -> None:
    """UI callbacks must never bring down the coordinator. Swallow
    exceptions with a warning so a misbehaving log line in the panel
    doesn't strand a paper submit halfway through."""
    try:
        fn(outcome)
    except Exception as e:  # noqa: BLE001
        _LOG.warning(
            "oneclick UI callback raised for stage=%s: %s",
            outcome.key, e)
