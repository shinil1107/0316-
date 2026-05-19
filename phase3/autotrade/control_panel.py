"""R10 — autotrade operator dashboard.

Scope (R10 §2)
--------------
R9 shipped a minimal Tkinter button. R10 turns it into a small
operator dashboard with five sections:

```text
Run                — selected run_id, artifact status, intent count
Safety Gates       — KIS_ENV, SUBMIT/CANCEL/APPLY env vars, halt, journal
Actions            — 5 buttons, each with enabled/disabled reason
Command Preview    — exact shell command the next button will run
Output / Log       — daily_runner stdout/stderr tail
```

Almost all correctness work lives in pure helpers below — `compute_panel_state`,
`compute_button_gates`, `build_command_preview` — so the test suite can
drive the dashboard logic without any Tk loop. Tkinter is loaded
lazily inside `launch_panel()` so headless test runs never touch it.

Safety contract (R10 §1)
------------------------
1. R10 stays paper-only — the dashboard refuses to render a live-mode
   gate row, and the manage-loop refuses to enter when ``KIS_ENV != paper``.
2. Submit / T10-apply buttons are **never** enabled by default. Each
   has an explicit enablement predicate the Refresh button re-evaluates.
3. Submit and T10-apply require a confirmation checkbox AND the env
   gate. The checkbox state is intentionally cleared on every Refresh
   so an operator can't leave it ticked between sessions.
4. STOP is a flag, not a process kill. ``order_manager.manage_order``
   and ``daily_runner.run_daily`` both honour the flag at entry.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import global_halt  # noqa: E402
from phase3.autotrade import intents_io   # noqa: E402
from phase3.autotrade import t10_apply_journal as tj  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
DISABLED_TOOLTIP_FULL_RUN = (
    "Disabled in R10. Combined paper-submit + T10-apply is a single "
    "high-impact action; R10 only ships it as two separate buttons. "
    "Will be enabled in R11 after one clean R10 market-open acceptance."
)

DASHBOARD_TITLE = "Autotrade Control Panel — Paper Mode (R10)"

SUBMIT_GATE = "KIS_PAPER_SUBMIT_OK"
CANCEL_GATE = "KIS_PAPER_CANCEL_OK"
APPLY_GATE  = "AUTOTRADE_T10_APPLY_OK"
KIS_ENV_VAR = "KIS_ENV"


# ──────────────────────────────────────────────────────────────────────
# Pure helpers — the unit-testable surface of the dashboard
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunCandidate:
    """A daily_runs subdirectory that *might* be runnable."""
    run_id: str
    run_dir: Path
    status: str
    mtime: float


def _list_run_candidates(output_dir: Path) -> List[RunCandidate]:
    runs_dir = Path(output_dir) / "daily_runs"
    if not runs_dir.exists():
        return []
    cands: List[RunCandidate] = []
    for sub in sorted(runs_dir.iterdir()):
        meta_path = sub / "run_meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cands.append(RunCandidate(
            run_id=str(meta.get("run_id") or sub.name),
            run_dir=sub,
            status=str(meta.get("status", "")),
            mtime=meta_path.stat().st_mtime,
        ))
    cands.sort(key=lambda c: c.mtime, reverse=True)
    return cands


def _latest_awaiting_execution_run_id(output_dir: Path) -> Optional[str]:
    for c in _list_run_candidates(output_dir):
        if c.status == "awaiting_execution":
            return c.run_id
    return None


def _load_run_meta(output_dir: Path, run_id: str) -> Optional[dict]:
    p = Path(output_dir) / "daily_runs" / run_id / "run_meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class GateStatus:
    """Single env / runtime gate row in the Safety Gates section."""
    name: str
    value: str             # "true" / "false" / "paper" / "(unset)" / etc.
    ok: bool               # green/red dot
    note: str = ""


@dataclass(frozen=True)
class T10JournalStatus:
    has_open_started: bool
    has_recovery: bool
    open_started_batches: List[str] = field(default_factory=list)
    recovery_batches: List[str]    = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (self.has_open_started or self.has_recovery)


@dataclass(frozen=True)
class LastReport:
    """Most recent daily report on disk for the selected run."""
    md_path: Optional[Path]
    json_path: Optional[Path]
    rc: Optional[int]
    summary: str

    @property
    def exists(self) -> bool:
        return self.md_path is not None or self.json_path is not None


@dataclass(frozen=True)
class PanelState:
    """Everything the dashboard needs to render one frame. Constructed
    by ``compute_panel_state`` from explicit inputs so tests can drive
    every combination of states."""
    output_dir: Path
    run_id: str
    run_dir: Path
    artifact_status: str
    intents: intents_io.IntentFileStatus
    last_report: LastReport
    gates: List[GateStatus]
    halt: global_halt.HaltState
    t10_journal: T10JournalStatus
    # R10B — recommendations.csv visibility for the Generate Intent button.
    recommendations_csv_exists: bool = False
    recommendations_buy_count: int = 0
    buy_candidates: List[intents_io.BuyCandidate] = field(default_factory=list)

    @property
    def kis_env(self) -> str:
        for g in self.gates:
            if g.name == KIS_ENV_VAR:
                return g.value
        return "(unset)"

    @property
    def submit_gate_on(self) -> bool:
        for g in self.gates:
            if g.name == SUBMIT_GATE:
                return g.ok
        return False

    @property
    def cancel_gate_on(self) -> bool:
        for g in self.gates:
            if g.name == CANCEL_GATE:
                return g.ok
        return False

    @property
    def apply_gate_on(self) -> bool:
        for g in self.gates:
            if g.name == APPLY_GATE:
                return g.ok
        return False


def _compute_gate_rows(env: Dict[str, str]) -> List[GateStatus]:
    """Translate env-var snapshot into Safety Gates rows. We don't read
    `os.environ` directly here so tests can drive deterministic snapshots."""
    def _eq_true(name: str) -> bool:
        return env.get(name, "").strip().lower() == "true"

    kis_env_val = env.get(KIS_ENV_VAR, "").strip() or "(unset)"
    return [
        GateStatus(
            name=KIS_ENV_VAR, value=kis_env_val,
            ok=(kis_env_val == "paper"),
            note=("R10 is paper-only — anything else blocks submit."
                  if kis_env_val != "paper" else ""),
        ),
        GateStatus(
            name=SUBMIT_GATE,
            value="true" if _eq_true(SUBMIT_GATE) else "(unset/false)",
            ok=_eq_true(SUBMIT_GATE),
            note=("Required for --paper-submit." if not _eq_true(SUBMIT_GATE) else ""),
        ),
        GateStatus(
            name=CANCEL_GATE,
            value="true" if _eq_true(CANCEL_GATE) else "(unset/false)",
            ok=_eq_true(CANCEL_GATE),
            note=("Required for cancel/reprice path."
                  if not _eq_true(CANCEL_GATE) else ""),
        ),
        GateStatus(
            name=APPLY_GATE,
            value="true" if _eq_true(APPLY_GATE) else "(unset/false)",
            ok=_eq_true(APPLY_GATE),
            note=("Required for T10 real apply." if not _eq_true(APPLY_GATE) else ""),
        ),
    ]


def _scan_t10_journal(run_dir: Path) -> T10JournalStatus:
    rows = tj.read_journal(run_dir)
    started = {r.get("batch_id") for r in rows if r.get("status") == "started"}
    applied = {r.get("batch_id") for r in rows if r.get("status") == "applied"}
    recovery = [r for r in rows if r.get("status") == "recovery"]
    open_started = sorted(b for b in (started - applied) if b)
    return T10JournalStatus(
        has_open_started=bool(open_started),
        has_recovery=bool(recovery),
        open_started_batches=open_started,
        recovery_batches=[r.get("batch_id", "") for r in recovery],
    )


def _scan_last_report(run_dir: Path) -> LastReport:
    md = run_dir / "autotrade_daily_report.md"
    js = run_dir / "autotrade_daily_report.json"
    if not md.exists() and not js.exists():
        return LastReport(md_path=None, json_path=None, rc=None, summary="(no report yet)")
    rc: Optional[int] = None
    summary = "(no rc)"
    if js.exists():
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
            rc = int(data.get("rc", -1))
            hard_stop = data.get("hard_stop")
            if hard_stop:
                where = (hard_stop or {}).get("where", "")
                reason = (hard_stop or {}).get("reason", "")
                summary = f"rc={rc} hard_stop@{where}: {reason}"[:160]
            else:
                summary = f"rc={rc} OK"
        except (OSError, json.JSONDecodeError) as e:
            summary = f"(report unreadable: {e})"
    return LastReport(
        md_path=md if md.exists() else None,
        json_path=js if js.exists() else None,
        rc=rc, summary=summary,
    )


def compute_panel_state(
    *,
    output_dir: Path,
    run_id: str,
    env: Optional[Dict[str, str]] = None,
    halt_path: Optional[Path] = None,
) -> PanelState:
    """Snapshot everything the dashboard renders for a single run_id.

    Inputs are explicit (env dict, halt path) so tests can construct
    deterministic snapshots without touching ``os.environ`` or the real
    halt file. Production callers pass ``env=os.environ`` and
    ``halt_path=None`` (resolves to default)."""
    output_dir = Path(output_dir)
    run_dir = output_dir / "daily_runs" / run_id if run_id else output_dir
    meta = _load_run_meta(output_dir, run_id) if run_id else None
    artifact_status = (
        str(meta.get("status", ""))
        if meta else ("(no run_id selected)" if not run_id else "(run_meta.json missing)")
    )
    intents = (intents_io.validate_submitted_intents(run_dir) if run_id
               else intents_io.IntentFileStatus(state="missing",
                                                 reason="no run_id selected",
                                                 path=""))
    last_report = (_scan_last_report(run_dir) if run_id
                    else LastReport(md_path=None, json_path=None, rc=None,
                                     summary="(select a run_id first)"))
    gates = _compute_gate_rows(dict(env or {}))
    halt = global_halt.read_halt(halt_path)
    t10 = (_scan_t10_journal(run_dir) if run_id
           else T10JournalStatus(has_open_started=False, has_recovery=False))
    # R10B — load BUY candidates from recommendations.csv if present.
    rec_csv_exists = (run_id
                       and intents_io.recommendations_csv_path(run_dir).exists())
    buy_candidates = (intents_io.load_buy_candidates(run_dir)
                       if run_id and rec_csv_exists else [])
    return PanelState(
        output_dir=output_dir,
        run_id=run_id, run_dir=run_dir,
        artifact_status=artifact_status,
        intents=intents,
        last_report=last_report,
        gates=gates,
        halt=halt,
        t10_journal=t10,
        recommendations_csv_exists=bool(rec_csv_exists),
        recommendations_buy_count=len(buy_candidates),
        buy_candidates=buy_candidates,
    )


# ──────────────────────────────────────────────────────────────────────
# Button enablement matrix (R10 §2.2)
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ButtonGate:
    button_id: str        # one of: dry_run / paper_submit / t10_dry / t10_apply / full_paper_run / stop / clear_halt
    enabled: bool
    reason: str = ""      # human-readable, used for tooltip + disabled label


def compute_button_gates(
    state: PanelState,
    *,
    dry_run_rc_clean: bool = False,
    submit_outcome_clean: bool = False,
    confirm_submit_checked: bool = False,
    confirm_apply_checked:  bool = False,
    overwrite_intents_checked: bool = False,
) -> Dict[str, ButtonGate]:
    """Translate a PanelState + session flags into per-button gates.

    Session flags (UI-only state, not on disk):

      dry_run_rc_clean       — did the most-recent UI dry-run return rc=0?
      submit_outcome_clean   — did the most-recent UI paper-submit
                                produce a report with no UNKNOWN /
                                stale / cancel-unconfirmed outcomes?
      confirm_submit_checked — operator ticked "I authorize submit"
      confirm_apply_checked  — operator ticked "I authorize T10 apply"

    The matrix mirrors R10 §2.2 line for line so the table in the doc
    is the single source of truth."""

    out: Dict[str, ButtonGate] = {}

    has_run_id = bool(state.run_id)

    # 1. Dry Run — enabled as soon as a run_id is picked.
    if has_run_id:
        out["dry_run"] = ButtonGate("dry_run", enabled=True)
    else:
        out["dry_run"] = ButtonGate(
            "dry_run", enabled=False,
            reason="pick a run_id (or press 'Use latest awaiting_execution')",
        )

    # STOP and Clear-halt are always usable.
    out["stop"] = ButtonGate("stop", enabled=True)
    out["clear_halt"] = ButtonGate("clear_halt", enabled=True)

    # 0. Generate Intent File — R10B §3.1. Default-disabled until the
    # operator has selected an `awaiting_execution` artifact whose
    # `recommendations.csv` is present. If `submitted_intents.json`
    # already exists, the operator must tick the overwrite checkbox
    # FIRST — otherwise we silently clobber a hand-authored file.
    gen_reasons: List[str] = []
    if not has_run_id:
        gen_reasons.append("no run_id selected")
    if state.artifact_status != "awaiting_execution":
        gen_reasons.append(
            f"artifact status is {state.artifact_status!r}, "
            f"need 'awaiting_execution'"
        )
    if not state.recommendations_csv_exists:
        gen_reasons.append("recommendations.csv missing for this run")
    elif state.recommendations_buy_count <= 0:
        gen_reasons.append("no BUY candidates in recommendations.csv")
    if state.intents.is_ok and not overwrite_intents_checked:
        gen_reasons.append(
            f"submitted_intents.json already exists "
            f"({state.intents.buy_count} BUY) — tick "
            f"'allow overwrite' to replace it"
        )
    if state.halt.halted:
        gen_reasons.append("global_halt is ON")
    out["generate_intent"] = ButtonGate(
        "generate_intent",
        enabled=not gen_reasons,
        reason="; ".join(gen_reasons),
    )
    # R10C — "Generate ALL Intents" shares the same enablement gates as
    # the single-shot generator. It is intentionally allowed even when
    # the dropdown has no selection, because batch generation does not
    # depend on the highlighted candidate.
    out["generate_all"] = ButtonGate(
        "generate_all",
        enabled=not gen_reasons,
        reason="; ".join(gen_reasons),
    )

    # 2. Paper Submit — many preconditions, all checked here.
    submit_reasons: List[str] = []
    if not has_run_id:
        submit_reasons.append("no run_id selected")
    if state.artifact_status != "awaiting_execution":
        submit_reasons.append(
            f"artifact status is {state.artifact_status!r}, need 'awaiting_execution'"
        )
    if not state.intents.is_ok:
        submit_reasons.append(f"submitted_intents.json: {state.intents.state}")
    elif state.intents.buy_count <= 0:
        submit_reasons.append("submitted_intents.json has 0 BUY rows")
    if state.kis_env != "paper":
        submit_reasons.append(f"KIS_ENV={state.kis_env!r} (need 'paper')")
    if not state.submit_gate_on:
        submit_reasons.append(f"{SUBMIT_GATE} not set to true")
    if not state.cancel_gate_on:
        submit_reasons.append(f"{CANCEL_GATE} not set to true")
    if state.halt.halted:
        submit_reasons.append("global_halt is ON")
    if not dry_run_rc_clean:
        submit_reasons.append("run a clean dry-run in this session first")
    if not confirm_submit_checked:
        submit_reasons.append("tick the 'authorize paper submit' checkbox")
    out["paper_submit"] = ButtonGate(
        "paper_submit",
        enabled=not submit_reasons,
        reason="; ".join(submit_reasons),
    )

    # 3. T10 Apply Dry Run — needs a clean paper-submit outcome on disk.
    t10dry_reasons: List[str] = []
    if not has_run_id:
        t10dry_reasons.append("no run_id selected")
    if not submit_outcome_clean:
        t10dry_reasons.append(
            "no clean paper-submit outcome yet (FILLED only, no UNKNOWN / open / cancel-unconfirmed)"
        )
    if state.halt.halted:
        t10dry_reasons.append("global_halt is ON")
    if not state.t10_journal.is_clean:
        t10dry_reasons.append("T10 journal has unresolved started/recovery marker")
    out["t10_dry"] = ButtonGate(
        "t10_dry", enabled=not t10dry_reasons,
        reason="; ".join(t10dry_reasons),
    )

    # 4. T10 Real Apply — strictest gate.
    t10apply_reasons: List[str] = []
    if not has_run_id:
        t10apply_reasons.append("no run_id selected")
    if not submit_outcome_clean:
        t10apply_reasons.append("paper-submit outcome must be clean (FILLED only)")
    if not state.apply_gate_on:
        t10apply_reasons.append(f"{APPLY_GATE} not set to true")
    if not state.t10_journal.is_clean:
        t10apply_reasons.append("T10 journal has unresolved started/recovery marker")
    if state.halt.halted:
        t10apply_reasons.append("global_halt is ON")
    if not confirm_apply_checked:
        t10apply_reasons.append("tick the 'authorize T10 apply' checkbox")
    out["t10_apply"] = ButtonGate(
        "t10_apply", enabled=not t10apply_reasons,
        reason="; ".join(t10apply_reasons),
    )

    # 5. Full Paper Run — R10 keeps disabled by design (§2.2).
    out["full_paper_run"] = ButtonGate(
        "full_paper_run", enabled=False,
        reason=DISABLED_TOOLTIP_FULL_RUN,
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Command preview (R10 §2.1 / §2.3)
# ──────────────────────────────────────────────────────────────────────
def _shell_quote(s: str) -> str:
    """Minimal POSIX-style quoter for the preview area. We don't shell
    out using this string — it's display-only — so quoting just needs
    to make the preview unambiguous to the operator."""
    if not s:
        return "''"
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_./="
    if all(ch in safe for ch in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def build_panel_snapshot_text(
    ps: "PanelState",
    *,
    run_id: str,
    output_tail: str = "",
    last_argv: Optional[List[str]] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Render the current panel state + last subprocess output as a
    plain-text block the operator can paste into chat / a bug report.

    Pure helper (no Tk, no os.environ) so the test suite can lock the
    format down. Anything that could leak credentials (e.g. raw env-var
    values that aren't already exposed in ``ps.gates``) is intentionally
    omitted — only the booleans/labels already shown in the dashboard
    are included.
    """
    ts = generated_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    lines: List[str] = []
    lines.append("=== Autotrade Control Panel snapshot ===")
    lines.append(f"generated_at : {ts}")
    lines.append(f"run_id       : {run_id or '(empty)'}")
    lines.append(f"run_dir      : {ps.run_dir}")
    lines.append(f"artifact     : {ps.artifact_status}")
    intents_line = (
        f"state={ps.intents.state}"
        f"  intents={ps.intents.intent_count}"
        f"  buys={ps.intents.buy_count}"
    )
    if ps.intents.reason:
        intents_line += f"  ({ps.intents.reason})"
    lines.append(f"intents      : {intents_line}")
    if ps.last_report.exists:
        rep = ps.last_report.md_path or ps.last_report.json_path
        lines.append(f"last_report  : {rep}")
        lines.append(f"last_rc      : {ps.last_report.summary}")
    else:
        lines.append("last_report  : —")
        lines.append("last_rc      : —")
    lines.append("")
    lines.append("--- Safety Gates ---")
    for g in ps.gates:
        flag = "OK" if g.ok else "BLOCK"
        note = f"  ({g.note})" if g.note else ""
        lines.append(f"  {g.name:<24} = {g.value:<14} [{flag}]{note}")
    lines.append(
        f"  halt                     halted={ps.halt.halted}  "
        f"reason={ps.halt.reason or '(none)'}"
    )
    if ps.t10_journal.is_clean:
        lines.append("  t10_journal              clean")
    else:
        lines.append(
            f"  t10_journal              open_started="
            f"{ps.t10_journal.open_started_batches}  "
            f"recovery={ps.t10_journal.recovery_batches}"
        )
    lines.append("")
    lines.append("--- Intent Preparation ---")
    lines.append(
        f"  recommendations.csv     "
        f"{'exists' if ps.recommendations_csv_exists else 'missing'}"
        f"  buy_candidates={ps.recommendations_buy_count}"
    )
    if last_argv:
        lines.append("")
        lines.append("--- Last command ---")
        lines.append("  $ " + " ".join(last_argv))
    if output_tail:
        lines.append("")
        lines.append("--- Output / Log tail ---")
        lines.append(output_tail.rstrip())
    return "\n".join(lines) + "\n"


