"""Step D — Walk-Forward Evaluation (T5 Phase A).

Evaluates 4 frozen signals (baseline + 3 P5_RETRAIN variants) across
**6 temporal folds** on the 14-year pack (2011-01-03 → 2026-02-27):

    F0a  2012-01-01 → 2014-12-31   pre-train OOS
    F0b  2015-01-01 → 2016-12-31   pre-train OOS
    F1   2019-01-01 → 2020-12-31   in-sample
    F2   2021-01-01 → 2022-12-31   in-sample
    F3   2023-01-01 → 2024-05-31   in-sample
    F4   2024-06-01 → pack end     post-train OOS  (~= Step C window)

Each sim starts with a fresh $100K portfolio ($1K daily buy limit,
10/5 bps commission/slippage, SIDE_DEF_p12 trigger stack, regime
blend OFF) — identical to Step C. 4 signals × 6 folds = 24 sims.

Aggregate metrics (per signal):
  - mean / std / CV CAGR (6 folds)
  - worst-fold CAGR
  - range CAGR
  - pre-train OOS mean (F0a/F0b) vs post-train OOS (F4) vs in-sample (F1-F3)

Gates (vs baseline V2 as reference):
  - G6-A : CV ≤ 0.5                                  (absolute stability)
  - G6-B : CV(cand) ≤ CV(baseline)                   (relative stability)   ← primary
  - G6-C : worst-fold(cand) ≥ worst-fold(baseline)   (tail-risk defense)
  - G6-D : all fold CAGR > 0                         (basic robustness)

See `phase3/docs/t5_walk_forward_plan.md` v2 for full specification.

Usage
-----
    python3 -u phase3/tests/step_d_walk_forward.py
    python3 -u phase3/tests/step_d_walk_forward.py --signals baseline,t1b
    python3 -u phase3/tests/step_d_walk_forward.py --folds F0a,F0b,F4
"""
from __future__ import annotations

# macOS: suppress fork-safety popup if called from Tk launcher
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
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg,
    _load_vix,
    _run_sim,
    _realized_oos_ic,
)


# ── Fixed pack (same as rebuild_pack_walk_forward.py) ────────────────
PACK_START_STR = "2011-01-03"
PACK_END_STR   = "2026-02-27"

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"


# ── 6-fold design ────────────────────────────────────────────────────
FOLDS: List[Dict[str, str]] = [
    {"id": "F0a", "start": "2012-01-01", "end": "2014-12-31", "group": "pre_oos"},
    {"id": "F0b", "start": "2015-01-01", "end": "2016-12-31", "group": "pre_oos"},
    {"id": "F1",  "start": "2019-01-01", "end": "2020-12-31", "group": "in_sample"},
    {"id": "F2",  "start": "2021-01-01", "end": "2022-12-31", "group": "in_sample"},
    {"id": "F3",  "start": "2023-01-01", "end": "2024-05-31", "group": "in_sample"},
    {"id": "F4",  "start": "2024-06-01", "end": PACK_END_STR, "group": "post_oos"},
]

