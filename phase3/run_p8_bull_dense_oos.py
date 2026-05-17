"""P8_BULL_DENSE retrain with OOS-safe cut + reduced budget.

Original (run_phase5_batch_b.py):
  train_window: 2011-01-01 → 2026-03-31
  ga_sec     : ~116717s (≈ 32.4 h)
  Recipe    : P7_V2_RECIPE_BASE + _p8_bull (high-entropy BULL specialist
              targeting dense wb (k=15+ features))

This OOS runner:
  train_window: 2011-01-01 → **2024-05-31**  (true post-OOS for F4)
  Budget reduced ~4-5× to fit M1 wall-clock budget while preserving the
  high-entropy / k=15+ targeting:
    ga_population        700 → 500
    ga_generations       30  → 18
    stability_seed_runs  10  → 4
    stability_top_n_seeds 8  → 4
    stability_fast_pop/gen 250/15 → 200/12
    stability_refine_pop/gen 700/18 → 500/12
  Estimated runtime: ~6-8 hours.

Why P8_BULL_DENSE?
  Phase A's hardgate review showed P11_FUNDB_ANCHOR has a relatively weak
  BULL slot (mean +28% on regime BULL_dom vs V2's +34%). P8_BULL_DENSE was
  the only signal in the phase 6/7/8 batches with feature [15] pct_from_low_20d
  active in BULL slot — V2's secret-weapon BULL feature according to
  breakthrough_strategy_analysis_20260507.md. An OOS-clean version gives us
  a candidate to upgrade P11_ANCHOR's BULL slot.

Usage
-----
    python3 -u 0316-/phase3/run_p8_bull_dense_oos.py
    python3 -u 0316-/phase3/run_p8_bull_dense_oos.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from phase3.run_phase5_retrain import run_phase5_retrain  # noqa: E402


# ── P7_V2_RECIPE_BASE replica ──
P7_V2_RECIPE_BASE: Dict[str, Any] = {
    "enable_meta_search": True,
    "meta_search_mode": "TEMPLATE_PLUS_RANDOM",
    "meta_search_trials": 8,
    "meta_disabled_template_name": "TPL_SPREAD",
    "meta_alpha_floor_candidates": (0.12, 0.18, 0.22, 0.28, 0.34, 0.40),
    "meta_top_quantile_candidates": (0.10, 0.12, 0.15, 0.18, 0.20, 0.22),
    "meta_factor_corr_penalty_lambda_candidates": (0.04, 0.06, 0.08, 0.10),
    "meta_perturb_alpha_floor": True,
    "meta_perturb_top_quantile": True,
    "meta_perturb_corr_penalty": True,
    "meta_perturb_entropy_bonus": True,
    "meta_perturb_conc_penalty": True,
    "meta_score_w_inner_fitness": 0.45,
    "meta_score_w_spread": 2.50,
    "meta_score_w_mean_ic": 1.75,
    "meta_score_w_positive_ic": 0.25,
    "meta_score_w_portfolio_fwd1m": 0.75,
    "meta_score_w_turnover": 0.40,
    "meta_score_target_bonus_cap": 0.30,
    "meta_score_target_bonus_lambda": 0.40,
    "meta_score_shortfall_penalty_lambda": 1.00,
    "meta_score_turnover_penalty_lambda": 0.75,
    "meta_score_core_metric_gate_floor": 0.50,
    "meta_score_core_metric_gate_penalty": 0.75,

    "factor_corr_penalty_lambda": 0.10,
    "bull_min_spread_mix": 0.006,
    "bull_penalty_lambda_spread": 1.25,
    "top_quantile": 0.12,
    "w_ic1": 0.34, "w_ic3": 0.34, "w_spread": 0.32,
    "enable_fitness_risk_penalty": True,
    "fitness_downside_vol_lambda": 0.50,
    "fitness_max_neg_spread_ratio_lambda": 0.30,
    "enable_deployment_penalty": True,
    "w_turnover": 0.3, "w_cost": 0.2,
    "use_random_seed": False,

    # Universe (V2 chain)
    "enable_historical_universe": True,
    "historical_universe_expand_tickers": True,
    "enable_coverage_based_universe": True,
    "enable_panel_cache_fallback_download": False,
}


# ── P8_BULL_DENSE-specific deltas (from _p8_bull in run_phase5_batch_b.py) ──
P8_BULL_DELTA: Dict[str, Any] = {
    "entropy_bonus": 0.15,           # high entropy to force k=15+
    "conc_penalty":  0.18,
    "weight_cap":    0.25,           # spread across more features
    "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18),
    "meta_conc_penalty_candidates":  (0.08, 0.10, 0.12, 0.14, 0.15, 0.18, 0.22),
}


# ── REDUCED budget (~4-5× faster than original) ──
REDUCED_BUDGET: Dict[str, Any] = {
    "ga_population":                 500,    # was 700
    "ga_generations":                18,     # was 30
    "stability_seed_runs":           4,      # was 10
    "stability_top_n_seeds":         4,      # was 8
    "stability_fast_population":     200,    # was 250
    "stability_fast_generations":    12,     # was 15
    "stability_refine_population":   500,    # was 700
    "stability_refine_generations":  12,     # was 18
}


P8_BULL_DENSE_OOS_OVERRIDES: Dict[str, Any] = {
    **P7_V2_RECIPE_BASE,
    **P8_BULL_DELTA,
    **REDUCED_BUDGET,
    "excel_prefix": "SP500_P8_BULL_DENSE_OOS",
}


TRAIN_START = datetime(2011, 1, 1)
TRAIN_END   = datetime(2024, 5, 31)
RUN_TAG     = "P8_BULL_DENSE_OOS"
GA_SEED     = 20260902  # original SEED_H_BULL


def main() -> int:
    ap = argparse.ArgumentParser(
        description="P8_BULL_DENSE retrain with OOS-safe cut (~6-8h)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-rebuild-pack", action="store_true")
    args = ap.parse_args()

    print("=" * 72)
    print("  P8_BULL_DENSE retrain — OOS-safe cut (reduced budget)")
    print("=" * 72)
    print(f"  train window  : {TRAIN_START.date()} → {TRAIN_END.date()}")
    print(f"  ↳ original    : 2011-01-01 → 2026-03-31 (~32.4 h)")
    print(f"  ↳ post-OOS now: 2024-06-01 → 2026-02-27 (= F4 fold)")
    print(f"  GA recipe     : BULL dense specialist (entropy=0.15, k=15+ target)")
    print(f"  Budget        : ga_pop=500/18, stability 4 seeds × 200/12 + 500/12")
    print(f"  GA seed       : {GA_SEED}")
    print(f"  mode          : {'DRY-RUN' if args.dry_run else 'FULL (~6-8h)'}")
    print("=" * 72)

    extra = dict(P8_BULL_DENSE_OOS_OVERRIDES)
    if args.dry_run:
        extra.update({
            "meta_search_trials":             1,
            "meta_template_trials_per_template": 1,
            "meta_random_extra_trials":       0,
            "meta_top_n_refine":              1,
            "meta_fast_ga_population":        20,
            "meta_fast_ga_generations":       3,
            "meta_fast_stability_seed_runs":  1,
            "meta_fast_stability_top_n":      1,
        })

    result = run_phase5_retrain(
        dry_run=args.dry_run,
        force_rebuild_pack=args.force_rebuild_pack,
        train_start=TRAIN_START,
        train_end=TRAIN_END,
        run_tag=RUN_TAG,
        ga_seed=GA_SEED,
        extra_overrides=extra,
        excel_prefix=P8_BULL_DENSE_OOS_OVERRIDES["excel_prefix"],
    )

    if result and result.get("frozen_signal_path"):
        print("\n" + "=" * 72)
        print("  P8_BULL_DENSE_OOS retrain complete")
        print("=" * 72)
        print(f"  signal: {result['frozen_signal_path']}")
        print()
        print("  Next steps:")
        print(f"  1) Add to step_d_walk_forward.py and run F4 comparison.")
        print(f"  2) Consider adding as wb component in a new P11 ensemble"
              f" (replace or augment the P5E_FUND_BRK BULL slot).")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
