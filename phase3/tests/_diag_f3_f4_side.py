"""Diagnostic: decompose F3/F4 returns by regime for Baseline vs ENS_C vs ENS_D.

Runs 6 sims (3 signals × 2 folds) and decomposes daily returns by regime,
showing cumulative return and annualized return for BULL-only vs SIDE-only
sub-periods within each fold.
"""
import os, sys, time, glob, copy, yaml
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3 = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3)
for p in (ROOT, PHASE3):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import pandas as pd
from engine_loader import engine
import simulator
from phase3.daily_runner import load_frozen_signal
from step_c_gate_evaluation import (
    _make_strategy, _build_cfg, _load_vix, _pick_oos_pack,
)

OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"

SIGNALS = {
    "Baseline_V2": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
    "ENSEMBLE_C":  f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_C_20260425_014939.npz",
    "ENSEMBLE_D":  f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_D_20260426_084731.npz",
}

FOLDS = {
    "F3": ("2023-01-01", "2024-05-31"),
    "F4": ("2024-06-01", "2026-02-27"),
}


def _run_and_decompose(name, sig_path, cfg, pack, vix_close, vix_regime, vix_smooth,
                       trigger_conf, start, end):
    signal = load_frozen_signal(sig_path)
    strat = _make_strategy()
    res = simulator.run_simulation(
        engine=engine, cfg=cfg, pack=pack, signal=signal,
        vix_close_by_date=vix_close, vix_regime_by_date=vix_regime,
        initial_capital=100000.0, daily_buy_limit=1000.0,
        strategy_conf=strat, trigger_conf=trigger_conf,
        rebalance_mode="daily", commission_bps=10.0, slippage_bps=5.0,
        start_date=start, end_date=end,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vix_smooth,
    )
    ts = res["daily_ts"]
    trades = res.get("trades", pd.DataFrame())

    if isinstance(ts, list):
        ts = pd.DataFrame(ts)

    ts["date_str"] = ts["Date"].astype(str).str[:10]
    ts["regime"] = ts["Regime"] if "Regime" in ts.columns else ts["date_str"].map(vix_regime)
    ts["daily_ret"] = ts["DailyReturn"].fillna(0)

    cagr_all = float(res["metrics"].get("CAGR", 0))
    mdd_all = float(res["metrics"].get("Max_Drawdown", 0))

    regime_stats = {}
    for rg in ["BULL", "SIDE", "DEFENSIVE"]:
        mask = ts["regime"] == rg
        n_days = int(mask.sum())
        if n_days == 0:
            continue
        sub_ret = ts.loc[mask, "daily_ret"]
        cum = (1 + sub_ret).prod() - 1
        years = n_days / 252.0
        ann = (1 + cum) ** (1 / years) - 1 if years > 0.05 else cum
        regime_stats[rg] = {
            "n_days": n_days,
            "cum_ret": float(cum),
            "ann_ret": float(ann),
            "mean_daily": float(sub_ret.mean()),
            "std_daily": float(sub_ret.std()),
        }

    buy_count = 0
    sell_count = 0
    buy_value = 0.0
    sell_value = 0.0
    if not trades.empty:
        actions = trades.get("Action", trades.get("action", pd.Series()))
        values = trades.get("Value", trades.get("value", pd.Series(dtype=float)))
        if not actions.empty:
            buy_mask = actions.str.contains("BUY", case=False, na=False)
            sell_mask = ~buy_mask
            buy_count = int(buy_mask.sum())
            sell_count = int(sell_mask.sum())
            buy_value = float(values[buy_mask].abs().sum()) if not values.empty else 0
            sell_value = float(values[sell_mask].abs().sum()) if not values.empty else 0

    return {
        "name": name,
        "cagr": cagr_all,
        "mdd": mdd_all,
        "regime_stats": regime_stats,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "total_days": len(ts),
    }


