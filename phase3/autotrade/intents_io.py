"""R10-1 — ``submitted_intents.json`` validation + helpers.

Why this exists
---------------
R9's ``daily_runner.default_intents_loader`` reads
``<run_dir>/submitted_intents.json`` and returns ``[]`` whenever the
file is missing, malformed, or contains zero BUY rows. That is safe
in isolation — manage_loop just doesn't submit anything — but it
also means the user can press *Paper Submit* and get a clean rc=0
"happy path" while exactly zero orders were placed.

R10 §3.3 calls that out explicitly:

    Do not silently submit zero orders.

This module owns the contract:

  - what shapes are valid in ``submitted_intents.json``
  - how the UI / CLI should describe the file's state
  - how to generate a one-shot file from a single BUY row (for the
    weekend pre-market test, where the operator doesn't want to
    hand-edit JSON)

Two consumers:

  - ``control_panel.compute_panel_state`` reads the status to drive
    the *Paper Submit* button enablement matrix.
  - ``daily_runner.main`` short-circuits paper-submit mode with rc=2
    when the file is missing / empty / malformed.

Wire shape
----------
The on-disk format is intentionally minimal — exactly the keys that
``OrderIntent`` needs plus a tiny wrapper so we can attach metadata
later without breaking parsers:

```json
{
  "schema_version": "intents/v1",
  "run_id": "20260516_001",
  "generated_at": "2026-05-16T22:00:00+00:00",
  "intents": [
    {
      "client_order_id": "co-20260516-001-B-1-0a1b2c",
      "symbol": "APA",
      "market": "NASD",
      "side": "BUY",
      "qty": 1,
      "ord_type": "LIMIT",
      "limit_price": 18.85
    }
  ]
}
```

Bare-list form ``[{...}, {...}]`` is also accepted for backwards
compatibility with anything the user may have already hand-written.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = "intents/v1"

# R10B §3.3 — BUY-only candidate filter. We intentionally keep this
# tuple local (not imported from `exits.RecosAction`) so a future
# rename of action codes can't silently expand the filter into SELL
# territory. R11 may centralize this.
_BUY_ACTIONS_DEFAULT: Tuple[str, ...] = ("BUY", "BUY_NEW", "BUY_MORE")


@dataclass(frozen=True)
class IntentFileStatus:
    """Outcome of inspecting ``<run_dir>/submitted_intents.json``.

    ``state`` is the single field both UI and CLI key on. Anything but
    ``"ok"`` means paper submit MUST be blocked. ``reason`` is a short
    human-readable string suitable for the UI's "disabled because…"
    tooltip and for the daily_runner's hard-stop note."""
    state: str          # one of: missing | unreadable | malformed | empty | ok
    reason: str = ""
    path: str = ""
    intent_count: int = 0
    buy_count: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return self.state == "ok"


# ──────────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────────
def intents_file_path(run_dir: Path) -> Path:
    return Path(run_dir) / "submitted_intents.json"


# ──────────────────────────────────────────────────────────────────────
# Read + validate
# ──────────────────────────────────────────────────────────────────────
def _coerce_rows(data: Any) -> Optional[List[Any]]:
    """Accept either ``{"intents": [...]}`` or a bare list. Anything
    else means "malformed" — the caller turns that into IntentFileStatus."""
    if isinstance(data, dict):
        rows = data.get("intents")
        if rows is None:
            return None
        return rows if isinstance(rows, list) else None
    if isinstance(data, list):
        return data
    return None


_REQUIRED_FIELDS = ("client_order_id", "symbol", "side", "qty")


def _validate_row(row: Any) -> Tuple[bool, str]:
    """Return (ok, reason) for a single intent row. Used both by the
    file-level validator and by ``write_submitted_intents`` so we have
    one definition of "valid"."""
    if not isinstance(row, dict):
        return False, "row is not a JSON object"
    for f in _REQUIRED_FIELDS:
        if row.get(f) in (None, ""):
            return False, f"missing required field: {f}"
    if str(row.get("side", "")).upper() != "BUY":
        return False, f"R10 supports BUY only (got side={row.get('side')!r})"
    if str(row.get("ord_type", "LIMIT")).upper() != "LIMIT":
        return False, f"only LIMIT orders allowed (got ord_type={row.get('ord_type')!r})"
    try:
        qty = int(row["qty"])
    except (TypeError, ValueError):
        return False, f"qty is not an integer: {row.get('qty')!r}"
    if qty <= 0:
        return False, f"qty must be > 0 (got {qty})"
    if row.get("limit_price") is None:
        return False, "limit_price is required for LIMIT orders"
    try:
        lp = float(row["limit_price"])
    except (TypeError, ValueError):
        return False, f"limit_price is not numeric: {row.get('limit_price')!r}"
    if lp <= 0:
        return False, f"limit_price must be > 0 (got {lp})"
    return True, ""


