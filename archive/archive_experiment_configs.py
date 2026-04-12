# Archived experiment configs from 0315 windows이사.ipynb Cell 5
# Date: 2026-04-10 (Phase 3 pre-cleanup)
# These configs are preserved for reference but no longer loaded in the notebook.

# Cell 2 - Experiment plans
# All experiment specs: baseline cfgs, overrides, run options.
# Run order: Cell 0 -> 1 -> 2 -> 3.

EXPERIMENT_GA_POP = 160
EXPERIMENT_GA_GEN = 16
EXPERIMENT_STABILITY_SEEDS = 1

IF_B_BREAKOUT_QUALITY_OVERRIDES = {
    "bull_allowed_factor_pool": (
        "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
        "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
        "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
        "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD", "QUAL_ROE_X_BREAKOUT_126",
    ),
    "bull_factor_pool": (
        "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
        "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
        "RSI_TREND", "SMA50_SLOPE", "ADX", "QUAL_ROE_X_BREAKOUT_126",
    ),
    "bull_breakout_presence_pool": (
        "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50", "QUAL_ROE_X_BREAKOUT_126",
    ),
    "bull_breadth_breakout_pool": (
        "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50", "QUAL_ROE_X_BREAKOUT_126",
    ),
}


def make_live_gap_base_cfg() -> Config:
    cfg = make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_QRESEARCH_V62_LIVE_GAP_TEST",
            "precompute_npz_prefix": "precompute_qresearch_v62_live_gap_test",
            "start_panel_date": datetime(2016, 1, 1),
            "end_date": datetime.now(),
            "ohlcv_policy": "UP_TO_D1",
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "historical_universe_repair_mode": "OFF",
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            "enable_meta_search": False,
            "enable_stability_layer": False,
            "ga_lightweight": False,
            "ga_population": 80,
            "ga_generations": 8,
            "stability_seed_runs": 1,
            "write_live_today_top10": True,
            "write_piot_result_log": False,
            "log_piot_result_verbose": False,
        },
    )
    return cfg


LIVE_GAP_BASE_CFG = make_live_gap_base_cfg()
LIVE_GAP_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_LIVE_GAP_CHECK",
    "show_progress": True,
}
LIVE_GAP_EXPERIMENT_SPECS = [
    {
        "name": "LG_NOW_D1",
        "notes": "Current live-gap check with run_main-aligned settings and UP_TO_D1 policy.",
        "cfg_overrides": {},
    },
    {
        "name": "LG_CUTOFF_2025_12_01",
        "notes": "Historical replay cutoff to confirm the same eval/live date split logic on past data.",
        "cfg_overrides": {
            "end_date": datetime(2025, 12, 1),
            "ohlcv_policy": "INCLUDE_TODAY",
        },
    },
]


def make_bull_spread_base_cfg() -> Config:
    return make_cfg_with_overrides(
        LIVE_GAP_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_BULL_SPREAD_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_bull_spread_hi",
            "ga_population": EXPERIMENT_GA_POP,
            "ga_generations": EXPERIMENT_GA_GEN,
            "stability_seed_runs": EXPERIMENT_STABILITY_SEEDS,
            "enable_meta_search": False,
            "enable_stability_layer": False,
            "write_piot_result_log": False,
            "log_piot_result_verbose": False,
        },
    )


BULL_SPREAD_BASE_CFG = make_bull_spread_base_cfg()
BULL_SPREAD_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_BULL_SPREAD_HI",
    "show_progress": True,
}
BULL_SPREAD_EXPERIMENT_SPECS = [
    {
        "name": "BS_BASELINE_HI",
        "notes": "High-budget baseline before any bull-specific strengthening.",
        "cfg_overrides": {},
    },
    {
        "name": "BS_OBJ_STRONG_HI",
        "notes": "High-budget revalidation of the bull spread objective patch only.",
        "cfg_overrides": {
            "bull_min_spread_mix": 0.005,
            "bull_penalty_lambda_spread": 1.10,
            "bull_spread_bonus_threshold": 0.003,
            "bull_spread_bonus_lambda": 1.20,
        },
    },
]


def make_interaction_ab_base_cfg() -> Config:
    return make_cfg_with_overrides(
        BULL_SPREAD_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_INTERACTION_AB_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_interaction_ab_hi",
            "bull_min_spread_mix": 0.005,
            "bull_penalty_lambda_spread": 1.10,
            "bull_spread_bonus_threshold": 0.003,
            "bull_spread_bonus_lambda": 1.20,
        },
    )


INTERACTION_AB_BASE_CFG = make_interaction_ab_base_cfg()
INTERACTION_AB_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_INTERACTION_AB_HI",
    "show_progress": True,
}
INTERACTION_AB_EXPERIMENT_SPECS = [
    {
        "name": "IF_BASE_OBJ_HI",
        "notes": "BS_OBJ_STRONG_HI carried forward as the interaction baseline.",
        "cfg_overrides": {},
    },
    {
        "name": "IF_A_MOM_QUALITY_HI",
        "notes": "Promote momentum-quality/value interaction factors in the bull pool.",
        "cfg_overrides": {
            "bull_allowed_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
                "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD",
                "QUAL_ROE_X_MOM_6M", "VAL_EARN_YIELD_X_MOM_6M", "CF_FCF_YIELD_X_MOM_6M",
            ),
            "bull_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "ADX",
                "QUAL_ROE_X_MOM_6M", "VAL_EARN_YIELD_X_MOM_6M", "CF_FCF_YIELD_X_MOM_6M",
            ),
            "bull_breadth_momentum_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "QUAL_ROE_X_MOM_6M", "VAL_EARN_YIELD_X_MOM_6M", "CF_FCF_YIELD_X_MOM_6M",
            ),
        },
    },
    {
        "name": "IF_B_BREAKOUT_QUALITY_HI",
        "notes": "Promote breakout-quality interaction factors in the bull breakout path.",
        "cfg_overrides": dict(IF_B_BREAKOUT_QUALITY_OVERRIDES),
    },
]


def make_top_quantile_base_cfg() -> Config:
    return make_cfg_with_overrides(
        INTERACTION_AB_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_TOPQ_SWEEP_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_topq_sweep_hi",
            "ga_population": EXPERIMENT_GA_POP,
            "ga_generations": EXPERIMENT_GA_GEN,
            **IF_B_BREAKOUT_QUALITY_OVERRIDES,
        },
    )


TOPQ_SWEEP_BASE_CFG = make_top_quantile_base_cfg()
TOPQ_SWEEP_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_TOPQ_SWEEP_HI",
    "show_progress": True,
}
TOPQ_SWEEP_EXPERIMENT_SPECS = [
    {"name": "TQ_012", "notes": "IF_B baseline with top_quantile=0.12", "cfg_overrides": {"top_quantile": 0.12}},
    {"name": "TQ_015", "notes": "IF_B baseline with top_quantile=0.15", "cfg_overrides": {"top_quantile": 0.15}},
    {"name": "TQ_018", "notes": "IF_B baseline with top_quantile=0.18", "cfg_overrides": {"top_quantile": 0.18}},
    {"name": "TQ_020", "notes": "IF_B baseline with top_quantile=0.20", "cfg_overrides": {"top_quantile": 0.20}},
    {"name": "TQ_022", "notes": "IF_B baseline with top_quantile=0.22", "cfg_overrides": {"top_quantile": 0.22}},
]


def make_topq_revalid_2arm_base_cfg() -> Config:
    """Separate excel/npz prefix so revalidation runs do not overwrite sweep artifacts."""
    return make_cfg_with_overrides(
        TOPQ_SWEEP_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_TOPQ_REVALID_2ARM",
            "precompute_npz_prefix": "precompute_qresearch_v62_topq_revalid_2arm",
            "ga_population": 180,
            "ga_generations": 18,
        },
    )


TOPQ_REVALID_2ARM_BASE_CFG = make_topq_revalid_2arm_base_cfg()
TOPQ_REVALID_2ARM_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_TOPQ_REVALID_2ARM",
    "show_progress": True,
}
TOPQ_REVALID_2ARM_EXPERIMENT_SPECS = [
    {
        "name": "TQ_015",
        "notes": "Revalidation arm A: IF_B + top_quantile=0.15 (balanced pick from sweep).",
        "cfg_overrides": {"top_quantile": 0.15},
    },
    {
        "name": "TQ_012",
        "notes": "Revalidation arm B: IF_B + top_quantile=0.12 (aggressive spread pick from sweep).",
        "cfg_overrides": {"top_quantile": 0.12},
    },
]


def make_stretch60_base_cfg() -> Config:
    return make_cfg_with_overrides(
        TOPQ_REVALID_2ARM_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_STRETCH60_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_stretch60_hi",
            "top_quantile": 0.12,
            "enable_meta_search": False,
            "ga_population": 180,
            "ga_generations": 18,
        },
    )


STRETCH60_BASE_CFG = make_stretch60_base_cfg()
STRETCH60_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_STRETCH60_HI",
    "show_progress": True,
}
STRETCH60_EXPERIMENT_SPECS = [
    {
        "name": "ST60_BASE_TQ012",
        "notes": "Baseline carry-forward: TQ_012 with meta search off.",
        "cfg_overrides": {},
    },
    {
        "name": "ST60_OBJ_SPREAD_UP",
        "notes": "Keep TQ_012 selection and strengthen spread-oriented objective terms.",
        "cfg_overrides": {
            "w_ic1": 0.35,
            "w_ic3": 0.35,
            "w_spread": 0.30,
            "bull_min_spread_mix": 0.0060,
            "bull_penalty_lambda_spread": 1.25,
            "bull_spread_bonus_threshold": 0.0025,
            "bull_spread_bonus_lambda": 1.35,
        },
    },
    {
        "name": "ST60_QUAL_CONSTRAINT_UP",
        "notes": "Keep TQ_012 objective and tighten correlation/diversity constraints to defend IC quality.",
        "cfg_overrides": {
            "factor_corr_penalty_lambda": 0.10,
            "weight_cap": 0.40,
            "entropy_bonus": 0.10,
        },
    },
    {
        "name": "ST60_OBJ_QUAL_COMBO",
        "notes": "Combine spread-objective strengthening with tighter quality constraints for stretch-60 push.",
        "cfg_overrides": {
            "w_ic1": 0.35,
            "w_ic3": 0.35,
            "w_spread": 0.30,
            "bull_min_spread_mix": 0.0060,
            "bull_penalty_lambda_spread": 1.25,
            "bull_spread_bonus_threshold": 0.0025,
            "bull_spread_bonus_lambda": 1.35,
            "factor_corr_penalty_lambda": 0.10,
            "weight_cap": 0.40,
            "entropy_bonus": 0.10,
        },
    },
]


def make_spread_recovery_base_cfg() -> Config:
    return make_cfg_with_overrides(
        STRETCH60_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_SPREAD_RECOVERY_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_spread_recovery_hi",
            "top_quantile": 0.12,
            "enable_meta_search": False,
            "ga_population": 180,
            "ga_generations": 18,
            "w_ic1": 0.35,
            "w_ic3": 0.35,
            "w_spread": 0.30,
            "bull_min_spread_mix": 0.0060,
            "bull_penalty_lambda_spread": 1.25,
            "bull_spread_bonus_threshold": 0.0025,
            "bull_spread_bonus_lambda": 1.35,
            "factor_corr_penalty_lambda": 0.10,
            "weight_cap": 0.40,
            "entropy_bonus": 0.10,
        },
    )


SPREAD_RECOVERY_BASE_CFG = make_spread_recovery_base_cfg()
SPREAD_RECOVERY_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_SPREAD_RECOVERY_HI",
    "show_progress": True,
}
SPREAD_RECOVERY_EXPERIMENT_SPECS = [
    {
        "name": "SR_BASE_COMBO",
        "notes": "Carry forward ST60_OBJ_QUAL_COMBO as the spread-recovery baseline.",
        "cfg_overrides": {},
    },
    {
        "name": "SR_BONUS_EARLY",
        "notes": "Reward bull spread earlier and more strongly while keeping combo quality controls.",
        "cfg_overrides": {
            "bull_spread_bonus_threshold": 0.0020,
            "bull_spread_bonus_lambda": 1.50,
        },
    },
    {
        "name": "SR_FLOOR_RELAX",
        "notes": "Relax spread floor pressure to test whether the combo is over-penalizing spread candidates.",
        "cfg_overrides": {
            "bull_min_spread_mix": 0.0050,
            "bull_penalty_lambda_spread": 1.00,
        },
    },
    {
        "name": "SR_WEIGHT_TILT",
        "notes": "Keep combo constraints and tilt the objective slightly more toward spread.",
        "cfg_overrides": {
            "w_ic1": 0.34,
            "w_ic3": 0.34,
            "w_spread": 0.32,
        },
    },
]


def make_spread_recovery_tune_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SPREAD_RECOVERY_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_SPREAD_RECOVERY_TUNE_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_spread_recovery_tune_hi",
            "w_ic1": 0.34,
            "w_ic3": 0.34,
            "w_spread": 0.32,
        },
    )


SPREAD_RECOVERY_TUNE_BASE_CFG = make_spread_recovery_tune_base_cfg()
SPREAD_RECOVERY_TUNE_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_SPREAD_RECOVERY_TUNE_HI",
    "show_progress": True,
}
SPREAD_RECOVERY_TUNE_EXPERIMENT_SPECS = [
    {
        "name": "SRT_BASE_WEIGHT_TILT",
        "notes": "Carry forward SR_WEIGHT_TILT as the local baseline.",
        "cfg_overrides": {},
    },
    {
        "name": "SRT_WT_MORE_SPREAD",
        "notes": "Increase spread emphasis slightly further from SR_WEIGHT_TILT.",
        "cfg_overrides": {
            "w_ic1": 0.33,
            "w_ic3": 0.33,
            "w_spread": 0.34,
        },
    },
    {
        "name": "SRT_WT_PLUS_BONUS",
        "notes": "Combine SR_WEIGHT_TILT with earlier/stronger bull spread bonus.",
        "cfg_overrides": {
            "bull_spread_bonus_threshold": 0.0020,
            "bull_spread_bonus_lambda": 1.50,
        },
    },
    {
        "name": "SRT_WT_SOFT_BONUS",
        "notes": "Apply a softer early-bonus variant on top of SR_WEIGHT_TILT.",
        "cfg_overrides": {
            "bull_spread_bonus_threshold": 0.0022,
            "bull_spread_bonus_lambda": 1.42,
        },
    },
]


