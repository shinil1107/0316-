"""Measure in-sample vs OOS IC for any frozen signal.

Purpose
-------
Resolve the "baseline V2 in-sample IC was ~0.04 (estimated)" claim by
computing the actual realized in-sample IC_3M using the same
portfolio-independent methodology as `step_c_gate_evaluation.py`.

Windows
-------
- In-sample : 2017-02-21 → 2024-05-31   (matches P5_RETRAIN train window)
- OOS       : 2024-06-01 → pack end     (matches Step C / Step A)

Usage
-----
    python3 -u phase3/tests/measure_signal_ic_full.py

Output
------
- Console table comparing baseline V2 + P5_RETRAIN variants across both windows
- JSON dump at phase3/docs/measure_signal_ic_full_<stamp>.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import yaml         # noqa: E402

from phase3.engine_loader import engine         # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (     # noqa: E402
    _build_cfg, _load_vix, _pick_oos_pack, _realized_oos_ic,
)

# ── Fixed windows ────────────────────────────────────────────────────
TRAIN_START = "2017-02-21"
TRAIN_END   = "2024-05-31"
OOS_START   = "2024-06-01"

# ── Signals to measure ───────────────────────────────────────────────
SIGNALS: List[Dict[str, str]] = [
    {
        "name": "Baseline_V2_GOLDEN_ENS_L3",
        "path": ("/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/"
                 "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"),
    },
    {
        "name": "P5_RETRAIN",
        "path": ("/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/"
                 "frozen_signal_P5_RETRAIN_20260423_153457.npz"),
    },
    {
        "name": "P5_RETRAIN_T1",
        "path": ("/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/"
                 "frozen_signal_P5_RETRAIN_T1_20260423_183119.npz"),
    },
    {
        "name": "P5_RETRAIN_T1b",
        "path": ("/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/"
                 "frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz"),
    },
]


def _measure(sig_name: str, sig_path: str,
             pack: Dict[str, Any], cfg, vix_regime_map: Dict[str, str],
             win_start: str, win_end: str) -> Dict[str, float]:
    signal = load_frozen_signal(sig_path)
    out = _realized_oos_ic(pack, signal, cfg, vix_regime_map, win_start, win_end)
    return out


def main() -> int:
    print("=" * 78)
    print("  measure_signal_ic_full — in-sample vs OOS IC per signal")
    print("=" * 78)

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]

    pack_path, pack_start, pack_end = _pick_oos_pack(save_dir)
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")

    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    # Need VIX covering the entire period (train + OOS)
    print("[VIX ] building regime timeseries for full train+OOS window…")
    vix_c, vix_r, vix_s = _load_vix(cfg, TRAIN_START, pack_end)
    print(f"[VIX ] {len(vix_c)} dates")

    results: List[Dict[str, Any]] = []
    for sig in SIGNALS:
        name = sig["name"]
        path = sig["path"]
        if not os.path.exists(path):
            print(f"[WARN] skipping {name}: signal not found ({path})")
            continue

        print(f"\n  computing  {name}")
        print(f"    in-sample [{TRAIN_START} → {TRAIN_END}] …")
        ins = _measure(name, path, pack, cfg, vix_r, TRAIN_START, TRAIN_END)
        print(f"      IC_3M = {ins['oos_mean_ic_3m']:+.5f}  "
              f"(n={ins['oos_days_3m']},  IC_1M = {ins['oos_mean_ic_1m']:+.5f})")

        print(f"    OOS      [{OOS_START} → {pack_end}] …")
        oos = _measure(name, path, pack, cfg, vix_r, OOS_START, pack_end)
        print(f"      IC_3M = {oos['oos_mean_ic_3m']:+.5f}  "
              f"(n={oos['oos_days_3m']},  IC_1M = {oos['oos_mean_ic_1m']:+.5f})")

        gap_3m = ins["oos_mean_ic_3m"] - oos["oos_mean_ic_3m"]
        gap_1m = ins["oos_mean_ic_1m"] - oos["oos_mean_ic_1m"]

        results.append({
            "name":        name,
            "path":        path,
            "in_sample":   ins,
            "oos":         oos,
            "gap_3m":      gap_3m,
            "gap_1m":      gap_1m,
        })

    # ── Summary table ──
    print("\n" + "=" * 78)
    print("  SUMMARY — IC_3M (In-Sample vs OOS)")
    print("=" * 78)
    print(f"  {'Signal':<28} {'IS IC_3M':>10} {'OOS IC_3M':>11} {'Gap':>10} {'IS days':>8} {'OOS days':>9}")
    print(f"  {'-'*28} {'-'*10} {'-'*11} {'-'*10} {'-'*8} {'-'*9}")
    for r in results:
        ins = r["in_sample"]; oos = r["oos"]
        print(f"  {r['name']:<28} "
              f"{ins['oos_mean_ic_3m']:+10.5f} "
              f"{oos['oos_mean_ic_3m']:+11.5f} "
              f"{r['gap_3m']:+10.5f} "
              f"{ins['oos_days_3m']:>8d} "
              f"{oos['oos_days_3m']:>9d}")

    print("\n  SUMMARY — IC_1M (In-Sample vs OOS)")
    print(f"  {'Signal':<28} {'IS IC_1M':>10} {'OOS IC_1M':>11} {'Gap':>10} {'IS days':>8} {'OOS days':>9}")
    print(f"  {'-'*28} {'-'*10} {'-'*11} {'-'*10} {'-'*8} {'-'*9}")
    for r in results:
        ins = r["in_sample"]; oos = r["oos"]
        print(f"  {r['name']:<28} "
              f"{ins['oos_mean_ic_1m']:+10.5f} "
              f"{oos['oos_mean_ic_1m']:+11.5f} "
              f"{r['gap_1m']:+10.5f} "
              f"{ins['oos_days_1m']:>8d} "
              f"{oos['oos_days_1m']:>9d}")

    print("\n  (Gap = IS − OOS;  positive = degradation on OOS)")

    # Save JSON
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(PHASE3_DIR, "docs", f"measure_signal_ic_full_{stamp}.json")
    with open(out_path, "w") as f:
        json.dump({
            "generated_at": stamp,
            "train_window": {"start": TRAIN_START, "end": TRAIN_END},
            "oos_window":   {"start": OOS_START,   "end": pack_end},
            "pack":         os.path.basename(pack_path),
            "results":      results,
        }, f, indent=2, default=float)
    print(f"\n  [saved] {out_path}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
