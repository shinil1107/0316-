"""Operator recovery helpers (R10F-S).

Companion to ``OrderStore.log_operator_cleared``. Given a stuck
client_order_id whose JSONL history is blocking new submits, the
operator can independently verify the broker's view of the underlying
ODNO and capture that verification in a structured payload that the
panel feeds to ``log_operator_cleared``.

We keep the broker probe in a tiny pure helper rather than as a method
on ``KisBrokerAdapter`` so:

* tests can pin the failure / success matrix without spinning up a
  real adapter, and
* the panel callback can wrap the result in a confirm dialog and an
  audit-friendly summary string with no Tk inside the probe itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from phase3.autotrade.order_state import (
    OrderState,
    classify_from_full_ccnl,
)


SAFE_TO_CLEAR_STATES = frozenset({
    "cancelled",
    "rejected",
    "absent",
    "no_broker_contact",
})


@dataclass(frozen=True)
class BrokerProbeResult:
    """Outcome of probing the broker for one stuck cid's ODNO.

    Fields:

    * ``broker_state_at_clear`` — canonical string the operator picks
      up and passes through to ``log_operator_cleared``. One of
      ``cancelled``, ``rejected``, ``filled``, ``open_or_pending``,
      ``no_broker_contact``, ``absent``, ``error``. The first four
      derive from ``classify_from_full_ccnl``; ``absent`` means the
      target ODNO was not found in any ccnl page; ``no_broker_contact``
      means the cid was blocked but the JSONL had no broker_order_id
      at all (e.g. duplicate-guard rows only).
    * ``safe_to_clear`` — convenience boolean computed from
      ``SAFE_TO_CLEAR_STATES``. The UI must refuse the clear button
      whenever this is False.
    * ``summary`` — single-line operator-facing string (used as the
      default ``operator_note`` and shown in the confirm dialog).
    * ``raw`` — full structured payload suitable for
      ``log_operator_cleared(broker_probe=...)``.
    """
    client_order_id: str
    broker_order_id: Optional[str]
    broker_state_at_clear: str
    safe_to_clear: bool
    summary: str
    raw: Dict[str, Any] = field(default_factory=dict)


class _AdapterLike(Protocol):
    """Structural slice of ``KisBrokerAdapter`` we use here — just
    ``get_order_history``. Tests pass a fake; the live panel passes
    the real adapter."""

    def get_order_history(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]: ...


def probe_broker_state(
    adapter: _AdapterLike,
    *,
    client_order_id: str,
    broker_order_id: Optional[str],
) -> BrokerProbeResult:
    """Look the cid's ODNO up in the broker's full ccnl page and return
    a ``BrokerProbeResult`` summarising what the broker thinks.

    The shape of the call surface is intentionally simple — we don't
    pass dates, sides, or symbols. That mirrors what the panel knows
    when it sees a stuck cid; everything else lives in the JSONL row
    the operator already inspected.

    Error handling:

    * ``broker_order_id is None`` → ``no_broker_contact`` (the cid was
      blocked by a duplicate-guard row that never reached the broker,
      so there is nothing for the broker to cancel).
    * ``adapter.get_order_history`` raises → ``broker_state_at_clear =
      'error'`` and ``safe_to_clear = False``; the UI must not allow a
      clear in that case (we have no proof the broker side is dead).
    """
    if not broker_order_id:
        return BrokerProbeResult(
            client_order_id=client_order_id,
            broker_order_id=None,
            broker_state_at_clear="no_broker_contact",
            safe_to_clear=True,
            summary=(
                "cid never reached the broker (no ODNO in JSONL); "
                "duplicate-guard / pre-submit failure only — safe to clear."
            ),
            raw={"reason": "no_broker_contact"},
        )

    try:
        rows = adapter.get_order_history()
    except Exception as e:  # noqa: BLE001
        return BrokerProbeResult(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            broker_state_at_clear="error",
            safe_to_clear=False,
            summary=(
                f"broker probe failed: {type(e).__name__}: {e}; "
                "do NOT clear without a successful probe."
            ),
            raw={"exception": f"{type(e).__name__}: {e}"},
        )

    bos = classify_from_full_ccnl(rows, target_odno=broker_order_id)
    state = bos.state
    state_str = _state_to_clear_string(state)

    # The matrix mirrors SAFE_TO_CLEAR_STATES above.
    safe = state_str in SAFE_TO_CLEAR_STATES

    filled = int(bos.filled_qty or 0)
    ordered = int(bos.ordered_qty or 0)
    summary = (
        f"ODNO={broker_order_id} broker_state={state_str} "
        f"filled={filled}/{ordered} "
        f"({'safe to clear' if safe else 'NOT safe to clear'})"
    )

    raw_payload: Dict[str, Any] = {
        "broker_order_id": broker_order_id,
        "ordered_qty": float(bos.ordered_qty or 0.0),
        "filled_qty": float(bos.filled_qty or 0.0),
        "remaining_qty": float(bos.remaining_qty or 0.0),
        "avg_fill_price": (
            float(bos.avg_fill_price) if bos.avg_fill_price is not None else None
        ),
        "ccnl_state": state.value if state is not None else None,
        "probe_rows_seen": len(rows),
    }

    return BrokerProbeResult(
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        broker_state_at_clear=state_str,
        safe_to_clear=safe,
        summary=summary,
        raw=raw_payload,
    )


def _state_to_clear_string(state: Optional[OrderState]) -> str:
    """Map ``OrderState`` (the broker's verdict) to the canonical
    clear-string used by ``log_operator_cleared``. UNKNOWN means the
    broker couldn't find the ODNO; we surface that as ``absent`` and
    treat it as safe to clear (the broker has nothing live for this
    cid)."""
    if state is None:
        return "absent"
    if state == OrderState.CANCELLED:
        return "cancelled"
    if state == OrderState.REJECTED:
        return "rejected"
    if state == OrderState.FILLED:
        return "filled"
    if state in (OrderState.OPEN_OR_PENDING, OrderState.PARTIALLY_FILLED,
                 OrderState.CANCEL_REQUESTED, OrderState.REPLACE_REQUESTED,
                 OrderState.REPLACED, OrderState.SUBMITTED):
        return "open_or_pending"
    if state == OrderState.UNKNOWN:
        return "absent"
    return state.value
