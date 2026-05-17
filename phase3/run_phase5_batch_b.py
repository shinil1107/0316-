"""Phase B — overnight batch orchestrator (Batches 1 / 2 / 3 / 4 / 5 / 6 / 7 / 8).

Runs sequential GA retrains that explore the deployment-penalty design
space, leaving the rest of the Phase-5 recipe (patched formula, BULL
biases, stability seeds, population sizes) identical to
``run_phase5_retrain.py``.

Design
------
Batch 1 — **scalar profile sweep** on the base window
          (2012-01-01 → 2024-05-31):

    consv  w_turnover=0.40  w_cost=0.25  (≈1.33× T1b; upper bound)
    prop   w_turnover=0.15  w_cost=0.10  (≈0.50× T1b; Option-A mimic)
    aggr   w_turnover=0.05  w_cost=0.03  (≈0.17× T1b; near baseline)

Batch 2 — **scalar window sweep** on the PROP profile:

    win_base  2012-01-01 → 2024-05-31   seed B  (seed-variance of #2)
    win_fwd   2013-01-01 → 2025-05-31   seed B  (+1y forward-shift)
    win_back  2011-01-01 → 2023-05-31   seed B  (−1y backward-shift)

Batch 3 — **Phase B2 regime-conditional penalties** (Option 3a engine
          modification, 2026-04-24). Same base window + seed; varies the
          per-regime (BULL / SIDE / DEFENSIVE) ``w_turnover`` and
          ``w_cost`` fields introduced in the engine Cell 0:

    A-axis (BULL concentration tier):
       mild         w_to = (0.10, 0.30, 0.40)   w_co = (0.05, 0.20, 0.25)
       balanced     w_to = (0.15, 0.25, 0.35)   w_co = (0.10, 0.15, 0.20)
       deep         w_to = (0.00, 0.40, 0.50)   w_co = (0.00, 0.25, 0.30)

    B-axis (protection direction):
       bull_free    w_to = (0.00, 0.30, 0.40)   w_co = (0.00, 0.20, 0.25)
       def_heavy    w_to = (0.05, 0.15, 0.60)   w_co = (0.02, 0.10, 0.35)
       side_heavy   w_to = (0.05, 0.50, 0.30)   w_co = (0.02, 0.30, 0.15)

Batch 4 — **Phase B2.1 SIDE-specialist family** (added 2026-04-25):

       side_pure    w_to = (0.00, 0.70, 0.00)  w_co = (0.00, 0.40, 0.00)  base window
       side_deep    w_to = (0.02, 0.60, 0.20)  w_co = (0.01, 0.35, 0.10)  base window
       side_win     w_to = (0.05, 0.50, 0.30)  w_co = (0.02, 0.30, 0.15)  2014-01→2024-05

  Goal: produce a frozen signal that is best-in-class on SIDE folds (F2,
  F0b) so the engine can ensemble it as the SIDE component on top of
  V2_GOLDEN (analogous to T1b_BULL_INJECTED for BULL).  Output signals
  will be evaluated by t26 walk-forward + the sandbox composer in
  ``tests/p2_ensemble_composer.py`` after the batch completes.

Batch 5 — **Phase B2.2 SIDE-specialist v2 (anti-collapse + carry-over)**
          (added 2026-04-26):

  Diagnosis from B4 walk-forward + per-regime weight audit:
    1. All 3 B4 ws collapsed to 1-2 features (P5D_SIDE_PURE.ws=[24],
       SIDE_DEEP.ws=[24,27], SIDE_WIN.ws=[27]).  Root cause: extreme
       SIDE turnover penalty (0.6-0.7) drove GA to the 4 fundamental
       features (VAL_*, QUAL_ROE, CF_FCF_YIELD) which rebalance only
       quarterly (turnover ≈ 0).  All anti-collapse knobs (conc_penalty,
       entropy_bonus, weight_cap) auto-degenerate when k_regime=1
       (formula: ``conc_pen = lambda * max(0, maxw - 1/k)`` → 0 at k=1).
    2. F3 (BULL 76%) ENS_D CAGR −17.95pp vs Baseline traced to the
       sparse ws picking value-tilt stocks that under-perform on BULL
       carry-over days; F4 (BULL 63% post-OOS) showed +4.4pp BULL
       improvement → F3 result is in-sample anomaly.

  Strategy: split the SIDE feature pool into two non-overlapping halves
  to force GA into different feature regions.  After the batch, the
  composer averages the 2 new specialists with the 3 B4 specialists
  using regime-aware weights → dense ws (5+ features) by construction
  (mirrors V2_GOLDEN's ensemble-averaging path).

       side_tech       SIDE pool = tech_short ONLY (12 features, no
                       fundamental escape hatch).  Forces diversification
                       into 4-8 short-horizon technical features.
                       w_to=(0.05, 0.20, 0.20)  w_co=(0.03, 0.12, 0.12)
                       window 2014-01 → 2024-05  seed 20260601

       side_fund_brk   SIDE pool = fund + breakout/momentum (11 features,
                       NO tech_short).  Explicitly opens BULL-aligned
                       trend features (excluded from engine default
                       SIDE pool) to tackle BULL carry-over disruption.
                       w_to=(0.05, 0.30, 0.20)  w_co=(0.03, 0.18, 0.12)
                       factor_corr_penalty_lambda 0.10→0.05 (allow
                       cross-regime feature sharing); window 2012-01 →
                       2024-05; seed 20260602

  Each B5 preset uses a +60% budget vs B4 (ga_pop 300→400, generations
  20→25, stability 300×12 → 400×15, fast 100×8 → 120×10) — runtime ~3 h
  per preset, 6 h overnight total.  Anti-collapse knobs strengthened:
  conc_penalty 0.12 → 0.25; entropy_bonus 0.04 → 0.10.

Runtime
-------
Each GA run takes ~2.0–2.5 h on Apple Silicon. Batch 3 is designed to
run all 6 regime-conditional presets back-to-back in one ~12 h overnight
session. Batches 1 and 2 (scalar sweep) remain available for reference
and can still be re-run individually with ``--batch 1`` / ``--batch 2``.

Why Batch 3 (regime-conditional)?
---------------------------------
Batch 1 results (evaluated 2026-04-24) showed a monotonic BULL/SIDE
trade-off: lowering scalar ``w_turnover`` recovered BULL CAGR but
collapsed SIDE-dominant folds (F2 → +1-2%). Scalar sweeps cannot break
the trade-off because one weight governs all three regimes. Phase B2
splits the penalty tier-by-tier: BULL gets a near-zero drag (allowing
the momentum concentration Baseline_V2 exploits) while SIDE/DEFENSIVE
retain strong churn suppression.

Usage
-----
    # Full run (all batches back-to-back)
    python3 -u phase3/run_phase5_batch_b.py

    # Individual batches
    python3 -u phase3/run_phase5_batch_b.py --batch 1   # scalar profile sweep
    python3 -u phase3/run_phase5_batch_b.py --batch 2   # scalar window sweep
    python3 -u phase3/run_phase5_batch_b.py --batch 3   # regime-conditional (Phase B2)
    python3 -u phase3/run_phase5_batch_b.py --batch 4   # SIDE specialist (Phase B2.1)
    python3 -u phase3/run_phase5_batch_b.py --batch 6   # post-backfill retrain (Phase B3)

    # Pick specific runs by id
    python3 -u phase3/run_phase5_batch_b.py --runs mild,deep
    python3 -u phase3/run_phase5_batch_b.py --runs bull_free,side_heavy
    python3 -u phase3/run_phase5_batch_b.py --runs side_pure,side_deep,side_win

    # Enumerate configs and exit (useful from launcher UI)
    python3 -u phase3/run_phase5_batch_b.py --list

    # Tiny smoke (≈1-2 min per preset; 6 × 2 = 12 min total)
    python3 -u phase3/run_phase5_batch_b.py --dry-run

Progress log
------------
After every run, an aggregated status JSON is written to
``phase3/docs/phase_b_batch_progress_<stamp>.json`` so the user can check
overnight status without tailing stdout. On failure the orchestrator
records the traceback and continues with the next preset (fail-soft).

See ``phase3/docs/phase_b_batch_plan.md`` (Batches 1-2),
``phase3/docs/phase_b2_regime_cond_plan.md`` (Batch 3), and
``phase3/docs/phase_b2_1_side_specialist_plan.md`` (Batch 4) for the
full decision record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

from phase3.run_phase5_retrain import run_phase5_retrain  # noqa: E402


# ─── 6 preset configurations ────────────────────────────────────────
# NB: all presets keep Config defaults for BULL biases (all 6 ON) and the
#     patched GA formula (F1/F4/F11/S1). Only deployment-penalty scalars,
#     the training window, and the GA seed differ.

SEED_A = 20260428   # baseline seed (matches legacy P5_RETRAIN_T1b)
SEED_B = 20260429   # Batch-2 seed-variance run
SEED_C = 20260501   # Batch-3 Phase B2 regime-conditional
SEED_D = 20260502   # Batch-4 SIDE Specialist family (Phase B2.1)
SEED_E_TECH  = 20260601  # Batch-5 E1 (tech-only SIDE pool)
SEED_E_FUNDB = 20260602  # Batch-5 E2 (fund+breakout SIDE pool)

# Batch 6 — post-backfill retrain (expanded financials universe)
SEED_F_BL_10 = 20260701  # B6 baseline 10Y
SEED_F_BL_15 = 20260702  # B6 baseline 15Y
SEED_F_ST_10 = 20260703  # B6 side_tech 10Y
SEED_F_ST_15 = 20260704  # B6 side_tech 15Y
SEED_F_SF_10 = 20260705  # B6 side_fund_brk 10Y
SEED_F_SF_15 = 20260706  # B6 side_fund_brk 15Y


# ── Batch 5 SIDE feature pools (override engine default
# `side_allowed_factor_pool`).  E1 and E2 are non-overlapping by design so
# averaging their ws produces a 5+ feature SIDE slot.
B5_SIDE_POOL_TECH: tuple = (
    # 12 short-horizon technical features (engine default SIDE pool minus
    # the 4 fundamental "low-turnover escape hatches" VAL_*, QUAL_ROE,
    # CF_FCF_YIELD).  Forces the GA to find diversified tech alpha
    # rather than collapsing to quarterly-rebalanced fundamentals.
    "RSI", "MACD", "SMA_CROSS", "BBP", "CCI", "STOCH",
    "ATR_LOW", "MFI", "WILLR", "VWAP_ABOVE", "VOL_SPIKE", "OBV_POS",
)

B5_SIDE_POOL_FUND_BRK: tuple = (
    # 11 features: 4 fundamentals + 7 trend/breakout/long-momentum.
    # Engine default SIDE pool excludes mom_long/breakout because they
    # tend to fail in pure SIDE tape.  We re-admit them HERE on purpose
    # to test the BULL carry-over hypothesis (F3 ENS_D −17.95pp issue):
    # if SIDE picks share trend features with BULL, the carry-over from
    # SIDE → BULL days should be smoother.
    "VAL_EARN_YIELD", "VAL_BOOK2PRICE", "QUAL_ROE", "CF_FCF_YIELD",
    "MOM_3M", "MOM_6M", "MOM_12M_EX1M",
    "BREAKOUT_252", "BREAKOUT_126", "RSI_TREND", "SMA50_SLOPE",
)


# ── Anti-collapse + budget overrides applied to every Batch 5 preset.
# Knob diagnosis (B4 audit, 2026-04-26):
#   - conc_penalty 0.12 with formula  `lambda * max(0, maxw - 1/k)`
#     auto-degenerates to 0 when a regime collapses to k=1 (1/k=1.0,
#     maxw=1.0 → penalty=0).  Bumping to 0.25 widens the gradient against
#     near-collapse (k=2, maxw≥0.5 → penalty>0.03; was ~0.01).
#   - entropy_bonus 0.04 (F1 fix lowered from 0.08) similarly returns 0
#     for k=1.  Bumped to 0.10 to give a stronger pull toward k≥3 within
#     each regime.
#   - GA budget +60% (ga_pop 300→400, gen 20→25; refine 300×12→400×15;
#     fast 100×8→120×10) → ~3 h per preset; gives the larger SIDE search
#     space (12 / 11 features vs 16 default) headroom to converge.
B5_ANTI_COLLAPSE_OVERRIDES: Dict[str, Any] = {
    "conc_penalty":  0.25,
    "entropy_bonus": 0.10,
    "ga_population":  400,
    "ga_generations": 25,
    "stability_fast_population":   120,
    "stability_fast_generations":   10,
    "stability_refine_population":  400,
    "stability_refine_generations":  15,
}


def _rc(w_to_bsd: tuple, w_co_bsd: tuple) -> Dict[str, Any]:
    """Build a Phase-B2 regime-conditional override dict.

    ``w_to_bsd`` and ``w_co_bsd`` are 3-tuples ``(BULL, SIDE, DEFENSIVE)``.
    The engine Config (notebook Cell 0) interprets any non-None value as a
    per-regime override; fields left unset inherit the scalar ``w_turnover``
    / ``w_cost``. We also set ``w_turnover`` / ``w_cost`` to the SIDE weights
    (mid-value) so any legacy diagnostic that reads the scalar still sees a
    plausible number.

    The ``enable_deployment_penalty`` flag is left to inherit True from
    PHASE5_OVERRIDES in run_phase5_retrain.py.
    """
    b, s, d = w_to_bsd
    cb, cs, cd = w_co_bsd
    return {
        # Scalar placeholder for legacy telemetry / diagnostics
        "w_turnover": float(s),
        "w_cost":     float(cs),
        # Phase B2 per-regime overrides
        "w_turnover_bull": float(b),
        "w_turnover_side": float(s),
        "w_turnover_def":  float(d),
        "w_cost_bull":     float(cb),
        "w_cost_side":     float(cs),
        "w_cost_def":      float(cd),
    }


def _b5(
    w_to_bsd: tuple,
    w_co_bsd: tuple,
    side_pool: tuple,
    factor_corr_lambda: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a Batch-5 override dict.

    Layers regime-conditional turnover/cost (same as :func:`_rc`) on top
    of the :data:`B5_ANTI_COLLAPSE_OVERRIDES` block, plus a SIDE-only
    feature pool override.  Optional ``factor_corr_lambda`` overrides
    the global ``factor_corr_penalty_lambda`` (engine default 0.08, but
    PHASE5_OVERRIDES bumps to 0.10) — used by E2 to explicitly allow
    BULL/SIDE feature sharing for carry-over alignment.
    """
    rc = _rc(w_to_bsd, w_co_bsd)
    rc.update(B5_ANTI_COLLAPSE_OVERRIDES)
    rc["side_allowed_factor_pool"] = tuple(side_pool)
    if factor_corr_lambda is not None:
        rc["factor_corr_penalty_lambda"] = float(factor_corr_lambda)
    return rc


