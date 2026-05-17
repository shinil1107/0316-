"""Step 3B — Artifact-first order intent dry-run.

This step turns the latest *actionable* daily run artifact (the canonical
output of `daily_runner.py`) into a list of KIS-ready `OrderIntent` objects,
attaches a marketable-limit price, runs pre-trade risk checks, and emits the
exact REST payload that *would* be sent — **but does not transmit**.

It is intentionally read-only and decoupled from any broker write path. The
goal is to surface every translation decision (action → side, qty, limit
price, lineage, risk flags) so the operator can verify the autotrade
pipeline before any real submission.

INPUT
-----
1. `daily_runs/<run_id>/run_meta.json` with status == "awaiting_execution"
   (auto-located; override via `--run-id`)
2. `recommendations.csv` (lineage: RunId, RecRowId, Ticker, Action,
   Shares, Price, Capital, Regime, …)
3. KIS broker state — `get_positions()` + `get_cash()` (for risk checks)
4. KIS quote per actionable ticker — for marketable-limit pricing

OUTPUT
------
- stdout per-intent table with risk flags
- JSON dump `~/.kis_audit/intents_<ts>_<profile>.json` (mode 600)

POLICIES (per Codex review)
---------------------------
- Paper LIMIT only (`ORD_DVSN=00`). Market orders disallowed in paper.
- Marketable-limit pricing:
    BUY  : `ask` if available, else `last * (1 + buy_buffer_pct)` (default 0.3%)
    SELL : `bid` if available, else `last * (1 − sell_buffer_pct)` (default 0.3%)
  Tick rounding: 2 decimals; BUY ceil, SELL floor (favours fill).
- `buy_only_mode` default: SELL intents are still **built and dry-run logged**,
  but flagged as `BLOCKED_BY_BUY_ONLY`. Disabling buy_only is a separate step.

WHAT THIS STEP DOES NOT DO
--------------------------
- Submit any order, modify, or cancel.
- Update local holdings_log or any artifact status.
- Calculate position sizing — qty comes straight from the reco row.

Action dispatch reuses `phase3.exits.RecosAction` so any future action
suffix added there is picked up automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from cache_health import load_config  # type: ignore[import-not-found]
from exits import RecosAction  # type: ignore[import-not-found]

from phase3.autotrade.kis_broker_adapter import (
    EP_ORDER,
    KisBrokerAdapter,
    OrderIntent,
    Quote,
    load_env_config,
    EnvConfig,
)
from phase3.autotrade.reconcile import (
    _PROFILE_CONFIG,
    _resolve_config_path,
    _find_latest_actionable_artifact,
    load_broker_state,
)


# ─── action classification ──────────────────────────────────────────────
_BUY_ACTIONS: Set[str] = {"BUY", "BUY_NEW", "BUY_MORE"}
_INFO_ACTIONS: Set[str] = {"HOLD", "DEFERRED"}  # never executable

# US exchange fallback. The QUOTE endpoint requires the *listing* venue,
# not just any US-exchange code (CIEN is NYSE-listed, so EXCD=NAS returns
# zeroes). We try in order until a non-zero `last` comes back. The chosen
# exchange is then echoed into the order payload's OVRS_EXCG_CD so the
# write path stays aligned with the price source.
_US_EXCHANGE_FALLBACK: Tuple[str, ...] = ("NASD", "NYSE", "AMEX")


def get_us_quote_with_fallback(
    adapter: KisBrokerAdapter, ticker: str,
) -> Tuple[Quote, str]:
    """Return (quote, resolved_market). Tries NASD → NYSE → AMEX until
    quote.last > 0; returns the last attempt (NASD/NYSE/AMEX) otherwise.
    """
    last_quote: Optional[Quote] = None
    last_market = _US_EXCHANGE_FALLBACK[0]
    for mkt in _US_EXCHANGE_FALLBACK:
        q = adapter.get_quote(ticker, market=mkt)
        last_quote = q
        last_market = mkt
        if q.last > 0:
            return q, mkt
    # All zero — return the most recent attempt for diagnostics.
    return (last_quote if last_quote is not None
            else Quote(symbol=ticker.upper(), market="NASD", last=0.0,
                       bid=None, ask=None,
                       asof=datetime.now(timezone.utc).isoformat())), last_market


def classify_action(action: str) -> str:
    """Return one of 'BUY' / 'SELL' / 'SKIP'."""
    a = (action or "").upper().strip()
    if a in _BUY_ACTIONS:
        return "BUY"
    if RecosAction.is_full_close(a) or RecosAction.is_partial_close(a):
        return "SELL"
    if RecosAction.is_no_op(a) or a in _INFO_ACTIONS:
        return "SKIP"
    return "SKIP"


# ─── pricing ────────────────────────────────────────────────────────────
_DEFAULT_BUFFER = 0.003   # 0.3 % — midpoint of Codex's 0.2~0.5 % range


def _tick_round(price: float, side: str, *, decimals: int = 2) -> float:
    """Round to tick (default 2 decimals). BUY ceils, SELL floors so the
    limit always favours fill in marketable conditions."""
    factor = 10 ** decimals
    if side == "BUY":
        return math.ceil(price * factor) / factor
    return math.floor(price * factor) / factor


def marketable_limit(
    quote: Quote,
    side: str,
    *,
    buy_buffer_pct: float = _DEFAULT_BUFFER,
    sell_buffer_pct: float = _DEFAULT_BUFFER,
) -> Tuple[float, str]:
    """Return (limit_price, source_tag).

    Source tag is one of: 'ask', 'last+buffer', 'bid', 'last-buffer'.
    Used by the reporter so the operator sees how each price was derived.
    """
    if side == "BUY":
        if quote.ask and quote.ask > 0:
            return _tick_round(float(quote.ask), "BUY"), "ask"
        return _tick_round(quote.last * (1 + buy_buffer_pct), "BUY"), "last+buffer"
    if quote.bid and quote.bid > 0:
        return _tick_round(float(quote.bid), "SELL"), "bid"
    return _tick_round(quote.last * (1 - sell_buffer_pct), "SELL"), "last-buffer"


# ─── data containers ────────────────────────────────────────────────────
@dataclass
class ResolvedIntent:
    run_id: str
    rec_row_id: int
    ticker: str
    action: str            # original artifact action string (e.g. "TRIM_PROFIT")
    side: str              # canonical: "BUY" or "SELL"
    qty: int
    artifact_price: float  # snapshot price stored when reco was generated
    quote_last: float
    quote_bid: Optional[float]
    quote_ask: Optional[float]
    limit_price: float
    limit_source: str      # 'ask' | 'bid' | 'last+buffer' | 'last-buffer'
    risk_flags: List[str]
    payload: Dict[str, Any]    # KIS REST body (transport-ready, no auth headers)
    headers: Dict[str, str]    # tr_id + custtype (no Bearer)
    client_order_id: str
    note: str = ""


@dataclass
class Step3BReport:
    timestamp: str
    profile: str
    kis_env: str
    run_id: str
    run_status: str
    counts_by_side: Dict[str, int]
    counts_by_flag: Dict[str, int]
    intents: List[Dict[str, Any]] = field(default_factory=list)


# ─── artifact loading ───────────────────────────────────────────────────
def load_artifact(
    output_dir: str,
    *,
    run_id: Optional[str] = None,
) -> Tuple[Path, Dict[str, Any], pd.DataFrame]:
    """Resolve and load (run_dir, run_meta, recommendations_df)."""
    root = Path(output_dir) / "daily_runs"
    if run_id:
        run_dir = root / run_id
        if not run_dir.exists():
            raise SystemExit(f"--run-id {run_id!r} not found under {root}")
    else:
        candidate = _find_latest_actionable_artifact(output_dir)
        if candidate is None:
            raise SystemExit(
                "no actionable artifact found (status=awaiting_execution). "
                "Use --run-id to target a specific run, or run "
                "daily_runner.py first."
            )
        run_dir = candidate

    meta_path = run_dir / "run_meta.json"
    try:
        run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"failed to read {meta_path}: {e}")

    reco_path = run_dir / "recommendations.csv"
    if not reco_path.exists():
        raise SystemExit(f"recommendations.csv missing in {run_dir}")
    recos = pd.read_csv(reco_path)
    return run_dir, run_meta, recos


# ─── pre-trade risk checks (subset of blueprint §7.1) ──────────────────
def pre_trade_risk(
    *,
    side: str,
    qty: int,
    ticker: str,
    limit_price: float,
    buy_only_mode: bool,
    broker_positions_by_ticker: Dict[str, int],
    cumulative_buy_capital_usd: float,
    broker_cash_available_usd: float,
    quote_last: float,
) -> List[str]:
    flags: List[str] = []
    if qty <= 0:
        flags.append("INVALID_QTY")
    if limit_price <= 0 or quote_last <= 0:
        flags.append("INVALID_PRICE")
    if side not in ("BUY", "SELL"):
        flags.append("UNKNOWN_SIDE")

    if side == "SELL":
        held = broker_positions_by_ticker.get(ticker, 0)
        if held <= 0:
            flags.append("NO_BROKER_POSITION")
        elif qty > held:
            flags.append(f"OVERSELL(qty>held{qty}/{held})")
        if buy_only_mode:
            flags.append("BLOCKED_BY_BUY_ONLY")

    if side == "BUY":
        # cumulative across the batch (passed in by caller).
        notional = limit_price * qty
        projected = cumulative_buy_capital_usd + notional
        if projected > broker_cash_available_usd:
            flags.append(
                f"INSUFFICIENT_CASH(proj=${projected:,.2f} > avail=${broker_cash_available_usd:,.2f})"
            )
    return flags


# ─── KIS payload assembly ───────────────────────────────────────────────
def build_kis_payload(
    cfg: EnvConfig,
    adapter: KisBrokerAdapter,
    *,
    side: str,
    ticker: str,
    qty: int,
    limit_price: float,
    market: str = "NASD",
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Return (body, headers) for `POST /uapi/overseas-stock/v1/trading/order`.
    Headers omit the Bearer token (kept out of dumps) but include tr_id +
    custtype so the operator can review exactly what would be sent.

    Paper requires ORD_DVSN=00 (LIMIT). We hard-code that.
    """
    tr_id_key = "order_buy" if side == "BUY" else "order_sell"
    tr_id = adapter._tr(tr_id_key)
    body: Dict[str, Any] = {
        "CANO": cfg.account_no,
        "ACNT_PRDT_CD": cfg.account_product_code,
        "OVRS_EXCG_CD": market,
        "PDNO": ticker,
        "ORD_QTY": str(int(qty)),
        # KIS string format: integer.decimal, here we use 4 decimals
        # (enough for sub-penny tickers later). Tick is enforced by us.
        "OVRS_ORD_UNPR": f"{limit_price:.4f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # LIMIT — only option in paper
    }
    if side == "SELL":
        body["SLL_TYPE"] = "00"  # 일반매도

    headers = {
        "content-type": "application/json; charset=UTF-8",
        "tr_id": tr_id,
        "custtype": "P",
        # Authorization: Bearer <token>  ← intentionally not dumped
    }
    return body, headers


