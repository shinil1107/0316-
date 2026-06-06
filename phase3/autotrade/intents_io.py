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

# V1-G.2 — Strategy parity.  The autotrade MUST submit the exact
# same actions the backtest simulator (``SimPortfolio.apply_actions``)
# executes, or backtested performance and live performance diverge
# by construction.  ``exits.RecosAction.FULL_CLOSE`` and
# ``PARTIAL_CLOSE`` are the canonical dispatch sets used by both
# the simulator (simulator.py:157, 172) and t10_applicator
# (t10_applicator.py:328); importing them here keeps the three call
# sites in lock-step.  Any new D2 / D4 / D-future SELL_* or TRIM_*
# variant added to ``RecosAction`` is automatically picked up by
# both backtest and live, without having to remember to update
# this file.
#
# Buy side: T7 emits ONLY ``BUY_NEW`` and ``BUY_MORE`` (see
# daily_runner.py:1082).  Bare ``"BUY"`` is never produced — it was
# a phantom action in the pre-V1-G filter that no recommendations.csv
# row could ever match.  Dropping it makes the filter byte-equal to
# the simulator's ``action in ("BUY_NEW", "BUY_MORE")`` check at
# simulator.py:190.
#
# Warning-only (``SELL_GRACE``) is intentionally excluded — the
# grace family escalates through TRIM_GRACE (step-1 partial) and
# then SELL (full close) when the grace window expires; SELL_GRACE
# itself is just the "we noticed but won't act yet" record.
from exits import RecosAction  # noqa: E402

