"""Round 3 (P2.B) — Order state model.

Codex Round-3 §3 P2 prescribed a small state model before any
cancel/replace/reprice work begins. Today this is **types-only** —
no persistence, no I/O, no broker call. The point is:

1. Codify the lifecycle every order goes through so callers can stop
   conflating "submitted" with "filled".
2. Make partial fills and open orders first-class values rather than
   ad-hoc bookkeeping inside the round-trip scripts.
3. Give the future reprice/cancel paths a known target shape they can
   produce without inventing yet another schema.

What still does NOT exist yet
-----------------------------
- a JSONL state log on disk
- a `transition()` validator (allowed → allowed)
- broker-row → OrderRecord conversion glue (will live with the new
  cancel/replace endpoints, not here)
- recovery / restart semantics

When those are wired, they will adopt the dataclasses below. The
**dataclasses themselves are frozen** to make it obvious that creating
a new state means producing a new value, not mutating an existing one
— which is the right default for an audit-friendly trade pipeline.

State transition diagram (textual)
----------------------------------
```
intent_created
   ├──> dry_run          (terminal; no broker contact)
   └──> submitted
            ├──> open_or_pending
            │       ├──> partially_filled
            │       │       ├──> partially_filled         (re-entry; qty grows)
            │       │       ├──> filled                    (terminal)
            │       │       └──> cancel_requested
            │       │              └──> cancelled          (terminal)
            │       ├──> filled                            (terminal)
            │       ├──> cancel_requested
            │       │       └──> cancelled                 (terminal)
            │       └──> replace_requested
            │              └──> replaced                   (re-entry with new ODNO)
            ├──> rejected                                  (terminal)
            └──> unknown                                   (recovery target)
```

Allowed transitions are encoded in `ALLOWED_TRANSITIONS` and validated
by `assert_transition()`. Callers do not have to use the validator —
it exists so anyone who *does* want to wire state mutation can do so
without re-deriving the legal graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────
# 1. Enumeration of states
# ──────────────────────────────────────────────────────────────────────
class OrderState(str, Enum):
    """v0 lifecycle. String-valued so it serializes cleanly to JSON
    without an Enum-aware decoder. Wire format is the string value."""

    INTENT_CREATED     = "intent_created"
    DRY_RUN            = "dry_run"
    SUBMITTED          = "submitted"
    OPEN_OR_PENDING    = "open_or_pending"
    PARTIALLY_FILLED   = "partially_filled"
    FILLED             = "filled"
    CANCEL_REQUESTED   = "cancel_requested"
    CANCELLED          = "cancelled"
    REPLACE_REQUESTED  = "replace_requested"
    REPLACED           = "replaced"
    REJECTED           = "rejected"
    UNKNOWN            = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in {OrderState.DRY_RUN, OrderState.FILLED,
                        OrderState.CANCELLED, OrderState.REJECTED}

    @property
    def is_active_at_broker(self) -> bool:
        """True iff this state describes an order that *could* still
        change quantity at the broker."""
        return self in {OrderState.SUBMITTED, OrderState.OPEN_OR_PENDING,
                        OrderState.PARTIALLY_FILLED,
                        OrderState.CANCEL_REQUESTED,
                        OrderState.REPLACE_REQUESTED}


# ──────────────────────────────────────────────────────────────────────
# 2. Source-of-update enumeration (audit metadata, not a state)
# ──────────────────────────────────────────────────────────────────────
class StatusSource(str, Enum):
    """Where did the *current* status come from? Used so a downstream
    consumer (audit viewer, reconcile) can tell apart a state we
    *believe* vs a state the broker confirmed."""

    LOCAL_INTENT    = "local_intent"
    PLACE_ORDER_ACK = "place_order_ack"
    NCCS_ECHO       = "nccs_echo"
    CCNL_ECHO       = "ccnl_echo"
    POSITION_DELTA  = "position_delta"   # inferred via positions/cash move
    CANCEL_ACK      = "cancel_ack"
    REPLACE_ACK     = "replace_ack"
    OPERATOR        = "operator"         # manual override
    UNKNOWN         = "unknown"


# ──────────────────────────────────────────────────────────────────────
# 3. Allowed transitions (legal directed edges)
# ──────────────────────────────────────────────────────────────────────
def _t(src: OrderState, *dsts: OrderState) -> Tuple[OrderState, FrozenSet[OrderState]]:
    return src, frozenset(dsts)


ALLOWED_TRANSITIONS: Dict[OrderState, FrozenSet[OrderState]] = dict([
    _t(OrderState.INTENT_CREATED,
       OrderState.DRY_RUN, OrderState.SUBMITTED, OrderState.REJECTED),
    _t(OrderState.DRY_RUN),                       # terminal
    _t(OrderState.SUBMITTED,
       OrderState.OPEN_OR_PENDING, OrderState.PARTIALLY_FILLED,
       OrderState.FILLED, OrderState.REJECTED, OrderState.UNKNOWN),
    _t(OrderState.OPEN_OR_PENDING,
       OrderState.PARTIALLY_FILLED, OrderState.FILLED,
       OrderState.CANCEL_REQUESTED, OrderState.REPLACE_REQUESTED,
       OrderState.UNKNOWN),
    _t(OrderState.PARTIALLY_FILLED,
       OrderState.PARTIALLY_FILLED,               # re-entry as fills grow
       OrderState.FILLED, OrderState.CANCEL_REQUESTED,
       OrderState.UNKNOWN),
    _t(OrderState.FILLED),                        # terminal
    _t(OrderState.CANCEL_REQUESTED,
       OrderState.CANCELLED, OrderState.PARTIALLY_FILLED,
       OrderState.FILLED, OrderState.UNKNOWN),
    _t(OrderState.CANCELLED),                     # terminal
    _t(OrderState.REPLACE_REQUESTED,
       OrderState.REPLACED, OrderState.OPEN_OR_PENDING,
       OrderState.PARTIALLY_FILLED, OrderState.FILLED,
       OrderState.UNKNOWN),
    _t(OrderState.REPLACED,
       OrderState.OPEN_OR_PENDING, OrderState.PARTIALLY_FILLED,
       OrderState.FILLED, OrderState.CANCEL_REQUESTED, OrderState.UNKNOWN),
    _t(OrderState.REJECTED),                      # terminal
    _t(OrderState.UNKNOWN,
       OrderState.OPEN_OR_PENDING, OrderState.PARTIALLY_FILLED,
       OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED),
])


class IllegalStateTransition(ValueError):
    """Raised when assert_transition() is called with an edge that does
    not appear in ALLOWED_TRANSITIONS."""


def assert_transition(prev: OrderState, nxt: OrderState) -> None:
    legal = ALLOWED_TRANSITIONS.get(prev, frozenset())
    if nxt not in legal:
        raise IllegalStateTransition(
            f"{prev.value!r} → {nxt.value!r} is not a legal transition. "
            f"Allowed: {sorted(s.value for s in legal) or '(terminal)'}"
        )


# ──────────────────────────────────────────────────────────────────────
# 4. Per-order record (frozen)
# ──────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class OrderRecord:
    """Single order, snapshotted at one point in time.

    `state` is the **current** state per `status_source`. `qty_filled` +
    `qty_remaining` should equal `qty_intended` once the order leaves
    SUBMITTED/OPEN_OR_PENDING. A new fact (broker echo, cancel ack, …)
    produces a *new* OrderRecord with the next state, not a mutation.

    Notes on fields:
        run_id, rec_row_id : lineage into the artifact recommendation row.
        ticker, side, qty_intended, limit_price : the *intent* values.
        broker_order_id : ODNO returned by /order. None for DRY_RUN.
        client_order_id : adapter-generated correlation id.
        status_source   : last source of update.
        submitted_at    : when the order was first sent (UTC ISO-8601).
        last_checked_at : when we last asked the broker for state (UTC).
        raw_broker_row  : whichever broker row produced the current state
                          — `inquire-nccs` row, `inquire-ccnl` row, place
                          ack body, cancel ack body, etc. Kept opaque.
    """
    run_id: str
    rec_row_id: int
    ticker: str
    market: str
    side: str
    qty_intended: int
    limit_price: float
    broker_order_id: Optional[str]
    client_order_id: str
    state: OrderState
    status_source: StatusSource
    qty_filled: float = 0.0
    qty_remaining: float = 0.0
    submitted_at: Optional[str] = None
    last_checked_at: str = field(default_factory=_now_iso)
    raw_broker_row: Optional[Dict[str, Any]] = None
    note: str = ""

    def with_state(
        self,
        new_state: OrderState,
        *,
        status_source: StatusSource,
        qty_filled: Optional[float] = None,
        qty_remaining: Optional[float] = None,
        raw_broker_row: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
        broker_order_id: Optional[str] = None,
        skip_validation: bool = False,
    ) -> "OrderRecord":
        """Return a NEW OrderRecord at `new_state`. Validates that the
        edge is legal unless `skip_validation` is set (used by recovery
        / operator paths)."""
        if not skip_validation:
            assert_transition(self.state, new_state)
        return OrderRecord(
            run_id=self.run_id,
            rec_row_id=self.rec_row_id,
            ticker=self.ticker,
            market=self.market,
            side=self.side,
            qty_intended=self.qty_intended,
            limit_price=self.limit_price,
            broker_order_id=broker_order_id if broker_order_id is not None
                            else self.broker_order_id,
            client_order_id=self.client_order_id,
            state=new_state,
            status_source=status_source,
            qty_filled=self.qty_filled if qty_filled is None else qty_filled,
            qty_remaining=self.qty_remaining if qty_remaining is None
                          else qty_remaining,
            submitted_at=self.submitted_at,
            last_checked_at=_now_iso(),
            raw_broker_row=raw_broker_row if raw_broker_row is not None
                           else self.raw_broker_row,
            note=note if note is not None else self.note,
        )


# ──────────────────────────────────────────────────────────────────────
# 5. Convenience constructors
# ──────────────────────────────────────────────────────────────────────
def new_intent(
    *,
    run_id: str, rec_row_id: int, ticker: str, market: str, side: str,
    qty_intended: int, limit_price: float, client_order_id: str,
    note: str = "",
) -> OrderRecord:
    """Initial record at INTENT_CREATED — i.e. resolved from artifact
    but not yet handed to `place_order`."""
    return OrderRecord(
        run_id=run_id, rec_row_id=rec_row_id, ticker=ticker.upper(),
        market=market, side=side, qty_intended=qty_intended,
        limit_price=limit_price, broker_order_id=None,
        client_order_id=client_order_id,
        state=OrderState.INTENT_CREATED,
        status_source=StatusSource.LOCAL_INTENT,
        qty_filled=0.0, qty_remaining=float(qty_intended),
        submitted_at=None,
        raw_broker_row=None, note=note,
    )


def reduce_history(records: List[OrderRecord]) -> OrderRecord:
    """Pick the most recent record in a list (audit log usage). Tie-breaker
    is `last_checked_at`. Caller is responsible for grouping by
    (run_id, rec_row_id) before calling this."""
    if not records:
        raise ValueError("reduce_history: empty list")
    return max(records, key=lambda r: r.last_checked_at)


# ══════════════════════════════════════════════════════════════════════
# R8-B — CCNL-based order state classifier
# ══════════════════════════════════════════════════════════════════════
# What this is
# ------------
# R6 proved that paper `inquire-nccs` is unreliable for open-order
# visibility, so R8 makes `inquire-ccnl` the canonical source of truth.
# R8-day-1 (cancel-path probe) discovered an important contract: KIS
# paper does NOT mutate the original ccnl row to mark it cancelled.
# Instead, a sibling row is appended:
#
#   original row (ODNO = target):
#       ft_ord_qty=N, ft_ccld_qty=0, nccs_qty=0 (was N)
#       rvse_cncl_dvsn='00', rvse_cncl_dvsn_name='보통'   ← NOT flipped
#
#   sibling cancel row (new ODNO):
#       orgn_odno = target
#       rvse_cncl_dvsn='02', rvse_cncl_dvsn_name='취소'
#       sll_buy_dvsn_cd_name='매수취소'
#
# Therefore a robust paper CCNL parser must scan **all** rows passed in,
# not just the row whose `odno == target`. This classifier accepts the
# full ccnl row list (or a single matched row) and looks for a sibling
# cancel row before deciding between FILLED / OPEN / CANCELLED.

# Field-alias maps (R8 §4 "Field aliases to support").
_QTY_ORDERED_KEYS    = ("ft_ord_qty3", "ft_ord_qty", "ord_qty")
_QTY_FILLED_KEYS     = ("ft_ccld_qty3", "ft_ccld_qty", "tot_ccld_qty", "ccld_qty")
_QTY_REMAINING_KEYS  = ("nccs_qty", "ord_psbl_qty", "rmn_qty")
_PRICE_FILL_KEYS     = ("ft_ccld_unpr3", "ft_ccld_unpr", "ccld_unpr")
_PRICE_LIMIT_KEYS    = ("ft_ord_unpr3", "ovrs_ord_unpr", "ord_unpr")
_REJECT_REASON_KEYS  = ("rjct_rson", "rjct_rson_name")
_PROC_STATUS_KEYS    = ("prcs_stat_name",)
_CANCEL_DVSN_KEYS    = ("rvse_cncl_dvsn",)
_CANCEL_NAME_KEYS    = ("rvse_cncl_dvsn_name",)
_SIDE_NAME_KEYS      = ("sll_buy_dvsn_cd_name",)


def _ccnl_get_float(row: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    """Return the first non-empty value across `keys`, parsed as float.
    Returns None if no key has a parseable value."""
    for k in keys:
        v = row.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _ccnl_get_str(row: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v in (None, ""):
            continue
        return str(v).strip()
    return ""


@dataclass(frozen=True)
class BrokerOrderState:
    """Frozen snapshot of one broker order's status, as derived from
    one or more ccnl rows.

    This is **not** a lifecycle record — it is a single classification
    decision at one point in time. The order_manager glues this with
    `OrderRecord.with_state(...)` to advance the lifecycle.

    Field meanings:
        broker_order_id  : ODNO surface form as it appeared in `row`
                           (could be padded or stripped — both are valid,
                           caller normalizes for matching).
        normalized_odno  : `normalize_odno(broker_order_id)`.
        symbol           : PDNO from the row.
        side             : Korean-named side string (`'매수'`/`'매도'` …).
        ordered_qty      : ft_ord_qty3 / ft_ord_qty / ord_qty.
        filled_qty       : ft_ccld_qty3 / ft_ccld_qty / tot_ccld_qty / ccld_qty.
        remaining_qty    : nccs_qty / ord_psbl_qty / rmn_qty (best guess).
        avg_fill_price   : ft_ccld_unpr3 / ft_ccld_unpr / ccld_unpr.
        limit_price      : ft_ord_unpr3 / ovrs_ord_unpr / ord_unpr.
        state            : the assigned OrderState value.
        source           : always `"ccnl"` here; field exists so future
                           live-only nccs paths can reuse the shape.
        cancel_row_odno  : when `state == CANCELLED`, the new ODNO that
                           KIS assigned to the cancel instruction row.
                           None otherwise.
        raw              : the original (target) ccnl row, for audit.
        note             : human-readable reason for the classification,
                           especially in UNKNOWN cases.
    """
    broker_order_id: str
    normalized_odno: str
    symbol: str
    side: str
    ordered_qty: Optional[float]
    filled_qty: Optional[float]
    remaining_qty: Optional[float]
    avg_fill_price: Optional[float]
    limit_price: Optional[float]
    state: OrderState
    source: str = "ccnl"
    cancel_row_odno: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


def _find_cancel_sibling(
    *,
    target_odno_normalized: str,
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the ccnl row that represents a cancel instruction issued
    against `target_odno_normalized`, or None.

    Detection contract (per R8 day-1 finding):
      - row.orgn_odno (after normalization) == target_odno_normalized
      - AND any of:
          row.rvse_cncl_dvsn == '02'
          row.rvse_cncl_dvsn_name endswith '취소'
          row.sll_buy_dvsn_cd_name endswith '취소'  (e.g. '매수취소')
    """
    from phase3.autotrade.order_ids import normalize_odno
    for r in rows:
        orgn = normalize_odno(r.get("orgn_odno", ""))
        if not orgn or orgn != target_odno_normalized:
            continue
        cncl_code = _ccnl_get_str(r, _CANCEL_DVSN_KEYS)
        cncl_name = _ccnl_get_str(r, _CANCEL_NAME_KEYS)
        side_name = _ccnl_get_str(r, _SIDE_NAME_KEYS)
        if (
            cncl_code == "02"
            or cncl_name.endswith("취소")
            or side_name.endswith("취소")
        ):
            return r
    return None


