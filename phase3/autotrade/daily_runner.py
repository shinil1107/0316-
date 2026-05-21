"""R8-F — single-command daily runner skeleton.

What this is
------------
The minimum viable wrapper that wires today's manual steps into one CLI
invocation:

    preflight (artifact / run_id sanity)
    → pre-reconcile         (reconcile.qty_mismatch must be 0)
    → manage_order × N      (uses order_manager.manage_order)
    → outcome summary       (filled / open / partial / cancelled / unknown)
    → T10 applicator dry-run
    → T10 applicator apply  (only with explicit gates + zero UNKNOWNs)
    → post-reconcile        (qty_mismatch must be 0)
    → write daily report    (R8-G)

R8 hard stops (§8) abort the run BEFORE mutating broker or local state:

  - artifact status is not awaiting_execution
  - --run-id missing
  - pre-submit reconcile has qty_mismatch
  - stale open/pending order exists from same run
  - UNKNOWN state exists anywhere in the run
  - cancel requested but unconfirmed
  - T10 applicator recovery marker exists
  - post-apply reconcile qty_mismatch != 0

What this is NOT
----------------
This is NOT a scheduler. R8 only provides the one-command runner.
Cron / launchd / market-open hooks are deferred.

The module is deliberately I/O-light: every step is invoked through
small, injectable functions (``make_adapter``, ``run_pre_reconcile``,
``run_manage_loop``, ``run_t10_apply``) so the test suite can drive
each hard-stop scenario with fakes and zero network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Defer real-broker / real-reconcile imports to keep module-load free of
# heavy side effects.
_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import t10_apply_journal as tj
from phase3.autotrade.kis_broker_adapter import OrderIntent
from phase3.autotrade.order_manager import (
    ManagedOrderOutcome,
    OrderManagementPolicy,
    manage_order,
)
from phase3.autotrade.order_state import OrderState
from phase3.autotrade.order_store import (
    OrderStore,
    build_client_order_id,
    new_autotrade_run_id,
)

# Heavy imports (KIS adapter / config loader / pandas) are kept lazy
# inside the default factory below. The R8-F injectable test path does
# not need any of them.


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
DAILY_REPORT_MD   = "autotrade_daily_report.md"
DAILY_REPORT_JSON = "autotrade_daily_report.json"
SUBMIT_ENV_GATE   = "KIS_PAPER_SUBMIT_OK"
CANCEL_ENV_GATE   = "KIS_PAPER_CANCEL_OK"
APPLY_ENV_GATE    = "AUTOTRADE_T10_APPLY_OK"


# ──────────────────────────────────────────────────────────────────────
# Value types
# ──────────────────────────────────────────────────────────────────────
@dataclass
class DailyRunContext:
    """All inputs to one daily_runner invocation, kept as plain data so
    tests can construct + mutate them without going through argparse."""
    run_id: str
    profile: str = "paper"
    dry_run: bool = True
    paper_submit: bool = False
    apply_t10: bool = False
    allow_partial: bool = False
    allow_recovery_apply: bool = False
    allow_duplicate_apply: bool = False
    output_dir: Optional[Path] = None
    holdings_log: Optional[Path] = None
    config_path: Optional[Path] = None
    autotrade_run_id: str = field(default_factory=new_autotrade_run_id)
    market: str = "NASD"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def run_dir(self) -> Path:
        assert self.output_dir is not None
        return Path(self.output_dir) / "daily_runs" / self.run_id


@dataclass
class HardStop:
    """A run was aborted before completing. Surfaced verbatim to the
    final report so the operator knows which gate fired."""
    where: str        # e.g. 'preflight', 'pre_reconcile', 'manage_loop', ...
    reason: str
    rc: int           # 2 = config/policy stop, 3 = recovery, 4 = post-apply mismatch
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyRunResult:
    """Terminal result of a single daily_runner invocation."""
    ctx: DailyRunContext
    rc: int
    hard_stop: Optional[HardStop] = None
    outcomes: List[ManagedOrderOutcome] = field(default_factory=list)
    pre_reconcile: Optional[Dict[str, Any]] = None
    post_reconcile: Optional[Dict[str, Any]] = None
    t10_apply_summary: Optional[Dict[str, Any]] = None
    report_paths: Dict[str, str] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# Step 1: preflight
# ──────────────────────────────────────────────────────────────────────
def preflight(ctx: DailyRunContext) -> Optional[HardStop]:
    """R8 §8 hard stop 1-2: artifact status / run_id sanity. Returns
    None if the run can proceed, else a HardStop describing the abort."""
    if not ctx.run_id:
        return HardStop(where="preflight", reason="--run-id is required", rc=2)
    if ctx.output_dir is None:
        return HardStop(
            where="preflight", reason="output_dir not resolved (config error)", rc=2,
        )
    run_dir = ctx.run_dir
    if not run_dir.exists():
        return HardStop(
            where="preflight",
            reason=f"artifact run_dir does not exist: {run_dir}",
            rc=2,
        )
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return HardStop(
            where="preflight",
            reason=f"run_meta.json missing: {meta_path}",
            rc=2,
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return HardStop(
            where="preflight", reason=f"run_meta.json unreadable: {e}", rc=2,
        )
    status = str(meta.get("status", "")).strip()
    if status != "awaiting_execution":
        return HardStop(
            where="preflight",
            reason=f"artifact status is {status!r}, expected 'awaiting_execution'",
            rc=2,
            detail={"actual_status": status},
        )

    # R8 §8: T10 applicator recovery marker must not exist.
    journal_rows = tj.read_journal(run_dir)
    open_starts = {
        r.get("batch_id") for r in journal_rows
        if r.get("status") == "started"
    } - {
        r.get("batch_id") for r in journal_rows
        if r.get("status") == "applied"
    }
    recovery_rows = [r for r in journal_rows if r.get("status") == "recovery"]
    if (open_starts or recovery_rows) and not ctx.allow_recovery_apply:
        return HardStop(
            where="preflight",
            reason=(
                "T10 applicator recovery marker present in journal — "
                "run was previously aborted mid-apply. Operator must "
                "verify holdings before retrying (pass --allow-recovery-apply)."
            ),
            rc=3,
            detail={
                "open_started_batches": sorted(b for b in open_starts if b),
                "recovery_batches": [r.get("batch_id") for r in recovery_rows],
            },
        )
    return None


# ──────────────────────────────────────────────────────────────────────
# Step 2 / Step 6: reconcile wrapping
# ──────────────────────────────────────────────────────────────────────
@dataclass
class ReconcileSummary:
    """Minimal slice of `reconcile.ReconcileReport` we feed into the
    hard-stop decision. The full report still gets written to disk by
    the underlying reconcile module."""
    qty_mismatch_count: int
    local_only_count: int
    broker_only_managed_count: int
    cash_drift_usd: float
    settlement_pending_usd: float
    raw: Dict[str, Any] = field(default_factory=dict)


ReconcileFn = Callable[[DailyRunContext], ReconcileSummary]


def evaluate_reconcile(
    summary: ReconcileSummary,
    *,
    phase: str,
) -> Optional[HardStop]:
    """R8 §8 hard stops for pre-/post-reconcile."""
    if summary.qty_mismatch_count > 0:
        return HardStop(
            where=f"{phase}_reconcile",
            reason=f"reconcile qty_mismatch_count={summary.qty_mismatch_count}",
            rc=2 if phase == "pre" else 4,
            detail=summary.raw,
        )
    return None


# ──────────────────────────────────────────────────────────────────────
# Step 3: manage loop wrapping
# ──────────────────────────────────────────────────────────────────────
ManageLoopFn = Callable[[DailyRunContext, List[OrderIntent]], List[ManagedOrderOutcome]]


def summarize_outcomes(outcomes: List[ManagedOrderOutcome]) -> Dict[str, int]:
    counts = {
        "filled": 0,
        "partially_filled": 0,
        "open_or_pending": 0,
        "cancel_requested": 0,
        "cancelled": 0,
        "rejected": 0,
        "unknown": 0,
    }
    for o in outcomes:
        counts[o.final_state.value] = counts.get(o.final_state.value, 0) + 1
    return counts


def evaluate_outcomes(outcomes: List[ManagedOrderOutcome]) -> Optional[HardStop]:
    """R8 §8 hard stops: any UNKNOWN, stale OPEN, or cancel-unconfirmed
    forces an abort BEFORE T10 apply."""
    unknowns: List[ManagedOrderOutcome] = [
        o for o in outcomes if o.final_state == OrderState.UNKNOWN
    ]
    if unknowns:
        return HardStop(
            where="manage_loop",
            reason=(
                f"{len(unknowns)} order(s) ended in UNKNOWN — operator "
                f"must investigate before T10 apply"
            ),
            rc=2,
            detail={
                "unknown_orders": [
                    {
                        "client_order_id": o.intent.client_order_id,
                        "ticker": o.intent.symbol,
                        "last_broker_order_id": o.last_broker_order_id,
                        "note": o.note,
                    }
                    for o in unknowns
                ],
            },
        )
    stale = [
        o for o in outcomes
        if o.final_state in (OrderState.OPEN_OR_PENDING, OrderState.CANCEL_REQUESTED)
    ]
    if stale:
        return HardStop(
            where="manage_loop",
            reason=(
                f"{len(stale)} order(s) still working at broker (open / "
                f"cancel-unconfirmed) — operator must resolve before T10 apply"
            ),
            rc=2,
            detail={
                "stale_orders": [
                    {
                        "client_order_id": o.intent.client_order_id,
                        "ticker": o.intent.symbol,
                        "state": o.final_state.value,
                        "last_broker_order_id": o.last_broker_order_id,
                        "note": o.note,
                    }
                    for o in stale
                ],
            },
        )
    return None


# ──────────────────────────────────────────────────────────────────────
# Step 4-5: T10 applicator wrapping
# ──────────────────────────────────────────────────────────────────────
T10ApplyFn = Callable[[DailyRunContext, bool], Dict[str, Any]]


def evaluate_t10_apply(summary: Dict[str, Any]) -> Optional[HardStop]:
    """T10 apply rc semantics (from t10_applicator):

      rc=0  : success
      rc=2  : policy abort (missing ccnl / duplicate / etc.)
      rc=3  : R8-E recovery mode hit (started but not applied)
    """
    rc = int(summary.get("rc", -1))
    if rc == 0:
        return None
    if rc == 3:
        return HardStop(
            where="t10_apply",
            reason="t10_applicator entered recovery mode (rc=3)",
            rc=3,
            detail=summary,
        )
    if rc == 2:
        return HardStop(
            where="t10_apply",
            reason="t10_applicator policy abort (rc=2)",
            rc=2,
            detail=summary,
        )
    return HardStop(
        where="t10_apply",
        reason=f"t10_applicator returned unexpected rc={rc}",
        rc=2, detail=summary,
    )


# ──────────────────────────────────────────────────────────────────────
# Daily report (R8-G)
# ──────────────────────────────────────────────────────────────────────
def _operator_action_required(result: DailyRunResult) -> Optional[str]:
    """Returns a short human-readable description of what the operator
    needs to do, or None if nothing is blocked."""
    if result.hard_stop is not None:
        return f"{result.hard_stop.where}: {result.hard_stop.reason}"
    for o in result.outcomes:
        if o.final_state in (
            OrderState.UNKNOWN, OrderState.OPEN_OR_PENDING,
            OrderState.CANCEL_REQUESTED, OrderState.PARTIALLY_FILLED,
        ):
            return (
                f"order_manager: {o.final_state.value} for "
                f"{o.intent.symbol} ({o.intent.client_order_id})"
            )
    return None


def render_daily_report_json(result: DailyRunResult) -> Dict[str, Any]:
    ctx = result.ctx
    op_action = _operator_action_required(result)
    counts = summarize_outcomes(result.outcomes)
    return {
        "schema_version": "autotrade_daily_report/v1",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operator_action_required": op_action,
        "run_id": ctx.run_id,
        "autotrade_run_id": ctx.autotrade_run_id,
        "profile": ctx.profile,
        "market_session_started_at": ctx.started_at,
        "mode": ("apply" if (ctx.paper_submit and ctx.apply_t10)
                 else "submit_only" if ctx.paper_submit
                 else "dry_run"),
        "rc": result.rc,
        "hard_stop": asdict(result.hard_stop) if result.hard_stop else None,
        "preflight_passed": result.hard_stop is None
            or result.hard_stop.where != "preflight",
        "pre_reconcile": result.pre_reconcile,
        "orders_submitted": len(result.outcomes),
        "outcome_counts": counts,
        "outcomes": [
            {
                "client_order_id": o.intent.client_order_id,
                "ticker": o.intent.symbol,
                "side": o.intent.side,
                "qty_intended": o.intent.qty,
                "qty_filled": o.qty_filled,
                "qty_remaining": o.qty_remaining,
                "last_limit_price": o.last_limit_price,
                "last_broker_order_id": o.last_broker_order_id,
                "final_state": o.final_state.value,
                "cancel_attempts": o.cancel_attempts,
                "reprice_attempts": o.reprice_attempts,
                "avg_fill_price": o.avg_fill_price,
                "elapsed_sec": round(o.elapsed_sec, 3),
                "note": o.note,
            }
            for o in result.outcomes
        ],
        "t10_apply": result.t10_apply_summary,
        "post_reconcile": result.post_reconcile,
        "artifact_paths": {
            "run_dir": str(ctx.run_dir),
            "report_md": result.report_paths.get("md"),
            "report_json": result.report_paths.get("json"),
        },
        "cash_drift_usd": (
            (result.post_reconcile or {}).get("cash_drift_usd")
            if result.post_reconcile else
            (result.pre_reconcile or {}).get("cash_drift_usd")
        ),
    }


def render_daily_report_md(result: DailyRunResult) -> str:
    j = render_daily_report_json(result)
    op = j["operator_action_required"]
    lines: List[str] = []
    lines.append(f"# Autotrade daily report — {j['run_id']}")
    lines.append("")
    if op:
        # Email-ready rule: when operator action is required, the FIRST
        # section MUST say so.
        lines.append("## ⚠ Operator action required")
        lines.append("")
        lines.append(f"- {op}")
        lines.append("")
    else:
        lines.append("## Status: clean")
        lines.append("")
        lines.append("No operator action required.")
        lines.append("")
    lines.append(f"- autotrade_run_id : `{j['autotrade_run_id']}`")
    lines.append(f"- profile          : `{j['profile']}`")
    lines.append(f"- mode             : `{j['mode']}`")
    lines.append(f"- rc               : `{j['rc']}`")
    lines.append(f"- session started  : {j['market_session_started_at']}")
    lines.append("")
    if j["hard_stop"]:
        hs = j["hard_stop"]
        lines.append(f"## Hard stop")
        lines.append("")
        lines.append(f"- where  : `{hs['where']}`")
        lines.append(f"- reason : {hs['reason']}")
        lines.append(f"- rc     : `{hs['rc']}`")
        lines.append("")
    if j["pre_reconcile"]:
        lines.append("## Pre-submit reconcile")
        lines.append("")
        pr = j["pre_reconcile"]
        lines.append(f"- qty_mismatch_count : `{pr.get('qty_mismatch_count')}`")
        lines.append(f"- local_only_count   : `{pr.get('local_only_count')}`")
        lines.append(f"- broker_only_managed: `{pr.get('broker_only_managed_count')}`")
        lines.append(f"- cash_drift_usd     : `{pr.get('cash_drift_usd')}`")
        lines.append("")
    if j["outcomes"]:
        lines.append(f"## Orders managed ({j['orders_submitted']})")
        lines.append("")
        lines.append("| ticker | side | qty | filled | last_limit | state | reprice | note |")
        lines.append("|---|---|---:|---:|---:|---|---:|---|")
        for o in j["outcomes"]:
            lines.append(
                f"| {o['ticker']} | {o['side']} | "
                f"{o['qty_intended']} | {o['qty_filled']:.4g} | "
                f"{o['last_limit_price']:.4f} | "
                f"`{o['final_state']}` | "
                f"{o['reprice_attempts']} | {o['note']} |"
            )
        lines.append("")
        lines.append("### Counts")
        lines.append("")
        for k, v in j["outcome_counts"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    if j["t10_apply"]:
        lines.append("## T10 applicator")
        lines.append("")
        lines.append(f"```json\n{json.dumps(j['t10_apply'], indent=2, ensure_ascii=False)}\n```")
        lines.append("")
    if j["post_reconcile"]:
        lines.append("## Post-apply reconcile")
        lines.append("")
        pr = j["post_reconcile"]
        lines.append(f"- qty_mismatch_count : `{pr.get('qty_mismatch_count')}`")
        lines.append(f"- cash_drift_usd     : `{pr.get('cash_drift_usd')}`")
        lines.append("")
    lines.append("## Artifact paths")
    lines.append("")
    for k, v in j["artifact_paths"].items():
        lines.append(f"- {k}: `{v}`")
    return "\n".join(lines) + "\n"


def write_daily_report(result: DailyRunResult) -> Dict[str, str]:
    run_dir = result.ctx.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    md_path = run_dir / DAILY_REPORT_MD
    json_path = run_dir / DAILY_REPORT_JSON
    md_path.write_text(render_daily_report_md(result), encoding="utf-8")
    json_path.write_text(
        json.dumps(render_daily_report_json(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"md": str(md_path), "json": str(json_path)}


# ──────────────────────────────────────────────────────────────────────
# The main run function — wired to injectable steps for tests
# ──────────────────────────────────────────────────────────────────────
def run_daily(
    ctx: DailyRunContext,
    *,
    pre_reconcile_fn: ReconcileFn,
    post_reconcile_fn: ReconcileFn,
    manage_loop_fn: ManageLoopFn,
    intents_loader: Callable[[DailyRunContext], List[OrderIntent]],
    t10_apply_fn: T10ApplyFn,
) -> DailyRunResult:
    """Orchestrate the full pipeline. Returns a ``DailyRunResult`` —
    never raises (top-level exceptions are caught and turned into a
    hard-stop record with rc=2)."""

    result = DailyRunResult(ctx=ctx, rc=0)

    try:
        # ── 1. preflight ────────────────────────────────────────
        stop = preflight(ctx)
        if stop is not None:
            result.hard_stop = stop
            result.rc = stop.rc
            result.report_paths = write_daily_report(result)
            return result

        # ── 2. pre-reconcile ────────────────────────────────────
        pre_summary = pre_reconcile_fn(ctx)
        result.pre_reconcile = {
            "qty_mismatch_count": pre_summary.qty_mismatch_count,
            "local_only_count": pre_summary.local_only_count,
            "broker_only_managed_count": pre_summary.broker_only_managed_count,
            "cash_drift_usd": pre_summary.cash_drift_usd,
            "settlement_pending_usd": pre_summary.settlement_pending_usd,
        }
        stop = evaluate_reconcile(pre_summary, phase="pre")
        if stop is not None:
            result.hard_stop = stop
            result.rc = stop.rc
            result.report_paths = write_daily_report(result)
            return result

        # ── 3. manage loop ──────────────────────────────────────
        # R10-1b: intents_loader is allowed to raise when paper-submit
        # is requested but submitted_intents.json is missing / malformed
        # / empty. The runner translates that to an explicit hard-stop
        # rather than silently submitting zero orders.
        try:
            intents = intents_loader(ctx)
        except Exception as e:  # noqa: BLE001
            stop = HardStop(
                where="intents_loader",
                reason=f"{type(e).__name__}: {e}",
                rc=2,
            )
            result.hard_stop = stop
            result.rc = stop.rc
            result.report_paths = write_daily_report(result)
            return result
        if ctx.paper_submit:
            # R9-C defense-in-depth: refuse to enter manage_loop if
            # the operator has set the global halt flag. Even though
            # ``manage_order`` itself also short-circuits on halt, we
            # surface it as an explicit hard-stop so the daily report
            # shows ``hard_stop@manage_loop: global_halt …`` rather
            # than a wall of REJECTED outcomes.
            from phase3.autotrade import global_halt as _gh
            _halt_state = _gh.read_halt()
            if _halt_state.halted:
                stop = HardStop(
                    where="manage_loop",
                    reason=(
                        f"global_halt is set ({_halt_state.raw_path}): "
                        f"reason={_halt_state.reason or '(none)'} "
                        f"ts={_halt_state.ts or '(none)'}"
                    ),
                    rc=2,
                    detail={"halt_path": _halt_state.raw_path},
                )
                result.hard_stop = stop
                result.rc = stop.rc
                result.report_paths = write_daily_report(result)
                return result
            result.outcomes = manage_loop_fn(ctx, intents)
        else:
            # Dry-run mode: do NOT submit. Report the intent count only.
            result.outcomes = []

        stop = evaluate_outcomes(result.outcomes)
        if stop is not None:
            result.hard_stop = stop
            result.rc = stop.rc
            result.report_paths = write_daily_report(result)
            return result

        # ── 4-5. T10 applicator ─────────────────────────────────
        # Always run the dry-run pass so the operator sees what WOULD
        # change. Only run apply when both --apply-t10 and the env
        # gate are set, AND there were no UNKNOWNs / stale states.
        t10_summary_dry = t10_apply_fn(ctx, False)
        result.t10_apply_summary = {"dry_run": t10_summary_dry}

        if ctx.apply_t10 and ctx.paper_submit:
            if os.environ.get(APPLY_ENV_GATE, "").strip().lower() != "true":
                stop = HardStop(
                    where="t10_apply",
                    reason=f"{APPLY_ENV_GATE}=true is required for --apply-t10",
                    rc=2,
                )
                result.hard_stop = stop
                result.rc = stop.rc
                result.report_paths = write_daily_report(result)
                return result

            t10_summary = t10_apply_fn(ctx, True)
            result.t10_apply_summary["apply"] = t10_summary
            stop = evaluate_t10_apply(t10_summary)
            if stop is not None:
                result.hard_stop = stop
                result.rc = stop.rc
                result.report_paths = write_daily_report(result)
                return result

            # ── 6. post-reconcile ──────────────────────────────
            post_summary = post_reconcile_fn(ctx)
            result.post_reconcile = {
                "qty_mismatch_count": post_summary.qty_mismatch_count,
                "local_only_count": post_summary.local_only_count,
                "broker_only_managed_count": post_summary.broker_only_managed_count,
                "cash_drift_usd": post_summary.cash_drift_usd,
                "settlement_pending_usd": post_summary.settlement_pending_usd,
            }
            stop = evaluate_reconcile(post_summary, phase="post")
            if stop is not None:
                result.hard_stop = stop
                result.rc = stop.rc
                result.report_paths = write_daily_report(result)
                return result

        result.rc = 0
        result.report_paths = write_daily_report(result)
        return result

    except Exception as e:  # noqa: BLE001 — never raise out of run_daily
        result.hard_stop = HardStop(
            where="run_daily.exception",
            reason=f"{type(e).__name__}: {e}",
            rc=2,
        )
        result.rc = 2
        try:
            result.report_paths = write_daily_report(result)
        except Exception:  # noqa: BLE001
            pass
        return result


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="daily_runner",
        description="R8-F single-command paper daily runner.",
    )
    p.add_argument("--run-id", required=True,
                   help="artifact run_id (must have status=awaiting_execution)")
    p.add_argument("--profile", default="paper", choices=("paper",),
                   help="phase3 profile (R8 is paper-only)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=False,
                   help="plan + report only; no broker submit, no T10 apply (default)")
    g.add_argument("--paper-submit", action="store_true", default=False,
                   help=f"actually submit orders via paper broker; "
                        f"requires {SUBMIT_ENV_GATE}=true (and optionally "
                        f"{CANCEL_ENV_GATE}=true for cancel/reprice)")
    p.add_argument("--apply-t10", action="store_true", default=False,
                   help=f"after manage loop, also run t10_applicator --apply; "
                        f"requires {APPLY_ENV_GATE}=true and --paper-submit")
    p.add_argument("--allow-partial", action="store_true", default=False,
                   help="t10_applicator --allow-partial passthrough")
    p.add_argument("--allow-duplicate-apply", action="store_true", default=False,
                   help="t10_applicator --allow-duplicate-apply passthrough")
    p.add_argument("--allow-recovery-apply", action="store_true", default=False,
                   help="t10_applicator --allow-recovery-apply passthrough")
    return p


# ──────────────────────────────────────────────────────────────────────
# R9-B real wiring helpers
#
# Everything in this section is the bridge between the injectable
# pipeline (`run_daily(...)`) and the real autotrade stack: KIS adapter,
# reconcile, recommendations.csv, T10 applicator. Each step is a small
# pure-Python function so `main(..., factories=...)` can still inject
# fakes for hermetic tests.
# ──────────────────────────────────────────────────────────────────────
@dataclass
class DefaultFactories:
    pre_reconcile_fn: "ReconcileFn"
    post_reconcile_fn: "ReconcileFn"
    intents_loader: Callable[[DailyRunContext], List[OrderIntent]]
    manage_loop_fn: "ManageLoopFn"
    t10_apply_fn: "T10ApplyFn"


def resolve_profile_paths(profile: str) -> Dict[str, Path]:
    """Return absolute paths the rest of the pipeline needs. Reuses
    `reconcile._resolve_config_path` + `cache_health.load_config` so we
    have exactly ONE source of truth for ``output_dir`` and
    ``holdings_log``."""
    from phase3.autotrade import reconcile as rec  # lazy
    from cache_health import load_config  # type: ignore[import-not-found]

    cfg_path = rec._resolve_config_path(profile)
    cfg = load_config(str(cfg_path))
    output_dir = Path(cfg["paths"]["output_dir"]).expanduser()
    holdings_log = Path(cfg["paths"]["holdings_log"]).expanduser()
    return {
        "config_path": cfg_path,
        "output_dir": output_dir,
        "holdings_log": holdings_log,
    }


def _reconcile_summary_from_report(rep: Any) -> ReconcileSummary:
    """Translate ``reconcile.ReconcileReport`` into the small slice the
    daily runner needs. Tolerant of attribute shape — only the fields
    R8 §8 actually keys on are required."""
    return ReconcileSummary(
        qty_mismatch_count=int(getattr(rep, "qty_mismatch_count", 0) or 0),
        local_only_count=int(getattr(rep, "local_only_count", 0) or 0),
        broker_only_managed_count=int(
            getattr(rep, "broker_only_managed_count", 0) or 0
        ),
        cash_drift_usd=float(getattr(rep, "cash_drift_usd", 0.0) or 0.0),
        settlement_pending_usd=float(
            getattr(rep, "settlement_pending_usd", 0.0) or 0.0
        ),
        raw={
            "qty_mismatches": getattr(rep, "qty_mismatches", []),
            "local_only": getattr(rep, "local_only", []),
            "broker_only_managed": getattr(rep, "broker_only_managed", []),
        },
    )


def default_pre_reconcile_fn(ctx: DailyRunContext) -> ReconcileSummary:
    """Real pre-reconcile: KIS broker positions vs holdings_log."""
    from phase3.autotrade import reconcile as rec  # lazy
    from phase3.autotrade.kis_broker_adapter import (
        KisBrokerAdapter, load_env_config,
    )
    from cache_health import load_config  # type: ignore[import-not-found]

    cfg = load_config(str(ctx.config_path))
    env_cfg = load_env_config()
    adapter = KisBrokerAdapter(cfg=env_cfg, verbose=False)
    local_positions, local_cash = rec.load_local_state(cfg)
    artifact_id, artifact_status, artifact_tickers = (
        rec.load_artifact_reco_universe(cfg["paths"]["output_dir"])
    )
    broker_positions, broker_cash = rec.load_broker_state(adapter)
    rep = rec.reconcile(
        local_positions=local_positions,
        local_cash=local_cash,
        broker_positions=broker_positions,
        broker_cash=broker_cash,
        managed_scope_extra=artifact_tickers,
        profile=ctx.profile,
        config_path=str(ctx.config_path),
        holdings_log_path=cfg["paths"]["holdings_log"],
        kis_env=env_cfg.env_name,
        artifact_run_id=artifact_id,
        artifact_run_status=artifact_status,
    )
    return _reconcile_summary_from_report(rep)


def default_post_reconcile_fn(ctx: DailyRunContext) -> ReconcileSummary:
    """Same as pre-reconcile in R9; the underlying state has just been
    mutated by T10 apply so the same call produces the post-apply
    snapshot. Kept as a separate factory entry so tests / future hooks
    can replace it independently."""
    return default_pre_reconcile_fn(ctx)


def default_intents_loader(ctx: DailyRunContext) -> List[OrderIntent]:
    """Real intent loader for the paper pipeline.

    Source of truth: ``<run_dir>/submitted_intents.json`` validated
    through ``intents_io.validate_submitted_intents``.

    Dry-run mode always returns ``[]`` because no order should be placed
    and the manage-loop is skipped anyway. Paper-submit mode raises on
    malformed/missing input — ``main()`` catches the raise and turns
    it into a hard-stop with rc=2 BEFORE manage_loop runs (R10 §3.3
    "do not silently submit zero orders")."""
    if ctx.dry_run:
        return []
    from phase3.autotrade import intents_io  # lazy

    status = intents_io.validate_submitted_intents(ctx.run_dir)
    if not status.is_ok:
        raise RuntimeError(
            f"submitted_intents.json invalid for paper-submit: "
            f"state={status.state} reason={status.reason}"
        )
    intents: List[OrderIntent] = []
    for r in status.rows:
        # File wire format uses ``ord_type`` (matches KIS TR docs);
        # the in-memory dataclass uses ``order_type``. The mapping is
        # local to this loader so the on-disk shape stays the canonical
        # one the operator hand-edits.
        # R10E — recover rec_row_id from the row's explicit field
        # first (post-R10E shape), falling back to parsing the
        # client_order_id (pre-R10E shape). Without this, every
        # submitted-event in OrderStore is rec_row_id=0 and
        # t10_applicator cannot match broker fills back to
        # recommendations.csv — the exact failure observed in
        # 20260519_220825_daily.
        cid = str(r["client_order_id"])
        rid_raw = r.get("rec_row_id")
        if rid_raw is None:
            rid_int = intents_io.rec_row_id_from_client_order_id(cid) or 0
        else:
            try:
                rid_int = int(rid_raw)
            except (TypeError, ValueError):
                rid_int = (
                    intents_io.rec_row_id_from_client_order_id(cid) or 0
                )
        intents.append(OrderIntent(
            client_order_id=cid,
            symbol=str(r["symbol"]),
            market=str(r.get("market", "NASD")),
            side="BUY",
            qty=int(r["qty"]),
            order_type=str(r.get("ord_type", "LIMIT")).upper(),
            limit_price=float(r["limit_price"]),
            rec_row_id=rid_int,
        ))
    return intents


def default_manage_loop_fn(
    ctx: DailyRunContext, intents: List[OrderIntent],
) -> List[ManagedOrderOutcome]:
    """Real manage loop: one ``manage_order`` invocation per intent
    against the live KIS paper adapter. Stops at the first non-FILLED
    outcome to avoid stacking blocking events on the OrderStore."""
    if not intents:
        return []
    from phase3.autotrade.kis_broker_adapter import (
        KisBrokerAdapter, load_env_config,
    )

    env_cfg = load_env_config()
    if env_cfg.env_name != "paper":
        raise RuntimeError(
            "default_manage_loop_fn refuses to run against a non-paper "
            f"KIS environment (got {env_cfg.env_name!r}). R9 is paper-only."
        )
    adapter = KisBrokerAdapter(cfg=env_cfg, verbose=False)
    # OrderStore takes the JSONL path, not the directory. Other callers
    # (orchestrator, t10_applicator) follow the same convention so this
    # restores parity.
    store = OrderStore(ctx.run_dir / "autotrade_orders.jsonl")
    # R10F-Q2: honour env-supplied policy overrides so the control
    # panel can dial step / ceiling / attempts per session without a
    # code change. ``from_env`` silently keeps the dataclass default
    # for any var the operator did not set.
    policy = OrderManagementPolicy.from_env()

    # R10F-Q3 — at every reprice, refresh the KIS ask so the new limit
    # can leapfrog the linear step ladder when the market moved more
    # than ``reprice_step_bps`` since submit. ``quote_fn`` is opt-in
    # via ``AUTOTRADE_REPRICE_QUOTE_CHASE`` (default on); we expose
    # the toggle so operators can fall back to step-only if a quote
    # outage is suspected. Quote pad uses the same env knob as intent
    # generation so the operator only tunes one value.
    quote_chase_enabled = os.environ.get(
        "AUTOTRADE_REPRICE_QUOTE_CHASE", "1").strip().lower() not in (
            "", "0", "false", "no", "off")
    try:
        reprice_quote_pad = float(os.environ.get(
            "AUTOTRADE_REPRICE_QUOTE_PAD_PCT", "0.1"))
    except (TypeError, ValueError):
        reprice_quote_pad = 0.1

    def _reprice_quote_fn(symbol: str, market: str):
        return adapter.get_quote_with_exchange_fallback(
            symbol, preferred_market=market,
        )

    quote_fn = _reprice_quote_fn if quote_chase_enabled else None

    outcomes: List[ManagedOrderOutcome] = []
    for intent in intents:
        # R10E — thread the intent's rec_row_id through manage_order
        # so OrderStore.log_transition writes the real RecRowId and
        # t10_applicator can match the fill back to recommendations.csv.
        # Falls back to 0 when an upstream caller has not populated
        # the field (probe scripts / unit tests).
        outcome = manage_order(
            intent,
            adapter=adapter, store=store, policy=policy,
            autotrade_run_id=ctx.autotrade_run_id,
            run_id=ctx.run_id,
            rec_row_id=int(getattr(intent, "rec_row_id", 0) or 0),
            quote_fn=quote_fn,
            quote_pad_pct=reprice_quote_pad,
        )
        outcomes.append(outcome)
        if outcome.final_state != OrderState.FILLED:
            # R8 §8 hard-stop principle: stop the loop the moment any
            # order is non-terminal-good. The runner-level evaluator
            # will turn this into a hard stop.
            break
    return outcomes


def default_t10_apply_fn(
    ctx: DailyRunContext, apply_mode: bool,
) -> Dict[str, Any]:
    """In-process call into ``t10_applicator.cmd_apply``. Captures
    return code + stdout/stderr into a dict the daily report renders."""
    from phase3.autotrade import t10_applicator as ta  # lazy
    from io import StringIO
    from contextlib import redirect_stdout, redirect_stderr

    parser = ta.build_argparser()
    argv = [
        "--run-id", ctx.run_id,
        "--profile", ctx.profile,
    ]
    if apply_mode:
        argv.append("--apply")
    if ctx.allow_partial:
        argv.append("--allow-partial")
    if ctx.allow_duplicate_apply:
        argv.append("--allow-duplicate-apply")
    if ctx.allow_recovery_apply:
        argv.append("--allow-recovery-apply")
    parsed = parser.parse_args(argv)
    out_buf, err_buf = StringIO(), StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = ta.cmd_apply(parsed)
    except SystemExit as e:  # ta sometimes exits hard on config error
        rc = int(getattr(e, "code", 2) or 2)
    return {
        "rc": int(rc),
        "apply_mode": bool(apply_mode),
        "stdout_tail": out_buf.getvalue()[-2000:],
        "stderr_tail": err_buf.getvalue()[-2000:],
    }


def build_default_factories(ctx: DailyRunContext) -> DefaultFactories:
    return DefaultFactories(
        pre_reconcile_fn=default_pre_reconcile_fn,
        post_reconcile_fn=default_post_reconcile_fn,
        intents_loader=default_intents_loader,
        manage_loop_fn=default_manage_loop_fn,
        t10_apply_fn=default_t10_apply_fn,
    )


def main(
    argv: Optional[List[str]] = None,
    *,
    factories: Optional[DefaultFactories] = None,
    paths_resolver: Optional[Callable[[str], Dict[str, Path]]] = None,
    stdout: Optional[Any] = None,
    stderr: Optional[Any] = None,
) -> int:
    """R9-B real CLI entrypoint.

    Returns the daily_runner rc (0 ok, 2 config/policy stop, 3 recovery
    needed, 4 post-apply mismatch). Never raises — every error path is
    mapped to a hard-stop record in the daily report.

    The keyword-only ``factories``, ``paths_resolver``, ``stdout``,
    ``stderr`` arguments exist for tests; pass nothing in production
    and the real KIS-backed defaults are used."""
    args = build_argparser().parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    paper_submit = bool(args.paper_submit)
    apply_t10 = bool(args.apply_t10)
    dry_run = not paper_submit

    # ── Env-gate enforcement before any side effect ────────────
    if apply_t10 and not paper_submit:
        print("[daily_runner] --apply-t10 requires --paper-submit", file=err)
        return 2
    if paper_submit and os.environ.get(SUBMIT_ENV_GATE, "").strip().lower() != "true":
        print(
            f"[daily_runner] --paper-submit requires {SUBMIT_ENV_GATE}=true",
            file=err,
        )
        return 2
    if paper_submit and os.environ.get(CANCEL_ENV_GATE, "").strip().lower() != "true":
        # Cancels would silently dry-run, which would in turn break the
        # cancel-confirm path and force UNKNOWN. Fail loudly instead.
        print(
            f"[daily_runner] --paper-submit requires {CANCEL_ENV_GATE}=true "
            f"(cancel/reprice path is part of order management)",
            file=err,
        )
        return 2
    if apply_t10 and os.environ.get(APPLY_ENV_GATE, "").strip().lower() != "true":
        print(
            f"[daily_runner] --apply-t10 requires {APPLY_ENV_GATE}=true",
            file=err,
        )
        return 2

    # ── Resolve config / output paths ──────────────────────────
    try:
        paths = (paths_resolver or resolve_profile_paths)(args.profile)
    except Exception as e:  # noqa: BLE001
        print(f"[daily_runner] failed to resolve profile paths: {e}", file=err)
        return 2

    ctx = DailyRunContext(
        run_id=args.run_id,
        profile=args.profile,
        dry_run=dry_run,
        paper_submit=paper_submit,
        apply_t10=apply_t10,
        allow_partial=bool(args.allow_partial),
        allow_recovery_apply=bool(args.allow_recovery_apply),
        allow_duplicate_apply=bool(args.allow_duplicate_apply),
        output_dir=paths["output_dir"],
        holdings_log=paths["holdings_log"],
        config_path=paths["config_path"],
    )

    if factories is None:
        factories = build_default_factories(ctx)

    result = run_daily(
        ctx,
        pre_reconcile_fn=factories.pre_reconcile_fn,
        post_reconcile_fn=factories.post_reconcile_fn,
        manage_loop_fn=factories.manage_loop_fn,
        intents_loader=factories.intents_loader,
        t10_apply_fn=factories.t10_apply_fn,
    )

    print(f"[daily_runner] rc={result.rc} run_id={ctx.run_id}", file=out)
    print(
        f"[daily_runner] mode="
        f"{'paper-submit' if paper_submit else 'dry-run'}"
        f"{' +apply-t10' if apply_t10 else ''}",
        file=out,
    )
    for k, v in (result.report_paths or {}).items():
        print(f"[daily_runner] report.{k} = {v}", file=out)
    if result.hard_stop:
        print(
            f"[daily_runner] hard_stop@{result.hard_stop.where}: "
            f"{result.hard_stop.reason}",
            file=err,
        )
    return int(result.rc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
