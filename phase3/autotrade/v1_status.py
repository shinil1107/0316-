"""V1-F.D — Progress snapshot for unattended launchd fires.

Problem
-------
The control panel and the launchd-fired ``v1_runner`` live in
different Mach sessions (panel = Aqua user session; launchd job =
gui/$UID daemon). The panel can't pipe the subprocess stdout the
way it does for the manual "Full Paper Run" button — by the time
the operator opens the panel after the 22:35 fire, the launchd
process has long since written to its own ``v1_launchd.out.log``.

This module is the bridge:

* ``Writer`` is what ``v1_runner`` writes to. One status.json per
  fire, atomically rewritten at every stage boundary.
* ``read_status`` is what the panel reads at every refresh — and
  what tests read to assert progress was reported as expected.

Atomic-write semantics
----------------------
Every snapshot is written to a temp file alongside ``status.json``
and ``os.replace``-d into place. Readers therefore either see the
prior snapshot or the new one, never a half-formed JSON. A crash
mid-write leaves the prior file intact. The temp suffix is the
writer's PID so concurrent fires (panel test-fire while launchd
is running) don't stomp each other's temp file.

What's stored
-------------
The schema is small on purpose — the panel renders a one-line
summary plus an optional two-line in-progress indicator, so we
only need:

* ``fire_label``         "trade" / "t7_prefetch" / "run" / "test-fire"
* ``started_at_utc``     ISO timestamp the run began
* ``current_stage``      name of the stage in flight (or "" if done)
* ``stages_done``        ordered list of completed stages with rc
* ``run_id``             T7 run_id once it is known
* ``finished_at_utc``    set when the run terminates (success OR halt)
* ``final_rc``           set alongside finished_at_utc
* ``halt_reason``        human-readable reason if final_rc != 0
* ``extra``              free-form dict for stage-local diagnostics
                         (e.g. recommendations_count after t7_generate)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_HERE = Path(__file__).resolve().parent
DEFAULT_STATUS_PATH = _HERE / "runtime" / "v1_status.json"


def _stamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class StageRecord:
    key: str
    rc: int
    started_at_utc: str
    completed_at_utc: str = ""
    halt_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "key": self.key,
            "rc": int(self.rc),
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
        }
        if self.halt_reason:
            d["halt_reason"] = self.halt_reason
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


@dataclass
class StatusSnapshot:
    """Panel-facing view of one V1 fire."""
    fire_label: str = ""
    started_at_utc: str = ""
    current_stage: str = ""
    stages_done: List[Dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    finished_at_utc: str = ""
    final_rc: Optional[int] = None
    halt_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    # V1-F.2: pointer to the launchd StandardOutPath log file so the
    # panel can tail it. ``log_start_offset`` is the byte position of
    # the log at the moment this fire began; subtracting it from
    # ``os.path.getsize(log_path)`` gives only THIS fire's bytes
    # (launchd appends to the same path across fires).
    log_path: str = ""
    log_start_offset: int = 0

    # Helpers the panel uses; pure on the dataclass state so the
    # render code doesn't have to re-compute these from raw fields.

    @property
    def in_progress(self) -> bool:
        """True iff the run started but has not written a finish
        record yet. The panel uses this to decide between rendering
        "Last fire: ..." and "In progress: ..."."""
        return bool(self.started_at_utc) and not self.finished_at_utc

    @property
    def stages_summary(self) -> str:
        """Short label like ``3 stages (rc=0)`` for the panel row."""
        if not self.stages_done:
            return "no stages"
        rcs = sorted({int(s.get("rc", 0)) for s in self.stages_done})
        if rcs == [0]:
            rc_label = "rc=0"
        else:
            rc_label = "rc=" + ",".join(str(r) for r in rcs)
        return f"{len(self.stages_done)} stages ({rc_label})"


class Writer:
    """Streaming status writer used by ``v1_runner``.

    Usage::

        sp = Writer()
        sp.start(fire_label="trade", started_at_utc="...")
        sp.set_stage("env_gates")
        ...
        sp.complete_stage("env_gates", rc=0)
        ...
        sp.finish(final_rc=0, ended_at_utc="...")

    Every public method ends with an atomic ``_flush()`` so the
    panel sees the freshest state available. Failures are silently
    swallowed (logged via ``print`` to stderr) — status reporting
    MUST NEVER take down a live trade fire.
    """

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_STATUS_PATH
        self._snap = StatusSnapshot()
        self._stage_starts: Dict[str, str] = {}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ── public API ───────────────────────────────────────────────
    def start(self, *, fire_label: str,
              started_at_utc: Optional[str] = None,
              log_path: Optional[Path] = None) -> None:
        snap = StatusSnapshot(
            fire_label=fire_label,
            started_at_utc=started_at_utc or _stamp(),
        )
        # V1-F.2 panel tail: record where THIS fire's bytes begin in
        # the launchd StandardOutPath so the panel can slice them out
        # cleanly even though launchd appends across fires.
        if log_path is not None:
            try:
                p = Path(log_path)
                snap.log_path = str(p)
                snap.log_start_offset = (p.stat().st_size
                                          if p.exists() else 0)
            except OSError:
                snap.log_path = str(log_path)
                snap.log_start_offset = 0
        self._snap = snap
        self._stage_starts.clear()
        self._flush()

    def set_stage(self, stage_key: str) -> None:
        self._snap.current_stage = stage_key
        self._stage_starts[stage_key] = _stamp()
        self._flush()

    def complete_stage(self, stage_key: str, *, rc: int,
                        halt_reason: Optional[str] = None,
                        **extra: Any) -> None:
        rec = StageRecord(
            key=stage_key, rc=int(rc),
            started_at_utc=self._stage_starts.get(stage_key, _stamp()),
            completed_at_utc=_stamp(),
            halt_reason=halt_reason,
            extra={k: v for k, v in extra.items() if v is not None},
        )
        self._snap.stages_done.append(rec.to_dict())
        # Promote run_id if the stage carried one — the panel
        # surfaces this prominently so the operator doesn't have to
        # spelunk daily_runs/.
        if "run_id" in extra and extra["run_id"]:
            self._snap.run_id = str(extra["run_id"])
        if self._snap.current_stage == stage_key:
            self._snap.current_stage = ""
        self._flush()

    def finish(self, *, final_rc: int,
                halt_reason: Optional[str] = None,
                ended_at_utc: Optional[str] = None) -> None:
        self._snap.final_rc = int(final_rc)
        self._snap.halt_reason = halt_reason
        self._snap.finished_at_utc = ended_at_utc or _stamp()
        self._snap.current_stage = ""
        self._flush()

    # ── internals ────────────────────────────────────────────────
    def _to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "fire_label": self._snap.fire_label,
            "started_at_utc": self._snap.started_at_utc,
            "current_stage": self._snap.current_stage,
            "stages_done": list(self._snap.stages_done),
            "run_id": self._snap.run_id,
            "finished_at_utc": self._snap.finished_at_utc,
            "final_rc": self._snap.final_rc,
        }
        if self._snap.halt_reason:
            d["halt_reason"] = self._snap.halt_reason
        if self._snap.extra:
            d["extra"] = dict(self._snap.extra)
        if self._snap.log_path:
            d["log_path"] = self._snap.log_path
            d["log_start_offset"] = int(self._snap.log_start_offset)
        return d

    def _flush(self) -> None:
        try:
            payload = json.dumps(self._to_dict(), ensure_ascii=False,
                                  indent=2)
            tmp_dir = self.path.parent
            # Use a NamedTemporaryFile in the SAME directory so the
            # ``os.replace`` is atomic on the same filesystem. PID
            # in the suffix prevents conflicts between concurrent
            # writers (panel test-fire vs launchd).
            fd, tmp_name = tempfile.mkstemp(
                prefix=".v1_status.",
                suffix=f".{os.getpid()}.tmp",
                dir=str(tmp_dir),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, self.path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001
            # Status reporting must never crash the pipeline.
            try:
                import sys
                print(f"[v1_status] flush failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
            except Exception:
                pass


def read_status(
    *, path: Optional[Path] = None,
) -> Optional[StatusSnapshot]:
    """Return the most recent ``StatusSnapshot`` or ``None`` if no
    file exists / is unreadable / is malformed.

    Malformed inputs are silently treated as "no status known yet"
    so a partially-written file (impossible under our atomic writer
    but defensible against external tools) never crashes the panel
    refresh loop.
    """
    p = Path(path) if path else DEFAULT_STATUS_PATH
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        obj = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return StatusSnapshot(
        fire_label=str(obj.get("fire_label", "") or ""),
        started_at_utc=str(obj.get("started_at_utc", "") or ""),
        current_stage=str(obj.get("current_stage", "") or ""),
        stages_done=list(obj.get("stages_done", []) or []),
        run_id=str(obj.get("run_id", "") or ""),
        finished_at_utc=str(obj.get("finished_at_utc", "") or ""),
        final_rc=(int(obj["final_rc"])
                  if isinstance(obj.get("final_rc"), (int, float))
                  else None),
        halt_reason=(obj.get("halt_reason")
                     if isinstance(obj.get("halt_reason"), str)
                     else None),
        extra=dict(obj.get("extra", {}) or {}),
        log_path=str(obj.get("log_path", "") or ""),
        log_start_offset=(int(obj["log_start_offset"])
                          if isinstance(obj.get("log_start_offset"),
                                         (int, float)) else 0),
    )


def render_panel_lines(
    snap: Optional[StatusSnapshot],
    *, now: Optional[datetime] = None,
) -> List[str]:
    """Produce the V1-E panel rows describing the last fire.

    Returns 1-2 strings. The panel uses these inside the existing
    V1-E LabelFrame so we don't bloat the layout further.

    * No status yet      → ``["Last fire: (none)"]``
    * Finished           → ``["Last fire: trade  22:36:01  rc=0  (5 stages)"]``
    * In progress        → ``["Last fire: trade  STARTED 22:35:01",
                              "In progress: paper_submit_and_apply (12s)"]``
    """
    if snap is None or not snap.started_at_utc:
        return ["Last fire: (none)"]
    started_hhmmss = snap.started_at_utc[11:19] or snap.started_at_utc
    if snap.in_progress:
        head = (f"Last fire: {snap.fire_label}  "
                f"STARTED {started_hhmmss}  "
                f"run_id={snap.run_id or '(pending)'}")
        # Estimate elapsed for in-progress
        cur = snap.current_stage or "(unknown stage)"
        elapsed = ""
        try:
            t0 = datetime.fromisoformat(snap.started_at_utc)
            t1 = now or datetime.now(tz=timezone.utc)
            secs = int((t1 - t0).total_seconds())
            if secs >= 0:
                m, s = divmod(secs, 60)
                elapsed = f" ({m}m {s:02d}s)" if m else f" ({s}s)"
        except (TypeError, ValueError):
            pass
        return [head, f"In progress: {cur}{elapsed}"]
    # Finished
    end_hhmmss = (snap.finished_at_utc[11:19]
                  if snap.finished_at_utc else "")
    rc_label = (f"rc={snap.final_rc}"
                if snap.final_rc is not None else "rc=?")
    head = (f"Last fire: {snap.fire_label}  "
            f"{end_hhmmss}  {rc_label}  ({snap.stages_summary})")
    return [head]