def make_objective_arch_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SPREAD_RECOVERY_TUNE_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_OBJECTIVE_ARCH_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_objective_arch_hi",
            "enable_meta_search": False,
            "top_quantile": 0.12,
            "ga_alpha_floor": 0.22,
            "factor_corr_penalty_lambda": 0.10,
            "entropy_bonus": 0.10,
            "conc_penalty": 0.12,
            "weight_cap": 0.40,
            "bull_min_spread_mix": 0.0060,
            "bull_penalty_lambda_spread": 1.25,
            "bull_spread_bonus_threshold": 0.0025,
            "bull_spread_bonus_lambda": 1.35,
            "w_ic1": 0.34,
            "w_ic3": 0.34,
            "w_spread": 0.32,
        },
    )


OBJECTIVE_ARCH_BASE_CFG = make_objective_arch_base_cfg()
OBJECTIVE_ARCH_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_OBJECTIVE_ARCH_HI",
    "show_progress": True,
}
OBJECTIVE_ARCH_EXPERIMENT_SPECS = [
    {
        "name": "OA_BASE_SR_WEIGHT_TILT",
        "notes": "Current local best baseline with locked SR_WEIGHT_TILT controls.",
        "cfg_overrides": {
            "meta_disabled_template_name": "TPL_BALANCED",
        },
    },
    {
        "name": "OA_TPL_SPREAD_LOCKED",
        "notes": "Use TPL_SPREAD objective family while keeping current scalar controls locked.",
        "cfg_overrides": {
            "meta_disabled_template_name": "TPL_SPREAD",
        },
    },
    {
        "name": "OA_TPL_SPREAD_AGG_LOCKED",
        "notes": "Use TPL_SPREAD_AGG objective family while keeping current scalar controls locked.",
        "cfg_overrides": {
            "meta_disabled_template_name": "TPL_SPREAD_AGG",
        },
    },
    {
        "name": "OA_TPL_IC_DEF_LOCKED",
        "notes": "Use TPL_IC_DEF objective family while keeping current scalar controls locked.",
        "cfg_overrides": {
            "meta_disabled_template_name": "TPL_IC_DEF",
        },
    },
]


def make_hybrid_objective_recovery_base_cfg() -> Config:
    return make_cfg_with_overrides(
        OBJECTIVE_ARCH_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_HYBRID_OBJECTIVE_RECOVERY_HI",
            "precompute_npz_prefix": "precompute_qresearch_v62_hybrid_objective_recovery_hi",
            "meta_disabled_template_name": "TPL_SPREAD",
            "enable_meta_search": False,
        },
    )


HYBRID_OBJECTIVE_RECOVERY_BASE_CFG = make_hybrid_objective_recovery_base_cfg()
HYBRID_OBJECTIVE_RECOVERY_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_HYBRID_OBJECTIVE_RECOVERY_HI",
    "show_progress": True,
}
HYBRID_OBJECTIVE_RECOVERY_EXPERIMENT_SPECS = [
    {
        "name": "HYB_BASE_SPREAD_LOCKED",
        "notes": "Carry forward OA_TPL_SPREAD_LOCKED as the architecture challenger baseline.",
        "cfg_overrides": {},
    },
    {
        "name": "HYB_SPREAD_PLUS_WEIGHTTILT",
        "notes": "Keep TPL_SPREAD architecture and partially restore weight-tilt balance toward IC.",
        "cfg_overrides": {
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
        },
    },
    {
        "name": "HYB_SPREAD_PLUS_ICDEF",
        "notes": "Keep TPL_SPREAD architecture and strengthen IC-defense quality constraints.",
        "cfg_overrides": {
            "factor_corr_penalty_lambda": 0.12,
            "weight_cap": 0.38,
            "entropy_bonus": 0.10,
        },
    },
    {
        "name": "HYB_SPREAD_SOFTENED",
        "notes": "Keep TPL_SPREAD architecture but soften spread pressure to recover IC quality.",
        "cfg_overrides": {
            "bull_min_spread_mix": 0.0050,
            "bull_penalty_lambda_spread": 1.00,
            "bull_spread_bonus_threshold": 0.0030,
            "bull_spread_bonus_lambda": 1.20,
        },
    },
]

# =============================================================================
# Signal Architecture Round v1
# Goal: expand interaction factor pool (4 original → 8) and test whether
#       new interaction types break the MeanIC ceiling and improve Bull spread.
# Budget: 1/3 screening (ga_pop=60, ga_gen=6).  Winner must be revalidated
#         at full budget (180/18) before any baseline promotion.
# Precompute: new prefix forces rebuild with 8-factor INTERACTION_FACTOR_NAMES.
# =============================================================================
SIGNAL_ARCH_GA_POP = 60
SIGNAL_ARCH_GA_GEN = 6


def make_signal_arch_base_cfg() -> Config:
    return make_cfg_with_overrides(
        HYBRID_OBJECTIVE_RECOVERY_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_SIGNAL_ARCH_V1",
            "precompute_npz_prefix": "precompute_qresearch_v62_signal_arch_v1",
            "ga_population": SIGNAL_ARCH_GA_POP,
            "ga_generations": SIGNAL_ARCH_GA_GEN,
            "enable_meta_search": False,
            "enable_stability_layer": False,
        },
    )


SIGNAL_ARCH_BASE_CFG = make_signal_arch_base_cfg()
SIGNAL_ARCH_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_SIGNAL_ARCH_V1",
    "show_progress": True,
}
SIGNAL_ARCH_EXPERIMENT_SPECS = [
    {
        "name": "SA_CTRL",
        "notes": (
            "Control: inherited IF_B_BREAKOUT_QUALITY pools as-is. "
            "New factors (VAL_BOOK2PRICE_X_MOM_6M etc.) exist in INDICATOR_NAMES "
            "but are NOT added to any bull pool. Confirms baseline stability after precompute rebuild."
        ),
        "cfg_overrides": {},
    },
    {
        "name": "SA_VAL_QUALITY_MOM",
        "notes": (
            "Test: add VAL_BOOK2PRICE_X_MOM_6M (value×momentum) and "
            "MOM_12M_EX1M_X_QUAL_ROE (12M quality momentum) to the bull pool. "
            "Hypothesis: orthogonal value-quality signals improve IC without sacrificing spread."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
                "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD", "QUAL_ROE_X_BREAKOUT_126",
                "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
            ),
            "bull_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "ADX", "QUAL_ROE_X_BREAKOUT_126",
                "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
            ),
        },
    },
    {
        "name": "SA_BREAKOUT_QUALITY_EXT",
        "notes": (
            "Test: add BREAKOUT_252_X_CF_FCF_YIELD (long-term quality breakout) to the bull pool, "
            "including breakout_presence and breadth_breakout pools. "
            "Hypothesis: FCF-quality breakout filter sharpens bull tail separation."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
                "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD", "QUAL_ROE_X_BREAKOUT_126",
                "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "ADX", "QUAL_ROE_X_BREAKOUT_126",
                "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_breakout_presence_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_breadth_breakout_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
        },
    },
]


# =============================================================================
# Signal Architecture Revalidation
# Goal: full-budget revalidation of SA_VAL_QUALITY_MOM winner from screening round.
# Budget: 180/18 (full).  Single arm only - pass/fail determines baseline promotion.
# Precompute: same prefix as SIGNAL_ARCH (reuses existing 36-factor cache).
# =============================================================================
SIGNAL_ARCH_REVALID_GA_POP = 180
SIGNAL_ARCH_REVALID_GA_GEN = 18


def make_signal_arch_revalid_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SIGNAL_ARCH_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_SIGNAL_ARCH_REVALID",
            "ga_population": SIGNAL_ARCH_REVALID_GA_POP,
            "ga_generations": SIGNAL_ARCH_REVALID_GA_GEN,
            "enable_stability_layer": True,
            "stability_seed_runs": 3,
            "stability_top_n_seeds": 2,
        },
    )


SIGNAL_ARCH_REVALID_BASE_CFG = make_signal_arch_revalid_base_cfg()
SIGNAL_ARCH_REVALID_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_SIGNAL_ARCH_REVALID",
    "show_progress": True,
}
SIGNAL_ARCH_REVALID_EXPERIMENT_SPECS = [
    {
        "name": "SA_VAL_QUALITY_MOM_REVALID",
        "notes": (
            "Full-budget revalidation of SA_VAL_QUALITY_MOM. "
            "Adds VAL_BOOK2PRICE_X_MOM_6M and MOM_12M_EX1M_X_QUAL_ROE to bull pool. "
            "Screening result: MeanIC=0.0173(96%), Spread=0.0110(PASS), PosIC=0.573(PASS). "
            "Stability layer ON (3 seeds) for robustness check."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "MACD", "SMA_CROSS", "ADX", "ROC",
                "VAL_EARN_YIELD", "QUAL_ROE", "CF_FCF_YIELD", "QUAL_ROE_X_BREAKOUT_126",
                "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
            ),
            "bull_factor_pool": (
                "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
                "BREAKOUT_252", "BREAKOUT_126", "DIST_FROM_SMA50", "HIGH_20_BREAK",
                "RSI_TREND", "SMA50_SLOPE", "ADX", "QUAL_ROE_X_BREAKOUT_126",
                "VAL_BOOK2PRICE_X_MOM_6M", "MOM_12M_EX1M_X_QUAL_ROE",
            ),
        },
    },
]


# =============================================================================
# Spread Push Round
# Context: SA_VAL_QUALITY_MOM_REVALID achieved:
#   MeanIC=0.0248 (60% Stretch PASS), Spread=0.0145 (need 0.018)
#   MeanIC headroom above 60% target: only 0.0008 (3.5%)
#   Spread gap to 60% target: 0.0035 (24.4%)
#
# Strategy: factor-pool-driven approach, NOT objective tilt.
#   MeanIC headroom is too thin for aggressive objective rebalancing.
#   Instead, add spread-specializing factors and let GA find combinations
#   that improve tail separation without sacrificing IC.
#
# Budget: stability ON (same as revalid, ~4800 evals per arm).
#   Small arm count (3) so total cost is manageable.
#   Results are promotion-grade if pass criteria are met.
# =============================================================================

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


def make_spread_push_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SIGNAL_ARCH_REVALID_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_SPREAD_PUSH",
            "ga_population": 100,
            "ga_generations": 10,
            "enable_stability_layer": False,
        },
    )


SPREAD_PUSH_BASE_CFG = make_spread_push_base_cfg()
SPREAD_PUSH_RUN_OPTIONS = {
    "write_reports": True,
    "report_prefix": "SP500_SPREAD_PUSH",
    "show_progress": True,
}
SPREAD_PUSH_EXPERIMENT_SPECS = [
    {
        "name": "SP_CTRL_VQM",
        "notes": (
            "Control: exact SA_VAL_QUALITY_MOM_REVALID config. "
            "Confirms revalid result reproducibility under same stability pipeline."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
        },
    },
    {
        "name": "SP_COMBINED_FACTORS",
        "notes": (
            "Merge SA_VAL_QUALITY_MOM + SA_BREAKOUT_QUALITY_EXT factor pools. "
            "Add BREAKOUT_252_X_CF_FCF_YIELD to bull_allowed, bull_factor, "
            "breakout_presence, and breadth_breakout pools. "
            "Hypothesis: quality-breakout factor sharpens tail separation (spread) "
            "while VAL_QUALITY_MOM factors maintain IC."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE + (
                "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE + (
                "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_breakout_presence_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_breadth_breakout_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
        },
    },
    {
        "name": "SP_COMBINED_PLUS_LEV",
        "notes": (
            "SP_COMBINED_FACTORS + LEV_DEBT_EQUITY_X_MOM_6M in bull pool. "
            "Full 8-interaction-factor deployment in bull regime. "
            "Hypothesis: low-leverage×momentum screens out fragile momentum stocks, "
            "improving bottom-quintile identification and thus spread."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE + (
                "BREAKOUT_252_X_CF_FCF_YIELD", "LEV_DEBT_EQUITY_X_MOM_6M",
            ),
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE + (
                "BREAKOUT_252_X_CF_FCF_YIELD", "LEV_DEBT_EQUITY_X_MOM_6M",
            ),
            "bull_breakout_presence_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
            "bull_breadth_breakout_pool": (
                "BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK", "DIST_FROM_SMA50",
                "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
            ),
        },
    },
]


# ──────────────────────────────────────────────────────────
# IC Floor + Spread Push (Option A)
# Strategy: harden bull IC floor so GA cannot sacrifice IC,
# then safely push spread objective weight upward.
# Step 1 screening → winner proceeds to stability revalidation.
# ──────────────────────────────────────────────────────────

IC_FLOOR_SPREAD_GA_POP = 100
IC_FLOOR_SPREAD_GA_GEN = 10


def make_ic_floor_spread_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SIGNAL_ARCH_REVALID_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_IC_FLOOR_SPREAD",
            "ga_population": IC_FLOOR_SPREAD_GA_POP,
            "ga_generations": IC_FLOOR_SPREAD_GA_GEN,
            "enable_stability_layer": False,
        },
    )


IC_FLOOR_SPREAD_BASE_CFG = make_ic_floor_spread_base_cfg()

IC_FLOOR_SPREAD_EXPERIMENT_SPECS = [
    {
        "name": "IFS_CTRL",
        "notes": (
            "Control: SA_VAL_QUALITY_MOM baseline with current objective params. "
            "Same factor pool, same w_ic/w_spread, same bull floor defaults. "
            "Reproducibility reference for same-batch relative comparison."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
        },
    },
    {
        "name": "IFS_IC_GUARD_MID",
        "notes": (
            "Moderate IC floor guard + spread tilt. "
            "bull_min_ic raised to 0.015 (from 0.01), penalty_lambda_ic doubled to 1.20. "
            "w_spread 0.40→0.44, spread_bonus_lambda 1.20→1.50. "
            "Hypothesis: IC floor prevents degradation seen in SPREAD_PUSH, "
            "allowing safe spread weight increase."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "bull_min_ic_1m": 0.015,
            "bull_min_ic_3m": 0.015,
            "bull_penalty_lambda_ic": 1.20,
            "w_ic1": 0.28,
            "w_ic3": 0.28,
            "w_spread": 0.44,
            "bull_spread_bonus_lambda": 1.50,
        },
    },
    {
        "name": "IFS_IC_GUARD_AGG",
        "notes": (
            "Aggressive IC floor guard + strong spread push. "
            "bull_min_ic raised to 0.018 (MainPerformance threshold), "
            "penalty_lambda_ic 2.5x to 1.50. "
            "w_spread 0.40→0.50, spread_bonus_lambda 1.20→1.80, threshold 0.003→0.004. "
            "Hypothesis: very hard IC guardrail allows maximum spread optimization "
            "without crossing the MainPerformance IC floor."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "bull_min_ic_1m": 0.018,
            "bull_min_ic_3m": 0.018,
            "bull_penalty_lambda_ic": 1.50,
            "w_ic1": 0.25,
            "w_ic3": 0.25,
            "w_spread": 0.50,
            "bull_spread_bonus_threshold": 0.004,
            "bull_spread_bonus_lambda": 1.80,
        },
    },
]