def classify_ccnl_row(
    target_row: Dict[str, Any],
    *,
    all_rows: Optional[List[Dict[str, Any]]] = None,
    position_delta: Optional[float] = None,
    target_odno: Optional[str] = None,
) -> BrokerOrderState:
    """Classify one ccnl row into a 6-state `BrokerOrderState`.

    Args:
        target_row: the ccnl row whose `odno` matches the order we're
            asking about. Use `phase3.autotrade.order_ids.normalize_odno`
            to find it among the full ccnl page.
        all_rows: the full ccnl row list (or a slice). Required for
            CANCELLED detection because KIS represents cancels as
            sibling rows, not in-place edits. If None, cancel detection
            falls back to the target row's own cancel marker only.
        position_delta: signed change in the underlying position since
            the order was submitted (positive for BUY fills). Used to
            disambiguate ccnl-zero-fill vs position-moved (R5B-P2.3
            conflict-detection rule).
        target_odno: optional explicit ODNO to use as the sibling-cancel
            target. Defaults to `target_row.get("odno")`. Provide this
            when the caller is matching on a normalized odno but the
            row uses a different surface form.

    Decision table (in priority order):
        1. reject_reason present                → REJECTED
        2. sibling cancel row exists            → CANCELLED
        3. ordered>0 AND filled>=ordered        → FILLED
        4. ordered>0 AND 0<filled<ordered       → PARTIALLY_FILLED
        5. filled==0 AND ccnl-zero-vs-pos-move  → UNKNOWN (R5B-P2.3)
        6. filled==0 AND remaining>0            → OPEN_OR_PENDING
        7. filled==0 AND remaining==0
           AND no cancel sibling                → UNKNOWN
        8. ambiguous / missing fields           → UNKNOWN

    Never raises. UNKNOWN is the safe default whenever fields contradict
    each other or are missing.
    """
    from phase3.autotrade.order_ids import normalize_odno

    bo_id = str(target_row.get("odno", "") or "").strip()
    norm = normalize_odno(target_odno if target_odno is not None else bo_id)

    ordered = _ccnl_get_float(target_row, _QTY_ORDERED_KEYS)
    filled  = _ccnl_get_float(target_row, _QTY_FILLED_KEYS)
    remain  = _ccnl_get_float(target_row, _QTY_REMAINING_KEYS)
    fill_px = _ccnl_get_float(target_row, _PRICE_FILL_KEYS)
    limit   = _ccnl_get_float(target_row, _PRICE_LIMIT_KEYS)

    reject_reason = _ccnl_get_str(target_row, _REJECT_REASON_KEYS)
    proc_status   = _ccnl_get_str(target_row, _PROC_STATUS_KEYS)

    # Always look up a cancel sibling row up-front so we can surface
    # ``cancel_row_odno`` on the result regardless of which terminal
    # state we end up in. The PARTIALLY_FILLED branch needs this so
    # callers (e.g. order_manager) can tell apart a "still open partial"
    # vs a "partial fill whose remainder was cancelled".
    sibling_rows = all_rows if all_rows is not None else [target_row]
    cancel_sib = _find_cancel_sibling(
        target_odno_normalized=norm, rows=sibling_rows,
    )
    cancel_sib_odno = (
        normalize_odno(cancel_sib.get("odno", "")) if cancel_sib is not None else None
    ) or None

    base_kwargs: Dict[str, Any] = dict(
        broker_order_id=bo_id,
        normalized_odno=norm,
        symbol=_ccnl_get_str(target_row, ("pdno",)),
        side=_ccnl_get_str(target_row, _SIDE_NAME_KEYS),
        ordered_qty=ordered,
        filled_qty=filled,
        remaining_qty=remain,
        avg_fill_price=fill_px if (fill_px and fill_px > 0.0) else None,
        limit_price=limit,
        source="ccnl",
        raw=dict(target_row),
        cancel_row_odno=cancel_sib_odno,
    )

    # 1. Explicit REJECT.
    if reject_reason:
        return BrokerOrderState(
            **base_kwargs,
            state=OrderState.REJECTED,
            note=f"rejected: {reject_reason}",
        )

    # 2. CANCELLED via sibling cancel row — only when the original row
    # had ZERO filled qty. A non-zero filled qty plus a cancel sibling
    # is reported as PARTIALLY_FILLED with `cancel_row_odno` populated
    # so the caller can tell the remainder is no longer working.
    # We treat a cancel sibling as authoritative ONLY if the target row's
    # filled qty is zero. If KIS already filled some shares before the
    # cancel landed, that's a partial fill on the original order — the
    # sibling exists but the original order's economic outcome is partial.
    if cancel_sib is not None and (filled is None or filled <= 0.0):
        cancel_oid = cancel_sib_odno
        # Conflict guard: if position moved despite ccnl saying zero
        # fill AND a cancel sibling, classify UNKNOWN — operator must
        # investigate (the broker likely partially filled then cancelled
        # the remainder, but the row hasn't been updated yet).
        if position_delta is not None and abs(position_delta) > 0.0:
            return BrokerOrderState(
                **base_kwargs,
                state=OrderState.UNKNOWN,
                note=(
                    f"cancel sibling present (ODNO {cancel_oid}) but "
                    f"position moved by {position_delta:+.4f} — "
                    f"possible partial-fill-before-cancel; operator review required"
                ),
            )
        return BrokerOrderState(
            **base_kwargs,
            state=OrderState.CANCELLED,
            note=f"cancel sibling row ODNO {cancel_oid}",
        )

    # 3-4. FILLED / PARTIALLY_FILLED — needs both qtys present.
    if ordered is not None and ordered > 0.0 and filled is not None:
        if filled >= ordered:
            return BrokerOrderState(
                **base_kwargs,
                state=OrderState.FILLED,
                note="filled_qty >= ordered_qty",
            )
        if 0.0 < filled < ordered:
            return BrokerOrderState(
                **base_kwargs,
                state=OrderState.PARTIALLY_FILLED,
                note=f"partial fill {filled}/{ordered}",
            )

    # 5. Conflict: ccnl says zero fill BUT position moved (R5B-P2.3).
    if (
        filled is not None and filled == 0.0
        and position_delta is not None
        and abs(position_delta) > 0.0
    ):
        return BrokerOrderState(
            **base_kwargs,
            state=OrderState.UNKNOWN,
            note=(
                f"ccnl filled_qty=0 but position moved by {position_delta:+.4f}"
                " — broker echo lags; do not auto-classify"
            ),
        )

    # 6. OPEN_OR_PENDING — zero filled, positive remaining.
    if (
        filled is not None and filled == 0.0
        and remain is not None and remain > 0.0
    ):
        return BrokerOrderState(
            **base_kwargs,
            state=OrderState.OPEN_OR_PENDING,
            note=f"filled=0, remaining={remain}",
        )

    # 7-8. Anything else is UNKNOWN.
    return BrokerOrderState(
        **base_kwargs,
        state=OrderState.UNKNOWN,
        note=(
            f"ambiguous ccnl row: ordered={ordered!r} filled={filled!r} "
            f"remaining={remain!r} proc_status={proc_status!r}"
        ),
    )


def classify_from_full_ccnl(
    rows: List[Dict[str, Any]],
    *,
    target_odno: str,
    position_delta: Optional[float] = None,
) -> BrokerOrderState:
    """Convenience: find the target row by normalized ODNO across a
    full ccnl page and run `classify_ccnl_row` against it.

    Returns an UNKNOWN BrokerOrderState if the target row is not found.
    """
    from phase3.autotrade.order_ids import normalize_odno
    norm = normalize_odno(target_odno)
    target_row: Optional[Dict[str, Any]] = None
    for r in rows:
        if normalize_odno(r.get("odno", "")) == norm:
            target_row = r
            break
    if target_row is None:
        return BrokerOrderState(
            broker_order_id=str(target_odno or "").strip(),
            normalized_odno=norm,
            symbol="",
            side="",
            ordered_qty=None,
            filled_qty=None,
            remaining_qty=None,
            avg_fill_price=None,
            limit_price=None,
            state=OrderState.UNKNOWN,
            source="ccnl",
            raw={},
            note=f"ODNO {target_odno!r} not in ccnl page (rows={len(rows)})",
        )
    return classify_ccnl_row(
        target_row,
        all_rows=rows,
        position_delta=position_delta,
        target_odno=target_odno,
    )