# ─── main resolution pipeline ───────────────────────────────────────────
def _to_int(x: Any) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _to_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def resolve_intents(
    cfg: EnvConfig,
    adapter: KisBrokerAdapter,
    recos: pd.DataFrame,
    *,
    run_id: str,
    buy_only_mode: bool,
    market: str = "NASD",
    only_tickers: Optional[Set[str]] = None,
    only_side: Optional[str] = None,  # 'BUY' / 'SELL' / None
) -> Tuple[List[ResolvedIntent], List[Tuple[str, str, str]]]:
    """Walk the reco rows and build ResolvedIntents.

    Returns (intents, skipped) where `skipped` is a list of
    (ticker, action, reason) tuples for operator visibility.
    """
    broker_positions, broker_cash = load_broker_state(adapter)
    broker_qty_by_ticker = {p.ticker.upper(): int(p.qty) for p in broker_positions}

    intents: List[ResolvedIntent] = []
    skipped: List[Tuple[str, str, str]] = []
    cumulative_buy_capital = 0.0

    for _, row in recos.iterrows():
        ticker = str(row.get("Ticker", "") or "").upper()
        action = str(row.get("Action", "") or "").upper()
        side = classify_action(action)

        if only_tickers and ticker not in only_tickers:
            continue
        if side == "SKIP":
            skipped.append((ticker, action, "no-op / info-only"))
            continue
        if only_side and side != only_side:
            continue

        qty = _to_int(row.get("Shares", 0))
        if qty <= 0:
            skipped.append((ticker, action, "zero or negative qty in reco"))
            continue

        artifact_price = _to_float(row.get("Price", 0.0))

        # Quote — uses verbose stream so operator can see each call.
        # Fallback across NASD/NYSE/AMEX since the QUOTE endpoint needs the
        # actual listing venue.
        try:
            quote, resolved_market = get_us_quote_with_fallback(adapter, ticker)
        except Exception as e:  # noqa: BLE001
            skipped.append((ticker, action, f"quote failed: {type(e).__name__}: {e}"))
            continue

        limit_price, limit_source = marketable_limit(quote, side)

        risk_flags = pre_trade_risk(
            side=side, qty=qty, ticker=ticker, limit_price=limit_price,
            buy_only_mode=buy_only_mode,
            broker_positions_by_ticker=broker_qty_by_ticker,
            cumulative_buy_capital_usd=cumulative_buy_capital,
            broker_cash_available_usd=broker_cash,
            quote_last=quote.last,
        )

        body, headers = build_kis_payload(
            cfg, adapter, side=side, ticker=ticker, qty=qty,
            limit_price=limit_price, market=resolved_market,
        )
        client_order_id = f"co-{uuid.uuid4().hex[:12]}"

        ri = ResolvedIntent(
            run_id=run_id,
            rec_row_id=_to_int(row.get("RecRowId", 0)),
            ticker=ticker,
            action=action,
            side=side,
            qty=qty,
            artifact_price=artifact_price,
            quote_last=quote.last,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            limit_price=limit_price,
            limit_source=limit_source,
            risk_flags=risk_flags,
            payload=body,
            headers=headers,
            client_order_id=client_order_id,
        )
        intents.append(ri)

        if side == "BUY" and "INSUFFICIENT_CASH" not in " ".join(risk_flags):
            cumulative_buy_capital += limit_price * qty

    return intents, skipped