class DangerActionDenied(RuntimeError):
    """Raised by ``revalidate_danger_action`` when a paper-submit or
    T10-apply callback fires while the button matrix says the action
    is disabled. The Tk callback catches this, shows the reason in a
    messagebox, and exits without spawning a subprocess.

    This is defence-in-depth on top of the visible button state — it
    closes the small window where (a) the operator clicks faster
    than ``_refresh`` runs, (b) a programmatic Tk event fires the
    callback directly, or (c) a future refactor accidentally removes
    the Tk-level disabled state. The Arm toggles MUST already have
    set the corresponding env vars on ``os.environ`` before this
    function is reached; we do not synthesise gate values here.
    """
    def __init__(self, action: str, reason: str):
        super().__init__(f"{action} denied: {reason}")
        self.action = action
        self.reason = reason


def revalidate_danger_action(
    *,
    action: str,
    output_dir: Optional[Path],
    run_id: str,
    env: Dict[str, str],
    confirm_submit_checked: bool = False,
    confirm_apply_checked: bool = False,
    overwrite_intents_checked: bool = False,
    dry_run_rc_clean: bool = False,
    submit_outcome_clean: bool = False,
) -> "PanelState":
    """Recompute panel state + button gates and refuse if the action
    is currently disabled. Returns the freshly computed PanelState so
    callers can reuse it for logging / preview.

    ``action`` is one of: 'paper_submit', 't10_apply', 'full_paper_run'
    (R11). Other actions are not danger-gated and use the gate matrix
    only for UX hints, not safety enforcement, so this helper refuses
    to be called with them — pass through directly.
    """
    DANGER_ACTIONS = {"paper_submit", "t10_apply", "full_paper_run"}
    if action not in DANGER_ACTIONS:
        raise ValueError(
            f"revalidate_danger_action only applies to danger actions "
            f"{sorted(DANGER_ACTIONS)}; got {action!r}"
        )
    if output_dir is None:
        raise DangerActionDenied(action, "phase3 paper config not loadable")
    if not run_id or not run_id.strip():
        raise DangerActionDenied(action, "no run_id selected")
    ps = compute_panel_state(
        output_dir=Path(output_dir), run_id=run_id, env=env,
    )
    gates = compute_button_gates(
        ps,
        dry_run_rc_clean=dry_run_rc_clean,
        submit_outcome_clean=submit_outcome_clean,
        confirm_submit_checked=confirm_submit_checked,
        confirm_apply_checked=confirm_apply_checked,
        overwrite_intents_checked=overwrite_intents_checked,
    )
    if action not in gates:
        raise DangerActionDenied(
            action, f"no gate entry for {action!r} in button matrix")
    g = gates[action]
    if not g.enabled:
        raise DangerActionDenied(action, g.reason or "gate disabled")
    return ps


