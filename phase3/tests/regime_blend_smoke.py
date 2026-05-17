"""Regime-blend live-path smoke test.

Compares the score vector computed with VIX=17.9 vs VIX=18.1 under three modes:

  (1) blend OFF   — step function (regime label flips, weights swap)
  (2) blend ON, width=2.0 — old setting, [16, 20] transition zone
  (3) blend ON, width=0.8 — new setting (Q2), [17.2, 18.8] transition zone

For each scenario the script reports:
  - α_bull / α_side / α_def at each VIX
  - Spearman rank correlation of (Score@17.9 vs Score@18.1)
  - Top-N overlap on top-15 picks
  - L2 distance of the score vector (cliff magnitude)

This validates that the user observation "17.9 vs 18.1 scoring completely different"
was the OFF-mode behaviour, and that Q2's enable + width=0.8 actually smooths it.
"""
from __future__ import annotations
import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import glob
import os
import sys
from datetime import datetime, timedelta
from typing import Tuple

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
from phase3.daily_runner import compute_today_scores, load_frozen_signal  # noqa: E402
from phase3.regime_blend import compute_blend_alphas  # noqa: E402

PACK_START_STR = "2011-01-03"
CONFIG_PATH = os.path.join(PHASE3_DIR, "config.yaml")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"


def _pick_pack(save_dir: str) -> str:
    pat = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    cands = sorted(glob.glob(pat), reverse=True)
    if not cands:
        raise RuntimeError(f"No pack found at {pat}")
    return cands[0]


def _build_cfg(conf):
    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except Exception:
                pass
    cfg.start_panel_date = datetime.strptime("2024-01-01", "%Y-%m-%d")
    cfg.end_date = datetime.strptime("2026-02-27", "%Y-%m-%d")
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


def _scores_for(cfg, pack, signal, regime: str,
                blend_alphas) -> pd.DataFrame:
    return compute_today_scores(
        cfg, pack, signal, regime, return_meta=False,
        blend_alphas=blend_alphas,
    )


