"""Round 4 (P5.4) — execution report builder.

Codex R4 §5.4 prescribed three artifacts per orchestrator run:

    daily_runs/<RUN_ID>/autotrade_execution_report.md
    daily_runs/<RUN_ID>/autotrade_execution_report.json
    daily_runs/<RUN_ID>/autotrade_execution_report.csv

Inputs come from `OrderStore.build_summary(...)`; the report builder
does not re-derive anything from the JSONL, so the three artifacts are
strictly consistent.

The .csv is shaped to be operator-friendly for the manual T10 path
(same column names where possible) but it is NOT yet wired as an
automatic T10 input — that bridge is deliberately deferred per Codex
R4 §4 / §7 ("does not auto-apply holdings unless separately gated").
"""
from __future__ import annotations

import csv
import json
import os
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional


# Filenames are constants so callers can refer to them when emailing.
REPORT_MD_NAME   = "autotrade_execution_report.md"
REPORT_JSON_NAME = "autotrade_execution_report.json"
REPORT_CSV_NAME  = "autotrade_execution_report.csv"


# ──────────────────────────────────────────────────────────────────────
# CSV — one row per order, T10-compatible columns where possible
# ──────────────────────────────────────────────────────────────────────
_CSV_COLUMNS: List[str] = [
    "ExecutionTimestamp",
    "Source",
    "RunId",
    "RecRowId",
    "Ticker",
    "Side",
    "Action",                 # left blank for v0; orchestrator doesn't know reco action
    "OrderState",
    "QtyIntended",
    "QtyFilled",
    "QtyRemaining",
    "LimitPrice",
    "FillPrice",
    "FillPriceSource",
    "BrokerOrderId",
    "ClientOrderId",
    "EchoSource",
    "EchoMatched",
    "StatusSource",
    "Error",
    "Note",
]


