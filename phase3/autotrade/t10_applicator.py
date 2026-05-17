"""Round 7-A — Autotrade T10 Applicator (paper-only).

Purpose
-------
Close the operational gap left by R6: the orchestrator now submits real
paper orders and we can confirm the fills via `inquire-ccnl`, but the
local `holdings_log.xlsx` is still updated by a human running the GUI T10
flow. This module is a non-GUI CLI that does the equivalent T10 update
*from broker-confirmed fills*, reusing the exact same primitives as the
manual flow:

  * ``HoldingsManager.apply_partial_execution``  — writes Current/History.
  * ``HoldingsManager.record_cash_event``        — appends CashLedger.
  * ``run_artifact.record_execution_artifact``   — appends
    ``execution_applied.csv``, writes ``execution_meta.json``,
    ``portfolio_after_execution.csv`` and updates ``run_meta.status``.

The single source of truth for what actually filled is the broker, queried
via ``KisBrokerAdapter.get_order_history()`` and matched on the
*normalized* ODNO (R6 finding: ``place_order`` returns ``0000041467``
while ``inquire-ccnl`` surfaces ``41467``).

Hard safety rules (per R7 handoff §2)
-------------------------------------
* paper-only — there is no `--live` route here.
* every write path defaults to dry-run.
* ``--run-id`` is required.
* ``--apply`` also requires ``AUTOTRADE_T10_APPLY_OK=true`` in the env.
* never overwrite JSONL rows; appending is the optional R8 follow-up.
* abort rather than guess on any of: missing ccnl row, ``ccld_qty == 0``,
  partial fill (unless ``--allow-partial``), or a RecRowId already present
  in ``execution_applied.csv``.

Typical use
-----------
Dry-run (no mutation, prints preview + writes preview/report files only):

    PYTHONPATH=. python3 -m phase3.autotrade.t10_applicator \\
        --run-id 20260515_191533_daily --dry-run

Real apply:

    AUTOTRADE_T10_APPLY_OK=true PYTHONPATH=. python3 -m \\
        phase3.autotrade.t10_applicator \\
        --run-id 20260515_191533_daily --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Match orchestrator.py: ensure phase3 sibling modules (cache_health,
# holdings_manager, run_artifact, exits) are importable when invoked as
# `python -m phase3.autotrade.t10_applicator` from the repo root.
_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# R8-A centralized ODNO normalization (replaces local `_norm_odno`).
from phase3.autotrade.order_ids import normalize_odno as _norm_odno  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Constants & light helpers
# ──────────────────────────────────────────────────────────────────────
APPLY_ENV_GATE = "AUTOTRADE_T10_APPLY_OK"
APPLY_TRIGGER = "AUTOTRADE"
APPLY_SOURCE = "AUTOTRADE"

PREVIEW_CSV = "autotrade_t10_apply_preview.csv"
REPORT_MD = "autotrade_t10_apply_report.md"
REPORT_JSON = "autotrade_t10_apply_report.json"

# Mirror launcher.py's _BUY_ACTIONS so apply policy stays in lockstep.
BUY_ACTIONS = ("BUY", "BUY_NEW", "BUY_MORE")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# `_norm_odno` is imported at the top of the file from
# ``phase3.autotrade.order_ids`` (R8-A centralization).


def _ccnl_filled_qty(row: Dict[str, Any]) -> Optional[float]:
    for k in ("ft_ccld_qty3", "ft_ccld_qty", "tot_ccld_qty", "ccld_qty"):
        v = row.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _ccnl_ordered_qty(row: Dict[str, Any]) -> Optional[float]:
    for k in ("ft_ord_qty3", "ft_ord_qty", "ord_qty"):
        v = row.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _ccnl_filled_price(row: Dict[str, Any]) -> Optional[float]:
    for k in ("ft_ccld_unpr3", "ft_ccld_unpr", "ccld_unpr"):
        v = row.get(k)
        if v not in (None, ""):
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                continue
    return None


# ──────────────────────────────────────────────────────────────────────
# Resolution dataclass — one per submitted intent
# ──────────────────────────────────────────────────────────────────────
@dataclass
class Resolution:
    rec_row_id: int
    ticker: str
    action: str
    intended_qty: int
    broker_order_id: str
    client_order_id: str
    autotrade_run_id: str
    matched: bool = False
    ord_qty: float = 0.0
    filled_qty: float = 0.0
    filled_price: float = 0.0
    score: float = 0.0
    regime: str = ""
    rank: int = -1
    abort_reason: Optional[str] = None
    note: str = ""
    raw_ccnl_row: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_full(self) -> bool:
        return self.matched and self.filled_qty > 0 and self.filled_qty >= self.ord_qty

    @property
    def is_partial(self) -> bool:
        return self.matched and 0 < self.filled_qty < self.ord_qty

    @property
    def is_zero(self) -> bool:
        return self.matched and self.filled_qty == 0

    @property
    def fill_status(self) -> str:
        if not self.matched:
            return "ccnl_missing"
        if self.is_zero:
            return "zero_filled"
        if self.is_partial:
            return "partially_filled"
        if self.is_full:
            return "fully_filled"
        return "unknown"


# ──────────────────────────────────────────────────────────────────────
# Input loaders
# ──────────────────────────────────────────────────────────────────────
def _load_submitted_events(jsonl_path: Path) -> List[Dict[str, Any]]:
    """Read ``autotrade_orders.jsonl`` and return the latest ``submitted``
    event per ``rec_row_id``. The orchestrator can log the same row twice
    in error paths; we trust the last ``submitted`` with a non-empty
    ``broker_order_id``.
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"autotrade_orders.jsonl missing: {jsonl_path}")
    by_row: Dict[int, Dict[str, Any]] = {}
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("state") != "submitted":
                continue
            if not ev.get("broker_order_id"):
                continue
            rid = ev.get("rec_row_id")
            if rid is None:
                continue
            try:
                rid_int = int(rid)
            except (TypeError, ValueError):
                continue
            # last write wins by insertion order (jsonl is append-only).
            by_row[rid_int] = ev
    return list(by_row.values())