IC_FLOOR_SPREAD_RUN_OPTIONS = dict(
    write_reports=True,
    report_prefix="IC_FLOOR_SPREAD",
    show_progress=True,
)


# ──────────────────────────────────────────────────────────
# IFS_IC_GUARD_MID Revalidation (Step 2)
# Winner from IC_FLOOR_SPREAD screening batch.
# Objective: bull_min_ic=0.015, penalty_lambda_ic=1.20,
#            w_spread=0.44, spread_bonus_lambda=1.50
# Half-budget stability: fast/refine each halved, seed_runs=3 kept.
# ──────────────────────────────────────────────────────────

IFS_MID_REVALID_GA_POP = 90      # 180 → 90
IFS_MID_REVALID_GA_GEN = 9       # 18  → 9


def make_ifs_mid_revalid_base_cfg() -> Config:
    return make_cfg_with_overrides(
        IC_FLOOR_SPREAD_BASE_CFG,
        {
            "excel_prefix": "SP500_QRESEARCH_V62_IFS_MID_REVALID",
            "ga_population": IFS_MID_REVALID_GA_POP,
            "ga_generations": IFS_MID_REVALID_GA_GEN,
            "enable_stability_layer": True,
            "stability_seed_runs": 3,
            "stability_top_n_seeds": 2,
            "stability_fast_population": 50,    # 100 → 50
            "stability_fast_generations": 4,    # 8   → 4
            "stability_refine_population": 100, # 200 → 100
            "stability_refine_generations": 6,  # 12  → 6
            # IFS_IC_GUARD_MID objective params inherited via cfg_overrides below
        },
    )


IFS_MID_REVALID_BASE_CFG = make_ifs_mid_revalid_base_cfg()

IFS_MID_REVALID_EXPERIMENT_SPECS = [
    {
        "name": "IFS_MID_REVALID",
        "notes": (
            "Step 2 stability revalidation of IFS_IC_GUARD_MID winner. "
            "Screening result: MeanIC=0.02417(PASS), Spread=0.01204(PASS), "
            "MainPerformance PASS, beat CTRL by MeanIC+62%/Spread+57%. "
            "Objective: bull_min_ic_1m/3m=0.015, penalty_lambda_ic=1.20, "
            "w_ic1/3=0.28, w_spread=0.44, spread_bonus_lambda=1.50. "
            "Half-budget stability to balance quality vs run time."
        ),
        "cfg_overrides": {
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "bull_min_ic_1m": 0.015,
            "bull_min_ic_3m": 0.015,
            "bull_penalty_lambda_ic": 1.20,
            "w_ic1": 0.28,
            "w_ic3": 0.28,
            "w_spread": 0.44,
            "bull_spread_bonus_lambda": 1.50,
        },
    },
]

IFS_MID_REVALID_RUN_OPTIONS = dict(
    write_reports=True,
    report_prefix="IFS_MID_REVALID",
    show_progress=True,
)


# =============================================================================
# Phase 2 Batch 01: Portfolio Parameter Sweep
# Signal: frozen SA_VAL_QUALITY_MOM_REVALID (Phase 1 best)
# Approach: 1 GA run → N portfolio configs → compare net performance
# Cost model: commission 10bps + slippage 5bps per side
# =============================================================================

def make_phase2_batch01_base_cfg() -> Config:
    return make_cfg_with_overrides(
        SIGNAL_ARCH_REVALID_BASE_CFG,
        {
            "excel_prefix": "SP500_PHASE2_BATCH01",
        },
    )

PHASE2_BATCH01_BASE_CFG = make_phase2_batch01_base_cfg()

PHASE2_BATCH01_COMMISSION_BPS = 10.0
PHASE2_BATCH01_SLIPPAGE_BPS = 5.0

PHASE2_BATCH01_PORTFOLIO_CONFIGS = [
    {
        "name": "P2_BASELINE",
        "notes": "Phase 1 default: top_n=20, score_proportional, max_weight=0.20",
        "overrides": {},
    },
    {
        "name": "P2_EQUAL_WEIGHT",
        "notes": "Equal weight: same top_n=20, lower concentration risk",
        "overrides": {
            "portfolio_weight_mode": "equal",
        },
    },
    {
        "name": "P2_TIGHT_TOP15",
        "notes": "Higher conviction: top_n=15, max_weight=0.10, score_proportional",
        "overrides": {
            "portfolio_top_n": 15,
            "portfolio_hold_buffer_n": 22,
            "portfolio_max_weight_cap": 0.10,
        },
    },
    {
        "name": "P2_WIDE_TOP30",
        "notes": "Diversification: top_n=30, max_weight=0.08, score_proportional",
        "overrides": {
            "portfolio_top_n": 30,
            "portfolio_hold_buffer_n": 40,
            "portfolio_max_weight_cap": 0.08,
        },
    },
    {
        "name": "P2_SOFTMAX_TEMP5",
        "notes": "Smoother weights via softmax temp=5.0, top_n=20",
        "overrides": {
            "portfolio_weight_mode": "softmax",
            "portfolio_softmax_temp": 5.0,
        },
    },
]

# GA seed reference (not enforced - use_random_seed=True for robustness)
PHASE2_FROZEN_GA_SEED = 3139437245

PHASE2_BATCH01_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# ============================================================
# Phase 2 Batch 02: Regime-Adaptive Portfolio
# ============================================================
def make_phase2_batch02_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return c

PHASE2_BATCH02_BASE_CFG = make_phase2_batch02_base_cfg()
PHASE2_BATCH02_COMMISSION_BPS = 10.0
PHASE2_BATCH02_SLIPPAGE_BPS = 5.0

PHASE2_BATCH02_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B2_CTRL_WIDE30",
        "notes": "Control: BATCH01 winner P2_WIDE_TOP30 (no regime-adaptive)",
        "overrides": {
            "regime_adaptive_portfolio": False,
            "portfolio_top_n": 30,
            "portfolio_max_weight_cap": 0.08,
        },
    },
    {
        "name": "P2B2_RA_CONSERVATIVE",
        "notes": "Regime-adaptive: BULL=30/0%, SIDE=20/15%, DEF=10/40%",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "regime_bull_top_n": 30,
            "regime_bull_cash_pct": 0.0,
            "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20,
            "regime_side_cash_pct": 0.15,
            "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 10,
            "regime_defensive_cash_pct": 0.40,
            "regime_defensive_max_weight_cap": 0.15,
        },
    },
    {
        "name": "P2B2_RA_MODERATE",
        "notes": "Regime-adaptive moderate: BULL=30/0%, SIDE=25/10%, DEF=15/25%",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "regime_bull_top_n": 30,
            "regime_bull_cash_pct": 0.0,
            "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 25,
            "regime_side_cash_pct": 0.10,
            "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 15,
            "regime_defensive_cash_pct": 0.25,
            "regime_defensive_max_weight_cap": 0.12,
        },
    },
    {
        "name": "P2B2_RA_AGGRESSIVE_DEF",
        "notes": "Strong defensive: BULL=30/0%, SIDE=20/20%, DEF=8/50%",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "regime_bull_top_n": 30,
            "regime_bull_cash_pct": 0.0,
            "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20,
            "regime_side_cash_pct": 0.20,
            "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 8,
            "regime_defensive_cash_pct": 0.50,
            "regime_defensive_max_weight_cap": 0.20,
        },
    },
    {
        "name": "P2B2_RA_BULL_PUSH",
        "notes": "Bull aggressive + defensive: BULL=35/0%, SIDE=25/10%, DEF=10/35%",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "regime_bull_top_n": 35,
            "regime_bull_cash_pct": 0.0,
            "regime_bull_max_weight_cap": 0.06,
            "regime_side_top_n": 25,
            "regime_side_cash_pct": 0.10,
            "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 10,
            "regime_defensive_cash_pct": 0.35,
            "regime_defensive_max_weight_cap": 0.15,
        },
    },
]

PHASE2_BATCH02_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# ============================================================
# Phase 2 Batch 03: VIX Fast-Regime + Turnover tuning
# ============================================================
def make_phase2_batch03_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return c

PHASE2_BATCH03_BASE_CFG = make_phase2_batch03_base_cfg()
PHASE2_BATCH03_COMMISSION_BPS = 10.0
PHASE2_BATCH03_SLIPPAGE_BPS = 5.0

PHASE2_BATCH03_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B3_CTRL_WIDE30",
        "notes": "Control: no regime-adaptive, no VIX, WIDE_TOP30 baseline",
        "overrides": {
            "regime_adaptive_portfolio": False,
            "enable_vix_fast_regime": False,
            "portfolio_top_n": 30,
            "portfolio_max_weight_cap": 0.08,
        },
    },
    {
        "name": "P2B3_SMA_MODERATE",
        "notes": "SMA200 regime (BATCH02 best): B30/S25/D15, 0/10/25% cash",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "enable_vix_fast_regime": False,
            "regime_bull_top_n": 30, "regime_bull_cash_pct": 0.0, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 25, "regime_side_cash_pct": 0.10, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 15, "regime_defensive_cash_pct": 0.25, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    {
        "name": "P2B3_VIX_MODERATE",
        "notes": "VIX regime: VIX<20=BULL, 20-30=SIDE, >30=DEF. B30/S25/D15, 0/10/25% cash",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "enable_vix_fast_regime": True,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 30.0,
            "vix_smoothing_window": 5,
            "regime_bull_top_n": 30, "regime_bull_cash_pct": 0.0, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 25, "regime_side_cash_pct": 0.10, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 15, "regime_defensive_cash_pct": 0.25, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    {
        "name": "P2B3_VIX_AGGRESSIVE",
        "notes": "VIX regime aggressive defense: VIX<18=BULL, 18-25=SIDE, >25=DEF. B30/S20/D10, 0/15/40% cash",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "enable_vix_fast_regime": True,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 25.0,
            "vix_smoothing_window": 5,
            "regime_bull_top_n": 30, "regime_bull_cash_pct": 0.0, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_cash_pct": 0.15, "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 10, "regime_defensive_cash_pct": 0.40, "regime_defensive_max_weight_cap": 0.15,
        },
    },
    {
        "name": "P2B3_VIX_TIGHT_TURNOVER",
        "notes": "VIX moderate + turnover optimization: higher hold buffer, higher entry margin",
        "overrides": {
            "regime_adaptive_portfolio": True,
            "enable_vix_fast_regime": True,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 30.0,
            "vix_smoothing_window": 5,
            "regime_bull_top_n": 30, "regime_bull_cash_pct": 0.0, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 25, "regime_side_cash_pct": 0.10, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 15, "regime_defensive_cash_pct": 0.25, "regime_defensive_max_weight_cap": 0.12,
            "portfolio_hold_buffer_n": 40,
            "portfolio_entry_score_margin": 5.0,
        },
    },
]

PHASE2_BATCH03_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# PHASE 2 BATCH04 — Live-Ready Portfolio
# Goal: Combine CTRL_WIDE30 high-CAGR + TIGHT_TURNOVER + Circuit-Breaker(VIX)
# Phase-3 prep: order-book simulation enabled (Action/Close/Shares per rebalance)
# =============================================================================

def make_phase2_batch04_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH04",
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH04_BASE_CFG = make_phase2_batch04_base_cfg()
PHASE2_BATCH04_COMMISSION_BPS = 10.0
PHASE2_BATCH04_SLIPPAGE_BPS = 5.0

# Shared TIGHT_TURNOVER base overrides (hold_buffer_n=40, entry_score_margin=5.0)
_B4_TIGHT = {
    "regime_adaptive_portfolio": False,
    "enable_vix_fast_regime": False,
    "portfolio_top_n": 30,
    "portfolio_max_weight_cap": 0.08,
    "portfolio_hold_buffer_n": 40,
    "portfolio_entry_score_margin": 5.0,
}

PHASE2_BATCH04_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B4_CTRL_TIGHT",
        "notes": "CTRL_WIDE30 + TIGHT_TURNOVER: no regime, hold_buf=40, margin=5.0",
        "overrides": {**_B4_TIGHT},
    },
    {
        "name": "P2B4_CTRL_TIGHT_BUF50",
        "notes": "TIGHT with higher buffer: hold_buf=50, margin=8.0. Targets Turnover Stretch <=18x",
        "overrides": {**_B4_TIGHT,
            "portfolio_hold_buffer_n": 50,
            "portfolio_entry_score_margin": 8.0,
        },
    },
    {
        "name": "P2B4_CTRL_TIGHT_CB35",
        "notes": "TIGHT + Circuit-Breaker VIX>35 → 50% cash. Targets MDD Stretch <=25%",
        "overrides": {**_B4_TIGHT,
            "enable_vix_fast_regime": True,
            "enable_circuit_breaker": True,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 40.0,
        },
    },
    {
        "name": "P2B4_CTRL_TIGHT_CB30",
        "notes": "TIGHT + Circuit-Breaker VIX>30 → 40% cash. Earlier trigger, more protection vs CAGR",
        "overrides": {**_B4_TIGHT,
            "enable_vix_fast_regime": True,
            "enable_circuit_breaker": True,
            "circuit_breaker_vix_threshold": 30.0,
            "circuit_breaker_cash_pct": 0.40,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 40.0,
        },
    },
    {
        "name": "P2B4_TIGHT_BUF50_CB35",
        "notes": "BUF50 + CB35 combined: targets Turnover Stretch + MDD Stretch simultaneously",
        "overrides": {**_B4_TIGHT,
            "portfolio_hold_buffer_n": 50,
            "portfolio_entry_score_margin": 8.0,
            "enable_vix_fast_regime": True,
            "enable_circuit_breaker": True,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 40.0,
        },
    },
]

