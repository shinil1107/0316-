"""P5c — Walk-Forward verification of vt_S_18 vs Baseline_V2.

Runs the same 6-fold design as ``step_d_walk_forward.py`` but for two
arms only:

    base                Baseline_V2 (V2_GOLDEN, buy_grace=3, no vol_target)
    base_vt_S_18        Baseline_V2 + SIDE-only vol_target(target=0.18,
                                                            lookback=30,
                                                            min_scale=0.30)

Goal: confirm that the small headline gain (+0.48pp CAGR, +0.024 Sharpe,
flat MDD) seen on the full-period sweep generalises across folds, with
acceptable per-fold stability and SPY-relative performance.

Gates
-----
- G6-A : CV(cand) ≤ 0.5
- G6-B : CV(cand) ≤ CV(base)
- G6-C : worst-fold CAGR(cand) ≥ worst-fold CAGR(base)
- G6-D : all 6 folds CAGR(cand) > 0
- G7-C : ≥ 5/6 folds where cand_CAGR > spy_CAGR  (production criterion)

Outputs
-------
- console summary
- ``phase3/docs/p5c_vt_S18_walk_forward_<ts>.md``
- ``phase3/docs/p5c_vt_S18_walk_forward_<ts>.json``
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

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
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg, _load_vix, _run_sim,
)

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"
SIGNAL_PATH = f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"

FOLDS: List[Dict[str, str]] = [
    {"id": "F0a", "start": "2012-01-01", "end": "2014-12-31", "group": "pre_oos"},
    {"id": "F0b", "start": "2015-01-01", "end": "2016-12-31", "group": "pre_oos"},
    {"id": "F1",  "start": "2019-01-01", "end": "2020-12-31", "group": "in_sample"},
    {"id": "F2",  "start": "2021-01-01", "end": "2022-12-31", "group": "in_sample"},
    {"id": "F3",  "start": "2023-01-01", "end": "2024-05-31", "group": "in_sample"},
    {"id": "F4",  "start": "2024-06-01", "end": PACK_END_STR, "group": "post_oos"},
]

VT_S18 = {
    "SIDE": {
        "enabled": True,
        "annual_target": 0.18,
        "lookback_days": 30,
        "min_scale": 0.30,
        "max_scale": 1.0,
    }
}

ARMS: List[Dict[str, Any]] = [
    {"id": "base",         "label": "Baseline_V2",        "vt": None},
    {"id": "base_vt_S_18", "label": "Baseline_V2+vt_S_18", "vt": VT_S18},
]


def _pick_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(f"No pack matching {pattern}")
    p = candidates[0]
    parts = os.path.splitext(os.path.basename(p))[0].split("_")
    return p, parts[-2], parts[-1]


def _spy_cagr_for_fold(spy_series, start: str, end: str) -> float:
    """Compute SPY CAGR over [start, end] from a price series.

    spy_series is a list of (date_str, close) tuples or a DataFrame.
    Falls back to NaN if the window has < 2 points.
    """
    if spy_series is None or len(spy_series) == 0:
        return float("nan")
    import pandas as _pd
    if hasattr(spy_series, "iloc"):
        df = spy_series
        date_col = "date" if "date" in df.columns else ("Date" if "Date" in df.columns else None)
        close_col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
        if date_col is None or close_col is None:
            return float("nan")
        sd_ts = _pd.Timestamp(start); ed_ts = _pd.Timestamp(end)
        d_series = _pd.to_datetime(df[date_col])
        sub = df[(d_series >= sd_ts) & (d_series <= ed_ts)]
        if len(sub) < 2:
            return float("nan")
        first = float(sub.iloc[0][close_col])
        last = float(sub.iloc[-1][close_col])
    else:
        pts = [(d, c) for d, c in spy_series if start <= d <= end]
        if len(pts) < 2:
            return float("nan")
        first = float(pts[0][1])
        last = float(pts[-1][1])
    days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    years = max(days / 365.25, 1e-6)
    return (last / first) ** (1.0 / years) - 1.0


def _try_load_spy_series(cfg, pack_start, pack_end):
    try:
        from phase3.tests.step_e_spy_benchmark import _load_spy_series
        return _load_spy_series(cfg, pack_start, pack_end)
    except Exception as e:
        print(f"[spy] series unavailable: {e}")
        return None


def _stats(xs: List[float]) -> Dict[str, float]:
    arr = np.asarray(xs, dtype=np.float64)
    mean = float(arr.mean()) if arr.size else float("nan")
    std = float(arr.std(ddof=0)) if arr.size else float("nan")
    cv = float(std / abs(mean)) if abs(mean) > 1e-9 else float("nan")
    return {
        "n": int(arr.size),
        "mean": mean, "std": std, "cv": cv,
        "min": float(arr.min()) if arr.size else float("nan"),
        "max": float(arr.max()) if arr.size else float("nan"),
        "pos_count": int((arr > 0).sum()),
    }


def main() -> int:
    print("=" * 72)
    print("  P5c — vt_S_18 walk-forward verification")
    print("=" * 72)

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]
    pack_path, pack_start, pack_end = _pick_pack(save_dir)
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")
    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] loaded {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")
    vc, vr, vs = _load_vix(cfg, pack_start, pack_end)
    print(f"[vix ] {len(vc)} dates")
    trigger_conf = conf.get("triggers", {})
    spy_series = _try_load_spy_series(cfg, pack_start, pack_end)

    BUY_GRACE_DAYS = int(conf.get("strategy", {}).get("buy_grace_days", 3) or 3)
    print(f"[cfg ] buy_grace_days = {BUY_GRACE_DAYS}")

    results: Dict[str, List[Dict[str, Any]]] = {a["id"]: [] for a in ARMS}
    t_total = time.time()
    for arm in ARMS:
        print()
        print("#" * 72)
        print(f"##  ARM: {arm['label']}  ({arm['id']})")
        print(f"##  vol_target = {arm['vt']}")
        print("#" * 72)
        for fold in FOLDS:
            start, end = fold["start"], fold["end"]
            print()
            print("-" * 60)
            print(f"  FOLD {fold['id']}  ({start} → {end})  [{fold['group']}]")
            print("-" * 60)
            t0 = time.time()
            sim = _run_sim(
                arm_name=f"{arm['label']}__{fold['id']}",
                signal_path=SIGNAL_PATH,
                cfg=cfg, pack=pack,
                vix_close_map=vc, vix_regime_map=vr, vix_smooth_map=vs,
                trigger_conf=trigger_conf,
                oos_start=start, oos_end=end,
                buy_grace_days=BUY_GRACE_DAYS,
                vol_target_per_regime=arm["vt"],
            )
            elapsed = time.time() - t0
            m = sim["metrics"]
            spy_cagr = _spy_cagr_for_fold(spy_series, start, end)
            results[arm["id"]].append({
                "fold": fold["id"], "group": fold["group"],
                "window_start": start, "window_end": end,
                "elapsed_sec": round(elapsed, 1),
                "metrics": {
                    "CAGR":         float(m.get("CAGR", float("nan"))),
                    "Max_Drawdown": float(m.get("Max_Drawdown", float("nan"))),
                    "Net_Sharpe":   float(m.get("Net_Sharpe", float("nan"))),
                    "Calmar_Ratio": float(m.get("Calmar_Ratio", float("nan"))),
                    "Commission_Pct": float(m.get("Commission_Pct", float("nan"))),
                    "Final_Value":  float(m.get("Final_Value", float("nan"))),
                    "vol_target_rebal_days_engaged": int(m.get("vol_target_rebal_days_engaged", 0)),
                    "vol_target_rebal_days_warmed": int(m.get("vol_target_rebal_days_warmed", 0)),
                },
                "spy_cagr": float(spy_cagr) if not (isinstance(spy_cagr, float) and np.isnan(spy_cagr)) else None,
            })
    total_elapsed = time.time() - t_total

    base_cagrs = [r["metrics"]["CAGR"] for r in results["base"]]
    cand_cagrs = [r["metrics"]["CAGR"] for r in results["base_vt_S_18"]]
    base_stats = _stats(base_cagrs)
    cand_stats = _stats(cand_cagrs)

    print()
    print("=" * 100)
    print("  Walk-Forward Headline (per fold CAGR)")
    print("=" * 100)
    print(f"  {'fold':<5s} {'window':<26s} {'group':<10s}  "
          f"{'base':>9s}  {'cand':>9s}  {'Δ':>7s}  {'spy':>7s}  "
          f"{'cand>spy':>10s}  {'engage/warm':>13s}")
    print("-" * 100)
    spy_beats = 0
    spy_total = 0
    for i, fold in enumerate(FOLDS):
        rb = results["base"][i]; rc = results["base_vt_S_18"][i]
        cb = rb["metrics"]["CAGR"] * 100
        cc = rc["metrics"]["CAGR"] * 100
        sp = rb["spy_cagr"]
        sp_str = f"{sp*100:>+6.2f}%" if sp is not None else "    n/a"
        beat = "—"
        if sp is not None and not (isinstance(sp, float) and np.isnan(sp)):
            spy_total += 1
            if cc / 100 > sp:
                spy_beats += 1
                beat = "yes"
            else:
                beat = "no"
        eng = rc["metrics"]["vol_target_rebal_days_engaged"]
        warm = rc["metrics"]["vol_target_rebal_days_warmed"]
        print(f"  {fold['id']:<5s} {fold['start']}→{fold['end']:<14s} "
              f"{fold['group']:<10s}  {cb:>+7.2f}%  {cc:>+7.2f}%  "
              f"{cc - cb:>+6.2f}pp  {sp_str}  {beat:>10s}  {eng:>5d}/{warm:<5d}")
    print("-" * 100)

    print()
    print("=" * 100)
    print("  Summary stats (CAGR across 6 folds)")
    print("=" * 100)
    print(f"  {'arm':<22s}  {'mean':>8s}  {'std':>8s}  {'CV':>6s}  "
          f"{'worst':>9s}  {'best':>9s}  {'pos/n':>7s}")
    print("-" * 100)
    print(f"  {'Baseline_V2':<22s}  {base_stats['mean']*100:>+7.2f}%  "
          f"{base_stats['std']*100:>+7.2f}%  {base_stats['cv']:>6.3f}  "
          f"{base_stats['min']*100:>+7.2f}%  {base_stats['max']*100:>+7.2f}%  "
          f"{base_stats['pos_count']:>3d}/{base_stats['n']:<3d}")
    print(f"  {'Baseline_V2+vt_S_18':<22s}  {cand_stats['mean']*100:>+7.2f}%  "
          f"{cand_stats['std']*100:>+7.2f}%  {cand_stats['cv']:>6.3f}  "
          f"{cand_stats['min']*100:>+7.2f}%  {cand_stats['max']*100:>+7.2f}%  "
          f"{cand_stats['pos_count']:>3d}/{cand_stats['n']:<3d}")
    print("=" * 100)

    g6a = bool(cand_stats["cv"] <= 0.5)
    g6b = bool(cand_stats["cv"] <= base_stats["cv"])
    g6c = bool(cand_stats["min"] >= base_stats["min"])
    g6d = bool(cand_stats["pos_count"] == cand_stats["n"] and cand_stats["n"] > 0)
    g7c = bool(spy_total > 0 and spy_beats >= 5)
    gates = {
        "G6_A_cv_le_0p5":         {"cv": cand_stats["cv"], "pass": g6a},
        "G6_B_cv_le_base":        {"cv_cand": cand_stats["cv"], "cv_base": base_stats["cv"], "pass": g6b},
        "G6_C_worst_ge_base":     {"worst_cand": cand_stats["min"], "worst_base": base_stats["min"], "pass": g6c},
        "G6_D_all_positive":      {"pos_count": cand_stats["pos_count"], "n": cand_stats["n"], "pass": g6d},
        "G7_C_beat_spy_5_of_6":   {"beats": spy_beats, "total": spy_total, "pass": g7c},
    }
    print()
    print("  GATES")
    print("-" * 60)
    for k, v in gates.items():
        mark = "✓ PASS" if v["pass"] else "✗ FAIL"
        print(f"  {k:<26s}  {mark}   {v}")
    print("-" * 60)

    all_pass = all(v["pass"] for v in gates.values())
    print()
    print(f"  total elapsed: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"  VERDICT: {'✓ PROMOTE — all gates pass' if all_pass else '⚠ MIXED — review gate failures before promotion'}")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(DOCS_DIR, f"p5c_vt_S18_walk_forward_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"p5c_vt_S18_walk_forward_{stamp}.json")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_basename": os.path.basename(pack_path),
        "buy_grace_days": BUY_GRACE_DAYS,
        "signal_path": SIGNAL_PATH,
        "arms": ARMS,
        "folds": FOLDS,
        "results": results,
        "stats": {"base": base_stats, "cand": cand_stats},
        "gates": gates,
        "spy_beats": spy_beats, "spy_total": spy_total,
        "total_elapsed_sec": round(total_elapsed, 1),
        "verdict": "PROMOTE" if all_pass else "MIXED",
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[saved] {json_path}")

    L: List[str] = []
    L.append(f"# P5c — vt_S_18 Walk-Forward Verification")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Pack**: `{os.path.basename(pack_path)}`")
    L.append(f"**Signal**: `{os.path.basename(SIGNAL_PATH)}` (Baseline_V2)")
    L.append(f"**Setup**: $100K · $1K/day · 10/5 bps · SIDE_DEF_p12 · "
             f"daily rebal · buy_grace_days={BUY_GRACE_DAYS}")
    L.append(f"**vt_S_18**: SIDE-only, target=18% annualised, lookback=30d, min_scale=0.30")
    L.append("")
    L.append("## Per-fold CAGR")
    L.append("")
    L.append("| fold | window | group | base | vt_S_18 | Δ | SPY | beats SPY | engage/warm |")
    L.append("|---|---|---|---:|---:|---:|---:|:---:|---:|")
    for i, fold in enumerate(FOLDS):
        rb = results["base"][i]; rc = results["base_vt_S_18"][i]
        cb = rb["metrics"]["CAGR"] * 100; cc = rc["metrics"]["CAGR"] * 100
        sp = rb["spy_cagr"]
        sp_str = f"{sp*100:+.2f}%" if sp is not None else "n/a"
        beat = "—"
        if sp is not None:
            beat = "✓" if cc / 100 > sp else "✗"
        eng = rc["metrics"]["vol_target_rebal_days_engaged"]
        warm = rc["metrics"]["vol_target_rebal_days_warmed"]
        L.append(f"| **{fold['id']}** | {fold['start']} → {fold['end']} | {fold['group']} "
                 f"| {cb:+.2f}% | {cc:+.2f}% | {cc-cb:+.2f}pp | {sp_str} | {beat} | {eng}/{warm} |")
    L.append("")
    L.append("## Summary stats (CAGR across 6 folds)")
    L.append("")
    L.append("| arm | mean | std | CV | worst | best | pos/n |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    L.append(f"| Baseline_V2 | {base_stats['mean']*100:+.2f}% | {base_stats['std']*100:+.2f}% "
             f"| {base_stats['cv']:.3f} | {base_stats['min']*100:+.2f}% | {base_stats['max']*100:+.2f}% "
             f"| {base_stats['pos_count']}/{base_stats['n']} |")
    L.append(f"| **Baseline_V2 + vt_S_18** | **{cand_stats['mean']*100:+.2f}%** "
             f"| {cand_stats['std']*100:+.2f}% | **{cand_stats['cv']:.3f}** "
             f"| {cand_stats['min']*100:+.2f}% | {cand_stats['max']*100:+.2f}% "
             f"| {cand_stats['pos_count']}/{cand_stats['n']} |")
    L.append("")
    L.append("## Gates")
    L.append("")
    for k, v in gates.items():
        mark = "✓ PASS" if v["pass"] else "✗ FAIL"
        L.append(f"- **{k}** — {mark}  `{v}`")
    L.append("")
    L.append(f"**Verdict**: {'✓ PROMOTE — all gates pass' if all_pass else '⚠ MIXED'}")
    with open(md_path, "w") as f:
        f.write("\n".join(L))
    print(f"[saved] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
