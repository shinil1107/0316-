"""Step A — Baseline Benchmark Freeze.

Evaluates 3 signal/strategy combinations on the fixed OOS window
(2024-06-01 → end of pack) and records portfolio-level realized metrics.

Arms
----
1. V1_BATCH11                       : anchor V1 signal + legacy exits
2. V2_ENS_L3_v1 (legacy)            : current V2 signal + legacy exits
3. V2_ENS_L3_v1 + SIDE_DEF_p12       : current live setup (v2.1 baseline)

Output
------
- Console comparison table
- JSON metrics dump at docs/baseline_benchmark_metrics.json
  (consumed by baseline_benchmark.md)

This script is the single source of truth for the "baseline" values that
Phase 5 retrains must beat. Once run, its output should NOT be re-computed
without explicit version-bump in docs/baseline_benchmark.md.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PHASE3_DIR not in sys.path:
    sys.path.insert(0, PHASE3_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3 import simulator  # noqa: E402


# ── OOS window (fixed) ────────────────────────────────────────────────
OOS_START = "2024-06-01"
# OOS_END uses pack's end.

# ── Signal paths ──────────────────────────────────────────────────────
V1_PATH = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P2_BATCH11_20260406_043415.npz"
V2_PATH = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"

# ── Output ────────────────────────────────────────────────────────────
DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
METRICS_JSON = os.path.join(DOCS_DIR, "baseline_benchmark_metrics.json")


# =====================================================================
# Strategy configurations
# =====================================================================
LEGACY_STRATEGY = {
    "rebalance_mode": "daily",
    "rebalance_gap_threshold": 0.02,
    "buy_allocation_mode": "gap_proportional",
    "enable_trim": True,
    "trim_threshold": 0.03,
    "sell_grace_days": 60,
    "min_buy_shares": 1,
    "enable_stop_loss": True,
    "stop_loss_pct": -15.0,
    "buy_limit_mode": "adaptive",
    "adaptive_deploy_rate": 0.10,
    "adaptive_min_limit": 500.0,
    "target_invest_pct": 0.97,
    "regime_overrides": {
        "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                 "target_invest_pct": 0.98},
        "SIDE": {"sell_grace_days": 120, "adaptive_deploy_rate": 0.10,
                 "enable_stop_loss": False},
        "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
    },
    # No "exit_triggers" key → build_triggers uses legacy stop_loss + sell_grace fallback.
}

SIDE_DEF_P12_TRIGGERS = [
    {"type": "stop_loss", "regimes": ["BULL"], "params": {"threshold_pct": -15.0}},
    {"type": "sell_grace", "regimes": ["BULL"], "params": {"days": 60}},
    {"type": "sell_grace", "regimes": ["SIDE"], "params": {"days": 120}},
    {"type": "sell_grace", "regimes": ["DEF"], "params": {"days": 60}},
    {"type": "profit_target", "regimes": ["SIDE", "DEF"],
     "params": {
         "target_pct": 30.0, "action": "TRIM", "partial_pct": 0.12,
         "score_gate_enabled": False,
         "extension_enabled": True, "extension_window": 20,
         "extension_threshold": 0.20, "min_days_held": 10,
     }},
]


def make_strategy(use_side_def_p12: bool) -> dict:
    s = copy.deepcopy(LEGACY_STRATEGY)
    if use_side_def_p12:
        s["exit_triggers"] = copy.deepcopy(SIDE_DEF_P12_TRIGGERS)
    return s


# =====================================================================
# Shared setup: pack + VIX regime
# =====================================================================
def _build_cfg_and_pack(conf: dict, pack_start: str, pack_end: str):
    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except Exception:
                pass
    cfg.start_panel_date = datetime.strptime(pack_start, "%Y-%m-%d")
    cfg.end_date = datetime.strptime(pack_end, "%Y-%m-%d")
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    # Ensure precompute path finds cached pack
    if "save_dir" not in cfg.__dict__ or not cfg.save_dir:
        cfg.save_dir = conf["paths"]["output_dir"]
    else:
        cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


def _load_vix(cfg, start: str, end: str):
    vix_df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=90),
        datetime.strptime(end, "%Y-%m-%d"),
    )
    vix_close_map, vix_regime_map, vix_smooth_map = {}, {}, {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
            vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
            if "vix_smooth" in row.index:
                vix_smooth_map[d_str] = float(row["vix_smooth"])
    return vix_close_map, vix_regime_map, vix_smooth_map


# =====================================================================
# Per-arm runner
# =====================================================================
def _run_arm(
    arm_name: str,
    signal_path: str,
    use_side_def_p12: bool,
    engine_mod,
    cfg,
    pack,
    vix_close_map,
    vix_regime_map,
    vix_smooth_map,
    trigger_conf,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    print(f"\n{'='*72}")
    print(f"  ARM: {arm_name}")
    print(f"  signal = {os.path.basename(signal_path)}")
    print(f"  triggers = {'SIDE_DEF_p12' if use_side_def_p12 else 'LEGACY'}")
    print(f"  window = {start_date} → {end_date}")
    print(f"{'='*72}")

    signal = load_frozen_signal(signal_path)
    strat = make_strategy(use_side_def_p12)

    t0 = time.time()
    res = simulator.run_simulation(
        engine=engine_mod,
        cfg=cfg,
        pack=pack,
        signal=signal,
        vix_close_by_date=vix_close_map,
        vix_regime_by_date=vix_regime_map,
        initial_capital=100000.0,
        daily_buy_limit=1000.0,
        strategy_conf=strat,
        trigger_conf=trigger_conf,
        rebalance_mode="daily",
        commission_bps=10.0,
        slippage_bps=5.0,
        start_date=start_date,
        end_date=end_date,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vix_smooth_map,
    )
    elapsed = time.time() - t0

    m = res["metrics"]
    print(f"\n  CAGR      = {m.get('CAGR', 0) * 100:+.2f}%")
    print(f"  Sharpe    = {m.get('Net_Sharpe', 0):.3f}")
    print(f"  MDD       = {m.get('Max_Drawdown', 0) * 100:.2f}%")
    print(f"  Calmar    = {m.get('Calmar_Ratio', 0):.3f}")
    print(f"  Commission = ${m.get('Total_Commission', 0):,.2f} ({m.get('Commission_Pct', 0):.2f}%)")
    print(f"  Rebalance  = {m.get('Rebalance_Days', 0)}")
    print(f"  Final      = ${m.get('Final_Value', 0):,.2f}")
    print(f"  elapsed    = {elapsed:.1f}s")

    # Compute realized turnover across the window for this arm.
    # Turnover = avg daily fraction of portfolio value traded.
    trades = res.get("trades", pd.DataFrame())
    traded_value = 0.0
    if not trades.empty and "Value" in trades.columns:
        traded_value = float(trades["Value"].abs().sum())
    # As an annualized turnover proxy: total traded value / (avg portfolio value × years).
    years = m.get("Years", 1.0) or 1.0
    avg_pv = (m.get("Initial_Capital", 100000.0) + m.get("Final_Value", 100000.0)) / 2.0
    turnover_proxy = traded_value / (avg_pv * years) if avg_pv > 0 else 0.0

    out = {
        "arm": arm_name,
        "signal_path": signal_path,
        "triggers": "SIDE_DEF_p12" if use_side_def_p12 else "LEGACY",
        "window_start": start_date,
        "window_end": end_date,
        "metrics": {
            "CAGR": m.get("CAGR", 0.0),
            "Max_Drawdown": m.get("Max_Drawdown", 0.0),
            "Net_Sharpe": m.get("Net_Sharpe", 0.0),
            "Calmar_Ratio": m.get("Calmar_Ratio", 0.0),
            "Total_Return": m.get("Total_Return", 0.0),
            "Daily_Win_Rate": m.get("Daily_Win_Rate", 0.0),
            "Monthly_Win_Rate": m.get("Monthly_Win_Rate", 0.0),
            "Years": m.get("Years", 0.0),
            "Trading_Days": m.get("Trading_Days", 0),
            "Rebalance_Days": m.get("Rebalance_Days", 0),
            "Total_Commission": m.get("Total_Commission", 0.0),
            "Commission_Pct_of_Capital": m.get("Commission_Pct", 0.0),
            "Final_Value": m.get("Final_Value", 0.0),
            "turnover_annualized_proxy": round(turnover_proxy, 4),
        },
        "regime_breakdown": {
            rg: {
                "Days": m.get(f"{rg}_Days", 0),
                "MaxStreak": m.get(f"{rg}_MaxStreak", 0),
                "AnnRet": m.get(f"{rg}_AnnRet", 0.0),
                "Sharpe": m.get(f"{rg}_Sharpe", 0.0),
                "MDD": m.get(f"{rg}_MDD", 0.0),
                "Calmar": m.get(f"{rg}_Calmar", 0.0),
                "WinRate": m.get(f"{rg}_WinRate", 0.0),
            }
            for rg in ("BULL", "SIDE", "DEF")
        },
        "elapsed_sec": round(elapsed, 1),
    }
    return out


# =====================================================================
# Main
# =====================================================================
def main():
    print("=" * 72)
    print("Step A — Baseline Benchmark Freeze")
    print("=" * 72)
    print(f"OOS window: {OOS_START} → pack end\n")

    # Load live config (for paths + regime params).
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)

    # Decide pack span. We need a pack whose start ≤ OOS_START and end is maximal.
    # Prefer packs that fully cover our OOS window; among those pick the one with
    # the most recent end, then smallest (fastest-load) span.
    import glob
    save_dir = conf["paths"]["output_dir"]
    pack_pattern = os.path.join(save_dir, "precompute_qresearch_v4_12_*.npz")
    all_packs = glob.glob(pack_pattern)
    if not all_packs:
        raise RuntimeError(f"No precomputed pack found under {pack_pattern}")

    def _parse_span(path: str):
        stem = os.path.splitext(os.path.basename(path))[0]
        parts = stem.split("_")
        return parts[-2], parts[-1]  # (start_str, end_str)

    qualified = []  # packs that cover OOS_START
    for p in all_packs:
        s, e = _parse_span(p)
        if s <= OOS_START and e >= OOS_START:
            qualified.append((p, s, e))
    if not qualified:
        raise RuntimeError(
            f"No pack covers OOS_START={OOS_START}. Candidates: "
            + ", ".join(os.path.basename(p) for p in all_packs)
        )
    # Sort: most recent end first, then latest start (= narrowest span, fastest load).
    qualified.sort(key=lambda t: (t[2], t[1]), reverse=True)
    latest, pack_start, pack_end = qualified[0]
    print(f"[pack] using {os.path.basename(latest)}")
    print(f"       {pack_start} → {pack_end}")
    print(f"       ({len(qualified)} packs cover OOS_START={OOS_START})\n")

    cfg = _build_cfg_and_pack(conf, pack_start, pack_end)
    # Prefer direct cached-npz load (fast path). If missing, fall back to prepare_inputs.
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        print("[pack] cached npz not found for this span; calling prepare_inputs (slow).")
        result = engine.prepare_inputs(cfg)
        pack = result["pack"] if isinstance(result, dict) and "pack" in result else result
    print(f"[pack] loaded: {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    # Set end_date to pack's end.
    oos_end = pack_end

    # Restrict OOS sim within pack range.
    if pack_start > OOS_START:
        print(f"[warn] pack_start({pack_start}) > OOS_START({OOS_START}); adjusting.")
        oos_start = pack_start
    else:
        oos_start = OOS_START
    print(f"[sim ] OOS window = {oos_start} → {oos_end}")

    print(f"\n[VIX ] building regime timeseries…")
    vix_close_map, vix_regime_map, vix_smooth_map = _load_vix(cfg, oos_start, oos_end)
    print(f"[VIX ] {len(vix_close_map)} dates")

    trigger_conf = conf.get("triggers", {})

    # Define arms
    arms = [
        ("V1_BATCH11_legacy",       V1_PATH, False),
        ("V2_ENS_L3_v1_legacy",     V2_PATH, False),
        ("V2_ENS_L3_v1_SIDE_DEF_p12", V2_PATH, True),
    ]

    results: List[Dict[str, Any]] = []
    for (name, path, use_p12) in arms:
        try:
            r = _run_arm(
                arm_name=name, signal_path=path, use_side_def_p12=use_p12,
                engine_mod=engine, cfg=cfg, pack=pack,
                vix_close_map=vix_close_map,
                vix_regime_map=vix_regime_map,
                vix_smooth_map=vix_smooth_map,
                trigger_conf=trigger_conf,
                start_date=oos_start, end_date=oos_end,
            )
            results.append(r)
        except Exception as e:
            import traceback
            print(f"\n[ERROR] arm {name} failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append({
                "arm": name, "signal_path": path, "error": f"{type(e).__name__}: {e}",
            })

    # Comparison table
    print("\n" + "=" * 72)
    print("Comparison (OOS realized metrics)")
    print("=" * 72)
    rows = []
    for r in results:
        if "error" in r:
            continue
        m = r["metrics"]
        rows.append({
            "Arm": r["arm"],
            "CAGR%": f"{m['CAGR']*100:+.2f}",
            "Sharpe": f"{m['Net_Sharpe']:.3f}",
            "MDD%": f"{m['Max_Drawdown']*100:.2f}",
            "Calmar": f"{m['Calmar_Ratio']:.3f}",
            "DailyWin%": f"{m['Daily_Win_Rate']*100:.1f}",
            "Turnover(yr)": f"{m['turnover_annualized_proxy']:.2f}",
            "Comm%": f"{m['Commission_Pct_of_Capital']:.2f}",
            "Rebal": m['Rebalance_Days'],
        })
    cmp_df = pd.DataFrame(rows)
    print(cmp_df.to_string(index=False))

    # Regime breakdown table
    print("\nRegime breakdown")
    rows2 = []
    for r in results:
        if "error" in r:
            continue
        for rg in ("BULL", "SIDE", "DEF"):
            rb = r["regime_breakdown"].get(rg, {})
            if rb.get("Days", 0) == 0:
                continue
            rows2.append({
                "Arm": r["arm"], "Regime": rg,
                "Days": rb["Days"],
                "AnnRet%": f"{rb.get('AnnRet', 0)*100:+.2f}",
                "Sharpe": f"{rb.get('Sharpe', 0):.3f}",
                "MDD%": f"{rb.get('MDD', 0)*100:.2f}",
                "Calmar": f"{rb.get('Calmar', 0):.3f}",
                "WinRate%": f"{rb.get('WinRate', 0)*100:.1f}",
            })
    print(pd.DataFrame(rows2).to_string(index=False))

    # Write JSON
    os.makedirs(DOCS_DIR, exist_ok=True)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "oos_window": {"start": oos_start, "end": oos_end},
            "pack": os.path.basename(latest),
            "initial_capital": 100000.0,
            "commission_bps": 10.0,
            "slippage_bps": 5.0,
            "daily_buy_limit": 1000.0,
            "note": "FROZEN baseline. Do not regenerate without version-bump.",
        },
        "arms": results,
    }
    with open(METRICS_JSON, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n[saved] {METRICS_JSON}")

    # Quick conclusion
    print("\n" + "=" * 72)
    print("Step A complete. Inspect docs/baseline_benchmark_metrics.json.")
    print("Next: run write_baseline_doc.py to materialize docs/baseline_benchmark.md.")
    print("=" * 72)


if __name__ == "__main__":
    main()