# Frozenset → tuple at import time so the filter is hashable + cheap
# to iterate.  ``sorted`` keeps the on-disk filter stable for
# diffs / audit-trail purposes.
_BUY_ACTIONS_DEFAULT: Tuple[str, ...] = ("BUY_NEW", "BUY_MORE")
_SELL_ACTIONS_DEFAULT: Tuple[str, ...] = tuple(sorted(
    RecosAction.FULL_CLOSE | RecosAction.PARTIAL_CLOSE
))
_VALID_SIDES: Tuple[str, ...] = ("BUY", "SELL")


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
    # V1-G — SELL is allowed at this layer. The autotrade pipeline
    # generates SELL rows for STOP_LOSS / SELL / TRIM actions out of
    # the T7 recommendations.csv (SELL_GRACE intentionally excluded —
    # see _SELL_ACTIONS_DEFAULT). The downstream broker adapter and
    # the t10_applicator have always understood both sides; the only
    # historical blocker was this validator. Anything outside the
    # {BUY, SELL} pair is still rejected so a stray TRIM action that
    # leaked the action code into ``side`` raises here instead of
    # going on the wire.
    side_upper = str(row.get("side", "")).upper()
    if side_upper not in _VALID_SIDES:
        return False, (
            f"side must be one of {_VALID_SIDES} "
            f"(got side={row.get('side')!r})"
        )
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
def _make_intent_row(
    *,
    side: str,
    client_order_id: str,
    symbol: str,
    qty: int,
    limit_price: float,
    market: str = "NASD",
    rec_row_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Internal generic builder shared by ``make_buy_intent_row`` and
    ``make_sell_intent_row``. Both wrappers exist so call sites read
    intent-fully (BUY vs SELL) and so a future direction-specific
    field (e.g. SELL's stop_price for STOP_LIMIT orders) has a
    natural home — but the canonical row shape and validation are
    one place."""
    side_u = str(side).upper()
    if side_u not in _VALID_SIDES:
        raise ValueError(
            f"side must be one of {_VALID_SIDES}, got {side!r}")
    if rec_row_id is None:
        rec_row_id = rec_row_id_from_client_order_id(client_order_id)
    row: Dict[str, Any] = {
        "client_order_id": str(client_order_id),
        "symbol": str(symbol).upper(),
        "market": str(market),
        "side": side_u,
        "qty": int(qty),
        "ord_type": "LIMIT",
        "limit_price": float(limit_price),
    }
    if rec_row_id is not None:
        row["rec_row_id"] = int(rec_row_id)
    ok, why = _validate_row(row)
    if not ok:
        raise ValueError(f"intent row rejected: {why}")
    return row


def make_buy_intent_row(
    *,
    client_order_id: str,
    symbol: str,
    qty: int,
    limit_price: float,
    market: str = "NASD",
    rec_row_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Construct one BUY intent row in the canonical shape. Raises
    ValueError on obvious mistakes so the operator can't accidentally
    write a zero-share or zero-price file.

    R10E: ``rec_row_id`` becomes an explicit on-disk field so the
    autotrade pipeline can thread it through manage_order without
    re-parsing it out of client_order_id. When the caller omits it
    we try to recover it from the client_order_id pattern
    ``co-<run_id>-<rec_row_id>-B-<qty>-<ticker>`` written by
    ``build_intent_client_order_id``; if that fails the field is
    left absent and the loader will fall back to 0 (the old shape).
    """
    return _make_intent_row(
        side="BUY",
        client_order_id=client_order_id, symbol=symbol,
        qty=qty, limit_price=limit_price, market=market,
        rec_row_id=rec_row_id,
    )


def make_sell_intent_row(
    *,
    client_order_id: str,
    symbol: str,
    qty: int,
    limit_price: float,
    market: str = "NASD",
    rec_row_id: Optional[int] = None,
) -> Dict[str, Any]:
    """V1-G counterpart of ``make_buy_intent_row``. Same canonical
    row shape with ``side="SELL"``. Validation and rec_row_id recovery
    behave identically — the client_order_id pattern
    ``co-<run_id>-<rec_row_id>-S-<qty>-<ticker>`` is the SELL twin
    of the BUY ``-B-`` form and ``rec_row_id_from_client_order_id``
    already accepts both side markers."""
    return _make_intent_row(
        side="SELL",
        client_order_id=client_order_id, symbol=symbol,
        qty=qty, limit_price=limit_price, market=market,
        rec_row_id=rec_row_id,
    )


def rec_row_id_from_client_order_id(cid: str) -> Optional[int]:
    """Best-effort recovery of ``rec_row_id`` from the canonical
    client_order_id pattern ``co-<run_id>-<rec_row_id>-B-<qty>-<ticker>``.

    Returns the parsed int on success, or ``None`` if the pattern is
    unrecognisable. Used both by ``make_buy_intent_row`` (when the
    caller did not pass rec_row_id explicitly) and by
    ``default_intents_loader`` in ``daily_runner`` (when reading
    older intent files written before R10E).

    The pattern is anchored on the ``-B-<qty>-<ticker>`` tail rather
    than on the run_id, because run_ids can contain underscores and
    dashes; we walk back from the tail instead.
    """
    if not cid or not isinstance(cid, str):
        return None
    parts = cid.split("-")
    # need at least co + run_id + rid + B + qty + ticker = 6 segments
    if len(parts) < 6:
        return None
    if parts[0] != "co":
        return None
    # walk back from the tail: ticker, qty, side, rec_row_id
    side_pos = None
    for i in range(len(parts) - 1, 1, -1):
        if parts[i] in ("B", "S"):
            side_pos = i
            break
    if side_pos is None or side_pos < 2:
        return None
    rid_part = parts[side_pos - 1]
    try:
        return int(rid_part)
    except (TypeError, ValueError):
        return None


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


@dataclass(frozen=True)
class SellCandidate:
    """One SELL-eligible row from ``recommendations.csv``. V1-G twin
    of ``BuyCandidate``; kept as a separate type (rather than adding
    a ``side`` field to BuyCandidate) so call sites that loop over
    "buys" never accidentally pick up a SELL row and vice versa.

    ``reco_shares`` here is the size of the SELL (T7 emits the full
    held-shares count for STOP_LOSS / SELL and a partial count for
    TRIM). ``reco_price`` is the close-price reference; the actual
    SELL limit is derived from the live bid at submit-time, with
    ``reco_price`` only used as a fallback when the live quote
    pipeline fails.
    """
    run_id: str
    rec_row_id: int
    ticker: str
    action: str            # SELL | SELL_NEW | STOP_LOSS | TRIM
    reco_shares: int       # share count to SELL (T7's Shares column)
    reco_price: float      # close-ref for fallback limit
    rank: Optional[int] = None
    regime: str = ""
    market: str = "NASD"
    actionable: bool = True
    raw_row: Dict[str, str] = field(default_factory=dict)


def load_sell_candidates(
    run_dir: Path,
    *,
    sell_actions: Tuple[str, ...] = _SELL_ACTIONS_DEFAULT,
    market: str = "NASD",
) -> List[SellCandidate]:
    """V1-G counterpart of ``load_buy_candidates``. Read
    ``<run_dir>/recommendations.csv`` and return the rows the
    autotrade should submit as SELL.

    Filters:
      - Action in ``sell_actions`` (default {SELL, SELL_NEW,
        STOP_LOSS, TRIM}). **SELL_GRACE is intentionally NOT in the
        default tuple** — see _SELL_ACTIONS_DEFAULT.
      - Actionable == True if column exists (else True)
      - Shares > 0   (T7 emits 0 for placeholder rows; never sell 0)
      - Price > 0

    BUY_NEW / BUY_MORE / HOLD / DEFERRED / SELL_GRACE rows are
    dropped here so a single ``load_buy_candidates`` +
    ``load_sell_candidates`` pair partitions actionable
    recommendations cleanly without overlap.
    """
    p = recommendations_csv_path(run_dir)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    out: List[SellCandidate] = []
    reader = csv.DictReader(text.splitlines())
    fieldnames = reader.fieldnames or []
    has_actionable_col = "Actionable" in fieldnames
    for row in reader:
        action = (row.get("Action") or "").strip().upper()
        if action not in sell_actions:
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
        out.append(SellCandidate(
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
    # Ordering: STOP_LOSS first (most urgent — PnL cut takes
    # precedence over everything), then other FULL_CLOSE variants
    # (SELL / SELL_PEAK_DD / SELL_PROFIT / ...), then PARTIAL_CLOSE
    # variants (TRIM / TRIM_GRACE / TRIM_PROFIT / ...).  Within each
    # tier we stable-sort by rec_row_id then ticker so the on-disk
    # intent file order is deterministic.  The paper broker submits
    # sequentially, so this order is also the execution order.
    def _action_tier(action: str) -> int:
        if action == RecosAction.STOP_LOSS:
            return 0
        if RecosAction.is_full_close(action):
            return 1
        if RecosAction.is_partial_close(action):
            return 2
        return 99
    out.sort(key=lambda c: (
        _action_tier(c.action),
        c.rec_row_id, c.ticker,
    ))
    return out


_CID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def build_intent_client_order_id(
    *, run_id: str, rec_row_id: int, ticker: str, qty: int,
    side: str = "BUY",
) -> str:
    """Deterministic, human-readable client_order_id.

    R10B §3.5 shape:  ``co-<run_id>-<rec_row_id>-<B|S>-<qty>-<ticker>``

    All segments are sanitized to ``[A-Za-z0-9_-]`` and the final
    string is capped at 80 chars (KIS doesn't constrain ``ord_seq``
    on the wire, but a short id is easier on operators and logs).
    This is intentionally different from ``order_store.build_client_order_id``
    which uses a sha256 prefix; the two coexist because the human-readable
    form is for the manual paper-acceptance trail, and the hashed form
    is for the duplicate guard.

    V1-G: ``side`` controls the third-from-tail segment.
    ``side="BUY"`` -> ``-B-`` (backwards-compatible default);
    ``side="SELL"`` -> ``-S-``. ``rec_row_id_from_client_order_id``
    already walks back from either marker, so on-disk parity with
    older files is preserved on BUY and SELL recovery just works.
    """
    side_u = str(side).upper()
    if side_u not in _VALID_SIDES:
        raise ValueError(
            f"side must be one of {_VALID_SIDES}, got {side!r}")
    side_marker = "B" if side_u == "BUY" else "S"

    def _clean(seg: str) -> str:
        seg = str(seg).strip().replace(" ", "-")
        seg = _CID_SANITIZE_RE.sub("", seg)
        return seg or "x"

    cid = (
        f"co-{_clean(run_id)}-{_clean(str(int(rec_row_id)))}"
        f"-{side_marker}-{_clean(str(int(qty)))}-{_clean(ticker)}"
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
        rec_row_id=int(candidate.rec_row_id),
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


# ──────────────────────────────────────────────────────────────────────
# Batch helpers (R10C — "submit everything in one shot" workflow)
# ──────────────────────────────────────────────────────────────────────

# R10D-3 / R10F-Q1 — quote-fresh limit metadata. ``quote_source`` is one of:
#   "reco_close"          -> reco_price * (1 + pad), no quote attempted
#   "quote_refreshed"     -> quote-binding limit (see below)
#   "quote_refreshed_below_reco" -> in floor mode, reco_padded won the max()
#   "fallback_quote_fail" -> quote_fn was provided but raised / returned None
#   "fallback_quote_zero" -> quote_fn returned a Quote whose ref price is <= 0
#
# R10F-Q1: ``quote_only`` parameter controls floor behaviour.
#   quote_only=False (R10D-3 legacy): chosen = max(reco_padded, quote_padded).
#       Reco_close acts as a price floor — protects against bad quote data
#       but exposes operator to gap-down risk (we keep buying near yesterday's
#       close even after today's price dropped 2%).
#   quote_only=True  (R10F-Q1, recommended): chosen = quote_padded when the
#       quote is healthy. Reco_close is used only when quote_fn fails or
#       returns a non-positive ref price (the existing fallback paths).
#       Gap-down risk goes away; gap-up risk is unchanged (we still pay the
#       live ask + pad).
# These are stored in each intent row under ``_quote_source`` so the
# post-trade email and the audit JSONL can show how each limit was set.

_QUOTE_SOURCE_KEY = "_quote_source"
_QUOTE_REF_PRICE_KEY = "_quote_ref_price"
_QUOTE_ASOF_KEY = "_quote_asof"


@dataclass(frozen=True)
class IntentBuildWarning:
    """One row-level warning produced by ``candidates_to_intent_rows``
    when quote refreshing was requested but fell back to the close.

    The UI surface uses ``ticker`` + ``reason`` to highlight problem
    rows in the confirmation dialog; the audit JSONL preserves the
    same info via the ``_quote_source`` field on the row itself.
    """
    ticker: str
    reason: str


def _resolve_quote_ref(quote: Any) -> Optional[float]:
    """Extract a reference price from a Quote-like object. We prefer
    ``ask`` (the operator pays the ask on a marketable BUY), then
    ``last``, then fall back to None so the caller can flag the row.
    """
    if quote is None:
        return None
    ask = getattr(quote, "ask", None)
    if ask is not None and float(ask) > 0:
        return float(ask)
    last = getattr(quote, "last", None)
    if last is not None and float(last) > 0:
        return float(last)
    return None


def _resolve_quote_ref_for_sell(quote: Any) -> Optional[float]:
    """V1-G — sell-side counterpart of ``_resolve_quote_ref``. A
    marketable SELL hits the bid, so we prefer ``bid`` first, then
    ``last``, then None. The limit price is then *padded down* by
    ``quote_pad_pct`` so the order is comfortably marketable even
    if the bid ticks down by a few cents between quote and submit.
    """
    if quote is None:
        return None
    bid = getattr(quote, "bid", None)
    if bid is not None and float(bid) > 0:
        return float(bid)
    last = getattr(quote, "last", None)
    if last is not None and float(last) > 0:
        return float(last)
    return None


_GAP_FILTER_WARN_PREFIX = "gap_filter"


def candidates_to_intent_rows(
    candidates: List[BuyCandidate],
    *,
    limit_pad_pct: float = 0.0,
    qty_override: Optional[int] = None,
    quote_fn: Optional[Any] = None,
    quote_pad_pct: float = 0.1,
    quote_only: bool = False,
    gap_filter_max_pct: Optional[float] = None,
    warnings_out: Optional[List[IntentBuildWarning]] = None,
) -> List[Dict[str, Any]]:
    """Build a full intent batch from every supplied ``BuyCandidate``.

    Each row uses ``candidate.reco_shares`` for qty (unless
    ``qty_override`` is given — useful for "tiny test" batches) and
    a limit derived from the reco close, the live quote, or both.

    Pricing modes:

    * ``quote_fn is None`` — pure ``reco_close`` mode:
      ``limit = reco_price * (1 + limit_pad_pct/100)``.

    * ``quote_fn`` provided, ``quote_only=False`` (R10D-3 legacy floor
      mode): ``limit = max(reco_padded, quote_padded)`` where
      ``quote_padded = quote_ref * (1 + quote_pad_pct/100)``.
      Protects against bad quote data at the cost of accepting
      gap-down risk (we keep buying near yesterday's close even when
      today's price dropped meaningfully).

    * ``quote_fn`` provided, ``quote_only=True`` (R10F-Q1, recommended):
      ``limit = quote_padded`` when the quote is healthy. The reco
      close is used only when the quote function raises, returns
      ``None``, or returns a non-positive reference price — same
      fallback paths as the floor mode. This removes the gap-down
      mispricing observed on R10E (NYSE symbols filling well above
      market when the live broker quote came back cleaner than the
      reco close).

    On quote failure ``IntentBuildWarning`` is appended to
    ``warnings_out`` (when provided). The row's ``_quote_source`` field
    records which path was taken.

    ``limit_pad_pct=1.0`` means "+1% above reco_price". A positive pad
    is the only direction that makes sense for BUY (paying a bit more
    to get filled). Negative pads are accepted but caller-beware
    (the UI rejects them for the full-auto path).

    ``quote_pad_pct`` may be **negative** on BUY (V1-I): a small
    negative pad starts the limit *slightly below the live ask* so the
    order rests passively and the manage-loop reprice ladder chases up
    toward / past the ask. This starts the order at the live market
    instead of yesterday's stale close (see V1-I design doc §1.4).

    ``gap_filter_max_pct`` (V1-I): when set AND a healthy live quote is
    available, a candidate whose live ref price has gapped UP more than
    this percentage versus its reco close is **dropped** (no row
    emitted) — buying a +N% gap is not a behaviour the backtest ever
    validated, so we refuse rather than chase it. Gap-DOWN is never
    filtered (cheaper than reco is fine for a BUY). When the quote
    pipeline fails the candidate is kept (priced at the reco close
    fallback) because we cannot compute a gap without a quote. Each
    drop appends an ``IntentBuildWarning`` whose reason starts with
    ``"gap_filter"`` so the caller can count / report them.
    """
    rows: List[Dict[str, Any]] = []
    reco_pad = 1.0 + float(limit_pad_pct) / 100.0
    qpad = 1.0 + float(quote_pad_pct) / 100.0
    for c in candidates:
        reco_limit = round(float(c.reco_price) * reco_pad, 4)
        quote_source = "reco_close"
        quote_ref_price: Optional[float] = None
        quote_asof: Optional[str] = None
        chosen_limit = reco_limit

        if quote_fn is not None:
            q = None
            failed = False
            try:
                q = quote_fn(c.ticker, c.market)
            except Exception as e:  # noqa: BLE001
                failed = True
                if warnings_out is not None:
                    warnings_out.append(IntentBuildWarning(
                        ticker=c.ticker,
                        reason=f"quote lookup failed: {type(e).__name__}: {e}",
                    ))
                quote_source = "fallback_quote_fail"
            if not failed and q is None:
                if warnings_out is not None:
                    warnings_out.append(IntentBuildWarning(
                        ticker=c.ticker,
                        reason="quote lookup failed: returned None",
                    ))
                quote_source = "fallback_quote_fail"
            elif q is not None:
                quote_ref_price = _resolve_quote_ref(q)
                quote_asof = getattr(q, "asof", None)
                if quote_ref_price is None or quote_ref_price <= 0:
                    if warnings_out is not None:
                        warnings_out.append(IntentBuildWarning(
                            ticker=c.ticker,
                            reason="quote returned non-positive ref price",
                        ))
                    quote_source = "fallback_quote_zero"
                    quote_ref_price = None
                else:
                    # V1-I gap filter — refuse to BUY a name that has
                    # gapped UP more than the cap versus its reco close.
                    # Only fires with a HEALTHY quote (we cannot compute
                    # a gap on the fallback path). Gap-down is allowed.
                    if gap_filter_max_pct is not None:
                        reco_px = float(c.reco_price)
                        if reco_px > 0:
                            gap_pct = (quote_ref_price - reco_px) / reco_px * 100.0
                            if gap_pct > float(gap_filter_max_pct):
                                if warnings_out is not None:
                                    warnings_out.append(IntentBuildWarning(
                                        ticker=c.ticker,
                                        reason=(
                                            f"{_GAP_FILTER_WARN_PREFIX}: "
                                            f"+{gap_pct:.1f}% vs reco "
                                            f"({reco_px:.4f}->{quote_ref_price:.4f}) "
                                            f"> {float(gap_filter_max_pct):.0f}% cap "
                                            f"— dropped (not bought)"),
                                    ))
                                continue
                    quote_limit = round(quote_ref_price * qpad, 4)
                    if quote_only:
                        # R10F-Q1: trust the live quote. Reco close
                        # acts only as a sanity fallback when the
                        # quote pipeline fails (see branches above).
                        chosen_limit = quote_limit
                        quote_source = "quote_only"
                    else:
                        # R10D-3 legacy floor mode.
                        chosen_limit = max(reco_limit, quote_limit)
                        if chosen_limit > reco_limit:
                            quote_source = "quote_refreshed"
                        else:
                            quote_source = "quote_refreshed_below_reco"

        row = candidate_to_intent_row(
            c,
            qty_override=qty_override,
            limit_price=chosen_limit,
        )
        row[_QUOTE_SOURCE_KEY] = quote_source
        if quote_ref_price is not None:
            row[_QUOTE_REF_PRICE_KEY] = quote_ref_price
        if quote_asof:
            row[_QUOTE_ASOF_KEY] = str(quote_asof)
        rows.append(row)
    return rows


def write_intent_file_from_candidates(
    run_dir: Path,
    candidates: List[BuyCandidate],
    *,
    limit_pad_pct: float = 0.0,
    qty_override: Optional[int] = None,
    quote_fn: Optional[Any] = None,
    quote_pad_pct: float = 0.1,
    quote_only: bool = False,
    warnings_out: Optional[List[IntentBuildWarning]] = None,
    overwrite: bool = False,
    run_id: Optional[str] = None,
) -> Path:
    """Batch counterpart of ``write_intent_file_from_candidate`` —
    serialize ALL given candidates into one ``submitted_intents.json``
    in one shot. Refuses an empty list outright so the operator can't
    accidentally clobber an existing intent file with nothing.

    R10D-3: when ``quote_fn`` is supplied, every row's limit is also
    lifted toward the broker's current ask. See
    ``candidates_to_intent_rows`` for the exact rule.

    R10F-Q1: ``quote_only`` (default False for backwards compatibility)
    drops the reco-close floor so a healthy live quote alone decides
    the limit. The UI sets this True by default. Falls back to the
    reco close when the quote pipeline fails (same paths as legacy).

    The resulting rows carry a ``_quote_source`` field so the
    post-trade audit / email can show how each limit was set.
    """
    if not candidates:
        raise ValueError(
            "write_intent_file_from_candidates: candidates is empty — "
            "refusing to write an empty intent batch."
        )
    rows = candidates_to_intent_rows(
        candidates,
        limit_pad_pct=limit_pad_pct,
        qty_override=qty_override,
        quote_fn=quote_fn,
        quote_pad_pct=quote_pad_pct,
        quote_only=quote_only,
        warnings_out=warnings_out,
    )
    return write_submitted_intents(
        run_dir, rows,
        run_id=(run_id if run_id is not None else candidates[0].run_id),
        overwrite=overwrite,
    )


def sell_candidates_to_intent_rows(
    candidates: List[SellCandidate],
    *,
    limit_pad_pct: float = 0.0,
    quote_fn: Optional[Any] = None,
    quote_pad_pct: float = 0.1,
    quote_only: bool = True,
    warnings_out: Optional[List[IntentBuildWarning]] = None,
) -> List[Dict[str, Any]]:
    """V1-G — build SELL intent rows from a list of ``SellCandidate``.

    Pricing model (mirrors BUY but flips direction):

    * ``quote_fn is None`` — pure reco-close mode:
      ``limit = reco_price * (1 - limit_pad_pct/100)``.
      A positive ``limit_pad_pct`` *lowers* the SELL limit (= more
      aggressive marketable price). Negative is accepted but
      caller-beware (would raise the limit above the close).

    * ``quote_fn`` provided (recommended):
      ``limit = bid * (1 - quote_pad_pct/100)`` when the bid is
      healthy. On quote failure the reco close is used as a fallback
      and the row's ``_quote_source`` is set accordingly so the
      audit / mail can flag it.

    ``quote_only=True`` is the default here (opposite of BUY's
    historical default). For STOP_LOSS we want the live bid to drive
    the limit — using the close as a floor would mean *refusing to
    sell* if the price gapped down overnight, which is exactly the
    bug a stop-loss exists to prevent. The reco close still gets used
    as a fallback when the quote pipeline fails entirely.

    Note: ``qty`` here always comes from ``candidate.reco_shares``;
    we never pass a ``qty_override`` because the autotrade SELL
    workflow is "sell exactly what T7 told you to sell" (no partials
    invented at this layer — TRIM partials are already encoded in
    ``reco_shares`` by ``daily_runner``).
    """
    rows: List[Dict[str, Any]] = []
    reco_pad = 1.0 - float(limit_pad_pct) / 100.0
    qpad = 1.0 - float(quote_pad_pct) / 100.0
    for c in candidates:
        reco_limit = round(float(c.reco_price) * reco_pad, 4)
        quote_source = "reco_close"
        quote_ref_price: Optional[float] = None
        quote_asof: Optional[str] = None
        chosen_limit = reco_limit

        if quote_fn is not None:
            q = None
            failed = False
            try:
                q = quote_fn(c.ticker, c.market)
            except Exception as e:  # noqa: BLE001
                failed = True
                if warnings_out is not None:
                    warnings_out.append(IntentBuildWarning(
                        ticker=c.ticker,
                        reason=f"quote lookup failed: {type(e).__name__}: {e}",
                    ))
                quote_source = "fallback_quote_fail"
            if not failed and q is None:
                if warnings_out is not None:
                    warnings_out.append(IntentBuildWarning(
                        ticker=c.ticker,
                        reason="quote lookup failed: returned None",
                    ))
                quote_source = "fallback_quote_fail"
            elif q is not None:
                quote_ref_price = _resolve_quote_ref_for_sell(q)
                quote_asof = getattr(q, "asof", None)
                if quote_ref_price is None or quote_ref_price <= 0:
                    if warnings_out is not None:
                        warnings_out.append(IntentBuildWarning(
                            ticker=c.ticker,
                            reason="quote returned non-positive bid",
                        ))
                    quote_source = "fallback_quote_zero"
                    quote_ref_price = None
                else:
                    quote_limit = round(quote_ref_price * qpad, 4)
                    if quote_only:
                        chosen_limit = quote_limit
                        quote_source = "quote_only"
                    else:
                        # Floor mode for SELL = min(reco, quote) so we
                        # don't accidentally raise the limit ABOVE
                        # today's market. This is the safe direction
                        # for SELL but is uncommon — quote_only=True
                        # is the V1-G recommended default.
                        chosen_limit = min(reco_limit, quote_limit)
                        if chosen_limit < reco_limit:
                            quote_source = "quote_refreshed"
                        else:
                            quote_source = "quote_refreshed_above_reco"

        cid = build_intent_client_order_id(
            run_id=c.run_id, rec_row_id=c.rec_row_id,
            ticker=c.ticker, qty=int(c.reco_shares), side="SELL",
        )
        row = make_sell_intent_row(
            client_order_id=cid,
            symbol=c.ticker,
            market=c.market,
            qty=int(c.reco_shares),
            limit_price=chosen_limit,
            rec_row_id=int(c.rec_row_id),
        )
        # Capture the originating T7 Action (STOP_LOSS / SELL / TRIM)
        # so the post-trade audit and the R11B email can group rows
        # by action without having to re-read recommendations.csv.
        row["_t7_action"] = c.action
        row[_QUOTE_SOURCE_KEY] = quote_source
        if quote_ref_price is not None:
            row[_QUOTE_REF_PRICE_KEY] = quote_ref_price
        if quote_asof:
            row[_QUOTE_ASOF_KEY] = str(quote_asof)
        rows.append(row)
    return rows


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
