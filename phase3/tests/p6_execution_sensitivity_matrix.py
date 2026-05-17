"""P6 — Execution-policy sensitivity matrix (R1).

Cross-tabulates a list of frozen signals against a list of execution
policies and reports per-cell metrics (CAGR, Sharpe, MDD, Calmar,
Comm-pct).  The point is to measure **how robust each signal is to
execution-layer choices** — a robust signal should show small spread
across policies, while a fragile one tunes itself implicitly to whatever
default policy was assumed during GA training.

Why this exists
---------------
The Phase-5 GA fitness uses signal-quality terms (IC, spread) plus two
cheap friction proxies (``w_turnover``, ``w_cost``).  Other execution
levers — ``buy_grace_days``, ``vol_target``, deploy-rate, trim
threshold — are *not* in fitness; we apply them at runtime.  As we saw
with ``vt_S_18`` (full-period +0.48 pp CAGR but walk-forward -0.07 pp),
the gain from any execution overlay is signal-specific.  This matrix
makes that fact directly visible: each row tells us, for a given
signal, which execution overlays help / neutralise / hurt.

Output
------
- console summary
- ``phase3/docs/p6_exec_sensitivity_matrix_<stamp>.md``
- ``phase3/docs/p6_exec_sensitivity_matrix_<stamp>.json``

Usage
-----
    python3 -u phase3/tests/p6_execution_sensitivity_matrix.py
    python3 -u phase3/tests/p6_execution_sensitivity_matrix.py --window full
    python3 -u phase3/tests/p6_execution_sensitivity_matrix.py --window post_oos
    python3 -u phase3/tests/p6_execution_sensitivity_matrix.py --signals v2,p5c_bull_free
    python3 -u phase3/tests/p6_execution_sensitivity_matrix.py --policies default,buy_grace_3,vt_S_18
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import copy
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
from phase3.tests.step_c_gate_evaluation import _build_cfg, _load_vix, _run_sim  # noqa: E402

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"


# ── Default signal set ───────────────────────────────────────────────
SIGNALS: List[Dict[str, str]] = [
    {"id": "v2",            "label": "V2_GOLDEN",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"},
    {"id": "t1b_inj",       "label": "T1b_BULL_INJECTED",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_20260423_225842.npz"},
    {"id": "p5c_bull_free", "label": "P5C_BULL_FREE",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_BULL_FREE_20260424_192109.npz"},
    {"id": "p5c_side_heavy","label": "P5C_SIDE_HEAVY",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_SIDE_HEAVY_20260424_232713.npz"},
    {"id": "p6_ens_a",      "label": "P6_ENSEMBLE_A",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_A_20260425_011612.npz"},
]


# ── Default policy set ───────────────────────────────────────────────
# Each entry maps to one execution-layer overlay applied on top of the
# LEGACY_STRATEGY in step_c.  Spans both signal-domain knobs (buy_grace)
# and portfolio-domain knobs (vol_target, deploy-rate, trim), so we can
# tell which dimension a given signal is sensitive to.
def _vt_block(target=0.18, lookback=30, min_scale=0.30) -> Dict[str, Any]:
    return {"enabled": True, "annual_target": target,
            "lookback_days": lookback, "min_scale": min_scale, "max_scale": 1.0}


POLICIES: List[Dict[str, Any]] = [
    {
        "id": "default",
        "label": "Default",
        "buy_grace_days": 0,
        "vol_target_per_regime": None,
        "strategy_patch": None,
        "regime_overrides_patch": None,
        "intent": "LEGACY V2 strategy verbatim — control.",
    },
    {
        "id": "buy_grace_3",
        "label": "+buy_grace=3",
        "buy_grace_days": 3,
        "vol_target_per_regime": None,
        "strategy_patch": None,
        "regime_overrides_patch": None,
        "intent": "Signal-domain noise filter (current production).",
    },
    {
        "id": "vt_S_18",
        "label": "+vt_S_18",
        "buy_grace_days": 3,
        "vol_target_per_regime": {"SIDE": _vt_block(0.18)},
        "strategy_patch": None,
        "regime_overrides_patch": None,
        "intent": "Portfolio-domain SIDE-only deleveraging (target 18% ann vol).",
    },
    {
        "id": "slow_deploy",
        "label": "+slow_deploy",
        "buy_grace_days": 3,
        "vol_target_per_regime": None,
        "strategy_patch": {"adaptive_deploy_rate": 0.05},
        "regime_overrides_patch": {
            "BULL": {"adaptive_deploy_rate": 0.10},
            "SIDE": {"adaptive_deploy_rate": 0.05},
            "DEF":  {"adaptive_deploy_rate": 0.05},
        },
        "intent": "Halve deploy rate across all regimes — slower position build-up.",
    },
    {
        "id": "strict_trim",
        "label": "+strict_trim",
        "buy_grace_days": 3,
        "vol_target_per_regime": None,
        "strategy_patch": {"trim_threshold": 0.015},
        "regime_overrides_patch": None,
        "intent": "Trim more aggressively (0.03 → 0.015) — over-trade dimension.",
    },
]


# ── Window presets ───────────────────────────────────────────────────
WINDOWS = {
    "full":     ("2012-01-03", PACK_END_STR),
    "post_oos": ("2024-06-01", PACK_END_STR),
    "f2":       ("2021-01-01", "2022-12-31"),
}


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
    yrs = max((datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days / 365.25, 1e-6)
    return (last / first) ** (1.0 / yrs) - 1.0


def _stats(xs: List[float]) -> Dict[str, float]:
    arr = np.asarray([x for x in xs if not (isinstance(x, float) and np.isnan(x))], dtype=np.float64)
    if arr.size == 0:
        return {"n": 0, "mean": float("nan"), "std": float("nan"),
                "min": float("nan"), "max": float("nan"), "range_pp": float("nan"),
                "cv": float("nan")}
    mean = float(arr.mean()); std = float(arr.std(ddof=0))
    cv = float(std / abs(mean)) if abs(mean) > 1e-9 else float("nan")
    return {"n": int(arr.size), "mean": mean, "std": std,
            "min": float(arr.min()), "max": float(arr.max()),
            "range_pp": float(arr.max() - arr.min()) * 100.0,
            "cv": cv}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="full", choices=list(WINDOWS.keys()),
                    help="Evaluation window (default: full 14yr).")
    ap.add_argument("--signals", default=None,
                    help="Comma-separated signal ids (default: all).")
    ap.add_argument("--policies", default=None,
                    help="Comma-separated policy ids (default: all).")
    args = ap.parse_args()

    sig_filter = set(args.signals.split(",")) if args.signals else None
    pol_filter = set(args.policies.split(",")) if args.policies else None
    sigs = [s for s in SIGNALS if (sig_filter is None or s["id"] in sig_filter)]
    pols = [p for p in POLICIES if (pol_filter is None or p["id"] in pol_filter)]

    win_start, win_end = WINDOWS[args.window]
    n_cells = len(sigs) * len(pols)

    print("=" * 78)
    print(f"  P6 — Execution-Policy Sensitivity Matrix")
    print(f"  window: {args.window}  ({win_start} → {win_end})")
    print(f"  signals: {len(sigs)}    policies: {len(pols)}    cells: {n_cells}")
    print("=" * 78)

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
    print(f"  pack: {os.path.basename(pack_path)}    SPY CAGR (window) = "
          f"{spy_cagr_window*100:+.2f}%" if not np.isnan(spy_cagr_window) else
          f"  pack: {os.path.basename(pack_path)}    SPY CAGR (window) = n/a")

    cells: List[Dict[str, Any]] = []
    t_total = time.time()
    cell_i = 0
    for s in sigs:
        for p in pols:
            cell_i += 1
            print()
            print("-" * 60)
            print(f"  [{cell_i}/{n_cells}]  signal={s['label']:<24s}  policy={p['label']}")
            print("-" * 60)
            t0 = time.time()
            res = _run_sim(
                arm_name=f"{s['label']}__{p['label']}",
                signal_path=s["path"],
                cfg=cfg, pack=pack,
                vix_close_map=vc, vix_regime_map=vr, vix_smooth_map=vs,
                trigger_conf=trigger_conf,
                oos_start=win_start, oos_end=win_end,
                buy_grace_days=int(p.get("buy_grace_days", 0)),
                vol_target_per_regime=p.get("vol_target_per_regime"),
                strategy_patch=p.get("strategy_patch"),
                regime_overrides_patch=p.get("regime_overrides_patch"),
            )
            elapsed = time.time() - t0
            m = res["metrics"]
            cells.append({
                "signal_id": s["id"], "signal_label": s["label"],
                "policy_id": p["id"], "policy_label": p["label"],
                "elapsed_sec": round(elapsed, 1),
                "CAGR":          float(m["CAGR"]),
                "Sharpe":        float(m["Net_Sharpe"]),
                "MDD":           float(m["Max_Drawdown"]),
                "Calmar":        float(m["Calmar_Ratio"]),
                "Comm_pct":      float(m.get("Commission_Pct_of_Capital", 0.0)),
                "vt_engaged":    int(m.get("vol_target_rebal_days_engaged", 0)),
                "vt_warmed":     int(m.get("vol_target_rebal_days_warmed", 0)),
                "vt_total":      int(m.get("vol_target_rebal_days_total", 0)),
            })
    total_elapsed = time.time() - t_total

    # Index by signal/policy for matrix views
    by_sig: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for c in cells:
        by_sig.setdefault(c["signal_id"], {})[c["policy_id"]] = c

    # ── CAGR matrix ──
    print()
    print("=" * 100)
    print("  CAGR matrix (rows=signal, cols=policy)")
    print("=" * 100)
    hdr = f"  {'signal':<22s}"
    for p in pols:
        hdr += f"  {p['label']:>14s}"
    hdr += f"  {'mean':>9s}  {'std':>8s}  {'range':>9s}"
    print(hdr)
    print("-" * 100)
    sig_stats: Dict[str, Dict[str, float]] = {}
    for s in sigs:
        row_cagrs = []
        line = f"  {s['label']:<22s}"
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            if v is None:
                line += f"  {'n/a':>14s}"; continue
            line += f"  {v['CAGR']*100:>+13.2f}%"
            row_cagrs.append(v["CAGR"])
        st = _stats(row_cagrs)
        sig_stats[s["id"]] = st
        line += (f"  {st['mean']*100:>+8.2f}%  {st['std']*100:>+7.2f}%  "
                 f"{st['range_pp']:>7.2f}pp")
        print(line)
    print("-" * 100)
    pol_stats: Dict[str, Dict[str, float]] = {}
    foot = f"  {'policy mean':<22s}"
    for p in pols:
        col = [by_sig[s["id"]][p["id"]]["CAGR"] for s in sigs
               if by_sig.get(s["id"], {}).get(p["id"])]
        st = _stats(col); pol_stats[p["id"]] = st
        foot += f"  {st['mean']*100:>+13.2f}%"
    foot += "  " + " " * 9 + "  " + " " * 8 + "  " + " " * 9
    print(foot)
    foot2 = f"  {'policy std (sigs)':<22s}"
    for p in pols:
        st = pol_stats[p["id"]]
        foot2 += f"  {st['std']*100:>+13.2f}%"
    print(foot2)
    print("=" * 100)

    # ── Sharpe matrix ──
    print()
    print("=" * 100)
    print("  Sharpe matrix")
    print("=" * 100)
    print(hdr)
    print("-" * 100)
    for s in sigs:
        row_sharpes = []
        line = f"  {s['label']:<22s}"
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            if v is None:
                line += f"  {'n/a':>14s}"; continue
            line += f"  {v['Sharpe']:>14.3f}"
            row_sharpes.append(v["Sharpe"])
        st = _stats(row_sharpes)
        line += f"  {st['mean']:>9.3f}  {st['std']:>8.3f}  {st['range_pp']/100:>7.3f}  "
        print(line)
    print("=" * 100)

    # ── MDD matrix ──
    print()
    print("=" * 100)
    print("  Max Drawdown matrix (lower is better)")
    print("=" * 100)
    print(hdr)
    print("-" * 100)
    for s in sigs:
        row_mdds = []
        line = f"  {s['label']:<22s}"
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            if v is None:
                line += f"  {'n/a':>14s}"; continue
            line += f"  {v['MDD']*100:>13.2f}%"
            row_mdds.append(v["MDD"])
        st = _stats(row_mdds)
        line += f"  {st['mean']*100:>+8.2f}%  {st['std']*100:>+7.2f}%  {st['range_pp']:>7.2f}pp"
        print(line)
    print("=" * 100)

    # ── Robustness summary ──
    print()
    print("=" * 78)
    print("  Robustness summary  (lower std = more execution-policy-agnostic signal)")
    print("=" * 78)
    print(f"  {'signal':<24s}  {'CAGR mean':>9s}  {'CAGR std':>9s}  "
          f"{'CAGR range':>11s}  {'rank':>5s}")
    print("-" * 78)
    sig_ranking = sorted(sigs, key=lambda s: sig_stats[s["id"]]["std"])
    for i, s in enumerate(sig_ranking):
        st = sig_stats[s["id"]]
        print(f"  {s['label']:<24s}  {st['mean']*100:>+8.2f}%  "
              f"{st['std']*100:>+8.2f}%  {st['range_pp']:>9.2f}pp  {i+1:>5d}")
    print("-" * 78)
    print(f"  → smallest CAGR std ⇒ most policy-robust signal.")
    print()
    print("=" * 78)
    print("  Best policy per signal  (which overlay maximises CAGR for each signal)")
    print("=" * 78)
    print(f"  {'signal':<24s}  {'best policy':<14s}  {'best CAGR':>9s}  "
          f"{'vs default':>11s}  {'worst policy':<14s}  {'worst CAGR':>10s}")
    print("-" * 78)
    for s in sigs:
        row = by_sig.get(s["id"], {})
        items = [(p["id"], p["label"], row[p["id"]]["CAGR"]) for p in pols if p["id"] in row]
        if not items:
            continue
        items.sort(key=lambda t: -t[2])
        best = items[0]; worst = items[-1]
        default_cagr = row.get("default", {}).get("CAGR")
        delta = (best[2] - default_cagr) * 100 if default_cagr is not None else float("nan")
        print(f"  {s['label']:<24s}  {best[1]:<14s}  {best[2]*100:>+8.2f}%  "
              f"{delta:>+9.2f}pp  {worst[1]:<14s}  {worst[2]*100:>+9.2f}%")
    print("=" * 78)

    print()
    print(f"  total elapsed: {total_elapsed:.1f}s  ({total_elapsed/60:.1f} min)")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(DOCS_DIR, f"p6_exec_sensitivity_matrix_{stamp}.json")
    md_path = os.path.join(DOCS_DIR, f"p6_exec_sensitivity_matrix_{stamp}.md")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack": os.path.basename(pack_path),
        "window": args.window,
        "window_start": win_start, "window_end": win_end,
        "spy_cagr_window": float(spy_cagr_window) if not np.isnan(spy_cagr_window) else None,
        "signals": sigs,
        "policies": pols,
        "cells": cells,
        "signal_stats": sig_stats,
        "policy_stats": pol_stats,
        "total_elapsed_sec": round(total_elapsed, 1),
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[saved] {json_path}")

    L: List[str] = []
    L.append(f"# P6 — Execution-Policy Sensitivity Matrix")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Window**: `{args.window}` ({win_start} → {win_end})")
    L.append(f"**Pack**: `{os.path.basename(pack_path)}`")
    if not np.isnan(spy_cagr_window):
        L.append(f"**SPY CAGR (window)**: {spy_cagr_window*100:+.2f}%")
    L.append("")
    L.append("## CAGR matrix")
    L.append("")
    head = "| signal \\ policy | " + " | ".join(p["label"] for p in pols) + " | mean | std | range |"
    sep = "|---|" + "|".join(["---:"] * (len(pols) + 3)) + "|"
    L.append(head); L.append(sep)
    for s in sigs:
        row = [s["label"]]
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            row.append(f"{v['CAGR']*100:+.2f}%" if v else "n/a")
        st = sig_stats[s["id"]]
        row += [f"{st['mean']*100:+.2f}%", f"{st['std']*100:.2f}%", f"{st['range_pp']:.2f}pp"]
        L.append("| " + " | ".join(row) + " |")
    L.append("")
    L.append("## Sharpe matrix")
    L.append("")
    L.append(head); L.append(sep)
    for s in sigs:
        row = [s["label"]]
        sharpes = []
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            row.append(f"{v['Sharpe']:.3f}" if v else "n/a")
            if v: sharpes.append(v["Sharpe"])
        st = _stats(sharpes)
        row += [f"{st['mean']:.3f}", f"{st['std']:.3f}", f"{st['range_pp']/100:.3f}"]
        L.append("| " + " | ".join(row) + " |")
    L.append("")
    L.append("## MDD matrix (lower better)")
    L.append("")
    L.append(head); L.append(sep)
    for s in sigs:
        row = [s["label"]]
        mdds = []
        for p in pols:
            v = by_sig.get(s["id"], {}).get(p["id"])
            row.append(f"{v['MDD']*100:.2f}%" if v else "n/a")
            if v: mdds.append(v["MDD"])
        st = _stats(mdds)
        row += [f"{st['mean']*100:.2f}%", f"{st['std']*100:.2f}%", f"{st['range_pp']:.2f}pp"]
        L.append("| " + " | ".join(row) + " |")
    L.append("")
    L.append("## Robustness ranking (CAGR std across policies)")
    L.append("")
    L.append("| rank | signal | mean CAGR | std CAGR | range CAGR |")
    L.append("|---:|---|---:|---:|---:|")
    for i, s in enumerate(sig_ranking):
        st = sig_stats[s["id"]]
        L.append(f"| {i+1} | {s['label']} | {st['mean']*100:+.2f}% | "
                 f"{st['std']*100:.2f}% | {st['range_pp']:.2f}pp |")
    L.append("")
    L.append("## Best policy per signal")
    L.append("")
    L.append("| signal | best policy | best CAGR | Δ vs default | worst policy | worst CAGR |")
    L.append("|---|---|---:|---:|---|---:|")
    for s in sigs:
        row = by_sig.get(s["id"], {})
        items = [(p["id"], p["label"], row[p["id"]]["CAGR"]) for p in pols if p["id"] in row]
        if not items:
            continue
        items.sort(key=lambda t: -t[2])
        best = items[0]; worst = items[-1]
        default_cagr = row.get("default", {}).get("CAGR")
        delta = (best[2] - default_cagr) * 100 if default_cagr is not None else float("nan")
        delta_s = f"{delta:+.2f}pp" if not np.isnan(delta) else "n/a"
        L.append(f"| {s['label']} | {best[1]} | {best[2]*100:+.2f}% | {delta_s} | "
                 f"{worst[1]} | {worst[2]*100:+.2f}% |")
    L.append("")
    L.append("## Policies")
    L.append("")
    for p in pols:
        L.append(f"- **{p['label']}** (`{p['id']}`) — {p['intent']}")
    with open(md_path, "w") as f:
        f.write("\n".join(L))
    print(f"[saved] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