# ─── reporting ──────────────────────────────────────────────────────────
def print_intents(report: Step3BReport, *, max_rows: int = 50) -> None:
    bar = "─" * 90
    print(bar)
    print(f"  ORDER INTENTS  profile={report.profile}  kis_env={report.kis_env}  "
          f"run_id={report.run_id} ({report.run_status})  ts={report.timestamp}")
    print(bar)
    print(f"  counts by side: {report.counts_by_side}")
    print(f"  counts by flag: {report.counts_by_flag or '(none)'}")
    if not report.intents:
        print("  (no intents produced — all reco rows were SKIP or filtered)")
        print(bar)
        return
    print()
    print(f"  {'ticker':6s} {'action':16s} {'side':4s} {'qty':>4s}  "
          f"{'limit':>9s} ({'src':10s})  "
          f"{'last':>9s} {'ask':>9s} {'bid':>9s}  flags")
    for i, it in enumerate(report.intents[:max_rows]):
        flags = ",".join(it.get("risk_flags") or []) or "-"
        ask = "-" if it.get("quote_ask") is None else f"{it['quote_ask']:.2f}"
        bid = "-" if it.get("quote_bid") is None else f"{it['quote_bid']:.2f}"
        print(f"  {it['ticker']:6s} {it['action']:16s} {it['side']:4s} {it['qty']:>4d}  "
              f"{it['limit_price']:>9.2f} ({it['limit_source']:10s})  "
              f"{it['quote_last']:>9.2f} {ask:>9s} {bid:>9s}  {flags}")
    if len(report.intents) > max_rows:
        print(f"  … {len(report.intents) - max_rows} more (see JSON dump)")
    print(bar)