def build_command_preview(
    button_id: str, *, run_id: str, profile: str = "paper",
) -> str:
    """Return the exact shell command the panel will run for the given
    button. Used by both the Tk preview area and the R10 test that
    locks the contract.

    The preview NEVER includes the actual secret values — gate env
    vars are shown as ``=true`` placeholders so the operator can see
    what *will* be set, without leaking anything from os.environ."""
    cwd_hint = f"cd {_shell_quote(str(_REPO_ROOT))} && "
    base_cmd = (
        f"PYTHONPATH=. {_shell_quote(sys.executable)} -m "
        f"phase3.autotrade.daily_runner --profile {_shell_quote(profile)} "
        f"--run-id {_shell_quote(run_id)}"
    )
    t10_base = (
        f"PYTHONPATH=. {_shell_quote(sys.executable)} -m "
        f"phase3.autotrade.t10_applicator --profile {_shell_quote(profile)} "
        f"--run-id {_shell_quote(run_id)}"
    )

    if button_id == "dry_run":
        return f"{cwd_hint}{base_cmd} --dry-run"
    if button_id == "paper_submit":
        return (f"{cwd_hint}{SUBMIT_GATE}=true {CANCEL_GATE}=true "
                f"{base_cmd} --paper-submit")
    if button_id == "t10_dry":
        return f"{cwd_hint}{t10_base}"
    if button_id == "t10_apply":
        return f"{cwd_hint}{APPLY_GATE}=true {t10_base} --apply"
    if button_id == "full_paper_run":
        return (f"{cwd_hint}{SUBMIT_GATE}=true {CANCEL_GATE}=true "
                f"{APPLY_GATE}=true {base_cmd} --paper-submit --apply-t10")
    if button_id == "stop":
        return "(writes phase3/autotrade/runtime/global_halt.json halt=true)"
    if button_id == "clear_halt":
        return "(writes phase3/autotrade/runtime/global_halt.json halt=false)"
    if button_id == "generate_intent":
        # R10B — pure in-process file write. No broker call, no subprocess.
        return (
            "(writes daily_runs/<run_id>/submitted_intents.json from selected "
            "BUY candidate; no broker call)"
        )
    raise ValueError(f"unknown button_id: {button_id!r}")


# ──────────────────────────────────────────────────────────────────────
# R10-ARM — In-UI gate activation
# ──────────────────────────────────────────────────────────────────────
# Why this is safe vs the original shell-export workflow:
#
#   * The R10 safety contract is "the operator must express explicit
#     intent each time a danger action is enabled". The original design
#     achieved that with a shell ``export``. The same contract is
#     preserved here by requiring an in-UI checkbox + a confirmation
#     dialog before the env var is set.
#   * The env var is written into the CURRENT process's ``os.environ``
#     ONLY. There is no on-disk persistence, no .env mutation, and no
#     exec into a parent shell. When the UI process exits, the value
#     is gone. Closing the UI = disarming.
#   * The four-layer guard is unchanged: KIS_ENV=paper, this Arm gate,
#     the "I authorize" checkbox, and the buttoned action itself.
#   * compute_button_gates() reads the same env mapping that
#     daily_runner subprocesses inherit, so an armed UI directly maps
#     to the same env that the subprocess sees on the next click.

ARM_PAPER_GATE_VARS: Tuple[str, ...] = (SUBMIT_GATE, CANCEL_GATE)
ARM_T10_GATE_VARS:   Tuple[str, ...] = (APPLY_GATE,)


def arm_gate_vars(env: Dict[str, str], gate_vars: Tuple[str, ...]) -> Dict[str, str]:
    """Return a copy of ``env`` with every var in ``gate_vars`` set to
    ``"true"``. Pure / testable; the UI callback applies the returned
    map back onto ``os.environ``."""
    out = dict(env)
    for var in gate_vars:
        out[var] = "true"
    return out


def disarm_gate_vars(env: Dict[str, str], gate_vars: Tuple[str, ...]) -> Dict[str, str]:
    """Return a copy of ``env`` with every var in ``gate_vars`` cleared.
    Mirrors :func:`arm_gate_vars` and is what the UI calls when the
    operator unticks an Arm checkbox."""
    out = dict(env)
    for var in gate_vars:
        out.pop(var, None)
    return out


def gate_is_armed(env: Dict[str, str], gate_vars: Tuple[str, ...]) -> bool:
    """True iff every var in ``gate_vars`` is currently set to ``"true"``
    in ``env``. Used to derive the initial checkbox state on UI start so
    a pre-existing shell export still reflects in the toggle."""
    return all(env.get(v, "").strip().lower() == "true" for v in gate_vars)


# ──────────────────────────────────────────────────────────────────────
# R10B-fix — Intent Preparation reset logic
# ──────────────────────────────────────────────────────────────────────
def intent_candidate_signature(
    candidates: List["intents_io.BuyCandidate"],
) -> Tuple[Tuple[int, str], ...]:
    """A stable, hashable identity for a list of BUY candidates that
    only changes when the row set itself changes (ticker / rec_row_id).
    Used by ``intent_prep_should_reset`` so the UI can detect "the
    operator switched to a different run_id" or "this run's
    recommendations.csv changed under us"."""
    return tuple((c.rec_row_id, c.ticker) for c in candidates)


def intent_prep_should_reset(
    *,
    prev_run_id: Optional[str],
    prev_signature: Optional[Tuple[Tuple[int, str], ...]],
    new_run_id: str,
    new_candidates: List["intents_io.BuyCandidate"],
) -> bool:
    """Return True iff the Intent Preparation widgets should be reset
    to defaults (selection cleared, qty=1, limit price blanked,
    overwrite checkbox cleared).

    Trigger cases (R10B follow-up bug):
      - First refresh after launch_panel()        (prev_run_id is None)
      - Operator changed run_id                   (different run_id)
      - recommendations.csv changed for the same run_id (different
        signature) — e.g. the upstream pipeline re-emitted the
        artifact with a different candidate set

    Not a trigger:
      - Plain Refresh on the same run_id with the same candidates
        (the operator may have typed a custom limit price; we must
        not clobber it).
    """
    if prev_run_id is None:
        return True
    if prev_run_id != new_run_id:
        return True
    new_sig = intent_candidate_signature(new_candidates)
    if prev_signature != new_sig:
        return True
    return False


def _build_dry_run_argv(run_id: str, *, profile: str = "paper") -> List[str]:
    if not run_id or not run_id.strip():
        raise ValueError("run_id is required for dry-run preflight")
    return [
        sys.executable, "-m", "phase3.autotrade.daily_runner",
        "--profile", profile, "--run-id", run_id, "--dry-run",
    ]


def _build_paper_submit_argv(run_id: str, *, profile: str = "paper") -> List[str]:
    if not run_id or not run_id.strip():
        raise ValueError("run_id is required for paper-submit")
    return [
        sys.executable, "-m", "phase3.autotrade.daily_runner",
        "--profile", profile, "--run-id", run_id, "--paper-submit",
    ]


def _build_t10_argv(run_id: str, *, apply_mode: bool,
                     profile: str = "paper") -> List[str]:
    if not run_id or not run_id.strip():
        raise ValueError("run_id is required for t10 apply")
    argv = [
        sys.executable, "-m", "phase3.autotrade.t10_applicator",
        "--profile", profile, "--run-id", run_id,
    ]
    if apply_mode:
        argv.append("--apply")
    return argv


# ──────────────────────────────────────────────────────────────────────
# Halt + subprocess helpers (R9 retained)
# ──────────────────────────────────────────────────────────────────────
def _write_halt_flag(reason: str = "operator_pressed_stop",
                      *, path: Optional[Path] = None) -> Path:
    return global_halt.write_halt(
        halt=True, reason=reason, operator="control_panel", path=path,
    )


