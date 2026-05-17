"""P7_V2_NO_DEPLOY retrain with OOS-safe cut + reduced budget.

Original (run_phase5_batch_b.py):
  train_window: 2011-01-01 → 2026-03-31
  ga_sec     : ~110315s (≈ 30.6 h)
  Recipe    : P7_V2_RECIPE_BASE with deployment penalty OFF
              (enable_deployment_penalty=False, w_turnover=0, w_cost=0)

This OOS runner:
  train_window: 2011-01-01 → **2024-05-31**  (true post-OOS for F4)
  Budget reduced ~5× to fit M1 wall-clock budget while keeping the
  stability layer's fundamental shape:
    ga_population        500 → 400
    ga_generations       25  → 15
    stability_seed_runs  8   → 4
    stability_top_n_seeds 6  → 4
    stability_fast_pop/gen 200/12 → 150/10
    stability_refine_pop/gen 500/15 → 400/10
  Estimated runtime: ~5-7 hours.

Why P7_V2_NO_DEPLOY?
  Phase A's hardgate review identified P7_STITCH_K (= P7_V2_NO_DEPLOY.wb +
  V2.ws + V2.wd) as the closest single non-OOS candidate to V2 (mean
  CAGR 30.90% on default fold-set, regime SIDE_1 = 5.42% vs V2's 1.67%).
  This run produces an OOS-clean wb that we can stitch with P2_BATCH11_OOS's
  ws/wd to build P7_STITCH_K_OOS — the OOS-clean equivalent of the strongest
  V2-style candidate.

Usage
-----
    python3 -u 0316-/phase3/run_p7_v2_no_deploy_oos.py
    python3 -u 0316-/phase3/run_p7_v2_no_deploy_oos.py --dry-run
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


# ── P7_V2_RECIPE_BASE replica (keeps the V2-style fitness shape) ─────
P7_V2_RECIPE_BASE: Dict[str, Any] = {
    # Meta search ON (the critical missing piece from P5/P6)
    "enable_meta_search": True,
    "meta_search_mode": "TEMPLATE_PLUS_RANDOM",
    "meta_search_trials": 8,
    "meta_disabled_template_name": "TPL_SPREAD",
    "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10),
    "meta_conc_penalty_candidates": (0.08, 0.10, 0.12, 0.14),
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

    # GA fitness recipe
    "entropy_bonus": 0.10,
    "conc_penalty": 0.12,
    "weight_cap": 0.40,
    "factor_corr_penalty_lambda": 0.10,

    # BULL floor tuning
    "bull_min_spread_mix": 0.006,
    "bull_penalty_lambda_spread": 1.25,

    "top_quantile": 0.12,
    "w_ic1": 0.34, "w_ic3": 0.34, "w_spread": 0.32,

    "enable_fitness_risk_penalty": True,
    "fitness_downside_vol_lambda": 0.50,
    "fitness_max_neg_spread_ratio_lambda": 0.30,

    "use_random_seed": False,

    # Universe (V2 chain)
    "enable_historical_universe": True,
    "historical_universe_expand_tickers": True,
    "enable_coverage_based_universe": True,
    "enable_panel_cache_fallback_download": False,
}


# ── NO_DEPLOY-specific overrides ──
NO_DEPLOY_DELTA: Dict[str, Any] = {
    "enable_deployment_penalty": False,
    "w_turnover": 0.0,
    "w_cost": 0.0,
}


# ── REDUCED budget (~5× faster than original) ──
REDUCED_BUDGET: Dict[str, Any] = {
    "ga_population":                 400,    # was 500
    "ga_generations":                15,     # was 25
    "stability_seed_runs":           4,      # was 8
    "stability_top_n_seeds":         4,      # was 6
    "stability_fast_population":     150,    # was 200
    "stability_fast_generations":    10,     # was 12
    "stability_refine_population":   400,    # was 500
    "stability_refine_generations":  10,     # was 15
}


P7_NODEP_OOS_OVERRIDES: Dict[str, Any] = {
    **P7_V2_RECIPE_BASE,
    **NO_DEPLOY_DELTA,
    **REDUCED_BUDGET,
    "excel_prefix": "SP500_P7_V2_NO_DEPLOY_OOS",
}


TRAIN_START = datetime(2011, 1, 1)
TRAIN_END   = datetime(2024, 5, 31)
RUN_TAG     = "P7_V2_NO_DEPLOY_OOS"
GA_SEED     = 20260802  # original SEED_G_NODEP


def main() -> int:
    ap = argparse.ArgumentParser(
        description="P7_V2_NO_DEPLOY retrain with OOS-safe cut (~5-7h)")
    ap.add_argument("--dry-run", action="store_true",
                    help="1-2 min smoke test (tiny GA budget) to verify wiring")
    ap.add_argument("--force-rebuild-pack", action="store_true",
                    help="Delete and rebuild the training pack")
    args = ap.parse_args()

    print("=" * 72)
    print("  P7_V2_NO_DEPLOY retrain — OOS-safe cut (reduced budget)")
    print("=" * 72)
    print(f"  train window  : {TRAIN_START.date()} → {TRAIN_END.date()}")
    print(f"  ↳ original    : 2011-01-01 → 2026-03-31 (~30.6 h)")
    print(f"  ↳ post-OOS now: 2024-06-01 → 2026-02-27 (= F4 fold)")
    print(f"  GA recipe     : P7_V2_RECIPE_BASE + NO_DEPLOY (penalty=False)")
    print(f"  Budget        : ga_pop=400/15, stability 4 seeds × 150/10 + 400/10")
    print(f"  GA seed       : {GA_SEED}")
    print(f"  mode          : {'DRY-RUN' if args.dry_run else 'FULL (~5-7h)'}")
    print("=" * 72)

    extra = dict(P7_NODEP_OOS_OVERRIDES)
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
        excel_prefix=P7_NODEP_OOS_OVERRIDES["excel_prefix"],
    )

    if result and result.get("frozen_signal_path"):
        print("\n" + "=" * 72)
        print("  P7_V2_NO_DEPLOY_OOS retrain complete")
        print("=" * 72)
        print(f"  signal: {result['frozen_signal_path']}")
        print()
        print("  Next steps:")
        print(f"  1) Use composer to build P7_STITCH_K_OOS:")
        print(f"     wb ← P7_V2_NO_DEPLOY_OOS  (this signal)")
        print(f"     ws ← P2_BATCH11_OOS or P5E_FUND_BRK")
        print(f"     wd ← P2_BATCH11_OOS or P5E_FUND_BRK")
        print(f"  2) Add to step_d_walk_forward.py SIGNALS list and evaluate.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