PHASE2_BATCH04_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# PHASE 2 BATCH05 — 3x Signal Budget + CB Bug-Fixed BATCH04 Configs
# Phase 1 signal quality revalidation with 3x compute:
#   Outer GA: pop=300, gen=27  (1.7x vs 180/18)
#   Stability: seed_runs=9, top_n_seeds=4  (3x vs 3/2)
#   Stability fast: pop=130, gen=10  (1.6x vs 100/8)
#   Stability refine: pop=270, gen=16  (1.5x vs 200/12)
#   Total: ~37K eval vs ~10K  ≈ 3.5x budget
# Portfolio: same 5 arms as BATCH04 (with per-config CB VIX bug fixed)
# =============================================================================

PHASE2_BATCH05_GA_POP = 300
PHASE2_BATCH05_GA_GEN = 27

def make_phase2_batch05_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH05",
        # 3x outer GA budget
        "ga_population": PHASE2_BATCH05_GA_POP,
        "ga_generations": PHASE2_BATCH05_GA_GEN,
        # 3x stability layer
        "stability_seed_runs": 9,
        "stability_top_n_seeds": 4,
        "stability_fast_population": 130,
        "stability_fast_generations": 10,
        "stability_refine_population": 270,
        "stability_refine_generations": 16,
        # Order book enabled
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH05_BASE_CFG = make_phase2_batch05_base_cfg()
PHASE2_BATCH05_COMMISSION_BPS = 10.0
PHASE2_BATCH05_SLIPPAGE_BPS = 5.0

# Reuse BATCH04 portfolio configs — CB bug is now fixed in run_phase2_portfolio_sweep
PHASE2_BATCH05_PORTFOLIO_CONFIGS = PHASE2_BATCH04_PORTFOLIO_CONFIGS

PHASE2_BATCH05_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# PHASE 2 BATCH06 — Risk-Adjusted Engine Revision
#   GA budget: pop-heavy, moderate-gen (initial exploration >>>)
#     Outer GA: pop=500, gen=15 (used when stability disabled)
#     Stability: seed_runs=12, top_n_seeds=5
#     Stability fast: pop=200, gen=7  (subprocess-isolated per seed)
#     Stability refine: pop=300, gen=12
#     Total: ~20.4K eval  (fast 16,800 + refine 3,600)
#   Fitness: enable_fitness_risk_penalty=True (downside_vol + neg_spread_ratio)
#   Portfolio: Top-N reduced (20/25/30) + BUF/Margin sweeps
# =============================================================================

PHASE2_BATCH06_GA_POP = 500
PHASE2_BATCH06_GA_GEN = 15

def make_phase2_batch06_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH06",
        # Pop-heavy GA budget
        "ga_population": PHASE2_BATCH06_GA_POP,
        "ga_generations": PHASE2_BATCH06_GA_GEN,
        # Stability layer — subprocess-isolated fast seeds reclaim memory before refine
        "stability_seed_runs": 12,
        "stability_top_n_seeds": 5,
        "stability_fast_population": 200,
        "stability_fast_generations": 7,
        "stability_refine_population": 300,
        "stability_refine_generations": 12,
        # Risk penalty enabled
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        # Order book
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH06_BASE_CFG = make_phase2_batch06_base_cfg()
PHASE2_BATCH06_COMMISSION_BPS = 10.0
PHASE2_BATCH06_SLIPPAGE_BPS = 5.0

_B6_BASE = {
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "vix_bull_threshold": 20.0,
    "vix_defensive_threshold": 40.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
}

PHASE2_BATCH06_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B6_CTRL_TIGHT_T20",
        "notes": "Top20 + tight buffer: concentrated high-conviction",
        "overrides": {**_B6_BASE,
            "portfolio_top_n": 20,
            "portfolio_hold_buffer_n": 30,
            "portfolio_entry_score_margin": 5.0,
        },
    },
    {
        "name": "P2B6_CTRL_BUF50_T20",
        "notes": "Top20 + wider buffer: low turnover concentrated",
        "overrides": {**_B6_BASE,
            "portfolio_top_n": 20,
            "portfolio_hold_buffer_n": 40,
            "portfolio_entry_score_margin": 8.0,
        },
    },
    {
        "name": "P2B6_CTRL_TIGHT_T30",
        "notes": "Top30 + tight buffer: BATCH04 reproduction with risk penalty GA",
        "overrides": {**_B6_BASE,
            "portfolio_top_n": 30,
            "portfolio_hold_buffer_n": 40,
            "portfolio_entry_score_margin": 5.0,
        },
    },
    {
        "name": "P2B6_CTRL_BUF50_T30",
        "notes": "Top30 + wide buffer: BATCH05 best arm reproduction",
        "overrides": {**_B6_BASE,
            "portfolio_top_n": 30,
            "portfolio_hold_buffer_n": 50,
            "portfolio_entry_score_margin": 8.0,
        },
    },
    {
        "name": "P2B6_CTRL_BUF50_T25",
        "notes": "Top25 + medium buffer: middle-ground exploration",
        "overrides": {**_B6_BASE,
            "portfolio_top_n": 25,
            "portfolio_hold_buffer_n": 45,
            "portfolio_entry_score_margin": 7.0,
        },
    },
]

PHASE2_BATCH06_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# PHASE 2 BATCH07 — MDD-Focused Defense Revision
#   Goal: Reduce MDD from 36.7% → ≤32% (Target tier) while preserving CAGR ≥15%
#   Strategy: faster regime transition + higher defensive cash + aggressive CB
#   Signal: reuse BATCH06 GA (risk penalty, pop-heavy) with fast gen bumped 7→9
#   GA budget:
#     Stability fast: pop=200, gen=9  (was 7 — saturation analysis shows headroom)
#     Stability refine: pop=300, gen=12
#     Total: ~25.2K eval  (fast 12x200x9=21,600  refine 300x12=3,600)
#   Portfolio: 5 arms — VIX threshold / cash profile / CB aggressiveness sweeps
# =============================================================================

PHASE2_BATCH07_GA_POP = 500
PHASE2_BATCH07_GA_GEN = 15

def make_phase2_batch07_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH07",
        "ga_population": PHASE2_BATCH07_GA_POP,
        "ga_generations": PHASE2_BATCH07_GA_GEN,
        # Stability — fast gen bumped 7→9 per saturation analysis
        "stability_seed_runs": 12,
        "stability_top_n_seeds": 5,
        "stability_fast_population": 200,
        "stability_fast_generations": 9,
        "stability_refine_population": 300,
        "stability_refine_generations": 12,
        # Risk penalty (same as BATCH06)
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        # Order book
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH07_BASE_CFG = make_phase2_batch07_base_cfg()
PHASE2_BATCH07_COMMISSION_BPS = 10.0
PHASE2_BATCH07_SLIPPAGE_BPS = 5.0

# --- MDD defense arms ---
# All arms use BATCH06-best structure (BUF50_T30) as base,
# then vary regime thresholds and cash profiles for MDD reduction.
_B7_PORTFOLIO_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
}