def _clear_halt_flag(*, path: Optional[Path] = None) -> Path:
    return global_halt.clear_halt(operator="control_panel", path=path)


@dataclass(frozen=True)
class DryRunResult:
    rc: int
    stdout: str
    stderr: str
    argv: List[str]


def run_dry_run(run_id: str, *, profile: str = "paper",
                 cwd: Optional[Path] = None,
                 timeout_sec: float = 120.0,
                 subprocess_run: Callable[..., Any] = subprocess.run) -> DryRunResult:
    argv = _build_dry_run_argv(run_id, profile=profile)
    proc = subprocess_run(
        argv, capture_output=True, text=True,
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        timeout=timeout_sec,
    )
    return DryRunResult(
        rc=int(proc.returncode),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        argv=list(argv),
    )


def run_paper_submit(run_id: str, *, profile: str = "paper",
                      cwd: Optional[Path] = None,
                      env: Optional[Dict[str, str]] = None,
                      timeout_sec: float = 600.0,
                      subprocess_run: Callable[..., Any] = subprocess.run) -> DryRunResult:
    """Subprocess wrapper for --paper-submit. ``env`` MUST include the
    submit/cancel gate values; the caller is responsible for that —
    the panel sets them up just before calling this, but the CLI test
    can also drive it with a hermetic env dict."""
    argv = _build_paper_submit_argv(run_id, profile=profile)
    proc = subprocess_run(
        argv, capture_output=True, text=True,
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        env=dict(env) if env is not None else None,
        timeout=timeout_sec,
    )
    return DryRunResult(
        rc=int(proc.returncode),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        argv=list(argv),
    )


def run_t10(run_id: str, *, apply_mode: bool, profile: str = "paper",
             cwd: Optional[Path] = None,
             env: Optional[Dict[str, str]] = None,
             timeout_sec: float = 600.0,
             subprocess_run: Callable[..., Any] = subprocess.run) -> DryRunResult:
    argv = _build_t10_argv(run_id, apply_mode=apply_mode, profile=profile)
    proc = subprocess_run(
        argv, capture_output=True, text=True,
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        env=dict(env) if env is not None else None,
        timeout=timeout_sec,
    )
    return DryRunResult(
        rc=int(proc.returncode),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        argv=list(argv),
    )


# ──────────────────────────────────────────────────────────────────────
# R10C — Streaming subprocess runner
# ──────────────────────────────────────────────────────────────────────
# The UI used to call ``subprocess.run`` which blocks the Tk main loop
# for the entire daily_runner invocation (up to minutes). That made the
# panel look frozen and gave the operator zero visibility into progress
# — the exact symptom that prompted R10C. ``run_subprocess_streaming``
# is the line-by-line replacement: it spawns the child immediately,
# pumps stdout / stderr from two background threads, and hands every
# line to ``on_line`` so the UI can append it to its log widget in
# real time. ``on_done(rc)`` fires once both pipes have drained and
# the process has exited.
#
# Pure helper (no Tk references). The Tk side just wraps every callback
# with ``root.after(0, …)`` so the actual widget mutation always lands
# on the main thread.

def run_subprocess_streaming(
    argv: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    on_line: Callable[[str, str], None],
    on_done: Callable[[int], None],
    popen: Callable[..., Any] = subprocess.Popen,
) -> Any:
    """Spawn ``argv`` and stream its stdout/stderr lines back via
    callbacks. Returns the ``Popen`` immediately so the caller can keep
    a handle (e.g. to surface PID, or to terminate on a STOP button).

    ``on_line(stream, line)`` is called once per logical output line
    (newline stripped). ``stream`` is one of ``"stdout"`` / ``"stderr"``
    so the UI can colourize. ``on_done(rc)`` is called exactly once
    after both pipes have closed and the child has exited.

    Both callbacks fire from background threads — callers that need
    to mutate Tk state MUST trampoline through ``root.after``.
    """
    # Force the child Python to flush stdout/stderr line-by-line.
    # Without PYTHONUNBUFFERED, CPython block-buffers when the parent
    # captures via PIPE, which would silently hold every line until
    # the buffer (~4 KiB) fills — i.e. no live progress in the UI.
    env_eff: Dict[str, str] = dict(env) if env is not None else dict(os.environ)
    env_eff.setdefault("PYTHONUNBUFFERED", "1")
    proc = popen(
        argv,
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        env=env_eff,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered on the parent side
    )

    def _pump(stream_name: str, fh: Any) -> None:
        try:
            for raw in iter(fh.readline, ""):
                on_line(stream_name, raw.rstrip("\n"))
        finally:
            try:
                fh.close()
            except Exception:
                pass

    t_out = threading.Thread(
        target=_pump, args=("stdout", proc.stdout), daemon=True)
    t_err = threading.Thread(
        target=_pump, args=("stderr", proc.stderr), daemon=True)
    t_out.start()
    t_err.start()

    def _waiter() -> None:
        proc.wait()
        # R10D / P1-3 — guarantee stdout/stderr are fully drained
        # before signalling completion. ``proc.wait()`` has already
        # returned, so both readline() loops will see EOF (empty
        # string) and exit. Joining without a timeout means
        # ``on_done`` cannot fire while there's still a queued line
        # waiting to be appended to the log — important once the
        # autotrade email and the panel snapshot start reading the
        # last_run buffers right after on_done.
        t_out.join()
        t_err.join()
        on_done(int(proc.returncode))

    threading.Thread(target=_waiter, daemon=True).start()
    return proc


# ──────────────────────────────────────────────────────────────────────
# Submit-outcome quality check
# ──────────────────────────────────────────────────────────────────────
def submit_outcome_is_clean(run_dir: Path) -> Tuple[bool, str]:
    """R10 §1 §2.2: T10 apply is only allowed when the most recent
    paper-submit outcome contains only FILLED orders. Returns
    (clean, reason)."""
    js = Path(run_dir) / "autotrade_daily_report.json"
    if not js.exists():
        return False, "no daily report yet"
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"report unreadable: {e}"
    if data.get("rc") != 0:
        return False, f"last report rc={data.get('rc')!r}"
    if data.get("hard_stop"):
        return False, f"hard_stop@{(data['hard_stop'] or {}).get('where','')}"
    counts = (data.get("outcome_counts") or {})
    blockers = {
        "unknown": int(counts.get("unknown", 0) or 0),
        "open_or_pending": int(counts.get("open_or_pending", 0) or 0),
        "cancel_requested": int(counts.get("cancel_requested", 0) or 0),
        "rejected": int(counts.get("rejected", 0) or 0),
        # Partial fills require explicit operator allowance — for R10
        # we conservatively block here too.
        "partially_filled": int(counts.get("partially_filled", 0) or 0),
    }
    bad = {k: v for k, v in blockers.items() if v > 0}
    if bad:
        return False, f"non-FILLED outcomes present: {bad}"
    filled = int(counts.get("filled", 0) or 0)
    if filled <= 0:
        return False, "no FILLED outcomes recorded"
    return True, f"{filled} FILLED, no blockers"


# ──────────────────────────────────────────────────────────────────────
# Tkinter UI
# ──────────────────────────────────────────────────────────────────────
def _resolve_paper_output_dir() -> Path:
    from phase3.autotrade.daily_runner import resolve_profile_paths
    return resolve_profile_paths("paper")["output_dir"]


def _resolve_paper_paths() -> Dict[str, Path]:
    from phase3.autotrade.daily_runner import resolve_profile_paths
    return resolve_profile_paths("paper")


def _format_intents_line(s: intents_io.IntentFileStatus) -> str:
    if s.state == "ok":
        return f"OK  (intents={s.intent_count} buy={s.buy_count})"
    return f"{s.state.upper()}  — {s.reason}"


def _format_t10_journal_line(j: T10JournalStatus) -> str:
    if j.is_clean:
        return "clean"
    parts = []
    if j.has_open_started:
        parts.append(f"open started: {j.open_started_batches}")
    if j.has_recovery:
        parts.append(f"recovery: {j.recovery_batches}")
    return "; ".join(parts)


