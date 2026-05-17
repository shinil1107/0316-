"""Round 4 (P4) — paper-only autotrade orchestrator skeleton.

Codex R4 §4 specified the target: connect the existing pieces into
one durable, resumable, dry-run-first runner — *without* expanding
into auto-reprice / cancel-replace / live capital this round.

What this module does
---------------------
```
artifact/intents
   -> deterministic client_order_id
   -> duplicate guard (order_store JSONL)
   -> place_order (gated)
   -> echo polling (nccs → ccnl)
   -> fill_resolver (paper vs live policy)
   -> per-event JSONL transition log
   -> summary.json + execution_report.{md,json,csv}
```

What this module does NOT do (intentional, per Codex R4 §4/§7)
--------------------------------------------------------------
- live capital: blocked by SafetyGuard's two-gate model; orchestrator
  also refuses if `KIS_ENV != paper` before parsing args.
- auto holdings write: T10 remains the sole writer.
- auto-reprice / cancel / replace: deferred to a later round.
- email send: hook is documented but not wired (one-line TODO at the
  bottom — kept out so this commit is testable offline).

Usage
-----
```bash
# Dry-run (default; no broker writes regardless of env state)
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \\
    --paper --run-id 20260512_210645_daily

# Real paper submit (still requires KIS_PAPER_SUBMIT_OK=true at the
# shell, same gate as paper_buy.py / paper_execute_intent.py)
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \\
    --paper --run-id 20260512_210645_daily --submit
```

Safety-of-default
-----------------
- `--paper` is mandatory; there is no `--live` flag in this revision.
- `--submit` requires `--run-id` (no latest-actionable inference for
  the real-write path) so the operator must point at a specific
  artifact and reason about it.
- Without `--submit` the run is dry-run; orchestrator still:
  - resolves intents,
  - writes a JSONL with `state=intent_created` per row plus
    `state=dry_run` terminal events,
  - emits the full summary + report triple.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cache_health import load_config  # type: ignore[import-not-found]

from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter,
    OrderIntent,
    SafetyState,
    load_env_config,
)
from phase3.autotrade.intents import (
    ResolvedIntent,
    load_artifact,
    resolve_intents,
)
from phase3.autotrade.reconcile import _PROFILE_CONFIG, _resolve_config_path
from phase3.autotrade.echo import echo_poll, attempt_stdout_line
from phase3.autotrade.order_state import OrderState, StatusSource
from phase3.autotrade.order_store import (
    OrderStore,
    build_client_order_id,
    new_autotrade_run_id,
)
from phase3.autotrade.fill_resolver import resolve_fill_state
from phase3.autotrade.execution_report import write_reports


# ──────────────────────────────────────────────────────────────────────
# Constants — caller-tunable but Codex R4 §6 set conservative defaults.
# ──────────────────────────────────────────────────────────────────────
DEFAULT_MAX_ORDERS_PER_RUN = 10
DEFAULT_MAX_NOTIONAL_PER_ORDER_USD = 5_000.0
DEFAULT_MAX_NOTIONAL_PER_RUN_USD = 20_000.0
DEFAULT_ECHO_POLLS = 4
DEFAULT_ECHO_INTERVAL_SEC = 3.0


def _abort(msg: str, *, rc: int = 2) -> int:
    print(f"\n[orchestrator][ABORT] {msg}")
    return rc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _market_session_hint() -> str:
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour + now_utc.minute / 60.0
    if 13.5 <= hour < 20.0:
        return "US regular session OPEN (approx)"
    if 8.0 <= hour < 13.5:
        return "US PRE-MARKET (approx) — paper may queue or reject LIMITs"
    return "US session CLOSED (approx) — paper likely rejects"


# ──────────────────────────────────────────────────────────────────────
# Pre-flight notional guard
# ──────────────────────────────────────────────────────────────────────
def _enforce_notional_limits(
    intents: List[ResolvedIntent],
    *,
    max_orders: int,
    max_notional_per_order: float,
    max_notional_per_run: float,
) -> Optional[str]:
    """Return a failure message if any policy is violated, else None."""
    if len(intents) > max_orders:
        return (f"intents ({len(intents)}) exceeds --max-orders={max_orders}; "
                "refusing to proceed (Codex R4 §9 max order count safety).")
    total = 0.0
    for ri in intents:
        notional = float(ri.limit_price) * float(ri.qty)
        if notional > max_notional_per_order:
            return (f"intent {ri.ticker} rec_row_id={ri.rec_row_id} notional "
                    f"${notional:,.2f} > --max-notional-per-order=${max_notional_per_order:,.2f}; "
                    "refusing.")
        total += notional
    if total > max_notional_per_run:
        return (f"cumulative notional ${total:,.2f} > "
                f"--max-notional-per-run=${max_notional_per_run:,.2f}; refusing.")
    return None


# ──────────────────────────────────────────────────────────────────────
# Per-intent driver
# ──────────────────────────────────────────────────────────────────────
def _process_intent(
    *,
    adapter: KisBrokerAdapter,
    store: OrderStore,
    intent: ResolvedIntent,
    artifact_run_id: str,
    autotrade_run_id: str,
    mode: str,                # 'dry_run' | 'paper_submit'
    echo_polls: int,
    echo_interval_sec: float,
) -> Dict[str, Any]:
    """Drive one intent end-to-end. Returns a tiny dict the
    orchestrator uses to update aggregates.

    `had_error` (Codex R5B-P1.2) is True iff *this* intent hit a
    runtime failure that the caller should treat as a non-zero
    finalize signal. Duplicate-skipped and risk-blocked are NOT
    errors — they are intentional skip outcomes."""
    out: Dict[str, Any] = {
        "rec_row_id": intent.rec_row_id,
        "ticker": intent.ticker,
        "final_state": None,
        "duplicate_skipped": False,
        "risk_blocked": False,
        "submitted": False,
        "had_error": False,
        "error": None,
        "cash_delta_usd": 0.0,
        "position_delta": 0,
    }

    market_payload = str(intent.payload.get("OVRS_EXCG_CD", "NASD"))
    client_id = build_client_order_id(
        run_id=artifact_run_id, rec_row_id=intent.rec_row_id,
        side=intent.side, qty=intent.qty,
    )

    # ── duplicate guard (BEFORE intent_created) ───────────────
    # Codex R5A-P1.1 fix:
    # the duplicate guard must run BEFORE we log any new event for this
    # client_order_id, otherwise `find_latest_by_client_id` returns the
    # `intent_created` row we just wrote and hides any prior SUBMITTED /
    # FILLED state, defeating the whole point of the guard.
    #
    # Codex R5B-P1.1 follow-up:
    # `is_already_active` now scans the whole history (not just the
    # latest row) and also treats blocking-UNKNOWN markers as active.
    # We surface the blocking row (not just the most recent row) in
    # the log so operators see WHY the guard fired.
    if store.is_already_active(client_id):
        blocking = store.find_latest_blocking_by_client_id(client_id)
        latest = store.find_latest_by_client_id(client_id)
        block_state = blocking.state.value if (blocking and blocking.state) else "?"
        latest_state = latest.state.value if (latest and latest.state) else "?"
        odno_to_preserve = (
            (blocking.broker_order_id if blocking else None)
            or (latest.broker_order_id if latest else None)
        )
        store.log_transition(
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
            rec_row_id=intent.rec_row_id, ticker=intent.ticker,
            market=market_payload, side=intent.side,
            qty_intended=intent.qty, qty_remaining=intent.qty,
            limit_price=intent.limit_price, client_order_id=client_id,
            broker_order_id=odno_to_preserve,
            state=OrderState.UNKNOWN,           # unknown == we believe the prior; not re-submitting
            status_source=StatusSource.LOCAL_INTENT,
            note=(f"duplicate guard: blocking_state={block_state} "
                  f"latest_state={latest_state}; not re-submitting"),
        )
        out["duplicate_skipped"] = True
        out["final_state"] = "duplicate_skipped"
        print(f"  [skip] {intent.ticker} client_order_id={client_id} "
              f"blocking={block_state} latest={latest_state}")
        return out

    # ── intent_created (only after duplicate guard cleared) ───
    store.log_transition(
        autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
        rec_row_id=intent.rec_row_id, ticker=intent.ticker,
        market=market_payload, side=intent.side,
        qty_intended=intent.qty, qty_remaining=intent.qty,
        limit_price=intent.limit_price, client_order_id=client_id,
        state=OrderState.INTENT_CREATED, status_source=StatusSource.LOCAL_INTENT,
        note=f"resolved limit_source={intent.limit_source}",
    )

    # ── risk_flags from intents.py pre-trade check ────────────
    if intent.risk_flags:
        store.log_transition(
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
            rec_row_id=intent.rec_row_id, ticker=intent.ticker,
            market=market_payload, side=intent.side,
            qty_intended=intent.qty, qty_remaining=intent.qty,
            limit_price=intent.limit_price, client_order_id=client_id,
            state=OrderState.REJECTED, status_source=StatusSource.LOCAL_INTENT,
            error=f"risk_flags: {intent.risk_flags}",
        )
        out["risk_blocked"] = True
        out["final_state"] = "rejected"
        print(f"  [reject] {intent.ticker} risk_flags={intent.risk_flags}")
        return out

    # ── dry-run terminal ──────────────────────────────────────
    if mode == "dry_run":
        # Still dispatch through SafetyGuard for parity with the real
        # path; this hits zero network in the adapter's place_order.
        oi = OrderIntent(
            symbol=intent.ticker, market=market_payload, side="BUY",
            qty=intent.qty, order_type="LIMIT", limit_price=intent.limit_price,
            client_order_id=client_id,
            note=f"orchestrator dry-run run_id={artifact_run_id} rec_row_id={intent.rec_row_id}",
        )
        po = adapter.place_order(oi, dry_run=True)
        store.log_transition(
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
            rec_row_id=intent.rec_row_id, ticker=intent.ticker,
            market=market_payload, side=intent.side,
            qty_intended=intent.qty, qty_remaining=intent.qty,
            limit_price=intent.limit_price, client_order_id=client_id,
            state=OrderState.DRY_RUN, status_source=StatusSource.LOCAL_INTENT,
            raw_broker_row={"placed_order_status": po.status,
                            "raw_response_summary": po.raw_response_summary},
            note="orchestrator dry-run: no transmission",
        )
        out["final_state"] = "dry_run"
        print(f"  [dry_run] {intent.ticker} qty={intent.qty} @ ${intent.limit_price:.4f}")
        return out

    # ── paper_submit path, wrapped in a try/except so post-submit
    #    exceptions become durable UNKNOWN events with the broker_order_id
    #    preserved (Codex R5A-P1.3 fix). The outer cmd_run() finally block
    #    is a second line of defence; this inner guard exists so that
    #    `place_order` succeeding but `echo_poll` / position read failing
    #    still produces a proper terminal transition, not a half-state.
    broker_order_id_captured: Optional[str] = None
    placed_status_captured: Optional[str] = None
    submitted_logged = False

    try:
        # ── pre-trade snapshot for fill_resolver ──────────────────
        positions_pre = adapter.get_positions(market="NASD")
        pos_pre = next((p for p in positions_pre if p.symbol.upper() == intent.ticker), None)
        held_pre = int(pos_pre.qty) if pos_pre else 0
        cash_pre = adapter.get_cash(market=market_payload, ref_symbol=intent.ticker,
                                    ref_price=intent.quote_last)
        print(f"  [pre]  held={held_pre}  cash_avail=${cash_pre.available:,.2f}")

        # ── submit ───────────────────────────────────────────────
        oi = OrderIntent(
            symbol=intent.ticker, market=market_payload, side="BUY",
            qty=intent.qty, order_type="LIMIT", limit_price=intent.limit_price,
            client_order_id=client_id,
            note=f"orchestrator submit run_id={artifact_run_id} rec_row_id={intent.rec_row_id}",
        )
        placed = adapter.place_order(oi, dry_run=False)
        broker_order_id_captured = placed.broker_order_id
        placed_status_captured = placed.status
        print(f"  [submit] status={placed.status}  broker_order_id={placed.broker_order_id}")

        if placed.status == "rejected":
            err = (placed.raw_response_summary or {}).get("error")
            store.log_transition(
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
                rec_row_id=intent.rec_row_id, ticker=intent.ticker,
                market=market_payload, side=intent.side,
                qty_intended=intent.qty, qty_remaining=intent.qty,
                limit_price=intent.limit_price, client_order_id=client_id,
                broker_order_id=placed.broker_order_id,
                state=OrderState.REJECTED, status_source=StatusSource.PLACE_ORDER_ACK,
                raw_broker_row=placed.raw_response_summary or {},
                error=str(err) if err else "rejected",
            )
            out["final_state"] = "rejected"
            return out

        if not placed.broker_order_id:
            store.log_transition(
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
                rec_row_id=intent.rec_row_id, ticker=intent.ticker,
                market=market_payload, side=intent.side,
                qty_intended=intent.qty, qty_remaining=intent.qty,
                limit_price=intent.limit_price, client_order_id=client_id,
                state=OrderState.UNKNOWN, status_source=StatusSource.PLACE_ORDER_ACK,
                raw_broker_row=placed.raw_response_summary or {},
                error="no ODNO returned",
                note=("place_order returned status=submitted without an ODNO — "
                      "broker state ambiguous; verify before retry"),
            )
            out["final_state"] = "unknown"
            # Codex R5B-P1.2: ambiguous broker ack → had_error
            out["had_error"] = True
            out["error"] = "no ODNO returned"
            return out

        out["submitted"] = True
        store.log_transition(
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
            rec_row_id=intent.rec_row_id, ticker=intent.ticker,
            market=market_payload, side=intent.side,
            qty_intended=intent.qty, qty_remaining=intent.qty,
            limit_price=intent.limit_price, client_order_id=client_id,
            broker_order_id=placed.broker_order_id,
            state=OrderState.SUBMITTED, status_source=StatusSource.PLACE_ORDER_ACK,
            raw_broker_row=placed.raw_response_summary or {},
        )
        submitted_logged = True

        # ── echo poll ────────────────────────────────────────────
        echo = echo_poll(
            adapter, placed.broker_order_id, market=market_payload,
            max_polls=echo_polls, interval_sec=echo_interval_sec,
            on_attempt=lambda a: print("  " + attempt_stdout_line(a, max_polls=echo_polls)),
        )

        # ── post-trade snapshot ──────────────────────────────────
        positions_post = adapter.get_positions(market="NASD")
        pos_post = next((p for p in positions_post if p.symbol.upper() == intent.ticker), None)
        held_post = int(pos_post.qty) if pos_post else 0
        cash_post = adapter.get_cash(market=market_payload, ref_symbol=intent.ticker,
                                     ref_price=intent.quote_last)
        out["cash_delta_usd"] = round(cash_post.available - cash_pre.available, 4)
        out["position_delta"] = held_post - held_pre
        print(f"  [post] held={held_post} (Δ{out['position_delta']:+d})  "
              f"cash=${cash_post.available:,.2f} (Δ{out['cash_delta_usd']:+,.2f})")

        # ── fill resolver (single source of fill truth) ──────────
        resolution = resolve_fill_state(
            mode="paper",
            echo=echo,
            pre_position_qty=held_pre, post_position_qty=held_post,
            pre_cash_available=float(cash_pre.available),
            post_cash_available=float(cash_post.available),
            qty_intended=intent.qty,
            limit_price=intent.limit_price,
        )
        print(f"  [resolve] state={resolution.state.value}  "
              f"price={resolution.fill_price}  source={resolution.fill_price_source}")

        store.log_transition(
            autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
            rec_row_id=intent.rec_row_id, ticker=intent.ticker,
            market=market_payload, side=intent.side,
            qty_intended=intent.qty,
            qty_filled=resolution.qty_filled, qty_remaining=resolution.qty_remaining,
            limit_price=intent.limit_price, client_order_id=client_id,
            broker_order_id=placed.broker_order_id,
            state=resolution.state, status_source=resolution.status_source,
            raw_broker_row=echo.get("matched_row"),
            echo={"source": echo.get("source"), "matched": echo.get("matched")},
            fill_price=resolution.fill_price,
            fill_price_source=resolution.fill_price_source,
            note=resolution.note,
        )
        out["final_state"] = resolution.state.value
        return out

    except Exception as e:  # noqa: BLE001 — we deliberately catch broadly
        err_msg = f"{type(e).__name__}: {e}"
        # Best-effort durable record of the failure. Logging itself can
        # in theory fail (disk full, perms) — swallow that too rather
        # than masking the original exception's UX.
        try:
            store.log_transition(
                autotrade_run_id=autotrade_run_id, mode=mode, run_id=artifact_run_id,
                rec_row_id=intent.rec_row_id, ticker=intent.ticker,
                market=market_payload, side=intent.side,
                qty_intended=intent.qty, qty_remaining=intent.qty,
                limit_price=intent.limit_price, client_order_id=client_id,
                broker_order_id=broker_order_id_captured,
                state=OrderState.UNKNOWN,
                status_source=StatusSource.UNKNOWN,
                error=err_msg,
                note=(
                    "exception during paper_submit path; "
                    f"submitted_logged={submitted_logged} "
                    f"placed_status={placed_status_captured!r} — "
                    "if ODNO present, the order may exist at the broker; verify before retry"
                ),
            )
        except Exception as log_err:  # noqa: BLE001
            print(f"  [WARN] failed to log UNKNOWN event after exception: {log_err}")
        out["final_state"] = "unknown"
        # Codex R5B-P1.2: post-submit failures must NOT finish as
        # `completed` / rc=0. Surface the error to the caller via
        # `had_error` so `cmd_run` can flip finalize_status and rc.
        out["had_error"] = True
        out["error"] = err_msg
        # If the ODNO came back, the broker has it; surface that to the
        # caller via `submitted=True` so the run-level counters do not
        # double-count it as "never sent". Operator decides what to do next.
        if broker_order_id_captured:
            out["submitted"] = True
        print(f"  [ERROR submit-path] {intent.ticker}: {err_msg}")
        return out


# ──────────────────────────────────────────────────────────────────────
# CLI entrypoints
# ──────────────────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    env_cfg = load_env_config()

    # Hard preflight — orchestrator R4 is paper-only.
    if env_cfg.env_name != "paper":
        return _abort(f"refuse: KIS_ENV={env_cfg.env_name!r}. R4 orchestrator is paper-only.")
    # Codex R5A-P1.2 fix:
    # --submit MUST be paired with an explicit --run-id. Otherwise
    # load_artifact() can silently pick up a stale awaiting_execution
    # ghost (e.g. 20260509_185312_daily) and trade against it.
    if args.submit and not args.run_id:
        return _abort(
            "refuse --submit: --run-id is required for submit mode "
            "(prevents accidental trade against stale awaiting_execution artifacts). "
            "Re-run with --run-id <SPECIFIC_RUN_ID>."
        )
    if args.submit and not env_cfg.paper_submit_ok:
        return _abort(
            "refuse --submit: KIS_PAPER_SUBMIT_OK is not true. "
            "Run with `KIS_PAPER_SUBMIT_OK=true python3 -m phase3.autotrade.orchestrator run …`."
        )

    cfg_path = _resolve_config_path(args.profile)
    cfg = load_config(str(cfg_path))

    adapter = KisBrokerAdapter(
        cfg=env_cfg, safety_state=SafetyState(buy_only_mode=True),
        verbose=not args.quiet,
    )

    # ── artifact ────────────────────────────────────────────
    run_dir, run_meta, recos = load_artifact(
        cfg["paths"]["output_dir"], run_id=args.run_id,
    )
    artifact_run_id = str(run_meta.get("run_id") or run_dir.name)
    artifact_status = str(run_meta.get("status") or "")
    print(f"[orchestrator] artifact = {run_dir}")
    print(f"[orchestrator] status = {artifact_status}  reco_rows = {len(recos)}")
    print(f"[orchestrator] {_market_session_hint()}")

    if args.submit and artifact_status != "awaiting_execution":
        return _abort(
            f"refuse --submit: artifact status={artifact_status!r} "
            f"(need 'awaiting_execution'). Re-run with --dry-run for inspection."
        )

    # ── intents ─────────────────────────────────────────────
    intents, _skipped = resolve_intents(
        cfg=env_cfg, adapter=adapter, recos=recos, run_id=artifact_run_id,
        buy_only_mode=True, market="NASD",
        only_tickers=None, only_side="BUY",
    )
    buy_intents = [i for i in intents if i.side == "BUY"]
    print(f"[orchestrator] resolved {len(buy_intents)} BUY intent(s) "
          f"(SELL/SKIP filtered)")

    if not buy_intents:
        print("[orchestrator] nothing to do — no BUY intents resolved. exiting cleanly.")
        return 0

    # ── notional guard ──────────────────────────────────────
    guard_msg = _enforce_notional_limits(
        buy_intents,
        max_orders=args.max_orders,
        max_notional_per_order=args.max_notional_per_order,
        max_notional_per_run=args.max_notional_per_run,
    )
    if guard_msg:
        return _abort(guard_msg)

    # ── store + autotrade_run_id ─────────────────────────────
    autotrade_run_id = new_autotrade_run_id()
    store_path = run_dir / "autotrade_orders.jsonl"
    store = OrderStore(store_path)

    mode_str = "paper_submit" if args.submit else "dry_run"
    gates = {
        "kis_env":         env_cfg.env_name,
        "paper_submit_ok": env_cfg.paper_submit_ok,
        "dry_run_arg":     not args.submit,
        "buy_only_mode":   True,
    }

    started_at = _now_iso()
    store.log_run_started(
        autotrade_run_id=autotrade_run_id,
        mode=mode_str, run_id=artifact_run_id, gates=gates,
        artifact_dir=str(run_dir), intents_count=len(buy_intents),
        cli_args={
            "profile": args.profile, "run_id": args.run_id,
            "submit": args.submit, "quiet": args.quiet,
            "max_orders": args.max_orders,
            "max_notional_per_order": args.max_notional_per_order,
            "max_notional_per_run":   args.max_notional_per_run,
            "echo_polls": args.echo_polls,
            "echo_interval_sec": args.echo_interval_sec,
        },
    )
    print(f"[orchestrator] mode={mode_str}  autotrade_run_id={autotrade_run_id}")
    print(f"[orchestrator] JSONL → {store_path}")

    # ── per-intent driver ────────────────────────────────────
    # Codex R5A-P1.3 fix: wrap the whole loop in try/finally so the
    # run_ended event + summary.json + reports are produced even if a
    # per-intent step raises uncaught. `_process_intent` already has its
    # own internal try/except around the paper_submit path; this outer
    # guard is the second line of defence for failures in the iterator
    # itself (e.g. ResolvedIntent attribute access, OOM, KeyboardInterrupt).
    t0 = time.perf_counter()
    aggregate_counts: Counter[str] = Counter()
    cash_delta_total = 0.0
    pos_delta_by_ticker: Dict[str, int] = defaultdict(int)
    finalize_status = "completed"
    exit_rc = 0

    try:
        for ri in buy_intents:
            print(f"\n[orchestrator] >>> rec_row_id={ri.rec_row_id} {ri.ticker} "
                  f"{ri.side} qty={ri.qty} @ ${ri.limit_price:.4f}")
            try:
                result = _process_intent(
                    adapter=adapter, store=store, intent=ri,
                    artifact_run_id=artifact_run_id, autotrade_run_id=autotrade_run_id,
                    mode=mode_str,
                    echo_polls=args.echo_polls,
                    echo_interval_sec=args.echo_interval_sec,
                )
            except Exception as e:  # noqa: BLE001 — catchall by design
                err_msg = f"{type(e).__name__}: {e}"
                client_id_fallback = build_client_order_id(
                    run_id=artifact_run_id, rec_row_id=ri.rec_row_id,
                    side=ri.side, qty=ri.qty,
                )
                try:
                    store.log_transition(
                        autotrade_run_id=autotrade_run_id, mode=mode_str,
                        run_id=artifact_run_id,
                        rec_row_id=ri.rec_row_id, ticker=ri.ticker,
                        market=str(ri.payload.get("OVRS_EXCG_CD", "NASD")),
                        side=ri.side,
                        qty_intended=ri.qty, qty_remaining=ri.qty,
                        limit_price=ri.limit_price,
                        client_order_id=client_id_fallback,
                        state=OrderState.UNKNOWN,
                        status_source=StatusSource.UNKNOWN,
                        error=err_msg,
                        note=("exception caught in orchestrator main loop "
                              "(outside _process_intent guard); state uncertain"),
                    )
                except Exception as log_err:  # noqa: BLE001
                    print(f"  [WARN] failed to log outer-loop UNKNOWN: {log_err}")
                finalize_status = "completed_with_errors"
                exit_rc = 1
                print(f"  [ERROR outer-loop] {ri.ticker}: {err_msg}")
                result = {
                    "final_state": "unknown",
                    "duplicate_skipped": False,
                    "risk_blocked": False,
                    "submitted": False,
                    "had_error": True,
                    "error": err_msg,
                    "cash_delta_usd": 0.0,
                    "position_delta": 0,
                }

            # Codex R5B-P1.2: propagate per-intent error to run-level
            # finalize / rc. Duplicate-skipped and risk-blocked are
            # intentional skip outcomes (not errors) so they stay rc=0.
            if result.get("had_error"):
                finalize_status = "completed_with_errors"
                exit_rc = 1
            aggregate_counts[result.get("final_state") or "unknown"] += 1
            if result.get("duplicate_skipped"):
                aggregate_counts["_duplicate_skipped"] += 1
            if result.get("risk_blocked"):
                aggregate_counts["_risk_blocked"] += 1
            if result.get("submitted"):
                aggregate_counts["_submitted_total"] += 1
            if result.get("had_error"):
                aggregate_counts["_had_error"] += 1
            cash_delta_total += float(result.get("cash_delta_usd") or 0.0)
            pos_delta_by_ticker[ri.ticker] += int(result.get("position_delta") or 0)

    finally:
        duration_sec = round(time.perf_counter() - t0, 3)

        # ── run_ended (best-effort) ─────────────────────────────
        try:
            store.log_run_ended(
                autotrade_run_id=autotrade_run_id, run_id=artifact_run_id,
                counts=dict(aggregate_counts),
                cash_delta_usd=round(cash_delta_total, 4),
                position_delta_by_ticker=dict(pos_delta_by_ticker),
                duration_sec=duration_sec,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] failed to log run_ended: {e}")
            finalize_status = "completed_with_errors"

        # ── summary + reports (best-effort) ─────────────────────
        summary_path: Optional[Path] = None
        out_paths: Dict[str, Path] = {}
        try:
            summary = store.build_summary(
                autotrade_run_id=autotrade_run_id, run_id=artifact_run_id,
            )
            summary["finalize_status"] = finalize_status
            summary_path = run_dir / "autotrade_summary.json"
            store.write_summary_json(summary, summary_path)
            out_paths = write_reports(summary, run_dir)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] failed to build/write summary or reports: {e}")
            finalize_status = "completed_with_errors"

        # ── operator-visible block (always printed) ─────────────
        print("\n" + "─" * 78)
        print(f"  ORCHESTRATOR RUN COMPLETE  ({duration_sec:.2f} s)  "
              f"[{finalize_status}]")
        print("─" * 78)
        print(f"  mode               : {mode_str}")
        print(f"  artifact run_id    : {artifact_run_id}")
        print(f"  autotrade_run_id   : {autotrade_run_id}")
        print(f"  intents resolved   : {len(buy_intents)}")
        print(f"  counts             : {dict(aggregate_counts)}")
        print(f"  cash delta (USD)   : {cash_delta_total:+,.2f}")
        print(f"  position delta     : {dict(pos_delta_by_ticker)}")
        print("─" * 78)
        print(f"  JSONL              : {store_path}")
        print(f"  summary.json       : {summary_path}")
        if out_paths:
            print(f"  report.md          : {out_paths.get('md')}")
            print(f"  report.json        : {out_paths.get('json')}")
            print(f"  report.csv         : {out_paths.get('csv')}")
        else:
            print(f"  report.*           : (NOT WRITTEN — see warnings above)")
        print("─" * 78)

        # TODO(R5): send_autotrade_email(out_paths.get('md'), summary,
        #     attachments=[out_paths.get('md'), out_paths.get('json'),
        #                  out_paths.get('csv'), store_path])

    return exit_rc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Round 4 — paper-only autotrade orchestrator skeleton "
                    "(dry-run default, --submit gates through KIS_PAPER_SUBMIT_OK)."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the orchestrator over one artifact")
    p_run.add_argument("--paper", action="store_true",
                       help="confirm paper route (mandatory in R4)")
    p_run.add_argument("--profile", default="paper", choices=list(_PROFILE_CONFIG))
    p_run.add_argument("--run-id", default=None,
                       help="specific artifact run id; default = latest awaiting_execution")
    p_run.add_argument("--submit", action="store_true",
                       help="enable real paper submit (still gated by KIS_PAPER_SUBMIT_OK)")
    p_run.add_argument("--quiet", action="store_true",
                       help="suppress KIS per-call verbose summary")
    p_run.add_argument("--max-orders", type=int, default=DEFAULT_MAX_ORDERS_PER_RUN,
                       help=f"hard cap on orders in one run (default {DEFAULT_MAX_ORDERS_PER_RUN})")
    p_run.add_argument("--max-notional-per-order", type=float,
                       default=DEFAULT_MAX_NOTIONAL_PER_ORDER_USD,
                       help="USD cap on a single order's notional")
    p_run.add_argument("--max-notional-per-run", type=float,
                       default=DEFAULT_MAX_NOTIONAL_PER_RUN_USD,
                       help="USD cap on the whole run's cumulative notional")
    p_run.add_argument("--echo-polls", type=int, default=DEFAULT_ECHO_POLLS,
                       help=f"echo poll attempts (default {DEFAULT_ECHO_POLLS})")
    p_run.add_argument("--echo-interval-sec", type=float, default=DEFAULT_ECHO_INTERVAL_SEC,
                       help=f"echo poll interval (default {DEFAULT_ECHO_INTERVAL_SEC}s)")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        if not args.paper:
            return _abort("--paper is mandatory in R4 (paper-only orchestrator).")
        return cmd_run(args)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
