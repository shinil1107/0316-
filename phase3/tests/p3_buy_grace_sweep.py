"""P3 — Buy-grace days sweep (Idea 2, variant a = strict).

Runs the Baseline_V2 signal across N values for ``buy_grace_days``
keeping every other variable identical (signal, exit stack, capital,
buy-limit, slippage, regime classification). Implements the strict
variant: a non-held ticker must have appeared in the regime-correct
top-N on each of the last K rebalance days before a NEW position opens.
``BUY_MORE`` (scaling existing positions) is unaffected.

Why this exists
---------------
Phase 5 retrain experiments (signal-layer redesign) all failed to
beat Baseline_V2. The ``buy_grace_days`` knob shifts the optimisation
target from the *signal* layer to the *execution* layer: same alpha,
quieter trading. Three empirical questions:

1. Does grace > 0 reduce realised commission / turnover meaningfully?
2. Does grace > 0 preserve (or even improve) CAGR through noise
   filtering, or does it lag too much in fast-moving regimes?
3. Does grace > 0 alter the SPY-relative alpha profile per fold?
   (Question 3 is answered by re-running step_e_spy_benchmark on the
   sweep-produced artefacts.)

Sweep
-----
Default: N ∈ {0, 1, 2, 3, 5}.  N=0 must produce byte-identical results
to the legacy run (this is verified separately in a paired smoke test).
Window: 2012-01-01 → pack-end. Runtime: ~3 min/run × 5 runs ≈ 15 min.

Outputs
-------
- Console summary: per-N CAGR, Sharpe, MDD, Calmar, Comm%, regime
  breakdown, plus ``buy_grace_filtered_total`` diagnostic.
- ``phase3/docs/p3_buy_grace_sweep_<TS>.json`` (machine-readable)
- ``phase3/docs/p3_buy_grace_sweep_<TS>.md``  (human-readable summary)

Usage
-----
    python3 -u phase3/tests/p3_buy_grace_sweep.py
    python3 -u phase3/tests/p3_buy_grace_sweep.py --grace 0,1,2,3
    python3 -u phase3/tests/p3_buy_grace_sweep.py --signal <path> \
        --leg-name MyArm
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3 import simulator  # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg, _load_vix, _make_strategy,
)

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"

PACK_START_STR = "2011-01-03"
PACK_END_STR   = "2026-02-27"

DEFAULT_SIGNAL = os.path.join(
    OUTPUT_SIG_DIR, "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
)
DEFAULT_LEG_NAME = "Baseline_V2"
DEFAULT_GRACE = "0,1,2,3,5"


def _pick_walk_forward_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(f"No walk-forward pack found matching {pattern}")
    p = candidates[0]
    stem = os.path.splitext(os.path.basename(p))[0]
    parts = stem.split("_")
    start, end = parts[-2], parts[-1]
    return p, start, end


def _run_one(
    grace: int, signal_path: str, leg_name: str,
    cfg, pack,
    vix_close_map: Dict[str, float],
    vix_regime_map: Dict[str, str],
    vix_smooth_map: Dict[str, float],
    trigger_conf,
    start_d: str, end_d: str,
) -> Dict[str, Any]:
    """Run one full-period simulation with ``buy_grace_days = grace``."""
    print()
    print("=" * 72)
    print(f"  RUN  {leg_name} · buy_grace_days = {grace}")
    print(f"  signal = {os.path.basename(signal_path)}")
    print(f"  window = {start_d} → {end_d}")
    print("=" * 72)

    signal = load_frozen_signal(signal_path)
    strat = _make_strategy()
    strat["buy_grace_days"] = int(grace)

    t0 = time.time()
    res = simulator.run_simulation(
        engine=engine, cfg=cfg, pack=pack, signal=signal,
        vix_close_by_date=vix_close_map,
        vix_regime_by_date=vix_regime_map,
        initial_capital=100000.0,
        daily_buy_limit=1000.0,
        strategy_conf=strat,
        trigger_conf=trigger_conf,
        rebalance_mode="daily",
        commission_bps=10.0, slippage_bps=5.0,
        start_date=start_d, end_date=end_d,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vix_smooth_map,
    )
    elapsed = time.time() - t0

    m = res["metrics"]
    rb = m.get("regime_breakdown", {}) or {}
    print(f"  CAGR      = {m.get('CAGR', 0)*100:+.2f}%   "
          f"Sharpe = {m.get('Net_Sharpe', 0):.3f}   "
          f"MDD = {m.get('Max_Drawdown', 0)*100:.2f}%")
    print(f"  Calmar    = {m.get('Calmar_Ratio', 0):.3f}    "
          f"Comm%  = {m.get('Commission_Pct', 0):.2f}%   "
          f"Final$ = ${m.get('Final_Value', 0):,.0f}")
    print(f"  filtered  buys total = {int(m.get('buy_grace_filtered_total', 0)):,}    "
          f"days w/ filter = {int(m.get('buy_grace_rebal_days_with_filter', 0)):,}")
    print(f"  elapsed   = {elapsed:.1f}s")
    print(f"  [Regime] BULL Ann = {float(rb.get('BULL', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"SIDE Ann = {float(rb.get('SIDE', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"DEF Ann = {float(rb.get('DEF', {}).get('AnnRet', 0))*100:+.2f}%")

    return {
        "grace": int(grace),
        "leg_name": leg_name,
        "signal_path": signal_path,
        "window_start": start_d, "window_end": end_d,
        "elapsed_sec": round(elapsed, 1),
        "metrics": {
            "CAGR":             float(m.get("CAGR", 0.0)),
            "Max_Drawdown":     float(m.get("Max_Drawdown", 0.0)),
            "Net_Sharpe":       float(m.get("Net_Sharpe", 0.0)),
            "Calmar_Ratio":     float(m.get("Calmar_Ratio", 0.0)),
            "Total_Return":     float(m.get("Total_Return", 0.0)),
            "Final_Value":      float(m.get("Final_Value", 0.0)),
            "Years":            float(m.get("Years", 0.0)),
            "Trading_Days":     int(m.get("Trading_Days", 0)),
            "Rebalance_Days":   int(m.get("Rebalance_Days", 0)),
            "Total_Commission": float(m.get("Total_Commission", 0.0)),
            "Commission_Pct":   float(m.get("Commission_Pct", 0.0)),
            "Daily_Win_Rate":   float(m.get("Daily_Win_Rate", 0.0)),
            "Monthly_Win_Rate": float(m.get("Monthly_Win_Rate", 0.0)),
            "buy_grace_filtered_total":          int(m.get("buy_grace_filtered_total", 0)),
            "buy_grace_rebal_days_with_filter":  int(m.get("buy_grace_rebal_days_with_filter", 0)),
        },
        "regime_breakdown": {
            rg: {
                k: (int(v) if isinstance(v, (int, np.integer)) else float(v))
                for k, v in (rb.get(rg) or {}).items()
            } for rg in ("BULL", "SIDE", "DEF")
        },
    }


def _print_summary(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    base = rows[0]
    print()
    print("=" * 100)
    print("  SWEEP SUMMARY  (vs grace=0 baseline as Δ)")
    print("=" * 100)
    print(f"  {'grace':>5s} {'CAGR':>9s} {'Δ vs g0':>9s} "
          f"{'Sharpe':>7s} {'MDD':>7s} {'Calmar':>7s} "
          f"{'Comm%':>7s} {'Δ Comm':>7s} {'Final $':>14s} "
          f"{'filtered':>10s}")
    print("-" * 100)
    base_cagr = base["metrics"]["CAGR"]
    base_comm = base["metrics"]["Commission_Pct"]
    for r in rows:
        m = r["metrics"]
        d_cagr = (m["CAGR"] - base_cagr) * 100
        d_comm = m["Commission_Pct"] - base_comm
        print(
            f"  {r['grace']:>5d} "
            f"{m['CAGR']*100:>+8.2f}% "
            f"{d_cagr:>+8.2f}% "
            f"{m['Net_Sharpe']:>7.3f} "
            f"{m['Max_Drawdown']*100:>6.2f}% "
            f"{m['Calmar_Ratio']:>7.3f} "
            f"{m['Commission_Pct']:>6.2f}% "
            f"{d_comm:>+6.2f}% "
            f"${m['Final_Value']:>13,.0f} "
            f"{m['buy_grace_filtered_total']:>10,d}"
        )
    print("=" * 100)
    print()
    print("  Regime breakdown — AnnRet (% / Δ vs g0)")
    print("-" * 100)
    print(f"  {'grace':>5s}  {'BULL':>14s}  {'SIDE':>14s}  {'DEF':>14s}")
    base_b = base["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = base["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = base["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        print(
            f"  {r['grace']:>5d}  "
            f"{ab*100:>+7.2f}% / {(ab-base_b)*100:>+5.2f}  "
            f"{aS*100:>+7.2f}% / {(aS-base_s)*100:>+5.2f}  "
            f"{ad*100:>+7.2f}% / {(ad-base_d)*100:>+5.2f}"
        )
    print("=" * 100)


def _write_md(rows: List[Dict[str, Any]], leg_name: str,
              start_d: str, end_d: str, pack_name: str,
              md_path: str) -> None:
    L: List[str] = []
    L.append(f"# P3 — Buy-Grace Days Sweep · {leg_name}")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Signal**: {os.path.basename(rows[0]['signal_path'])}")
    L.append(f"**Window**: {start_d} → {end_d}")
    L.append(f"**Pack**: `{pack_name}`")
    L.append(f"**Protocol**: $100K · $1K/day · 10/5 bps · SIDE_DEF_p12 · daily rebal · variant=a (strict)")
    L.append("")

    L.append("## 1. Headline sweep")
    L.append("")
    L.append("| grace | CAGR | Δ CAGR | Sharpe | MDD | Calmar | Comm % | Δ Comm | Final $ | filtered buys |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    base = rows[0]
    base_cagr = base["metrics"]["CAGR"]
    base_comm = base["metrics"]["Commission_Pct"]
    for r in rows:
        m = r["metrics"]
        L.append(
            f"| **{r['grace']}** "
            f"| {m['CAGR']*100:+.2f}% "
            f"| {(m['CAGR']-base_cagr)*100:+.2f}pp "
            f"| {m['Net_Sharpe']:+.3f} "
            f"| {m['Max_Drawdown']*100:.2f}% "
            f"| {m['Calmar_Ratio']:.3f} "
            f"| {m['Commission_Pct']:.2f}% "
            f"| {m['Commission_Pct']-base_comm:+.2f}pp "
            f"| ${m['Final_Value']:,.0f} "
            f"| {m['buy_grace_filtered_total']:,} |"
        )
    L.append("")

    L.append("## 2. Regime breakdown — AnnRet")
    L.append("")
    L.append("| grace | BULL | Δ vs g0 | SIDE | Δ vs g0 | DEF | Δ vs g0 |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|")
    base_b = base["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = base["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = base["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        L.append(
            f"| **{r['grace']}** "
            f"| {ab*100:+.2f}% | {(ab-base_b)*100:+.2f}pp "
            f"| {aS*100:+.2f}% | {(aS-base_s)*100:+.2f}pp "
            f"| {ad*100:+.2f}% | {(ad-base_d)*100:+.2f}pp |"
        )
    L.append("")

    L.append("## 3. Diagnostics")
    L.append("")
    L.append("| grace | rebal days w/ filter | total filtered tickers | avg per filtered day |")
    L.append("|---:|---:|---:|---:|")
    for r in rows:
        m = r["metrics"]
        nd = max(int(m["buy_grace_rebal_days_with_filter"]), 1)
        avg = m["buy_grace_filtered_total"] / nd if m["buy_grace_filtered_total"] > 0 else 0
        L.append(
            f"| **{r['grace']}** "
            f"| {m['buy_grace_rebal_days_with_filter']:,} "
            f"| {m['buy_grace_filtered_total']:,} "
            f"| {avg:.1f} |"
        )
    L.append("")

    L.append("---")
    L.append("")
    L.append("**Reading guide**")
    L.append("")
    L.append("- **grace = 0** is byte-identical to legacy (verified by paired smoke test).")
    L.append("- A grace level is a Pareto-improvement if it shows: ΔCAGR ≥ −1pp **and** ΔComm < 0 **and** ΔMDD ≤ 0.")
    L.append("- BULL ΔAnnRet often degrades fastest with higher grace (slow entries miss momentum).")
    L.append("- SIDE ΔAnnRet often improves with grace (whipsaw filtering).")
    L.append("- DEF behaviour is noisy due to short DEF day-counts.")
    L.append("")
    L.append("**Next steps after sweep**")
    L.append("")
    L.append("- If a grace level Pareto-dominates: re-run `step_e_spy_benchmark.py` against per-N walk-forward to recompute G7 verdicts.")
    L.append("- If trade-off is borderline: explore variant **(b) soft** (M-of-N) or **(c) score-avg** to soften BULL lag while keeping noise filter.")
    L.append("- If higher grace strictly dominates: explore variant **(d) regime-conditional** (BULL: small N, SIDE: large N).")

    with open(md_path, "w") as f:
        f.write("\n".join(L))


def main() -> int:
    parser = argparse.ArgumentParser(description="P3 — buy_grace_days sweep")
    parser.add_argument("--signal", default=DEFAULT_SIGNAL,
                        help=f"Signal npz path (default: Baseline_V2)")
    parser.add_argument("--leg-name", default=DEFAULT_LEG_NAME,
                        help=f"Leg display name (default: {DEFAULT_LEG_NAME})")
    parser.add_argument("--grace", default=DEFAULT_GRACE,
                        help=f"Comma-separated grace values (default: {DEFAULT_GRACE})")
    parser.add_argument("--start", default="2012-01-01")
    parser.add_argument("--end", default=PACK_END_STR)
    args = parser.parse_args()

    grace_list = [int(x) for x in args.grace.split(",") if x.strip()]
    if not grace_list:
        print("[ERROR] empty --grace list")
        return 2

    print("=" * 72)
    print(f"  P3 — Buy-Grace Days Sweep")
    print("=" * 72)
    print(f"  signal   : {args.leg_name}  ({os.path.basename(args.signal)})")
    print(f"  window   : {args.start} → {args.end}")
    print(f"  grace    : {grace_list}")
    print(f"  variant  : (a) strict  — N consecutive top-N appearances")
    print("=" * 72)

    if not os.path.exists(args.signal):
        print(f"[ERROR] missing signal: {args.signal}")
        return 2

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]

    pack_path, pack_start, pack_end = _pick_walk_forward_pack(save_dir)
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")
    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] loaded {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    print("[VIX ] building regime timeseries…")
    vix_c, vix_r, vix_s = _load_vix(cfg, pack_start, pack_end)
    print(f"[VIX ] {len(vix_c)} dates")
    trigger_conf = conf.get("triggers", {})

    rows: List[Dict[str, Any]] = []
    t_total = time.time()
    for g in grace_list:
        rec = _run_one(
            g, args.signal, args.leg_name, cfg, pack,
            vix_c, vix_r, vix_s, trigger_conf, args.start, args.end,
        )
        rows.append(rec)
    total_elapsed = time.time() - t_total

    _print_summary(rows)
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path   = os.path.join(DOCS_DIR, f"p3_buy_grace_sweep_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"p3_buy_grace_sweep_{stamp}.json")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_basename": os.path.basename(pack_path),
        "pack_start": pack_start, "pack_end": pack_end,
        "window_start": args.start, "window_end": args.end,
        "leg_name": args.leg_name,
        "signal_path": args.signal,
        "variant": "a_strict",
        "grace_list": grace_list,
        "total_elapsed_sec": round(total_elapsed, 1),
        "rows": rows,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    _write_md(rows, args.leg_name, args.start, args.end,
              os.path.basename(pack_path), md_path)
    print(f"[saved] {md_path}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