def launch_panel() -> None:  # pragma: no cover — Tk loop is interactive
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception as e:  # noqa: BLE001
        print(f"[control_panel] tkinter unavailable: {e}", file=sys.stderr)
        return

    try:
        paths = _resolve_paper_paths()
        output_dir = paths["output_dir"]
        config_path = paths["config_path"]
        load_err: Optional[str] = None
    except Exception as e:  # noqa: BLE001
        output_dir = None  # type: ignore[assignment]
        config_path = None  # type: ignore[assignment]
        load_err = str(e)

    root = tk.Tk()
    root.title(DASHBOARD_TITLE)
    root.geometry("900x860")

    # — Scrollable body. The panel has grown enough that the bottom
    # half scrolls off-screen on standard-resolution monitors. We wrap
    # all sections in a Canvas + inner Frame so the operator can scroll
    # vertically with the trackpad / mouse wheel without resizing the
    # window. The mouse-wheel delta convention differs between platforms
    # so we normalise to a single "one row per notch" step.
    _outer = ttk.Frame(root)
    _outer.pack(fill="both", expand=True)
    _canvas = tk.Canvas(_outer, highlightthickness=0)
    _vscroll = ttk.Scrollbar(_outer, orient="vertical", command=_canvas.yview)
    _canvas.configure(yscrollcommand=_vscroll.set)
    _vscroll.pack(side="right", fill="y")
    _canvas.pack(side="left", fill="both", expand=True)
    _body = ttk.Frame(_canvas)
    _body_window = _canvas.create_window((0, 0), window=_body, anchor="nw")

    def _on_body_configure(_event):
        _canvas.configure(scrollregion=_canvas.bbox("all"))

    def _on_canvas_configure(event):
        _canvas.itemconfigure(_body_window, width=event.width)

    def _on_mousewheel(event):
        # macOS sends ±1 per notch, Windows/Linux send ±120; normalise.
        step = -1 if event.delta > 0 else 1
        _canvas.yview_scroll(step, "units")

    _body.bind("<Configure>", _on_body_configure)
    _canvas.bind("<Configure>", _on_canvas_configure)
    _canvas.bind_all("<MouseWheel>", _on_mousewheel)
    _canvas.bind_all("<Button-4>", lambda e: _canvas.yview_scroll(-1, "units"))
    _canvas.bind_all("<Button-5>", lambda e: _canvas.yview_scroll(1, "units"))

    # — Session flags
    session = {"dry_run_rc_clean": False, "submit_outcome_clean": False}
    confirm_submit_var = tk.BooleanVar(value=False)
    confirm_apply_var  = tk.BooleanVar(value=False)
    overwrite_intents_var = tk.BooleanVar(value=False)
    # R10-ARM — Activation toggles. Initial state mirrors whatever is
    # *already* in os.environ so a pre-launch shell export still shows
    # the checkbox ticked. Untick = clear from os.environ this session.
    arm_paper_var = tk.BooleanVar(value=gate_is_armed(os.environ, ARM_PAPER_GATE_VARS))
    arm_t10_var   = tk.BooleanVar(value=gate_is_armed(os.environ, ARM_T10_GATE_VARS))
    run_id_var = tk.StringVar()
    # R10B — Intent Preparation widget state. We hold the buy-candidate
    # list in a closure dict so _refresh() can repopulate the dropdown.
    # ``prev_run_id`` + ``prev_signature`` let the refresher decide
    # whether to reset qty / limit / overwrite — without them a stale
    # limit price from a previous run could survive into the next
    # Generate Intent File click (R10B follow-up).
    intent_state: Dict[str, Any] = {
        "candidates": [],          # list[intents_io.BuyCandidate]
        "selected_idx": tk.IntVar(value=-1),
        "qty_var":   tk.StringVar(value="1"),
        "limit_var": tk.StringVar(value=""),
        "prev_run_id": None,
        "prev_signature": None,
    }

    # — Top action row
    frm_top = ttk.Frame(_body, padding=8)
    frm_top.pack(fill="x")
    ttk.Button(frm_top, text="Refresh", command=lambda: _refresh()).pack(side="left", padx=4)
    ttk.Button(frm_top, text="Use latest awaiting_execution",
                command=lambda: _pick_latest()).pack(side="left", padx=4)
    ttk.Button(frm_top, text="STOP / Emergency Halt",
                command=lambda: _stop()).pack(side="right", padx=4)
    ttk.Button(frm_top, text="Clear halt flag",
                command=lambda: _clear_stop()).pack(side="right", padx=4)

    # — Run ID row
    frm_rid = ttk.Frame(_body, padding=(8, 0))
    frm_rid.pack(fill="x")
    ttk.Label(frm_rid, text="Run ID:").pack(side="left")
    ttk.Entry(frm_rid, textvariable=run_id_var, width=42).pack(side="left", padx=4)

    # — Run section
    frm_run = ttk.LabelFrame(_body, text="Run", padding=8)
    frm_run.pack(fill="x", padx=8, pady=4)
    run_lbls: Dict[str, tk.StringVar] = {
        k: tk.StringVar(value="—")
        for k in ("artifact_status", "run_dir", "intents", "last_report", "last_rc")
    }
    for i, (key, title) in enumerate([
        ("artifact_status", "Artifact status"),
        ("run_dir",         "Run dir"),
        ("intents",         "submitted_intents.json"),
        ("last_report",     "Latest daily report"),
        ("last_rc",         "Latest report summary"),
    ]):
        ttk.Label(frm_run, text=title + ":").grid(row=i, column=0, sticky="w")
        ttk.Label(frm_run, textvariable=run_lbls[key], width=100,
                  anchor="w").grid(row=i, column=1, sticky="we", padx=4)

    # — Safety Gates section
    frm_gates = ttk.LabelFrame(_body, text="Safety Gates", padding=8)
    frm_gates.pack(fill="x", padx=8, pady=4)
    gate_lbls: Dict[str, tk.StringVar] = {
        k: tk.StringVar(value="—")
        for k in (KIS_ENV_VAR, SUBMIT_GATE, CANCEL_GATE, APPLY_GATE,
                  "halt", "t10_journal")
    }
    for i, (key, title) in enumerate([
        (KIS_ENV_VAR, "KIS_ENV"),
        (SUBMIT_GATE, "KIS_PAPER_SUBMIT_OK"),
        (CANCEL_GATE, "KIS_PAPER_CANCEL_OK"),
        (APPLY_GATE,  "AUTOTRADE_T10_APPLY_OK"),
        ("halt",      "global_halt"),
        ("t10_journal", "T10 journal"),
    ]):
        ttk.Label(frm_gates, text=title + ":").grid(row=i, column=0, sticky="w")
        ttk.Label(frm_gates, textvariable=gate_lbls[key], width=100,
                  anchor="w").grid(row=i, column=1, sticky="we", padx=4)

    # — Intent Preparation section (R10B §3.2)
    frm_prep = ttk.LabelFrame(_body, text="Intent Preparation", padding=8)
    frm_prep.pack(fill="x", padx=8, pady=4)
    ttk.Label(frm_prep, text="recommendations.csv:").grid(
        row=0, column=0, sticky="w")
    rec_csv_var = tk.StringVar(value="—")
    ttk.Label(frm_prep, textvariable=rec_csv_var, width=80, anchor="w").grid(
        row=0, column=1, sticky="we", padx=4)
    ttk.Label(frm_prep, text="BUY candidate:").grid(row=1, column=0, sticky="w")
    cand_var = tk.StringVar(value="(refresh first)")
    cand_combo = ttk.Combobox(frm_prep, textvariable=cand_var,
                                state="readonly", width=72)
    cand_combo.grid(row=1, column=1, sticky="we", padx=4, pady=2)
    ttk.Label(frm_prep, text="Qty override:").grid(row=2, column=0, sticky="w")
    ttk.Entry(frm_prep, textvariable=intent_state["qty_var"], width=10).grid(
        row=2, column=1, sticky="w", padx=4)
    ttk.Label(frm_prep, text="Limit price:").grid(row=3, column=0, sticky="w")
    ttk.Entry(frm_prep, textvariable=intent_state["limit_var"], width=14).grid(
        row=3, column=1, sticky="w", padx=4)
    ttk.Checkbutton(frm_prep,
                     text="Allow overwrite of existing submitted_intents.json",
                     variable=overwrite_intents_var,
                     command=lambda: _refresh()).grid(
        row=4, column=0, columnspan=2, sticky="w", padx=4, pady=2)

    # R10C — batch knob: limit pad % applied to every candidate's
    # reco_price in the batch path. 0 = use reco_price as-is.
    ttk.Label(frm_prep, text="Batch limit pad (%):").grid(
        row=5, column=0, sticky="w")
    intent_state["limit_pad_var"] = tk.StringVar(value="0.0")
    ttk.Entry(
        frm_prep, textvariable=intent_state["limit_pad_var"], width=10,
    ).grid(row=5, column=1, sticky="w", padx=4)
    ttk.Label(
        frm_prep,
        text="(positive % bumps every BUY limit upward to lift fill probability)",
        foreground="gray",
    ).grid(row=6, column=0, columnspan=2, sticky="w", padx=4)

    # R10D-3 — quote-fresh limit toggle. When checked, the batch
    # generator calls ``adapter.get_quote(symbol)`` for each candidate
    # and sets ``limit = max(reco_padded, ask*(1+quote_pad))``. The
    # confirmation dialog shows row-level reco/refreshed/asof so the
    # operator can sanity-check before clobbering the intent file.
    intent_state["use_quote_var"] = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frm_prep,
        text="Refresh limits with live KIS quote (R10D-3)",
        variable=intent_state["use_quote_var"],
    ).grid(row=7, column=0, columnspan=2, sticky="w", padx=4, pady=2)
    ttk.Label(frm_prep, text="Quote pad (%):").grid(
        row=8, column=0, sticky="w")
    intent_state["quote_pad_var"] = tk.StringVar(value="0.1")
    ttk.Entry(
        frm_prep, textvariable=intent_state["quote_pad_var"], width=10,
    ).grid(row=8, column=1, sticky="w", padx=4)
    ttk.Label(
        frm_prep,
        text="(applied to max(reco*(1+batch_pad), ask*(1+quote_pad)))",
        foreground="gray",
    ).grid(row=9, column=0, columnspan=2, sticky="w", padx=4)
    frm_prep.columnconfigure(1, weight=1)

    def _on_cand_select(_event: Any = None) -> None:
        cands: List[intents_io.BuyCandidate] = intent_state["candidates"]
        idx = cand_combo.current()
        intent_state["selected_idx"].set(idx)
        if 0 <= idx < len(cands):
            c = cands[idx]
            intent_state["limit_var"].set(f"{c.reco_price:.4f}".rstrip("0").rstrip("."))
        _refresh()

    cand_combo.bind("<<ComboboxSelected>>", _on_cand_select)

    # — Actions section
    frm_act = ttk.LabelFrame(_body, text="Actions", padding=8)
    frm_act.pack(fill="x", padx=8, pady=4)
    btns: Dict[str, ttk.Button] = {}
    reasons: Dict[str, tk.StringVar] = {
        k: tk.StringVar(value="")
        for k in ("generate_intent", "generate_all", "dry_run",
                  "paper_submit", "t10_dry", "t10_apply", "full_paper_run")
    }

    def _on_generate_intent():
        rid = run_id_var.get().strip()
        if not rid:
            messagebox.showwarning("Run ID required", "Pick a run_id first.")
            return
        cands: List[intents_io.BuyCandidate] = intent_state["candidates"]
        idx = cand_combo.current()
        if idx < 0 or idx >= len(cands):
            messagebox.showwarning(
                "No candidate selected",
                "Pick a BUY candidate from the dropdown first.")
            return
        cand = cands[idx]
        try:
            qty = int(intent_state["qty_var"].get())
            lp  = float(intent_state["limit_var"].get())
        except (TypeError, ValueError):
            messagebox.showerror(
                "Invalid qty or limit",
                "Qty must be a positive integer and limit price a positive number.")
            return
        if qty <= 0 or lp <= 0:
            messagebox.showerror(
                "Invalid qty or limit",
                "Qty must be > 0 and limit price > 0.")
            return
        run_dir = Path(output_dir) / "daily_runs" / rid
        already = intents_io.validate_submitted_intents(run_dir)
        if already.is_ok and not overwrite_intents_var.get():
            messagebox.showerror(
                "submitted_intents.json already exists",
                "Tick 'Allow overwrite of existing submitted_intents.json' "
                "and press Refresh first.")
            return
        if already.is_ok:
            if not messagebox.askokcancel(
                "Confirm overwrite",
                f"This will OVERWRITE the existing\n  {already.path}\n"
                f"(currently {already.buy_count} BUY rows).\n\nProceed?"):
                return
        try:
            written = intents_io.write_intent_file_from_candidate(
                run_dir, cand,
                qty_override=qty, limit_price=lp,
                overwrite=overwrite_intents_var.get(),
                run_id=rid,
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Generate Intent File failed", f"{type(e).__name__}: {e}")
            return
        _set_preview(build_command_preview("generate_intent", run_id=rid))
        txt.delete("1.0", "end")
        txt.insert("end",
                    f"Wrote {written}\n"
                    f"  ticker={cand.ticker} action={cand.action} "
                    f"rec_row_id={cand.rec_row_id} qty={qty} limit={lp}\n"
                    f"  client_order_id={intents_io.build_intent_client_order_id(run_id=rid, rec_row_id=cand.rec_row_id, ticker=cand.ticker, qty=qty)}\n")
        overwrite_intents_var.set(False)
        _refresh()
        messagebox.showinfo(
            "Generated", f"submitted_intents.json written for {cand.ticker} qty={qty}.")

    # R10C — generate one intent file covering every BUY candidate at
    # once. Uses each candidate's reco_shares & reco_price (with the
    # optional pad %) so the operator does not have to walk through
    # them one by one.
    # R10D-3 — when ``Refresh limits with live KIS quote`` is checked,
    # each candidate's limit is lifted with the broker's current ask
    # so gap-up tickers do not silently mis-fill at yesterday's close.
    def _on_generate_all_intents():
        rid = run_id_var.get().strip()
        if not rid:
            messagebox.showwarning("Run ID required", "Pick a run_id first.")
            return
        cands: List[intents_io.BuyCandidate] = list(intent_state["candidates"])
        if not cands:
            messagebox.showwarning(
                "No BUY candidates",
                "recommendations.csv has no BUY rows for this run.")
            return
        try:
            pad = float(intent_state["limit_pad_var"].get())
        except (TypeError, ValueError):
            messagebox.showerror(
                "Invalid limit pad",
                "Batch limit pad must be a number (e.g. 0, 0.5, 1.0).")
            return
        if pad < 0:
            messagebox.showerror(
                "Negative batch pad refused",
                "Negative batch pad is not allowed for BUY orders "
                "(it would lower limits below reco_close). "
                "Set pad >= 0.")
            return
        use_quote = bool(intent_state["use_quote_var"].get())
        try:
            qpad = float(intent_state["quote_pad_var"].get())
        except (TypeError, ValueError):
            messagebox.showerror(
                "Invalid quote pad",
                "Quote pad must be a number (e.g. 0, 0.1, 0.5).")
            return
        if use_quote and qpad < 0:
            messagebox.showerror(
                "Negative quote pad refused",
                "Negative quote pad is not allowed for BUY orders.")
            return

        run_dir = Path(output_dir) / "daily_runs" / rid
        already = intents_io.validate_submitted_intents(run_dir)
        if already.is_ok and not overwrite_intents_var.get():
            messagebox.showerror(
                "submitted_intents.json already exists",
                "Tick 'Allow overwrite of existing submitted_intents.json' "
                "and press Refresh first.")
            return

        # R10D-3: build a quote_fn closure if the operator wants live
        # quotes. We construct one adapter per click (cheap; the
        # auth token is cached on the adapter so subsequent quote
        # calls reuse it).
        quote_fn = None
        warnings: List[intents_io.IntentBuildWarning] = []
        if use_quote:
            try:
                from phase3.autotrade.kis_broker_adapter import (
                    KisBrokerAdapter, load_env_config,
                )
                env_cfg = load_env_config()
                if env_cfg.env_name != "paper":
                    messagebox.showerror(
                        "Quote refresh paper-only",
                        f"R10D-3 quote refresh is paper-only "
                        f"(KIS_ENV={env_cfg.env_name!r}).")
                    return
                _quote_adapter = KisBrokerAdapter(cfg=env_cfg, verbose=False)

                # R10E — recommendations.csv has no exchange column,
                # so candidate.market is hard-coded NASD. KIS quote
                # then returns last=0/ask=0 for NYSE/AMEX tickers
                # (JBL, DOW, etc.) and the R10D-3 helper falls back
                # to yesterday's close — which is exactly how
                # 20260519_220825_daily overpriced its limits.
                # ``get_quote_with_exchange_fallback`` probes
                # NASD → NYSE → AMEX so the helper only falls back
                # to reco_close when the symbol genuinely has no
                # live US quote.
                def _quote_fn(symbol: str, market: str):
                    return _quote_adapter.get_quote_with_exchange_fallback(
                        symbol, preferred_market=market,
                    )
                quote_fn = _quote_fn
            except Exception as e:  # noqa: BLE001
                messagebox.showerror(
                    "Quote adapter init failed",
                    f"{type(e).__name__}: {e}")
                return

        # Preview line so the operator knows what they're about to write.
        rows_preview = intents_io.candidates_to_intent_rows(
            cands, limit_pad_pct=pad,
            quote_fn=quote_fn, quote_pad_pct=qpad,
            warnings_out=warnings)
        total_qty = sum(int(r["qty"]) for r in rows_preview)
        usd_estimate = sum(
            int(r["qty"]) * float(r["limit_price"]) for r in rows_preview)
        sample_lines = "\n".join(
            f"  - {r['symbol']:<6} qty={r['qty']:<3} "
            f"limit={r['limit_price']:.4f}  src={r.get('_quote_source', '?')}"
            for r in rows_preview[:10]
        )
        more = (f"\n  ... and {len(rows_preview) - 10} more"
                if len(rows_preview) > 10 else "")
        warn_block = ""
        if warnings:
            warn_lines = "\n".join(
                f"  ! {w.ticker}: {w.reason}" for w in warnings[:10])
            warn_more = (f"\n  ... and {len(warnings) - 10} more"
                          if len(warnings) > 10 else "")
            warn_block = (
                f"\n\nQuote-refresh fallback warnings "
                f"({len(warnings)} row(s)):\n{warn_lines}{warn_more}"
            )
        if already.is_ok:
            confirm_msg = (
                f"This will OVERWRITE the existing\n  {already.path}\n"
                f"(currently {already.buy_count} BUY rows).\n\n"
            )
        else:
            confirm_msg = ""
        quote_line = (f"  quote refresh: ON (quote pad {qpad:+.2f}%)"
                      if use_quote else "  quote refresh: OFF")
        if not messagebox.askokcancel(
            "Confirm batch intent generation",
            f"{confirm_msg}Write {len(rows_preview)} BUY intent rows\n"
            f"  total qty     : {total_qty}\n"
            f"  total notional ≈ ${usd_estimate:,.2f}\n"
            f"  batch pad     : {pad:+.2f}%\n"
            f"{quote_line}\n\n"
            f"{sample_lines}{more}{warn_block}\n\nProceed?",
        ):
            return
        try:
            # Write rows_preview directly — we already paid the
            # network cost (and recorded warnings) above. Calling
            # write_intent_file_from_candidates with the same quote_fn
            # would re-issue every quote call and could yield
            # different results in the worst case.
            written = intents_io.write_submitted_intents(
                run_dir, rows_preview, run_id=rid,
                overwrite=overwrite_intents_var.get(),
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Generate ALL Intents failed",
                f"{type(e).__name__}: {e}")
            return
        _set_preview(build_command_preview("generate_intent", run_id=rid))
        txt.delete("1.0", "end")
        txt.insert("end", f"Wrote {written}\n")
        txt.insert("end",
                    f"  rows={len(rows_preview)}  total_qty={total_qty}  "
                    f"notional≈${usd_estimate:,.2f}  "
                    f"batch_pad={pad:+.2f}%  "
                    f"quote_refresh={'on' if use_quote else 'off'}\n\n")
        for r in rows_preview:
            txt.insert("end",
                        f"  {r['symbol']:<6} qty={r['qty']:<3} "
                        f"limit={r['limit_price']:.4f}  "
                        f"src={r.get('_quote_source', '?')}  "
                        f"cid={r['client_order_id']}\n")
        if warnings:
            txt.insert("end", "\n[quote refresh warnings]\n")
            for w in warnings:
                txt.insert("end", f"  ! {w.ticker}: {w.reason}\n")
        overwrite_intents_var.set(False)
        _refresh()
        messagebox.showinfo(
            "Generated (batch)",
            f"submitted_intents.json written with {len(rows_preview)} rows "
            f"(total qty={total_qty}, notional≈${usd_estimate:,.2f}, "
            f"warnings={len(warnings)}).")

    # ── R10C — Tk-side wrapper around run_subprocess_streaming ────────
    # The previous wiring used blocking ``subprocess.run`` which froze
    # the Tk main loop for the entire daily_runner invocation. This
    # wrapper streams every stdout / stderr line into the Output area
    # the moment the subprocess prints it, while a ticking status
    # label keeps the operator aware that "yes, it's still working".
    def _tick_status():
        if running_state["active"]:
            elapsed = time.monotonic() - running_state["started"]
            status_var.set(
                f"running {running_state['label']}  {elapsed:5.1f}s"
            )
            root.after(500, _tick_status)

    def _stream_argv(
        argv: List[str],
        *,
        label: str,
        env: Optional[Dict[str, str]] = None,
        on_finished: Optional[Callable[[int], None]] = None,
    ) -> None:
        if running_state["active"]:
            messagebox.showwarning(
                "Another action is still running",
                f"Wait for '{running_state['label']}' to finish first."
            )
            return
        txt.delete("1.0", "end")
        started_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        txt.insert("end", f"$ {' '.join(argv)}\n")
        txt.insert("end", f"[{label}] starting at {started_iso}\n\n")
        txt.see("end")
        running_state.update({
            "active": True,
            "label": label,
            "started": time.monotonic(),
        })
        status_var.set(f"running {label}  0.0s")
        for b in btns.values():
            b.config(state="disabled")
        # Reset the streaming accumulators so the Copy Snapshot button
        # only ever exposes the current invocation.
        stream_buffers: Dict[str, List[str]] = {"stdout": [], "stderr": []}

        def _on_line(stream: str, line: str) -> None:
            stream_buffers[stream].append(line)

            def _append() -> None:
                prefix = "[stderr] " if stream == "stderr" else ""
                txt.insert("end", prefix + line + "\n")
                txt.see("end")
            root.after(0, _append)

        def _on_done(rc: int) -> None:
            def _finish() -> None:
                elapsed = time.monotonic() - running_state["started"]
                txt.insert(
                    "end",
                    f"\n[done] {label} rc={rc}  elapsed={elapsed:.1f}s\n",
                )
                txt.see("end")
                status_var.set(f"done rc={rc}  ({elapsed:.1f}s)")
                running_state["active"] = False
                running_state["proc"] = None
                last_run["argv"] = list(argv)
                last_run["stdout"] = "\n".join(stream_buffers["stdout"])
                last_run["stderr"] = "\n".join(stream_buffers["stderr"])
                if on_finished is not None:
                    try:
                        on_finished(rc)
                    except Exception as e:  # noqa: BLE001
                        txt.insert(
                            "end",
                            f"[panel] on_finished hook raised: "
                            f"{type(e).__name__}: {e}\n",
                        )
                _refresh()
            root.after(0, _finish)

        proc = run_subprocess_streaming(
            list(argv), cwd=_REPO_ROOT, env=env,
            on_line=_on_line, on_done=_on_done,
        )
        running_state["proc"] = proc
        root.after(500, _tick_status)

    def _on_dry_run():
        rid = run_id_var.get().strip()
        if not rid:
            messagebox.showwarning("Run ID required", "Pick a run_id first.")
            return
        cmd = build_command_preview("dry_run", run_id=rid)
        _set_preview(cmd)
        argv = _build_dry_run_argv(rid, profile="paper")

        def _after(rc: int) -> None:
            session["dry_run_rc_clean"] = (rc == 0)
        _stream_argv(argv, label="dry-run", on_finished=_after)

    def _on_paper_submit():
        rid = run_id_var.get().strip()
        # R10D / P0-3 — defence-in-depth: re-evaluate the button matrix
        # at callback entry so a stale UI state can't bypass the gate.
        # Crucially, this also forbids the callback from synthesising
        # the danger env vars itself — the operator must have already
        # armed them via the UI toggle, so they are present in
        # os.environ. The previous wiring forced
        # SUBMIT_GATE/CANCEL_GATE to "true" inside this callback
        # regardless of Arm state; that loophole is now closed.
        try:
            revalidate_danger_action(
                action="paper_submit",
                output_dir=output_dir,
                run_id=rid,
                env=os.environ,
                confirm_submit_checked=confirm_submit_var.get(),
                confirm_apply_checked=confirm_apply_var.get(),
                overwrite_intents_checked=overwrite_intents_var.get(),
                dry_run_rc_clean=session["dry_run_rc_clean"],
                submit_outcome_clean=session["submit_outcome_clean"],
            )
        except DangerActionDenied as e:
            messagebox.showerror("Paper submit refused", e.reason)
            _refresh()
            return
        cmd = build_command_preview("paper_submit", run_id=rid)
        if not messagebox.askokcancel(
            "Confirm paper submit",
            f"This will execute:\n\n{cmd}\n\nProceed?"):
            return
        # Inherit the already-armed env. We do NOT inject the gates
        # here — revalidate_danger_action above confirmed they are
        # set in os.environ via the Arm toggle.
        env = os.environ.copy()
        _set_preview(cmd)
        argv = _build_paper_submit_argv(rid, profile="paper")

        def _after(rc: int) -> None:
            clean = False
            if output_dir is not None:
                clean, _why = submit_outcome_is_clean(
                    Path(output_dir) / "daily_runs" / rid
                )
            session["submit_outcome_clean"] = clean
            confirm_submit_var.set(False)
        _stream_argv(argv, label="paper-submit", env=env, on_finished=_after)

    def _on_t10_dry():
        rid = run_id_var.get().strip()
        cmd = build_command_preview("t10_dry", run_id=rid)
        _set_preview(cmd)
        argv = _build_t10_argv(rid, apply_mode=False, profile="paper")
        _stream_argv(argv, label="t10-dry")

    def _on_t10_apply():
        rid = run_id_var.get().strip()
        # R10D / P0-3 — same defence-in-depth as paper-submit.
        try:
            revalidate_danger_action(
                action="t10_apply",
                output_dir=output_dir,
                run_id=rid,
                env=os.environ,
                confirm_submit_checked=confirm_submit_var.get(),
                confirm_apply_checked=confirm_apply_var.get(),
                overwrite_intents_checked=overwrite_intents_var.get(),
                dry_run_rc_clean=session["dry_run_rc_clean"],
                submit_outcome_clean=session["submit_outcome_clean"],
            )
        except DangerActionDenied as e:
            messagebox.showerror("T10 apply refused", e.reason)
            _refresh()
            return
        cmd = build_command_preview("t10_apply", run_id=rid)
        if not messagebox.askokcancel(
            "Confirm T10 real apply",
            f"This will mutate holdings_log.xlsx via:\n\n{cmd}\n\nProceed?"):
            return
        env = os.environ.copy()
        _set_preview(cmd)
        argv = _build_t10_argv(rid, apply_mode=True, profile="paper")

        def _after(rc: int) -> None:
            confirm_apply_var.set(False)
        _stream_argv(argv, label="t10-apply", env=env, on_finished=_after)

    btn_specs = [
        ("generate_intent","0a. Generate Intent File (single)", _on_generate_intent),
        ("generate_all",   "0b. Generate ALL Intents (batch)",  _on_generate_all_intents),
        ("dry_run",        "1. Dry Run Preflight / Report",     _on_dry_run),
        ("paper_submit",   "2. Paper Submit + Manage",          _on_paper_submit),
        ("t10_dry",        "3. T10 Apply Dry Run",              _on_t10_dry),
        ("t10_apply",      "4. T10 Apply Real",                 _on_t10_apply),
        ("full_paper_run", "5. Full Paper Run (R11)",           lambda: messagebox.showinfo(
            "Disabled", DISABLED_TOOLTIP_FULL_RUN)),
    ]
    for i, (bid, label, fn) in enumerate(btn_specs):
        b = ttk.Button(frm_act, text=label, command=fn)
        b.grid(row=i, column=0, sticky="we", padx=4, pady=2)
        ttk.Label(frm_act, textvariable=reasons[bid], foreground="#666",
                  wraplength=580, anchor="w").grid(row=i, column=1, sticky="we", padx=4)
        btns[bid] = b
    frm_act.columnconfigure(1, weight=1)

    # — Activation (R10-ARM) — in-UI replacement for the old shell-export
    #   workflow. Ticking either box sets the corresponding env var(s)
    #   ON this process's os.environ; unticking clears them. The
    #   subsequent confirmation row + the per-button "I authorize"
    #   checkbox are unchanged.
    frm_arm = ttk.LabelFrame(_body, text="Activation (this session)", padding=8)
    frm_arm.pack(fill="x", padx=8, pady=4)

    def _on_arm_paper_toggle():
        if arm_paper_var.get():
            ok = messagebox.askokcancel(
                "Arm Paper Submit gate",
                "This will set\n"
                f"  {SUBMIT_GATE}=true\n"
                f"  {CANCEL_GATE}=true\n"
                "in THIS UI session only. No file is modified. Closing "
                "the UI clears it. You still have to tick "
                "'I authorize PAPER SUBMIT' and click the button "
                "before any order is sent.\n\nContinue?",
            )
            if not ok:
                arm_paper_var.set(False)
                _refresh()
                return
            for k, v in arm_gate_vars(os.environ, ARM_PAPER_GATE_VARS).items():
                os.environ[k] = v
        else:
            for k in ARM_PAPER_GATE_VARS:
                os.environ.pop(k, None)
        _refresh()

    def _on_arm_t10_toggle():
        if arm_t10_var.get():
            ok = messagebox.askokcancel(
                "Arm T10 Apply gate",
                "This will set\n"
                f"  {APPLY_GATE}=true\n"
                "in THIS UI session only. No file is modified. Closing "
                "the UI clears it. You still have to tick "
                "'I authorize T10 REAL APPLY' and click the button "
                "before holdings_log.xlsx is mutated.\n\nContinue?",
            )
            if not ok:
                arm_t10_var.set(False)
                _refresh()
                return
            for k, v in arm_gate_vars(os.environ, ARM_T10_GATE_VARS).items():
                os.environ[k] = v
        else:
            for k in ARM_T10_GATE_VARS:
                os.environ.pop(k, None)
        _refresh()

    ttk.Checkbutton(
        frm_arm,
        text=f"Arm Paper Submit gate  ({SUBMIT_GATE} + {CANCEL_GATE} = true)",
        variable=arm_paper_var,
        command=_on_arm_paper_toggle,
    ).grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Checkbutton(
        frm_arm,
        text=f"Arm T10 Apply gate  ({APPLY_GATE} = true)",
        variable=arm_t10_var,
        command=_on_arm_t10_toggle,
    ).grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Label(
        frm_arm,
        text=("Each toggle requires a confirmation dialog and only affects "
              "this UI session. Closing the UI fully disarms."),
        foreground="#666", wraplength=820,
    ).grid(row=2, column=0, sticky="w", padx=4, pady=(4, 0))

    # — Confirmation checkboxes
    frm_conf = ttk.Frame(_body, padding=(8, 0))
    frm_conf.pack(fill="x")
    ttk.Checkbutton(frm_conf,
                     text="I authorize PAPER SUBMIT for this run",
                     variable=confirm_submit_var,
                     command=lambda: _refresh()).pack(side="left", padx=8)
    ttk.Checkbutton(frm_conf,
                     text="I authorize T10 REAL APPLY for this run",
                     variable=confirm_apply_var,
                     command=lambda: _refresh()).pack(side="left", padx=8)

    # — Command preview
    frm_prev = ttk.LabelFrame(_body, text="Command Preview", padding=6)
    frm_prev.pack(fill="x", padx=8, pady=4)
    preview_var = tk.StringVar(value="(select a button)")
    ttk.Label(frm_prev, textvariable=preview_var, foreground="#0a0",
              font=("Menlo", 10), wraplength=860,
              anchor="w").pack(fill="x")

    def _set_preview(text: str):
        preview_var.set(text)

    # — Output
    frm_out = ttk.LabelFrame(_body, text="Output / Log", padding=6)
    frm_out.pack(fill="both", expand=True, padx=8, pady=4)

    # Toolbar above the text area (copy buttons + live status).
    frm_out_tools = ttk.Frame(frm_out)
    frm_out_tools.pack(fill="x", pady=(0, 4))

    txt = tk.Text(frm_out, wrap="word", height=12)
    txt.pack(fill="both", expand=True)

    # Remember the last subprocess invocation so the snapshot button
    # can include it without re-parsing the text widget.
    last_run = {"argv": [], "stdout": "", "stderr": ""}

    # ── R10C — live progress / streaming status ───────────────────────
    # status_var drives a label next to the copy buttons that ticks the
    # elapsed seconds while a subprocess is running, and freezes on
    # "done rc=…" once it exits. running_state is a 1-cell dict so
    # nested closures can mutate it without the ``nonlocal`` dance.
    status_var = tk.StringVar(value="idle")
    running_state = {
        "active": False,
        "label": "",
        "started": 0.0,
        "proc": None,
    }

    def _render_output(res: DryRunResult):
        txt.delete("1.0", "end")
        txt.insert("end", f"$ {' '.join(res.argv)}\n\n")
        if res.stdout:
            txt.insert("end", res.stdout + "\n")
        if res.stderr:
            txt.insert("end", "[stderr]\n" + res.stderr + "\n")
        last_run["argv"] = list(res.argv)
        last_run["stdout"] = res.stdout or ""
        last_run["stderr"] = res.stderr or ""

    # ── R10 — copy helpers ────────────────────────────────────────────
    def _copy_to_clipboard(text: str, *, toast: str) -> None:
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            # update() forces the X selection / NSPasteboard sync so
            # the clipboard survives after this Tk app exits.
            root.update()
            messagebox.showinfo("Copied", toast)
        except tk.TclError as e:
            messagebox.showerror("Copy failed", str(e))

    def _copy_output() -> None:
        text = txt.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("Copy", "Output area is empty.")
            return
        _copy_to_clipboard(
            text,
            toast=f"Copied output/log ({len(text)} chars) to clipboard.",
        )

    def _copy_snapshot() -> None:
        rid = run_id_var.get().strip()
        if output_dir is None:
            messagebox.showerror(
                "Cannot build snapshot",
                f"phase3 config not loadable: {load_err}",
            )
            return
        ps = compute_panel_state(
            output_dir=Path(output_dir), run_id=rid, env=os.environ,
        )
        tail_parts = []
        if last_run["stdout"]:
            tail_parts.append(last_run["stdout"])
        if last_run["stderr"]:
            tail_parts.append("[stderr]\n" + last_run["stderr"])
        # Cap the tail so we don't blow up the clipboard on a huge
        # stdout. 8 KiB is plenty for one daily_runner invocation.
        tail = "\n".join(tail_parts)
        if len(tail) > 8192:
            tail = "... (truncated head) ...\n" + tail[-8192:]
        snap = build_panel_snapshot_text(
            ps,
            run_id=rid,
            output_tail=tail,
            last_argv=last_run["argv"] or None,
        )
        _copy_to_clipboard(
            snap,
            toast=f"Copied panel snapshot ({len(snap)} chars) to clipboard.",
        )

    ttk.Button(
        frm_out_tools, text="Copy Output / Log",
        command=_copy_output,
    ).pack(side="left", padx=2)
    ttk.Button(
        frm_out_tools, text="Copy Panel Snapshot",
        command=_copy_snapshot,
    ).pack(side="left", padx=2)
    ttk.Label(
        frm_out_tools,
        text="(or select text + Cmd+C)",
        foreground="gray",
    ).pack(side="left", padx=8)
    # R10C — live status: "idle" / "running <label> 12.3s" / "done rc=0 (4.5s)"
    ttk.Label(
        frm_out_tools, textvariable=status_var,
        foreground="#0a5", font=("TkDefaultFont", 10, "bold"),
    ).pack(side="right", padx=8)

    # ── R10 — keyboard + context menu on the output area ──────────────
    # macOS Tk needs explicit Cmd-bindings; Linux/Win get Control-* too.
    def _select_all_text(event=None):
        txt.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy_selection(event=None):
        try:
            sel = txt.get("sel.first", "sel.last")
        except tk.TclError:
            sel = txt.get("1.0", "end-1c")
        if sel:
            root.clipboard_clear()
            root.clipboard_append(sel)
            root.update()
        return "break"

    for seq in ("<Command-a>", "<Command-A>", "<Control-a>", "<Control-A>"):
        txt.bind(seq, _select_all_text)
    for seq in ("<Command-c>", "<Command-C>", "<Control-c>", "<Control-C>"):
        txt.bind(seq, _copy_selection)

    txt_menu = tk.Menu(txt, tearoff=0)
    txt_menu.add_command(label="Copy selection", command=_copy_selection)
    txt_menu.add_command(label="Select all", command=_select_all_text)
    txt_menu.add_separator()
    txt_menu.add_command(label="Copy panel snapshot", command=_copy_snapshot)

    def _show_txt_menu(event):
        try:
            txt_menu.tk_popup(event.x_root, event.y_root)
        finally:
            txt_menu.grab_release()

    # Button-2 (middle) on X11, Button-3 (right) elsewhere, plus the
    # Mac Control-click convention.
    for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
        txt.bind(seq, _show_txt_menu)

    # — Refresh logic
    def _refresh():
        rid = run_id_var.get().strip()
        if output_dir is None:
            for v in run_lbls.values():
                v.set("(phase3 config unloadable)")
            return
        # R10-ARM — keep Arm checkboxes in sync with the actual env in
        # case something else (a still-open Terminal export, a subprocess
        # that leaked an env var, etc.) changed os.environ behind our
        # back. The checkbox is the user-facing source of truth, but the
        # env mapping is the runtime source of truth.
        arm_paper_var.set(gate_is_armed(os.environ, ARM_PAPER_GATE_VARS))
        arm_t10_var.set(gate_is_armed(os.environ, ARM_T10_GATE_VARS))
        ps = compute_panel_state(
            output_dir=Path(output_dir), run_id=rid, env=os.environ,
        )
        run_lbls["artifact_status"].set(ps.artifact_status)
        run_lbls["run_dir"].set(str(ps.run_dir))
        run_lbls["intents"].set(_format_intents_line(ps.intents))
        if ps.last_report.exists:
            run_lbls["last_report"].set(
                str(ps.last_report.md_path or ps.last_report.json_path)
            )
            run_lbls["last_rc"].set(ps.last_report.summary)
        else:
            run_lbls["last_report"].set("—")
            run_lbls["last_rc"].set("—")

        for g in ps.gates:
            gate_lbls[g.name].set(f"{g.value}  {'OK' if g.ok else 'BLOCK'}"
                                   + (f"  ({g.note})" if g.note else ""))
        gate_lbls["halt"].set(
            f"halted={ps.halt.halted}  reason={ps.halt.reason or '(none)'}"
        )
        gate_lbls["t10_journal"].set(_format_t10_journal_line(ps.t10_journal))

        # R10B — repopulate Intent Preparation dropdown from the snapshot.
        rec_csv_var.set(
            f"exists, BUY candidates={ps.recommendations_buy_count}"
            if ps.recommendations_csv_exists else "missing"
        )
        intent_state["candidates"] = list(ps.buy_candidates)
        labels = [
            f"#{c.rec_row_id:<3} {c.ticker:<6} {c.action:<9} "
            f"reco_shares={c.reco_shares} price={c.reco_price:.4f}"
            + (f" rank={c.rank}" if c.rank is not None else "")
            for c in ps.buy_candidates
        ]

        # R10B-fix — if the operator switched run_id (or this run's
        # recommendations.csv changed), reset selection + qty + limit
        # so a stale limit price from a previous candidate cannot leak
        # into the next Generate Intent File click.
        should_reset = intent_prep_should_reset(
            prev_run_id=intent_state.get("prev_run_id"),
            prev_signature=intent_state.get("prev_signature"),
            new_run_id=rid,
            new_candidates=ps.buy_candidates,
        )
        cand_combo["values"] = labels
        if should_reset:
            cand_combo.set("")
            intent_state["qty_var"].set("1")
            intent_state["limit_var"].set("")
            overwrite_intents_var.set(False)
            intent_state["selected_idx"].set(-1)
        intent_state["prev_run_id"] = rid
        intent_state["prev_signature"] = intent_candidate_signature(
            ps.buy_candidates)

        if ps.buy_candidates and cand_combo.current() < 0:
            cand_combo.current(0)
            c0 = ps.buy_candidates[0]
            if not intent_state["limit_var"].get().strip():
                intent_state["limit_var"].set(
                    f"{c0.reco_price:.4f}".rstrip("0").rstrip("."))

        gates = compute_button_gates(
            ps,
            dry_run_rc_clean=session["dry_run_rc_clean"],
            submit_outcome_clean=session["submit_outcome_clean"],
            confirm_submit_checked=confirm_submit_var.get(),
            confirm_apply_checked=confirm_apply_var.get(),
            overwrite_intents_checked=overwrite_intents_var.get(),
        )
        for bid, btn in btns.items():
            g = gates[bid]
            btn.config(state="normal" if g.enabled else "disabled")
            reasons[bid].set("" if g.enabled else f"disabled — {g.reason}")

    def _pick_latest():
        if output_dir is None:
            messagebox.showerror("Cannot list runs",
                                  f"phase3 config not loadable: {load_err}")
            return
        cand = _latest_awaiting_execution_run_id(output_dir)
        if cand is None:
            messagebox.showinfo("No awaiting_execution run",
                                 "No run is currently awaiting_execution.")
            return
        run_id_var.set(cand)
        _refresh()

    def _stop():
        if not messagebox.askokcancel(
            "Set global halt",
            "Write phase3/autotrade/runtime/global_halt.json halt=true ?\n"
            "New order_manager submits will be rejected until you "
            "press 'Clear halt flag'."):
            return
        p = _write_halt_flag()
        _refresh()
        messagebox.showinfo("Halt set", f"Wrote {p}")

    def _clear_stop():
        p = _clear_halt_flag()
        _refresh()
        messagebox.showinfo("Halt cleared", f"Wrote {p}")

    if load_err:
        txt.insert("end",
                    f"[panel] WARNING: phase3 paper config not loadable: "
                    f"{load_err}\n")
    _refresh()
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    launch_panel()