# Batch 7 — V2-recipe reproduction on expanded data (Phase 2 Breakthrough)
SEED_G_FULL    = 20260801
SEED_G_NODEP   = 20260802
SEED_G_MEGA    = 20260803
SEED_G_BULLAGG = 20260804

# Batch 8 — targeted specialist sweep for V2 breakthrough
SEED_H_SIDE = 20260901   # B8 SIDE specialist v3
SEED_H_BULL = 20260902   # B8 BULL dense specialist
SEED_H_BAL  = 20260903   # B8 balanced specialist

P7_V2_RECIPE_BASE: Dict[str, Any] = {
    # ── Meta search ON (the critical missing piece from P5/P6) ──
    "enable_meta_search": True,
    "meta_search_mode": "TEMPLATE_PLUS_RANDOM",
    "meta_search_trials": 8,
    "meta_disabled_template_name": "TPL_SPREAD",

    # Meta candidate spaces (must include the base entropy_bonus=0.10)
    "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10),
    "meta_conc_penalty_candidates": (0.08, 0.10, 0.12, 0.14),
    "meta_alpha_floor_candidates": (0.12, 0.18, 0.22, 0.28, 0.34, 0.40),
    "meta_top_quantile_candidates": (0.10, 0.12, 0.15, 0.18, 0.20, 0.22),
    "meta_factor_corr_penalty_lambda_candidates": (0.04, 0.06, 0.08, 0.10),

    # Meta perturbation flags
    "meta_perturb_alpha_floor": True,
    "meta_perturb_top_quantile": True,
    "meta_perturb_corr_penalty": True,
    "meta_perturb_entropy_bonus": True,
    "meta_perturb_conc_penalty": True,

    # Meta scoring weights (from P2_BATCH11)
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

    # ── GA fitness recipe ──
    "entropy_bonus": 0.10,
    "conc_penalty": 0.12,
    "weight_cap": 0.40,
    "factor_corr_penalty_lambda": 0.10,

    # ── BULL floor tuning ──
    "bull_min_spread_mix": 0.006,
    "bull_penalty_lambda_spread": 1.25,

    "top_quantile": 0.12,
    "w_ic1": 0.34, "w_ic3": 0.34, "w_spread": 0.32,

    "enable_fitness_risk_penalty": True,
    "fitness_downside_vol_lambda": 0.50,
    "fitness_max_neg_spread_ratio_lambda": 0.30,

    "enable_deployment_penalty": True,
    "w_turnover": 0.3, "w_cost": 0.2,

    # ── Generous budget ──
    "ga_population": 500,
    "ga_generations": 25,
    "stability_seed_runs": 8,
    "stability_top_n_seeds": 6,
    "stability_fast_population": 200,
    "stability_fast_generations": 12,
    "stability_refine_population": 500,
    "stability_refine_generations": 15,

    "use_random_seed": True,
}


