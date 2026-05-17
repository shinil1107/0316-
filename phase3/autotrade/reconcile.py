"""Step 3A — Local vs Broker reconcile report.

Inputs
------
- phase3 profile config (paper / real) — supplies `paths.holdings_log` and
  `paths.output_dir`.
- `holdings_log.xlsx` → `Current` sheet (Ticker, Shares, …) + cash balance.
- Latest actionable run artifact (`daily_runs/<run_id>/run_meta.json` whose
  status is "awaiting_execution") → `recommendations.csv` ticker universe.
- KIS broker state via `KisBrokerAdapter.get_positions()` + `get_cash()`.
  Paper-only at this stage (config.yaml profile=PAPER).

Outputs
-------
- stdout summary + per-bucket sample rows.
- Structured JSON dump at `~/.kis_audit/reconcile_<ts>.json` (or
  `KIS_LOG_DIR`-overridden) — the operational artifact of this step.

Bucket model (per Codex review P2-B / Q5.4)
-------------------------------------------
- `matched`             managed scope, qty equal (tolerance = 0)
- `qty_mismatch`        managed scope, qty diff
- `local_only`          held locally but not present at broker
- `broker_only_managed` held at broker AND ticker is in managed_scope
                        (e.g. recently sold locally, still in artifact reco)
- `background_broker`   held at broker but ticker is OUTSIDE managed_scope
                        (paper account accumulation from earlier manual
                        trades — surfaced separately so the managed report
                        is not buried in noise)
- `reco_only`           ticker appears in latest artifact recommendations
                        but neither held locally nor at broker
                        (typical case: BUY reco not yet executed, or SELL
                        reco already executed and removed everywhere)
- `cash_drift`          broker.cash_usd − local.cash_usd (absolute USD)
- `settlement_pending`  placeholder; later filled from `inquire-nccs`

Safety
------
- All local I/O is read-only. `holdings_log.xlsx` is never written.
- Broker calls are read-only (positions + cash). No order paths.
- Output JSON contains ticker + qty + price (account data, but not secret);
  goes to the same ~/.kis_audit/ directory which is outside the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ─── path bootstrap ─────────────────────────────────────────────────────
# `phase3/` itself must be importable so we can reuse `cache_health` and
# `holdings_manager`. When invoked as `python3 -m phase3.autotrade.reconcile`
# from the repo root, `phase3.*` works for autotrade siblings but the bare
# `cache_health` / `holdings_manager` imports require phase3 on sys.path.
_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from cache_health import load_config  # type: ignore[import-not-found]
from holdings_manager import HoldingsManager  # type: ignore[import-not-found]

from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter,
    EnvConfig,
    load_env_config,
)


# ─── profile resolution ─────────────────────────────────────────────────
_PROFILE_CONFIG: Dict[str, str] = {
    "paper": str(_PHASE3 / "config.yaml"),
    "real":  str(_PHASE3 / "config_real.yaml"),
}


def _resolve_config_path(profile: str) -> Path:
    profile = profile.lower()
    if profile not in _PROFILE_CONFIG:
        raise SystemExit(
            f"unknown profile {profile!r}, expected one of {list(_PROFILE_CONFIG)}"
        )
    p = Path(_PROFILE_CONFIG[profile])
    if not p.exists():
        raise SystemExit(f"profile config not found: {p}")
    return p


# ─── data containers ────────────────────────────────────────────────────
@dataclass
class LocalPosition:
    ticker: str
    shares: int
    avg_price: float
    current_price: float
    market_value: float
    source: str = "holdings_log"


@dataclass
class BrokerPosition:
    ticker: str
    qty: int
    avg_price: float
    market: str = "NASD"
    source: str = "kis_paper"


@dataclass
class TickerDiff:
    ticker: str
    bucket: str
    local_shares: Optional[int]
    broker_qty: Optional[int]
    local_avg_price: Optional[float]
    broker_avg_price: Optional[float]
    note: str = ""


@dataclass
class ReconcileReport:
    timestamp: str
    profile: str
    kis_env: str
    config_path: str
    holdings_log_path: str
    artifact_run_id: Optional[str]
    artifact_run_status: Optional[str]
    managed_scope_size: int
    local_only_count: int
    broker_only_managed_count: int
    background_broker_count: int
    matched_count: int
    qty_mismatch_count: int
    reco_only_count: int
    cash_local_usd: float
    cash_broker_usd: float
    cash_drift_usd: float
    settlement_pending_usd: float
    buckets: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


# ─── loaders ────────────────────────────────────────────────────────────
def load_local_state(cfg: Dict[str, Any]) -> Tuple[List[LocalPosition], float]:
    """Read Current sheet + cash balance from holdings_log.xlsx (read-only)."""
    holdings_path = cfg["paths"]["holdings_log"]
    hm = HoldingsManager(holdings_path)  # read-only access used below
    current = hm.load_current()
    cash = float(hm.get_cash_balance() or 0.0)

    rows: List[LocalPosition] = []
    if not current.empty:
        for _, r in current.iterrows():
            try:
                shares = int(r.get("Shares") or 0)
            except (TypeError, ValueError):
                shares = 0
            if shares <= 0:
                continue
            rows.append(LocalPosition(
                ticker=str(r.get("Ticker") or "").upper(),
                shares=shares,
                avg_price=float(r.get("BuyPrice") or 0.0),
                current_price=float(r.get("CurrentPrice") or 0.0),
                market_value=float(r.get("MarketValue") or 0.0),
            ))
    return rows, cash


def _find_latest_actionable_artifact(output_dir: str) -> Optional[Path]:
    """Return the most recent `daily_runs/<id>/` whose `run_meta.json`
    has status == 'awaiting_execution'. Returns None if there isn't one.
    """
    root = Path(output_dir) / "daily_runs"
    if not root.exists():
        return None
    candidates: List[Tuple[float, Path]] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "run_meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(meta.get("status", "")) != "awaiting_execution":
            continue
        candidates.append((meta_path.stat().st_mtime, run_dir))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def load_artifact_reco_universe(output_dir: str) -> Tuple[Optional[str], Optional[str], Set[str]]:
    """Returns (run_id, status, tickers_set). Empty set if no actionable run."""
    run_dir = _find_latest_actionable_artifact(output_dir)
    if run_dir is None:
        return None, None, set()
    meta_path = run_dir / "run_meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    reco_path = run_dir / "recommendations.csv"
    tickers: Set[str] = set()
    if reco_path.exists():
        try:
            df = pd.read_csv(reco_path)
            if "Ticker" in df.columns:
                tickers = {str(t).upper() for t in df["Ticker"].dropna().tolist()}
        except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
            tickers = set()
    return str(meta.get("run_id") or run_dir.name), str(meta.get("status") or ""), tickers


def load_broker_state(
    adapter: KisBrokerAdapter,
) -> Tuple[List[BrokerPosition], float]:
    positions = adapter.get_positions(market="NASD")
    rows = [
        BrokerPosition(
            ticker=p.symbol,
            qty=int(p.qty),
            avg_price=float(p.avg_price),
            market=p.market,
        )
        for p in positions
        if int(p.qty) > 0
    ]
    cash_obj = adapter.get_cash(market="NASD", ref_symbol="AAPL")
    return rows, float(cash_obj.available)


# ─── core reconcile ─────────────────────────────────────────────────────
def reconcile(
    local_positions: List[LocalPosition],
    local_cash: float,
    broker_positions: List[BrokerPosition],
    broker_cash: float,
    managed_scope_extra: Set[str],
    *,
    profile: str,
    config_path: str,
    holdings_log_path: str,
    kis_env: str,
    artifact_run_id: Optional[str],
    artifact_run_status: Optional[str],
) -> ReconcileReport:
    """Pure function: build the report from already-loaded inputs.

    Tolerance: qty == 0 (any diff = mismatch).
    Cash drift in absolute USD (no percentage threshold at v0).
    """
    local_by_ticker: Dict[str, LocalPosition] = {p.ticker: p for p in local_positions}
    broker_by_ticker: Dict[str, BrokerPosition] = {p.ticker: p for p in broker_positions}

    local_tickers: Set[str] = set(local_by_ticker)
    broker_tickers: Set[str] = set(broker_by_ticker)
    managed_scope: Set[str] = local_tickers | managed_scope_extra

    diffs: List[TickerDiff] = []

    # 1. union of locally-known tickers + tickers shared with broker:
    for t in sorted(local_tickers | broker_tickers | managed_scope_extra):
        lp = local_by_ticker.get(t)
        bp = broker_by_ticker.get(t)
        if lp and bp:
            if lp.shares == bp.qty:
                bucket = "matched"
                note = ""
            else:
                bucket = "qty_mismatch"
                note = f"diff = broker − local = {bp.qty - lp.shares:+d}"
        elif lp and not bp:
            bucket = "local_only"
            note = "held locally but not at broker"
        elif (not lp) and bp:
            if t in managed_scope:
                bucket = "broker_only_managed"
                note = "broker has shares; ticker still in managed scope"
            else:
                bucket = "background_broker"
                note = "broker accumulation outside managed scope"
        else:
            # Both absent — ticker shows up only via managed_scope_extra
            # (latest reco). Typical: BUY reco not yet executed, or SELL
            # reco already cleared on both sides.
            bucket = "reco_only"
            note = "in latest artifact reco; held nowhere"
        diffs.append(TickerDiff(
            ticker=t,
            bucket=bucket,
            local_shares=lp.shares if lp else None,
            broker_qty=bp.qty if bp else None,
            local_avg_price=lp.avg_price if lp else None,
            broker_avg_price=bp.avg_price if bp else None,
            note=note,
        ))

    bucket_groups: Dict[str, List[Dict[str, Any]]] = {
        "matched": [], "qty_mismatch": [], "local_only": [],
        "broker_only_managed": [], "background_broker": [], "reco_only": [],
    }
    for d in diffs:
        bucket_groups[d.bucket].append(asdict(d))

    cash_drift = broker_cash - local_cash

    return ReconcileReport(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        profile=profile,
        kis_env=kis_env,
        config_path=config_path,
        holdings_log_path=holdings_log_path,
        artifact_run_id=artifact_run_id,
        artifact_run_status=artifact_run_status,
        managed_scope_size=len(managed_scope),
        local_only_count=len(bucket_groups["local_only"]),
        broker_only_managed_count=len(bucket_groups["broker_only_managed"]),
        background_broker_count=len(bucket_groups["background_broker"]),
        matched_count=len(bucket_groups["matched"]),
        qty_mismatch_count=len(bucket_groups["qty_mismatch"]),
        reco_only_count=len(bucket_groups["reco_only"]),
        cash_local_usd=round(float(local_cash), 2),
        cash_broker_usd=round(float(broker_cash), 2),
        cash_drift_usd=round(float(cash_drift), 2),
        settlement_pending_usd=0.0,  # placeholder; wired in later step
        buckets=bucket_groups,
    )


# ─── output ─────────────────────────────────────────────────────────────
def print_report(rep: ReconcileReport, *, max_rows_per_bucket: int = 12) -> None:
    bar = "─" * 70
    print(bar)
    print(f"  RECONCILE  profile={rep.profile}  kis_env={rep.kis_env}  ts={rep.timestamp}")
    print(bar)
    print(f"  config             : {rep.config_path}")
    print(f"  holdings_log       : {rep.holdings_log_path}")
    print(f"  latest artifact run: {rep.artifact_run_id or '(none — no awaiting_execution)'}")
    print(f"  artifact status    : {rep.artifact_run_status or '-'}")
    print()
    print(f"  managed_scope      : {rep.managed_scope_size} tickers "
          f"(current ∪ latest reco)")
    print(f"  matched            : {rep.matched_count}")
    print(f"  qty_mismatch       : {rep.qty_mismatch_count}")
    print(f"  local_only         : {rep.local_only_count}")
    print(f"  broker_only_managed: {rep.broker_only_managed_count}")
    print(f"  background_broker  : {rep.background_broker_count}")
    print(f"  reco_only          : {rep.reco_only_count}")
    print()
    print(f"  cash local         : ${rep.cash_local_usd:,.2f}")
    print(f"  cash broker (USD)  : ${rep.cash_broker_usd:,.2f}")
    print(f"  cash drift         : ${rep.cash_drift_usd:+,.2f}   (broker − local)")
    print(f"  settlement_pending : ${rep.settlement_pending_usd:,.2f}  (placeholder)")

    for bucket_name in ("qty_mismatch", "local_only", "broker_only_managed",
                        "background_broker", "reco_only", "matched"):
        rows = rep.buckets.get(bucket_name) or []
        if not rows:
            continue
        print()
        print(f"  ── {bucket_name}  ({len(rows)} rows)")
        print(f"     {'ticker':8s} {'local_qty':>10s} {'broker_qty':>10s} "
              f"{'local_avg':>10s} {'broker_avg':>10s}  note")
        for r in rows[:max_rows_per_bucket]:
            lq = r.get("local_shares")
            bq = r.get("broker_qty")
            la = r.get("local_avg_price")
            ba = r.get("broker_avg_price")
            print(f"     {r['ticker']:8s} "
                  f"{(str(lq) if lq is not None else '-'):>10s} "
                  f"{(str(bq) if bq is not None else '-'):>10s} "
                  f"{('%.2f' % la if la is not None else '-'):>10s} "
                  f"{('%.2f' % ba if ba is not None else '-'):>10s}  "
                  f"{r.get('note','')}")
        if len(rows) > max_rows_per_bucket:
            print(f"     … {len(rows) - max_rows_per_bucket} more (see JSON dump)")
    print(bar)


def dump_json(rep: ReconcileReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = rep.timestamp.replace(":", "").replace("-", "")[:15]
    path = out_dir / f"reconcile_{ts_compact}_{rep.profile}.json"
    path.write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ─── CLI ────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 3A — local-vs-broker reconcile report (read-only).",
    )
    ap.add_argument("--profile", default="paper", choices=list(_PROFILE_CONFIG),
                    help="phase3 profile to read local truth from (default: paper)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-call KIS verbose lines")
    ap.add_argument("--no-artifact", action="store_true",
                    help="skip the latest actionable artifact lookup "
                         "(managed_scope = local current tickers only)")
    ap.add_argument("--json-out-dir", default=None,
                    help="override output directory for JSON dump "
                         "(default: $KIS_LOG_DIR or ~/.kis_audit/)")
    args = ap.parse_args(argv)

    cfg_path = _resolve_config_path(args.profile)
    cfg = load_config(str(cfg_path))

    # broker config — paper for now. Allowing real later is a deliberate
    # follow-up: cross-checking real-profile local state against paper
    # broker state would be misleading.
    env_cfg: EnvConfig = load_env_config()
    if args.profile == "real" and env_cfg.is_paper:
        print("[warn] phase3 profile=real but KIS_ENV=paper — running anyway, "
              "but reconcile will show massive drift. Set KIS_ENV=live in .env "
              "to enable real cross-check.")

    adapter = KisBrokerAdapter(cfg=env_cfg, verbose=not args.quiet)

    print(f"[load] profile={args.profile}  config={cfg_path}")
    print(f"[load] holdings_log: {cfg['paths']['holdings_log']}")
    local_positions, local_cash = load_local_state(cfg)
    print(f"[load] local positions: {len(local_positions)} tickers, "
          f"cash=${local_cash:,.2f}")

    if args.no_artifact:
        artifact_id, artifact_status, artifact_tickers = None, None, set()
        print("[load] artifact lookup skipped (--no-artifact)")
    else:
        artifact_id, artifact_status, artifact_tickers = load_artifact_reco_universe(
            cfg["paths"]["output_dir"]
        )
        print(f"[load] latest actionable artifact: "
              f"{artifact_id or '(none)'} ({artifact_status or '-'}); "
              f"reco tickers = {len(artifact_tickers)}")

    print(f"[load] broker (KIS {env_cfg.env_name}) positions + cash …")
    broker_positions, broker_cash = load_broker_state(adapter)
    print(f"[load] broker positions: {len(broker_positions)} tickers, "
          f"cash_available=${broker_cash:,.2f}")

    rep = reconcile(
        local_positions=local_positions,
        local_cash=local_cash,
        broker_positions=broker_positions,
        broker_cash=broker_cash,
        managed_scope_extra=artifact_tickers,
        profile=args.profile,
        config_path=str(cfg_path),
        holdings_log_path=cfg["paths"]["holdings_log"],
        kis_env=env_cfg.env_name,
        artifact_run_id=artifact_id,
        artifact_run_status=artifact_status,
    )

    print()
    print_report(rep)

    out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
    out_path = dump_json(rep, out_dir)
    print(f"\n[ok] JSON dump → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
