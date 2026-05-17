"""P2 — ENSEMBLE_A vs Baseline_V2 (regime-breakdown A/B).

Full-period (2012-01-01 → pack end) side-by-side backtest comparing::

    LEG-A : P6_ENSEMBLE_A    (wb=T1b_INJ / ws=BULL_FREE / wd=DEF_HEAVY)
    LEG-B : Baseline_V2      (current production frozen signal)

Why this script exists
----------------------
``step_d_walk_forward.py`` proved that P6_ENSEMBLE_A is Pareto-optimal across
the 6-fold split (CAGR +28.52 % / CV 0.407 / worst +11.04 %), but the 6-fold
pack contains **zero DEFENSIVE trading days**, so the ENSEMBLE's ``wd`` slot
is never exercised. A long-horizon single-period backtest (2012 → 2026) is
needed to:

1. Verify ENSEMBLE_A behaves sanely through real DEF regimes
   (VIX-based DEF classifier fires for 2015 oil-crash, 2016 Brexit,
    2018 vol-event, 2020 COVID, 2022 inflation, 2024 yen-carry).
2. Provide per-regime (BULL / SIDE / DEF) CAGR, Sharpe, MDD, Calmar,
   WinRate breakdown for both legs.
3. Establish the deployment-level decision data point.

Reuses the exact same protocol as ``step_c_gate_evaluation.py`` and
``step_d_walk_forward.py``: $100K initial, $1K daily buy limit, 10/5 bps
cost, SIDE_DEF_p12 trigger stack, regime-blend OFF, daily rebalance.

Usage
-----
    python3 -u phase3/tests/p2_ensemble_vs_baseline.py
    python3 -u phase3/tests/p2_ensemble_vs_baseline.py --start 2017-01-03
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
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
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

LEG_A_PATH = os.path.join(OUTPUT_SIG_DIR, "frozen_signal_P6_ENSEMBLE_A_20260425_011612.npz")
LEG_A_NAME = "P6_ENSEMBLE_A"

LEG_B_PATH = os.path.join(OUTPUT_SIG_DIR, "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz")
LEG_B_NAME = "Baseline_V2"


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
    name: str, signal_path: str,
    cfg, pack,
    vix_close_map: Dict[str, float],
    vix_regime_map: Dict[str, str],
    vix_smooth_map: Dict[str, float],
    trigger_conf,
    start_d: str, end_d: str,
) -> Dict[str, Any]:
    print()
    print("=" * 72)
    print(f"  LEG  {name}")
    print(f"  signal = {os.path.basename(signal_path)}")
    print(f"  window = {start_d} → {end_d}")
    print("=" * 72)

    signal = load_frozen_signal(signal_path)
    strat = _make_strategy()

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
    print(f"  CAGR       = {m.get('CAGR', 0)*100:+.2f}%")
    print(f"  Sharpe     = {m.get('Net_Sharpe', 0):.3f}")
    print(f"  MDD        = {m.get('Max_Drawdown', 0)*100:.2f}%")
    print(f"  Calmar     = {m.get('Calmar_Ratio', 0):.3f}")
    print(f"  Total Ret  = {m.get('Total_Return', 0)*100:+.2f}%")
    print(f"  Final $    = ${m.get('Final_Value', 0):,.0f}")
    print(f"  Years      = {m.get('Years', 0):.2f}")
    print(f"  Comm %     = {m.get('Commission_Pct', 0):.2f}%")
    print(f"  elapsed    = {elapsed:.1f}s")

    rb = m.get("regime_breakdown", {}) or {}
    print()
    print(f"  [Regime Breakdown]")
    print(f"    {'Regime':<6s} {'Days':>6s} {'Streak':>7s} {'Ann %':>8s} {'Sharpe':>8s} {'MDD %':>7s} {'Calmar':>8s} {'Win %':>7s}")
    print(f"    {'-'*6} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*7}")
    for rg in ("BULL", "SIDE", "DEF"):
        r = rb.get(rg, {})
        print(f"    {rg:<6s} "
              f"{int(r.get('Days', 0)):>6d} "
              f"{int(r.get('MaxStreak', 0)):>7d} "
              f"{float(r.get('AnnRet', 0))*100:>+7.2f}% "
              f"{float(r.get('Sharpe', 0)):>8.3f} "
              f"{float(r.get('MDD', 0))*100:>6.2f}% "
              f"{float(r.get('Calmar', 0)):>8.3f} "
              f"{float(r.get('WinRate', 0))*100:>6.2f}%")

    return {
        "name": name,
        "signal_path": signal_path,
        "window_start": start_d, "window_end": end_d,
        "elapsed_sec": round(elapsed, 1),
        "metrics": {
            "CAGR":            float(m.get("CAGR", 0.0)),
            "Max_Drawdown":    float(m.get("Max_Drawdown", 0.0)),
            "Net_Sharpe":      float(m.get("Net_Sharpe", 0.0)),
            "Calmar_Ratio":    float(m.get("Calmar_Ratio", 0.0)),
            "Total_Return":    float(m.get("Total_Return", 0.0)),
            "Final_Value":     float(m.get("Final_Value", 0.0)),
            "Years":           float(m.get("Years", 0.0)),
            "Trading_Days":    int(m.get("Trading_Days", 0)),
            "Rebalance_Days":  int(m.get("Rebalance_Days", 0)),
            "Total_Commission": float(m.get("Total_Commission", 0.0)),
            "Commission_Pct":  float(m.get("Commission_Pct", 0.0)),
            "Daily_Win_Rate":  float(m.get("Daily_Win_Rate", 0.0)),
            "Monthly_Win_Rate": float(m.get("Monthly_Win_Rate", 0.0)),
        },
        "regime_breakdown": {
            rg: {
                k: (int(v) if isinstance(v, (int, np.integer)) else float(v))
                for k, v in (rb.get(rg) or {}).items()
            } for rg in ("BULL", "SIDE", "DEF")
        },
    }


def _delta_row(label: str, a: float, b: float, unit: str = "%", fmt: str = "+.2f",
               better: str = "higher") -> str:
    da = a - b
    sign = "+" if da > 0 else ""
    arrow = "↑" if (better == "higher" and da > 0) or (better == "lower" and da < 0) else (
        "↓" if da != 0 else "="
    )
    return f"| {label:<22s} | {a:{fmt}}{unit} | {b:{fmt}}{unit} | {sign}{da:{fmt}}{unit} {arrow} |"


def _write_markdown(a: Dict[str, Any], b: Dict[str, Any],
                    start_d: str, end_d: str, pack_name: str,
                    md_path: str) -> None:
    lines: List[str] = []
    lines.append("# P6_ENSEMBLE_A vs Baseline_V2 — Regime Breakdown A/B")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"**Window**: {start_d} → {end_d}")
    lines.append(f"**Pack**: `{pack_name}`")
    lines.append(f"**Protocol**: $100K init · $1K/day buy limit · 10/5 bps cost · SIDE_DEF_p12 · blend OFF · daily rebal")
    lines.append("")

    lines.append("## 1. Headline metrics (A = ENSEMBLE, B = Baseline)")
    lines.append("")
    lines.append("| Metric | ENSEMBLE_A | Baseline_V2 | Δ (A − B) |")
    lines.append("|---|---:|---:|---:|")
    am = a["metrics"]; bm = b["metrics"]
    lines.append(_delta_row("CAGR",               am["CAGR"]*100,              bm["CAGR"]*100,              "%", "+.2f", "higher"))
    lines.append(_delta_row("Total Return",       am["Total_Return"]*100,      bm["Total_Return"]*100,      "%", "+.2f", "higher"))
    lines.append(_delta_row("Net Sharpe",         am["Net_Sharpe"],            bm["Net_Sharpe"],            "",  "+.3f", "higher"))
    lines.append(_delta_row("Max Drawdown",       am["Max_Drawdown"]*100,      bm["Max_Drawdown"]*100,      "%", ".2f",  "lower"))
    lines.append(_delta_row("Calmar",             am["Calmar_Ratio"],          bm["Calmar_Ratio"],          "",  "+.3f", "higher"))
    lines.append(_delta_row("Daily Win-rate",     am["Daily_Win_Rate"]*100,    bm["Daily_Win_Rate"]*100,    "%", ".2f",  "higher"))
    lines.append(_delta_row("Monthly Win-rate",   am["Monthly_Win_Rate"]*100,  bm["Monthly_Win_Rate"]*100,  "%", ".2f",  "higher"))
    lines.append(_delta_row("Commission %",       am["Commission_Pct"],        bm["Commission_Pct"],        "%", ".2f",  "lower"))
    lines.append(_delta_row("Final $",            am["Final_Value"],           bm["Final_Value"],           "",  ",.0f", "higher"))
    lines.append("")

    lines.append("## 2. Regime breakdown (ENSEMBLE_A)")
    lines.append("")
    lines.append("| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for rg in ("BULL", "SIDE", "DEF"):
        r = a["regime_breakdown"][rg]
        lines.append(f"| {rg} | {int(r.get('Days',0))} | {int(r.get('MaxStreak',0))} "
                     f"| {float(r.get('AnnRet',0))*100:+.2f}% "
                     f"| {float(r.get('Sharpe',0)):+.3f} "
                     f"| {float(r.get('MDD',0))*100:.2f}% "
                     f"| {float(r.get('Calmar',0)):+.3f} "
                     f"| {float(r.get('WinRate',0))*100:.2f}% |")
    lines.append("")

    lines.append("## 3. Regime breakdown (Baseline_V2)")
    lines.append("")
    lines.append("| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for rg in ("BULL", "SIDE", "DEF"):
        r = b["regime_breakdown"][rg]
        lines.append(f"| {rg} | {int(r.get('Days',0))} | {int(r.get('MaxStreak',0))} "
                     f"| {float(r.get('AnnRet',0))*100:+.2f}% "
                     f"| {float(r.get('Sharpe',0)):+.3f} "
                     f"| {float(r.get('MDD',0))*100:.2f}% "
                     f"| {float(r.get('Calmar',0)):+.3f} "
                     f"| {float(r.get('WinRate',0))*100:.2f}% |")
    lines.append("")

    lines.append("## 4. Regime delta (ENSEMBLE_A − Baseline_V2)")
    lines.append("")
    lines.append("| Regime | ΔAnnRet | ΔSharpe | ΔMDD | ΔCalmar | ΔWinRate |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for rg in ("BULL", "SIDE", "DEF"):
        ra = a["regime_breakdown"][rg]; rb = b["regime_breakdown"][rg]
        d_ann   = float(ra.get("AnnRet", 0)) - float(rb.get("AnnRet", 0))
        d_sharpe = float(ra.get("Sharpe", 0)) - float(rb.get("Sharpe", 0))
        d_mdd   = float(ra.get("MDD", 0)) - float(rb.get("MDD", 0))
        d_calmar = float(ra.get("Calmar", 0)) - float(rb.get("Calmar", 0))
        d_win   = float(ra.get("WinRate", 0)) - float(rb.get("WinRate", 0))
        lines.append(f"| {rg} "
                     f"| {d_ann*100:+.2f}% "
                     f"| {d_sharpe:+.3f} "
                     f"| {d_mdd*100:+.2f}% "
                     f"| {d_calmar:+.3f} "
                     f"| {d_win*100:+.2f}% |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**Reading guide**")
    lines.append("")
    lines.append("- **§2 / §3 DEF row** answers the key question: does ENSEMBLE_A's DEF_HEAVY wd")
    lines.append("  actually deliver better tail-defense than Baseline_V2's DEF slot?")
    lines.append("- **§2 SIDE row** validates the F2 walk-forward finding at full-period scale.")
    lines.append("- **§4 deltas**: positive numbers = ENSEMBLE_A is better (for AnnRet/Sharpe/Calmar/WinRate)")
    lines.append("  and negative numbers = ENSEMBLE_A is better (for MDD).")
    lines.append("- Headline CAGR gap: expect ~−2 pp vs Baseline (priced-in from 6-fold).")
    lines.append("  Net Sharpe / Calmar / MDD should favour ENSEMBLE_A → that is the trade we want.")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 — ENSEMBLE vs Baseline A/B")
    parser.add_argument("--start", default="2012-01-01",
                        help="Backtest start date (default: 2012-01-01).")
    parser.add_argument("--end", default=PACK_END_STR,
                        help=f"Backtest end date (default: {PACK_END_STR}).")
    parser.add_argument("--leg-a", default=LEG_A_PATH,
                        help="Path to LEG-A signal npz (default: ENSEMBLE_A).")
    parser.add_argument("--leg-a-name", default=LEG_A_NAME,
                        help="Display name for LEG-A (default: P6_ENSEMBLE_A).")
    parser.add_argument("--leg-b", default=LEG_B_PATH,
                        help="Path to LEG-B signal npz (default: Baseline_V2).")
    parser.add_argument("--leg-b-name", default=LEG_B_NAME,
                        help="Display name for LEG-B (default: Baseline_V2).")
    args = parser.parse_args()

    start_d = args.start
    end_d = args.end
    leg_a_path = args.leg_a
    leg_a_name = args.leg_a_name
    leg_b_path = args.leg_b
    leg_b_name = args.leg_b_name

    print("=" * 72)
    print(f"  P2 — ENSEMBLE_A vs Baseline_V2 (Regime Breakdown A/B)")
    print("=" * 72)
    print(f"  window : {start_d} → {end_d}")
    print(f"  LEG-A  : {leg_a_name}  ({os.path.basename(leg_a_path)})")
    print(f"  LEG-B  : {leg_b_name}  ({os.path.basename(leg_b_path)})")
    print("=" * 72)

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

    for leg_path in (leg_a_path, leg_b_path):
        if not os.path.exists(leg_path):
            print(f"[ERROR] missing signal: {leg_path}")
            return 2

    print("[VIX ] building regime timeseries…")
    vix_c, vix_r, vix_s = _load_vix(cfg, pack_start, pack_end)
    print(f"[VIX ] {len(vix_c)} dates")
    trigger_conf = conf.get("triggers", {})

    t0 = time.time()
    leg_a = _run_one(leg_a_name, leg_a_path, cfg, pack, vix_c, vix_r, vix_s,
                     trigger_conf, start_d, end_d)
    leg_b = _run_one(leg_b_name, leg_b_path, cfg, pack, vix_c, vix_r, vix_s,
                     trigger_conf, start_d, end_d)
    total_elapsed = time.time() - t0

    print()
    print("=" * 72)
    print("  SIDE-BY-SIDE SUMMARY")
    print("=" * 72)
    print(f"  {'Metric':<20s} {leg_a_name:>14s} {leg_b_name:>14s} {'Δ (A−B)':>14s}")
    print(f"  {'-'*20} {'-'*14} {'-'*14} {'-'*14}")
    def _r(label, a, b, unit="%", fmt="+.2f"):
        da = a - b
        a_str  = format(a,  fmt)
        b_str  = format(b,  fmt)
        da_str = format(da, fmt)
        print(f"  {label:<20s} {a_str:>13s}{unit} {b_str:>13s}{unit} {da_str:>13s}{unit}")
    am = leg_a["metrics"]; bm = leg_b["metrics"]
    _r("CAGR",          am["CAGR"]*100,          bm["CAGR"]*100)
    _r("Total Return",  am["Total_Return"]*100,  bm["Total_Return"]*100)
    _r("Net Sharpe",    am["Net_Sharpe"],        bm["Net_Sharpe"],     "",  "+.3f")
    _r("MDD",           am["Max_Drawdown"]*100,  bm["Max_Drawdown"]*100)
    _r("Calmar",        am["Calmar_Ratio"],      bm["Calmar_Ratio"],   "",  "+.3f")
    _r("Daily Win",     am["Daily_Win_Rate"]*100,bm["Daily_Win_Rate"]*100)
    _r("Commission",    am["Commission_Pct"],    bm["Commission_Pct"])
    print(f"  {'Final $':<20s} ${am['Final_Value']:>12,.0f}  ${bm['Final_Value']:>12,.0f}  ${am['Final_Value']-bm['Final_Value']:>+12,.0f}")

    print()
    print("=" * 72)
    print(f"  REGIME-DELTA ({leg_a_name} − {leg_b_name})")
    print("=" * 72)
    print(f"  {'Regime':<6s} {'ΔAnnRet':>10s} {'ΔSharpe':>10s} {'ΔMDD':>10s} {'ΔCalmar':>10s} {'ΔWinRate':>10s}")
    for rg in ("BULL", "SIDE", "DEF"):
        ra = leg_a["regime_breakdown"][rg]; rb2 = leg_b["regime_breakdown"][rg]
        d_ann = (ra.get("AnnRet", 0) - rb2.get("AnnRet", 0)) * 100
        d_sharpe = ra.get("Sharpe", 0) - rb2.get("Sharpe", 0)
        d_mdd = (ra.get("MDD", 0) - rb2.get("MDD", 0)) * 100
        d_calmar = ra.get("Calmar", 0) - rb2.get("Calmar", 0)
        d_win = (ra.get("WinRate", 0) - rb2.get("WinRate", 0)) * 100
        print(f"  {rg:<6s} {d_ann:>+9.2f}% {d_sharpe:>+10.3f} {d_mdd:>+9.2f}% {d_calmar:>+10.3f} {d_win:>+9.2f}%")
    print("=" * 72)
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print("=" * 72)

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = f"{leg_a_name.lower()}_vs_{leg_b_name.lower()}"
    md_path = os.path.join(DOCS_DIR, f"p2_{slug}_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"p2_{slug}_{stamp}.json")

    _write_markdown(leg_a, leg_b, start_d, end_d,
                    os.path.basename(pack_path), md_path)
    print(f"[saved] {md_path}")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_basename": os.path.basename(pack_path),
        "pack_start": pack_start, "pack_end": pack_end,
        "window_start": start_d, "window_end": end_d,
        "total_elapsed_sec": round(total_elapsed, 1),
        "leg_a": leg_a,
        "leg_b": leg_b,
        "protocol": {
            "initial_capital": 100000.0,
            "daily_buy_limit": 1000.0,
            "commission_bps": 10.0, "slippage_bps": 5.0,
            "rebalance_mode": "daily",
            "strategy_stack": "SIDE_DEF_p12",
            "regime_blend": False,
        },
    }
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=float)
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