def _load_recommendations(run_dir: Path) -> pd.DataFrame:
    p = run_dir / "recommendations.csv"
    if not p.exists():
        raise FileNotFoundError(f"recommendations.csv missing: {p}")
    return pd.read_csv(p)


def _load_existing_applied(run_dir: Path) -> pd.DataFrame:
    p = run_dir / "execution_applied.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _load_run_meta(run_dir: Path) -> Dict[str, Any]:
    p = run_dir / "run_meta.json"
    if not p.exists():
        raise FileNotFoundError(f"run_meta.json missing: {p}")
    return json.loads(p.read_text())


# ──────────────────────────────────────────────────────────────────────
# Core resolution: submitted events × ccnl rows → Resolutions
# ──────────────────────────────────────────────────────────────────────
def _checkable_actions() -> Tuple[str, ...]:
    """Same set the GUI T10 uses (see launcher.py)."""
    from exits import RecosAction as _RA
    sell = tuple(sorted((_RA.FULL_CLOSE | _RA.PARTIAL_CLOSE) - {"SELL_GRACE"}))
    return tuple(sorted(set(sell) | set(BUY_ACTIONS)))


def _count_checkable(recos: pd.DataFrame) -> int:
    if recos.empty or "Action" not in recos.columns:
        return 0
    checkable = set(_checkable_actions())
    return int(recos["Action"].astype(str).isin(checkable).sum())


