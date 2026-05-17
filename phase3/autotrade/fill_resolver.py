"""Round 4 (P5.3) — fill state resolver.

Codex R4 §5.3 prescribed:

    "Determine whether an order is actually filled.
     Prefer authoritative broker execution/history rows when available.
     In paper only, allow position/cash delta as a fallback estimate.
     Never treat nccs as a fill confirmation. nccs means open/pending."

This module is the **single place** where "is it filled?" is decided.
The orchestrator passes in the inputs it gathered (echo, pre/post pos,
pre/post cash, intent qty, limit, mode) and gets back a uniform
`FillResolution` it writes to the order_store and the execution report.

Policy summary
--------------
For mode == 'paper':
    1. ccnl echo match → state=FILLED, price from broker row
    2. nccs echo match → state=OPEN_OR_PENDING (NEVER FILLED via nccs)
    3. cash/pos delta matches intent → state=FILLED, price from cash_delta
    4. partial cash/pos delta → state=PARTIALLY_FILLED, price from cash_delta
    5. no movement, no echo → state=UNKNOWN

For mode == 'live':
    1. ccnl echo match → state=FILLED, price from broker row
    2. nccs echo match → state=OPEN_OR_PENDING
    3. pos delta matches intent → state=FILLED, but price unavailable
       (cash_delta on live is NOT authoritative — fees/FX/settlement
       complicate it. R4 §5.3 explicitly forbids using it as primary
       price for live.)
    4. partial pos delta → state=PARTIALLY_FILLED, price unavailable
    5. no movement, no echo → state=UNKNOWN

The resolver does **not** mutate anything and does **not** call the
broker. It is a pure function of its inputs, which makes it cheap to
unit-test and inspect in audit dumps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from phase3.autotrade.order_state import OrderState, StatusSource


@dataclass(frozen=True)
class FillResolution:
    """Output of the resolver. Mirrors the small subset of OrderRecord
    fields that change in a fill decision; orchestrator combines this
    with the intent context to log a full transition."""
    state:             OrderState
    status_source:     StatusSource
    qty_filled:        float
    qty_remaining:     float
    fill_price:        Optional[float]
    fill_price_source: Optional[str]   # 'broker_ccnl' | 'paper_cash_delta' | 'unavailable' | None
    note:              str = ""


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────
def _ccnl_fill_price(matched_row: Dict[str, Any]) -> Optional[float]:
    """Pull the first non-zero `*_unpr*` field that looks like a fill
    price out of a ccnl row."""
    for key in ("ft_ccld_unpr3", "ccld_unpr", "ft_ord_unpr3", "ord_unpr"):
        raw = matched_row.get(key)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return round(v, 4)
    return None


def _ccnl_filled_qty(matched_row: Dict[str, Any]) -> Optional[float]:
    """Pull the executed (filled) quantity from a ccnl row.

    Codex R5A-P2.4 fix:
    KIS has shipped at least two field names for the filled qty on
    overseas ccnl rows — `ft_ccld_qty3` (newer normalized name) and
    `ccld_qty` (legacy). We try both. A non-numeric or missing value
    returns None so the caller can decide whether to fall back to
    position-delta inference (paper) or mark UNKNOWN (live).

    Returns 0.0 explicitly when the row contains a numeric zero. A row
    that exists in ccnl with filled_qty=0 typically means "order is
    accepted, sitting open, no fill yet" — different from "row absent",
    so the caller can distinguish.
    """
    for key in ("ft_ccld_qty3", "ccld_qty"):
        raw = matched_row.get(key)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v < 0:
            continue
        return v
    return None


def _ccnl_order_qty(matched_row: Dict[str, Any]) -> Optional[float]:
    """Pull the ordered quantity from a ccnl row. Used as a sanity
    cross-check against the intent's qty_intended."""
    for key in ("ft_ord_qty3", "ord_qty"):
        raw = matched_row.get(key)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            return v
    return None


