"""Today's plan — submit ONE artifact-resolved BUY intent to KIS paper, hand
the operator a T10-ready execution summary, and stop. Manual T10 apply
remains the local-state writer.

This is the **strategy-faithful** counterpart to `paper_buy.py`:
`paper_buy.py` is a ticker-driven write-path test (ad hoc shares/limit);
this script is a recommendation-row-driven operational tool. Today's
acceptance criterion (Codex `TODAY_INTENT_BUY_T10.md` §11) is to send
exactly what `intents.py` resolved, capture broker evidence, then let
the operator reflect the fill via existing T10.

USAGE
-----
1. PREVIEW (always safe — no transmission regardless of env state)
    PYTHONPATH=. python3 -m phase3.autotrade.paper_execute_intent \\
        --profile paper --ticker ON --preview

2. REAL BUY (requires KIS_PAPER_SUBMIT_OK=true)
    KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_execute_intent \\
        --profile paper --ticker ON --yes

3. SELECT BY REC ROW ID (when ticker is ambiguous)
    KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_execute_intent \\
        --profile paper --rec-row-id 17 --yes

4. EXPLICIT RUN ID OVERRIDE (default: latest awaiting_execution)
    --run-id 20260511_214648_daily

GUARDRAILS (Codex TODAY §10)
----------------------------
- BUY-only. SELL/TRIM intents are filtered out before selection.
- One intent at a time. If selector matches >1 BUY, the script aborts and
  tells the operator to disambiguate via --rec-row-id.
- Risk flags abort the run (no override flag today).
- No local-state writes: `holdings_log.xlsx` and `run_meta.json` are not
  touched. T10 remains the sole local writer.
- Echo missing + position/cash MOVED = visibility issue, success.
- Echo missing + position/cash UNCHANGED = treat as not-filled, do NOT
  hand the operator a T10 summary that says it filled.

OUTPUT
------
- stdout: full per-step trace + a `T10 MANUAL APPLY SUMMARY` block at the
  bottom that the operator pastes into T10.
- JSON dump: `~/.kis_audit/intent_buy_<ts>_<run_id>_<ticker>.json` mode 600
  (full schema per Codex TODAY §8).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
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


# ─── helpers ────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _market_session_hint() -> str:
    """UTC-based approximation; precise enough for an operator hint."""
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour + now_utc.minute / 60.0
    if 13.5 <= hour < 20.0:
        return "US regular session is OPEN (approx, DST-aware band 13:30–20:00 UTC)."
    if 8.0 <= hour < 13.5:
        return "US PRE-MARKET (approx). Paper may queue or reject LIMIT orders."
    return "US session is CLOSED (approx). Paper will likely reject."


def _abort(msg: str) -> int:
    print(f"\n[intent_buy][ABORT] {msg}")
    return 2


# ─── data containers ────────────────────────────────────────────────────
@dataclass
class IntentBuyReport:
    timestamp: str
    profile: str
    kis_env: str
    paper_submit_ok: bool
    mode: str                # 'preview' | 'real_buy'
    run_id: str
    run_status: str
    rec_row_id: Optional[int]
    ticker: str
    action: str
    side: str
    qty: int
    selected_intent: Dict[str, Any] = field(default_factory=dict)
    order_intent: Dict[str, Any] = field(default_factory=dict)
    placed_order: Dict[str, Any] = field(default_factory=dict)
    pre_position_qty: Optional[int] = None
    post_position_qty: Optional[int] = None
    pre_cash_available: Optional[float] = None
    post_cash_available: Optional[float] = None
    cash_delta: Optional[float] = None
    echo_result: Dict[str, Any] = field(default_factory=dict)
    fill_price_source: str = "unavailable"     # 'broker' | 'cash_delta' | 'limit_fallback' | 'unavailable'
    fill_price: Optional[float] = None
    estimated_price_from_cash_delta: Optional[float] = None
    t10_manual_apply_summary: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    note: str = ""


# ─── intent selection ───────────────────────────────────────────────────
def select_one_buy(
    intents: List[ResolvedIntent],
    *,
    ticker: Optional[str],
    rec_row_id: Optional[int],
) -> tuple[Optional[ResolvedIntent], str]:
    """Return (chosen, reason). Codex TODAY §7.3 selector contract:
    - zero match → abort
    - exactly one → OK
    - >1 match → abort unless --rec-row-id disambiguates
    """
    buys = [i for i in intents if i.side == "BUY"]
    if not buys:
        return None, "no BUY intents resolved from artifact"

    if rec_row_id is not None:
        matches = [i for i in buys if int(i.rec_row_id) == int(rec_row_id)]
        if not matches:
            return None, f"--rec-row-id {rec_row_id} not found among BUY intents"
        if len(matches) > 1:
            return None, f"--rec-row-id {rec_row_id} matched {len(matches)} BUY rows (data corruption)"
        chosen = matches[0]
        if ticker and chosen.ticker != ticker.upper():
            return None, (f"--rec-row-id {rec_row_id} → {chosen.ticker}, "
                          f"conflicts with --ticker {ticker.upper()}")
        return chosen, "matched by rec_row_id"

    if ticker:
        wanted = ticker.upper()
        matches = [i for i in buys if i.ticker == wanted]
        if not matches:
            avail = ", ".join(sorted(set(i.ticker for i in buys)))
            return None, f"--ticker {wanted} not in BUY intents (available: {avail})"
        if len(matches) > 1:
            ids = ", ".join(str(i.rec_row_id) for i in matches)
            return None, (f"--ticker {wanted} matched {len(matches)} BUY rows. "
                          f"Disambiguate with --rec-row-id (candidates: {ids})")
        return matches[0], "matched by ticker"

    if len(buys) == 1:
        return buys[0], "single BUY intent in artifact"
    listing = ", ".join(f"{i.ticker}(qty={i.qty},row={i.rec_row_id})" for i in buys)
    return None, (f"{len(buys)} BUY intents available; pass --ticker or --rec-row-id "
                  f"to select one. Candidates: {listing}")


# ─── fill price extraction ──────────────────────────────────────────────
def derive_fill_price(
    *,
    echo: Dict[str, Any],
    pre_position_qty: int,
    post_position_qty: int,
    pre_cash: float,
    post_cash: float,
    limit_price: float,
) -> tuple[str, Optional[float], Optional[float]]:
    """Return (source, fill_price, estimated_price_from_cash_delta).

    Priority per Codex TODAY §7.7 + R3 P1.B (echo dual-source):
        1. broker echo row (preferred)
              - if echo came via ccnl: cumulative-fill price field
              - if echo came via nccs: only an open/pending order is
                visible. Use it for limit price diagnostics but NOT as
                an authoritative fill price (the order isn't filled yet
                from the nccs perspective). Caller still falls through
                to cash_delta which IS the real fill evidence.
        3. cash_delta / shares (estimated)
        4. limit_price as fallback (operator decides)
    """
    qty_delta = post_position_qty - pre_position_qty
    cash_delta = post_cash - pre_cash

    estimated: Optional[float] = None
    if qty_delta > 0 and cash_delta < 0:
        estimated = round(abs(cash_delta) / qty_delta, 4)

    source_of_echo = echo.get("source") if echo else None
    matched_row = echo.get("matched_row") if echo else None
    if matched_row and source_of_echo == "ccnl":
        for key in ("ft_ccld_unpr3", "ccld_unpr", "ft_ord_unpr3", "ord_unpr"):
            raw = matched_row.get(key)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if v > 0:
                return "broker", round(v, 4), estimated
    if estimated is not None:
        return "cash_delta", estimated, estimated
    return "limit_fallback", round(float(limit_price), 4), estimated


# ─── output ─────────────────────────────────────────────────────────────
def print_preview(intent: ResolvedIntent, *, run_id: str, env_url: str) -> None:
    bar = "─" * 78
    print(bar)
    print(f"  SELECTED BUY INTENT  run_id={run_id}  rec_row_id={intent.rec_row_id}")
    print(bar)
    print(f"  ticker             : {intent.ticker}")
    print(f"  action             : {intent.action}")
    print(f"  qty                : {intent.qty}")
    print(f"  artifact_price     : ${intent.artifact_price:.2f}  (snapshot at run-time)")
    print(f"  quote_last         : ${intent.quote_last:.2f}")
    print(f"  quote_ask / bid    : {intent.quote_ask} / {intent.quote_bid}")
    print(f"  limit_price        : ${intent.limit_price:.2f}  ({intent.limit_source})")
    print(f"  notional (≈)       : ${intent.limit_price * intent.qty:,.2f}")
    print(f"  risk_flags         : {intent.risk_flags or '(none)'}")
    print(f"  client_order_id    : {intent.client_order_id}")
    print()
    print(f"  KIS payload (POST {env_url}/uapi/overseas-stock/v1/trading/order):")
    print(f"  headers tr_id={intent.headers.get('tr_id')}  custtype=P  (Bearer omitted)")
    print(f"  body: {json.dumps(intent.payload, ensure_ascii=False)}")
    print(bar)


def print_t10_summary(rep: IntentBuyReport) -> None:
    bar = "─" * 78
    print()
    print(bar)
    print("  T10 MANUAL APPLY SUMMARY")
    print(bar)
    s = rep.t10_manual_apply_summary
    for k, v in s.items():
        print(f"  {k:32s} : {v}")
    print(bar)
    print("  Action for operator:")
    print("    1. Open launcher → T10 'Report Execution'.")
    print(f"    2. Apply RecRowId={s.get('RecRowId')} for {s.get('Ticker')} {s.get('Action')}.")
    print(f"    3. Use ExecutedShares = {s.get('ExecutedShares')}.")
    print(f"    4. Use ExecutedPrice  = {s.get('SuggestedT10Price')}  (source: {s.get('FillPriceSource')}).")
    print(f"    5. Paste ExecutionNote = '{s.get('ExecutionNote')}'.")
    print("    6. After saving, run reconcile to confirm qty_mismatch=0.")
    print(bar)


def dump_json(rep: IntentBuyReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = rep.timestamp.replace(":", "").replace("-", "")[:15]
    safe_run = rep.run_id.replace("/", "_")
    path = out_dir / f"intent_buy_{ts_compact}_{safe_run}_{rep.ticker}.json"
    path.write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ─── main ───────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Today's plan — submit ONE artifact-resolved BUY intent to KIS paper "
                    "and emit a T10-ready summary (no local state writes).",
    )
    ap.add_argument("--profile", default="paper", choices=list(_PROFILE_CONFIG))
    ap.add_argument("--run-id", default=None,
                    help="specific run id; default = latest awaiting_execution")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--ticker", default=None, help="select by symbol (e.g. ON)")
    sel.add_argument("--rec-row-id", type=int, default=None,
                     help="select by RecRowId from recommendations.csv")
    ap.add_argument("--preview", action="store_true",
                    help="dry-run only; never transmits")
    ap.add_argument("--yes", action="store_true",
                    help="confirm real transmission (still gated by KIS_PAPER_SUBMIT_OK)")
    ap.add_argument("--quiet", action="store_true", help="suppress KIS verbose per-call lines")
    ap.add_argument("--json-out-dir", default=None)
    args = ap.parse_args(argv)

    if not args.yes and not args.preview:
        print("[intent_buy] neither --yes nor --preview given → defaulting to --preview")
        args.preview = True

    cfg_path = _resolve_config_path(args.profile)
    cfg = load_config(str(cfg_path))
    env_cfg = load_env_config()

    if env_cfg.env_name != "paper":
        return _abort(f"refuse: KIS_ENV={env_cfg.env_name!r}. Paper-only today.")
    if not args.preview:
        if not env_cfg.paper_submit_ok:
            return _abort(
                "refuse: KIS_PAPER_SUBMIT_OK is not true. "
                "Run with `KIS_PAPER_SUBMIT_OK=true python3 -m phase3.autotrade.paper_execute_intent …`."
            )

    print(f"[intent_buy] profile={args.profile}  kis_env={env_cfg.env_name}  "
          f"paper_submit_ok={env_cfg.paper_submit_ok}")
    print(f"[intent_buy] {_market_session_hint()}")

    # Buy-only run; do not disable buy_only_mode.
    adapter = KisBrokerAdapter(cfg=env_cfg, safety_state=SafetyState(buy_only_mode=True),
                               verbose=not args.quiet)

    # 1. Artifact
    run_dir, run_meta, recos = load_artifact(
        cfg["paths"]["output_dir"], run_id=args.run_id,
    )
    run_id = str(run_meta.get("run_id") or run_dir.name)
    run_status = str(run_meta.get("status") or "")
    print(f"[intent_buy] artifact = {run_dir}")
    print(f"[intent_buy] status = {run_status}  reco_rows = {len(recos)}")

    # 2. Resolve intents (BUY-only filter at resolve time)
    intents, _skipped = resolve_intents(
        cfg=env_cfg, adapter=adapter, recos=recos, run_id=run_id,
        buy_only_mode=True, market="NASD",
        only_tickers=None,
        only_side="BUY",
    )
    buy_intents = [i for i in intents if i.side == "BUY"]
    print(f"[intent_buy] resolved {len(buy_intents)} BUY intent(s) "
          f"(SELL/SKIP filtered)")

    # 3. Selector
    chosen, why = select_one_buy(buy_intents, ticker=args.ticker, rec_row_id=args.rec_row_id)
    if chosen is None:
        return _abort(why)
    print(f"[intent_buy] selected: {chosen.ticker} rec_row_id={chosen.rec_row_id} "
          f"qty={chosen.qty} ({why})")
    if chosen.risk_flags:
        return _abort(f"selected intent has risk flags {chosen.risk_flags}; "
                      "refusing to submit. Inspect with `intents.py` first.")

    resolved_market = str(chosen.payload.get("OVRS_EXCG_CD", "NASD"))

    # 4. OrderIntent
    order_intent = OrderIntent(
        symbol=chosen.ticker, market=resolved_market, side="BUY",
        qty=chosen.qty, order_type="LIMIT", limit_price=chosen.limit_price,
        client_order_id=chosen.client_order_id,
        note=f"intent_buy run_id={run_id} rec_row_id={chosen.rec_row_id}",
    )

    rep = IntentBuyReport(
        timestamp=_now(), profile=args.profile,
        kis_env=env_cfg.env_name,
        paper_submit_ok=env_cfg.paper_submit_ok,
        mode=("preview" if args.preview else "real_buy"),
        run_id=run_id, run_status=run_status,
        rec_row_id=int(chosen.rec_row_id),
        ticker=chosen.ticker, action=chosen.action,
        side="BUY", qty=int(chosen.qty),
        selected_intent=asdict(chosen),
        order_intent=asdict(order_intent),
    )

    # 5. Preview
    print()
    print_preview(chosen, run_id=run_id, env_url=env_cfg.base_url)
    if args.preview:
        po = adapter.place_order(order_intent, dry_run=True)
        rep.placed_order = asdict(po)
        rep.success = True
        rep.note = "preview only — no transmission"
        out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
        out_path = dump_json(rep, out_dir)
        print(f"\n[intent_buy] preview complete (status={po.status})")
        print(f"[intent_buy] JSON dump → {out_path}")
        return 0

    # 6. Pre-trade snapshot
    print("\n[intent_buy] step pre — broker snapshot before submit")
    positions_pre = adapter.get_positions(market="NASD")
    pos_pre = next((p for p in positions_pre if p.symbol.upper() == chosen.ticker), None)
    held_pre = int(pos_pre.qty) if pos_pre else 0
    cash_pre = adapter.get_cash(market=resolved_market, ref_symbol=chosen.ticker,
                                ref_price=chosen.quote_last)
    rep.pre_position_qty = held_pre
    rep.pre_cash_available = float(cash_pre.available)
    print(f"  held_pre={held_pre}  cash_avail_pre=${cash_pre.available:,.2f}")

    # 7. Submit
    print("\n[intent_buy] step submit — POST /order")
    placed = adapter.place_order(order_intent, dry_run=False)
    rep.placed_order = asdict(placed)
    print(f"  → status={placed.status}  broker_order_id={placed.broker_order_id}")
    print(f"  raw_response_summary: {placed.raw_response_summary}")

    if placed.status == "rejected":
        rep.note = (f"broker rejected: "
                    f"{placed.raw_response_summary.get('error') if placed.raw_response_summary else ''}")
        out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
        out_path = dump_json(rep, out_dir)
        print(f"\n[intent_buy] REJECTED. No T10 summary generated.")
        print(f"[intent_buy] JSON dump → {out_path}")
        return 1
    if not placed.broker_order_id:
        rep.note = "submitted but no ODNO returned — treat as inconclusive"
        out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
        out_path = dump_json(rep, out_dir)
        print(f"\n[intent_buy] INCONCLUSIVE (no ODNO). No T10 summary generated.")
        print(f"[intent_buy] JSON dump → {out_path}")
        return 1

    # 8. Echo polling (R3 P1.B: nccs first → ccnl fallback)
    print("\n[intent_buy] step echo — inquire-nccs → inquire-ccnl polling")
    MAX_POLLS = 4
    echo = echo_poll(
        adapter, placed.broker_order_id, market=resolved_market,
        max_polls=MAX_POLLS, interval_sec=3.0,
        on_attempt=lambda a: print(attempt_stdout_line(a, max_polls=MAX_POLLS)),
    )
    if echo["matched"]:
        print(f"  → MATCHED via {echo['source']}.")
    else:
        print("  → not visible via nccs OR ccnl within budget. "
              "Treating position/cash delta as source of truth.")
    rep.echo_result = echo

    # 9. Post-trade snapshot
    print("\n[intent_buy] step post — broker snapshot after submit")
    positions_post = adapter.get_positions(market="NASD")
    pos_post = next((p for p in positions_post if p.symbol.upper() == chosen.ticker), None)
    held_post = int(pos_post.qty) if pos_post else 0
    cash_post = adapter.get_cash(market=resolved_market, ref_symbol=chosen.ticker,
                                 ref_price=chosen.quote_last)
    rep.post_position_qty = held_post
    rep.post_cash_available = float(cash_post.available)
    rep.cash_delta = round(rep.post_cash_available - rep.pre_cash_available, 4)
    print(f"  held: {held_pre} → {held_post}  (Δ={held_post - held_pre:+d})")
    print(f"  cash: ${cash_pre.available:,.2f} → ${cash_post.available:,.2f}  "
          f"(Δ=${rep.cash_delta:+,.2f})")

    qty_delta = held_post - held_pre
    if qty_delta == 0 and not echo["matched"]:
        # Codex TODAY §10: do not hand operator a fill summary in this case.
        rep.note = ("no position movement AND no echo — treat as not-filled. "
                    "Do NOT manually apply via T10.")
        out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
        out_path = dump_json(rep, out_dir)
        print(f"\n[intent_buy] NOT FILLED. {rep.note}")
        print(f"[intent_buy] JSON dump → {out_path}")
        return 1

    # 10. Fill price derivation
    source, fill_price, est_price = derive_fill_price(
        echo=echo,
        pre_position_qty=held_pre, post_position_qty=held_post,
        pre_cash=rep.pre_cash_available, post_cash=rep.post_cash_available,
        limit_price=chosen.limit_price,
    )
    rep.fill_price_source = source
    rep.fill_price = fill_price
    rep.estimated_price_from_cash_delta = est_price

    # 11. T10 summary
    note_str = (f"KIS paper ODNO={placed.broker_order_id} "
                f"client_order_id={chosen.client_order_id} "
                f"run_id={run_id} rec_row_id={chosen.rec_row_id} "
                f"fill_price_source={source}")
    rep.t10_manual_apply_summary = {
        "RunId": run_id,
        "RecRowId": chosen.rec_row_id,
        "Ticker": chosen.ticker,
        "Action": chosen.action,
        "ExecutedShares": chosen.qty,
        "BrokerOrderId": placed.broker_order_id,
        "LimitPrice": chosen.limit_price,
        "BrokerFillPrice": (fill_price if source == "broker" else "unavailable"),
        "EstimatedPriceFromCashDelta": est_price,
        "FillPriceSource": source,
        "SuggestedT10Price": fill_price,
        "ExecutionNote": note_str,
        "EchoVisible": echo["matched"],
        "EchoSource": echo["source"],     # 'nccs' | 'ccnl' | None
    }
    rep.success = True

    print_t10_summary(rep)

    out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
    out_path = dump_json(rep, out_dir)
    print(f"\n[intent_buy] success={rep.success}  note={rep.note or '-'}")
    print(f"[intent_buy] JSON dump → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