def _resolve_against_ccnl(
    submitted: List[Dict[str, Any]],
    recos: pd.DataFrame,
    ccnl_rows: List[Dict[str, Any]],
) -> List[Resolution]:
    by_rec: Dict[int, pd.Series] = {}
    if not recos.empty and "RecRowId" in recos.columns:
        for _, row in recos.iterrows():
            try:
                by_rec[int(row["RecRowId"])] = row
            except (TypeError, ValueError):
                continue

    ccnl_index: Dict[str, Dict[str, Any]] = {}
    for r in ccnl_rows:
        odno = _norm_odno(r.get("odno"))
        if odno:
            ccnl_index[odno] = r

    resolutions: List[Resolution] = []
    for ev in submitted:
        rid = int(ev["rec_row_id"])
        broker_odno = str(ev.get("broker_order_id", "")).strip()
        target = _norm_odno(broker_odno)
        reco_row = by_rec.get(rid)
        if reco_row is None:
            res = Resolution(
                rec_row_id=rid,
                ticker=str(ev.get("ticker", "?")),
                action="UNKNOWN",
                intended_qty=int(ev.get("qty_intended", 0)),
                broker_order_id=broker_odno,
                client_order_id=str(ev.get("client_order_id", "")),
                autotrade_run_id=str(ev.get("autotrade_run_id", "")),
            )
            res.abort_reason = (
                f"recommendations.csv has no RecRowId={rid}"
            )
            resolutions.append(res)
            continue

        ticker = str(reco_row.get("Ticker", ev.get("ticker", "?")))
        action = str(reco_row.get("Action", ""))
        intended_qty = int(reco_row.get("Shares", ev.get("qty_intended", 0)))
        try:
            score = float(reco_row.get("Score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        regime = str(reco_row.get("Regime", "") or "")
        try:
            rank = int(reco_row.get("Rank", -1))
        except (TypeError, ValueError):
            rank = -1

        res = Resolution(
            rec_row_id=rid,
            ticker=ticker,
            action=action,
            intended_qty=intended_qty,
            broker_order_id=broker_odno,
            client_order_id=str(ev.get("client_order_id", "")),
            autotrade_run_id=str(ev.get("autotrade_run_id", "")),
            score=score,
            regime=regime,
            rank=rank,
        )

        match = ccnl_index.get(target)
        if match is None:
            res.matched = False
            res.abort_reason = (
                f"ODNO {broker_odno} not found in inquire-ccnl "
                f"(normalized target='{target}')"
            )
            resolutions.append(res)
            continue

        res.matched = True
        res.raw_ccnl_row = match
        res.ord_qty = _ccnl_ordered_qty(match) or 0.0
        fq = _ccnl_filled_qty(match)
        res.filled_qty = fq if fq is not None else 0.0
        fp = _ccnl_filled_price(match)
        res.filled_price = fp if fp is not None else 0.0

        if res.filled_qty == 0:
            res.abort_reason = (
                f"ccnl row present but filled_qty=0 (ord_qty={res.ord_qty})"
            )
        elif res.is_partial:
            res.note = (
                f"partial fill: {res.filled_qty}/{res.ord_qty}"
            )
        elif res.filled_price <= 0:
            res.abort_reason = (
                "ccnl row matched but ccld_unpr is missing/zero"
            )

        resolutions.append(res)

    return resolutions


# ──────────────────────────────────────────────────────────────────────
# Apply policy
# ──────────────────────────────────────────────────────────────────────
@dataclass
class PolicyDecision:
    applicable: List[Resolution] = field(default_factory=list)
    blocked: List[Resolution] = field(default_factory=list)
    duplicate_rec_ids: List[int] = field(default_factory=list)
    abort: bool = False
    abort_reason: Optional[str] = None


def _apply_policy(
    resolutions: List[Resolution],
    existing_applied: pd.DataFrame,
    *,
    allow_partial: bool,
    allow_duplicate_apply: bool,
) -> PolicyDecision:
    pd_out = PolicyDecision()
    if not resolutions:
        pd_out.abort = True
        pd_out.abort_reason = "no submitted broker orders to apply"
        return pd_out

    existing_ids: List[int] = []
    if (
        not existing_applied.empty
        and "RecRowId" in existing_applied.columns
    ):
        existing_ids = [
            int(v)
            for v in pd.to_numeric(
                existing_applied["RecRowId"], errors="coerce"
            ).dropna().astype(int).tolist()
        ]
    existing_id_set = set(existing_ids)

    for res in resolutions:
        if res.abort_reason:
            pd_out.blocked.append(res)
            continue
        if res.action not in BUY_ACTIONS:
            res.abort_reason = (
                f"action {res.action!r} is not in BUY-only "
                f"R7-A allowlist {BUY_ACTIONS}"
            )
            pd_out.blocked.append(res)
            continue
        if res.rec_row_id in existing_id_set and not allow_duplicate_apply:
            pd_out.duplicate_rec_ids.append(res.rec_row_id)
            res.abort_reason = (
                f"RecRowId={res.rec_row_id} already present in "
                f"execution_applied.csv"
            )
            pd_out.blocked.append(res)
            continue
        if res.is_partial and not allow_partial:
            res.abort_reason = (
                f"partial fill {res.filled_qty}/{res.ord_qty} "
                f"and --allow-partial not set"
            )
            pd_out.blocked.append(res)
            continue
        pd_out.applicable.append(res)

    if pd_out.blocked and not pd_out.applicable:
        pd_out.abort = True
        pd_out.abort_reason = (
            "every submitted intent was blocked; nothing safe to apply"
        )
    elif pd_out.blocked:
        # Codex §3.2: be conservative — if any row aborts, the whole
        # batch aborts. The operator can re-run with --allow-partial or
        # fix the artifact and try again.
        pd_out.abort = True
        pd_out.abort_reason = (
            f"{len(pd_out.blocked)} of {len(resolutions)} intents "
            f"failed apply policy; aborting whole batch"
        )

    return pd_out


# ──────────────────────────────────────────────────────────────────────
# Build the executed_df expected by HoldingsManager / record_execution_artifact
# ──────────────────────────────────────────────────────────────────────
def _build_executed_df(
    run_id: str,
    applicable: List[Resolution],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for r in applicable:
        applied_qty = int(min(r.intended_qty, int(r.filled_qty)))
        if applied_qty <= 0:
            continue
        notes_bits = [f"ODNO={r.broker_order_id}"]
        if r.note:
            notes_bits.append(r.note)
        rows.append({
            "RunId": run_id,
            "RecRowId": r.rec_row_id,
            "Ticker": r.ticker,
            "Action": r.action,
            "ExecutedPrice": float(r.filled_price),
            "ExecutedShares": applied_qty,
            "ExecutionNote": " | ".join(notes_bits),
            "ProfitTier": "",
            "Score": r.score,
            "Regime": r.regime,
            "Rank": r.rank,
            "BrokerOrderId": r.broker_order_id,
            "ClientOrderId": r.client_order_id,
            "AutotradeRunId": r.autotrade_run_id,
            "FillSource": "ccnl",
        })
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────
# Report writers (always run, both dry-run and apply)
# ──────────────────────────────────────────────────────────────────────
def _render_report_md(
    *,
    run_id: str,
    mode: str,
    resolutions: List[Resolution],
    policy: PolicyDecision,
    executed_df: pd.DataFrame,
    total_checkable_count: int,
    pre_status: str,
    post_status: Optional[str],
    cash_after: Optional[float],
    total_after: Optional[float],
    operator_note: str,
) -> str:
    lines: List[str] = []
    lines.append(f"# Autotrade T10 Apply Report")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- mode: `{mode}`")
    lines.append(f"- generated_at: {_now_iso()}")
    lines.append(f"- pre artifact status: `{pre_status}`")
    if post_status is not None:
        lines.append(f"- post artifact status: `{post_status}`")
    lines.append(f"- total_checkable_count: {total_checkable_count}")
    if operator_note:
        lines.append(f"- operator_note: {operator_note}")
    lines.append("")
    lines.append("## Broker truth (per submitted intent)")
    lines.append("")
    lines.append("| RecRowId | Ticker | Action | Intended | ODNO | Ord Qty | Fill Qty | Fill Price | Status | Notes |")
    lines.append("|---:|---|---|---:|---|---:|---:|---:|---|---|")
    for r in resolutions:
        notes = []
        if r.abort_reason:
            notes.append(r.abort_reason)
        if r.note:
            notes.append(r.note)
        lines.append(
            f"| {r.rec_row_id} | {r.ticker} | {r.action} | {r.intended_qty} | "
            f"`{r.broker_order_id}` | {r.ord_qty:g} | {r.filled_qty:g} | "
            f"{r.filled_price:.4f} | {r.fill_status} | "
            f"{'; '.join(notes) if notes else ''} |"
        )
    lines.append("")
    if policy.abort:
        lines.append(f"## ABORT: {policy.abort_reason}")
        lines.append("")
    else:
        lines.append("## Applied rows")
        lines.append("")
        if executed_df.empty:
            lines.append("(none)")
        else:
            lines.append("| RecRowId | Ticker | Action | Qty | Price | Notional | ODNO |")
            lines.append("|---:|---|---|---:|---:|---:|---|")
            total = 0.0
            for _, row in executed_df.iterrows():
                notional = float(row["ExecutedPrice"]) * float(row["ExecutedShares"])
                total += notional
                lines.append(
                    f"| {int(row['RecRowId'])} | {row['Ticker']} | {row['Action']} | "
                    f"{int(row['ExecutedShares'])} | {float(row['ExecutedPrice']):.4f} | "
                    f"{notional:.2f} | `{row['BrokerOrderId']}` |"
                )
            lines.append(f"| | | | | **total** | **{total:.2f}** | |")
        lines.append("")
        if cash_after is not None:
            lines.append(f"- cash_after: ${cash_after:,.2f}")
        if total_after is not None:
            lines.append(f"- total_capital_after: ${total_after:,.2f}")
    return "\n".join(lines) + "\n"


def _render_report_json(
    *,
    run_id: str,
    mode: str,
    resolutions: List[Resolution],
    policy: PolicyDecision,
    executed_df: pd.DataFrame,
    total_checkable_count: int,
    pre_status: str,
    post_status: Optional[str],
    cash_after: Optional[float],
    total_after: Optional[float],
    operator_note: str,
) -> Dict[str, Any]:
    return {
        "schema_version": "autotrade_t10_apply_report/v1",
        "run_id": run_id,
        "mode": mode,
        "generated_at": _now_iso(),
        "pre_status": pre_status,
        "post_status": post_status,
        "total_checkable_count": int(total_checkable_count),
        "operator_note": operator_note,
        "intents": [
            {
                "rec_row_id": r.rec_row_id,
                "ticker": r.ticker,
                "action": r.action,
                "intended_qty": r.intended_qty,
                "broker_order_id": r.broker_order_id,
                "client_order_id": r.client_order_id,
                "autotrade_run_id": r.autotrade_run_id,
                "matched": r.matched,
                "ord_qty": r.ord_qty,
                "filled_qty": r.filled_qty,
                "filled_price": r.filled_price,
                "fill_status": r.fill_status,
                "abort_reason": r.abort_reason,
                "note": r.note,
            }
            for r in resolutions
        ],
        "policy": {
            "abort": policy.abort,
            "abort_reason": policy.abort_reason,
            "applicable_rec_ids": [r.rec_row_id for r in policy.applicable],
            "blocked_rec_ids": [r.rec_row_id for r in policy.blocked],
            "duplicate_rec_ids": policy.duplicate_rec_ids,
        },
        "applied": (
            [] if executed_df.empty
            else json.loads(executed_df.to_json(orient="records"))
        ),
        "cash_after": cash_after,
        "total_after": total_after,
    }


def _write_reports(
    run_dir: Path,
    *,
    md_text: str,
    json_obj: Dict[str, Any],
    preview_df: Optional[pd.DataFrame],
) -> Dict[str, Path]:
    written: Dict[str, Path] = {}
    md_path = run_dir / REPORT_MD
    md_path.write_text(md_text)
    written["report_md"] = md_path

    json_path = run_dir / REPORT_JSON
    json_path.write_text(json.dumps(json_obj, indent=2, default=str))
    written["report_json"] = json_path

    if preview_df is not None and not preview_df.empty:
        preview_path = run_dir / PREVIEW_CSV
        preview_df.to_csv(preview_path, index=False)
        written["preview_csv"] = preview_path
    return written


# ──────────────────────────────────────────────────────────────────────
# Apply (real) — only invoked when --apply + env gate
# ──────────────────────────────────────────────────────────────────────
def _apply_to_holdings(
    *,
    executed_df: pd.DataFrame,
    hm: Any,
    autotrade_run_id: str,
) -> None:
    """Mirror launcher.py:_apply: rename for HoldingsManager, then log cash
    events for the BUY rows."""
    applied_df = executed_df.rename(
        columns={"ExecutedPrice": "Price", "ExecutedShares": "Shares"}
    )
    hm.apply_partial_execution(applied_df, trigger_type=APPLY_TRIGGER)

    for _, row in executed_df.iterrows():
        cost = round(float(row["ExecutedPrice"]) * int(row["ExecutedShares"]), 2)
        action = str(row["Action"])
        ticker = str(row["Ticker"])
        shares = int(row["ExecutedShares"])
        odno = str(row.get("BrokerOrderId", "") or "").strip()
        note = f"{ticker} {shares}sh ODNO={odno} run={autotrade_run_id}"
        if action in BUY_ACTIONS:
            hm.record_cash_event(action, -cost, note)
        # SELL/TRIM not handled in R7-A; explicit allowlist above prevents
        # those rows from reaching here.


# ──────────────────────────────────────────────────────────────────────
# Adapter factory — overridable for tests
# ──────────────────────────────────────────────────────────────────────
def _default_make_adapter(*, paper_only: bool = True):  # pragma: no cover
    from phase3.autotrade.kis_broker_adapter import (
        KisBrokerAdapter, SafetyState, load_env_config,
    )
    cfg = load_env_config()
    if paper_only and not getattr(cfg, "is_paper", False):
        raise SystemExit(
            f"[t10_applicator] hard-stop: KIS env is "
            f"'{getattr(cfg, 'env_name', '?')}', applicator is paper-only"
        )
    return KisBrokerAdapter(cfg=cfg, safety_state=SafetyState(buy_only_mode=True), verbose=False)


def _default_make_hm(holdings_log: Path):  # pragma: no cover
    from holdings_manager import HoldingsManager
    return HoldingsManager(str(holdings_log))


def _default_record_artifact(*args, **kwargs):  # pragma: no cover
    from run_artifact import record_execution_artifact
    return record_execution_artifact(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────
def _resolve_paths(profile: str) -> Tuple[Path, Path, Path]:
    """Resolve (output_dir, holdings_log, cfg_path) via the same profile
    table used by ``phase3.autotrade.reconcile``."""
    from phase3.autotrade.reconcile import _resolve_config_path
    from cache_health import load_config
    cfg_path = _resolve_config_path(profile)
    cfg = load_config(str(cfg_path))
    if "paths" not in cfg:
        raise SystemExit(
            f"[t10_applicator] config {cfg_path} has no 'paths' section"
        )
    out = Path(cfg["paths"]["output_dir"]).expanduser()
    hold = Path(cfg["paths"]["holdings_log"]).expanduser()
    return out, hold, cfg_path


def cmd_apply(
    args: argparse.Namespace,
    *,
    make_adapter=None,
    make_hm=None,
    record_artifact=None,
) -> int:
    """Main path. Returns process exit code (0 success, non-zero abort)."""
    make_adapter = make_adapter or _default_make_adapter
    make_hm = make_hm or _default_make_hm
    record_artifact = record_artifact or _default_record_artifact

    if not args.run_id:
        print("[t10_applicator] --run-id is required", file=sys.stderr)
        return 2
    if args.apply and args.dry_run:
        print(
            "[t10_applicator] --apply and --dry-run are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    is_apply = bool(args.apply)
    if is_apply and os.environ.get(APPLY_ENV_GATE, "").lower() != "true":
        print(
            f"[t10_applicator] {APPLY_ENV_GATE}=true is required to use --apply",
            file=sys.stderr,
        )
        return 2

    if args.profile != "paper":
        # R7-A is explicitly paper-only (see handoff §2 rule #1).
        print(
            f"[t10_applicator] hard-stop: --profile must be 'paper', "
            f"got '{args.profile}'", file=sys.stderr,
        )
        return 2
    output_dir, holdings_log, cfg_used = _resolve_paths(args.profile)
    run_dir = output_dir / "daily_runs" / args.run_id
    if not run_dir.exists():
        print(f"[t10_applicator] artifact run_dir missing: {run_dir}", file=sys.stderr)
        return 2
    jsonl_path = run_dir / "autotrade_orders.jsonl"

    print(f"[t10_applicator] config       = {cfg_used}")
    print(f"[t10_applicator] holdings_log = {holdings_log}")
    print(f"[t10_applicator] run_dir      = {run_dir}")
    mode_label = "apply" if is_apply else "dry_run"
    print(f"[t10_applicator] mode         = {mode_label}")

    run_meta = _load_run_meta(run_dir)
    pre_status = str(run_meta.get("status", ""))
    if is_apply and pre_status == "executed":
        print(
            f"[t10_applicator] artifact already status=executed; "
            f"refusing to re-apply without --allow-duplicate-apply",
            file=sys.stderr,
        )
        if not args.allow_duplicate_apply:
            return 2

    # R10B-fix: ``daily_runner --dry-run`` for a fresh artifact (no
    # paper-submit yet) reaches this code path before any
    # ``autotrade_orders.jsonl`` has been written. Treat the missing
    # file as the same "nothing to apply" outcome the empty-events
    # branch below already returns, instead of letting
    # ``_load_submitted_events`` raise FileNotFoundError into
    # daily_runner's catch-all (which would surface as a misleading
    # ``run_daily.exception`` hard-stop, see R10 PROGRESS doc).
    if not jsonl_path.exists():
        print(
            f"[t10_applicator] {jsonl_path.name} not present yet; "
            f"no submissions to apply for this run",
            file=sys.stderr,
        )
        return 2

    submitted = _load_submitted_events(jsonl_path)
    if not submitted:
        print("[t10_applicator] no submitted events with broker_order_id; nothing to do", file=sys.stderr)
        return 2

    recos = _load_recommendations(run_dir)
    existing_applied = _load_existing_applied(run_dir)

    adapter = make_adapter(paper_only=True)
    ccnl_rows = adapter.get_order_history()

    resolutions = _resolve_against_ccnl(submitted, recos, ccnl_rows)
    policy = _apply_policy(
        resolutions,
        existing_applied,
        allow_partial=args.allow_partial,
        allow_duplicate_apply=args.allow_duplicate_apply,
    )
    total_checkable_count = _count_checkable(recos)

    autotrade_run_id = ""
    for r in resolutions:
        if r.autotrade_run_id:
            autotrade_run_id = r.autotrade_run_id
            break

    print("[broker truth]")
    for r in resolutions:
        print(
            f"  {r.ticker:<5} ODNO={r.broker_order_id} "
            f"filled={r.filled_qty:g}/{r.ord_qty:g} "
            f"price={r.filled_price:.4f} status={r.fill_status}"
            + (f" abort={r.abort_reason}" if r.abort_reason else "")
        )

    executed_df = pd.DataFrame()
    if not policy.abort:
        executed_df = _build_executed_df(args.run_id, policy.applicable)

    cash_after: Optional[float] = None
    total_after: Optional[float] = None
    post_status: Optional[str] = None
    exit_code = 0

    if policy.abort:
        print(f"[t10_applicator] ABORT: {policy.abort_reason}")
        exit_code = 1
    elif executed_df.empty:
        print("[t10_applicator] nothing to apply after policy filters")
        exit_code = 1
    elif not is_apply:
        print("[would apply]")
        for _, row in executed_df.iterrows():
            print(
                f"  {row['Action']:<8} {row['Ticker']:<5} "
                f"{int(row['ExecutedShares'])} @ {float(row['ExecutedPrice']):.4f} "
                f"(ODNO={row['BrokerOrderId']})"
            )
        print("[would update]")
        print(f"  holdings_log.xlsx (HoldingsManager.apply_partial_execution)")
        print(f"  cash ledger (record_cash_event × {len(executed_df)})")
        print(f"  {run_dir / 'execution_applied.csv'}")
        print(f"  {run_dir / 'execution_meta.json'}")
        print(f"  {run_dir / 'portfolio_after_execution.csv'}")
        print(f"  {run_dir / 'run_meta.json'} (status)")
    else:
        # ── R8-E idempotency journal ───────────────────────────────
        from phase3.autotrade.t10_apply_journal import (
            backup_holdings_log,
            compute_apply_batch_id,
            inspect_batch,
            local_duplicate_present,
            write_marker,
        )
        batch_id = compute_apply_batch_id(args.run_id, policy.applicable)
        batch_state = inspect_batch(run_dir, batch_id)
        print(f"[t10_applicator] apply_batch_id = {batch_id}")
        if batch_state.prior_status == "applied":
            print(
                f"[t10_applicator] apply_batch_id already marked applied at "
                f"{batch_state.applied_at}; refusing to re-apply.",
                file=sys.stderr,
            )
            write_marker(
                run_dir, batch_id=batch_id, status="aborted",
                reason="prior_applied", run_id=args.run_id,
            )
            return 2
        # R9-A2: both `started` (mid-apply crash) and `recovery` (prior
        # attempt deliberately marked for operator review) must block
        # the next run until --allow-recovery-apply is explicitly set.
        # The previous implementation only checked `started`, so a
        # prior `recovery` marker silently allowed the very next run
        # to proceed, defeating the entire R8-E guarantee.
        if (batch_state.prior_status in ("started", "recovery")
                and not args.allow_recovery_apply):
            advice = (
                "Inspect holdings_log.xlsx Current/History/CashLedger for the "
                "listed RecRowIds + BrokerOrderIds. If they are already present, "
                "do NOT pass --allow-recovery-apply. If they are missing, the "
                "previous attempt died before Excel mutation; you may retry "
                "with --allow-recovery-apply once you have confirmed."
            )
            if batch_state.prior_status == "started":
                stderr_msg = (
                    f"[t10_applicator] apply_batch_id has a 'started' marker at "
                    f"{batch_state.started_at} but no 'applied' — entering recovery mode."
                )
                marker_reason = "prior_started_requires_operator"
            else:  # 'recovery'
                stderr_msg = (
                    f"[t10_applicator] apply_batch_id has a 'recovery' marker "
                    f"from a previous aborted attempt — operator must re-confirm "
                    f"before retrying."
                )
                marker_reason = "prior_recovery_requires_operator"
            print(stderr_msg, file=sys.stderr)
            write_marker(
                run_dir, batch_id=batch_id, status="recovery", run_id=args.run_id,
                applicable_rec_ids=[r.rec_row_id for r in policy.applicable],
                reason=marker_reason,
                advice=advice,
            )
            return 3

        # Defense-in-depth: peek at holdings_log History before mutating.
        hm = make_hm(holdings_log)
        try:
            hist_df = hm.load_history()
        except Exception:  # noqa: BLE001 — best-effort check
            hist_df = pd.DataFrame()
        local_dupes = local_duplicate_present(
            executed_df=executed_df, history_df=hist_df,
        )
        if local_dupes and not args.allow_duplicate_apply:
            print(
                f"[t10_applicator] local-Excel duplicate suspect: {local_dupes}. "
                f"Refusing to apply. Use --allow-duplicate-apply if you have "
                f"verified this is a legitimate same-day same-price rebuy.",
                file=sys.stderr,
            )
            write_marker(
                run_dir, batch_id=batch_id, status="aborted",
                reason="local_duplicate", run_id=args.run_id,
                duplicates=local_dupes,
            )
            return 2

        backup_path = backup_holdings_log(
            holdings_log, run_dir, run_id=args.run_id, batch_id=batch_id,
        )
        write_marker(
            run_dir, batch_id=batch_id, status="started", run_id=args.run_id,
            holdings_backup=str(backup_path) if backup_path else None,
            applicable_rec_ids=[r.rec_row_id for r in policy.applicable],
            executed_rows=int(len(executed_df)),
        )

        # ── original mutation path ─────────────────────────────────
        _apply_to_holdings(
            executed_df=executed_df, hm=hm,
            autotrade_run_id=autotrade_run_id,
        )
        current_after = hm.load_current()
        cash_after = float(hm.get_cash_balance())
        total_after = float(hm.get_portfolio_value()) + max(cash_after, 0.0)

        op_note = (
            f"autotrade_run_id={autotrade_run_id}; "
            f"source=t10_applicator; "
            f"applied_rec_ids={[r.rec_row_id for r in policy.applicable]}; "
            f"apply_batch_id={batch_id}"
        )
        exec_meta = record_artifact(
            run_dir, executed_df,
            source=APPLY_SOURCE,
            total_checkable_count=total_checkable_count,
            portfolio_after_execution_df=current_after,
            cash_balance=cash_after,
            total_capital=total_after,
            operator_note=op_note,
        )
        post_status = str(exec_meta.get("execution_status", ""))
        write_marker(
            run_dir, batch_id=batch_id, status="applied", run_id=args.run_id,
            execution_status=post_status,
            cash_after=cash_after, total_after=total_after,
        )
        print(
            f"[t10_applicator] applied {len(executed_df)} broker-confirmed "
            f"executions  source={APPLY_SOURCE}"
        )
        print(
            f"[t10_applicator] artifact status: "
            f"{pre_status} -> {post_status}"
        )
        print(f"[t10_applicator] cash_after=${cash_after:,.2f}  total_after=${total_after:,.2f}")
        print(
            f"[t10_applicator] next: reconcile --run-id {args.run_id}"
        )

    operator_note = (
        f"autotrade_run_id={autotrade_run_id}; "
        f"source=t10_applicator; "
        f"mode={mode_label}"
    )
    md_text = _render_report_md(
        run_id=args.run_id,
        mode=mode_label,
        resolutions=resolutions,
        policy=policy,
        executed_df=executed_df,
        total_checkable_count=total_checkable_count,
        pre_status=pre_status,
        post_status=post_status,
        cash_after=cash_after,
        total_after=total_after,
        operator_note=operator_note,
    )
    json_obj = _render_report_json(
        run_id=args.run_id,
        mode=mode_label,
        resolutions=resolutions,
        policy=policy,
        executed_df=executed_df,
        total_checkable_count=total_checkable_count,
        pre_status=pre_status,
        post_status=post_status,
        cash_after=cash_after,
        total_after=total_after,
        operator_note=operator_note,
    )
    written = _write_reports(
        run_dir,
        md_text=md_text,
        json_obj=json_obj,
        preview_df=executed_df if not is_apply else None,
    )
    print("[t10_applicator] reports written:")
    for k, v in written.items():
        print(f"  {k}: {v}")
    return exit_code


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="t10_applicator",
        description="Apply broker-confirmed paper fills to holdings_log.xlsx (R7-A).",
    )
    p.add_argument("--run-id", required=True,
                   help="artifact run_id (e.g. 20260515_191533_daily)")
    p.add_argument("--profile", default="paper", choices=("paper",),
                   help="phase3 profile (R7-A is paper-only)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=False,
                   help="preview only (default if neither flag is given)")
    g.add_argument("--apply", action="store_true", default=False,
                   help=f"actually mutate state; requires {APPLY_ENV_GATE}=true env var")
    p.add_argument("--allow-partial", action="store_true", default=False,
                   help="apply partially-filled rows (off by default)")
    p.add_argument("--allow-duplicate-apply", action="store_true", default=False,
                   help="(debug) allow apply even if RecRowId already in execution_applied.csv")
    p.add_argument("--allow-recovery-apply", action="store_true", default=False,
                   help="R8-E: allow apply even if a prior 'started' marker exists for the "
                        "same apply_batch_id. Operator must have manually verified that the "
                        "earlier attempt did NOT already touch holdings_log.xlsx / CashLedger.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    if not args.apply and not args.dry_run:
        args.dry_run = True
    return cmd_apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
