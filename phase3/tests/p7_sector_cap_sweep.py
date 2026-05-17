"""P7 — Sector-cap sweep (R2).

Tests the A2 / R2 sector-cap concentration limit on the V2_GOLDEN
signal across a reliable 10-yr-survivor S&P 500 universe (334 tickers
loaded from ``cache_fmp_c2_1/universe/r2_sector_universe.json``).

What this answers
-----------------
1. How costly is universe restriction?  (full pack vs R2-only, no cap)
2. Does a per-sector cap on portfolio share improve risk-adjusted
   return on a survivor-bias-aware universe?  (R2 × cap ∈ {0.20, 0.25,
   0.30, 0.35, 0.40} vs R2-only baseline.)
3. How often does each cap actually bind?  (sector_cap_breach_days,
   sector_cap_filtered_total — emitted by simulator R2 hooks.)

Survivor-bias caveat
--------------------
The R2 universe is built from CURRENT S&P 500 constituents whose
``dateFirstAdded ≤ 2016-01-01``.  This guarantees high-fidelity sector
metadata (FMP ``/stable/profile`` resolves all 334 tickers cleanly) but
introduces winner bias — companies that delisted between 2016 and now
are excluded.  The full-universe baseline keeps the comparison honest
on the cost-of-restriction axis; cap variants are only compared against
the R2-restricted control to isolate the cap effect from the universe
effect.

Output
------
- console summary
- ``phase3/docs/p7_sector_cap_sweep_<stamp>.md``
- ``phase3/docs/p7_sector_cap_sweep_<stamp>.json``

Usage
-----
    python3 -u phase3/tests/p7_sector_cap_sweep.py
    python3 -u phase3/tests/p7_sector_cap_sweep.py --window full
    python3 -u phase3/tests/p7_sector_cap_sweep.py --window post_oos
    python3 -u phase3/tests/p7_sector_cap_sweep.py --arms r2_only,r2_cap_30
    python3 -u phase3/tests/p7_sector_cap_sweep.py --signal v2
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
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3.tests.step_c_gate_evaluation import _build_cfg, _load_vix, _run_sim  # noqa: E402

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
UNIVERSE_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/universe"
R2_UNIVERSE_PATH = os.path.join(UNIVERSE_DIR, "r2_sector_universe.json")

PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"


# ── Default signal set ───────────────────────────────────────────────
SIGNALS: Dict[str, Dict[str, str]] = {
    "v2": {
        "id": "v2",
        "label": "V2_GOLDEN",
        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
    },
}


# ── Window presets ───────────────────────────────────────────────────
WINDOWS = {
    "full":     ("2012-01-03", PACK_END_STR),
    "post_oos": ("2024-06-01", PACK_END_STR),
    "f1":       ("2018-01-01", "2019-12-31"),
    "f2":       ("2021-01-01", "2022-12-31"),
}


# ── Arm definitions ──────────────────────────────────────────────────
# Each arm = (universe_restriction, sector_cap_max_pct).
#   universe="full"   ⇒ no restriction (full pack universe)
#   universe="r2"     ⇒ restricted to the 334-ticker R2 list
#   max_pct=None      ⇒ cap disabled (sector_cap.enabled=False)
ARMS: List[Dict[str, Any]] = [
    {"id": "baseline_full", "label": "Full SP&500 (no cap)",   "universe": "full", "max_pct": None},
    {"id": "r2_only",        "label": "R2 only (no cap)",       "universe": "r2",   "max_pct": None},
    {"id": "r2_cap_40",      "label": "R2 + cap 40%",           "universe": "r2",   "max_pct": 0.40},
    {"id": "r2_cap_35",      "label": "R2 + cap 35%",           "universe": "r2",   "max_pct": 0.35},
    {"id": "r2_cap_30",      "label": "R2 + cap 30%",           "universe": "r2",   "max_pct": 0.30},
    {"id": "r2_cap_25",      "label": "R2 + cap 25%",           "universe": "r2",   "max_pct": 0.25},
    {"id": "r2_cap_20",      "label": "R2 + cap 20%",           "universe": "r2",   "max_pct": 0.20},
]


def _load_r2_universe(path: str = R2_UNIVERSE_PATH) -> Tuple[List[str], Dict[str, str]]:
    with open(path, "r") as f:
        d = json.load(f)
    tickers = list(d.get("tickers", []))
    sec_map = dict(d.get("sector_by_ticker", {}))
    return tickers, sec_map


def _pick_pack(save_dir: str) -> Tuple[str, str, str]:
    pat = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    candidates = sorted(glob.glob(pat), reverse=True)
    if not candidates:
        raise RuntimeError(f"No pack found matching {pat}")
    p = candidates[0]
    parts = os.path.splitext(os.path.basename(p))[0].split("_")
    return p, parts[-2], parts[-1]


def _load_spy(cfg, ps, pe):
    try:
        from phase3.tests.step_e_spy_benchmark import _load_spy_series
        return _load_spy_series(cfg, ps, pe)
    except Exception:
        return None


def _spy_cagr(spy_df, start: str, end: str) -> float:
    if spy_df is None or not hasattr(spy_df, "iloc") or len(spy_df) == 0:
        return float("nan")
    import pandas as _pd
    sd = _pd.Timestamp(start); ed = _pd.Timestamp(end)
    d = _pd.to_datetime(spy_df["date"])
    sub = spy_df[(d >= sd) & (d <= ed)]
    if len(sub) < 2:
        return float("nan")
    first = float(sub.iloc[0]["close"]); last = float(sub.iloc[-1]["close"])
    yrs = max(
        (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days / 365.25,
        1e-6,
    )
    return (last / first) ** (1.0 / yrs) - 1.0


def _build_strategy_patch(
    arm: Dict[str, Any],
    r2_tickers: List[str],
    r2_sector_map: Dict[str, str],
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    if arm.get("universe") == "r2":
        patch["restricted_universe"] = list(r2_tickers)
    if arm.get("max_pct") is not None:
        patch["sector_cap"] = {
            "enabled": True,
            "max_pct": float(arm["max_pct"]),
            "sector_by_ticker": dict(r2_sector_map),
            "exempt_unknown": True,
        }
    else:
        # Diagnostic-only: pass the sector map so the simulator can
        # still record max_breach_pct / breach_days even when the cap
        # is disabled — useful for sanity-checking how often the cap
        # would bind in the no-cap baseline.
        patch["sector_cap"] = {
            "enabled": False,
            "max_pct": 1.0,
            "sector_by_ticker": dict(r2_sector_map),
            "exempt_unknown": True,
        }
    return patch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="full", choices=list(WINDOWS.keys()),
                    help="Evaluation window (default: full 14yr).")
    ap.add_argument("--signal", default="v2", choices=list(SIGNALS.keys()),
                    help="Which frozen signal to evaluate (default: v2).")
    ap.add_argument("--arms", default=None,
                    help="Comma-separated arm ids (default: all).")
    ap.add_argument("--buy-grace", type=int, default=3,
                    help="Buy-grace days applied to all arms (default: 3 — current production).")
    args = ap.parse_args()

    arm_filter = set(args.arms.split(",")) if args.arms else None
    arms = [a for a in ARMS if (arm_filter is None or a["id"] in arm_filter)]
    sig = SIGNALS[args.signal]

    win_start, win_end = WINDOWS[args.window]

    print("=" * 80)
    print("  P7 — Sector-Cap Sweep (R2)")
    print(f"  signal: {sig['label']}    window: {args.window} ({win_start} → {win_end})")
    print(f"  arms:   {len(arms)}    buy_grace_days: {args.buy_grace}")
    print("=" * 80)

    r2_tickers, r2_sec_map = _load_r2_universe()
    print(f"  R2 universe: {len(r2_tickers)} tickers, {len(set(r2_sec_map.values()))} sectors")
    print(f"    cutoff: dateFirstAdded ≤ 2016-01-01 (current S&P 500 survivors)")

    with open(os.path.join(PHASE3_DIR, "config.yaml")) as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]
    pack_path, ps, pe = _pick_pack(save_dir)
    cfg = _build_cfg(conf, ps, pe)
    pack = engine.load_precompute_panel(cfg, ps, pe)
    if pack is None:
        prep = engine.prepare_inputs(cfg)
        pack = prep["pack"] if isinstance(prep, dict) else prep
    vc, vr, vs = _load_vix(cfg, ps, pe)
    trigger_conf = conf.get("triggers", {})
    spy_df = _load_spy(cfg, ps, pe)
    spy_cagr_window = _spy_cagr(spy_df, win_start, win_end)
    spy_str = (f"{spy_cagr_window*100:+.2f}%"
               if not np.isnan(spy_cagr_window) else "n/a")
    print(f"  pack: {os.path.basename(pack_path)}    SPY CAGR (window) = {spy_str}")

    # Pack universe size for context
    try:
        n_pack_tickers = int(np.asarray(pack["tickers"]).size)
    except Exception:
        n_pack_tickers = -1
    print(f"  pack universe: {n_pack_tickers} tickers")
    in_pack = sum(1 for t in r2_tickers
                  if isinstance(pack.get("tickers"), (list, np.ndarray))
                  and t in (list(pack["tickers"]) if isinstance(pack["tickers"], np.ndarray) else pack["tickers"]))
    print(f"  R2 ∩ pack:    {in_pack} tickers")

    cells: List[Dict[str, Any]] = []
    t_total = time.time()
    cell_i = 0
    for arm in arms:
        cell_i += 1
        print()
        print("-" * 70)
        print(f"  [{cell_i}/{len(arms)}]  arm = {arm['label']}")
        print("-" * 70)
        patch = _build_strategy_patch(arm, r2_tickers, r2_sec_map)
        t0 = time.time()
        res = _run_sim(
            arm_name=f"{sig['label']}__{arm['id']}",
            signal_path=sig["path"],
            cfg=cfg, pack=pack,
            vix_close_map=vc, vix_regime_map=vr, vix_smooth_map=vs,
            trigger_conf=trigger_conf,
            oos_start=win_start, oos_end=win_end,
            buy_grace_days=int(args.buy_grace),
            vol_target_per_regime=None,
            strategy_patch=patch,
            regime_overrides_patch=None,
        )
        elapsed = time.time() - t0
        m = res["metrics"]
        cells.append({
            "arm_id":   arm["id"],
            "arm_label": arm["label"],
            "universe": arm["universe"],
            "max_pct":   arm["max_pct"],
            "elapsed_sec":                              round(elapsed, 1),
            "CAGR":                                     float(m["CAGR"]),
            "Sharpe":                                   float(m["Net_Sharpe"]),
            "MDD":                                      float(m["Max_Drawdown"]),
            "Calmar":                                   float(m["Calmar_Ratio"]),
            "Comm_pct":                                 float(m.get("Commission_Pct_of_Capital", 0.0)),
            "turnover_proxy":                           float(m.get("turnover_annualized_proxy", 0.0)),
            "rebal_days":                               int(m.get("Rebalance_Days", 0)),
            "ru_filtered_total":                        int(m.get("restricted_universe_filtered_total", 0)),
            "ru_rebal_days_with_filter":                int(m.get("restricted_universe_rebal_days_with_filter", 0)),
            "sec_cap_filtered_total":                   int(m.get("sector_cap_filtered_total", 0)),
            "sec_cap_rebal_days_with_filter":           int(m.get("sector_cap_rebal_days_with_filter", 0)),
            "sec_cap_max_breach_pct":                   float(m.get("sector_cap_max_breach_pct", 0.0)),
            "sec_cap_breach_days":                      int(m.get("sector_cap_breach_days", 0)),
        })

    total_elapsed = time.time() - t_total

    # ── Performance table ─────────────────────────────────────────────
    print()
    print("=" * 110)
    print("  Performance summary")
    print("=" * 110)
    hdr = (f"  {'arm':<24s}  {'CAGR':>9s}  {'Sharpe':>8s}  {'MDD':>9s}  "
           f"{'Calmar':>7s}  {'Comm%':>7s}  {'Rebal':>6s}")
    print(hdr); print("-" * 110)
    for c in cells:
        print(f"  {c['arm_label']:<24s}  {c['CAGR']*100:>+8.2f}%  {c['Sharpe']:>8.3f}  "
              f"{c['MDD']*100:>8.2f}%  {c['Calmar']:>7.3f}  {c['Comm_pct']:>6.2f}%  {c['rebal_days']:>6d}")
    print("=" * 110)

    # ── Concentration / cap diagnostics ──────────────────────────────
    print()
    print("=" * 110)
    print("  Concentration diagnostics")
    print("=" * 110)
    hdr2 = (f"  {'arm':<24s}  {'max breach':>10s}  {'breach days':>11s}  "
            f"{'cap filtered':>12s}  {'cap rebal-d':>12s}  {'RU filtered':>12s}")
    print(hdr2); print("-" * 110)
    for c in cells:
        print(f"  {c['arm_label']:<24s}  {c['sec_cap_max_breach_pct']*100:>+9.2f}%  "
              f"{c['sec_cap_breach_days']:>11d}  {c['sec_cap_filtered_total']:>12d}  "
              f"{c['sec_cap_rebal_days_with_filter']:>12d}  {c['ru_filtered_total']:>12d}")
    print("=" * 110)

    # ── Δ vs r2_only baseline ─────────────────────────────────────────
    by_id = {c["arm_id"]: c for c in cells}
    base = by_id.get("r2_only")
    if base is not None:
        print()
        print("=" * 90)
        print("  Δ vs R2-only baseline (positive = improvement, except MDD where lower is better)")
        print("=" * 90)
        print(f"  {'arm':<24s}  {'ΔCAGR':>8s}  {'ΔSharpe':>9s}  {'ΔMDD':>8s}  {'ΔCalmar':>9s}  {'ΔComm%':>8s}")
        print("-" * 90)
        for c in cells:
            if c["arm_id"] == "r2_only":
                continue
            dCAGR = (c["CAGR"] - base["CAGR"]) * 100
            dSh = c["Sharpe"] - base["Sharpe"]
            dMDD = (c["MDD"] - base["MDD"]) * 100
            dCal = c["Calmar"] - base["Calmar"]
            dCom = c["Comm_pct"] - base["Comm_pct"]
            print(f"  {c['arm_label']:<24s}  {dCAGR:>+7.2f}pp  {dSh:>+8.3f}  "
                  f"{dMDD:>+7.2f}pp  {dCal:>+8.3f}  {dCom:>+7.2f}pp")
        print("=" * 90)

    print()
    print(f"  total elapsed: {total_elapsed:.1f}s  ({total_elapsed/60:.1f} min)")

    # ── Persist ──────────────────────────────────────────────────────
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(DOCS_DIR, f"p7_sector_cap_sweep_{stamp}.json")
    md_path = os.path.join(DOCS_DIR, f"p7_sector_cap_sweep_{stamp}.md")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack": os.path.basename(pack_path),
        "window": args.window,
        "window_start": win_start, "window_end": win_end,
        "buy_grace_days": int(args.buy_grace),
        "signal": sig,
        "r2_universe_path": R2_UNIVERSE_PATH,
        "r2_n_tickers": len(r2_tickers),
        "r2_n_in_pack": int(in_pack),
        "spy_cagr_window": float(spy_cagr_window) if not np.isnan(spy_cagr_window) else None,
        "arms": arms,
        "cells": cells,
        "total_elapsed_sec": round(total_elapsed, 1),
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[saved] {json_path}")

    # markdown
    L: List[str] = []
    L.append(f"# P7 — Sector-Cap Sweep (R2)")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Signal**: `{sig['label']}`  &nbsp;&nbsp; **Window**: `{args.window}` ({win_start} → {win_end})")
    L.append(f"**Buy-grace days**: {args.buy_grace}  &nbsp;&nbsp; **R2 universe**: {len(r2_tickers)} tickers (∩ pack = {in_pack})")
    if not np.isnan(spy_cagr_window):
        L.append(f"**SPY CAGR (window)**: {spy_cagr_window*100:+.2f}%")
    L.append(f"**Pack**: `{os.path.basename(pack_path)}`")
    L.append("")
    L.append("## Performance")
    L.append("")
    L.append("| arm | CAGR | Sharpe | MDD | Calmar | Comm% | Rebal |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for c in cells:
        L.append(f"| {c['arm_label']} | {c['CAGR']*100:+.2f}% | {c['Sharpe']:.3f} | "
                 f"{c['MDD']*100:.2f}% | {c['Calmar']:.3f} | {c['Comm_pct']:.2f}% | {c['rebal_days']} |")
    L.append("")
    L.append("## Concentration diagnostics")
    L.append("")
    L.append("| arm | max breach | breach days | cap filtered | cap rebal-d | RU filtered |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for c in cells:
        L.append(f"| {c['arm_label']} | {c['sec_cap_max_breach_pct']*100:+.2f}% | "
                 f"{c['sec_cap_breach_days']} | {c['sec_cap_filtered_total']} | "
                 f"{c['sec_cap_rebal_days_with_filter']} | {c['ru_filtered_total']} |")
    L.append("")
    if base is not None:
        L.append("## Δ vs R2-only (positive = improvement, except MDD where lower is better)")
        L.append("")
        L.append("| arm | ΔCAGR | ΔSharpe | ΔMDD | ΔCalmar | ΔComm% |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for c in cells:
            if c["arm_id"] == "r2_only":
                continue
            dCAGR = (c["CAGR"] - base["CAGR"]) * 100
            dSh = c["Sharpe"] - base["Sharpe"]
            dMDD = (c["MDD"] - base["MDD"]) * 100
            dCal = c["Calmar"] - base["Calmar"]
            dCom = c["Comm_pct"] - base["Comm_pct"]
            L.append(f"| {c['arm_label']} | {dCAGR:+.2f}pp | {dSh:+.3f} | "
                     f"{dMDD:+.2f}pp | {dCal:+.3f} | {dCom:+.2f}pp |")
        L.append("")
    L.append("---")
    L.append(f"_total elapsed: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)_")
    with open(md_path, "w") as f:
        f.write("\n".join(L))
    print(f"[saved] {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
