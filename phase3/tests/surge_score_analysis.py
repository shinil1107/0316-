"""Phase B — 주제 #5: 급등 ticker × 사전 score 상관성 분석.

Answers: "When a ticker surges +X% in N days, what was its signal score
*before* the surge began?"

Analysis dimensions:
  1. Surge-event detection: forward 5d/10d/21d return >= threshold.
  2. Pre-event score percentile: at t-1, t-3, t-7 before the surge window.
  3. Top-decile precision: P(surge | score in top 10%) vs random baseline.
  4. False-positive profile: top-decile tickers that dropped hard.
  5. Per-signal comparison across Baseline_V2 and quasi-baselines.

Usage:
    python3 -u phase3/tests/surge_score_analysis.py
    python3 -u phase3/tests/surge_score_analysis.py --signals baseline,b7_full
    python3 -u phase3/tests/surge_score_analysis.py --surge-pct 30 --horizons 5,10
    python3 -u phase3/tests/surge_score_analysis.py --start 2020-01-01 --end 2024-12-31
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
from collections import defaultdict
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

# ── Constants ─────────────────────────────────────────────────────────
PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
CONFIG_PATH = os.path.join(PHASE3_DIR, "config.yaml")
DOCS_DIR = os.path.join(PHASE3_DIR, "docs")

SIGNALS: List[Dict[str, str]] = [
    {"id": "baseline", "arm": "Baseline_V2",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"},
    {"id": "b7_full",  "arm": "P7_V2_FULL",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_FULL_20260429_190918.npz"},
    {"id": "b7_nodep", "arm": "P7_V2_NO_DEPLOY",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_NO_DEPLOY_20260501_014909.npz"},
    {"id": "b7_mega",  "arm": "P7_V2_MEGA",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_MEGA_20260502_165121.npz"},
    {"id": "p7_j",     "arm": "P7_STITCH_J",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_J_20260503_164726.npz"},
    {"id": "p7_k",     "arm": "P7_STITCH_K",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_K_20260503_164726.npz"},
    {"id": "p7_l",     "arm": "P7_BLEND_L",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_BLEND_L_20260503_164726.npz"},
    {"id": "p7_n",     "arm": "P7_STITCH_N",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_N_20260503_164726.npz"},
    {"id": "p7_l3_h",  "arm": "P7_L3_ENSEMBLE_H", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_L3_ENSEMBLE_H_20260503_163436.npz"},
    {"id": "b8_side",  "arm": "P8_SIDE_V3",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_SIDE_V3_20260504_174158.npz"},
    {"id": "b8_bull",  "arm": "P8_BULL_DENSE",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BULL_DENSE_20260506_020833.npz"},
    {"id": "p9_spec_a","arm": "P9_TRIPLE_SPEC_A", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P9_TRIPLE_SPEC_A_20260506_030809.npz"},
    # ── OOS-clean candidates (train_end ≤ 2024-05-31) — added 2026-05-08 ──
    {"id": "p2_oos",          "arm": "P2_BATCH11_OOS",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P2_BATCH11_OOS_20260508_112505.npz"},
    {"id": "p5e_side_fundb",  "arm": "P5E_SIDE_FUND_BREAKOUT", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5E_SIDE_FUND_BREAKOUT_20260426_152005.npz"},
    {"id": "p5d_side_deep",   "arm": "P5D_SIDE_DEEP",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5D_SIDE_DEEP_20260426_024004.npz"},
    {"id": "p5e_side_tech",   "arm": "P5E_SIDE_TECH",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5E_SIDE_TECH_20260426_124200.npz"},
    {"id": "p5_t1b",          "arm": "P5_RETRAIN_T1b",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz"},
    {"id": "p5c_balanced",    "arm": "P5C_BALANCED",           "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_BALANCED_20260424_151512.npz"},
    {"id": "p5c_def_heavy",   "arm": "P5C_DEF_HEAVY",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_DEF_HEAVY_20260424_212616.npz"},
    # ── P11 L3 ensembles (OOS-clean) — added 2026-05-09 ──
    {"id": "ens_u",           "arm": "P11_OOS_CLEAN_L3_EQ",            "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_EQ_20260508_180824.npz"},
    {"id": "ens_v",           "arm": "P11_OOS_CLEAN_L3_FUNDB_ANCHOR",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_FUNDB_ANCHOR_20260508_180824.npz"},
    {"id": "ens_w",           "arm": "P11_OOS_CLEAN_L3_REGIME_SPEC",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_REGIME_SPEC_20260508_180824.npz"},
    # ── P7/P8 OOS-clean retrains — added 2026-05-09 ──
    {"id": "p7_nodep_oos",    "arm": "P7_V2_NO_DEPLOY_OOS",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_NO_DEPLOY_OOS_20260509_152907.npz"},
    {"id": "p8_bull_oos",     "arm": "P8_BULL_DENSE_OOS",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BULL_DENSE_OOS_20260509_164158.npz"},
    # ── P12 L3 ensembles (OOS-clean) — added 2026-05-09 ──
    {"id": "p12_x",           "arm": "P12_BULL_INJ_FUNDB_ANCHOR", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_BULL_INJ_FUNDB_ANCHOR_20260509_165605.npz"},
    {"id": "p12_y",           "arm": "P12_TRIPLE_SPEC_OOS",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_TRIPLE_SPEC_OOS_20260509_165605.npz"},
    {"id": "p12_z",           "arm": "P12_FULL_OOS_L3",           "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_FULL_OOS_L3_20260509_165605.npz"},
]

DEFAULT_SURGE_PCT = 20.0       # +20% forward return threshold
DEFAULT_HORIZONS = [5, 10, 21]  # trading days
DEFAULT_LOOKBACKS = [0, 5, 10]  # pre-event offsets in trading days (0 = same day)
SAMPLE_STEP = 5                 # score every N-th trading day (speed/accuracy trade-off)


# ── Pack loader ───────────────────────────────────────────────────────
def _pick_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(
        save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz"
    )
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(
            f"No walk-forward pack matching {pattern}\n"
            f"Run: python3 -u phase3/tests/rebuild_pack_walk_forward.py"
        )
    p = candidates[0]
    stem = os.path.splitext(os.path.basename(p))[0]
    parts = stem.split("_")
    start, end = parts[-2], parts[-1]
    return p, start, end


def _build_cfg(conf: Dict, pack_start: str, pack_end: str):
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
    cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


def _load_vix(cfg, start: str, end: str):
    vix_df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start, "%Y-%m-%d"),
        datetime.strptime(end, "%Y-%m-%d"),
    )
    c_map, r_map, s_map = {}, {}, {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d = str(row.get("date", row.name))[:10]
            c_map[d] = float(row.get("close", row.get("vix_close", 20)))
            r_map[d] = str(row.get("regime", "SIDE"))
            if "vix_smooth" in row.index:
                s_map[d] = float(row["vix_smooth"])
    return c_map, r_map, s_map


# ── Core analysis ─────────────────────────────────────────────────────
def _compute_forward_returns(close: np.ndarray, horizons: List[int]) -> Dict[int, np.ndarray]:
    """(D, N) close → {horizon: (D, N) forward return array}."""
    D, N = close.shape
    result = {}
    for h in horizons:
        fwd = np.full((D, N), np.nan, dtype=np.float64)
        for di in range(D - h):
            c0 = close[di]
            c1 = close[di + h]
            mask = (c0 > 0) & np.isfinite(c0) & np.isfinite(c1)
            fwd[di, mask] = (c1[mask] / c0[mask]) - 1.0
        result[h] = fwd
    return result


def _score_all_dates(
    pack: dict, signal: dict, cfg,
    vix_regime_map: Dict[str, str],
    dates: np.ndarray,
    start: str, end: str,
    sample_step: int,
) -> Dict[int, np.ndarray]:
    """Score the universe at sampled dates. Returns {di: score_vector(N)}."""
    mask = np.asarray(signal["mask"], dtype=bool)
    wb = np.asarray(signal["wb"], dtype=np.float64)
    ws = np.asarray(signal["ws"], dtype=np.float64)
    wd = np.asarray(signal["wd"], dtype=np.float64)

    tradable = pack.get("tradable")
    scored: Dict[int, np.ndarray] = {}
    n_scored = 0

    for di in range(len(dates)):
        d_str = str(dates[di])[:10]
        if d_str < start or d_str > end:
            continue
        if di % sample_step != 0:
            continue

        rg = vix_regime_map.get(d_str, "SIDE").upper()
        if rg in ("DEF", "DEFENSIVE", "CRASH", "BEAR"):
            score_regime = "DEFENSIVE"
        elif rg == "BULL":
            score_regime = "BULL"
        else:
            score_regime = "SIDE"

        svec = engine.score_vector_for_day(
            pack, di, mask, mask, mask, wb, ws, wd,
            score_regime=score_regime, cfg=cfg,
        )
        svec = np.asarray(svec, dtype=np.float64)
        if tradable is not None:
            svec = np.where(tradable[di], svec, np.nan)
        scored[di] = svec
        n_scored += 1

    return scored


def _analyze_signal(
    sig_cfg: Dict[str, str],
    pack: dict, cfg,
    vix_regime_map: Dict[str, str],
    close: np.ndarray,
    fwd_returns: Dict[int, np.ndarray],
    dates: np.ndarray,
    tickers: np.ndarray,
    start: str, end: str,
    surge_pct: float,
    horizons: List[int],
    lookbacks: List[int],
    sample_step: int,
) -> Dict[str, Any]:
    """Run full surge-score analysis for one signal."""
    arm = sig_cfg["arm"]
    sig_path = sig_cfg["path"]

    if not os.path.exists(sig_path):
        print(f"  [SKIP] {arm}: signal not found at {sig_path}")
        return {"arm": arm, "error": "signal_not_found"}

    print(f"\n{'─'*60}")
    print(f"  Analyzing: {arm}")
    print(f"  Signal:    {os.path.basename(sig_path)}")
    print(f"  Window:    {start} → {end}")
    t0 = time.time()

    signal = load_frozen_signal(sig_path)
    scored = _score_all_dates(
        pack, signal, cfg, vix_regime_map, dates,
        start, end, sample_step,
    )
    print(f"  Scored {len(scored)} dates (step={sample_step})")

    D, N = close.shape
    surge_threshold = surge_pct / 100.0
    drop_threshold = -0.05

    # Per-horizon analysis
    horizon_results: Dict[int, Dict[str, Any]] = {}

    for h in horizons:
        fwd = fwd_returns[h]

        # Containers for accumulation
        total_surge_events = 0
        surge_pre_percentiles: Dict[int, List[float]] = {lb: [] for lb in lookbacks}
        top_decile_surges = 0
        top_decile_total = 0
        top_decile_drops = 0
        random_surge_rate = 0.0
        random_count = 0

        # Per-scored-date analysis
        for di, svec in scored.items():
            if di + h >= D:
                continue

            fwd_ret = fwd[di]
            valid = np.isfinite(svec) & np.isfinite(fwd_ret) & (svec > 0)
            n_valid = int(valid.sum())
            if n_valid < 30:
                continue

            scores_valid = svec[valid]
            fwd_valid = fwd_ret[valid]

            # Percentile ranks of scores (0-100, higher = better)
            score_ranks = np.zeros_like(scores_valid)
            order = np.argsort(scores_valid)
            score_ranks[order] = np.linspace(0, 100, len(order))

            # Surge events at this date
            is_surge = fwd_valid >= surge_threshold
            n_surge = int(is_surge.sum())
            total_surge_events += n_surge

            # Random baseline: what fraction surges overall?
            random_surge_rate += float(is_surge.sum())
            random_count += n_valid

            # Top decile analysis
            top_decile_mask = score_ranks >= 90.0
            n_top = int(top_decile_mask.sum())
            top_decile_total += n_top
            top_decile_surges += int((top_decile_mask & is_surge).sum())
            top_decile_drops += int((top_decile_mask & (fwd_valid <= drop_threshold)).sum())

            # Pre-event score percentile for surge tickers.
            # lb=0 uses the same scored date (di); lb>0 finds the
            # nearest scored date that is >= lb trading days before di.
            surge_indices_in_valid = np.where(is_surge)[0]
            ticker_indices = np.where(valid)[0]

            # Build ordered list of scored dates for fast backward search
            scored_dis = sorted(scored.keys())

            for lb in lookbacks:
                if lb == 0:
                    lb_di = di
                else:
                    target = di - lb
                    # Find largest scored di <= target
                    lb_di = None
                    for sd in reversed(scored_dis):
                        if sd <= target:
                            lb_di = sd
                            break
                    if lb_di is None:
                        continue

                lb_svec = scored[lb_di]
                lb_valid = np.isfinite(lb_svec) & (lb_svec > 0)
                if int(lb_valid.sum()) < 30:
                    continue

                lb_scores_all = lb_svec.copy()
                lb_scores_all[~lb_valid] = np.nan

                lb_vals = lb_scores_all[lb_valid]
                lb_sorted = np.sort(lb_vals)
                n_lb = len(lb_sorted)

                for si in surge_indices_in_valid:
                    ti = ticker_indices[si]
                    if ti < len(lb_scores_all) and np.isfinite(lb_scores_all[ti]):
                        pct = float(np.searchsorted(lb_sorted, lb_scores_all[ti])) / n_lb * 100.0
                        surge_pre_percentiles[lb].append(pct)

        # Aggregate metrics
        overall_surge_rate = (random_surge_rate / random_count * 100.0) if random_count > 0 else 0.0
        top_decile_precision = (top_decile_surges / top_decile_total * 100.0) if top_decile_total > 0 else 0.0
        top_decile_drop_rate = (top_decile_drops / top_decile_total * 100.0) if top_decile_total > 0 else 0.0
        lift = (top_decile_precision / overall_surge_rate) if overall_surge_rate > 0 else float("nan")

        pre_pct_summary = {}
        for lb in lookbacks:
            vals = surge_pre_percentiles[lb]
            if vals:
                arr = np.array(vals)
                pre_pct_summary[f"t-{lb}"] = {
                    "mean": round(float(arr.mean()), 1),
                    "median": round(float(np.median(arr)), 1),
                    "p25": round(float(np.percentile(arr, 25)), 1),
                    "p75": round(float(np.percentile(arr, 75)), 1),
                    "n": len(vals),
                    "pct_above_50": round(float((arr >= 50).sum() / len(arr) * 100), 1),
                    "pct_above_80": round(float((arr >= 80).sum() / len(arr) * 100), 1),
                }
            else:
                pre_pct_summary[f"t-{lb}"] = {"mean": None, "n": 0}

        # Quintile breakdown: surge rate per score quintile
        quintile_surge_rates = []
        quintile_labels = ["Q1 (bottom)", "Q2", "Q3", "Q4", "Q5 (top)"]
        quintile_counts = [0] * 5
        quintile_surges = [0] * 5
        quintile_drops = [0] * 5

        for di, svec in scored.items():
            if di + h >= D:
                continue
            fwd_ret = fwd[di]
            valid = np.isfinite(svec) & np.isfinite(fwd_ret) & (svec > 0)
            if int(valid.sum()) < 30:
                continue

            scores_v = svec[valid]
            fwd_v = fwd_ret[valid]
            order = np.argsort(scores_v)
            n_v = len(order)
            q_size = n_v // 5

            for qi in range(5):
                lo = qi * q_size
                hi = (qi + 1) * q_size if qi < 4 else n_v
                q_idx = order[lo:hi]
                quintile_counts[qi] += len(q_idx)
                quintile_surges[qi] += int((fwd_v[q_idx] >= surge_threshold).sum())
                quintile_drops[qi] += int((fwd_v[q_idx] <= drop_threshold).sum())

        for qi in range(5):
            rate = (quintile_surges[qi] / quintile_counts[qi] * 100.0) if quintile_counts[qi] > 0 else 0.0
            drop_rate = (quintile_drops[qi] / quintile_counts[qi] * 100.0) if quintile_counts[qi] > 0 else 0.0
            quintile_surge_rates.append({
                "quintile": quintile_labels[qi],
                "count": quintile_counts[qi],
                "surges": quintile_surges[qi],
                "surge_rate_pct": round(rate, 2),
                "drop_rate_pct": round(drop_rate, 2),
            })

        horizon_results[h] = {
            "horizon_days": h,
            "surge_threshold_pct": surge_pct,
            "total_surge_events": total_surge_events,
            "scored_dates": len(scored),
            "overall_surge_rate_pct": round(overall_surge_rate, 2),
            "top_decile_precision_pct": round(top_decile_precision, 2),
            "top_decile_drop_rate_pct": round(top_decile_drop_rate, 2),
            "top_decile_observations": top_decile_total,
            "lift_vs_random": round(lift, 2) if np.isfinite(lift) else None,
            "pre_event_score_percentile": pre_pct_summary,
            "quintile_breakdown": quintile_surge_rates,
        }

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    return {
        "arm": arm,
        "signal_path": os.path.basename(sig_path),
        "analysis_window": f"{start} → {end}",
        "sample_step": sample_step,
        "horizons": horizon_results,
        "elapsed_sec": round(elapsed, 1),
    }


# ── Hardgate integration API ──────────────────────────────────────────
def compute_surge_metrics_for_gate(
    signal_path: str,
    pack: dict, cfg,
    vix_regime_map: Dict[str, str],
    start: str = "2012-01-01",
    end: str = PACK_END_STR,
    surge_pct: float = 20.0,
    sample_step: int = 5,
) -> Dict[str, float]:
    """Compute lift_10d and q5q1_10d for hardgate G-H evaluation.

    Returns dict with keys: lift_10d, q5q1_10d.
    Designed to be called from run_hardgate_sweep.py or step_d_walk_forward.py.
    """
    close = np.asarray(pack.get("close", pack.get("raw_close")), dtype=np.float64)
    dates = np.asarray(pack["dates"])
    tickers = np.asarray(pack["tickers"])
    fwd_returns = _compute_forward_returns(close, [10])

    result = _analyze_signal(
        sig_cfg={"arm": "gate_eval", "path": signal_path},
        pack=pack, cfg=cfg,
        vix_regime_map=vix_regime_map,
        close=close, fwd_returns=fwd_returns,
        dates=dates, tickers=tickers,
        start=start, end=end,
        surge_pct=surge_pct,
        horizons=[10],
        lookbacks=[0],
        sample_step=sample_step,
    )
    if "error" in result:
        return {"lift_10d": float("nan"), "q5q1_10d": float("nan")}

    hr = result["horizons"].get(10, {})
    lift = hr.get("lift_vs_random", float("nan"))
    qb = hr.get("quintile_breakdown", [])
    if len(qb) >= 5 and qb[0]["surge_rate_pct"] > 0:
        q5q1 = qb[4]["surge_rate_pct"] / qb[0]["surge_rate_pct"]
    else:
        q5q1 = float("nan")

    return {"lift_10d": float(lift) if lift else float("nan"),
            "q5q1_10d": round(q5q1, 3) if np.isfinite(q5q1) else float("nan")}


# ── Markdown report ───────────────────────────────────────────────────
def _write_markdown(results: List[Dict], surge_pct: float, horizons: List[int],
                    lookbacks: List[int], out_path: str):
    lines: List[str] = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 급등 Ticker Score 상관성 분석 — {ts}")
    lines.append("")
    lines.append(f"- **Surge threshold**: forward return >= +{surge_pct:.0f}%")
    lines.append(f"- **Horizons**: {', '.join(f'{h}d' for h in horizons)}")
    lines.append(f"- **Pre-event lookbacks**: {', '.join(f't-{lb}' for lb in lookbacks)}")
    lines.append("")

    # Per-horizon cross-signal comparison
    for h in horizons:
        lines.append(f"## {h}-Day Horizon")
        lines.append("")

        # Summary table
        header = "| Signal | Surge Rate | Top-10% Precision | Lift | Top-10% Drop | Surge Events |"
        sep = "|--------|-----------|-------------------|------|-------------|-------------|"
        lines.append(header)
        lines.append(sep)

        for r in results:
            if "error" in r:
                lines.append(f"| {r['arm']} | ERROR | — | — | — | — |")
                continue
            hr = r["horizons"].get(h, {})
            if not hr:
                continue
            lines.append(
                f"| {r['arm']} "
                f"| {hr['overall_surge_rate_pct']:.2f}% "
                f"| {hr['top_decile_precision_pct']:.2f}% "
                f"| {hr.get('lift_vs_random', 'N/A')}x "
                f"| {hr['top_decile_drop_rate_pct']:.2f}% "
                f"| {hr['total_surge_events']:,} |"
            )
        lines.append("")

        # Pre-event percentile table
        lines.append(f"### Pre-Event Score Percentile (surge tickers, {h}d horizon)")
        lines.append("")
        lb_header = "| Signal |"
        lb_sep = "|--------|"
        for lb in lookbacks:
            lb_header += f" t-{lb} mean | t-{lb} median | t-{lb} %>80 |"
            lb_sep += "----------|------------|---------|"
        lines.append(lb_header)
        lines.append(lb_sep)

        for r in results:
            if "error" in r:
                continue
            hr = r["horizons"].get(h, {})
            if not hr:
                continue
            row = f"| {r['arm']} |"
            for lb in lookbacks:
                key = f"t-{lb}"
                pps = hr.get("pre_event_score_percentile", {}).get(key, {})
                if pps.get("mean") is not None:
                    row += f" {pps['mean']:.1f} | {pps['median']:.1f} | {pps.get('pct_above_80', 0):.1f}% |"
                else:
                    row += " — | — | — |"
            lines.append(row)
        lines.append("")

        # Quintile breakdown for each signal
        lines.append(f"### Score Quintile → Surge Rate ({h}d)")
        lines.append("")
        for r in results:
            if "error" in r:
                continue
            hr = r["horizons"].get(h, {})
            if not hr:
                continue
            lines.append(f"**{r['arm']}**")
            lines.append("")
            lines.append("| Quintile | Count | Surges | Surge% | Drop% |")
            lines.append("|----------|-------|--------|--------|-------|")
            for qr in hr.get("quintile_breakdown", []):
                lines.append(
                    f"| {qr['quintile']} "
                    f"| {qr['count']:,} "
                    f"| {qr['surges']:,} "
                    f"| {qr['surge_rate_pct']:.2f}% "
                    f"| {qr['drop_rate_pct']:.2f}% |"
                )
            lines.append("")

    # Interpretation
    lines.append("## 해석 가이드")
    lines.append("")
    lines.append("- **Lift > 1.0**: signal의 top-decile이 random보다 급등 포착을 잘 함")
    lines.append("- **Lift ≈ 1.0**: score rank와 급등이 무관함 (alpha 부재)")
    lines.append("- **Pre-event percentile 높을수록**: 급등 전에 이미 high-score → 예측력 있음")
    lines.append("- **Top-decile drop rate 높으면**: false positive 많음 → 순수 momentum 의존?")
    lines.append("- **Quintile monotonicity**: Q1→Q5로 surge rate가 단조증가하면 score 자체에 alpha")
    lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Report saved → {out_path}")


# ── Main ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Surge-score correlation analysis")
    parser.add_argument("--signals", type=str, default=None,
                        help="Comma-separated signal IDs (default: all)")
    parser.add_argument("--surge-pct", type=float, default=DEFAULT_SURGE_PCT,
                        help=f"Surge threshold %% (default: {DEFAULT_SURGE_PCT})")
    parser.add_argument("--horizons", type=str, default=None,
                        help=f"Comma-separated forward horizons in days (default: {DEFAULT_HORIZONS})")
    parser.add_argument("--lookbacks", type=str, default=None,
                        help=f"Comma-separated pre-event lookback in trading days, 0=same day (default: {DEFAULT_LOOKBACKS})")
    parser.add_argument("--start", type=str, default="2012-01-01",
                        help="Analysis start date (default: 2012-01-01)")
    parser.add_argument("--end", type=str, default=PACK_END_STR,
                        help=f"Analysis end date (default: {PACK_END_STR})")
    parser.add_argument("--step", type=int, default=SAMPLE_STEP,
                        help=f"Score sampling step (default: {SAMPLE_STEP})")
    args = parser.parse_args()

    surge_pct = args.surge_pct
    horizons = [int(x) for x in args.horizons.split(",")] if args.horizons else DEFAULT_HORIZONS
    lookbacks = [int(x) for x in args.lookbacks.split(",")] if args.lookbacks else DEFAULT_LOOKBACKS

    if args.signals:
        wanted = set(args.signals.split(","))
        signals = [s for s in SIGNALS if s["id"] in wanted]
        if not signals:
            print(f"No matching signals for: {wanted}")
            print(f"Available: {[s['id'] for s in SIGNALS]}")
            sys.exit(1)
    else:
        signals = list(SIGNALS)

    # Filter to existing signal files
    signals = [s for s in signals if os.path.exists(s["path"])]
    if not signals:
        print("No signal files found on disk. Exiting.")
        sys.exit(1)

    print("=" * 70)
    print("  SURGE-SCORE CORRELATION ANALYSIS (주제 #5)")
    print("=" * 70)
    print(f"  Surge threshold : +{surge_pct:.0f}%")
    print(f"  Horizons        : {horizons}")
    print(f"  Lookbacks       : {lookbacks}")
    print(f"  Window          : {args.start} → {args.end}")
    print(f"  Signals         : {len(signals)}")
    for s in signals:
        print(f"    [{s['id']}] {s['arm']}")
    print(f"  Sample step     : every {args.step} trading days")
    print("=" * 70)

    # Load config & pack
    with open(CONFIG_PATH) as f:
        conf = yaml.safe_load(f)

    save_dir = conf["paths"]["output_dir"]
    pack_path, pack_start, pack_end = _pick_pack(save_dir)
    print(f"\n  Loading pack: {os.path.basename(pack_path)}")
    print(f"  Pack span: {pack_start} → {pack_end}")

    t_load = time.time()
    cfg = _build_cfg(conf, pack_start, pack_end)

    raw = np.load(pack_path, allow_pickle=True)
    pack = {k: raw[k] for k in raw.files}
    if "pack" in pack:
        pack = pack["pack"]

    dates = np.asarray(pack["dates"])
    tickers = np.asarray(pack["tickers"])
    close = np.asarray(pack.get("close", pack.get("raw_close")), dtype=np.float64)
    D, N = close.shape
    print(f"  Pack loaded: {D} dates × {N} tickers ({time.time()-t_load:.1f}s)")

    # VIX regime
    print("  Building VIX regime map...")
    _, vix_regime_map, _ = _load_vix(cfg, pack_start, pack_end)
    print(f"  VIX regime entries: {len(vix_regime_map)}")

    # Forward returns
    max_h = max(horizons)
    print(f"  Computing forward returns (max horizon={max_h}d)...")
    fwd_returns = _compute_forward_returns(close, horizons)

    # Analyze each signal
    all_results: List[Dict] = []
    for sig_cfg in signals:
        result = _analyze_signal(
            sig_cfg, pack, cfg, vix_regime_map, close, fwd_returns,
            dates, tickers,
            start=args.start, end=args.end,
            surge_pct=surge_pct,
            horizons=horizons,
            lookbacks=lookbacks,
            sample_step=args.step,
        )
        all_results.append(result)

    # Write outputs
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(DOCS_DIR, f"surge_score_analysis_{ts}.md")
    json_path = os.path.join(DOCS_DIR, f"surge_score_analysis_{ts}.json")

    _write_markdown(all_results, surge_pct, horizons, lookbacks, md_path)

    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  JSON saved → {json_path}")

    # Console summary
    print("\n" + "=" * 70)
    print("  QUICK SUMMARY")
    print("=" * 70)
    for h in horizons:
        print(f"\n  [{h}d horizon, surge >= +{surge_pct:.0f}%]")
        print(f"  {'Signal':<25s} {'SurgeRate':>10s} {'Top10%Prec':>11s} {'Lift':>6s} {'Q5/Q1':>7s}")
        print(f"  {'─'*25} {'─'*10} {'─'*11} {'─'*6} {'─'*7}")
        for r in all_results:
            if "error" in r:
                print(f"  {r['arm']:<25s} {'ERROR':>10s}")
                continue
            hr = r["horizons"].get(h, {})
            if not hr:
                continue
            lift_str = f"{hr.get('lift_vs_random', 0):.2f}x" if hr.get('lift_vs_random') else "N/A"
            qb = hr.get("quintile_breakdown", [])
            if len(qb) >= 5 and qb[0]["surge_rate_pct"] > 0:
                q5q1 = qb[4]["surge_rate_pct"] / qb[0]["surge_rate_pct"]
                q5q1_str = f"{q5q1:.2f}x"
            else:
                q5q1_str = "N/A"
            print(f"  {r['arm']:<25s} {hr['overall_surge_rate_pct']:>9.2f}% {hr['top_decile_precision_pct']:>10.2f}% {lift_str:>6s} {q5q1_str:>7s}")

    print(f"\n  Total elapsed: {sum(r.get('elapsed_sec', 0) for r in all_results):.1f}s")
    print(f"  Report: {md_path}")


if __name__ == "__main__":
    main()
