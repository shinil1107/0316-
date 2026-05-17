"""Step 4 — paper marketable-limit round-trip operator script.

Goal (per Codex P2-C): the smallest possible end-to-end write-path
verification — submit → broker ack → history echo → optional unwind.
This is NOT an algorithmic trading driver, it is a safety harness.

USAGE
-----
1. PREVIEW (always safe — no transmission regardless of env state)
    PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \\
        --ticker ON --shares 1 --preview

2. REAL BUY (requires KIS_PAPER_SUBMIT_OK=true at the call site)
    KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \\
        --ticker ON --shares 1 --yes

3. UNWIND (sell back; requires both KIS_PAPER_SUBMIT_OK=true and --allow-sell
   since v0 default is buy_only_mode=True)
    KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \\
        --ticker ON --shares 1 --unwind --allow-sell --yes

PRE-FLIGHT GUARANTEES
---------------------
- Requires `--yes` for any actual transmission. Bare invocation defaults to
  --preview (dry-run intent only).
- Refuses to send if KIS_PAPER_SUBMIT_OK != true (refuses BEFORE building
  the payload so nothing accidentally hits the wire).
- Refuses live env (KIS_ENV != paper) since this script is paper-only.
- Marketable-limit pricing only (Codex P2-C): ask if present, else
  last * (1 ± 0.3 %). 2-decimal tick; BUY ceil / SELL floor.
- Echo polling: `inquire-nccs` first then `inquire-ccnl` fallback for up to
  ~12 seconds looking for our ODNO (Codex R3 P1.B).
- All decisions, payloads, and broker responses go to a JSON dump in
  ~/.kis_audit/paper_buy_<ts>.json (mode 600).

The script never modifies `holdings_log.xlsx`. Post-trade reconcile is a
separate step (Step 4 follow-up).
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

from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter,
    OrderIntent,
    SafetyState,
    load_env_config,
)
from phase3.autotrade.intents import (
    get_us_quote_with_fallback,
    marketable_limit,
)
from phase3.autotrade.echo import echo_poll, attempt_stdout_line


# ─── data containers ────────────────────────────────────────────────────
@dataclass
class StepRecord:
    name: str
    ts: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoundTripReport:
    timestamp: str
    ticker: str
    shares: int
    side: str
    mode: str                 # 'preview' | 'real_buy' | 'real_unwind'
    kis_env: str
    paper_submit_ok: bool
    steps: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = False
    note: str = ""


# ─── helpers ────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _market_session_hint() -> str:
    """One-line hint about the US regular-hours session relative to now."""
    from datetime import timezone as _tz
    now_utc = datetime.now(_tz.utc)
    # NYSE/NASDAQ regular: 14:30–21:00 UTC (no DST adjustment — close enough
    # for an operator-facing hint). Pre-market: 09:00–14:30 UTC.
    hour = now_utc.hour + now_utc.minute / 60.0
    if 14.5 <= hour < 21.0:
        return "US regular session is OPEN (approx)."
    if 9.0 <= hour < 14.5:
        return "US PRE-MARKET (approx). Paper may queue LIMIT orders."
    return "US session is CLOSED (approx). Paper behaviour: order may queue or reject."


def _add_step(report: RoundTripReport, name: str, **detail: Any) -> None:
    report.steps.append(asdict(StepRecord(name=name, ts=_now(), detail=detail)))


# ─── preflight ──────────────────────────────────────────────────────────
def preflight(env_cfg, *, args: argparse.Namespace) -> Optional[str]:
    """Return None if preflight passes, otherwise a human-readable failure reason."""
    if env_cfg.env_name != "paper":
        return (f"refuse: KIS_ENV={env_cfg.env_name!r}. This script is paper-only. "
                "Switch to paper before running Step 4.")
    if not args.preview:
        if not args.yes:
            return ("refuse: real transmission requires --yes. "
                    "Re-run with --preview to inspect, or --yes to send.")
        if not env_cfg.paper_submit_ok:
            return ("refuse: KIS_PAPER_SUBMIT_OK is not true. "
                    "Run with `KIS_PAPER_SUBMIT_OK=true python3 -m phase3.autotrade.paper_buy …` "
                    "to unlock the paper submit gate.")
        if args.unwind and not args.allow_sell:
            return ("refuse: --unwind implies SELL; pass --allow-sell to disable "
                    "buy_only_mode for this run.")
    return None


# ─── core round-trip ────────────────────────────────────────────────────
def run_round_trip(args: argparse.Namespace) -> RoundTripReport:
    env_cfg = load_env_config()
    side = "SELL" if args.unwind else "BUY"
    report = RoundTripReport(
        timestamp=_now(),
        ticker=args.ticker.upper(),
        shares=int(args.shares),
        side=side,
        mode=("preview" if args.preview else ("real_unwind" if args.unwind else "real_buy")),
        kis_env=env_cfg.env_name,
        paper_submit_ok=env_cfg.paper_submit_ok,
    )

    print(f"\n[step4] ticker={report.ticker}  shares={report.shares}  side={side}  "
          f"mode={report.mode}")
    print(f"[step4] kis_env={env_cfg.env_name}  paper_submit_ok={env_cfg.paper_submit_ok}")
    print(f"[step4] {_market_session_hint()}")

    fail = preflight(env_cfg, args=args)
    if fail:
        print(f"\n[step4][ABORT] {fail}")
        report.note = fail
        return report

    safety_state = SafetyState(buy_only_mode=not args.allow_sell)
    adapter = KisBrokerAdapter(cfg=env_cfg, safety_state=safety_state, verbose=True)

    # 1. Pre-trade snapshot
    print("\n[step4] step 1/5 — pre-trade snapshot")
    quote, resolved_market = get_us_quote_with_fallback(adapter, report.ticker)
    if quote.last <= 0:
        print(f"[step4][ABORT] quote.last is 0 for {report.ticker}; refusing to submit.")
        report.note = "zero quote"
        _add_step(report, "preflight_zero_quote", ticker=report.ticker, quote=asdict(quote))
        return report
    limit_price, limit_src = marketable_limit(quote, side)
    cash = adapter.get_cash(market=resolved_market, ref_symbol=report.ticker, ref_price=quote.last)
    positions = adapter.get_positions(market="NASD")
    pos_match = next((p for p in positions if p.symbol.upper() == report.ticker), None)
    held = int(pos_match.qty) if pos_match else 0
    print(f"  quote: last={quote.last:.2f}  bid={quote.bid}  ask={quote.ask}  market={resolved_market}")
    print(f"  limit: {limit_price:.2f} ({limit_src})  cash_avail=${cash.available:,.2f}  "
          f"held={held}")
    _add_step(
        report, "snapshot",
        quote=asdict(quote), resolved_market=resolved_market,
        limit_price=limit_price, limit_source=limit_src,
        cash_available_usd=cash.available, held=held,
    )

    if side == "SELL" and held < report.shares:
        print(f"[step4][ABORT] SELL but held={held} < requested {report.shares}")
        report.note = "insufficient holdings for SELL"
        return report

    intent = OrderIntent(
        symbol=report.ticker, market=resolved_market, side=side,
        qty=report.shares, order_type="LIMIT", limit_price=limit_price,
        note=f"step4_{report.mode}",
    )

    # 2. Preview always
    print("\n[step4] step 2/5 — payload preview")
    print(f"  intent: {asdict(intent)}")
    _add_step(report, "intent_built", intent=asdict(intent))

    if args.preview:
        # Force dry_run path through SafetyGuard for completeness.
        po = adapter.place_order(intent, dry_run=True)
        print(f"  preview-only: place_order(dry_run=True) → status={po.status}")
        _add_step(report, "preview_dryrun", status=po.status,
                  client_order_id=po.client_order_id, raw=po.raw_response_summary)
        report.success = True
        report.note = "preview only — no transmission"
        return report

    # 3. Real submit
    print("\n[step4] step 3/5 — submit")
    placed = adapter.place_order(intent, dry_run=False)
    print(f"  → status={placed.status}  client_order_id={placed.client_order_id}  "
          f"broker_order_id={placed.broker_order_id}")
    print(f"  raw_response_summary: {placed.raw_response_summary}")
    _add_step(report, "submit", status=placed.status,
              client_order_id=placed.client_order_id,
              broker_order_id=placed.broker_order_id,
              raw=placed.raw_response_summary)
    if placed.status == "rejected":
        report.note = f"broker rejected: {placed.raw_response_summary.get('error')}"
        return report
    if not placed.broker_order_id:
        report.note = "submitted but no ODNO returned"
        return report

    # 4. History echo (R3 P1.B: nccs first → ccnl fallback)
    print("\n[step4] step 4/5 — echo polling (inquire-nccs → inquire-ccnl)")
    MAX_POLLS = 4
    echo = echo_poll(
        adapter, placed.broker_order_id, market=resolved_market,
        max_polls=MAX_POLLS, interval_sec=3.0,
        on_attempt=lambda a: print(attempt_stdout_line(a, max_polls=MAX_POLLS)),
    )
    if echo["matched"]:
        print(f"  → MATCHED via {echo['source']}.")
        _add_step(report, "echo_matched", source=echo["source"],
                  matched_row=echo["matched_row"], attempts=echo["attempts"])
    else:
        print("  → not visible via nccs OR ccnl within budget. "
              "Position/cash deltas (below) are the source of truth.")
        _add_step(report, "echo_missing", odno=placed.broker_order_id,
                  attempts=echo["attempts"])

    # 5. Post-trade snapshot
    print("\n[step4] step 5/5 — post-trade snapshot")
    positions_after = adapter.get_positions(market="NASD")
    pos_after = next((p for p in positions_after if p.symbol.upper() == report.ticker), None)
    held_after = int(pos_after.qty) if pos_after else 0
    cash_after = adapter.get_cash(market=resolved_market, ref_symbol=report.ticker,
                                  ref_price=quote.last)
    print(f"  held_before={held}  held_after={held_after}  "
          f"delta={held_after - held:+d}")
    print(f"  cash_before=${cash.available:,.2f}  cash_after=${cash_after.available:,.2f}  "
          f"delta=${cash_after.available - cash.available:+,.2f}")
    _add_step(
        report, "post_trade",
        held_before=held, held_after=held_after,
        cash_before_usd=cash.available, cash_after_usd=cash_after.available,
    )

    report.success = True
    return report


# ─── output ─────────────────────────────────────────────────────────────
def dump_json(report: RoundTripReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = report.timestamp.replace(":", "").replace("-", "")[:15]
    path = out_dir / f"paper_buy_{ts_compact}_{report.ticker}_{report.side}.json"
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ─── CLI ────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 4 — paper marketable-limit round-trip (safety-gated)."
    )
    ap.add_argument("--ticker", required=True,
                    help="symbol to trade (uppercase, US-listed)")
    ap.add_argument("--shares", type=int, default=1,
                    help="share count (default: 1)")
    ap.add_argument("--unwind", action="store_true",
                    help="SELL instead of BUY (requires --allow-sell)")
    ap.add_argument("--allow-sell", action="store_true",
                    help="disable buy_only_mode for this run")
    ap.add_argument("--preview", action="store_true",
                    help="dry-run only; never transmits (default if --yes is absent)")
    ap.add_argument("--yes", action="store_true",
                    help="confirm real transmission (still gated by KIS_PAPER_SUBMIT_OK)")
    ap.add_argument("--json-out-dir", default=None)
    args = ap.parse_args(argv)

    if not args.yes and not args.preview:
        # Default to preview when neither is set to avoid foot-guns.
        print("[step4] neither --yes nor --preview given → defaulting to --preview")
        args.preview = True

    report = run_round_trip(args)

    env_cfg = load_env_config()
    out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
    out_path = dump_json(report, out_dir)
    print(f"\n[step4] success={report.success}  note={report.note or '-'}")
    print(f"[step4] JSON dump → {out_path}")
    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