def main():
    with open(os.path.join(PHASE3, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]

    pack_pattern = os.path.join(save_dir, "precompute_qresearch_v4_12_2011-01-03_*.npz")
    packs = sorted(glob.glob(pack_pattern), reverse=True)
    if not packs:
        print(f"[ERROR] no walk-forward pack: {pack_pattern}")
        return
    pack_path = packs[0]
    stem = os.path.splitext(os.path.basename(pack_path))[0]
    parts = stem.split("_")
    pack_start, pack_end = parts[-2], parts[-1]
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")

    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    vix_close, vix_regime, vix_smooth = _load_vix(cfg, "2023-01-01", pack_end)
    print(f"[VIX] {len(vix_close)} dates")
    trigger_conf = conf.get("triggers", {})

    all_results = {}

    for fold_name, (start, end) in FOLDS.items():
        print(f"\n{'#'*72}")
        print(f"##  FOLD {fold_name}  ({start} → {end})")
        print(f"{'#'*72}")
        fold_results = {}
        for sig_name, sig_path in SIGNALS.items():
            t0 = time.time()
            r = _run_and_decompose(sig_name, sig_path, cfg, pack, vix_close,
                                   vix_regime, vix_smooth, trigger_conf, start, end)
            elapsed = time.time() - t0
            fold_results[sig_name] = r
            print(f"\n  {sig_name:20s} | CAGR {r['cagr']*100:+6.2f}%  MDD {r['mdd']*100:5.2f}%  ({elapsed:.1f}s)")
            for rg, rs in sorted(r["regime_stats"].items()):
                print(f"    {rg:12s}  {rs['n_days']:4d}d  cum={rs['cum_ret']*100:+7.2f}%  "
                      f"ann={rs['ann_ret']*100:+7.2f}%  "
                      f"μ_daily={rs['mean_daily']*10000:+5.1f}bp  σ_daily={rs['std_daily']*10000:5.1f}bp")
            print(f"    trades: {r['buy_count']} buys (${r['buy_value']:,.0f})  "
                  f"{r['sell_count']} sells (${r['sell_value']:,.0f})")
        all_results[fold_name] = fold_results

    print(f"\n\n{'='*72}")
    print("  COMPARATIVE SUMMARY: SIDE regime return decomposition")
    print(f"{'='*72}")
    print(f"\n{'Signal':20s} | {'Fold':4s} | {'SIDE days':>9s} | {'SIDE cum%':>9s} | {'SIDE ann%':>9s} | {'BULL cum%':>9s} | {'BULL ann%':>9s} | {'ALL CAGR%':>9s}")
    print("-" * 110)
    for fold_name in FOLDS:
        for sig_name in SIGNALS:
            r = all_results[fold_name][sig_name]
            s_side = r["regime_stats"].get("SIDE", {"n_days": 0, "cum_ret": 0, "ann_ret": 0})
            s_bull = r["regime_stats"].get("BULL", {"n_days": 0, "cum_ret": 0, "ann_ret": 0})
            print(f"{sig_name:20s} | {fold_name:4s} | {s_side['n_days']:9d} | "
                  f"{s_side['cum_ret']*100:+9.2f} | {s_side['ann_ret']*100:+9.2f} | "
                  f"{s_bull['cum_ret']*100:+9.2f} | {s_bull['ann_ret']*100:+9.2f} | "
                  f"{r['cagr']*100:+9.2f}")
        print("-" * 110)

    print(f"\n{'Signal':20s} | {'Fold':4s} | {'Δ SIDE cum%':>11s} | {'Δ SIDE ann%':>11s} | {'Δ BULL cum%':>11s} | {'Δ BULL ann%':>11s} | {'Δ ALL CAGR':>11s}")
    print("-" * 100)
    for fold_name in FOLDS:
        base = all_results[fold_name]["Baseline_V2"]
        b_side = base["regime_stats"].get("SIDE", {"cum_ret": 0, "ann_ret": 0})
        b_bull = base["regime_stats"].get("BULL", {"cum_ret": 0, "ann_ret": 0})
        for sig_name in ["ENSEMBLE_C", "ENSEMBLE_D"]:
            r = all_results[fold_name][sig_name]
            s_side = r["regime_stats"].get("SIDE", {"cum_ret": 0, "ann_ret": 0})
            s_bull = r["regime_stats"].get("BULL", {"cum_ret": 0, "ann_ret": 0})
            print(f"{sig_name:20s} | {fold_name:4s} | "
                  f"{(s_side['cum_ret']-b_side['cum_ret'])*100:+11.2f} | "
                  f"{(s_side['ann_ret']-b_side['ann_ret'])*100:+11.2f} | "
                  f"{(s_bull['cum_ret']-b_bull['cum_ret'])*100:+11.2f} | "
                  f"{(s_bull['ann_ret']-b_bull['ann_ret'])*100:+11.2f} | "
                  f"{(r['cagr']-base['cagr'])*100:+11.2f}")
        print("-" * 100)


if __name__ == "__main__":
    main()
