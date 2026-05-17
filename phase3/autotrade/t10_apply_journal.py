"""R8-E — durable apply-attempt journal for `t10_applicator`.

Problem solved
--------------
R7-A's applicator mutates ``holdings_log.xlsx`` and the cash ledger
BEFORE the artifact marker (`execution_applied.csv`, ``run_meta.status``)
is written. If the Excel write succeeds but the artifact write fails,
a naive rerun of the applicator does NOT see ``execution_applied.csv``
and would happily re-apply the same broker fills, double-counting them.

R8-E adds a small append-only JSONL journal next to the artifact:

    daily_runs/<RUN_ID>/autotrade_t10_apply_attempts.jsonl

Each row is one attempt at applying a particular ``apply_batch_id``,
tagged with a status:

    started       — about to mutate holdings/cash; nothing local committed yet
    applied       — local mutation + artifact write both succeeded
    recovery      — operator-invoked recovery report; do NOT re-apply
    aborted       — pre-mutation abort (no local changes)

Apply-batch ID
--------------
A deterministic SHA-256 derived from the (run_id, RecRowId, ODNO, qty,
price) tuple of every applicable execution in the attempted batch.
Same inputs → same id, so the next invocation sees the prior marker.

This module is intentionally I/O-light and has no KIS dependency, so it
can be unit-tested without any broker fakes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


JOURNAL_FILE = "autotrade_t10_apply_attempts.jsonl"
HOLDINGS_BACKUP_PREFIX = "holdings_log.preapply"


# ──────────────────────────────────────────────────────────────────────
# Apply batch id
# ──────────────────────────────────────────────────────────────────────
def _coerce_batch_item(item: Any) -> Tuple[str, str, str, str, str]:
    """Normalize a heterogenous input into the canonical hash tuple.

    Accepts:
      - the t10_applicator `Resolution` dataclass (duck-typed by
        attribute access; we never import it here)
      - a plain dict with keys 'rec_row_id', 'broker_order_id',
        'filled_qty', 'filled_price'
      - a pandas Series row with the same fields
    """
    if hasattr(item, "rec_row_id") and hasattr(item, "broker_order_id"):
        rec_row_id = getattr(item, "rec_row_id")
        broker_oid = getattr(item, "broker_order_id")
        filled_qty = getattr(item, "filled_qty", 0)
        filled_px  = getattr(item, "filled_price", 0)
        ticker     = getattr(item, "ticker", "")
    elif isinstance(item, dict):
        rec_row_id = item.get("rec_row_id") or item.get("RecRowId")
        broker_oid = item.get("broker_order_id") or item.get("BrokerOrderId")
        filled_qty = item.get("filled_qty") or item.get("ExecutedShares") or item.get("Shares")
        filled_px  = item.get("filled_price") or item.get("ExecutedPrice") or item.get("Price")
        ticker     = item.get("ticker") or item.get("Ticker") or ""
    else:
        # pandas Series-like
        try:
            rec_row_id = item.get("RecRowId", item.get("rec_row_id"))
            broker_oid = item.get("BrokerOrderId", item.get("broker_order_id"))
            filled_qty = item.get("ExecutedShares", item.get("Shares", item.get("filled_qty", 0)))
            filled_px  = item.get("ExecutedPrice", item.get("Price", item.get("filled_price", 0)))
            ticker     = item.get("Ticker", item.get("ticker", ""))
        except Exception as e:  # noqa: BLE001
            raise TypeError(
                f"compute_apply_batch_id: unsupported item type {type(item).__name__}: {e}"
            )
    return (
        str(rec_row_id),
        str(broker_oid),
        str(ticker).upper(),
        f"{float(filled_qty or 0.0):.4f}",
        f"{float(filled_px or 0.0):.6f}",
    )


def compute_apply_batch_id(run_id: str, items: Iterable[Any]) -> str:
    """Deterministic SHA-256-based id for ONE attempted apply batch.

    The id is invariant under list reordering: items are sorted by
    ``(rec_row_id, broker_order_id)`` before hashing. Same execution
    set → same batch id forever.
    """
    norm: List[Tuple[str, str, str, str, str]] = [_coerce_batch_item(i) for i in items]
    if not norm:
        # Empty batch is its own deterministic id.
        body = f"{run_id}|<empty>"
    else:
        norm.sort()
        body = run_id + "|" + "|".join(
            f"{rec}:{oid}:{tic}:{qty}:{px}" for rec, oid, tic, qty, px in norm
        )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return f"apply-{digest}"


# ──────────────────────────────────────────────────────────────────────
# Journal read / write
# ──────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def journal_path(run_dir: Path) -> Path:
    return Path(run_dir) / JOURNAL_FILE


def read_journal(run_dir: Path) -> List[Dict[str, Any]]:
    """Return every JSONL row in file order. Tolerates a partial last
    line (skips it). Empty list if the file does not exist yet."""
    p = journal_path(run_dir)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def write_marker(
    run_dir: Path,
    *,
    batch_id: str,
    status: str,
    **payload: Any,
) -> Dict[str, Any]:
    """Append one JSONL row. Returns the written record (with `ts`).

    `status` should be one of: ``"started"``, ``"applied"``,
    ``"recovery"``, ``"aborted"``.
    """
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    record: Dict[str, Any] = {
        "schema_version": "autotrade_t10_apply_attempt/v1",
        "ts": _now_iso(),
        "batch_id": batch_id,
        "status": status,
    }
    record.update(payload)
    p = journal_path(run_dir)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return record


def latest_status_for_batch(run_dir: Path, batch_id: str) -> Optional[str]:
    """Last status logged for ``batch_id``, or None if it has never
    appeared in the journal. Insertion order is the source of truth."""
    last: Optional[str] = None
    for row in read_journal(run_dir):
        if row.get("batch_id") == batch_id:
            last = row.get("status")
    return last


@dataclass(frozen=True)
class JournalState:
    """Snapshot of the relevant prior journal state for one batch."""
    prior_status: Optional[str]
    started_at: Optional[str]
    applied_at: Optional[str]
    all_for_batch: List[Dict[str, Any]] = field(default_factory=list)


def inspect_batch(run_dir: Path, batch_id: str) -> JournalState:
    rows = [r for r in read_journal(run_dir) if r.get("batch_id") == batch_id]
    if not rows:
        return JournalState(prior_status=None, started_at=None, applied_at=None,
                            all_for_batch=[])
    started = next((r for r in rows if r.get("status") == "started"), None)
    applied = next((r for r in rows if r.get("status") == "applied"), None)
    return JournalState(
        prior_status=rows[-1].get("status"),
        started_at=started.get("ts") if started else None,
        applied_at=applied.get("ts") if applied else None,
        all_for_batch=rows,
    )


# ──────────────────────────────────────────────────────────────────────
# Local-Excel duplicate detection (defense in depth)
# ──────────────────────────────────────────────────────────────────────
def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def local_duplicate_present(
    *,
    executed_df: pd.DataFrame,
    history_df: pd.DataFrame,
    today_str: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a list of (Ticker, Action, Price, Shares) tuples in
    ``executed_df`` that already appear in today's slice of
    ``history_df``. An empty list means no local-Excel duplicates
    were detected.

    The check is intentionally conservative: a same-day BUY of the same
    ticker at the same price+shares is treated as a duplicate even if
    the BrokerOrderId is not stored in History.Notes (the current
    `apply_partial_execution` writes "" in Notes). Real same-day
    re-buys at the same price+shares are rare in this account's
    strategy; if it does happen, the operator can override via
    ``--allow-duplicate-apply``.
    """
    today_str = today_str or _today_str()
    if history_df is None or history_df.empty or executed_df is None or executed_df.empty:
        return []
    if "Date" not in history_df.columns:
        return []
    today_hist = history_df.loc[history_df["Date"].astype(str) == today_str]
    if today_hist.empty:
        return []
    dupes: List[Dict[str, Any]] = []
    for _, row in executed_df.iterrows():
        ticker = str(row.get("Ticker", "")).upper()
        action = str(row.get("Action", ""))
        try:
            price = round(float(row.get("ExecutedPrice", row.get("Price", 0.0))), 4)
        except Exception:  # noqa: BLE001
            price = 0.0
        try:
            shares = int(float(row.get("ExecutedShares", row.get("Shares", 0))))
        except Exception:  # noqa: BLE001
            shares = 0
        mask = (
            (today_hist["Ticker"].astype(str).str.upper() == ticker)
            & (today_hist["Action"].astype(str) == action)
            & (today_hist["Shares"].astype(float).round(0).astype(int) == shares)
            & (today_hist["Price"].astype(float).round(4) == price)
        )
        if bool(mask.any()):
            dupes.append({
                "Ticker": ticker, "Action": action,
                "Price": price, "Shares": shares,
            })
    return dupes


# ──────────────────────────────────────────────────────────────────────
# Holdings backup (cheap recovery affordance)
# ──────────────────────────────────────────────────────────────────────
def backup_holdings_log(
    holdings_log_path: Path,
    run_dir: Path,
    *,
    run_id: str,
    batch_id: str,
) -> Optional[Path]:
    """Copy ``holdings_log.xlsx`` into the run dir before mutation.

    Returns the backup path (or None if the source does not exist).
    Backup filename: ``holdings_log.preapply.<run_id>.<batch_id>.xlsx``.
    Existing backups are NOT overwritten — a rerun for the same batch
    keeps the original backup.
    """
    src = Path(holdings_log_path)
    if not src.exists():
        return None
    dest = Path(run_dir) / f"{HOLDINGS_BACKUP_PREFIX}.{run_id}.{batch_id}.xlsx"
    if dest.exists():
        return dest
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
