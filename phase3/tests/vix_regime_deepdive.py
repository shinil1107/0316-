"""VIX-regime deep-dive: distribution, threshold sweep, optimal cutoff search.

Purpose
-------
1. Describe the VIX distribution over the back-test horizon (min/max/percentiles).
2. Show how each candidate ``vix_bull_threshold`` re-shapes BULL / SIDE / DEF
   day counts and their *forward* universe return (proxy for regime alpha).
3. Identify the threshold that maximizes the BULL-vs-SIDE forward-return spread
   while keeping a healthy mass in each bucket.

Outputs
-------
``phase3/docs/vix_regime_deepdive_<ts>.{md,json}``

Quick CLI:
    python3 -u phase3/tests/vix_regime_deepdive.py
    python3 -u phase3/tests/vix_regime_deepdive.py --start 2012-01-01
"""
from __future__ import annotations
import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Tuple

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

PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"
CONFIG_PATH = os.path.join(PHASE3_DIR, "config.yaml")
DOCS_DIR = os.path.join(PHASE3_DIR, "docs")


def _pick_pack(save_dir: str) -> str:
    pat = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    cands = sorted(glob.glob(pat), reverse=True)
    if not cands:
        raise RuntimeError(f"No pack found at {pat}")
    return cands[0]


def _build_cfg(conf, start, end):
    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except Exception:
                pass
    cfg.start_panel_date = datetime.strptime(start, "%Y-%m-%d")
    cfg.end_date = datetime.strptime(end, "%Y-%m-%d")
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


def _load_vix(cfg, start, end) -> pd.DataFrame:
    df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start, "%Y-%m-%d"),
        datetime.strptime(end, "%Y-%m-%d"),
    )
    if df is None or df.empty:
        raise RuntimeError("VIX time-series is empty")
    if "date" not in df.columns:
        df = df.reset_index().rename(columns={"index": "date"})
    df["date_str"] = df["date"].astype(str).str[:10]
    return df