def _compare(s_a: pd.DataFrame, s_b: pd.DataFrame, top_n: int = 15) -> dict:
    if s_a.empty or s_b.empty:
        return {"common_n": 0}
    merged = pd.merge(
        s_a[["Ticker", "Score"]].rename(columns={"Score": "ScoreA"}),
        s_b[["Ticker", "Score"]].rename(columns={"Score": "ScoreB"}),
        on="Ticker", how="inner",
    )
    spear = float(merged["ScoreA"].rank(ascending=False)
                  .corr(merged["ScoreB"].rank(ascending=False), method="spearman"))
    pearson = float(merged["ScoreA"].corr(merged["ScoreB"]))
    l2 = float(np.sqrt(((merged["ScoreA"] - merged["ScoreB"]) ** 2).sum()))
    top_a = set(s_a.head(top_n)["Ticker"])
    top_b = set(s_b.head(top_n)["Ticker"])
    overlap = len(top_a & top_b)
    return {
        "common_n": int(len(merged)),
        "spearman": round(spear, 4),
        "pearson": round(pearson, 4),
        "l2_score_diff": round(l2, 3),
        "topN_overlap": f"{overlap}/{top_n}",
        "topN_overlap_rate": round(overlap / top_n, 3),
        "scoreA_top1": s_a.iloc[0].to_dict() if len(s_a) else None,
        "scoreB_top1": s_b.iloc[0].to_dict() if len(s_b) else None,
        "max_abs_diff": round(float(merged.assign(d=lambda x: (x.ScoreA - x.ScoreB).abs())["d"].max()), 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal", type=str,
                        default=os.path.join(OUTPUT_SIG_DIR,
                                             "frozen_signal_P11_OOS_CLEAN_L3_FUNDB_ANCHOR_20260508_180824.npz"))
    parser.add_argument("--vix-low", type=float, default=17.9)
    parser.add_argument("--vix-high", type=float, default=18.1)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()

    print("=" * 70)
    print("  REGIME-BLEND LIVE-PATH SMOKE TEST")
    print("=" * 70)
    print(f"  Signal      : {os.path.basename(args.signal)}")
    print(f"  VIX low/high: {args.vix_low} vs {args.vix_high}")
    print(f"  Top-N       : {args.top_n}")

    with open(CONFIG_PATH) as f:
        conf = yaml.safe_load(f)
    cfg = _build_cfg(conf)
    bull_thr = float(conf["regime"].get("vix_bull_threshold", 18.0))
    def_thr = float(conf["regime"].get("vix_defensive_threshold", 30.0))

    pack_path = _pick_pack(conf["paths"]["output_dir"])
    print(f"  Pack        : {os.path.basename(pack_path)}")

    print("  Loading inputs ...")
    signal = load_frozen_signal(args.signal)
    raw = np.load(pack_path, allow_pickle=True)
    pack = {k: raw[k] for k in raw.files}
    if "pack" in pack:
        pack = pack["pack"]
    print(f"  Pack loaded: {len(pack['dates'])} dates × {len(pack['tickers'])} tickers")

    # Helper: compute alphas for (vix, width)
    def _alphas(vix: float, width: float) -> Tuple[float, float, float]:
        return compute_blend_alphas(
            vix,
            bull_threshold=bull_thr, def_threshold=def_thr,
            bull_side_blend_width=width, side_def_blend_width=3.0,
        )

    scenarios = [
        # (label, vix_low, vix_high, blend_alphas_low, blend_alphas_high, regime_low_label, regime_high_label)
        ("(A) blend OFF (step function — pre-Q2 behaviour)",
         args.vix_low, args.vix_high,
         None, None, "BULL", "SIDE"),
        ("(B) blend ON, width=2.0 (old default — [16, 20] zone)",
         args.vix_low, args.vix_high,
         _alphas(args.vix_low, 2.0), _alphas(args.vix_high, 2.0),
         "BULL", "SIDE"),
        ("(C) blend ON, width=0.8 (Q2 new — [17.2, 18.8] zone)",
         args.vix_low, args.vix_high,
         _alphas(args.vix_low, 0.8), _alphas(args.vix_high, 0.8),
         "BULL", "SIDE"),
    ]

    results = []
    for label, vlo, vhi, alo, ahi, rlo, rhi in scenarios:
        print()
        print("─" * 70)
        print(f"  {label}")
        print(f"    VIX={vlo}: alphas={alo} regime_label={rlo}")
        print(f"    VIX={vhi}: alphas={ahi} regime_label={rhi}")
        s_lo = _scores_for(cfg, pack, signal, rlo, alo)
        s_hi = _scores_for(cfg, pack, signal, rhi, ahi)
        cmp_res = _compare(s_lo, s_hi, top_n=args.top_n)
        cmp_res["scenario"] = label
        cmp_res["vix_low"] = vlo
        cmp_res["vix_high"] = vhi
        results.append(cmp_res)
        print(f"    common N      : {cmp_res['common_n']}")
        print(f"    Spearman ρ    : {cmp_res['spearman']}  (1.00 = identical ranking)")
        print(f"    Pearson  r    : {cmp_res['pearson']}")
        print(f"    L2 score Δ    : {cmp_res['l2_score_diff']}")
        print(f"    Top-{args.top_n} overlap : {cmp_res['topN_overlap']}  ({cmp_res['topN_overlap_rate']:.0%})")
        print(f"    max |ΔScore|  : {cmp_res['max_abs_diff']}")

    # Summary diff
    print()
    print("=" * 70)
    print("  SUMMARY — does Q2 actually smooth the cliff?")
    print("=" * 70)
    print(f"  {'scenario':<55s} {'rho':>6} {'overlap':>9} {'L2':>9}")
    for r in results:
        lbl = r["scenario"][:55]
        print(f"  {lbl:<55s} {r['spearman']:>6.3f} {r['topN_overlap']:>9} {r['l2_score_diff']:>9.2f}")
    print()
    a, b, c = results
    if a["l2_score_diff"] > 0 and c["l2_score_diff"] < a["l2_score_diff"] * 0.3:
        print(f"  ✓ Confirmed: width=0.8 reduces the cliff L2 by "
              f"{(1 - c['l2_score_diff']/a['l2_score_diff'])*100:.1f}% vs blend-OFF")
    elif a["l2_score_diff"] == 0:
        print(f"  ⚠ blend-OFF cliff is zero — likely both sides resolve to same regime label")
    else:
        print(f"  ⚠ Smoothing benefit smaller than expected. Inspect.")


if __name__ == "__main__":
    main()