def _render_csv(summary: Dict[str, Any]) -> str:
    """Render the per-order CSV from a summary dict."""
    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)

    for row in summary.get("orders") or []:
        echo = row.get("echo") or {}
        writer.writerow([
            row.get("event_ts") or "",
            "AUTOTRADE",
            row.get("run_id") or "",
            row.get("rec_row_id") or "",
            row.get("ticker") or "",
            row.get("side") or "",
            "",
            row.get("state") or "",
            row.get("qty_intended") if row.get("qty_intended") is not None else "",
            row.get("qty_filled") if row.get("qty_filled") is not None else "",
            row.get("qty_remaining") if row.get("qty_remaining") is not None else "",
            row.get("limit_price") if row.get("limit_price") is not None else "",
            row.get("fill_price") if row.get("fill_price") is not None else "",
            row.get("fill_price_source") or "",
            row.get("broker_order_id") or "",
            row.get("client_order_id") or "",
            echo.get("source") or "",
            echo.get("matched") if echo.get("matched") is not None else "",
            row.get("status_source") or "",
            row.get("error") or "",
            row.get("note") or "",
        ])
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# Markdown — operator-readable narrative
# ──────────────────────────────────────────────────────────────────────
def _fmt_currency(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    sign = "−" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _fmt_qty(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return ""
    if abs(v - int(v)) < 1e-9:
        return str(int(v))
    return f"{v:.4f}"


def _slippage_pct(limit: Any, fill: Any) -> str:
    try:
        lim = float(limit)
        fill_v = float(fill)
    except (TypeError, ValueError):
        return "n/a"
    if lim <= 0:
        return "n/a"
    return f"{(fill_v - lim) / lim * 100:+.2f}%"


def _render_md(summary: Dict[str, Any]) -> str:
    """Render an operator-readable markdown report from a summary dict."""
    lines: List[str] = []
    push = lines.append

    push(f"# Autotrade Execution Report — `{summary.get('run_id')}`")
    push("")
    push(f"- **autotrade_run_id**: `{summary.get('autotrade_run_id')}`")
    push(f"- **mode**: `{summary.get('mode')}`")
    push(f"- **started_at**: `{summary.get('started_at')}`")
    push(f"- **ended_at**: `{summary.get('ended_at')}`")
    dur = summary.get("duration_sec")
    if dur is not None:
        push(f"- **duration**: {float(dur):.2f} s")
    push(f"- **intents_count**: {summary.get('intents_count')}")
    push(f"- **cash_delta_usd**: {_fmt_currency(summary.get('cash_delta_usd'))}")
    gates = summary.get("gates") or {}
    if gates:
        push("")
        push("## Gates")
        for k, v in gates.items():
            push(f"- `{k}` = `{v}`")
    push("")
    push("## Counts by state")
    cbs = summary.get("counts_by_state") or {}
    if not cbs:
        push("- _(none)_")
    else:
        for state, n in sorted(cbs.items()):
            push(f"- `{state}` × {n}")
    pdb = summary.get("position_delta_by_ticker") or {}
    if pdb:
        push("")
        push("## Position delta (broker, this run)")
        for tk, dq in sorted(pdb.items()):
            push(f"- `{tk}` Δ {dq:+d}")
    push("")
    push("## Orders")
    rows = summary.get("orders") or []
    if not rows:
        push("- _(no orders processed)_")
    else:
        push("")
        push("| RecRow | Ticker | Side | Qty | Limit | Fill | Slip | State | Echo | Broker ODNO | Source |")
        push("|---:|:--|:--|---:|---:|---:|---:|:--|:--|:--|:--|")
        for r in rows:
            push("| {rec} | {tk} | {side} | {qty} | {lim} | {fill} | {slip} | `{state}` | {echo} | `{odno}` | {fps} |".format(
                rec=r.get("rec_row_id"),
                tk=r.get("ticker"),
                side=r.get("side"),
                qty=_fmt_qty(r.get("qty_intended")),
                lim=("$" + f"{r['limit_price']:.4f}" if r.get("limit_price") is not None else ""),
                fill=("$" + f"{r['fill_price']:.4f}" if r.get("fill_price") is not None else "—"),
                slip=_slippage_pct(r.get("limit_price"), r.get("fill_price")),
                state=r.get("state") or "?",
                echo=(r.get("echo") or {}).get("source") or "—",
                odno=r.get("broker_order_id") or "—",
                fps=r.get("fill_price_source") or "—",
            ))

    # Surface error / note rows separately so they aren't lost in the table
    err_rows = [r for r in rows if r.get("error")]
    if err_rows:
        push("")
        push("## Errors")
        for r in err_rows:
            push(f"- `{r.get('ticker')}` rec_row_id={r.get('rec_row_id')} — {r.get('error')}")
    note_rows = [r for r in rows if r.get("note")]
    if note_rows:
        push("")
        push("## Notes")
        for r in note_rows:
            push(f"- `{r.get('ticker')}` rec_row_id={r.get('rec_row_id')} — {r.get('note')}")

    push("")
    push("---")
    push(f"_Schema_: `{summary.get('schema_version')}`")
    push("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Public — write three artifacts
# ──────────────────────────────────────────────────────────────────────
def write_reports(summary: Dict[str, Any], out_dir: Path) -> Dict[str, Path]:
    """Write `.md`, `.json`, `.csv` next to each other. Returns the
    three resulting paths keyed by extension."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path   = out_dir / REPORT_MD_NAME
    json_path = out_dir / REPORT_JSON_NAME
    csv_path  = out_dir / REPORT_CSV_NAME

    md_path.write_text(_render_md(summary), encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    csv_path.write_text(_render_csv(summary), encoding="utf-8")

    # 0600 for the JSON since it can carry raw broker rows; .md/.csv are
    # operator-readable narratives without secrets.
    for p in (md_path, json_path, csv_path):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    return {"md": md_path, "json": json_path, "csv": csv_path}