def _load_universe_close(pack_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    raw = np.load(pack_path, allow_pickle=True)
    pack = {k: raw[k] for k in raw.files}
    if "pack" in pack:
        pack = pack["pack"]
    dates = np.asarray(pack["dates"]).astype(str)
    tickers = np.asarray(pack["tickers"])
    close = np.asarray(pack.get("close", pack.get("raw_close")), dtype=np.float64)
    return dates, tickers, close


def _equal_weight_universe_returns(close: np.ndarray) -> np.ndarray:
    """Daily universe-mean log-return (D,)."""
    D, N = close.shape
    out = np.zeros(D, dtype=np.float64)
    for d in range(1, D):
        c0 = close[d - 1]
        c1 = close[d]
        m = (c0 > 0) & (c1 > 0) & np.isfinite(c0) & np.isfinite(c1)
        if int(m.sum()) > 0:
            out[d] = float(np.nanmean(np.log(c1[m] / c0[m])))
    return out


def _classify_regime(vix: float, bull_thr: float, def_thr: float) -> str:
    if vix < bull_thr:
        return "BULL"
    if vix < def_thr:
        return "SIDE"
    return "DEF"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2011-01-03")
    parser.add_argument("--end", type=str, default=PACK_END_STR)
    parser.add_argument(
        "--bull-thresholds", type=str,
        default="14,15,16,17,17.5,18,18.5,19,20,21,22",
        help="Comma-separated VIX cutoffs to sweep for the BULL/SIDE boundary.",
    )
    parser.add_argument("--def-threshold", type=float, default=30.0)
    parser.add_argument("--horizons", type=str, default="5,21",
                        help="Forward horizons (trading days) for fwd-return analysis.")
    args = parser.parse_args()

    bull_thrs = sorted({float(x) for x in args.bull_thresholds.split(",")})
    horizons = sorted({int(x) for x in args.horizons.split(",")})

    print("=" * 70)
    print("  VIX-REGIME DEEP-DIVE")
    print("=" * 70)
    print(f"  Window         : {args.start} → {args.end}")
    print(f"  Bull thresholds: {bull_thrs}")
    print(f"  Def  threshold : {args.def_threshold}")

    with open(CONFIG_PATH) as f:
        conf = yaml.safe_load(f)
    cfg = _build_cfg(conf, args.start, args.end)

    pack_path = _pick_pack(conf["paths"]["output_dir"])
    print(f"  Pack           : {os.path.basename(pack_path)}")
    dates, tickers, close = _load_universe_close(pack_path)
    D, N = close.shape
    print(f"  Pack loaded    : {D} dates × {N} tickers")

    print("  Loading VIX series ...")
    vix_df = _load_vix(cfg, args.start, args.end)
    vix_col = "vix_smooth" if "vix_smooth" in vix_df.columns else "close"
    vix_df = vix_df[(vix_df["date_str"] >= args.start) & (vix_df["date_str"] <= args.end)].copy()
    vix_df = vix_df.sort_values("date_str").reset_index(drop=True)
    print(f"  VIX entries    : {len(vix_df)}  (col={vix_col})")

    # Pack-aligned VIX vector + universe returns
    date_to_idx = {d: i for i, d in enumerate(dates)}
    pack_aligned: List[Tuple[int, float, float, str]] = []
    # pre-compute universe daily log-return
    univ_logret = _equal_weight_universe_returns(close)
    # forward-return arrays
    fwd: Dict[int, np.ndarray] = {}
    for h in horizons:
        arr = np.full(D, np.nan, dtype=np.float64)
        for d in range(D - h):
            window = univ_logret[d + 1 : d + 1 + h]
            if window.size > 0:
                arr[d] = float(np.nansum(window))  # log forward return over h days
        fwd[h] = arr

    # Filter VIX to dates that exist in the pack
    used = []
    for _, row in vix_df.iterrows():
        d = row["date_str"]
        if d not in date_to_idx:
            continue
        used.append((date_to_idx[d], float(row[vix_col]), d))

    if not used:
        raise RuntimeError("No VIX dates align with the pack — check window.")

    vix_arr = np.array([u[1] for u in used], dtype=np.float64)
    idx_arr = np.array([u[0] for u in used], dtype=np.int64)
    date_arr = np.array([u[2] for u in used])

    # Drop NaN VIX rows (e.g. boundary days before vix_smooth warm-up).
    valid_v = np.isfinite(vix_arr)
    n_drop = int((~valid_v).sum())
    if n_drop:
        print(f"  Dropping {n_drop} NaN VIX rows (warm-up boundary)")
    vix_arr = vix_arr[valid_v]
    idx_arr = idx_arr[valid_v]
    date_arr = date_arr[valid_v]

    # ── Section 1: VIX distribution ────────────────────────────────────
    pct = np.nanpercentile(vix_arr, [1, 5, 10, 25, 50, 75, 90, 95, 99]).tolist()
    summary_dist = {
        "n_days": int(len(vix_arr)),
        "min": float(np.nanmin(vix_arr)),
        "max": float(np.nanmax(vix_arr)),
        "mean": float(np.nanmean(vix_arr)),
        "std": float(np.nanstd(vix_arr)),
        "p1": pct[0], "p5": pct[1], "p10": pct[2],
        "p25": pct[3], "p50": pct[4], "p75": pct[5],
        "p90": pct[6], "p95": pct[7], "p99": pct[8],
    }

    print("\n" + "─" * 70)
    print(f"  VIX distribution ({summary_dist['n_days']} days)")
    print("─" * 70)
    for k in ["min", "p1", "p5", "p10", "p25", "p50", "mean",
              "p75", "p90", "p95", "p99", "max", "std"]:
        print(f"    {k:<6}: {summary_dist[k]:6.2f}")

    # ── Section 2: Threshold sweep ─────────────────────────────────────
    sweep_rows = []
    for thr in bull_thrs:
        labels = np.array(["BULL" if v < thr else ("SIDE" if v < args.def_threshold else "DEF")
                           for v in vix_arr])
        bull_mask = labels == "BULL"
        side_mask = labels == "SIDE"
        def_mask = labels == "DEF"
        n_b, n_s, n_d = int(bull_mask.sum()), int(side_mask.sum()), int(def_mask.sum())
        row = {"bull_thr": thr,
               "bull_pct": round(n_b / len(vix_arr) * 100, 2),
               "side_pct": round(n_s / len(vix_arr) * 100, 2),
               "def_pct":  round(n_d / len(vix_arr) * 100, 2)}
        for h in horizons:
            f = fwd[h][idx_arr]
            for label, mask in (("bull", bull_mask), ("side", side_mask), ("def", def_mask)):
                if int(mask.sum()) == 0:
                    row[f"{label}_fwd{h}d_mean"] = None
                    row[f"{label}_fwd{h}d_winrate"] = None
                else:
                    vals = f[mask]
                    vals = vals[np.isfinite(vals)]
                    if len(vals) == 0:
                        row[f"{label}_fwd{h}d_mean"] = None
                        row[f"{label}_fwd{h}d_winrate"] = None
                    else:
                        row[f"{label}_fwd{h}d_mean"] = round(float(vals.mean()) * 100, 4)  # log %
                        row[f"{label}_fwd{h}d_winrate"] = round(float((vals > 0).mean()) * 100, 2)
            # Spread metric for this threshold (B - S forward log-return %)
            bull_mean = row.get(f"bull_fwd{h}d_mean") or 0
            side_mean = row.get(f"side_fwd{h}d_mean") or 0
            row[f"bull_minus_side_{h}d"] = round(bull_mean - side_mean, 4)
        sweep_rows.append(row)

    # Pretty print
    print("\n" + "─" * 70)
    print("  Threshold sweep — day-count share & forward universe log-return")
    print("─" * 70)
    print(f"  Forward returns are log %, computed on equal-weight universe close-to-close.")
    print()
    print(f"  {'thr':>5} | {'BULL%':>6} {'SIDE%':>6} {'DEF%':>5} | "
          f"{'bull5d':>7} {'side5d':>7} {'B-S5d':>7} | "
          f"{'bull21d':>8} {'side21d':>8} {'B-S21d':>7}")
    for r in sweep_rows:
        bull5 = r.get("bull_fwd5d_mean") or 0
        side5 = r.get("side_fwd5d_mean") or 0
        bull21 = r.get("bull_fwd21d_mean") or 0
        side21 = r.get("side_fwd21d_mean") or 0
        print(f"  {r['bull_thr']:>5.2f} | {r['bull_pct']:>5.1f}% {r['side_pct']:>5.1f}% {r['def_pct']:>4.1f}% | "
              f"{bull5:>+7.3f} {side5:>+7.3f} {r.get('bull_minus_side_5d'):>+7.3f} | "
              f"{bull21:>+8.3f} {side21:>+8.3f} {r.get('bull_minus_side_21d'):>+7.3f}")

    # ── Section 3: Distribution by VIX buckets ─────────────────────────
    bucket_edges = [0, 12, 14, 16, 18, 20, 22, 25, 30, 35, 50, 100]
    bucket_rows = []
    for lo, hi in zip(bucket_edges[:-1], bucket_edges[1:]):
        m = (vix_arr >= lo) & (vix_arr < hi)
        if int(m.sum()) == 0:
            continue
        f5 = fwd[5][idx_arr][m]; f5 = f5[np.isfinite(f5)]
        f21 = fwd[21][idx_arr][m]; f21 = f21[np.isfinite(f21)]
        bucket_rows.append({
            "lo": lo, "hi": hi, "n": int(m.sum()),
            "pct": round(int(m.sum()) / len(vix_arr) * 100, 2),
            "fwd5d_mean": round(float(f5.mean()) * 100, 4) if len(f5) else None,
            "fwd5d_winrate": round(float((f5 > 0).mean()) * 100, 2) if len(f5) else None,
            "fwd21d_mean": round(float(f21.mean()) * 100, 4) if len(f21) else None,
            "fwd21d_winrate": round(float((f21 > 0).mean()) * 100, 2) if len(f21) else None,
        })

    print("\n" + "─" * 70)
    print("  VIX bucket → Forward universe returns (log %)")
    print("─" * 70)
    print(f"  {'bucket':<10} {'n':>5} {'%':>5} | {'fwd5d':>7} {'win5%':>5} | {'fwd21d':>8} {'win21%':>6}")
    for r in bucket_rows:
        print(f"  [{r['lo']:>2}-{r['hi']:<3}) {r['n']:>5} {r['pct']:>4}% | "
              f"{r['fwd5d_mean'] or 0:>+7.3f} {r['fwd5d_winrate'] or 0:>4.0f}% | "
              f"{r['fwd21d_mean'] or 0:>+8.3f} {r['fwd21d_winrate'] or 0:>5.0f}%")

    # ── Save artifacts ─────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(DOCS_DIR, f"vix_regime_deepdive_{ts}.md")
    json_path = os.path.join(DOCS_DIR, f"vix_regime_deepdive_{ts}.json")

    payload = {
        "window": {"start": args.start, "end": args.end},
        "vix_distribution": summary_dist,
        "threshold_sweep": sweep_rows,
        "bucket_breakdown": bucket_rows,
        "def_threshold": args.def_threshold,
        "horizons": horizons,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\n  JSON saved → {json_path}")

    lines = [f"# VIX Regime Deep-Dive — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             "",
             f"- **Window**: {args.start} → {args.end}",
             f"- **Days**: {summary_dist['n_days']}",
             f"- **Forward horizons**: {horizons}",
             f"- **DEF cutoff fixed**: {args.def_threshold}",
             "",
             "## 1. VIX distribution",
             "",
             "| stat | value |",
             "|---|---:|"]
    for k in ["min", "p1", "p5", "p10", "p25", "p50", "mean",
              "p75", "p90", "p95", "p99", "max", "std"]:
        lines.append(f"| {k} | {summary_dist[k]:.2f} |")

    lines += ["",
              "## 2. BULL/SIDE/DEF threshold sweep",
              "",
              "Forward returns are equal-weight universe close-to-close log-return averages "
              "(percent). `B-S` = BULL_mean − SIDE_mean (positive ⇒ BULL identifies higher "
              "expected forward return).",
              "",
              f"| bull_thr | BULL% | SIDE% | DEF% | bull_fwd5d | side_fwd5d | B-S 5d | bull_fwd21d | side_fwd21d | B-S 21d |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sweep_rows:
        lines.append(
            f"| {r['bull_thr']:.2f} | {r['bull_pct']:.1f} | {r['side_pct']:.1f} | {r['def_pct']:.1f} "
            f"| {r.get('bull_fwd5d_mean') or 0:+.3f} | {r.get('side_fwd5d_mean') or 0:+.3f} "
            f"| {r.get('bull_minus_side_5d'):+.3f} "
            f"| {r.get('bull_fwd21d_mean') or 0:+.3f} | {r.get('side_fwd21d_mean') or 0:+.3f} "
            f"| {r.get('bull_minus_side_21d'):+.3f} |"
        )

    lines += ["",
              "## 3. VIX bucket → forward returns",
              "",
              "| bucket | days | share | fwd5d log% | win5% | fwd21d log% | win21% |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for r in bucket_rows:
        lines.append(
            f"| [{r['lo']}, {r['hi']}) | {r['n']} | {r['pct']:.1f}% "
            f"| {r['fwd5d_mean'] or 0:+.3f} | {r['fwd5d_winrate'] or 0:.0f}% "
            f"| {r['fwd21d_mean'] or 0:+.3f} | {r['fwd21d_winrate'] or 0:.0f}% |"
        )

    lines += ["",
              "## Interpretation",
              "",
              "- **Optimal BULL/SIDE cutoff** is the threshold that maximizes the `B-S` "
              "spread *while* keeping enough mass in BULL bucket (≥30% of days). ",
              "- A wide `B-S` gap means VIX cleanly separates expected returns; a narrow "
              "or negative gap means VIX provides little regime alpha at that boundary.",
              "- The bucket table reveals the underlying structure: forward-return monotonic "
              "behaviour vs. VIX, and the practical 'breakpoint' where SIDE-style risk "
              "actually emerges.",
              ""]

    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Markdown saved → {md_path}")


if __name__ == "__main__":
    main()