# ── Signal set (4 arms) ──────────────────────────────────────────────
SIGNALS: List[Dict[str, str]] = [
    {"id": "baseline", "arm": "Baseline_V2",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"},
    {"id": "p5",       "arm": "P5_RETRAIN",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_20260423_153457.npz"},
    {"id": "t1",       "arm": "P5_RETRAIN_T1",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1_20260423_183119.npz"},
    {"id": "t1b",      "arm": "P5_RETRAIN_T1b", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz"},
    # P1 Option A — surgical BULL injection: T1b's ws/wd + Baseline's wb (mask = union).
    # Built by phase3/tests/p1_bull_injection.py on 2026-04-23.
    {"id": "t1b_inj",  "arm": "T1b_BULL_INJECTED", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_20260423_225842.npz"},
    # Phase B — Batch 1 scalar sweep (window 2012-01-03 → 2024-05-31, seed 20260428).
    # See phase3/docs/phase_b_batch_plan.md.
    {"id": "p5b_consv","arm": "P5B_CONSV",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5B_CONSV_20260424_020254.npz"},
    {"id": "p5b_prop", "arm": "P5B_PROP",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5B_PROP_20260424_041213.npz"},
    {"id": "p5b_aggr", "arm": "P5B_AGGR",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5B_AGGR_20260424_061621.npz"},
]


def _pick_walk_forward_pack(save_dir: str) -> Tuple[str, str, str]:
    """Return the 2011-start pack (built by rebuild_pack_walk_forward.py)."""
    pattern = os.path.join(
        save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz"
    )
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(
            f"No walk-forward pack found matching {pattern}\n"
            f"Run: python3 -u phase3/tests/rebuild_pack_walk_forward.py"
        )
    p = candidates[0]
    stem = os.path.splitext(os.path.basename(p))[0]
    parts = stem.split("_")
    start, end = parts[-2], parts[-1]
    return p, start, end


def _regime_distribution(vix_regime_map: Dict[str, str],
                         start: str, end: str) -> Dict[str, int]:
    counts = {"BULL": 0, "SIDE": 0, "DEF": 0, "DEFENSIVE": 0}
    for d, r in vix_regime_map.items():
        if start <= d <= end:
            key = str(r).upper()
            counts[key] = counts.get(key, 0) + 1
    # Normalise DEF/DEFENSIVE
    counts["DEF"] = counts.get("DEF", 0) + counts.pop("DEFENSIVE", 0)
    return {k: v for k, v in counts.items() if v >= 0}


def _aggregate(folds_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute mean/std/CV CAGR and group summaries."""
    by_group: Dict[str, List[float]] = {"pre_oos": [], "in_sample": [], "post_oos": []}
    all_cagr: List[float] = []
    for r in folds_results:
        c = float(r["metrics"]["CAGR"])
        all_cagr.append(c)
        by_group.setdefault(r["group"], []).append(c)

    def _stats(xs: List[float]) -> Dict[str, float]:
        if not xs:
            return {"n": 0, "mean": float("nan"), "std": float("nan"), "cv": float("nan"),
                    "min": float("nan"), "max": float("nan"), "pos_count": 0}
        arr = np.asarray(xs, dtype=np.float64)
        mean = float(arr.mean())
        std = float(arr.std(ddof=0))
        cv = float(std / abs(mean)) if abs(mean) > 1e-9 else float("nan")
        return {
            "n": int(arr.size),
            "mean": mean,
            "std": std,
            "cv": cv,
            "min": float(arr.min()),
            "max": float(arr.max()),
            "pos_count": int((arr > 0).sum()),
        }

    return {
        "all":       _stats(all_cagr),
        "pre_oos":   _stats(by_group.get("pre_oos", [])),
        "in_sample": _stats(by_group.get("in_sample", [])),
        "post_oos":  _stats(by_group.get("post_oos", [])),
    }


def _compute_gates(cand_agg: Dict[str, Any], base_agg: Dict[str, Any]) -> Dict[str, Any]:
    """G6-A / B / C / D evaluation."""
    c_all = cand_agg["all"]
    b_all = base_agg["all"]

    cv_cand = c_all["cv"]
    cv_base = b_all["cv"]

    g6a = bool(np.isfinite(cv_cand) and cv_cand <= 0.5)
    g6b = bool(
        np.isfinite(cv_cand) and np.isfinite(cv_base) and cv_cand <= cv_base + 1e-9
    )
    g6c = bool(c_all["min"] >= b_all["min"] - 1e-9)
    g6d = bool(c_all["pos_count"] == c_all["n"] and c_all["n"] > 0)

    return {
        "G6_A_absolute_cv_le_0p5":  {"cv": cv_cand, "threshold": 0.5,    "pass": g6a},
        "G6_B_relative_cv_le_base": {"cv_cand": cv_cand, "cv_base": cv_base, "pass": g6b},
        "G6_C_worst_ge_base":       {"worst_cand": c_all["min"], "worst_base": b_all["min"], "pass": g6c},
        "G6_D_all_positive":        {"pos_count": c_all["pos_count"], "n": c_all["n"], "pass": g6d},
    }


def _run_signal_over_folds(
    sig_cfg: Dict[str, str],
    folds: List[Dict[str, str]],
    cfg, pack,
    vix_c, vix_r, vix_s,
    trigger_conf,
) -> Dict[str, Any]:
    print()
    print("#" * 72)
    print(f"##  SIGNAL: {sig_cfg['arm']}  ({sig_cfg['id']})")
    print(f"##  path  : {os.path.basename(sig_cfg['path'])}")
    print("#" * 72)

    per_fold: List[Dict[str, Any]] = []
    for fold in folds:
        start, end = fold["start"], fold["end"]
        print()
        print("-" * 60)
        print(f"  FOLD {fold['id']}  ({start} → {end})  [{fold['group']}]")
        print("-" * 60)
        try:
            sim = _run_sim(
                arm_name=f"{sig_cfg['arm']}__{fold['id']}",
                signal_path=sig_cfg["path"],
                cfg=cfg, pack=pack,
                vix_close_map=vix_c, vix_regime_map=vix_r, vix_smooth_map=vix_s,
                trigger_conf=trigger_conf,
                oos_start=start, oos_end=end,
            )
        except Exception as exc:
            print(f"  [ERROR] fold {fold['id']} failed: {exc}")
            per_fold.append({
                "fold": fold["id"], "group": fold["group"],
                "window_start": start, "window_end": end,
                "error": str(exc),
                "metrics": {"CAGR": float("nan"), "Max_Drawdown": float("nan"),
                            "Calmar_Ratio": float("nan"), "Net_Sharpe": float("nan"),
                            "Commission_Pct_of_Capital": float("nan")},
                "oos_ic": {"oos_mean_ic_1m": float("nan"), "oos_mean_ic_3m": float("nan"),
                           "oos_mean_spread_1m": float("nan"), "oos_mean_spread_3m": float("nan")},
            })
            continue

        sim_row = {
            "fold": fold["id"], "group": fold["group"],
            "window_start": start, "window_end": end,
            "regime_dist": _regime_distribution(vix_r, start, end),
            "metrics": sim["metrics"],
            "oos_ic":  sim["oos_ic"],
            "elapsed_sec": sim.get("elapsed_sec", 0.0),
        }
        per_fold.append(sim_row)

    agg = _aggregate(per_fold)
    return {
        "signal_id": sig_cfg["id"],
        "arm": sig_cfg["arm"],
        "path": sig_cfg["path"],
        "folds": per_fold,
        "aggregate": agg,
    }


def _write_markdown(report: Dict[str, Any], md_path: str) -> None:
    lines: List[str] = []
    lines.append(f"# T5 Walk-Forward Results")
    lines.append(f"")
    lines.append(f"**Generated**: {report['meta']['generated_at']}")
    lines.append(f"**Pack**: `{report['meta']['pack_basename']}`")
    lines.append(f"**Folds**: {len(report['meta']['folds'])}  |  **Signals**: {len(report['meta']['signals'])}")
    lines.append(f"**Total sims**: {len(report['meta']['folds']) * len(report['meta']['signals'])}")
    lines.append(f"")

    # Per-signal per-fold CAGR table
    lines.append("## 1. Per-fold CAGR (%)")
    lines.append("")
    header = "| Signal | " + " | ".join(f"{f['id']}<br/>({f['group']})" for f in report["meta"]["folds"]) + " | mean | CV |"
    sep    = "|" + "---|" * (2 + len(report["meta"]["folds"]) + 1) + "---|"
    lines.append(header)
    lines.append(sep)
    for sig in report["per_signal"]:
        cagrs = []
        for fold in report["meta"]["folds"]:
            match = next((f for f in sig["folds"] if f["fold"] == fold["id"]), None)
            v = match["metrics"]["CAGR"] if match and np.isfinite(match["metrics"]["CAGR"]) else float("nan")
            cagrs.append(f"{v*100:+.2f}" if np.isfinite(v) else "n/a")
        a = sig["aggregate"]["all"]
        mean = f"{a['mean']*100:+.2f}" if np.isfinite(a["mean"]) else "n/a"
        cv = f"{a['cv']:.3f}" if np.isfinite(a["cv"]) else "n/a"
        row = f"| **{sig['arm']}** | " + " | ".join(cagrs) + f" | {mean} | {cv} |"
        lines.append(row)
    lines.append("")

    # Aggregate by group
    lines.append("## 2. CAGR aggregate by fold group (%)")
    lines.append("")
    lines.append("| Signal | All (mean / std / CV) | Pre-OOS (F0a,F0b) | In-sample (F1-F3) | Post-OOS (F4) | Worst fold | Pos / n |")
    lines.append("|---|---|---|---|---|---|---|")
    for sig in report["per_signal"]:
        a = sig["aggregate"]
        def _fmt(s: Dict[str, Any]) -> str:
            if s["n"] == 0:
                return "—"
            return f"{s['mean']*100:+.2f} / {s['std']*100:.2f} / {s['cv']:.2f}"
        worst = f"{a['all']['min']*100:+.2f}" if np.isfinite(a["all"]["min"]) else "n/a"
        posn = f"{a['all']['pos_count']}/{a['all']['n']}"
        lines.append(
            f"| **{sig['arm']}** | {_fmt(a['all'])} | {_fmt(a['pre_oos'])} | "
            f"{_fmt(a['in_sample'])} | {_fmt(a['post_oos'])} | {worst} | {posn} |"
        )
    lines.append("")

    # Gate verdicts (vs baseline)
    lines.append("## 3. Gate verdicts (vs baseline V2)")
    lines.append("")
    lines.append("| Signal | G6-A (CV≤0.5) | G6-B (CV≤base) | G6-C (worst≥base) | G6-D (all>0) |")
    lines.append("|---|:---:|:---:|:---:|:---:|")
    for sig in report["per_signal"]:
        g = sig.get("gates")
        if g is None:
            continue
        def _p(d):
            return "✓" if d["pass"] else "✗"
        lines.append(
            f"| **{sig['arm']}** | {_p(g['G6_A_absolute_cv_le_0p5'])} "
            f"| {_p(g['G6_B_relative_cv_le_base'])} "
            f"| {_p(g['G6_C_worst_ge_base'])} "
            f"| {_p(g['G6_D_all_positive'])} |"
        )
    lines.append("")

    # Per-fold full metrics (collapsible detail)
    lines.append("## 4. Per-fold detail")
    lines.append("")
    for sig in report["per_signal"]:
        lines.append(f"### {sig['arm']}")
        lines.append("")
        lines.append("| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for fr in sig["folds"]:
            m = fr["metrics"]; ic = fr["oos_ic"]
            reg = fr.get("regime_dist", {})
            reg_str = f"{reg.get('BULL',0)}/{reg.get('SIDE',0)}/{reg.get('DEF',0)}"
            lines.append(
                f"| {fr['fold']} | {fr['group']} | {fr['window_start']}→{fr['window_end']} "
                f"| {m.get('CAGR', float('nan'))*100:+.2f}% "
                f"| {m.get('Max_Drawdown', float('nan'))*100:.2f}% "
                f"| {m.get('Net_Sharpe', float('nan')):.2f} "
                f"| {m.get('Calmar_Ratio', float('nan')):.2f} "
                f"| {m.get('Commission_Pct_of_Capital', float('nan')):.2f}% "
                f"| {ic.get('oos_mean_ic_3m', float('nan')):+.4f} "
                f"| {reg_str} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**Interpretation notes**")
    lines.append("")
    lines.append("- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).")
    lines.append("  CAGR absolute values may be survivorship-biased (delisted names absent from cache);")
    lines.append("  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.")
    lines.append("- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.")
    lines.append("- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="T5 Walk-Forward — 6-fold evaluation")
    parser.add_argument(
        "--signals", default="all",
        help="Comma-separated signal ids (baseline,p5,t1,t1b) or 'all' (default).",
    )
    parser.add_argument(
        "--folds", default="all",
        help="Comma-separated fold ids (F0a,F0b,F1,F2,F3,F4) or 'all' (default).",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("  Step D — T5 Walk-Forward (Phase A, 6 folds)")
    print("=" * 72)

    # ── Filter signals / folds per CLI ─────────────────────────────
    if args.signals == "all":
        signals = list(SIGNALS)
    else:
        wanted = {s.strip() for s in args.signals.split(",")}
        signals = [s for s in SIGNALS if s["id"] in wanted]
    if not signals:
        print(f"[ERROR] no signals matched {args.signals}")
        return 1

    if args.folds == "all":
        folds = list(FOLDS)
    else:
        wanted = {s.strip() for s in args.folds.split(",")}
        folds = [f for f in FOLDS if f["id"] in wanted]
    if not folds:
        print(f"[ERROR] no folds matched {args.folds}")
        return 1

    print(f"  signals : {[s['arm'] for s in signals]}")
    print(f"  folds   : {[f['id']  for f in folds]}")
    print(f"  total   : {len(signals) * len(folds)} sims")
    print("=" * 72)

    # ── Load config + pack ─────────────────────────────────────────
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

    # ── Sanity: verify signal files exist ─────────────────────────
    missing: List[str] = []
    for s in signals:
        if not os.path.exists(s["path"]):
            missing.append(s["path"])
    if missing:
        print("[ERROR] missing signal files:")
        for p in missing:
            print(f"  - {p}")
        return 2

    # ── VIX regime map over the entire pack window ─────────────────
    print("[VIX ] building regime timeseries over full pack range…")
    vix_c, vix_r, vix_s = _load_vix(cfg, pack_start, pack_end)
    print(f"[VIX ] {len(vix_c)} dates")
    trigger_conf = conf.get("triggers", {})

    # ── Run 24 sims ────────────────────────────────────────────────
    t0 = time.time()
    per_signal: List[Dict[str, Any]] = []
    for sig_cfg in signals:
        result = _run_signal_over_folds(
            sig_cfg, folds,
            cfg=cfg, pack=pack,
            vix_c=vix_c, vix_r=vix_r, vix_s=vix_s,
            trigger_conf=trigger_conf,
        )
        per_signal.append(result)
    total_elapsed = time.time() - t0

    # ── Compute gates (vs baseline) ────────────────────────────────
    baseline = next((s for s in per_signal if s["signal_id"] == "baseline"), None)
    if baseline is not None:
        for sig in per_signal:
            sig["gates"] = _compute_gates(sig["aggregate"], baseline["aggregate"])

    # ── Console summary ───────────────────────────────────────────
    print()
    print("=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"{'Signal':<22s} {'n':>3s} {'mean_CAGR':>10s} {'std':>8s} {'CV':>7s} "
          f"{'worst':>8s} {'pos/n':>6s}  gate_B  gate_C  gate_D")
    print("-" * 72)
    for sig in per_signal:
        a = sig["aggregate"]["all"]
        g = sig.get("gates") or {}
        def _p(k):
            if k not in g:
                return " —  "
            return " YES " if g[k]["pass"] else " NO  "
        print(f"{sig['arm']:<22s} {a['n']:>3d} "
              f"{a['mean']*100:>+9.2f}% "
              f"{a['std']*100:>+7.2f}% "
              f"{a['cv']:>7.3f} "
              f"{a['min']*100:>+7.2f}% "
              f"{a['pos_count']:>3d}/{a['n']:<2d}"
              f"{_p('G6_B_relative_cv_le_base')}"
              f"{_p('G6_C_worst_ge_base')}"
              f"{_p('G6_D_all_positive')}")
    print("=" * 72)
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print("=" * 72)

    # ── Persist ───────────────────────────────────────────────────
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(DOCS_DIR, f"t5_walk_forward_results_{stamp}.json")
    md_path   = os.path.join(DOCS_DIR, f"t5_walk_forward_results_{stamp}.md")

    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pack_path": pack_path,
            "pack_basename": os.path.basename(pack_path),
            "pack_start": pack_start, "pack_end": pack_end,
            "total_elapsed_sec": round(total_elapsed, 1),
            "signals": [{"id": s["id"], "arm": s["arm"], "path": s["path"]} for s in signals],
            "folds": [{"id": f["id"], "start": f["start"], "end": f["end"], "group": f["group"]} for f in folds],
            "protocol": {
                "initial_capital": 100000.0,
                "daily_buy_limit": 1000.0,
                "commission_bps": 10.0, "slippage_bps": 5.0,
                "rebalance_mode": "daily",
                "strategy_stack": "SIDE_DEF_p12",
                "regime_blend": False,
            },
        },
        "per_signal": per_signal,
    }
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=float)
    print(f"[saved] {json_path}")

    _write_markdown(report, md_path)
    print(f"[saved] {md_path}")

    # Exit code: 0 if baseline-relative G6-B passed by any candidate, 2 otherwise
    any_pass = False
    for sig in per_signal:
        if sig["signal_id"] == "baseline":
            continue
        g = sig.get("gates") or {}
        if g.get("G6_B_relative_cv_le_base", {}).get("pass"):
            any_pass = True
            break
    return 0 if any_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