PHASE2_BATCH07_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B7_CTRL_B6BEST",
        "notes": "BATCH06 best (BUF50_T30) exact reproduction as control",
        "overrides": {**_B7_PORTFOLIO_BASE,
            "vix_bull_threshold": 20.0,
            "vix_defensive_threshold": 40.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.15,
            "regime_defensive_cash_pct": 0.40,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 25, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 15, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    {
        "name": "P2B7_FAST_DEF30",
        "notes": "Earlier defensive entry (VIX>=30 vs 40) + side cash 25%",
        "overrides": {**_B7_PORTFOLIO_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    {
        "name": "P2B7_HIGH_CASH_DEF",
        "notes": "Heavy cash in defensive (60%) + aggressive CB (VIX>=30 -> 70% cash)",
        "overrides": {**_B7_PORTFOLIO_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 30.0,
            "circuit_breaker_cash_pct": 0.70,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.60,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
        },
    },
    {
        "name": "P2B7_GRADUAL_SHIELD",
        "notes": "Gradual cash escalation: Bull5%->Side30%->Def65% + CB VIX>=28 -> 75%",
        "overrides": {**_B7_PORTFOLIO_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 28.0,
            "circuit_breaker_vix_threshold": 28.0,
            "circuit_breaker_cash_pct": 0.75,
            "regime_bull_cash_pct": 0.05,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.65,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 18, "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
        },
    },
    {
        "name": "P2B7_MAX_PROTECT",
        "notes": "Maximum MDD protection: CB VIX>=25->80% cash, Def70%, fewer stocks",
        "overrides": {**_B7_PORTFOLIO_BASE,
            "vix_bull_threshold": 16.0,
            "vix_defensive_threshold": 25.0,
            "circuit_breaker_vix_threshold": 25.0,
            "circuit_breaker_cash_pct": 0.80,
            "regime_bull_cash_pct": 0.05,
            "regime_side_cash_pct": 0.35,
            "regime_defensive_cash_pct": 0.70,
            "regime_bull_top_n": 28, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 8, "regime_defensive_max_weight_cap": 0.20,
        },
    },
]

PHASE2_BATCH07_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# BATCH08 — Frozen-Signal MDD Fine-Tuning
#   GA identical to BATCH07 (run once, then reuse via FROZEN_SIGNAL_PATH).
#   Dense sweep around FAST_DEF30 sweet-spot (BATCH07 MDD winner: 0.326).
#   Goal: close the 0.6%p gap to MDD Target (0.32) with minimal CAGR sacrifice.
# =============================================================================

PHASE2_BATCH08_GA_POP = 500
PHASE2_BATCH08_GA_GEN = 15

def make_phase2_batch08_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH08",
        "ga_population": PHASE2_BATCH08_GA_POP,
        "ga_generations": PHASE2_BATCH08_GA_GEN,
        "stability_seed_runs": 12,
        "stability_top_n_seeds": 5,
        "stability_fast_population": 200,
        "stability_fast_generations": 9,
        "stability_refine_population": 300,
        "stability_refine_generations": 12,
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH08_BASE_CFG = make_phase2_batch08_base_cfg()
PHASE2_BATCH08_COMMISSION_BPS = 10.0
PHASE2_BATCH08_SLIPPAGE_BPS = 5.0

_B8_PORT_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
}

PHASE2_BATCH08_PORTFOLIO_CONFIGS = [
    # ─── Arm 0: CTRL — exact FAST_DEF30 reproduction ───
    {
        "name": "P2B8_CTRL",
        "notes": "BATCH07 FAST_DEF30 control (MDD=0.326)",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 1: earlier defensive (VIX>=28) ───
    {
        "name": "P2B8_DEF28",
        "notes": "VIX def 30→28, slightly earlier defensive transition",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 28.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 2: VIX def=29 midpoint ───
    {
        "name": "P2B8_DEF29",
        "notes": "VIX def=29 midpoint between 28 and 30",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 29.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 3: more side cash (25→28%) ───
    {
        "name": "P2B8_SIDE28",
        "notes": "Side cash 25%→28% for earlier risk reduction",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 4: earlier CB trigger (35→32) ───
    {
        "name": "P2B8_CB32",
        "notes": "CB VIX 35→32, earlier circuit-breaker activation",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 32.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 5: stronger CB cash (50→60%) ───
    {
        "name": "P2B8_CB60",
        "notes": "CB cash 50%→60% for deeper drawdown cushion",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.60,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 6: more def cash (50→55%) ───
    {
        "name": "P2B8_DCASH55",
        "notes": "Defensive cash 50%→55%, slight extra cushion",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.55,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 7: combo — def28 + side28 + CB32 (triple squeeze) ───
    {
        "name": "P2B8_TRIPLE",
        "notes": "def=28, side=28%, CB=32 — triple-squeeze for MDD",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 28.0,
            "circuit_breaker_vix_threshold": 32.0,
            "circuit_breaker_cash_pct": 0.55,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.52,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
        },
    },
    # ─── Arm 8: fewer def stocks (12→8) + tighter weight ───
    {
        "name": "P2B8_CONC_DEF",
        "notes": "Defensive top_n=8, cap=0.20 — concentrated during stress",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 30.0,
            "circuit_breaker_vix_threshold": 35.0,
            "circuit_breaker_cash_pct": 0.50,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 8, "regime_defensive_max_weight_cap": 0.20,
        },
    },
    # ─── Arm 9: balanced — def29 + side27 + CB33 + CB55% + dcash52% ───
    {
        "name": "P2B8_BALANCED",
        "notes": "Balanced nudge across all levers for minimal CAGR impact",
        "overrides": {**_B8_PORT_BASE,
            "vix_bull_threshold": 18.0,
            "vix_defensive_threshold": 29.0,
            "circuit_breaker_vix_threshold": 33.0,
            "circuit_breaker_cash_pct": 0.55,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.27,
            "regime_defensive_cash_pct": 0.52,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 11, "regime_defensive_max_weight_cap": 0.12,
        },
    },
]

PHASE2_BATCH08_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# BATCH09 — High-Budget GA Signal + SIDE28 Fine-Tuning
#   Primary goal: produce a high-quality frozen signal (MeanIC ≥ 0.025).
#   GA budget: outer pop 500→800, fast pop 200→300 (subprocess-safe).
#   Portfolio: SIDE28 anchor + side/def cash variations.
# =============================================================================

PHASE2_BATCH09_GA_POP = 500
PHASE2_BATCH09_GA_GEN = 15

def make_phase2_batch09_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH09",
        "ga_population": PHASE2_BATCH09_GA_POP,
        "ga_generations": PHASE2_BATCH09_GA_GEN,
        # Stability — fast is seed screening only; refine gets the real budget
        "stability_seed_runs": 12,
        "stability_top_n_seeds": 5,
        "stability_fast_population": 200,
        "stability_fast_generations": 9,
        "stability_refine_population": 800,
        "stability_refine_generations": 15,
        # Risk penalty
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
    })

PHASE2_BATCH09_BASE_CFG = make_phase2_batch09_base_cfg()
PHASE2_BATCH09_COMMISSION_BPS = 10.0
PHASE2_BATCH09_SLIPPAGE_BPS = 5.0

_B9_PORT_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
    "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
    "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
}

PHASE2_BATCH09_PORTFOLIO_CONFIGS = [
    # ─── Arm 0: CTRL — BATCH08 SIDE28 exact ───
    {
        "name": "P2B9_CTRL",
        "notes": "SIDE28 anchor (BATCH08 winner: MDD=0.308, Sharpe=0.705)",
        "overrides": {**_B9_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Arm 1: side cash 30% ───
    {
        "name": "P2B9_SIDE30",
        "notes": "Side cash 28%→30%: check if more side cash still helps",
        "overrides": {**_B9_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Arm 2: small bull cash (3%) ───
    {
        "name": "P2B9_BULL3",
        "notes": "Add 3% bull cash for constant baseline cushion",
        "overrides": {**_B9_PORT_BASE,
            "regime_bull_cash_pct": 0.03,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Arm 3: defensive cash 55% ───
    {
        "name": "P2B9_DCASH55",
        "notes": "Defensive cash 50%→55% for deeper crisis protection",
        "overrides": {**_B9_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.55,
            "regime_defensive_top_n": 10,
        },
    },
    # ─── Arm 4: concentrated top_n=25 ───
    {
        "name": "P2B9_T25",
        "notes": "Top_n 30→25, concentrate on highest-conviction picks",
        "overrides": {**_B9_PORT_BASE,
            "portfolio_top_n": 25,
            "portfolio_hold_buffer_n": 40,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.50,
            "regime_bull_top_n": 25,
            "regime_side_top_n": 18,
        },
    },
    # ─── Arm 5: combo — side30 + def55 ───
    {
        "name": "P2B9_S30_D55",
        "notes": "Side=30% + DefCash=55% combo for max MDD defense",
        "overrides": {**_B9_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
            "regime_defensive_top_n": 10,
        },
    },
]

PHASE2_BATCH09_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# BATCH10 — Frozen-Signal Sweep: MDD Target Lock + CAGR Maximization
#   Uses BATCH09 frozen signal (MeanIC=0.0225).
#   Goal 1: lock MDD ≤ 0.32 via side cash fine-tuning (31~33%)
#   Goal 2: maximize CAGR within signal constraint (less defensive = more CAGR)
#   Goal 3: find the Pareto-optimal MDD vs CAGR config
#   No GA runs — frozen signal only, ~5 min total.
# =============================================================================

PHASE2_BATCH10_BASE_CFG = make_phase2_batch09_base_cfg()
PHASE2_BATCH10_COMMISSION_BPS = 10.0
PHASE2_BATCH10_SLIPPAGE_BPS = 5.0

_B10_PORT_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
    "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
    "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
}

PHASE2_BATCH10_PORTFOLIO_CONFIGS = [
    # ─── Group A: MDD Target lock — side cash gradient ───
    {
        "name": "P2B10_S30",
        "notes": "BATCH09 winner baseline (side=30%)",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    {
        "name": "P2B10_S31",
        "notes": "Side=31%: +1%p from SIDE30",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    {
        "name": "P2B10_S32",
        "notes": "Side=32%: +2%p from SIDE30",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.32,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    {
        "name": "P2B10_S33",
        "notes": "Side=33%: +3%p from SIDE30",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.33,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Group B: CAGR maximization — less defensive, more equity ───
    {
        "name": "P2B10_LEAN",
        "notes": "Minimal defense: side=20%, def=40% — maximize CAGR at MDD cost",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    {
        "name": "P2B10_CAP10",
        "notes": "Higher weight cap 0.08→0.10: more conviction on top picks",
        "overrides": {**_B10_PORT_BASE,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Group C: combo — MDD Target + CAGR recovery ───
    {
        "name": "P2B10_S31_D45",
        "notes": "Side=31% (MDD lock) + DefCash=45% (CAGR recovery in crisis)",
        "overrides": {**_B10_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    {
        "name": "P2B10_S32_CAP10",
        "notes": "Side=32% (MDD lock) + weight cap=0.10 (CAGR boost)",
        "overrides": {**_B10_PORT_BASE,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.32,
            "regime_defensive_cash_pct": 0.50,
        },
    },
]

PHASE2_BATCH10_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# BATCH11 — Meta Search + High-Budget GA for Signal Quality Improvement
#   Goal 1: MeanIC >= 0.025 (from 0.0225) via Meta Search optimization
#   Goal 2: Maintain/improve Spread >= 0.016
#   Approach:
#     - Enable Meta Search to explore fitness hyperparameters (OBJ, REG, alpha, top_q, penalties)
#     - Keep BATCH09 high refine budget (pop=800, gen=15)
#     - Portfolio configs re-use BATCH10 winners for fair signal comparison
#   Budget estimate:
#     Meta search: 16 trials x 4 seeds x (120 x 10) = 76,800 evals (lightweight)
#     Final GA:    12 fast x (200 x 9) + 5 refine x (800 x 15) = 81,600 evals
#     Total: ~158,400 evals, estimated ~7 hours
# =============================================================================

PHASE2_BATCH11_GA_POP = 500
PHASE2_BATCH11_GA_GEN = 15

def make_phase2_batch11_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH11",
        "ga_population": PHASE2_BATCH11_GA_POP,
        "ga_generations": PHASE2_BATCH11_GA_GEN,
        # Stability — same high budget as BATCH09
        "stability_seed_runs": 12,
        "stability_top_n_seeds": 5,
        "stability_fast_population": 200,
        "stability_fast_generations": 9,
        "stability_refine_population": 800,
        "stability_refine_generations": 15,
        # Risk penalty (from BATCH08+)
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
        # --- Meta Search ON ---
        "enable_meta_search": True,
        "meta_search_mode": "TEMPLATE_PLUS_RANDOM",
        "meta_search_trials": 14,
        "meta_template_trials_per_template": 2,
        "meta_random_extra_trials": 6,
        "meta_allow_template_perturbation": True,
        "meta_top_n_refine": 3,
        "meta_fast_ga_population": 120,
        "meta_fast_ga_generations": 10,
        "meta_fast_stability_seed_runs": 4,
        "meta_fast_stability_top_n": 2,
    })

PHASE2_BATCH11_BASE_CFG = make_phase2_batch11_base_cfg()
PHASE2_BATCH11_COMMISSION_BPS = 10.0
PHASE2_BATCH11_SLIPPAGE_BPS = 5.0

_B11_PORT_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
    "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
    "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
}

PHASE2_BATCH11_PORTFOLIO_CONFIGS = [
    # ─── BATCH10 winner: MDD + CAGR Pareto-optimal ───
    {
        "name": "P2B11_S31_D45",
        "notes": "BATCH10 winner: Side=31% + DefCash=45%",
        "overrides": {**_B11_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    # ─── BATCH09/10 baseline ───
    {
        "name": "P2B11_S30",
        "notes": "Side=30% baseline for comparison",
        "overrides": {**_B11_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── Higher side cash for MDD safety ───
    {
        "name": "P2B11_S33",
        "notes": "Side=33% for max MDD defense",
        "overrides": {**_B11_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.33,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ─── CAGR push: lower defense + higher cap ───
    {
        "name": "P2B11_LEAN_CAP10",
        "notes": "Minimal defense + weight cap 0.10: max CAGR push",
        "overrides": {**_B11_PORT_BASE,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
]

PHASE2_BATCH11_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# BATCH11T — Lightweight test: verify Meta Search → Final GA transition
#   Same structure as BATCH11 but drastically reduced budget (~20 min)
# =============================================================================

def make_phase2_batch11t_base_cfg() -> Config:
    c = make_phase2_batch01_base_cfg()
    return make_cfg_with_overrides(c, {
        "excel_prefix": "SP500_PHASE2_BATCH11T",
        "ga_population": 100,
        "ga_generations": 7,
        "stability_seed_runs": 4,
        "stability_top_n_seeds": 2,
        "stability_fast_population": 100,
        "stability_fast_generations": 7,
        "stability_refine_population": 200,
        "stability_refine_generations": 10,
        "enable_fitness_risk_penalty": True,
        "fitness_downside_vol_lambda": 0.50,
        "fitness_max_neg_spread_ratio_lambda": 0.30,
        "enable_order_book": True,
        "order_book_total_capital": 100000.0,
        # Meta Search ON — small budget
        "enable_meta_search": True,
        "meta_search_mode": "TEMPLATE_PLUS_RANDOM",
        "meta_search_trials": 3,
        "meta_template_trials_per_template": 1,
        "meta_random_extra_trials": 0,
        "meta_allow_template_perturbation": False,
        "meta_top_n_refine": 1,
        "meta_fast_ga_population": 60,
        "meta_fast_ga_generations": 5,
        "meta_fast_stability_seed_runs": 2,
        "meta_fast_stability_top_n": 1,
    })

PHASE2_BATCH11T_BASE_CFG = make_phase2_batch11t_base_cfg()
PHASE2_BATCH11T_COMMISSION_BPS = 10.0
PHASE2_BATCH11T_SLIPPAGE_BPS = 5.0

PHASE2_BATCH11T_PORTFOLIO_CONFIGS = [
    {
        "name": "P2B11T_S31_D45",
        "notes": "Quick test config",
        "overrides": {**_B11_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
]

PHASE2_BATCH11T_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# SIGNAL_LAB_TEST — Minimal budget smoke test (~5 min)
#   Signal Lab 3 candidates + tiny GA (pop=30, gen=3). Code verification only.
# =============================================================================

def make_signal_lab_test_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_SIGNAL_LAB_TEST",
            "precompute_npz_prefix": "precompute_qresearch_v62_signal_lab_test",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            "enable_meta_search": False,
            "enable_stability_layer": False,
            "ga_population": 30,
            "ga_generations": 3,
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "enable_signal_lab": True,
            "signal_lab_mode": "DIAGNOSTIC",
            "signal_lab_top_k": 3,
            "signal_lab_bucket_n": 5,
            "signal_lab_save_tables": True,
            "signal_lab_use_regime_split": True,
            "signal_lab_use_tail_separation_bonus": True,
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

SIGNAL_LAB_TEST_BASE_CFG = make_signal_lab_test_base_cfg()
SIGNAL_LAB_TEST_EXPERIMENT_SPECS = [
    {"name": "SL_TEST_BASELINE", "notes": "Signal Lab code verification only", "cfg_overrides": {}},
]
SIGNAL_LAB_TEST_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# SIGNAL_LAB_FULL — 5 hand-designed 후보만 평가, GA 완전 생략 (~15~20분)
#
# 구조:
#   ① Signal Lab: 5개 후보 전 기간 평가 (MeanIC/Spread/PosIC/regime-split/quintile)
#   ② STANDALONE: Signal Lab winner를 최종 신호로 사용 (GA 없음)
#   ③ Excel: Phase1 gate report (winner 지표) + SignalLab_* 시트 4개
#
# GA 기준값(baseline): BATCH11 MeanIC=0.0226, Spread=0.0166
#   → Signal Lab 후보가 이 수치에 도달하는지 비교
#
# 예상 시간: ~15~20분 (Signal Lab 5개 × ~2~3min + winner 1회 최종 평가)
# =============================================================================

def make_signal_lab_full_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_SIGNAL_LAB_FULL",
            "precompute_npz_prefix": "precompute_qresearch_v62_batch09",  # reuse BATCH09 npz
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # GA: completely skipped in STANDALONE mode
            "enable_meta_search": False,
            "enable_stability_layer": False,
            "ga_population": 10,
            "ga_generations": 1,
            # Portfolio: OFF (Phase 1 focus)
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            # Factor pools: same as BATCH11 for fair comparison
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
            # Signal Lab: STANDALONE mode — 5 candidates, no GA
            "enable_signal_lab": True,
            "signal_lab_mode": "STANDALONE",
            "signal_lab_top_k": 5,
            "signal_lab_bucket_n": 5,
            "signal_lab_save_tables": True,
            "signal_lab_use_regime_split": True,
            "signal_lab_use_tail_separation_bonus": True,
            "signal_lab_min_mean_ic": 0.015,
            "signal_lab_target_mean_ic": 0.025,
            "signal_lab_target_spread": 0.020,
        },
    )

SIGNAL_LAB_FULL_BASE_CFG = make_signal_lab_full_base_cfg()

SIGNAL_LAB_FULL_EXPERIMENT_SPECS = [
    {
        "name": "SL_FULL_HANDDESIGNED",
        "notes": "5 hand-designed candidates, GA skipped (STANDALONE). Baseline: BATCH11 MeanIC=0.0226 Spread=0.0166",
        "cfg_overrides": {},
    },
]

SIGNAL_LAB_FULL_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_TEST — CS-Rank Architecture A+B ultra-light test (~5~10min)
#
# Architecture A: CS-Rank Scoring (score_vector_for_day → rank_to_01)
# Architecture B: CS-Rank Features (tech features cross-sectional rank in precompute)
#
# New NPZ prefix forces rebuild with CS-Rank features enabled.
# GA budget minimal: just verify metrics compute correctly.
# =============================================================================

def make_csrank_test_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_TEST",
            "precompute_npz_prefix": "precompute_csrank_v1",  # new prefix → forces NPZ rebuild
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B: CS-Rank
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            # GA: minimal budget for test
            "enable_meta_search": False,
            "enable_stability_layer": True,
            "ga_population": 30,
            "ga_generations": 3,
            "stability_seed_runs": 2,
            "stability_top_n_seeds": 1,
            "stability_fast_population": 30,
            "stability_fast_generations": 3,
            "stability_refine_population": 30,
            "stability_refine_generations": 3,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            # Signal Lab: OFF (pure GA test for CS-Rank)
            "enable_signal_lab": False,
            # Factor pools: same as BATCH11
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_TEST_BASE_CFG = make_csrank_test_base_cfg()

CSRANK_TEST_EXPERIMENT_SPECS = [
    {
        "name": "CSRANK_AB_TEST",
        "notes": "CS-Rank A+B ultra-light: verify pipeline + NPZ rebuild with rank-normalised features",
        "cfg_overrides": {},
    },
]

CSRANK_TEST_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_FULL — CS-Rank A+B full budget (~4~5h)
#
# Architecture A: CS-Rank Scoring (score → rank_to_01)
# Architecture B: CS-Rank Features (tech features cross-sectional rank)
# GA budget: BATCH09 equivalent (fast 12×200×9 + refine 5×800×15 ≈ 81,600 eval)
# NPZ: reuse precompute_csrank_v1 (already built by CSRANK_TEST)
#
# Baseline 비교: BATCH11 MeanIC=0.0226, Spread=0.0166 (sigmoid features)
# =============================================================================

def make_csrank_full_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_FULL",
            "precompute_npz_prefix": "precompute_csrank_v1",  # reuse CS-Rank NPZ
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B: CS-Rank
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            # Meta: OFF (pure GA comparison first)
            "enable_meta_search": False,
            # GA stability — BATCH09 equivalent
            "enable_stability_layer": True,
            "ga_population": 500,
            "ga_generations": 15,
            "stability_seed_runs": 12,
            "stability_top_n_seeds": 5,
            "stability_fast_population": 200,
            "stability_fast_generations": 9,
            "stability_refine_population": 800,
            "stability_refine_generations": 15,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF (Phase 1 signal quality focus)
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            # Signal Lab: OFF
            "enable_signal_lab": False,
            # Factor pools: same as BATCH11
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_FULL_BASE_CFG = make_csrank_full_base_cfg()

CSRANK_FULL_EXPERIMENT_SPECS = [
    {
        "name": "CSRANK_AB_FULL",
        "notes": "CS-Rank A+B full GA budget. Baseline: BATCH11 MeanIC=0.0226 Spread=0.0166",
        "cfg_overrides": {},
    },
]

CSRANK_FULL_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_SPREAD — Spread recovery 3-arm experiment (~6~7h)
#
# CS-Rank A+B 유지 + GA fitness Spread 비중 / top_quantile 조합 비교
# NPZ: precompute_csrank_v1 (reuse)
# Baseline: CSRANK_FULL MeanIC=0.0276, Spread=0.0140
#
# ARM1: IC:Spread 60:40, top_q=0.15 (온건)
# ARM2: IC:Spread 50:50, top_q=0.15 (공격)
# ARM3: IC:Spread 60:40, top_q=0.10 (콤보)
#
# Per-arm budget:
#   fast:   10 seeds × 150 pop × 7 gen  = 10,500
#   refine:  3 seeds × 700 pop × 12 gen = 25,200  (+15%)
#   total per arm: 35,700
#   3 arms total: ~107,100 eval
# =============================================================================

def make_csrank_spread_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_SPREAD",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            # Meta: OFF
            "enable_meta_search": False,
            # GA stability
            "enable_stability_layer": True,
            "ga_population": 150,
            "ga_generations": 7,
            "stability_seed_runs": 10,
            "stability_top_n_seeds": 3,
            "stability_fast_population": 150,
            "stability_fast_generations": 7,
            "stability_refine_population": 700,
            "stability_refine_generations": 12,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            # Signal Lab: OFF
            "enable_signal_lab": False,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_SPREAD_BASE_CFG = make_csrank_spread_base_cfg()

CSRANK_SPREAD_EXPERIMENT_SPECS = [
    {
        "name": "ARM1_SPREAD40",
        "notes": "IC:Spread 60:40, top_q=0.15 (moderate spread boost)",
        "cfg_overrides": {
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.15,
        },
    },
    {
        "name": "ARM2_SPREAD50",
        "notes": "IC:Spread 50:50, top_q=0.15 (aggressive spread boost)",
        "cfg_overrides": {
            "w_ic1": 0.25,
            "w_ic3": 0.25,
            "w_spread": 0.50,
            "top_quantile": 0.15,
        },
    },
    {
        "name": "ARM3_COMBO",
        "notes": "IC:Spread 60:40 + top_q=0.10 (combo: spread boost + tight quantile)",
        "cfg_overrides": {
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
        },
    },
]

CSRANK_SPREAD_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_ADAPTIVE_TEST — Architecture A+B+C ultra-light test (~10~15min)
#
# ARM3_COMBO settings (w_spread=0.40, top_q=0.10) + Architecture C
# =============================================================================

def make_csrank_adaptive_test_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_ADAPTIVE_TEST",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B: CS-Rank
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            # Architecture C: Rolling IC-Adaptive
            "enable_rolling_ic_adaptive": True,
            "rolling_ic_lookback": 63,
            "rolling_ic_min_obs": 10,
            "rolling_ic_floor": 0.0,
            # GA: minimal
            "enable_meta_search": False,
            "enable_stability_layer": True,
            "ga_population": 30,
            "ga_generations": 3,
            "stability_seed_runs": 2,
            "stability_top_n_seeds": 1,
            "stability_fast_population": 30,
            "stability_fast_generations": 3,
            "stability_refine_population": 30,
            "stability_refine_generations": 3,
            # ARM3_COMBO fitness settings
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            "enable_signal_lab": False,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_ADAPTIVE_TEST_BASE_CFG = make_csrank_adaptive_test_base_cfg()

CSRANK_ADAPTIVE_TEST_EXPERIMENT_SPECS = [
    {
        "name": "ABC_TEST",
        "notes": "Architecture A+B+C ultra-light: verify rolling IC adaptive pipeline",
        "cfg_overrides": {},
    },
]

CSRANK_ADAPTIVE_TEST_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_ADAPTIVE — Architecture A+B+C full budget (~5~6h)
#
# ARM3_COMBO settings + Architecture C (Rolling IC-Adaptive)
# Budget: same as CSRANK_SPREAD per arm
#   fast:   10 seeds × 150 pop × 7 gen  = 10,500
#   refine:  3 seeds × 700 pop × 12 gen = 25,200
#   total: 35,700 eval
#
# Baseline: ARM3_COMBO MeanIC=0.0259, Spread=0.0182, PosIC=0.6097
# Target:   Spread 0.020+ (while keeping MeanIC 0.025+, PosIC 0.60+)
# =============================================================================

def make_csrank_adaptive_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_ADAPTIVE",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B: CS-Rank
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            # Architecture C: Rolling IC-Adaptive
            "enable_rolling_ic_adaptive": True,
            "rolling_ic_lookback": 63,
            "rolling_ic_min_obs": 10,
            "rolling_ic_floor": 0.0,
            # Meta: OFF
            "enable_meta_search": False,
            # GA stability
            "enable_stability_layer": True,
            "ga_population": 150,
            "ga_generations": 7,
            "stability_seed_runs": 10,
            "stability_top_n_seeds": 3,
            "stability_fast_population": 150,
            "stability_fast_generations": 7,
            "stability_refine_population": 700,
            "stability_refine_generations": 12,
            # ARM3_COMBO fitness settings
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            "enable_signal_lab": False,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_ADAPTIVE_BASE_CFG = make_csrank_adaptive_base_cfg()

CSRANK_ADAPTIVE_EXPERIMENT_SPECS = [
    {
        "name": "ABC_FULL",
        "notes": "A+B+C full. Baseline: ARM3 MeanIC=0.0259 Spread=0.0182. Target: Spread 0.020+",
        "cfg_overrides": {},
    },
]

CSRANK_ADAPTIVE_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# CSRANK_COMBO_FULL — ARM3_COMBO + full budget (~7~8h)
#
# Best config so far: CS-Rank A+B + w_spread=0.40 + top_q=0.10
# Full GA budget (BATCH09 equivalent) to maximise signal quality.
# Architecture C: OFF (Rolling IC-Adaptive degraded performance)
#
# Baseline: ARM3_COMBO (moderate budget) MeanIC=0.0259, Spread=0.0182, PosIC=0.6097
# Target:   All three metrics pass Target tier (Spread 0.020+ is the key)
# =============================================================================

def make_csrank_combo_full_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_CSRANK_COMBO_FULL",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B: CS-Rank (C: OFF)
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            "enable_rolling_ic_adaptive": False,
            # Meta: OFF
            "enable_meta_search": False,
            # GA stability — full budget (BATCH09 equivalent)
            "enable_stability_layer": True,
            "ga_population": 500,
            "ga_generations": 15,
            "stability_seed_runs": 12,
            "stability_top_n_seeds": 5,
            "stability_fast_population": 200,
            "stability_fast_generations": 9,
            "stability_refine_population": 800,
            "stability_refine_generations": 15,
            # ARM3_COMBO fitness settings
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio: OFF
            "enable_portfolio_construction": False,
            "enable_order_book": False,
            "write_today_top10": False,
            "write_live_today_top10": False,
            "write_score_1y_long": False,
            "enable_signal_lab": False,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

CSRANK_COMBO_FULL_BASE_CFG = make_csrank_combo_full_base_cfg()

CSRANK_COMBO_FULL_EXPERIMENT_SPECS = [
    {
        "name": "COMBO_FULL",
        "notes": "ARM3_COMBO + full budget. Baseline: MeanIC=0.0259 Spread=0.0182 PosIC=0.6097 (mod budget)",
        "cfg_overrides": {},
    },
]

CSRANK_COMBO_FULL_RUN_OPTIONS = {"write_reports": True, "show_progress": True}


# =============================================================================
# P2_BATCH12 — Phase 2 with COMBO_FULL signal (CS-Rank A+B) (~8~10h)
#
# GA: COMBO_FULL settings (CS-Rank A+B, w_spread=0.40, top_q=0.10, full budget)
# Portfolio: BATCH11 best configs + new CAGR push variants
# Signal: GA runs first → auto-saves frozen signal → portfolio sweep
#
# Phase 1 baseline: MeanIC=0.0287, Spread=0.0206, PosIC=0.597
# =============================================================================

_P2B12_PORT_BASE = {
    "portfolio_top_n": 30,
    "portfolio_hold_buffer_n": 50,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_max_weight_cap": 0.08,
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
    "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
    "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
}

def make_phase2_batch12_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_PHASE2_BATCH12",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture A+B
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": True,
            "enable_rolling_ic_adaptive": False,
            # Meta: OFF (pure GA as in COMBO_FULL)
            "enable_meta_search": False,
            # GA stability — full budget
            "enable_stability_layer": True,
            "ga_population": 500,
            "ga_generations": 15,
            "stability_seed_runs": 12,
            "stability_top_n_seeds": 5,
            "stability_fast_population": 200,
            "stability_fast_generations": 9,
            "stability_refine_population": 800,
            "stability_refine_generations": 15,
            # ARM3_COMBO fitness
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio ON
            "enable_portfolio_construction": True,
            "enable_order_book": True,
            "order_book_total_capital": 100000.0,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

PHASE2_BATCH12_BASE_CFG = make_phase2_batch12_base_cfg()
PHASE2_BATCH12_COMMISSION_BPS = 10.0
PHASE2_BATCH12_SLIPPAGE_BPS = 5.0

PHASE2_BATCH12_PORTFOLIO_CONFIGS = [
    {
        "name": "B12_S31_D45",
        "notes": "BATCH11 winner: Side=31% DefCash=45%",
        "overrides": {**_P2B12_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    {
        "name": "B12_S30_D50",
        "notes": "Side=30% DefCash=50% (safe baseline)",
        "overrides": {**_P2B12_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    {
        "name": "B12_S25_D40",
        "notes": "Lower cash: Side=25% DefCash=40% (CAGR push)",
        "overrides": {**_P2B12_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    {
        "name": "B12_S33_D50",
        "notes": "High safety: Side=33% DefCash=50% (MDD defense)",
        "overrides": {**_P2B12_PORT_BASE,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.33,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    {
        "name": "B12_LEAN_CAP10",
        "notes": "Minimal defense + weight cap 0.10 (max CAGR)",
        "overrides": {**_P2B12_PORT_BASE,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    {
        "name": "B12_BULL5_S28_D45",
        "notes": "Small bull cash 5% + moderate side/def for smooth curve",
        "overrides": {**_P2B12_PORT_BASE,
            "regime_bull_cash_pct": 0.05,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.45,
        },
    },
]

PHASE2_BATCH12_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
    # CS-Rank must be applied to the signal as well
    "enable_cs_rank_features": True,
    "enable_cs_rank_scoring": True,
    "w_ic1": 0.30,
    "w_ic3": 0.30,
    "w_spread": 0.40,
    "top_quantile": 0.10,
}


# =============================================================================
# P2_BATCH13 — B-only (CS-Rank Features ON, Scoring OFF) + 2x GA budget
#
# Hypothesis: Architecture B improves GA training quality (IC > BATCH11)
#             without destroying score magnitude (CAGR recovery).
# Budget: 2x COMBO_FULL ≈ 163k evaluations
#   fast  = 16 seeds × 250 pop × 9 gen = 36,000
#   refine = 6 top   × 1200 pop × 18 gen = 129,600
#   total ≈ 165,600
#
# Portfolio: concentration push (Top_N↓, weight_cap↑, cash↓)
# =============================================================================

_P2B13_PORT_BASE = {
    "portfolio_hold_buffer_n": 40,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
}

def make_phase2_batch13_base_cfg() -> Config:
    return make_cfg_with_overrides(
        Config(),
        {
            "excel_prefix": "SP500_PHASE2_BATCH13",
            "precompute_npz_prefix": "precompute_csrank_v1",
            "start_panel_date": datetime(2017, 2, 21),
            "end_date": datetime(2026, 3, 2),
            # Data
            "enable_historical_universe": True,
            "historical_universe_expand_tickers": True,
            "enable_coverage_based_universe": True,
            "enable_panel_cache_fallback_download": False,
            # Architecture B ONLY: features normalized, scoring raw
            "enable_cs_rank_features": True,
            "enable_cs_rank_scoring": False,
            "enable_rolling_ic_adaptive": False,
            # Meta: OFF
            "enable_meta_search": False,
            # GA stability — 2x full budget
            "enable_stability_layer": True,
            "ga_population": 500,
            "ga_generations": 15,
            "stability_seed_runs": 16,
            "stability_top_n_seeds": 6,
            "stability_fast_population": 250,
            "stability_fast_generations": 9,
            "stability_refine_population": 1200,
            "stability_refine_generations": 18,
            # ARM3_COMBO fitness
            "w_ic1": 0.30,
            "w_ic3": 0.30,
            "w_spread": 0.40,
            "top_quantile": 0.10,
            # Risk penalties
            "enable_fitness_risk_penalty": True,
            "fitness_downside_vol_lambda": 0.50,
            "fitness_max_neg_spread_ratio_lambda": 0.30,
            # Portfolio ON
            "enable_portfolio_construction": True,
            "enable_order_book": True,
            "order_book_total_capital": 100000.0,
            # Factor pools
            "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
            "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
            "use_random_seed": True,
        },
    )

PHASE2_BATCH13_BASE_CFG = make_phase2_batch13_base_cfg()
PHASE2_BATCH13_COMMISSION_BPS = 10.0
PHASE2_BATCH13_SLIPPAGE_BPS = 5.0

PHASE2_BATCH13_PORTFOLIO_CONFIGS = [
    # ── 1. BATCH11 winner baseline (reference) ──
    {
        "name": "B13_B11_WINNER",
        "notes": "BATCH11 winner: Top30, cap=0.08, Side=31% DefCash=45%",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 30,
            "portfolio_max_weight_cap": 0.08,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    # ── 2. Concentrated: fewer stocks, higher cap ──
    {
        "name": "B13_CONC_T15",
        "notes": "Top15 + cap=0.15 + moderate cash: high conviction",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 15,
            "portfolio_max_weight_cap": 0.15,
            "portfolio_hold_buffer_n": 25,
            "regime_bull_top_n": 15, "regime_bull_max_weight_cap": 0.15,
            "regime_side_top_n": 12, "regime_side_max_weight_cap": 0.15,
            "regime_defensive_top_n": 8, "regime_defensive_max_weight_cap": 0.20,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    # ── 3. Low cash: max market exposure ──
    {
        "name": "B13_LOW_CASH",
        "notes": "Top30 + cap=0.08 + very low cash: max exposure",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 30,
            "portfolio_max_weight_cap": 0.08,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.15,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 4. Aggressive: concentration + low cash ──
    {
        "name": "B13_AGG_T20",
        "notes": "Top20 + cap=0.12 + low cash: CAGR push",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 20,
            "portfolio_max_weight_cap": 0.12,
            "portfolio_hold_buffer_n": 30,
            "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
            "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 5. Ultra concentrated ──
    {
        "name": "B13_ULTRA_T12",
        "notes": "Top12 + cap=0.20 + moderate cash: max conviction",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 12,
            "portfolio_max_weight_cap": 0.20,
            "portfolio_hold_buffer_n": 20,
            "regime_bull_top_n": 12, "regime_bull_max_weight_cap": 0.20,
            "regime_side_top_n": 10, "regime_side_max_weight_cap": 0.20,
            "regime_defensive_top_n": 6, "regime_defensive_max_weight_cap": 0.25,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    # ── 6. Balanced push: moderate concentration + moderate cash ──
    {
        "name": "B13_BAL_T20",
        "notes": "Top20 + cap=0.10 + BATCH11-style cash: balanced",
        "overrides": {**_P2B13_PORT_BASE,
            "portfolio_top_n": 20,
            "portfolio_max_weight_cap": 0.10,
            "portfolio_hold_buffer_n": 35,
            "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.10,
            "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.28,
            "regime_defensive_cash_pct": 0.45,
        },
    },
]

PHASE2_BATCH13_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
    "enable_cs_rank_features": True,
    "enable_cs_rank_scoring": False,
    "w_ic1": 0.30,
    "w_ic3": 0.30,
    "w_spread": 0.40,
    "top_quantile": 0.10,
}


# =============================================================================
# P2_BATCH14 — BATCH11 frozen signal + aggressive portfolio (~30 min)
#
# Reuses BATCH11 base cfg (no CS-Rank, original precompute).
# BATCH11 signal: MeanIC=0.0226, Spread=0.0166, PosICRatio=0.601
# BATCH11 Phase 2 best: CAGR=15.7%, MDD=30.1% (LEAN_CAP10)
#
# New portfolio configs: concentration push + low cash
# =============================================================================

_P2B14_PORT_BASE = {
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
}

PHASE2_BATCH14_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH14"},
)
PHASE2_BATCH14_COMMISSION_BPS = 10.0
PHASE2_BATCH14_SLIPPAGE_BPS = 5.0

PHASE2_BATCH14_PORTFOLIO_CONFIGS = [
    # ── 1. BATCH11 original winner (reference) ──
    {
        "name": "B14_B11_REF",
        "notes": "BATCH11 winner: Top30 cap=0.08 Side=31% Def=45% (reference)",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 30, "portfolio_hold_buffer_n": 50,
            "portfolio_max_weight_cap": 0.08,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.31,
            "regime_defensive_cash_pct": 0.45,
        },
    },
    # ── 2. BATCH11 LEAN_CAP10 (previous best CAGR) ──
    {
        "name": "B14_LEAN10",
        "notes": "BATCH11 LEAN: Top30 cap=0.10 Side=20% Def=40% (prev best CAGR)",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 30, "portfolio_hold_buffer_n": 50,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.10,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.10,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 3. Concentrated T20 + low cash (CAGR push) ──
    {
        "name": "B14_AGG_T20",
        "notes": "Top20 cap=0.12 Side=15% Def=35%: concentration + max exposure",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 20, "portfolio_hold_buffer_n": 30,
            "portfolio_max_weight_cap": 0.12,
            "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
            "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.15,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 4. Very low cash, wide portfolio ──
    {
        "name": "B14_LOWCASH",
        "notes": "Top30 cap=0.08 Side=10% Def=30%: minimal cash drag",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 30, "portfolio_hold_buffer_n": 50,
            "portfolio_max_weight_cap": 0.08,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.10,
            "regime_defensive_cash_pct": 0.30,
        },
    },
    # ── 5. Concentrated T15 + moderate cash ──
    {
        "name": "B14_CONC_T15",
        "notes": "Top15 cap=0.15 Side=20% Def=40%: high conviction",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 15, "portfolio_hold_buffer_n": 25,
            "portfolio_max_weight_cap": 0.15,
            "regime_bull_top_n": 15, "regime_bull_max_weight_cap": 0.15,
            "regime_side_top_n": 12, "regime_side_max_weight_cap": 0.15,
            "regime_defensive_top_n": 8, "regime_defensive_max_weight_cap": 0.20,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 6. Aggressive T20 + bull cash 0 + minimal side cash ──
    {
        "name": "B14_MAX_CAGR",
        "notes": "Top20 cap=0.12 Side=10% Def=30%: max CAGR push",
        "overrides": {**_P2B14_PORT_BASE,
            "portfolio_top_n": 20, "portfolio_hold_buffer_n": 30,
            "portfolio_max_weight_cap": 0.12,
            "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
            "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
            "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
            "regime_bull_cash_pct": 0.0,
            "regime_side_cash_pct": 0.10,
            "regime_defensive_cash_pct": 0.30,
        },
    },
]

PHASE2_BATCH14_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH15 — Sharpe micro-tuning around AGG_T20 (~1 min, frozen signal)
#
# Baseline: B14_AGG_T20 (CAGR=17.4%, MDD=31.4%, Sharpe=0.797, Calmar=0.555)
# Goal: Sharpe 0.797 → 0.85 by reducing vol (slightly more cash, tighter caps)
# Strategy: keep Top20/cap0.12 core, tune cash levels around AGG_T20
# =============================================================================

_P2B15_PORT_BASE = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
}

PHASE2_BATCH15_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH15"},
)
PHASE2_BATCH15_COMMISSION_BPS = 10.0
PHASE2_BATCH15_SLIPPAGE_BPS = 5.0

PHASE2_BATCH15_PORTFOLIO_CONFIGS = [
    # ── 0. AGG_T20 원본 (reference) ──
    {
        "name": "B15_REF",
        "notes": "B14_AGG_T20 원본: S15/D35 (Sharpe=0.797)",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.15,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 1. Side cash +5%: vol 억제 ──
    {
        "name": "B15_S20_D35",
        "notes": "Side 20% (+5%): Sharpe push via side vol reduction",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 2. Def cash +5%: crash vol 억제 ──
    {
        "name": "B15_S15_D40",
        "notes": "Def 40% (+5%): Sharpe push via crash vol reduction",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.15,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 3. 양쪽 +5%: 양면 vol 억제 ──
    {
        "name": "B15_S20_D40",
        "notes": "Side 20% + Def 40%: balanced vol reduction",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 4. 양쪽 +7~8%: 강한 vol 억제 ──
    {
        "name": "B15_S22_D43",
        "notes": "Side 22% + Def 43%: strong vol reduction",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.22,
            "regime_defensive_cash_pct": 0.43,
        },
    },
    # ── 5. Cap 축소 0.10 + 양쪽 +5%: 집중도 낮춰 vol 억제 ──
    {
        "name": "B15_CAP10_S20_D40",
        "notes": "Cap 0.10 + S20/D40: lower concentration = lower vol",
        "overrides": {**_P2B15_PORT_BASE,
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_defensive_max_weight_cap": 0.12,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.40,
        },
    },
    # ── 6. Bull cash 소량 + 양쪽 moderate ──
    {
        "name": "B15_B3_S18_D38",
        "notes": "Bull 3% + S18/D38: smooth equity curve",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_bull_cash_pct": 0.03,
            "regime_side_cash_pct": 0.18,
            "regime_defensive_cash_pct": 0.38,
        },
    },
    # ── 7. Side +5% + tighter defensive cap ──
    {
        "name": "B15_S20_D38_DCAP12",
        "notes": "S20/D38 + def_cap=0.12: tighter in crash",
        "overrides": {**_P2B15_PORT_BASE,
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.38,
            "regime_defensive_max_weight_cap": 0.12,
        },
    },
]

PHASE2_BATCH15_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH16 — Structural Sharpe push: inv-vol / biweekly / combo (~1 min)
#
# Baseline: B15_S20_D35 (Sharpe=0.801, CAGR=17.0%, MDD=30.0%)
# Goal: Sharpe 0.801 → 0.85 via structural portfolio changes
#
# ARM1: Inverse-vol weighting (reduce high-vol stock exposure)
# ARM2: Biweekly rebalancing (reduce turnover → lower cost → higher net return)
# ARM3: ARM1 + ARM2 combined
# ARM4-6: Best combos with cash tuning
# =============================================================================

_P2B16_BASE_OVERRIDES = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
    "regime_side_cash_pct": 0.20,
    "regime_defensive_cash_pct": 0.35,
}

PHASE2_BATCH16_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH16"},
)
PHASE2_BATCH16_COMMISSION_BPS = 10.0
PHASE2_BATCH16_SLIPPAGE_BPS = 5.0

PHASE2_BATCH16_PORTFOLIO_CONFIGS = [
    # ── REF: B15 best (S20/D35, weekly, no vol-adj) ──
    {
        "name": "B16_REF",
        "notes": "B15_S20_D35 baseline: weekly + score_weighted",
        "overrides": {**_P2B16_BASE_OVERRIDES},
    },
    # ── ARM1: Inverse-vol weighting ──
    {
        "name": "B16_INVVOL",
        "notes": "ARM1: score × (1/trailing_vol) weighting",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "portfolio_vol_adjust": True,
            "portfolio_vol_lookback": 60,
        },
    },
    # ── ARM2: Biweekly rebalancing ──
    {
        "name": "B16_BIWEEK",
        "notes": "ARM2: eval_freq=2W → halve turnover",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "eval_freq": "2W",
        },
    },
    # ── ARM3: inv-vol + biweekly ──
    {
        "name": "B16_COMBO",
        "notes": "ARM3: inv-vol + biweekly combined",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "portfolio_vol_adjust": True,
            "portfolio_vol_lookback": 60,
            "eval_freq": "2W",
        },
    },
    # ── ARM4: inv-vol + biweekly + slightly higher cash ──
    {
        "name": "B16_COMBO_S22",
        "notes": "ARM4: combo + Side 22% for extra vol reduction",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "portfolio_vol_adjust": True,
            "portfolio_vol_lookback": 60,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.22,
            "regime_defensive_cash_pct": 0.38,
        },
    },
    # ── ARM5: inv-vol only + lower cash for CAGR ──
    {
        "name": "B16_INVVOL_S15",
        "notes": "ARM5: inv-vol + low cash S15/D35 for CAGR",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "portfolio_vol_adjust": True,
            "portfolio_vol_lookback": 60,
            "regime_side_cash_pct": 0.15,
        },
    },
    # ── ARM6: biweekly + shorter vol lookback ──
    {
        "name": "B16_COMBO_V40",
        "notes": "ARM6: combo + vol_lookback=40 (faster adapt)",
        "overrides": {**_P2B16_BASE_OVERRIDES,
            "portfolio_vol_adjust": True,
            "portfolio_vol_lookback": 40,
            "eval_freq": "2W",
        },
    },
]

PHASE2_BATCH16_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH17 — Biweekly+ rebalancing × MDD defense grid (~1 min)
#
# Finding: biweekly → CAGR 32%, Sharpe 0.836 but MDD 44.6%
# Goal: reduce MDD ≤ 32% while keeping Sharpe ≥ 0.85
# Levers: rebalance freq (2W/3W/4W/6W) × cash (Side/Def)
# =============================================================================

_P2B17_PORT_BASE = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
}

PHASE2_BATCH17_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH17"},
)
PHASE2_BATCH17_COMMISSION_BPS = 10.0
PHASE2_BATCH17_SLIPPAGE_BPS = 5.0

PHASE2_BATCH17_PORTFOLIO_CONFIGS = [
    # ── 1. 2W baseline (B16_BIWEEK 재현) ──
    {
        "name": "B17_2W_REF",
        "notes": "2W + S20/D35: B16_BIWEEK reference",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 2. 2W + high defense cash ──
    {
        "name": "B17_2W_S25_D55",
        "notes": "2W + Side=25% Def=55%: strong MDD defense",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.55,
        },
    },
    # ── 3. 2W + very high defense ──
    {
        "name": "B17_2W_S30_D60",
        "notes": "2W + Side=30% Def=60%: max MDD defense",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.60,
        },
    },
    # ── 4. 2W + moderate defense + bull cash ──
    {
        "name": "B17_2W_B5_S25_D50",
        "notes": "2W + Bull=5% Side=25% Def=50%: smooth curve",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "2W",
            "regime_bull_cash_pct": 0.05,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 5. 3W + moderate cash ──
    {
        "name": "B17_3W_S25_D50",
        "notes": "3W + Side=25% Def=50%",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "3W",
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 6. 3W + high defense ──
    {
        "name": "B17_3W_S30_D60",
        "notes": "3W + Side=30% Def=60%: 3-weekly + max defense",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "3W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.60,
        },
    },
    # ── 7. 4W (monthly-ish) + moderate cash ──
    {
        "name": "B17_4W_S25_D50",
        "notes": "4W + Side=25% Def=50%",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "4W",
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 8. 4W + high defense ──
    {
        "name": "B17_4W_S30_D60",
        "notes": "4W + Side=30% Def=60%",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "4W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.60,
        },
    },
    # ── 9. 6W + moderate cash ──
    {
        "name": "B17_6W_S25_D50",
        "notes": "6W + Side=25% Def=50%",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "6W",
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 10. 6W + high defense ──
    {
        "name": "B17_6W_S30_D60",
        "notes": "6W + Side=30% Def=60%",
        "overrides": {**_P2B17_PORT_BASE,
            "eval_freq": "6W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.60,
        },
    },
]

PHASE2_BATCH17_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH18 — B16 even-week 2W schedule + MDD defense (~1 min)
#
# B16_BIWEEK (2W even-week): CAGR=31.9%, Sharpe=0.836, MDD=44.6%
# Goal: keep Sharpe ~0.836+ while reducing MDD ≤ 32%
# Also test odd-week (2WB) and weekly (REF) for comparison
# =============================================================================

_P2B18_PORT_BASE = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
}

PHASE2_BATCH18_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH18"},
)
PHASE2_BATCH18_COMMISSION_BPS = 10.0
PHASE2_BATCH18_SLIPPAGE_BPS = 5.0

PHASE2_BATCH18_PORTFOLIO_CONFIGS = [
    # ── 1. Weekly REF (B15 best) ──
    {
        "name": "B18_1W_REF",
        "notes": "Weekly S20/D35: B15 best (Sharpe=0.801) reference",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "W",
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 2. 2W even-week REF (B16_BIWEEK 재현) ──
    {
        "name": "B18_2W_REF",
        "notes": "2W even-week S20/D35: B16_BIWEEK reference",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 3. 2W odd-week (B17 schedule) ──
    {
        "name": "B18_2WB_REF",
        "notes": "2W odd-week S20/D35: B17 schedule for comparison",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2WB",
            "regime_side_cash_pct": 0.20,
            "regime_defensive_cash_pct": 0.35,
        },
    },
    # ── 4. 2W even + moderate defense ──
    {
        "name": "B18_2W_S25_D50",
        "notes": "2W even + Side=25% Def=50%",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 5. 2W even + strong defense ──
    {
        "name": "B18_2W_S30_D55",
        "notes": "2W even + Side=30% Def=55%",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
        },
    },
    # ── 6. 2W even + max defense ──
    {
        "name": "B18_2W_S35_D65",
        "notes": "2W even + Side=35% Def=65%: max MDD push",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.35,
            "regime_defensive_cash_pct": 0.65,
        },
    },
    # ── 7. 2W even + bull cash + strong defense ──
    {
        "name": "B18_2W_B5_S30_D55",
        "notes": "2W even + Bull=5% Side=30% Def=55%",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_bull_cash_pct": 0.05,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
        },
    },
    # ── 8. 2W even + defense + tighter cap ──
    {
        "name": "B18_2W_S30_D55_C10",
        "notes": "2W even + S30/D55 + cap=0.10",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "portfolio_max_weight_cap": 0.10,
            "regime_bull_max_weight_cap": 0.10,
            "regime_side_max_weight_cap": 0.10,
            "regime_defensive_max_weight_cap": 0.12,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
        },
    },
    # ── 9. 2W even + moderate defense + wider portfolio ──
    {
        "name": "B18_2W_T30_S25_D50",
        "notes": "2W even + Top30 cap=0.08 + S25/D50",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "portfolio_top_n": 30, "portfolio_hold_buffer_n": 50,
            "portfolio_max_weight_cap": 0.08,
            "regime_bull_top_n": 30, "regime_bull_max_weight_cap": 0.08,
            "regime_side_top_n": 20, "regime_side_max_weight_cap": 0.08,
            "regime_defensive_top_n": 12, "regime_defensive_max_weight_cap": 0.12,
            "regime_side_cash_pct": 0.25,
            "regime_defensive_cash_pct": 0.50,
        },
    },
    # ── 10. 2W even + strong defense + CB tighter ──
    {
        "name": "B18_2W_S30_D55_CB30",
        "notes": "2W even + S30/D55 + CB trigger at VIX=30",
        "overrides": {**_P2B18_PORT_BASE,
            "eval_freq": "2W",
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
            "circuit_breaker_vix_threshold": 30.0,
            "circuit_breaker_cash_pct": 0.60,
        },
    },
]

PHASE2_BATCH18_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH19 — Event-Driven Rebalancing (~1 min, frozen signal)
#
# Phase 3 prep: daily trigger check, rebalance only when needed.
# Baseline comparison: B18_1W_REF (Sharpe=0.801, MDD=30.0%)
#                      B18_2W_REF (Sharpe=0.836, MDD=44.6%)
# Goal: combine 2W CAGR with Weekly MDD defense via smart triggers
# =============================================================================

_P2B19_PORT_BASE = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
    "regime_side_cash_pct": 0.20,
    "regime_defensive_cash_pct": 0.35,
}

PHASE2_BATCH19_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH11_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH19"},
)
PHASE2_BATCH19_COMMISSION_BPS = 10.0
PHASE2_BATCH19_SLIPPAGE_BPS = 5.0

PHASE2_BATCH19_PORTFOLIO_CONFIGS = [
    # ── REF: baselines (non-event-driven) ──
    {
        "name": "B19_1W_REF",
        "notes": "Weekly baseline (B18_1W_REF)",
        "overrides": {**_P2B19_PORT_BASE, "eval_freq": "W"},
    },
    {
        "name": "B19_2W_REF",
        "notes": "2W even-week baseline (B18_2W_REF)",
        "overrides": {**_P2B19_PORT_BASE, "eval_freq": "2W"},
    },

    # ── EVT: Event-Driven arms ──
    # ARM1: conservative triggers (long interval, high VIX threshold)
    {
        "name": "B19_EVT_CONS",
        "notes": "Event: min=7d max=14d VIX_emg=32 drift=0.15 score=0.20",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 7,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 32.0,
            "event_vix_recovery_threshold": 25.0,
            "event_drift_threshold": 0.15,
            "event_score_change_threshold": 0.20,
        },
    },
    # ARM2: moderate triggers (balanced interval + moderate VIX)
    {
        "name": "B19_EVT_MOD",
        "notes": "Event: min=5d max=14d VIX_emg=28 drift=0.12 score=0.15",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 5,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 28.0,
            "event_vix_recovery_threshold": 22.0,
            "event_drift_threshold": 0.12,
            "event_score_change_threshold": 0.15,
        },
    },
    # ARM3: aggressive triggers (short cooldown, sensitive VIX)
    {
        "name": "B19_EVT_AGG",
        "notes": "Event: min=3d max=14d VIX_emg=25 drift=0.10 score=0.12",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 3,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 25.0,
            "event_vix_recovery_threshold": 20.0,
            "event_drift_threshold": 0.10,
            "event_score_change_threshold": 0.12,
        },
    },
    # ARM4: VIX-only triggers (no drift/score)
    {
        "name": "B19_EVT_VIX_ONLY",
        "notes": "Event: min=5d max=14d VIX_emg=28 no drift/score",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 5,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 28.0,
            "event_vix_recovery_threshold": 22.0,
            "event_drift_threshold": 99.0,
            "event_score_change_threshold": 99.0,
        },
    },
    # ARM5: moderate + longer max interval (3W equivalent)
    {
        "name": "B19_EVT_MOD_3W",
        "notes": "Event: min=5d max=21d VIX_emg=28 drift=0.12 score=0.15",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 5,
            "event_max_interval_days": 21,
            "event_vix_emergency_threshold": 28.0,
            "event_vix_recovery_threshold": 22.0,
            "event_drift_threshold": 0.12,
            "event_score_change_threshold": 0.15,
        },
    },
    # ARM6: moderate + stronger defense cash
    {
        "name": "B19_EVT_MOD_DEF",
        "notes": "Event: min=5d max=14d VIX_emg=28 + S30%/D55%",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 5,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 28.0,
            "event_vix_recovery_threshold": 22.0,
            "event_drift_threshold": 0.12,
            "event_score_change_threshold": 0.15,
            "regime_side_cash_pct": 0.30,
            "regime_defensive_cash_pct": 0.55,
        },
    },
    # ARM7: aggressive + shorter max interval (10d ≈ 2W but flexible)
    {
        "name": "B19_EVT_AGG_10D",
        "notes": "Event: min=3d max=10d VIX_emg=25 drift=0.10 score=0.12",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 3,
            "event_max_interval_days": 10,
            "event_vix_emergency_threshold": 25.0,
            "event_vix_recovery_threshold": 20.0,
            "event_drift_threshold": 0.10,
            "event_score_change_threshold": 0.12,
        },
    },
    # ARM8: moderate + VIX emergency triggers immediate defense
    {
        "name": "B19_EVT_MOD_CB25",
        "notes": "Event: min=5d max=14d VIX_emg=28 + CB at VIX=25",
        "overrides": {**_P2B19_PORT_BASE,
            "enable_event_driven_rebal": True,
            "event_min_interval_days": 5,
            "event_max_interval_days": 14,
            "event_vix_emergency_threshold": 28.0,
            "event_vix_recovery_threshold": 22.0,
            "event_drift_threshold": 0.12,
            "event_score_change_threshold": 0.15,
            "circuit_breaker_vix_threshold": 25.0,
            "circuit_breaker_cash_pct": 0.55,
        },
    },
]

PHASE2_BATCH19_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


# =============================================================================
# P2_BATCH20 — BATCH13 (Arch-B only) frozen signal + EVT_CONS strategy
#
# Q: Does higher Phase 1 signal quality (B-only MeanIC=0.0281)
#    translate to better Phase 2 with the event-driven strategy?
# Baseline: BATCH19 EVT_CONS (BATCH11 signal, MeanIC=0.0226)
#   → CAGR=34.1%, Sharpe=1.212, MDD=29.6%
# =============================================================================

PHASE2_BATCH20_BASE_CFG = make_cfg_with_overrides(
    PHASE2_BATCH13_BASE_CFG,
    {"excel_prefix": "SP500_PHASE2_BATCH20"},
)
PHASE2_BATCH20_COMMISSION_BPS = 10.0
PHASE2_BATCH20_SLIPPAGE_BPS = 5.0

_P2B20_EVT_CONS = {
    "portfolio_top_n": 20,
    "portfolio_hold_buffer_n": 30,
    "portfolio_max_weight_cap": 0.12,
    "portfolio_entry_score_margin": 8.0,
    "portfolio_weight_mode": "score_weighted",
    "portfolio_min_rebalance_weight_change": 0.005,
    "portfolio_enable_turnover_hysteresis": True,
    "regime_adaptive_portfolio": True,
    "enable_vix_fast_regime": True,
    "enable_circuit_breaker": True,
    "enable_order_book": True,
    "order_book_total_capital": 100000.0,
    "vix_bull_threshold": 18.0,
    "vix_defensive_threshold": 30.0,
    "circuit_breaker_vix_threshold": 35.0,
    "circuit_breaker_cash_pct": 0.50,
    "regime_bull_top_n": 20, "regime_bull_max_weight_cap": 0.12,
    "regime_side_top_n": 15, "regime_side_max_weight_cap": 0.12,
    "regime_defensive_top_n": 10, "regime_defensive_max_weight_cap": 0.15,
    "regime_bull_cash_pct": 0.0,
    "regime_side_cash_pct": 0.20,
    "regime_defensive_cash_pct": 0.35,
    "enable_event_driven_rebal": True,
    "event_min_interval_days": 7,
    "event_max_interval_days": 14,
    "event_vix_emergency_threshold": 32.0,
    "event_vix_recovery_threshold": 25.0,
    "event_drift_threshold": 0.15,
    "event_score_change_threshold": 0.20,
}

PHASE2_BATCH20_PORTFOLIO_CONFIGS = [
    {
        "name": "B20_B13_EVT_CONS",
        "notes": "BATCH13 B-only signal + EVT_CONS strategy",
        "overrides": {**_P2B20_EVT_CONS},
    },
    {
        "name": "B20_B13_1W_REF",
        "notes": "BATCH13 B-only signal + Weekly reference",
        "overrides": {**_P2B20_EVT_CONS,
            "enable_event_driven_rebal": False,
            "eval_freq": "W",
        },
    },
    {
        "name": "B20_B13_2W_REF",
        "notes": "BATCH13 B-only signal + 2W even-week reference",
        "overrides": {**_P2B20_EVT_CONS,
            "enable_event_driven_rebal": False,
            "eval_freq": "2W",
        },
    },
]

PHASE2_BATCH20_SIGNAL_OVERRIDES = {
    "bull_allowed_factor_pool": _SA_VQM_BULL_POOL_BASE,
    "bull_factor_pool": _SA_VQM_BULL_FACTOR_BASE,
    "use_random_seed": True,
}


print(
    f"[ExperimentPlan] ready live_gap={len(LIVE_GAP_EXPERIMENT_SPECS)} "
    f"bull={len(BULL_SPREAD_EXPERIMENT_SPECS)} interaction={len(INTERACTION_AB_EXPERIMENT_SPECS)} "
    f"topq={len(TOPQ_SWEEP_EXPERIMENT_SPECS)} topq_revalid_2arm={len(TOPQ_REVALID_2ARM_EXPERIMENT_SPECS)} "
    f"stretch60={len(STRETCH60_EXPERIMENT_SPECS)} spread_recovery={len(SPREAD_RECOVERY_EXPERIMENT_SPECS)} "
    f"spread_recovery_tune={len(SPREAD_RECOVERY_TUNE_EXPERIMENT_SPECS)} objective_arch={len(OBJECTIVE_ARCH_EXPERIMENT_SPECS)} "
    f"hybrid_objective_recovery={len(HYBRID_OBJECTIVE_RECOVERY_EXPERIMENT_SPECS)} "
    f"signal_arch={len(SIGNAL_ARCH_EXPERIMENT_SPECS)} signal_arch_revalid={len(SIGNAL_ARCH_REVALID_EXPERIMENT_SPECS)} "
    f"spread_push={len(SPREAD_PUSH_EXPERIMENT_SPECS)} ic_floor_spread={len(IC_FLOOR_SPREAD_EXPERIMENT_SPECS)} "
    f"ifs_mid_revalid={len(IFS_MID_REVALID_EXPERIMENT_SPECS)} "
    f"p2_batch01={len(PHASE2_BATCH01_PORTFOLIO_CONFIGS)} "
    f"p2_batch02={len(PHASE2_BATCH02_PORTFOLIO_CONFIGS)} "
    f"p2_batch03={len(PHASE2_BATCH03_PORTFOLIO_CONFIGS)} "
    f"p2_batch04={len(PHASE2_BATCH04_PORTFOLIO_CONFIGS)} "
    f"p2_batch05={len(PHASE2_BATCH05_PORTFOLIO_CONFIGS)} "
    f"p2_batch06={len(PHASE2_BATCH06_PORTFOLIO_CONFIGS)} "
    f"p2_batch07={len(PHASE2_BATCH07_PORTFOLIO_CONFIGS)} "
    f"p2_batch08={len(PHASE2_BATCH08_PORTFOLIO_CONFIGS)} "
    f"p2_batch09={len(PHASE2_BATCH09_PORTFOLIO_CONFIGS)} "
    f"p2_batch10={len(PHASE2_BATCH10_PORTFOLIO_CONFIGS)} "
    f"p2_batch11={len(PHASE2_BATCH11_PORTFOLIO_CONFIGS)} "
    f"p2_batch11t={len(PHASE2_BATCH11T_PORTFOLIO_CONFIGS)} "
    f"ga_pop={EXPERIMENT_GA_POP} ga_gen={EXPERIMENT_GA_GEN}"
)