# ──────────────────────────────────────────────────────────────────────
# Main resolver
# ──────────────────────────────────────────────────────────────────────
def resolve_fill_state(
    *,
    mode: str,                        # 'paper' | 'live'
    echo: Dict[str, Any],
    pre_position_qty: int,
    post_position_qty: int,
    pre_cash_available: float,
    post_cash_available: float,
    qty_intended: int,
    limit_price: float,
) -> FillResolution:
    """Return a `FillResolution` summarizing whether the just-submitted
    order is filled / open / unknown, with the best fill-price source
    we have under the active mode's rules."""

    qty_delta = post_position_qty - pre_position_qty
    cash_delta = post_cash_available - pre_cash_available

    matched = bool(echo.get("matched"))
    source = echo.get("source") if matched else None
    matched_row = echo.get("matched_row") if matched else None

    # 1) ccnl echo → authoritative, but classify by actual filled qty
    #    (Codex R5A-P2.4 fix). A ccnl match is no longer assumed to be a
    #    full fill; we parse the broker's executed qty and split into
    #    FILLED / PARTIALLY_FILLED / OPEN_OR_PENDING / UNKNOWN.
    if matched and source == "ccnl" and matched_row is not None:
        broker_price = _ccnl_fill_price(matched_row)
        filled_qty_ccnl = _ccnl_filled_qty(matched_row)
        ordered_qty_ccnl = _ccnl_order_qty(matched_row)

        # 1a) ccnl row has a numeric filled qty → trust it
        if filled_qty_ccnl is not None:
            if filled_qty_ccnl >= float(qty_intended):
                return FillResolution(
                    state=OrderState.FILLED,
                    status_source=StatusSource.CCNL_ECHO,
                    qty_filled=float(qty_intended),
                    qty_remaining=0.0,
                    fill_price=broker_price,
                    fill_price_source="broker_ccnl",
                    note=("filled per ccnl row"
                          + ("" if broker_price else " (price missing from row)")),
                )
            if filled_qty_ccnl > 0:
                return FillResolution(
                    state=OrderState.PARTIALLY_FILLED,
                    status_source=StatusSource.CCNL_ECHO,
                    qty_filled=filled_qty_ccnl,
                    qty_remaining=float(qty_intended) - filled_qty_ccnl,
                    fill_price=broker_price,
                    fill_price_source="broker_ccnl",
                    note=(f"partial fill per ccnl: {filled_qty_ccnl}/{qty_intended}"
                          + ("" if broker_price else " (price missing)")),
                )
            # filled_qty_ccnl == 0 → accepted but not yet filled.
            # Codex R5B-P2.3 fix: cross-check against position_delta.
            # If the broker reports 0 fills BUT our position already
            # moved, the two sources disagree and we must NOT silently
            # call this OPEN_OR_PENDING — emit UNKNOWN with a conflict
            # note instead. Operator decides which side is authoritative.
            if qty_delta > 0:
                # Conflict: ccnl says open, position says filled.
                # In paper we can still surface an estimated price from
                # cash_delta (matches our paper fallback policy); in
                # live we deliberately leave fill_price unset because
                # cash_delta is not authoritative for live (R4 §5.3).
                est_price: Optional[float] = None
                if mode == "paper" and cash_delta < 0 and qty_delta > 0:
                    est_price = round(abs(cash_delta) / qty_delta, 4)
                return FillResolution(
                    state=OrderState.UNKNOWN,
                    status_source=StatusSource.CCNL_ECHO,
                    qty_filled=float(qty_delta),
                    qty_remaining=max(float(qty_intended) - float(qty_delta), 0.0),
                    fill_price=est_price,
                    fill_price_source=("paper_cash_delta"
                                       if est_price is not None else None),
                    note=(f"conflict: ccnl filled_qty=0 but position moved "
                          f"+{int(qty_delta)} (mode={mode}); broker state "
                          "ambiguous, verify before retry / auto-apply"),
                )
            if qty_delta < 0:
                # Conflict in the other direction: ccnl says open BUY,
                # but position dropped. Same treatment — UNKNOWN.
                return FillResolution(
                    state=OrderState.UNKNOWN,
                    status_source=StatusSource.CCNL_ECHO,
                    qty_filled=0.0,
                    qty_remaining=float(qty_intended),
                    fill_price=None,
                    fill_price_source=None,
                    note=(f"conflict: ccnl filled_qty=0 but position moved "
                          f"{int(qty_delta)} (mode={mode}); concurrent activity? "
                          "verify before retry"),
                )
            # Consistent (ccnl filled=0 AND no position movement)
            return FillResolution(
                state=OrderState.OPEN_OR_PENDING,
                status_source=StatusSource.CCNL_ECHO,
                qty_filled=0.0,
                qty_remaining=float(qty_intended),
                fill_price=None,
                fill_price_source=None,
                note="ccnl row found with 0 filled qty — order is open at broker",
            )

        # 1b) ccnl match but filled qty is missing/ambiguous.
        #     R5A-P2.4 policy:
        #       paper: fall through to position/cash delta inference
        #       live : treat as UNKNOWN so operator investigates
        if mode == "live":
            return FillResolution(
                state=OrderState.UNKNOWN,
                status_source=StatusSource.CCNL_ECHO,
                qty_filled=0.0,
                qty_remaining=float(qty_intended),
                fill_price=broker_price,
                fill_price_source="broker_ccnl" if broker_price else None,
                note=("live ccnl match but filled qty fields missing "
                      f"(ordered_qty_row={ordered_qty_ccnl}); refusing to "
                      "infer fill state from cash_delta on live"),
            )
        # paper: fall through (see step 3+ below). We keep the ccnl row
        # but rely on position delta for the actual qty.

    # 2) nccs echo → open/pending, NEVER fill (per Codex R4 §5.3)
    if matched and source == "nccs" and matched_row is not None:
        # nccs may carry partial-fill fields; surface them if present.
        norm = matched_row.get("_normalized") or {}
        qty_filled_nccs = float(norm.get("qty_filled") or 0.0)
        qty_remaining_nccs = float(norm.get("qty_remaining") or qty_intended)
        state = (OrderState.PARTIALLY_FILLED if qty_filled_nccs > 0
                 else OrderState.OPEN_OR_PENDING)
        return FillResolution(
            state=state,
            status_source=StatusSource.NCCS_ECHO,
            qty_filled=qty_filled_nccs,
            qty_remaining=qty_remaining_nccs,
            fill_price=None,
            fill_price_source=None,
            note="open per nccs (fill price not available from open list)",
        )

    # 3 / 4 / 5) no echo match → infer from position/cash deltas
    if qty_delta == 0:
        # Codex R4 §5.3: paper allows cash_delta but only with a real
        # position move. With no move and no echo, we don't know.
        return FillResolution(
            state=OrderState.UNKNOWN,
            status_source=StatusSource.UNKNOWN,
            qty_filled=0.0,
            qty_remaining=float(qty_intended),
            fill_price=None,
            fill_price_source=None,
            note="no echo match and no position movement — true state unknown",
        )

    # Position moved.
    if qty_delta >= qty_intended:
        qty_filled = float(qty_intended)
        qty_remaining = 0.0
        state = OrderState.FILLED
    elif qty_delta > 0:
        qty_filled = float(qty_delta)
        qty_remaining = float(qty_intended) - qty_filled
        state = OrderState.PARTIALLY_FILLED
    else:
        # Negative delta on a BUY would mean a concurrent SELL fired —
        # we can't attribute it to this order. Flag as unknown.
        return FillResolution(
            state=OrderState.UNKNOWN,
            status_source=StatusSource.UNKNOWN,
            qty_filled=0.0,
            qty_remaining=float(qty_intended),
            fill_price=None,
            fill_price_source=None,
            note=f"position moved in unexpected direction (Δ={qty_delta}); "
                 "concurrent activity? treating as unknown",
        )

    if mode == "paper":
        # Paper: cash_delta / qty is the practical fill price source
        # (corroborated across R2/R3 with 8 prior orders).
        est_price: Optional[float] = None
        if cash_delta < 0 and qty_delta > 0:
            est_price = round(abs(cash_delta) / qty_delta, 4)
        return FillResolution(
            state=state,
            status_source=StatusSource.POSITION_DELTA,
            qty_filled=qty_filled,
            qty_remaining=qty_remaining,
            fill_price=est_price,
            fill_price_source="paper_cash_delta" if est_price is not None else None,
            note="paper: position+cash delta authoritative for v0",
        )

    # mode == 'live'
    # R4 §5.3 explicitly: do not use cash_delta as primary fill price in
    # live. Position delta is enough to establish FILLED, but the price
    # field stays empty until an authoritative broker endpoint is wired.
    return FillResolution(
        state=state,
        status_source=StatusSource.POSITION_DELTA,
        qty_filled=qty_filled,
        qty_remaining=qty_remaining,
        fill_price=None,
        fill_price_source="unavailable",
        note="live: position moved but no broker fill-price endpoint wired yet; "
             "do NOT use cash_delta for live fill price",
    )
