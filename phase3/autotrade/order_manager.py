"""R8-D — wait / cancel / reprice loop for a single order intent.

What this module is
-------------------
A small state machine that takes ONE ``OrderIntent`` and drives it to a
terminal outcome (filled / cancelled / rejected / unknown), polling the
broker via ``inquire-ccnl`` and acting per ``OrderManagementPolicy``.

The economic policy is intentionally conservative for R8:

  1. Only LIMIT orders. Market orders are refused outright
     (``allow_market_order=False`` and never overridden).
  2. Only BUY-side repricing. SELL partial fills stop the order; the
     remaining shares are cancelled, never repriced.
  3. ``UNKNOWN`` at any point stops the loop and emits a blocking
     UNKNOWN event. The order_store duplicate guard then prevents the
     same ``client_order_id`` from being resubmitted.
  4. Cancel must be confirmed (``CANCELLED`` via ccnl sibling row OR
     ``OPEN_OR_PENDING`` cleared with cash reserve released) before any
     reprice. An unconfirmed cancel does NOT reprice.

Time injection
--------------
``time_provider`` and ``sleep_fn`` are passed in so the unit tests can
drive deterministic ``monotonic`` clocks without real sleeps. Default
values use ``time.monotonic`` and ``time.sleep``.

Outputs
-------
- Every state transition is appended to the supplied ``OrderStore``
  (one JSONL line per transition).
- ``manage_order`` returns a ``ManagedOrderOutcome`` summarizing the
  terminal state, the final broker_order_id, qty filled, and how many
  cancels / reprices were issued.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from phase3.autotrade.kis_broker_adapter import (
    CancelResult,
    OrderIntent,
    PlacedOrder,
)
from phase3.autotrade.order_ids import normalize_odno
from phase3.autotrade.order_state import (
    BrokerOrderState,
    OrderState,
    StatusSource,
    classify_from_full_ccnl,
)
from phase3.autotrade.order_store import OrderStore


# ──────────────────────────────────────────────────────────────────────
# Policy + outcome value types
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OrderManagementPolicy:
    """R8 §6 policy. Defaults are conservative; tests inject tight values
    so the suite runs in milliseconds."""

    poll_interval_sec: float = 5.0
    max_wait_sec: float = 120.0
    max_reprice_attempts: int = 2
    reprice_step_bps: float = 10.0
    max_total_slippage_bps: float = 35.0
    cancel_before_reprice: bool = True
    cancel_confirm_wait_sec: float = 3.0
    allow_market_order: bool = False  # never set True in R8

    # R10D-1: KIS paper inquire-ccnl transient disconnect absorption.
    # Number of extra retries on a ccnl polling exception AFTER the
    # broker has already accepted the order. 0 reproduces the pre-R10D
    # one-shot behaviour. 2 retries with 2 s backoff covered every
    # disconnect observed in R10C Run 1 + Run 2.
    ccnl_poll_retry_count: int = 2
    ccnl_poll_retry_backoff_sec: float = 2.0


@dataclass(frozen=True)
class ManagedOrderOutcome:
    """Final terminal-ish summary returned by ``manage_order``.

    ``final_state`` is one of:
      FILLED / PARTIALLY_FILLED / OPEN_OR_PENDING / CANCELLED /
      REJECTED / UNKNOWN

    ``last_broker_order_id`` is the most recent KIS ODNO this order
    pipeline touched — for a successful reprice that's the LAST reprice
    submission, not the original ODNO. The original ODNO chain lives in
    the JSONL via ``parent_broker_order_id`` on each transition.
    """
    final_state: OrderState
    intent: OrderIntent
    last_broker_order_id: Optional[str]
    last_normalized_odno: str
    qty_filled: float
    qty_remaining: float
    avg_fill_price: Optional[float]
    last_limit_price: float
    cancel_attempts: int
    reprice_attempts: int
    elapsed_sec: float
    note: str
    classifications: List[BrokerOrderState] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Adapter protocol — the bits of KisBrokerAdapter we actually need
# ──────────────────────────────────────────────────────────────────────
class _AdapterLike(Protocol):
    """Structural subset of ``KisBrokerAdapter`` used by ``manage_order``.

    Defined explicitly so the unit tests can supply a ``FakeBroker``
    without inheriting from the real adapter."""

    def place_order(self, intent: OrderIntent, *, dry_run: bool = True) -> PlacedOrder: ...
    def get_order_history(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def cancel_order(
        self, *,
        broker_order_id: str,
        symbol: str,
        market: str = "NASD",
        qty: int,
        dry_run: bool = True,
        note: str = "",
    ) -> CancelResult: ...


# ──────────────────────────────────────────────────────────────────────
# Reprice math (R8 §6)
# ──────────────────────────────────────────────────────────────────────
def reprice_limit_buy(
    *,
    original_limit: float,
    current_limit: float,
    policy: OrderManagementPolicy,
) -> float:
    """Bumped BUY limit, never exceeding the original limit times the
    total-slippage cap. Returns the new price rounded to 4 decimals
    (KIS overseas price precision).

    Formula (R8 §6):
        step    = current_limit * (1 + reprice_step_bps / 10000)
        ceiling = original_limit * (1 + max_total_slippage_bps / 10000)
        new     = min(step, ceiling)
    """
    step    = current_limit * (1.0 + policy.reprice_step_bps    / 10_000.0)
    ceiling = original_limit * (1.0 + policy.max_total_slippage_bps / 10_000.0)
    new_price = min(step, ceiling)
    return round(new_price, 4)


def reprice_would_improve(*, current_limit: float, candidate: float,
                          eps: float = 1e-9) -> bool:
    """True iff the new limit is strictly above the current limit.

    Avoids the degenerate case where the slippage ceiling has already
    been reached and another reprice would just resubmit at the same
    price (which the broker would reject as a duplicate)."""
    return candidate > (current_limit + eps)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────
def _reprice_client_id(parent_cid: str, attempt: int) -> str:
    """Deterministic child client_order_id for a reprice. Suffixes the
    parent id with ``:rp<attempt>``. Stable across crashes — a recovery
    pass with the same parent + attempt will produce the same id and
    therefore hit the order_store duplicate guard."""
    return f"{parent_cid}:rp{int(attempt)}"


def _safe_log_transition(
    store: Optional[OrderStore],
    **kwargs: Any,
) -> None:
    """Forwarder that swallows store=None (so callers can use the manager
    without a persistence layer in tests). Adds R8 §6 lifecycle metadata
    fields under ``note`` when they don't fit ``log_transition`` natively
    (the store schema is closed; R8 adds reprice_attempt /
    parent_broker_order_id under note + raw_broker_row.extra)."""
    if store is None:
        return
    extra: Dict[str, Any] = kwargs.pop("extra", None) or {}
    raw = kwargs.get("raw_broker_row") or {}
    if extra:
        merged = dict(raw)
        merged.setdefault("_r8_extra", extra)
        kwargs["raw_broker_row"] = merged
    store.log_transition(**kwargs)


def _now_classify(
    adapter: _AdapterLike,
    *,
    broker_order_id: str,
    position_delta: Optional[float],
) -> BrokerOrderState:
    rows = adapter.get_order_history()
    return classify_from_full_ccnl(
        rows,
        target_odno=broker_order_id,
        position_delta=position_delta,
    )


# ──────────────────────────────────────────────────────────────────────
# R10D-1 — ccnl poll retry
# ──────────────────────────────────────────────────────────────────────
# R10C observed KIS paper's ``inquire-ccnl`` randomly raising
# ``ConnectionError('Connection aborted.', RemoteDisconnected(...))``
# 30~80 s after a successful broker accept. That single transient
# exception was enough to flip the manage loop into UNKNOWN ->
# rc=2 hard_stop, blocking T10 apply for the whole batch. R10D-1
# absorbs those transients with a small bounded retry budget while
# preserving today's conservative UNKNOWN behaviour when retries are
# exhausted.
#
# Pure helper (no Tk, no os.environ). The caller passes ``log_attempt``
# so each retry can leave an audit row through ``_safe_log_transition``
# without coupling this helper to the OrderStore.

def _now_classify_with_retry(
    adapter: _AdapterLike,
    *,
    broker_order_id: str,
    position_delta: Optional[float],
    retry_count: int,
    backoff_sec: float,
    sleep_fn: Callable[[float], None],
    log_attempt: Optional[Callable[[int, int, BaseException], None]] = None,
) -> BrokerOrderState:
    """Call ``_now_classify`` with up to ``retry_count`` retries on any
    ``Exception``. ``log_attempt(attempt_index, max_attempts, exc)`` is
    invoked exactly once per failed attempt (before sleeping for the
    next retry, and also for the final attempt that exhausts the
    budget). On final failure the original exception is re-raised so
    the caller's existing UNKNOWN bookkeeping fires unchanged.

    ``retry_count = 0`` reproduces the previous one-shot behaviour
    exactly (no extra calls, same exception path).
    """
    if retry_count < 0:
        retry_count = 0
    max_attempts = retry_count + 1
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            return _now_classify(
                adapter,
                broker_order_id=broker_order_id,
                position_delta=position_delta,
            )
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if log_attempt is not None:
                try:
                    log_attempt(attempt, max_attempts, e)
                except Exception:  # noqa: BLE001
                    # Audit logging must never raise out — swallow any
                    # logging error so we don't mask the original ccnl
                    # exception path.
                    pass
            if attempt < retry_count:
                sleep_fn(backoff_sec)
                continue
            break
    assert last_exc is not None
    raise last_exc


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────
def manage_order(
    intent: OrderIntent,
    *,
    adapter: _AdapterLike,
    store: Optional[OrderStore] = None,
    policy: Optional[OrderManagementPolicy] = None,
    autotrade_run_id: str = "",
    run_id: str = "",
    rec_row_id: int = 0,
    mode: str = "paper_submit",
    pre_position_qty: float = 0.0,
    position_lookup: Optional[Callable[[str, str], float]] = None,
    cancel_dry_run: bool = False,
    time_provider: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ManagedOrderOutcome:
    """Submit ``intent`` and drive it to a terminal state per ``policy``.

    Args:
        intent: a validated ``OrderIntent`` (LIMIT only).
        adapter: anything matching ``_AdapterLike`` (real or fake).
        store: optional OrderStore for durable transition events.
        policy: OrderManagementPolicy (defaults to conservative R8 §6).
        autotrade_run_id / run_id / rec_row_id / mode: forwarded to
            ``OrderStore.log_transition`` so the JSONL is queryable.
        pre_position_qty: position in ``intent.symbol`` BEFORE submit.
            Used as the baseline for the ccnl-zero-vs-position-moved
            conflict check (R5B-P2.3).
        position_lookup: optional callable ``(symbol, market) -> qty``
            that the manager invokes after each cancel / fill to refresh
            the position delta. If None, ``pre_position_qty`` is used as
            a constant baseline (no delta detection).
        cancel_dry_run: when True, all ``adapter.cancel_order`` calls run
            with ``dry_run=True``. Used by the daily-runner's
            ``--dry-run`` path so a plan can be produced without
            actually mutating the broker.
        time_provider / sleep_fn: injection points for tests.

    Never raises. Errors are surfaced via ``ManagedOrderOutcome.note``
    with ``final_state == UNKNOWN``.
    """
    policy = policy or OrderManagementPolicy()

    # ── 0a. R9-C global halt flag ──────────────────────────────────
    # Operator-pressed STOP must be respected BEFORE we touch the
    # broker. The check is intentionally cheap (a single Path.exists +
    # tiny json read) so we can re-check it inline in the polling
    # loop too if a future round wants to.
    from phase3.autotrade import global_halt as _gh
    _halt_state = _gh.read_halt()
    if _halt_state.halted:
        return ManagedOrderOutcome(
            final_state=OrderState.REJECTED,
            intent=intent,
            last_broker_order_id=None,
            last_normalized_odno="",
            qty_filled=0.0,
            qty_remaining=float(intent.qty),
            avg_fill_price=None,
            last_limit_price=float(intent.limit_price or 0.0),
            cancel_attempts=0,
            reprice_attempts=0,
            elapsed_sec=0.0,
            note=(f"global_halt is set ({_halt_state.raw_path}): "
                  f"reason={_halt_state.reason or '(none)'} "
                  f"ts={_halt_state.ts or '(none)'} — refusing to submit"),
        )

    # ── 0. Reject market orders outright ───────────────────────────
    if intent.order_type != "LIMIT" or intent.limit_price is None or intent.limit_price <= 0:
        return ManagedOrderOutcome(
            final_state=OrderState.REJECTED,
            intent=intent,
            last_broker_order_id=None,
            last_normalized_odno="",
            qty_filled=0.0,
            qty_remaining=float(intent.qty),
            avg_fill_price=None,
            last_limit_price=float(intent.limit_price or 0.0),
            cancel_attempts=0,
            reprice_attempts=0,
            elapsed_sec=0.0,
            note=f"R8 refuses non-LIMIT or zero-price intents: order_type={intent.order_type!r} limit={intent.limit_price!r}",
        )

    # ── 1. Duplicate guard before submit ───────────────────────────
    if store is not None and store.is_already_active(intent.client_order_id):
        prev = store.find_latest_blocking_by_client_id(intent.client_order_id)
        _safe_log_transition(
            store,
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
            rec_row_id=rec_row_id, ticker=intent.symbol, market=intent.market,
            side=intent.side, qty_intended=intent.qty,
            limit_price=intent.limit_price, client_order_id=intent.client_order_id,
            state=OrderState.UNKNOWN, status_source=StatusSource.LOCAL_INTENT,
            broker_order_id=prev.broker_order_id if prev else None,
            error=None,
            note=f"manage_order duplicate guard fired — verify before retry",
        )
        return ManagedOrderOutcome(
            final_state=OrderState.UNKNOWN,
            intent=intent,
            last_broker_order_id=prev.broker_order_id if prev else None,
            last_normalized_odno=normalize_odno(prev.broker_order_id) if prev else "",
            qty_filled=float(prev.raw.get("qty_filled", 0.0)) if prev else 0.0,
            qty_remaining=float(intent.qty),
            avg_fill_price=None,
            last_limit_price=float(intent.limit_price),
            cancel_attempts=0, reprice_attempts=0, elapsed_sec=0.0,
            note="duplicate guard fired before submit",
        )

    started_at = time_provider()
    cancel_attempts = 0
    reprice_attempts = 0
    classifications: List[BrokerOrderState] = []
    cur_intent = intent
    cur_limit = float(intent.limit_price)
    original_limit = float(intent.limit_price)
    last_broker_order_id: Optional[str] = None
    qty_filled_total = 0.0
    avg_fill_price_seen: Optional[float] = None

    def _elapsed() -> float:
        return time_provider() - started_at

    def _pos_qty() -> float:
        if position_lookup is None:
            return pre_position_qty
        try:
            return float(position_lookup(cur_intent.symbol, cur_intent.market))
        except Exception:  # noqa: BLE001 — position lookup must never raise out
            return pre_position_qty

    # ── 2. Submit loop (one iteration per attempt: first + repricing) ──
    while True:
        # 2.a Place this attempt.
        try:
            placed = adapter.place_order(cur_intent, dry_run=False)
        except Exception as e:  # noqa: BLE001
            _safe_log_transition(
                store,
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                side=cur_intent.side, qty_intended=cur_intent.qty,
                limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                state=OrderState.UNKNOWN, status_source=StatusSource.UNKNOWN,
                broker_order_id=None, error=f"{type(e).__name__}: {e}",
                note="exception during paper_submit — verify before retry",
                extra={"reprice_attempt": reprice_attempts,
                       "parent_broker_order_id": last_broker_order_id},
            )
            return ManagedOrderOutcome(
                final_state=OrderState.UNKNOWN,
                intent=cur_intent,
                last_broker_order_id=last_broker_order_id,
                last_normalized_odno=normalize_odno(last_broker_order_id),
                qty_filled=qty_filled_total,
                qty_remaining=float(cur_intent.qty) - qty_filled_total,
                avg_fill_price=avg_fill_price_seen,
                last_limit_price=cur_limit,
                cancel_attempts=cancel_attempts,
                reprice_attempts=reprice_attempts,
                elapsed_sec=_elapsed(),
                note=f"submit exception: {type(e).__name__}: {e}",
                classifications=classifications,
            )

        if placed.status == "rejected":
            _safe_log_transition(
                store,
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                side=cur_intent.side, qty_intended=cur_intent.qty,
                limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                state=OrderState.REJECTED, status_source=StatusSource.PLACE_ORDER_ACK,
                broker_order_id=None,
                error=str(placed.raw_response_summary.get("error", "")),
                note=f"place_order rejected at submit",
                extra={"reprice_attempt": reprice_attempts,
                       "parent_broker_order_id": last_broker_order_id},
            )
            return ManagedOrderOutcome(
                final_state=OrderState.REJECTED,
                intent=cur_intent,
                last_broker_order_id=None,
                last_normalized_odno="",
                qty_filled=qty_filled_total,
                qty_remaining=float(cur_intent.qty) - qty_filled_total,
                avg_fill_price=avg_fill_price_seen,
                last_limit_price=cur_limit,
                cancel_attempts=cancel_attempts,
                reprice_attempts=reprice_attempts,
                elapsed_sec=_elapsed(),
                note=f"place_order rejected: {placed.raw_response_summary}",
                classifications=classifications,
            )

        if not placed.broker_order_id:
            _safe_log_transition(
                store,
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                side=cur_intent.side, qty_intended=cur_intent.qty,
                limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                state=OrderState.UNKNOWN, status_source=StatusSource.PLACE_ORDER_ACK,
                broker_order_id=None, error="place_order ack returned no ODNO",
                note="ack_no_id — verify before retry",
                extra={"reprice_attempt": reprice_attempts,
                       "parent_broker_order_id": last_broker_order_id},
            )
            return ManagedOrderOutcome(
                final_state=OrderState.UNKNOWN,
                intent=cur_intent,
                last_broker_order_id=last_broker_order_id,
                last_normalized_odno=normalize_odno(last_broker_order_id),
                qty_filled=qty_filled_total,
                qty_remaining=float(cur_intent.qty) - qty_filled_total,
                avg_fill_price=avg_fill_price_seen,
                last_limit_price=cur_limit,
                cancel_attempts=cancel_attempts,
                reprice_attempts=reprice_attempts,
                elapsed_sec=_elapsed(),
                note="place_order ack_no_id",
                classifications=classifications,
            )

        new_broker_order_id = placed.broker_order_id
        _safe_log_transition(
            store,
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
            rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
            side=cur_intent.side, qty_intended=cur_intent.qty,
            limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
            state=OrderState.SUBMITTED, status_source=StatusSource.PLACE_ORDER_ACK,
            broker_order_id=new_broker_order_id, error=None,
            note=("initial submit" if reprice_attempts == 0
                  else f"reprice submit #{reprice_attempts}"),
            extra={"reprice_attempt": reprice_attempts,
                   "parent_broker_order_id": last_broker_order_id},
        )
        last_broker_order_id = new_broker_order_id

        # 2.b Poll loop for this attempt.
        attempt_started_at = time_provider()
        terminal_state: Optional[OrderState] = None
        last_class: Optional[BrokerOrderState] = None

        while True:
            sleep_fn(policy.poll_interval_sec)
            pos_now = _pos_qty()
            position_delta = pos_now - pre_position_qty
            try:
                # R10D-1: absorb KIS paper ccnl transient disconnects
                # with a bounded retry budget. Every retry leaves an
                # audit row via ``_log_ccnl_retry`` below.
                def _log_ccnl_retry(
                    attempt: int, max_attempts: int, exc: BaseException,
                ) -> None:
                    _safe_log_transition(
                        store,
                        autotrade_run_id=autotrade_run_id, mode=mode,
                        run_id=run_id, rec_row_id=rec_row_id,
                        ticker=cur_intent.symbol, market=cur_intent.market,
                        side=cur_intent.side, qty_intended=cur_intent.qty,
                        limit_price=cur_limit,
                        client_order_id=cur_intent.client_order_id,
                        state=OrderState.UNKNOWN,
                        status_source=StatusSource.UNKNOWN,
                        broker_order_id=new_broker_order_id,
                        error=f"{type(exc).__name__}: {exc}",
                        note=(f"ccnl poll retry {attempt + 1}/"
                              f"{max_attempts} — transient"),
                        extra={"reprice_attempt": reprice_attempts,
                               "parent_broker_order_id": last_broker_order_id,
                               "ccnl_retry_phase": "main"},
                    )
                bs = _now_classify_with_retry(
                    adapter,
                    broker_order_id=new_broker_order_id,
                    position_delta=position_delta,
                    retry_count=policy.ccnl_poll_retry_count,
                    backoff_sec=policy.ccnl_poll_retry_backoff_sec,
                    sleep_fn=sleep_fn,
                    log_attempt=_log_ccnl_retry,
                )
            except Exception as e:  # noqa: BLE001
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.UNKNOWN, status_source=StatusSource.UNKNOWN,
                    broker_order_id=new_broker_order_id,
                    error=f"{type(e).__name__}: {e}",
                    note=(f"ccnl poll exception after "
                          f"{policy.ccnl_poll_retry_count} retries "
                          f"— verify before retry"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.UNKNOWN,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=f"ccnl poll exception: {e}",
                    classifications=classifications,
                )

            classifications.append(bs)
            last_class = bs

            if bs.state == OrderState.FILLED:
                qty_filled_total = float(bs.filled_qty or cur_intent.qty)
                avg_fill_price_seen = bs.avg_fill_price or avg_fill_price_seen
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.FILLED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    qty_filled=qty_filled_total, qty_remaining=0.0,
                    raw_broker_row=bs.raw,
                    fill_price=bs.avg_fill_price, fill_price_source="ccnl",
                    error=None, note=bs.note,
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.FILLED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=0.0,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="FILLED",
                    classifications=classifications,
                )

            if bs.state == OrderState.PARTIALLY_FILLED:
                # Record the partial; per R8 §6 default, cancel the
                # remainder and stop (no in-place reprice on partial in
                # the v0 conservative loop).
                qty_filled_total = float(bs.filled_qty or qty_filled_total)
                avg_fill_price_seen = bs.avg_fill_price or avg_fill_price_seen
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.PARTIALLY_FILLED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    raw_broker_row=bs.raw,
                    fill_price=bs.avg_fill_price, fill_price_source="ccnl",
                    error=None, note=bs.note,
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                terminal_state = OrderState.PARTIALLY_FILLED
                break

            if bs.state == OrderState.REJECTED:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.REJECTED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    raw_broker_row=bs.raw,
                    error=None, note=bs.note,
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.REJECTED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=bs.note or "REJECTED via ccnl",
                    classifications=classifications,
                )

            if bs.state == OrderState.UNKNOWN:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.UNKNOWN, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    raw_broker_row=bs.raw,
                    error=None,
                    note=f"UNKNOWN during poll — verify before retry: {bs.note}",
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.UNKNOWN,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=bs.note,
                    classifications=classifications,
                )

            if bs.state == OrderState.CANCELLED:
                # Unsolicited / out-of-band cancel (e.g. operator cancelled
                # via the GUI while we were waiting). Record + stop; do NOT
                # auto-reprice this case.
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.CANCELLED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    raw_broker_row=bs.raw, error=None,
                    note=f"out-of-band cancel detected: {bs.note}",
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "cancel_row_odno": bs.cancel_row_odno},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.CANCELLED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="out-of-band cancel",
                    classifications=classifications,
                )

            # OPEN_OR_PENDING — keep polling until this attempt's budget
            # exhausts. Use the per-attempt clock so a long cancel-confirm
            # wait doesn't eat into the next reprice's wait window.
            if (time_provider() - attempt_started_at) >= policy.max_wait_sec:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.OPEN_OR_PENDING, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    raw_broker_row=bs.raw, error=None,
                    note=f"timeout after {policy.max_wait_sec}s — will request cancel",
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                terminal_state = OrderState.OPEN_OR_PENDING
                break

        # ── 3. Per-attempt post-loop handling (cancel / reprice / stop) ──
        if terminal_state in (OrderState.OPEN_OR_PENDING,
                              OrderState.PARTIALLY_FILLED):
            qty_to_cancel = max(int(round(float(cur_intent.qty) - qty_filled_total)), 0)
            if qty_to_cancel <= 0:
                # nothing left to cancel
                return ManagedOrderOutcome(
                    final_state=OrderState.PARTIALLY_FILLED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=0.0,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="partially_filled, no remaining qty to cancel",
                    classifications=classifications,
                )

            cancel_attempts += 1
            _safe_log_transition(
                store,
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                side=cur_intent.side, qty_intended=cur_intent.qty,
                limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                state=OrderState.CANCEL_REQUESTED, status_source=StatusSource.LOCAL_INTENT,
                broker_order_id=new_broker_order_id,
                error=None,
                note=f"requesting cancel of remaining {qty_to_cancel}",
                extra={"reprice_attempt": reprice_attempts,
                       "parent_broker_order_id": last_broker_order_id,
                       "cancel_qty": qty_to_cancel},
            )
            cancel_res = adapter.cancel_order(
                broker_order_id=new_broker_order_id,
                symbol=cur_intent.symbol,
                market=cur_intent.market,
                qty=qty_to_cancel,
                dry_run=cancel_dry_run,
                note=f"order_manager cancel #{cancel_attempts}",
            )
            if not cancel_res.accepted:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.UNKNOWN, status_source=StatusSource.CANCEL_ACK,
                    broker_order_id=new_broker_order_id,
                    error=cancel_res.note,
                    note=f"cancel rejected by adapter — verify before retry",
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.UNKNOWN,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=f"cancel rejected: {cancel_res.note}",
                    classifications=classifications,
                )

            # Dry-run cancel never confirms; treat as cancel-requested-but-
            # unconfirmed and stop (no reprice).
            if cancel_res.dry_run:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.CANCEL_REQUESTED, status_source=StatusSource.CANCEL_ACK,
                    broker_order_id=new_broker_order_id,
                    error=None,
                    note="cancel dry-run — not confirmed; will not reprice",
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "dry_run": True},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.CANCEL_REQUESTED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="cancel_dry_run — unconfirmed",
                    classifications=classifications,
                )

            # Wait for the cancel to surface in ccnl as a sibling row.
            sleep_fn(policy.cancel_confirm_wait_sec)
            pos_now = _pos_qty()
            position_delta = pos_now - pre_position_qty
            try:
                # R10D-1: same transient absorption as the main poll.
                # The cancel-post phase is the worst case for an
                # UNKNOWN classification (the order may have filled
                # during the cancel race), so retry visibility is
                # especially important.
                def _log_ccnl_retry_post_cancel(
                    attempt: int, max_attempts: int, exc: BaseException,
                ) -> None:
                    _safe_log_transition(
                        store,
                        autotrade_run_id=autotrade_run_id, mode=mode,
                        run_id=run_id, rec_row_id=rec_row_id,
                        ticker=cur_intent.symbol, market=cur_intent.market,
                        side=cur_intent.side, qty_intended=cur_intent.qty,
                        limit_price=cur_limit,
                        client_order_id=cur_intent.client_order_id,
                        state=OrderState.UNKNOWN,
                        status_source=StatusSource.CANCEL_ACK,
                        broker_order_id=new_broker_order_id,
                        error=f"{type(exc).__name__}: {exc}",
                        note=(f"ccnl re-poll retry {attempt + 1}/"
                              f"{max_attempts} after cancel — transient"),
                        extra={"reprice_attempt": reprice_attempts,
                               "parent_broker_order_id": last_broker_order_id,
                               "cancel_ack_oid": cancel_res.cancel_order_id,
                               "ccnl_retry_phase": "post_cancel"},
                    )
                bs_post_cancel = _now_classify_with_retry(
                    adapter,
                    broker_order_id=new_broker_order_id,
                    position_delta=position_delta,
                    retry_count=policy.ccnl_poll_retry_count,
                    backoff_sec=policy.ccnl_poll_retry_backoff_sec,
                    sleep_fn=sleep_fn,
                    log_attempt=_log_ccnl_retry_post_cancel,
                )
            except Exception as e:  # noqa: BLE001
                # ccnl re-poll exception after our cancel went out is
                # the worst case: broker may or may not have cancelled,
                # we have no visibility. R9-A1: never reprice.
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.UNKNOWN, status_source=StatusSource.CANCEL_ACK,
                    broker_order_id=new_broker_order_id,
                    error=f"{type(e).__name__}: {e}",
                    note=("cancel_requested_but_unconfirmed: ccnl re-poll "
                          f"exception after {policy.ccnl_poll_retry_count} "
                          "retries — verify before retry"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "cancel_ack_oid": cancel_res.cancel_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.UNKNOWN,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=f"ccnl re-poll exception after cancel: {e}",
                    classifications=classifications,
                )

            classifications.append(bs_post_cancel)

            # ── R9-A1: explicit cancel-outcome branches. ────────────
            # The previous implementation merged FILLED / PARTIAL+sibling
            # / CANCELLED into a single ``cancel_confirmed`` flag and
            # then logged CANCELLED and ran the reprice eligibility
            # block. That was a P1 double-buy bug: if the broker filled
            # the original order during the cancel-send race, we would
            # (a) log a FILLED order as CANCELLED, and (b) submit a
            # reprice child BUY on top. Each cancel-outcome state now
            # has its own terminal branch.

            # 1. Cancel-race full fill — record FILLED, NEVER reprice.
            if bs_post_cancel.state == OrderState.FILLED:
                qty_filled_total = float(
                    bs_post_cancel.filled_qty if bs_post_cancel.filled_qty is not None
                    else cur_intent.qty
                )
                avg_fill_price_seen = bs_post_cancel.avg_fill_price or avg_fill_price_seen
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.FILLED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    qty_filled=qty_filled_total, qty_remaining=0.0,
                    raw_broker_row=bs_post_cancel.raw,
                    fill_price=bs_post_cancel.avg_fill_price, fill_price_source="ccnl",
                    error=None,
                    note=("cancel-race fill — broker filled original order "
                          "before cancel landed; no reprice"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "cancel_ack_oid": cancel_res.cancel_order_id},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.FILLED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=0.0,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="cancel-race fill",
                    classifications=classifications,
                )

            # 2. Cancel-race partial fill — record PARTIAL, NEVER reprice.
            if (
                bs_post_cancel.state == OrderState.PARTIALLY_FILLED
                and bs_post_cancel.cancel_row_odno is not None
            ):
                qty_filled_total = float(
                    bs_post_cancel.filled_qty if bs_post_cancel.filled_qty is not None
                    else qty_filled_total
                )
                avg_fill_price_seen = bs_post_cancel.avg_fill_price or avg_fill_price_seen
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.PARTIALLY_FILLED, status_source=StatusSource.CCNL_ECHO,
                    broker_order_id=new_broker_order_id,
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    raw_broker_row=bs_post_cancel.raw,
                    fill_price=bs_post_cancel.avg_fill_price, fill_price_source="ccnl",
                    error=None,
                    note=("cancel-race partial fill — sibling cancel row "
                          "present for remainder; no reprice"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "cancel_ack_oid": cancel_res.cancel_order_id,
                           "cancel_row_odno": bs_post_cancel.cancel_row_odno},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.PARTIALLY_FILLED,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note="cancel-race partial fill",
                    classifications=classifications,
                )

            # 3. Anything that is not a clean CANCELLED is an UNKNOWN:
            #    - OPEN_OR_PENDING            (cancel didn't land yet)
            #    - PARTIALLY_FILLED w/o sib   (broker partially filled but
            #                                   cancel sibling not visible yet)
            #    - UNKNOWN                    (classifier said so)
            #    - REJECTED                   (rare: broker rejected the
            #                                   underlying order between
            #                                   our cancel send and re-poll)
            if bs_post_cancel.state != OrderState.CANCELLED:
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                    state=OrderState.UNKNOWN, status_source=StatusSource.CANCEL_ACK,
                    broker_order_id=new_broker_order_id,
                    raw_broker_row=bs_post_cancel.raw,
                    error=None,
                    note=(f"cancel_requested_but_unconfirmed — "
                          f"post-cancel state={bs_post_cancel.state.value}; "
                          f"verify before retry: {bs_post_cancel.note}"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "cancel_ack_oid": cancel_res.cancel_order_id,
                           "post_cancel_state": bs_post_cancel.state.value},
                )
                return ManagedOrderOutcome(
                    final_state=OrderState.UNKNOWN,
                    intent=cur_intent,
                    last_broker_order_id=new_broker_order_id,
                    last_normalized_odno=normalize_odno(new_broker_order_id),
                    qty_filled=qty_filled_total,
                    qty_remaining=float(cur_intent.qty) - qty_filled_total,
                    avg_fill_price=avg_fill_price_seen,
                    last_limit_price=cur_limit,
                    cancel_attempts=cancel_attempts,
                    reprice_attempts=reprice_attempts,
                    elapsed_sec=_elapsed(),
                    note=f"cancel_requested_but_unconfirmed: {bs_post_cancel.note}",
                    classifications=classifications,
                )

            # 4. Clean CANCELLED — eligible for reprice.
            _safe_log_transition(
                store,
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                side=cur_intent.side, qty_intended=cur_intent.qty,
                limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
                state=OrderState.CANCELLED, status_source=StatusSource.CANCEL_ACK,
                broker_order_id=new_broker_order_id,
                raw_broker_row=bs_post_cancel.raw,
                error=None,
                note=f"cancel confirmed via ccnl sibling row",
                extra={"reprice_attempt": reprice_attempts,
                       "parent_broker_order_id": last_broker_order_id,
                       "cancel_ack_oid": cancel_res.cancel_order_id,
                       "cancel_row_odno": bs_post_cancel.cancel_row_odno},
            )

            # Reprice eligibility:
            #   - only on OPEN_OR_PENDING timeout (not on PARTIAL)
            #   - only BUY side (R8 §6)
            #   - within max_reprice_attempts
            #   - the new price must strictly exceed cur_limit (else
            #     ceiling reached → stop with cancelled state)
            if (terminal_state == OrderState.OPEN_OR_PENDING
                    and cur_intent.side == "BUY"
                    and reprice_attempts < policy.max_reprice_attempts):
                new_limit = reprice_limit_buy(
                    original_limit=original_limit,
                    current_limit=cur_limit,
                    policy=policy,
                )
                if not reprice_would_improve(
                    current_limit=cur_limit, candidate=new_limit,
                ):
                    return ManagedOrderOutcome(
                        final_state=OrderState.CANCELLED,
                        intent=cur_intent,
                        last_broker_order_id=new_broker_order_id,
                        last_normalized_odno=normalize_odno(new_broker_order_id),
                        qty_filled=qty_filled_total,
                        qty_remaining=float(cur_intent.qty) - qty_filled_total,
                        avg_fill_price=avg_fill_price_seen,
                        last_limit_price=cur_limit,
                        cancel_attempts=cancel_attempts,
                        reprice_attempts=reprice_attempts,
                        elapsed_sec=_elapsed(),
                        note=("cancelled; reprice ceiling reached "
                              f"({cur_limit:.4f} vs cap "
                              f"{original_limit * (1 + policy.max_total_slippage_bps / 10_000):.4f})"),
                        classifications=classifications,
                    )

                reprice_attempts += 1
                next_qty = max(int(round(float(cur_intent.qty) - qty_filled_total)), 0)
                if next_qty <= 0:
                    return ManagedOrderOutcome(
                        final_state=OrderState.CANCELLED,
                        intent=cur_intent,
                        last_broker_order_id=new_broker_order_id,
                        last_normalized_odno=normalize_odno(new_broker_order_id),
                        qty_filled=qty_filled_total,
                        qty_remaining=0.0,
                        avg_fill_price=avg_fill_price_seen,
                        last_limit_price=cur_limit,
                        cancel_attempts=cancel_attempts,
                        reprice_attempts=reprice_attempts,
                        elapsed_sec=_elapsed(),
                        note="cancelled; nothing left to reprice",
                        classifications=classifications,
                    )

                # New child intent with reprice client_order_id.
                child_cid = _reprice_client_id(intent.client_order_id, reprice_attempts)
                _safe_log_transition(
                    store,
                    autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
                    rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
                    side=cur_intent.side, qty_intended=cur_intent.qty,
                    limit_price=new_limit, client_order_id=child_cid,
                    state=OrderState.REPLACE_REQUESTED, status_source=StatusSource.LOCAL_INTENT,
                    broker_order_id=new_broker_order_id,
                    error=None,
                    note=(f"reprice #{reprice_attempts}: "
                          f"{cur_limit:.4f} -> {new_limit:.4f}"),
                    extra={"reprice_attempt": reprice_attempts,
                           "parent_broker_order_id": last_broker_order_id,
                           "parent_client_order_id": cur_intent.client_order_id,
                           "qty": next_qty},
                )
                cur_intent = OrderIntent(
                    symbol=cur_intent.symbol,
                    market=cur_intent.market,
                    side=cur_intent.side,
                    qty=next_qty,
                    order_type="LIMIT",
                    limit_price=new_limit,
                    client_order_id=child_cid,
                    note=f"reprice attempt {reprice_attempts} of parent "
                         f"{intent.client_order_id}",
                )
                cur_limit = new_limit
                # Continue outer while-loop: re-submit.
                continue

            # No more reprice attempts (max reached, or SELL side, or
            # partial-fill terminal). Stop with the most informative
            # terminal state.
            final_state = (
                OrderState.PARTIALLY_FILLED
                if qty_filled_total > 0 and qty_filled_total < float(intent.qty)
                else OrderState.CANCELLED
            )
            note = (
                f"max_reprice_attempts={policy.max_reprice_attempts} reached"
                if (terminal_state == OrderState.OPEN_OR_PENDING
                    and cur_intent.side == "BUY"
                    and reprice_attempts >= policy.max_reprice_attempts)
                else
                ("partial-fill terminal: cancelled remainder"
                 if final_state == OrderState.PARTIALLY_FILLED
                 else f"cancelled (side={cur_intent.side}, no reprice)")
            )
            return ManagedOrderOutcome(
                final_state=final_state,
                intent=cur_intent,
                last_broker_order_id=new_broker_order_id,
                last_normalized_odno=normalize_odno(new_broker_order_id),
                qty_filled=qty_filled_total,
                qty_remaining=float(cur_intent.qty) - qty_filled_total,
                avg_fill_price=avg_fill_price_seen,
                last_limit_price=cur_limit,
                cancel_attempts=cancel_attempts,
                reprice_attempts=reprice_attempts,
                elapsed_sec=_elapsed(),
                note=note,
                classifications=classifications,
            )

        # If we exit the inner loop without a terminal state, that's a
        # bug in the matcher — surface as UNKNOWN to be safe.
        _safe_log_transition(
            store,
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=run_id,
            rec_row_id=rec_row_id, ticker=cur_intent.symbol, market=cur_intent.market,
            side=cur_intent.side, qty_intended=cur_intent.qty,
            limit_price=cur_limit, client_order_id=cur_intent.client_order_id,
            state=OrderState.UNKNOWN, status_source=StatusSource.UNKNOWN,
            broker_order_id=new_broker_order_id,
            error="manage_order inner loop fell through",
            note="manage_order internal — verify before retry",
            extra={"reprice_attempt": reprice_attempts,
                   "parent_broker_order_id": last_broker_order_id},
        )
        return ManagedOrderOutcome(
            final_state=OrderState.UNKNOWN,
            intent=cur_intent,
            last_broker_order_id=new_broker_order_id,
            last_normalized_odno=normalize_odno(new_broker_order_id),
            qty_filled=qty_filled_total,
            qty_remaining=float(cur_intent.qty) - qty_filled_total,
            avg_fill_price=avg_fill_price_seen,
            last_limit_price=cur_limit,
            cancel_attempts=cancel_attempts,
            reprice_attempts=reprice_attempts,
            elapsed_sec=_elapsed(),
            note="inner loop fell through",
            classifications=classifications,
        )