def dump_json(report: Step3BReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = report.timestamp.replace(":", "").replace("-", "")[:15]
    path = out_dir / f"intents_{ts_compact}_{report.profile}.json"
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ─── CLI ────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 3B — artifact-first order intent dry-run (no transmission)."
    )
    ap.add_argument("--profile", default="paper", choices=list(_PROFILE_CONFIG),
                    help="phase3 profile (default: paper)")
    ap.add_argument("--run-id", default=None,
                    help="specific run id under daily_runs/. "
                         "Default: latest awaiting_execution.")
    ap.add_argument("--ticker", default=None,
                    help="comma-separated tickers to filter (uppercase)")
    ap.add_argument("--side", default=None, choices=["BUY", "SELL"],
                    help="only resolve intents on one side")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress KIS verbose per-call lines")
    ap.add_argument("--allow-sell", action="store_true",
                    help="lift buy_only_mode (still dry-run; flags only)")
    ap.add_argument("--json-out-dir", default=None,
                    help="override output directory for JSON dump")
    args = ap.parse_args(argv)

    cfg_path = _resolve_config_path(args.profile)
    cfg_dict = load_config(str(cfg_path))

    env_cfg = load_env_config()
    if args.profile == "real" and env_cfg.is_paper:
        print("[warn] phase3 profile=real but KIS_ENV=paper — running anyway.")
    adapter = KisBrokerAdapter(cfg=env_cfg, verbose=not args.quiet)

    only_tickers: Optional[Set[str]] = None
    if args.ticker:
        only_tickers = {t.strip().upper() for t in args.ticker.split(",") if t.strip()}

    print(f"[load] profile={args.profile}  config={cfg_path}")
    run_dir, run_meta, recos = load_artifact(
        cfg_dict["paths"]["output_dir"], run_id=args.run_id,
    )
    run_id = str(run_meta.get("run_id") or run_dir.name)
    run_status = str(run_meta.get("status") or "")
    print(f"[load] artifact: {run_dir}")
    print(f"[load] status={run_status}  rows={len(recos)}")
    if run_status != "awaiting_execution":
        print(f"[warn] artifact status is {run_status!r}, "
              "not 'awaiting_execution'. Resolving anyway.")

    buy_only = (not args.allow_sell)
    intents, skipped = resolve_intents(
        cfg=env_cfg,
        adapter=adapter,
        recos=recos,
        run_id=run_id,
        buy_only_mode=buy_only,
        market="NASD",
        only_tickers=only_tickers,
        only_side=args.side,
    )

    if skipped:
        print(f"\n[skip] {len(skipped)} reco rows skipped:")
        for tk, ac, why in skipped[:20]:
            print(f"   - {tk:6s} {ac:16s}  {why}")
        if len(skipped) > 20:
            print(f"   … {len(skipped) - 20} more")

    counts_by_side: Dict[str, int] = {}
    counts_by_flag: Dict[str, int] = {}
    for it in intents:
        counts_by_side[it.side] = counts_by_side.get(it.side, 0) + 1
        for f in it.risk_flags:
            key = f.split("(")[0]   # collapse parametrised flags
            counts_by_flag[key] = counts_by_flag.get(key, 0) + 1

    report = Step3BReport(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        profile=args.profile,
        kis_env=env_cfg.env_name,
        run_id=run_id,
        run_status=run_status,
        counts_by_side=counts_by_side,
        counts_by_flag=counts_by_flag,
        intents=[asdict(it) for it in intents],
    )

    print()
    print_intents(report)

    out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
    out_path = dump_json(report, out_dir)
    print(f"\n[ok] JSON dump → {out_path}")
    print(f"     Endpoint that WOULD be called: POST {env_cfg.base_url}{EP_ORDER}")
    print(f"     (Step 3B never transmits. Step 4 will, behind a separate gate.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