def _p7(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Layer extra overrides on top of P7_V2_RECIPE_BASE."""
    ov = dict(P7_V2_RECIPE_BASE)
    if extra:
        ov.update(extra)
    return ov


def _p8_side() -> Dict[str, Any]:
    """Batch 8a — SIDE specialist v3 with meta-search ON.

    Key differences from B5 SIDE specialists:
      - Meta-search ON (B5 had it off → sparse collapse to k=1-2)
      - Higher entropy_bonus candidate space (0.08-0.14)
      - Regime-conditional turnover: SIDE low (0.05), BULL/DEF near zero
      - Uses full SIDE pool (no feature restriction) to let meta-search decide
      - Generous budget matching B7
    """
    base = dict(P7_V2_RECIPE_BASE)
    base.update({
        # Regime-conditional: suppress SIDE turnover, free BULL/DEF
        "w_turnover_bull": 0.00,
        "w_turnover_side": 0.05,
        "w_turnover_def":  0.00,
        "w_cost_bull":     0.00,
        "w_cost_side":     0.05,
        "w_cost_def":      0.00,
        "w_turnover":      0.05,
        "w_cost":          0.05,
        "enable_deployment_penalty": True,
        # Higher entropy to avoid collapse
        "entropy_bonus": 0.10,
        "conc_penalty":  0.25,
        "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10, 0.12, 0.14),
        "meta_conc_penalty_candidates":  (0.08, 0.10, 0.12, 0.14, 0.18, 0.22, 0.25),
        # Stronger spread emphasis for SIDE differentiation
        "meta_score_w_spread": 3.00,
    })
    return base


def _p8_bull() -> Dict[str, Any]:
    """Batch 8b — BULL dense specialist targeting k=15+ features.

    Key differences from B7:
      - Much higher entropy_bonus (0.15) and candidate range (0.10-0.18)
      - Higher conc_penalty (0.18) to punish sparse solutions
      - Lower weight_cap (0.25) to force spreading across features
      - Larger GA budget for convergence at higher feature counts
    """
    base = dict(P7_V2_RECIPE_BASE)
    base.update({
        "entropy_bonus": 0.15,
        "conc_penalty":  0.18,
        "weight_cap":    0.25,
        "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18),
        "meta_conc_penalty_candidates":  (0.08, 0.10, 0.12, 0.14, 0.15, 0.18, 0.22),
        # Larger budget for dense feature space exploration
        "ga_population": 700,
        "ga_generations": 30,
        "stability_seed_runs": 10,
        "stability_top_n_seeds": 8,
        "stability_fast_population": 250,
        "stability_fast_generations": 15,
        "stability_refine_population": 700,
        "stability_refine_generations": 18,
    })
    return base


def _p8_balanced() -> Dict[str, Any]:
    """Batch 8c — full-regime balanced specialist.

    Based on P7_V2_NO_DEPLOY (best B7 mean=29.74%) with modifications:
      - Deployment penalty OFF (more freedom for turnover)
      - Stronger downside vol penalty (1.0 vs 0.5) for tail-risk defense
      - Higher spread weight in meta-scoring (3.0 vs 2.5)
      - All 3 slots (wb/ws/wd) usable as ensemble ingredients
    """
    base = dict(P7_V2_RECIPE_BASE)
    base.update({
        "enable_deployment_penalty": False,
        "w_turnover": 0.0,
        "w_cost":     0.0,
        "fitness_downside_vol_lambda":       1.00,
        "fitness_max_neg_spread_ratio_lambda": 0.50,
        "meta_score_w_spread": 3.00,
        "meta_score_w_turnover": 0.0,
        # Slightly higher entropy for feature diversity
        "entropy_bonus": 0.12,
        "meta_entropy_bonus_candidates": (0.04, 0.06, 0.08, 0.10, 0.12, 0.14),
    })
    return base


BATCH_CONFIGS: List[Dict[str, Any]] = [
    # ──────────────── Batch 1 — profile sweep (base window) ───────────────
    {
        "id": "consv",
        "batch": 1,
        "tag": "P5B_CONSV",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_CONSV",
        "overrides": {"w_turnover": 0.40, "w_cost": 0.25},
        "intent": "Upper-bound stability test (1.33× T1b).",
    },
    {
        "id": "prop",
        "batch": 1,
        "tag": "P5B_PROP",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_PROP",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "Proposed balanced profile (0.5× T1b; Option-A mimic).",
    },
    {
        "id": "aggr",
        "batch": 1,
        "tag": "P5B_AGGR",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_AGGR",
        "overrides": {"w_turnover": 0.05, "w_cost": 0.03},
        "intent": "Near-baseline loosening (0.17× T1b).",
    },
    # ──────────────── Batch 2 — window sweep (PROP profile) ───────────────
    {
        "id": "win_base",
        "batch": 2,
        "tag": "P5B_WIN_BASE",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_BASE",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "Seed-variance re-run of PROP on the base window.",
    },
    {
        "id": "win_fwd",
        "batch": 2,
        "tag": "P5B_WIN_FWD",
        "train_start": datetime(2013, 1, 3),
        "train_end":   datetime(2025, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_FWD",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "+1y forward-shift window (includes more 2025 BULL tape).",
    },
    {
        "id": "win_back",
        "batch": 2,
        "tag": "P5B_WIN_BACK",
        "train_start": datetime(2011, 1, 3),
        "train_end":   datetime(2023, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_BACK",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "−1y backward-shift window (more 2011-2013 recovery tape).",
    },
    # ────── Batch 3 — Phase B2 regime-conditional penalties (Option 3a) ──
    # A-axis: BULL-concentration tier (allowable BULL turnover descending).
    {
        "id": "mild",
        "batch": 3,
        "tag": "P5C_MILD",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_MILD",
        "overrides": _rc(w_to_bsd=(0.10, 0.30, 0.40), w_co_bsd=(0.05, 0.20, 0.25)),
        "intent": "Mild BULL relaxation; SIDE/DEF held at T1b baseline.",
    },
    {
        "id": "balanced",
        "batch": 3,
        "tag": "P5C_BALANCED",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_BALANCED",
        "overrides": _rc(w_to_bsd=(0.15, 0.25, 0.35), w_co_bsd=(0.10, 0.15, 0.20)),
        "intent": "Regime-tiered version of P5B_PROP (0.5× T1b gradient).",
    },
    {
        "id": "deep",
        "batch": 3,
        "tag": "P5C_DEEP",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_DEEP",
        "overrides": _rc(w_to_bsd=(0.00, 0.40, 0.50), w_co_bsd=(0.00, 0.25, 0.30)),
        "intent": "BULL fully free + aggressive SIDE/DEF churn suppression.",
    },
    # B-axis: protection-direction tier (where the penalty is concentrated).
    {
        "id": "bull_free",
        "batch": 3,
        "tag": "P5C_BULL_FREE",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_BULL_FREE",
        "overrides": _rc(w_to_bsd=(0.00, 0.30, 0.40), w_co_bsd=(0.00, 0.20, 0.25)),
        "intent": "BULL absolutely free × SIDE/DEF at T1b baseline (MILD w/ BULL=0).",
    },
    {
        "id": "def_heavy",
        "batch": 3,
        "tag": "P5C_DEF_HEAVY",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_DEF_HEAVY",
        "overrides": _rc(w_to_bsd=(0.05, 0.15, 0.60), w_co_bsd=(0.02, 0.10, 0.35)),
        "intent": "DEF-churn isolated as primary cost driver; SIDE relaxed.",
    },
    {
        "id": "side_heavy",
        "batch": 3,
        "tag": "P5C_SIDE_HEAVY",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_SIDE_HEAVY",
        "overrides": _rc(w_to_bsd=(0.05, 0.50, 0.30), w_co_bsd=(0.02, 0.30, 0.15)),
        "intent": "F2 SIDE-collapse direct attack (strongest SIDE churn penalty).",
    },
    # ──── Batch 4 — SIDE Specialist family (Phase B2.1, 2026-04-25) ────
    # Purpose: train GA signals specifically engineered for the SIDE
    # regime, where Baseline_V2 underperforms (+18.7% AnnRet, weakest of
    # 3 regimes).  Result will be ensembled with V2_GOLDEN's wb/wd in a
    # SIDE-only override (analog of the surgical bull-injection used for
    # T1b_BULL_INJECTED).  Keep BULL/DEF penalties near zero so the GA's
    # selection pressure is concentrated on SIDE-quality alpha.
    #
    # Three complementary points spanning the SIDE-spec design space:
    #   side_pure  : extreme SIDE concentration, BULL/DEF effectively off
    #   side_deep  : harder SIDE than P5C_SIDE_HEAVY w/ small BULL/DEF tax
    #   side_win   : retrain the previous SIDE-heavy preset on a SIDE-rich
    #                window (2014-01 → 2024-05; drops F0a/F0b BULL recovery)
    {
        "id": "side_pure",
        "batch": 4,
        "tag": "P5D_SIDE_PURE",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_D,
        "excel_prefix": "SP500_P5D_SIDE_PURE",
        "overrides": _rc(w_to_bsd=(0.00, 0.70, 0.00), w_co_bsd=(0.00, 0.40, 0.00)),
        "intent": "SIDE-only penalty; BULL/DEF zeroed → max SIDE specialisation.",
    },
    {
        "id": "side_deep",
        "batch": 4,
        "tag": "P5D_SIDE_DEEP",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_D,
        "excel_prefix": "SP500_P5D_SIDE_DEEP",
        "overrides": _rc(w_to_bsd=(0.02, 0.60, 0.20), w_co_bsd=(0.01, 0.35, 0.10)),
        "intent": "Harder SIDE than P5C_SIDE_HEAVY; small BULL/DEF tax preserved.",
    },
    {
        "id": "side_win",
        "batch": 4,
        "tag": "P5D_SIDE_WIN",
        "train_start": datetime(2014, 1, 3),     # +2y forward shift
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_D,
        "excel_prefix": "SP500_P5D_SIDE_WIN",
        "overrides": _rc(w_to_bsd=(0.05, 0.50, 0.30), w_co_bsd=(0.02, 0.30, 0.15)),
        "intent": "Re-train P5C_SIDE_HEAVY recipe on a SIDE-rich (2014-2024) window.",
    },
    # ──── Batch 5 — SIDE Specialist v2 (Phase B2.2, 2026-04-26) ────
    # Anti-collapse + BULL carry-over fix.  Each preset uses +60% GA
    # budget (~3 h each, ~6 h overnight total) and bumps anti-collapse
    # knobs (conc_penalty 0.12→0.25, entropy_bonus 0.04→0.10) on top of
    # a SIDE-only feature pool override that splits the engine default
    # SIDE pool (16 features) into two non-overlapping halves.  After
    # the batch, the composer averages B4+B5 SIDE specialists with
    # regime-aware weights to produce a dense ws by construction.
    #
    # E1 / side_tech       : forces tech-only SIDE alpha (no
    #                        fundamental escape hatch → diversifies into
    #                        4-8 short-horizon technical features).
    # E2 / side_fund_brk   : explicitly admits trend/breakout/long-mom
    #                        features (excluded from engine default
    #                        SIDE pool) → BULL carry-over alignment.
    {
        "id": "side_tech",
        "batch": 5,
        "tag": "P5E_SIDE_TECH",
        "train_start": datetime(2014, 1, 3),     # SIDE-rich window
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_E_TECH,
        "excel_prefix": "SP500_P5E_SIDE_TECH",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.20, 0.20),
            w_co_bsd=(0.03, 0.12, 0.12),
            side_pool=B5_SIDE_POOL_TECH,
        ),
        "intent": "Tech-only SIDE pool (12 short-horizon technicals); "
                  "moderate SIDE turnover (0.20) to avoid collapse path.",
    },
    {
        "id": "side_fund_brk",
        "batch": 5,
        "tag": "P5E_SIDE_FUND_BREAKOUT",
        "train_start": datetime(2012, 1, 3),     # full base window
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_E_FUNDB,
        "excel_prefix": "SP500_P5E_SIDE_FUND_BRK",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.30, 0.20),
            w_co_bsd=(0.03, 0.18, 0.12),
            side_pool=B5_SIDE_POOL_FUND_BRK,
            factor_corr_lambda=0.05,             # allow BULL/SIDE feature sharing
        ),
        "intent": "Fund + breakout/momentum SIDE pool; BULL carry-over "
                  "alignment via lower factor_corr_penalty (0.10→0.05).",
    },
    # ──── Batch 6 — Post-backfill retrain (Phase B3, 2026-04-27) ────
    # The legacy-ticker financials backfill expanded fundamental coverage
    # from ~508 to ~1382 tickers.  Old training packs lack this data, so
    # `force_rebuild_pack=True` is strongly recommended.
    #
    # 3 GA configs × 2 time windows = 6 presets.
    #   Baseline (T1b recipe)   — the main frozen signal config
    #   SIDE Tech (B5 E1)       — tech-only SIDE pool
    #   SIDE Fund+Brk (B5 E2)  — fund+breakout SIDE pool
    #
    # Windows:
    #   10Y: 2016-01-01 → 2026-03-31  (recent decade)
    #   15Y: 2011-01-01 → 2026-03-31  (full backfill span)
    {
        "id": "b6_baseline_10y",
        "batch": 6,
        "tag": "P6_BASELINE_10Y",
        "train_start": datetime(2016, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_BL_10,
        "excel_prefix": "SP500_P6_BASELINE_10Y",
        "overrides": {
            "w_turnover": 0.3,
            "w_cost":     0.2,
        },
        "intent": "Baseline T1b recipe on 10Y window with expanded financials.",
    },
    {
        "id": "b6_baseline_15y",
        "batch": 6,
        "tag": "P6_BASELINE_15Y",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_BL_15,
        "excel_prefix": "SP500_P6_BASELINE_15Y",
        "overrides": {
            "w_turnover": 0.3,
            "w_cost":     0.2,
        },
        "intent": "Baseline T1b recipe on 15Y window with expanded financials.",
    },
    {
        "id": "b6_side_tech_10y",
        "batch": 6,
        "tag": "P6_SIDE_TECH_10Y",
        "train_start": datetime(2016, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_ST_10,
        "excel_prefix": "SP500_P6_SIDE_TECH_10Y",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.20, 0.20),
            w_co_bsd=(0.03, 0.12, 0.12),
            side_pool=B5_SIDE_POOL_TECH,
        ),
        "intent": "SIDE tech-only pool on 10Y window with expanded financials.",
    },
    {
        "id": "b6_side_tech_15y",
        "batch": 6,
        "tag": "P6_SIDE_TECH_15Y",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_ST_15,
        "excel_prefix": "SP500_P6_SIDE_TECH_15Y",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.20, 0.20),
            w_co_bsd=(0.03, 0.12, 0.12),
            side_pool=B5_SIDE_POOL_TECH,
        ),
        "intent": "SIDE tech-only pool on 15Y window with expanded financials.",
    },
    {
        "id": "b6_side_fund_10y",
        "batch": 6,
        "tag": "P6_SIDE_FUND_10Y",
        "train_start": datetime(2016, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_SF_10,
        "excel_prefix": "SP500_P6_SIDE_FUND_10Y",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.30, 0.20),
            w_co_bsd=(0.03, 0.18, 0.12),
            side_pool=B5_SIDE_POOL_FUND_BRK,
            factor_corr_lambda=0.05,
        ),
        "intent": "SIDE fund+breakout pool on 10Y window with expanded financials.",
    },
    {
        "id": "b6_side_fund_15y",
        "batch": 6,
        "tag": "P6_SIDE_FUND_15Y",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_F_SF_15,
        "excel_prefix": "SP500_P6_SIDE_FUND_15Y",
        "overrides": _b5(
            w_to_bsd=(0.05, 0.30, 0.20),
            w_co_bsd=(0.03, 0.18, 0.12),
            side_pool=B5_SIDE_POOL_FUND_BRK,
            factor_corr_lambda=0.05,
        ),
        "intent": "SIDE fund+breakout pool on 15Y window with expanded financials.",
    },
    # ──── Batch 7 — V2-recipe reproduction on expanded 15Y data (2026-04-28) ────
    # Reproduce P2_BATCH11 recipe (meta search ON, entropy 0.10, BULL spread
    # tuning, generous budget) that originally produced Baseline_V2, now with
    # 875 legacy tickers' financials backfilled.  4 variants explore the
    # design space for Phase 3 L3 ensemble composition.
    {
        "id": "p7_v2_full",
        "batch": 7,
        "tag": "P7_V2_FULL",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_G_FULL,
        "excel_prefix": "SP500_P7_V2_FULL",
        "overrides": _p7(),
        "intent": "Full V2 recipe (meta ON, entropy 0.10, BULL tuning) on expanded 15Y data.",
    },
    {
        "id": "p7_v2_no_deploy",
        "batch": 7,
        "tag": "P7_V2_NO_DEPLOY",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_G_NODEP,
        "excel_prefix": "SP500_P7_V2_NO_DEPLOY",
        "overrides": _p7({"enable_deployment_penalty": False, "w_turnover": 0.0, "w_cost": 0.0}),
        "intent": "V2 recipe with deployment penalty OFF (test if it costs CAGR).",
    },
    {
        "id": "p7_v2_mega",
        "batch": 7,
        "tag": "P7_V2_MEGA",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_G_MEGA,
        "excel_prefix": "SP500_P7_V2_MEGA",
        "overrides": _p7({
            "ga_population": 800,
            "ga_generations": 30,
            "stability_seed_runs": 12,
            "stability_top_n_seeds": 8,
            "stability_fast_population": 300,
            "stability_fast_generations": 15,
            "stability_refine_population": 800,
            "stability_refine_generations": 20,
        }),
        "intent": "V2 recipe with 2x GA budget (pop 800, gen 30, 12 seeds).",
    },
    {
        "id": "p7_v2_bull_agg",
        "batch": 7,
        "tag": "P7_V2_BULL_AGG",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_G_BULLAGG,
        "excel_prefix": "SP500_P7_V2_BULL_AGG",
        "overrides": _p7({
            "bull_min_spread_mix": 0.008,
            "bull_penalty_lambda_spread": 1.50,
        }),
        "intent": "V2 recipe with aggressive BULL tuning (spread_mix 0.008, lambda 1.50).",
    },
    # ──── Batch 8 — targeted specialist sweep for V2 breakthrough (2026-05-03) ────
    # Diagnosis from Phase 3 walk-forward: F2 (SIDE 72%) is the single fold
    # that drags all B7 signals below Baseline_V2.  B7 wb vectors are sparse
    # (k=3-6) vs V2's k=15.  Three complementary recipes:
    #
    #   8a / b8_side_v3   : SIDE specialist with meta-search ON + expanded data.
    #                       Goal: produce a ws that beats V2's ws on F2.
    #   8b / b8_bull_dense: High-entropy BULL specialist targeting k=15+ dense wb.
    #                       Goal: reproduce V2's multi-feature BULL robustness.
    #   8c / b8_balanced  : Full-regime balanced with downside vol penalty.
    #                       Goal: a single signal usable in all 3 slots.
    {
        "id": "b8_side_v3",
        "batch": 8,
        "tag": "P8_SIDE_V3",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_H_SIDE,
        "excel_prefix": "SP500_P8_SIDE_V3",
        "overrides": _p8_side(),
        "intent": "SIDE specialist v3: meta ON + high entropy on expanded 15Y data.",
    },
    {
        "id": "b8_bull_dense",
        "batch": 8,
        "tag": "P8_BULL_DENSE",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_H_BULL,
        "excel_prefix": "SP500_P8_BULL_DENSE",
        "overrides": _p8_bull(),
        "intent": "High-entropy BULL specialist targeting dense wb (k=15+).",
    },
    {
        "id": "b8_balanced",
        "batch": 8,
        "tag": "P8_BALANCED",
        "train_start": datetime(2011, 1, 1),
        "train_end":   datetime(2026, 3, 31),
        "ga_seed": SEED_H_BAL,
        "excel_prefix": "SP500_P8_BALANCED",
        "overrides": _p8_balanced(),
        "intent": "Balanced specialist: downside-vol penalty, NO deploy penalty, spread-heavy.",
    },
]


DOCS_DIR = os.path.join(HERE, "docs")


def _find_configs(batch: Optional[int], runs: Optional[List[str]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for c in BATCH_CONFIGS:
        if batch is not None and c["batch"] != batch:
            continue
        if runs is not None and c["id"] not in runs:
            continue
        selected.append(c)
    return selected


def _fmt_overrides(ov: Dict[str, Any]) -> str:
    """Compact one-line summary of the penalty overrides in ``ov``.

    Regime-conditional dict (Batch 3) prints as
    ``w_to=(B=0.00,S=0.40,D=0.50) w_co=(B=0.00,S=0.25,D=0.30)``.
    Scalar-only (Batches 1-2) prints the original ``w_to=X w_co=Y``.
    """
    has_rc = any(
        k in ov for k in (
            "w_turnover_bull", "w_turnover_side", "w_turnover_def",
            "w_cost_bull", "w_cost_side", "w_cost_def",
        )
    )
    if has_rc:
        b = ov.get("w_turnover_bull", ov.get("w_turnover", 0.0))
        s = ov.get("w_turnover_side", ov.get("w_turnover", 0.0))
        d = ov.get("w_turnover_def",  ov.get("w_turnover", 0.0))
        cb = ov.get("w_cost_bull",    ov.get("w_cost", 0.0))
        cs = ov.get("w_cost_side",    ov.get("w_cost", 0.0))
        cd = ov.get("w_cost_def",     ov.get("w_cost", 0.0))
        return (
            f"w_to=(B={float(b):.2f},S={float(s):.2f},D={float(d):.2f}) "
            f"w_co=(B={float(cb):.2f},S={float(cs):.2f},D={float(cd):.2f})"
        )
    w_to = ov.get("w_turnover", 0.3)
    w_co = ov.get("w_cost", 0.2)
    return f"w_to={w_to} w_co={w_co}"


def _print_header(configs: List[Dict[str, Any]], dry_run: bool) -> None:
    print("=" * 78)
    print("  Phase B — overnight batch orchestrator (Batches 1-8)")
    print("=" * 78)
    print(f"  mode         : {'DRY-RUN (per-run ~1-2 min)' if dry_run else 'FULL (B1-4 ~2.0-2.5 h, B5-6 ~3.0 h)'}")
    print(f"  runs queued  : {len(configs)}")
    for c in configs:
        print(
            f"    [B{c['batch']}] {c['id']:<10s} tag={c['tag']:<16s} "
            f"window={c['train_start'].date()} → {c['train_end'].date()}  "
            f"seed={c['ga_seed']}  {_fmt_overrides(c['overrides'])}"
        )
    print("=" * 78)


def _write_progress(progress_path: str, payload: Dict[str, Any]) -> None:
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(progress_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as exc:
        print(f"[batch] warn: failed to write progress file: {exc}")


def run_batch(
    batch: Optional[int] = None,
    runs: Optional[List[str]] = None,
    dry_run: bool = False,
    force_rebuild_pack: bool = False,
) -> Dict[str, Any]:
    os.makedirs(DOCS_DIR, exist_ok=True)
    configs = _find_configs(batch, runs)
    if not configs:
        print("[batch] no configs matched filter — nothing to do.")
        return {"status": "empty", "results": []}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    progress_path = os.path.join(DOCS_DIR, f"phase_b_batch_progress_{stamp}.json")

    _print_header(configs, dry_run)

    results: List[Dict[str, Any]] = []
    t_batch0 = time.time()
    progress = {
        "batch_started_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "n_queued": len(configs),
        "queue": [c["id"] for c in configs],
        "current": None,
        "completed": [],
        "failed": [],
        "results": results,
    }
    _write_progress(progress_path, progress)

    for idx, cfg in enumerate(configs, 1):
        print()
        print("#" * 78)
        print(f"#  RUN {idx}/{len(configs)}  —  {cfg['tag']}  ({cfg['intent']})")
        print("#" * 78)
        progress["current"] = {
            "id": cfg["id"],
            "tag": cfg["tag"],
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_progress(progress_path, progress)

        t0 = time.time()
        try:
            res = run_phase5_retrain(
                dry_run=dry_run,
                force_rebuild_pack=force_rebuild_pack,
                train_start=cfg["train_start"],
                train_end=cfg["train_end"],
                run_tag=cfg["tag"],
                ga_seed=cfg["ga_seed"],
                extra_overrides=cfg["overrides"],
                excel_prefix=cfg.get("excel_prefix"),
            )
            elapsed = time.time() - t0
            rec = {
                "id": cfg["id"],
                "tag": cfg["tag"],
                "batch": cfg["batch"],
                "status": "ok",
                "elapsed_sec": round(elapsed, 1),
                "elapsed_min": round(elapsed / 60.0, 1),
                "frozen_signal_path": res.get("frozen_signal_path"),
                "run_log_path":       res.get("run_log_path"),
                "signal_quality":     res.get("signal_quality"),
                "w_turnover":      cfg["overrides"].get("w_turnover"),
                "w_cost":          cfg["overrides"].get("w_cost"),
                "w_turnover_bull": cfg["overrides"].get("w_turnover_bull"),
                "w_turnover_side": cfg["overrides"].get("w_turnover_side"),
                "w_turnover_def":  cfg["overrides"].get("w_turnover_def"),
                "w_cost_bull":     cfg["overrides"].get("w_cost_bull"),
                "w_cost_side":     cfg["overrides"].get("w_cost_side"),
                "w_cost_def":      cfg["overrides"].get("w_cost_def"),
                "train_window": [
                    cfg["train_start"].strftime("%Y-%m-%d"),
                    cfg["train_end"].strftime("%Y-%m-%d"),
                ],
                "ga_seed": cfg["ga_seed"],
            }
            results.append(rec)
            progress["completed"].append(cfg["id"])
            print(f"\n[batch] {cfg['tag']} OK in {elapsed/60.0:.1f} min")
        except Exception as exc:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            print(f"\n[batch] !! {cfg['tag']} FAILED after {elapsed/60.0:.1f} min: {exc}")
            print(tb)
            rec = {
                "id": cfg["id"],
                "tag": cfg["tag"],
                "batch": cfg["batch"],
                "status": "failed",
                "elapsed_sec": round(elapsed, 1),
                "error": str(exc),
                "traceback": tb,
            }
            results.append(rec)
            progress["failed"].append(cfg["id"])
            # fail-soft: continue to next preset
        progress["current"] = None
        _write_progress(progress_path, progress)

    total_sec = time.time() - t_batch0
    progress["batch_finished_at"] = datetime.now().isoformat(timespec="seconds")
    progress["total_sec"] = round(total_sec, 1)
    progress["total_min"] = round(total_sec / 60.0, 1)
    _write_progress(progress_path, progress)

    print()
    print("=" * 78)
    print("  BATCH SUMMARY")
    print("=" * 78)
    print(f"  total elapsed   : {total_sec/60.0:.1f} min ({total_sec/3600.0:.2f} h)")
    print(f"  completed       : {len(progress['completed'])}/{len(configs)}")
    if progress["failed"]:
        print(f"  FAILED          : {progress['failed']}")
    print()
    print(f"  {'tag':<18s} {'status':<8s} {'elapsed':>8s}  frozen_signal")
    print("  " + "-" * 76)
    for r in results:
        sig = (os.path.basename(r.get("frozen_signal_path") or "")) or "—"
        print(
            f"  {r['tag']:<18s} {r['status']:<8s} "
            f"{r.get('elapsed_min', 0):>7.1f}m  {sig}"
        )
    print("=" * 78)
    print(f"[batch] progress log → {progress_path}")

    return {
        "status": "done",
        "progress_path": progress_path,
        "results": results,
        "n_completed": len(progress["completed"]),
        "n_failed": len(progress["failed"]),
        "total_min": round(total_sec / 60.0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B — overnight batch orchestrator (Batches 1-8)")
    parser.add_argument(
        "--batch", type=int, choices=(1, 2, 3, 4, 5, 6, 7, 8),
        help="Run only a specific batch. "
             "Omit to run all batches back-to-back.",
    )
    parser.add_argument(
        "--runs", type=str,
        help="Comma-separated subset of run ids: "
             "consv,prop,aggr,win_base,win_fwd,win_back,"
             "mild,balanced,deep,bull_free,def_heavy,side_heavy,"
             "side_pure,side_deep,side_win,"
             "side_tech,side_fund_brk,"
             "b6_baseline_10y,b6_baseline_15y,"
             "b6_side_tech_10y,b6_side_tech_15y,"
             "b6_side_fund_10y,b6_side_fund_15y,"
             "p7_v2_full,p7_v2_no_deploy,p7_v2_mega,p7_v2_bull_agg,"
             "b8_side_v3,b8_bull_dense,b8_balanced",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Tiny GA per preset (1-2 min each) to validate wiring end-to-end.",
    )
    parser.add_argument(
        "--force-rebuild-pack", action="store_true",
        help="Delete existing training pack for each run before prepare_inputs.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print all presets (6 batches) and exit.",
    )
    args = parser.parse_args()

    if args.list:
        for c in BATCH_CONFIGS:
            print(
                f"[B{c['batch']}] {c['id']:<10s} tag={c['tag']:<16s} "
                f"window={c['train_start'].date()} → {c['train_end'].date()}  "
                f"seed={c['ga_seed']}  {_fmt_overrides(c['overrides'])}  "
                f"| {c['intent']}"
            )
        return 0

    runs = None
    if args.runs:
        runs = [x.strip() for x in args.runs.split(",") if x.strip()]
        valid_ids = {c["id"] for c in BATCH_CONFIGS}
        bad = [r for r in runs if r not in valid_ids]
        if bad:
            print(f"[batch] unknown run ids: {bad}")
            print(f"[batch] valid: {sorted(valid_ids)}")
            return 2

    out = run_batch(
        batch=args.batch,
        runs=runs,
        dry_run=args.dry_run,
        force_rebuild_pack=args.force_rebuild_pack,
    )
    return 0 if out.get("n_failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