def validate_submitted_intents(run_dir: Path) -> IntentFileStatus:
    """Read + classify ``<run_dir>/submitted_intents.json``. Never
    raises. Caller should refuse paper-submit when ``state != "ok"``."""
    p = intents_file_path(run_dir)
    if not p.exists():
        return IntentFileStatus(
            state="missing",
            reason=f"submitted_intents.json not found at {p}",
            path=str(p),
        )
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return IntentFileStatus(
            state="unreadable",
            reason=f"cannot read {p}: {e}",
            path=str(p),
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return IntentFileStatus(
            state="malformed",
            reason=f"invalid JSON in {p}: {e}",
            path=str(p),
        )
    rows = _coerce_rows(data)
    if rows is None:
        return IntentFileStatus(
            state="malformed",
            reason=("expected an object with 'intents': [...] or a bare "
                    f"list, got {type(data).__name__}"),
            path=str(p),
        )
    if not rows:
        return IntentFileStatus(
            state="empty",
            reason="intents list is empty",
            path=str(p),
            intent_count=0, buy_count=0, rows=[],
        )

    valid_rows: List[Dict[str, Any]] = []
    bad: List[str] = []
    for i, r in enumerate(rows):
        ok, why = _validate_row(r)
        if ok:
            valid_rows.append(r)
        else:
            bad.append(f"row {i}: {why}")

    if bad:
        return IntentFileStatus(
            state="malformed",
            reason="; ".join(bad[:3]) + (f"; … (+{len(bad)-3} more)" if len(bad) > 3 else ""),
            path=str(p),
            intent_count=len(rows),
            buy_count=len(valid_rows),
            rows=valid_rows,
        )

    return IntentFileStatus(
        state="ok",
        reason="",
        path=str(p),
        intent_count=len(rows),
        buy_count=len(valid_rows),
        rows=valid_rows,
    )


# ──────────────────────────────────────────────────────────────────────
# Write helpers — for the weekend pre-market workflow
# ──────────────────────────────────────────────────────────────────────
def make_buy_intent_row(
    *,
    client_order_id: str,
    symbol: str,
    qty: int,
    limit_price: float,
    market: str = "NASD",
) -> Dict[str, Any]:
    """Construct one BUY intent row in the canonical shape. Raises
    ValueError on obvious mistakes so the operator can't accidentally
    write a zero-share or zero-price file."""
    row = {
        "client_order_id": str(client_order_id),
        "symbol": str(symbol).upper(),
        "market": str(market),
        "side": "BUY",
        "qty": int(qty),
        "ord_type": "LIMIT",
        "limit_price": float(limit_price),
    }
    ok, why = _validate_row(row)
    if not ok:
        raise ValueError(f"intent row rejected: {why}")
    return row


# ──────────────────────────────────────────────────────────────────────
# R10B — recommendations.csv → submitted_intents.json projector
#
# These helpers are the UI side of R10's intent contract. The dashboard
# loads candidates with `load_buy_candidates`, the operator picks one
# row + qty + limit, and `write_intent_file_from_candidate` persists a
# single-row file. Existing R10 validation continues to gate
# paper-submit, so this projector never touches the broker.
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BuyCandidate:
    """One BUY-eligible row from ``recommendations.csv``. Kept frozen
    so the UI can pass it around without surprise mutation. The
    ``raw_row`` field preserves the full CSV row for debugging /
    audit, but ``candidate_to_intent_row`` only uses the typed
    fields below."""
    run_id: str
    rec_row_id: int
    ticker: str
    action: str
    reco_shares: int
    reco_price: float
    rank: Optional[int] = None
    regime: str = ""
    market: str = "NASD"
    actionable: bool = True
    raw_row: Dict[str, str] = field(default_factory=dict)


def recommendations_csv_path(run_dir: Path) -> Path:
    return Path(run_dir) / "recommendations.csv"


def _coerce_int(s: Any, default: int = 0) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def _coerce_float(s: Any, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _coerce_bool(s: Any, default: bool = True) -> bool:
    """``recommendations.csv`` writes booleans as 'True'/'False' (pandas
    default). Be permissive on input — None / empty / unknown maps to
    the default (True), keeping pre-existing rows without an
    `Actionable` column compatible with the BUY filter."""
    if s is None:
        return default
    if isinstance(s, bool):
        return s
    s_str = str(s).strip().lower()
    if not s_str:
        return default
    return s_str in ("true", "1", "yes", "y", "t")


def load_buy_candidates(
    run_dir: Path,
    *,
    buy_actions: Tuple[str, ...] = _BUY_ACTIONS_DEFAULT,
    market: str = "NASD",
) -> List[BuyCandidate]:
    """Read ``<run_dir>/recommendations.csv`` and return the rows
    eligible for paper BUY submission. Returns ``[]`` if the CSV is
    missing or unreadable — the UI surfaces that as "no candidates".

    Filters (R10B §3.3):
      - Action in ``buy_actions`` (default {BUY, BUY_NEW, BUY_MORE})
      - Actionable == True if column exists (else assumed True)
      - Shares > 0
      - Price > 0

    SELL_GRACE / SELL / TRIM / HOLD / DEFERRED rows are dropped here
    so the UI dropdown can never accidentally surface them.
    """
    p = recommendations_csv_path(run_dir)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    out: List[BuyCandidate] = []
    reader = csv.DictReader(text.splitlines())
    fieldnames = reader.fieldnames or []
    has_actionable_col = "Actionable" in fieldnames
    for row in reader:
        action = (row.get("Action") or "").strip().upper()
        if action not in buy_actions:
            continue
        if has_actionable_col and not _coerce_bool(row.get("Actionable")):
            continue
        shares = _coerce_int(row.get("Shares"))
        price  = _coerce_float(row.get("Price"))
        if shares <= 0 or price <= 0:
            continue
        ticker = (row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        out.append(BuyCandidate(
            run_id=str(row.get("RunId") or "").strip(),
            rec_row_id=_coerce_int(row.get("RecRowId")),
            ticker=ticker,
            action=action,
            reco_shares=shares,
            reco_price=price,
            rank=(_coerce_int(row.get("Rank"), default=-1)
                  if row.get("Rank") not in (None, "") else None),
            regime=str(row.get("Regime") or "").strip(),
            market=market,
            actionable=(_coerce_bool(row.get("Actionable"))
                        if has_actionable_col else True),
            raw_row=dict(row),
        ))
    # Stable ordering: rank ascending if present, else recos order.
    out.sort(key=lambda c: ((c.rank if c.rank is not None else 10**9),
                              c.rec_row_id, c.ticker))
    return out


_CID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def build_intent_client_order_id(
    *, run_id: str, rec_row_id: int, ticker: str, qty: int,
) -> str:
    """Deterministic, human-readable client_order_id.

    R10B §3.5 shape:  ``co-<run_id>-<rec_row_id>-B-<qty>-<ticker>``

    All segments are sanitized to ``[A-Za-z0-9_-]`` and the final
    string is capped at 80 chars (KIS doesn't constrain ``ord_seq``
    on the wire, but a short id is easier on operators and logs).
    This is intentionally different from ``order_store.build_client_order_id``
    which uses a sha256 prefix; the two coexist because the human-readable
    form is for the manual paper-acceptance trail, and the hashed form
    is for the duplicate guard.
    """
    def _clean(seg: str) -> str:
        seg = str(seg).strip().replace(" ", "-")
        seg = _CID_SANITIZE_RE.sub("", seg)
        return seg or "x"

    cid = (
        f"co-{_clean(run_id)}-{_clean(str(int(rec_row_id)))}"
        f"-B-{_clean(str(int(qty)))}-{_clean(ticker)}"
    )
    if len(cid) > 80:
        cid = cid[:80]
    return cid


def candidate_to_intent_row(
    candidate: BuyCandidate,
    *,
    qty_override: Optional[int] = None,
    limit_price: Optional[float] = None,
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one canonical intent-file row from a ``BuyCandidate``.

    Defaults:
      qty       — caller's ``qty_override``, else the candidate's
                  ``reco_shares``. The R10 first-acceptance plan is
                  qty=1 so the operator typically passes ``qty_override=1``.
      limit_price — caller's value, else the candidate's ``reco_price``.

    Validation is delegated to ``make_buy_intent_row`` so the same
    rules that gate hand-edited files apply here too."""
    qty = int(qty_override if qty_override is not None else candidate.reco_shares)
    lp = float(limit_price if limit_price is not None else candidate.reco_price)
    cid = client_order_id or build_intent_client_order_id(
        run_id=candidate.run_id, rec_row_id=candidate.rec_row_id,
        ticker=candidate.ticker, qty=qty,
    )
    return make_buy_intent_row(
        client_order_id=cid,
        symbol=candidate.ticker,
        market=candidate.market,
        qty=qty,
        limit_price=lp,
    )


def write_intent_file_from_candidate(
    run_dir: Path,
    candidate: BuyCandidate,
    *,
    qty_override: Optional[int] = None,
    limit_price: Optional[float] = None,
    overwrite: bool = False,
    run_id: Optional[str] = None,
) -> Path:
    """One-shot helper used by the control-panel Generate-Intent-File
    button. Validates the candidate, builds the row, refuses to clobber
    an existing file unless ``overwrite=True``."""
    row = candidate_to_intent_row(
        candidate,
        qty_override=qty_override,
        limit_price=limit_price,
    )
    return write_submitted_intents(
        run_dir, [row],
        run_id=run_id if run_id is not None else candidate.run_id,
        overwrite=overwrite,
    )


def write_submitted_intents(
    run_dir: Path,
    intents: List[Dict[str, Any]],
    *,
    run_id: str = "",
    overwrite: bool = False,
) -> Path:
    """Persist ``intents`` to ``<run_dir>/submitted_intents.json`` in
    the canonical wrapper shape. Refuses to overwrite an existing file
    unless ``overwrite=True`` — this is intentional: the OrderStore
    duplicate guard keys on client_order_id, so re-writing intents
    silently is a footgun."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    p = intents_file_path(run_dir)
    if p.exists() and not overwrite:
        raise FileExistsError(
            f"{p} already exists. Pass overwrite=True if you really intend "
            f"to replace the intent list for run_id={run_id!r}."
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "intents": list(intents),
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                  encoding="utf-8")
    return p
