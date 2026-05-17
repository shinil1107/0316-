"""P2_BATCH11 retrain with OOS-safe cut — exact replica of the original GA
config that produced ``frozen_signal_P2_BATCH11_20260406_043415.npz``,
but with ``end_date = 2024-05-31`` so the F4 fold (2024-06 → 2026-02)
becomes a *true post-OOS* validation window for the signal itself.

This is the V2 baseline's dominant component (P2_BATCH11). All other
V2 ENS_L3 members (BULL_GA_V2, E2E) were trained on similar windows
ending in 2026-Q1; this run isolates whether V2's apparent +45.92%
F4 CAGR is real signal alpha or partial lookahead.

Why a separate runner instead of editing run_phase5_retrain.py
--------------------------------------------------------------
``run_phase5_retrain.py`` carries the **post-audit (F1/F4/F11/S1)
patched** GA recipe with deployment-penalty (w_turnover, w_cost),
meta-search OFF, and modest ga_pop=300. That is the *intentionally
modified* recipe used to validate strategy changes.

P2_BATCH11's *original* recipe is different:
  • Meta-search ON (8 trials, top_n_refine=2)
  • Larger GA budget (population 400, generations 12)
  • Larger stability sweep (8 seeds, refine population 600 × 11 gens)
  • BULL spread bonus pump (bull_spread_bonus_lambda=1.35)
  • entropy_bonus=0.10 (the un-patched value; F1 audit reduced it to 0.04)
  • Specific BULL pool (_SA_VQM_BULL_POOL_BASE / _FACTOR_BASE)
  • meta_disabled_template_name="TPL_SPREAD" (NOT TPL_BALANCED)
  • use_random_seed=True (random init each run, NOT seeded)

We replicate those exact knobs here so the only intentional change
is the train-end cut (2026-03-02 → 2024-05-31).

Estimated runtime (full run): 3-4 hours
Use ``--dry-run`` for a 1-2 minute smoke test.
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


# ── Original P2_BATCH11 BULL pools (from archive_experiment_configs.py) ──
_SA_VQM_BULL_POOL_BASE = (
    "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
    "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
    "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
    "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD", "QUAL_ROE_X_BREAKOUT_126",
    "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
)

_SA_VQM_BULL_FACTOR_BASE = (
    "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
    "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
    "RSI_TREND", "SMA50_SLOPE", "ADX", "QUAL_ROE_X_BREAKOUT_126",
    "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
)


# ── Exact P2_BATCH11 GA config from notebook cell ~12270 ─────────────
# (only end_date is changed; everything else is byte-for-byte identical)
P2_BATCH11_OVERRIDES: Dict[str, Any] = {
    # Universe (match V1/V2 chain)
    "enable_historical_universe":           True,
    "historical_universe_expand_tickers":   True,
    "enable_coverage_based_universe":       True,
    "enable_panel_cache_fallback_download": False,
    "enable_cs_rank_features":              False,
    "enable_cs_rank_scoring":               False,
    "enable_rolling_ic_adaptive":           False,

    # GA fitness function (BATCH01 chain — original P2_BATCH11 recipe)
    "top_quantile":                         0.12,
    "w_ic1":                                0.34,
    "w_ic3":                                0.34,
    "w_spread":                             0.32,
    "factor_corr_penalty_lambda":           0.10,
    "entropy_bonus":                        0.10,    # un-patched (F1 audit reduced this to 0.04)
    "weight_cap":                           0.40,
    "bull_min_spread_mix":                  0.0060,
    "bull_penalty_lambda_spread":           1.25,
    "bull_spread_bonus_threshold":          0.0025,
    "bull_spread_bonus_lambda":             1.35,
    "meta_disabled_template_name":          "TPL_SPREAD",  # NOT TPL_BALANCED

    # Override Phase 5 deployment-penalty (P2_BATCH11 did not use it)
    "enable_deployment_penalty":            False,

    # GA budget (original — ga_pop=400, ga_gen=12)
    "ga_population":                        400,
    "ga_generations":                       12,
    "enable_stability_layer":               True,
    "stability_seed_runs":                  8,
    "stability_top_n_seeds":                4,
    "stability_fast_population":            200,
    "stability_fast_generations":           9,
    "stability_refine_population":          600,
    "stability_refine_generations":         11,
    "enable_fitness_risk_penalty":          True,
    "fitness_downside_vol_lambda":          0.50,
    "fitness_max_neg_spread_ratio_lambda":  0.30,

    # Meta search (original had this ON)
    "enable_order_book":                    True,
    "order_book_total_capital":             100000.0,
    "enable_portfolio_construction":        True,
    "enable_meta_search":                   True,
    "meta_search_mode":                     "TEMPLATE_PLUS_RANDOM",
    "meta_search_trials":                   8,
    "meta_template_trials_per_template":    1,
    "meta_random_extra_trials":             1,
    "meta_allow_template_perturbation":     True,
    "meta_top_n_refine":                    2,
    "meta_fast_ga_population":              100,
    "meta_fast_ga_generations":             10,
    "meta_fast_stability_seed_runs":        3,
    "meta_fast_stability_top_n":            2,
    # Meta candidate spaces (must include the base values above so
    # template validation passes — same widening used by run_phase5_batch_b.py).
    "meta_entropy_bonus_candidates":        (0.04, 0.06, 0.08, 0.10, 0.12, 0.14),
    "meta_conc_penalty_candidates":         (0.08, 0.10, 0.12, 0.14, 0.18, 0.22),
    "meta_alpha_floor_candidates":          (0.12, 0.18, 0.22, 0.28, 0.34, 0.40),

    # Reproducibility
    # NOTE: original P2_BATCH11 used use_random_seed=True. For reproducibility
    # of *this* OOS run we set a fixed seed; if you want bit-identical
    # variance reproduction set use_random_seed=True explicitly.
    "use_random_seed":                      False,

    # BULL factor pools
    "bull_allowed_factor_pool":             _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool":                     _SA_VQM_BULL_FACTOR_BASE,

    # Reports
    "excel_prefix":                         "SP500_P2_BATCH11_OOS",
}


# ── Train window — intentional changes vs original ───────────────────
# Original: 2017-02-21 → 2026-03-02
# This run: 2015-01-01 → 2024-05-31
#   ① END cut for true signal-OOS validation (F4 = 2024-06 → 2026-02 becomes
#      genuine post-OOS instead of in-sample as in V2 baseline).
#   ② START extended back to 2015 to absorb the 2 yrs lost to the END cut and
#      cover an additional volatility regime (2015 China devaluation, 2016
#      Brexit, oil crash) — total in-sample length ≈ 9.4 yrs (vs original 9.0).
#      Recent legacy-ticker financial backfill (batch6/7/8) makes 2015-onwards
#      universe coverage usable.
TRAIN_START = datetime(2015, 1, 1)
TRAIN_END   = datetime(2024, 5, 31)
RUN_TAG     = "P2_BATCH11_OOS"
GA_SEED     = 20260507                # fixed seed for this OOS replication


def main() -> int:
    ap = argparse.ArgumentParser(description="P2_BATCH11 retrain with OOS-safe cut")
    ap.add_argument("--dry-run", action="store_true",
                    help="1-2 min smoke test (tiny GA budget) to verify wiring")
    ap.add_argument("--force-rebuild-pack", action="store_true",
                    help="Delete and rebuild the training pack")
    args = ap.parse_args()

    print("=" * 72)
    print("  P2_BATCH11 retrain — OOS-safe cut + extended start")
    print("=" * 72)
    print(f"  train window   : {TRAIN_START.date()} → {TRAIN_END.date()}")
    print(f"  ↳ original was : 2017-02-21 → 2026-03-02 (no post-OOS region)")
    print(f"  ↳ start back to: 2015-01-01 (absorb 2 yrs lost to END cut)")
    print(f"  ↳ post-OOS now : 2024-06-01 → 2026-02-27 (= F4 fold)")
    print(f"  GA recipe      : original P2_BATCH11 (meta ON, ga_pop=400/12, "
          f"stability 8 seeds × 200/9 + 600/11)")
    print(f"  GA seed        : {GA_SEED}")
    print(f"  mode           : {'DRY-RUN' if args.dry_run else 'FULL (~3-4h)'}")
    print("=" * 72)

    extra = dict(P2_BATCH11_OVERRIDES)
    if args.dry_run:
        # run_phase5_retrain.DRY_RUN_OVERRIDES only shrinks the *outer* GA
        # budget, not the meta-search inner loops. With our recipe meta-search
        # has 8 trials × (fast_ga 100×10 + 3 stability seeds) which still
        # takes ~20 min even in dry-run. Force meta down to a single trial
        # so wiring verification stays under 2-3 min.
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
        excel_prefix=P2_BATCH11_OVERRIDES["excel_prefix"],
    )

    if result and result.get("frozen_signal_path"):
        print("\n" + "=" * 72)
        print("  P2_BATCH11_OOS retrain complete")
        print("=" * 72)
        print(f"  signal       : {result['frozen_signal_path']}")
        print()
        print("  Next steps:")
        print(f"  1) Add to step_d_walk_forward.py SIGNALS list:")
        print(f'     {{"id": "p2_oos", "arm": "P2_BATCH11_OOS",')
        print(f'       "path": "{result["frozen_signal_path"]}"}}')
        print(f"  2) Run F4 comparison (true post-OOS for P2_BATCH11_OOS):")
        print(f"     python3 -u 0316-/phase3/tests/step_d_walk_forward.py \\")
        print(f"       --signals baseline,v2m_p2,p2_oos,ml_xgb_v15 --folds F4")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
