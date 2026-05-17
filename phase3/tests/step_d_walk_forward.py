"""Step D — Walk-Forward Evaluation (T5 Phase A).

Evaluates frozen signals across multiple fold sets on the 14-year pack
(2011-01-03 → 2026-02-27).

Fold sets (``--fold-set``):

  **default** — 6-fold design (Batch 7/8, training: 2011-01 → 2026-03):
    F0a  2012-01-01 → 2014-12-31   early in-sample
    F0b  2015-01-01 → 2016-12-31   early in-sample
    F1   2019-01-01 → 2020-12-31   core in-sample
    F2   2021-01-01 → 2022-12-31   core in-sample
    F3   2023-01-01 → 2024-05-31   core in-sample
    F4   2024-06-01 → pack end     late in-sample (NOT true OOS)
    
    **주의**: 6개 fold 전부 in-sample (training ~ 2026-03-31)
             진정한 OOS = Step C + live production만 가능

  **rolling** — sliding 8-year windows (in-sample temporal stability audit):
    R1  2011-01-01 → 2018-12-31   sliding (8yr)
    R2  2012-01-01 → 2019-12-31   sliding (8yr)
    R3  2013-01-01 → 2020-12-31   sliding (8yr)
    R4  2014-01-01 → 2021-12-31   sliding (8yr)
    R5  2015-01-01 → 2022-12-31   sliding (8yr)
    R6  2016-01-01 → 2023-12-31   sliding (8yr)
    R7  2017-01-01 → 2024-12-31   sliding (8yr)
    R8  2018-01-01 → pack end     sliding (8yr)

    All folds are in-sample (GA training: 2011 → 2026-03).
    Purpose: temporal stability audit — is the signal's performance stable
    as the 8-year evaluation window slides forward by 1 year?
    NOT OOS validation; true OOS requires Phase B P9_OOS_VALIDATION.

  **regime** — regime-classified windows (regime-dependence test):
    BULL_1  2012-01-01 → 2014-12-31  (BULL 82%)
    BULL_2  2016-07-01 → 2018-01-31  (BULL 82%)
    BULL_3  2023-01-01 → 2024-05-31  (BULL 74%)
    SIDE_1  2015-01-01 → 2016-06-30  (SIDE 40%+)
    SIDE_2  2021-01-01 → 2022-12-31  (SIDE 78%)
    MIX_1   2019-01-01 → 2020-12-31  (COVID mix, BULL 56%/SIDE 44%)
    MIX_2   2024-06-01 → pack end    (post-train mix)

Gate system (``_compute_gates_v2``):

  All gates are RELATIVE to baseline — baseline always auto-passes.

  Hard gates (must ALL pass for promotion):
    G-A  : CV(cand) ≤ CV(base) + 0.05         (relative stability, 5pp tolerance)
    G-B  : mean_CAGR(cand) ≥ mean_CAGR(base) × 0.90  (CAGR floor, 10% tolerance)
    G-C  : worst_fold_CAGR(cand) ≥ worst_fold_CAGR(base) - 0.01  (tail risk, 1pp tolerance)
    G-D  : pos_count(cand) ≥ pos_count(base)   (no fewer positive folds than baseline)

  Soft gates (informational, flag but don't block):
    G-E  : worst_MDD(cand) ≤ worst_MDD(base) × 1.10   (drawdown guard)
    G-F  : mean_Sharpe(cand) ≥ mean_Sharpe(base) × 0.90 (risk-adj floor)
    G-G  : OOS_CAGR_std(cand) ≤ OOS_CAGR_std(base) + 0.01 (OOS consistency)

Usage
-----
    python3 -u phase3/tests/step_d_walk_forward.py
    python3 -u phase3/tests/step_d_walk_forward.py --signals baseline,t1b
    python3 -u phase3/tests/step_d_walk_forward.py --folds F0a,F0b,F4
    python3 -u phase3/tests/step_d_walk_forward.py --fold-set rolling
    python3 -u phase3/tests/step_d_walk_forward.py --fold-set regime
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


# ── Fold set designs ──────────────────────────────────────────────────
FOLDS: List[Dict[str, str]] = [
    {"id": "F0a", "start": "2012-01-01", "end": "2014-12-31", "group": "pre_oos"},
    {"id": "F0b", "start": "2015-01-01", "end": "2016-12-31", "group": "pre_oos"},
    {"id": "F1",  "start": "2019-01-01", "end": "2020-12-31", "group": "in_sample"},
    {"id": "F2",  "start": "2021-01-01", "end": "2022-12-31", "group": "in_sample"},
    {"id": "F3",  "start": "2023-01-01", "end": "2024-05-31", "group": "in_sample"},
    {"id": "F4",  "start": "2024-06-01", "end": PACK_END_STR, "group": "post_oos"},
]

FOLDS_ROLLING: List[Dict[str, str]] = [
    {"id": "R1", "start": "2011-01-01", "end": "2018-12-31", "group": "sliding"},
    {"id": "R2", "start": "2012-01-01", "end": "2019-12-31", "group": "sliding"},
    {"id": "R3", "start": "2013-01-01", "end": "2020-12-31", "group": "sliding"},
    {"id": "R4", "start": "2014-01-01", "end": "2021-12-31", "group": "sliding"},
    {"id": "R5", "start": "2015-01-01", "end": "2022-12-31", "group": "sliding"},
    {"id": "R6", "start": "2016-01-01", "end": "2023-12-31", "group": "sliding"},
    {"id": "R7", "start": "2017-01-01", "end": "2024-12-31", "group": "sliding"},
    {"id": "R8", "start": "2018-01-01", "end": PACK_END_STR,  "group": "sliding"},
]

FOLDS_REGIME: List[Dict[str, str]] = [
    {"id": "BULL_1", "start": "2012-01-01", "end": "2014-12-31", "group": "bull_dom"},
    {"id": "BULL_2", "start": "2016-07-01", "end": "2018-01-31", "group": "bull_dom"},
    {"id": "BULL_3", "start": "2023-01-01", "end": "2024-05-31", "group": "bull_dom"},
    {"id": "SIDE_1", "start": "2015-01-01", "end": "2016-06-30", "group": "side_dom"},
    {"id": "SIDE_2", "start": "2021-01-01", "end": "2022-12-31", "group": "side_dom"},
    {"id": "MIX_1",  "start": "2019-01-01", "end": "2020-12-31", "group": "mixed"},
    {"id": "MIX_2",  "start": "2024-06-01", "end": PACK_END_STR, "group": "mixed"},
]

FOLD_SETS: Dict[str, List[Dict[str, str]]] = {
    "default": FOLDS,
    "rolling": FOLDS_ROLLING,
    "regime":  FOLDS_REGIME,
}

# ── Signal set (4 arms) ──────────────────────────────────────────────
SIGNALS: List[Dict[str, str]] = [
    {"id": "baseline", "arm": "Baseline_V2",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"},
    # ── V2 ENS_L3 original members (component decomposition for B-direction validation) ──
    {"id": "v2m_p2",   "arm": "V2m_P2_BATCH11", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P2_BATCH11_20260406_043415.npz"},
    {"id": "v2m_bull", "arm": "V2m_BULL_GA_V2", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_BULL_GA_V2_20260418_150012.npz"},
    {"id": "v2m_e2e",  "arm": "V2m_E2E",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_E2E_20260413_235043.npz"},
    # ── P2_BATCH11_OOS — original P2_BATCH11 recipe retrained with end_date=2024-05-31
    #    (signal-OOS clean). Train: 2015-01-01 → 2024-05-31. For THIS signal only:
    #      F0a (2012-01-01 → 2014-12-31)  = TRUE pre-OOS (predates train_start)
    #      F0b/F1/F2/F3                    = in-sample
    #      F4  (2024-06-01 → pack_end)    = TRUE post-OOS (after train_end)
    #    Built by phase3/run_p2_batch11_oos.py (2026-05-08, 11.8h GA).
    {"id": "p2_oos",   "arm": "P2_BATCH11_OOS", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P2_BATCH11_OOS_20260508_112505.npz"},
    # ML-1.6 — XGBRanker w/ per-date minmax (NOT rank post-process), bins=200,
    # deeper trees (md=7, n_est=1200, lr=0.04). Built 2026-05-08 17:59.
    {"id": "ml_xgb_v16", "arm": "ML_XGB_v16", "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/frozen_signal_ML_v16_20260508_175944.npz"},
    # ML-2.0 — regime-specific XGBRanker submodels (no regime one-hot, 36 feats).
    # BULL/SIDE/DEF each trained on own-regime rows only. Composite gate ALL PASS.
    {"id": "ml_xgb_v20", "arm": "ML_XGB_v20", "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/frozen_signal_ML_v20_20260510_154835.npz"},
    # ── ML-1.7 Phase B blend signals (2026-05-10) — research-only, signal_type='ml_external_scores'.
    # BlendScore[d,n,r] = α[r] * GA[d,n,r] + (1-α[r]) * ML_v16[d,n,r], per-date min-max normalised.
    # Static profiles: same α for all regimes. RC profiles: α_BULL=1.0 (GA-only), SIDE/DEF blended.
    # Live-blocked via signal_type guard in run_daily.
    {"id": "blend_p2oos_a25",  "arm": "BLEND_p2_oos_static_a25", "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_p2_oos_static_a25_20260510_143612.npz"},
    {"id": "blend_p2oos_a50",  "arm": "BLEND_p2_oos_static_a50", "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_p2_oos_static_a50_20260510_143612.npz"},
    {"id": "blend_p2oos_a75",  "arm": "BLEND_p2_oos_static_a75", "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_p2_oos_static_a75_20260510_143612.npz"},
    {"id": "blend_p2oos_rc1",  "arm": "BLEND_p2_oos_rc_v1",      "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_p2_oos_rc_v1_20260510_143612.npz"},
    {"id": "blend_p2oos_rc2",  "arm": "BLEND_p2_oos_rc_v2",      "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_p2_oos_rc_v2_20260510_143612.npz"},
    {"id": "blend_ensv_a25",   "arm": "BLEND_ens_v_static_a25",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_static_a25_20260510_143612.npz"},
    {"id": "blend_ensv_a50",   "arm": "BLEND_ens_v_static_a50",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_static_a50_20260510_143612.npz"},
    {"id": "blend_ensv_a75",   "arm": "BLEND_ens_v_static_a75",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_static_a75_20260510_143612.npz"},
    {"id": "blend_ensv_rc1",   "arm": "BLEND_ens_v_rc_v1",       "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_rc_v1_20260510_143612.npz"},
    {"id": "blend_ensv_rc2",   "arm": "BLEND_ens_v_rc_v2",       "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_rc_v2_20260510_143612.npz"},
    # ── ML-2.1 blend signals (2026-05-10) — v20 (regime-specific) × ens_v.
    # New rc_v3/v4 profiles blend ML into BULL (BULL IC now positive).
    {"id": "b21_a25",  "arm": "B21_ensv_a25",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_static_a25_20260510_161349.npz"},
    {"id": "b21_a50",  "arm": "B21_ensv_a50",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_static_a50_20260510_161349.npz"},
    {"id": "b21_a75",  "arm": "B21_ensv_a75",  "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_static_a75_20260510_161349.npz"},
    {"id": "b21_rc3",  "arm": "B21_ensv_rc_v3","path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_rc_v3_20260510_161349.npz"},
    {"id": "b21_rc4",  "arm": "B21_ensv_rc_v4","path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_rc_v4_20260510_161349.npz"},
    {"id": "b21_rc1",  "arm": "B21_ensv_rc_v1","path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_rc_v1_20260510_161349.npz"},
    {"id": "b21_rc2",  "arm": "B21_ensv_rc_v2","path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/ml21/blends/frozen_signal_ML_BLEND21_ens_v_rc_v2_20260510_161349.npz"},
    # ── OOS-clean L3 ensembles built 2026-05-08 — train_end ≤ 2024-05-31 for all members
    {"id": "ens_u",  "arm": "P11_OOS_CLEAN_L3_EQ",            "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_EQ_20260508_180824.npz"},
    {"id": "ens_v",  "arm": "P11_OOS_CLEAN_L3_FUNDB_ANCHOR",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_FUNDB_ANCHOR_20260508_180824.npz"},
    {"id": "ens_w",  "arm": "P11_OOS_CLEAN_L3_REGIME_SPEC",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P11_OOS_CLEAN_L3_REGIME_SPEC_20260508_180824.npz"},
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
    # Phase B2 — Batch 3 regime-conditional sweep (Option 3a engine, window 2012-01-03 → 2024-05-31, seed 20260501).
    # See phase3/docs/phase_b2_regime_cond_plan.md.
    {"id": "p5c_mild",      "arm": "P5C_MILD",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_MILD_20260424_132104.npz"},
    {"id": "p5c_balanced",  "arm": "P5C_BALANCED",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_BALANCED_20260424_151512.npz"},
    {"id": "p5c_deep",      "arm": "P5C_DEEP",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_DEEP_20260424_172137.npz"},
    {"id": "p5c_bull_free", "arm": "P5C_BULL_FREE", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_BULL_FREE_20260424_192109.npz"},
    {"id": "p5c_def_heavy", "arm": "P5C_DEF_HEAVY", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_DEF_HEAVY_20260424_212616.npz"},
    {"id": "p5c_side_heavy","arm": "P5C_SIDE_HEAVY","path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5C_SIDE_HEAVY_20260424_232713.npz"},
    # P2 regime-composite ensembles (2026-04-25). Recipes:
    #   ENSEMBLE_A: wb=T1b_INJ  ws=BULL_FREE  wd=DEF_HEAVY   (explicit tail defense)
    #   ENSEMBLE_B: wb=T1b_INJ  ws=BULL_FREE  wd=BULL_FREE   (stability-consistent)
    # Built by phase3/tests/p2_ensemble_composer.py.
    {"id": "p6_ens_a", "arm": "P6_ENSEMBLE_A", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_A_20260425_011612.npz"},
    {"id": "p6_ens_b", "arm": "P6_ENSEMBLE_B", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_B_20260425_011612.npz"},
    {"id": "p6_ens_c", "arm": "P6_ENSEMBLE_C", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_C_20260425_014939.npz"},
    # Phase B2.1 — Batch 4 SIDE specialist (seed 20260502, 2026-04-26).
    # See phase3/docs/phase_b2_1_side_specialist_plan.md.
    {"id": "p5d_side_pure", "arm": "P5D_SIDE_PURE", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5D_SIDE_PURE_20260426_003825.npz"},
    {"id": "p5d_side_deep", "arm": "P5D_SIDE_DEEP", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5D_SIDE_DEEP_20260426_024004.npz"},
    {"id": "p5d_side_win",  "arm": "P5D_SIDE_WIN",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5D_SIDE_WIN_20260426_042332.npz"},
    # P2 Preset D — SIDE-specialist ensemble (2026-04-26).
    #   wb=Baseline_V2, ws=P5D_SIDE_DEEP, wd=Baseline_V2.
    {"id": "p6_ens_d", "arm": "P6_ENSEMBLE_D", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_D_20260426_084731.npz"},
    # Phase B2.2 — Batch 5 SIDE specialist v2 (anti-collapse + BULL carry-over, 2026-04-26).
    {"id": "p5e_side_tech",  "arm": "P5E_SIDE_TECH",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5E_SIDE_TECH_20260426_124200.npz"},
    {"id": "p5e_side_fundb", "arm": "P5E_SIDE_FUND_BREAKOUT","path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5E_SIDE_FUND_BREAKOUT_20260426_152005.npz"},
    # P2 Preset E — weighted-avg SIDE ensemble (B4×3 + B5×2, 2026-04-26).
    #   wb=Baseline_V2, ws=weighted_avg(PURE,DEEP,WIN,TECH,FUND_BRK), wd=Baseline_V2.
    {"id": "p6_ens_e", "arm": "P6_ENSEMBLE_E", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_ENSEMBLE_E_20260426_160211.npz"},
    # ENSEMBLE_F variants — ws = weighted-avg of multiple healthy (ws>=4) GA signals.
    #   Pool filtered by F2-fold (SIDE 72%) CAGR. wb/wd = Baseline_V2.
    {"id": "f_top5",     "arm": "F_TOP5_EQUAL",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_TOP5_EQUAL_20260426_180503.npz"},
    {"id": "f_abl_eq",   "arm": "F_ABOVE_BL_EQUAL", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_ABOVE_BL_EQUAL_20260426_180503.npz"},
    {"id": "f_abl_f2",   "arm": "F_ABOVE_BL_F2WT",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_ABOVE_BL_F2WT_20260426_180503.npz"},
    {"id": "f_top10",    "arm": "F_TOP10_EQUAL",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_TOP10_EQUAL_20260426_180503.npz"},
    {"id": "f_all19_eq", "arm": "F_ALL19_EQUAL",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_ALL19_EQUAL_20260426_180503.npz"},
    {"id": "f_all19_f2", "arm": "F_ALL19_F2WT",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_F_ALL19_F2WT_20260426_180503.npz"},
    # ── Batch 6 — post-backfill retrain (expanded financials, 2026-04-28) ──
    {"id": "b6_bl_10",   "arm": "P6_BASELINE_10Y",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_BASELINE_10Y_20260428_004850.npz"},
    {"id": "b6_bl_15",   "arm": "P6_BASELINE_15Y",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_BASELINE_15Y_20260428_032446.npz"},
    {"id": "b6_st_10",   "arm": "P6_SIDE_TECH_10Y",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_SIDE_TECH_10Y_20260428_055551.npz"},
    {"id": "b6_st_15",   "arm": "P6_SIDE_TECH_15Y",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_SIDE_TECH_15Y_20260428_093425.npz"},
    {"id": "b6_sf_10",   "arm": "P6_SIDE_FUND_10Y",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_SIDE_FUND_10Y_20260428_114926.npz"},
    {"id": "b6_sf_15",   "arm": "P6_SIDE_FUND_15Y",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P6_SIDE_FUND_15Y_20260428_151355.npz"},
    {"id": "b6_ens_10",  "arm": "B6_ENS_10Y",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_B6_ENS_10Y_20260428_191451.npz"},
    {"id": "b6_ens_15",  "arm": "B6_ENS_15Y",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_B6_ENS_15Y_20260428_191451.npz"},
    # ── Phase 1 Quick Win — V2 wb/wd + B6 ws regime-stitched (2026-04-28) ──
    {"id": "p7_ens_f",   "arm": "P7_ENSEMBLE_F",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_ENSEMBLE_F_20260428_195034.npz"},
    {"id": "p7_ens_g",   "arm": "P7_ENSEMBLE_G",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_ENSEMBLE_G_20260428_195034.npz"},
    # ── Batch 7 — V2-recipe GA retrain on expanded data (2026-04-28 → 2026-05-03) ──
    {"id": "b7_full",    "arm": "P7_V2_FULL",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_FULL_20260429_190918.npz"},
    {"id": "b7_nodep",   "arm": "P7_V2_NO_DEPLOY",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_NO_DEPLOY_20260501_014909.npz"},
    {"id": "b7_mega",    "arm": "P7_V2_MEGA",          "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_MEGA_20260502_165121.npz"},
    {"id": "b7_bull",    "arm": "P7_V2_BULL_AGG",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_BULL_AGG_20260503_162233.npz"},
    # ── Phase 3 L3 ensembles from Batch 7 candidates (2026-05-03) ──
    {"id": "p7_l3_h",    "arm": "P7_L3_ENSEMBLE_H",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_L3_ENSEMBLE_H_20260503_163436.npz"},
    {"id": "p7_l3_i",    "arm": "P7_L3_HYBRID_I",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_L3_HYBRID_I_20260503_163436.npz"},
    # ── Phase 3 regime-stitch combos (J-N): B7 BULL + V2/B6 SIDE (2026-05-03) ──
    {"id": "p7_j",       "arm": "P7_STITCH_J",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_J_20260503_164726.npz"},
    {"id": "p7_k",       "arm": "P7_STITCH_K",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_K_20260503_164726.npz"},
    {"id": "p7_l",       "arm": "P7_BLEND_L",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_BLEND_L_20260503_164726.npz"},
    {"id": "p7_m",       "arm": "P7_TRIPLE_M",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_TRIPLE_M_20260503_164726.npz"},
    {"id": "p7_n",       "arm": "P7_STITCH_N",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_STITCH_N_20260503_164726.npz"},
    # ── Batch 8 — specialist GA signals (SIDE v3 / BULL dense / balanced, 2026-05-04) ──
    {"id": "b8_side",   "arm": "P8_SIDE_V3",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_SIDE_V3_20260504_174158.npz"},
    {"id": "b8_bull",  "arm": "P8_BULL_DENSE",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BULL_DENSE_20260506_020833.npz"},
    # P8_BALANCED: path will be updated when GA completes.
    # {"id": "b8_bal",    "arm": "P8_BALANCED",         "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BALANCED_PLACEHOLDER.npz"},
    # ── P9 ensembles — post-Batch 8 stitch combos (2026-05-06) ──
    {"id": "p9_spec_a", "arm": "P9_TRIPLE_SPEC_A",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P9_TRIPLE_SPEC_A_20260506_030809.npz"},
    {"id": "p9_bal_b",  "arm": "P9_BAL_SIDE_B",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P9_BAL_SIDE_B_20260507_032749.npz"},
    {"id": "b8_bal",    "arm": "P8_BALANCED",        "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BALANCED_20260507_022105.npz"},
    # ── P9 L3 blending — Direction A, equal-weight blend of Batch 8 (2026-05-07) ──
    {"id": "p9_l3_q",   "arm": "P9_L3_EQUAL_Q",      "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P9_L3_EQUAL_Q_20260507_035153.npz"},
    # ── P10 Cross-Era L3 — V2 original members + Batch 8 blends (2026-05-07) ──
    {"id": "p10_eq",    "arm": "P10_CROSS_ERA_EQ",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P10_CROSS_ERA_EQ_20260507_190326.npz"},
    {"id": "p10_v2h",   "arm": "P10_CROSS_ERA_V2H",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P10_CROSS_ERA_V2H_20260507_190326.npz"},
    {"id": "p10_full",  "arm": "P10_CROSS_ERA_FULL", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P10_CROSS_ERA_FULL_20260507_190326.npz"},
    # ── Phase ML-1 — XGBoost walk-forward (research-only; live-blocked, 2026-05-07) ──
    # signal_type='ml_external_scores' inside the npz; live run_daily refuses it.
    {"id": "ml_xgb_v1",  "arm": "ML_XGB_v1",          "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/frozen_signal_ML_v1_20260507_221335.npz"},
    # ── Phase ML-1.5 — XGBRanker (rank:pairwise) + regime one-hot + cs-rank post-process (2026-05-07) ──
    {"id": "ml_xgb_v15", "arm": "ML_XGB_v15",         "path": "/Users/shin-il/PyCharmMiscProject/0316-/phase3/ml/artifacts/frozen_signal_ML_v15_20260507_224123.npz"},
    # ── OOS-clean retrains of P7/P8 originals (train_end=2024-05-31, 2026-05-09) ──
    # Same GA recipe as their lookahead siblings, but with a 2024-05-31 cutoff so
    # F4 (2024-06 → 2026-02) is a true post-OOS holdout for these signals.
    {"id": "p7_nodep_oos", "arm": "P7_V2_NO_DEPLOY_OOS", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P7_V2_NO_DEPLOY_OOS_20260509_152907.npz"},
    {"id": "p8_bull_oos",  "arm": "P8_BULL_DENSE_OOS",   "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P8_BULL_DENSE_OOS_20260509_164158.npz"},
    # ── P12 OOS-clean L3 ensembles (2026-05-09): all members train_end ≤ 2024-05-31 ──
    # X — V (FUNDB anchor) + P8_BULL_DENSE_OOS injected to wb (BULL-specialist boost).
    # Y — Triple-spec stitch: P8_BULL_DENSE_OOS / P5E_FUND_BRK / P2_BATCH11_OOS
    #     (re-compose of P9_TRIPLE_SPEC_A with OOS-clean leaves only).
    # Z — 5-way equal blend (P2_OOS + P5E_FUND_BRK + P5D_SIDE_DEEP + P7_OOS + P8_OOS).
    {"id": "p12_x", "arm": "P12_BULL_INJ_FUNDB_ANCHOR", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_BULL_INJ_FUNDB_ANCHOR_20260509_165605.npz"},
    {"id": "p12_y", "arm": "P12_TRIPLE_SPEC_OOS",       "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_TRIPLE_SPEC_OOS_20260509_165605.npz"},
    {"id": "p12_z", "arm": "P12_FULL_OOS_L3",           "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P12_FULL_OOS_L3_20260509_165605.npz"},
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


def _aggregate(folds_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute mean/std/CV CAGR and group summaries.

    Dynamically collects groups from the actual fold data, so it works
    for the default fold set (pre_oos/in_sample/post_oos), the rolling
    sliding set (sliding), and the regime set (bull_dom/side_dom/mixed).
    """
    by_group: Dict[str, List[float]] = {}
    all_cagr: List[float] = []
    for r in folds_results:
        c = float(r["metrics"]["CAGR"])
        all_cagr.append(c)
        g = r.get("group", "unknown")
        by_group.setdefault(g, []).append(c)

    out: Dict[str, Any] = {"all": _stats(all_cagr)}
    for group_name, cagrs in by_group.items():
        out[group_name] = _stats(cagrs)
    return out


def _compute_gates(cand_agg: Dict[str, Any], base_agg: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy G6-A/B/C/D — kept for backward compatibility."""
    return _compute_gates_v2(cand_agg, base_agg, None, None)


def _compute_gates_v2(
    cand_agg: Dict[str, Any],
    base_agg: Dict[str, Any],
    cand_folds: Optional[List[Dict[str, Any]]],
    base_folds: Optional[List[Dict[str, Any]]],
    cand_surge: Optional[Dict[str, Any]] = None,
    base_surge: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Relative gate system v2: G-A through G-H.

    All gates are RELATIVE to baseline — baseline always auto-passes.
    Hard gates (A-D): must all pass for production promotion.
    Soft gates (E-H): informational flags, no blocking.

    Optional surge metrics (G-H):
      cand_surge / base_surge: {"lift_10d": float, "q5q1_10d": float}
      from surge_score_analysis.py.
    """
    c_all = cand_agg["all"]
    b_all = base_agg["all"]
    cv_cand = c_all["cv"]
    cv_base = b_all["cv"]

    # ── Hard gates (all relative to baseline) ──
    # G-A: CV(cand) ≤ CV(baseline) + 0.05
    g_a = bool(np.isfinite(cv_cand) and np.isfinite(cv_base)
               and cv_cand <= cv_base + 0.05 + 1e-9)

    # G-B: mean_CAGR(cand) ≥ mean_CAGR(baseline) × 0.90
    g_b = bool(np.isfinite(c_all["mean"]) and np.isfinite(b_all["mean"])
               and c_all["mean"] >= b_all["mean"] * 0.90 - 1e-9)

    # G-C: worst_fold_CAGR(cand) ≥ worst_fold_CAGR(baseline) - 0.01
    g_c = bool(np.isfinite(c_all["min"]) and np.isfinite(b_all["min"])
               and c_all["min"] >= b_all["min"] - 0.01 - 1e-9)

    # G-D: pos_count(cand) ≥ pos_count(baseline)
    g_d = bool(c_all["pos_count"] >= b_all["pos_count"] and c_all["n"] > 0)

    gates: Dict[str, Any] = {
        "G_A_cv_le_base": {
            "cv_cand": cv_cand, "cv_base": cv_base, "tolerance": 0.05,
            "pass": g_a, "hard": True,
        },
        "G_B_cagr_ge_90pct": {
            "cagr_cand": c_all["mean"], "cagr_base": b_all["mean"],
            "ratio": float(c_all["mean"] / b_all["mean"]) if abs(b_all["mean"]) > 1e-9 else float("nan"),
            "pass": g_b, "hard": True,
        },
        "G_C_worst_ge_base": {
            "worst_cand": c_all["min"], "worst_base": b_all["min"], "tolerance": 0.01,
            "pass": g_c, "hard": True,
        },
        "G_D_pos_ge_base": {
            "pos_cand": c_all["pos_count"], "pos_base": b_all["pos_count"], "n": c_all["n"],
            "pass": g_d, "hard": True,
        },
    }

    # ── Soft gates (need per-fold metrics) ──
    if cand_folds and base_folds:
        def _fold_metric(folds_list, key):
            vals = []
            for f in folds_list:
                v = f.get("metrics", {}).get(key)
                if v is not None and np.isfinite(v):
                    vals.append(float(v))
            return vals

        cand_mdds = _fold_metric(cand_folds, "Max_Drawdown")
        base_mdds = _fold_metric(base_folds, "Max_Drawdown")
        cand_sharpes = _fold_metric(cand_folds, "Net_Sharpe")
        base_sharpes = _fold_metric(base_folds, "Net_Sharpe")

        # G-E: worst_MDD(cand) ≤ worst_MDD(baseline) × 1.10
        worst_mdd_cand = max(cand_mdds) if cand_mdds else float("nan")
        worst_mdd_base = max(base_mdds) if base_mdds else float("nan")
        g_e = bool(np.isfinite(worst_mdd_cand) and np.isfinite(worst_mdd_base)
                   and worst_mdd_cand <= worst_mdd_base * 1.10 + 1e-9)

        # G-F: mean_Sharpe(cand) ≥ mean_Sharpe(baseline) × 0.90
        mean_sharpe_cand = float(np.mean(cand_sharpes)) if cand_sharpes else float("nan")
        mean_sharpe_base = float(np.mean(base_sharpes)) if base_sharpes else float("nan")
        g_f = bool(np.isfinite(mean_sharpe_cand) and np.isfinite(mean_sharpe_base)
                   and mean_sharpe_cand >= mean_sharpe_base * 0.90 - 1e-9)

        # G-G: OOS_CAGR_std(cand) ≤ OOS_CAGR_std(baseline) + 0.01
        oos_cagrs_cand = [float(f["metrics"]["CAGR"]) for f in cand_folds
                          if f.get("group") in ("pre_oos", "post_oos", "oos")
                          and np.isfinite(f["metrics"]["CAGR"])]
        oos_cagrs_base = [float(f["metrics"]["CAGR"]) for f in base_folds
                          if f.get("group") in ("pre_oos", "post_oos", "oos")
                          and np.isfinite(f["metrics"]["CAGR"])]
        oos_std_cand = float(np.std(oos_cagrs_cand)) if len(oos_cagrs_cand) >= 2 else float("nan")
        oos_std_base = float(np.std(oos_cagrs_base)) if len(oos_cagrs_base) >= 2 else float("nan")
        g_g = bool(np.isfinite(oos_std_cand) and np.isfinite(oos_std_base)
                   and oos_std_cand <= oos_std_base + 0.01 + 1e-9)

        gates["G_E_mdd_le_110pct"] = {
            "mdd_cand": worst_mdd_cand, "mdd_base": worst_mdd_base,
            "pass": g_e, "hard": False,
        }
        gates["G_F_sharpe_ge_90pct"] = {
            "sharpe_cand": mean_sharpe_cand, "sharpe_base": mean_sharpe_base,
            "pass": g_f, "hard": False,
        }
        gates["G_G_oos_std_le_base"] = {
            "oos_std_cand": oos_std_cand, "oos_std_base": oos_std_base, "tolerance": 0.01,
            "pass": g_g, "hard": False,
        }

    # G-H: Surge capture — Lift(cand, 10d) ≥ Lift(baseline, 10d) × 0.80
    # Ensures the candidate doesn't sacrifice surge-capture ability.
    if cand_surge and base_surge:
        lift_cand = float(cand_surge.get("lift_10d", 0))
        lift_base = float(base_surge.get("lift_10d", 0))
        q5q1_cand = float(cand_surge.get("q5q1_10d", 0))
        q5q1_base = float(base_surge.get("q5q1_10d", 0))
        g_h = bool(np.isfinite(lift_cand) and np.isfinite(lift_base)
                   and lift_base > 0
                   and lift_cand >= lift_base * 0.80 - 1e-9)
        gates["G_H_surge_lift_ge_80pct"] = {
            "lift_cand": round(lift_cand, 3),
            "lift_base": round(lift_base, 3),
            "ratio": round(lift_cand / lift_base, 3) if lift_base > 0 else None,
            "q5q1_cand": round(q5q1_cand, 3),
            "q5q1_base": round(q5q1_base, 3),
            "pass": g_h, "hard": False,
        }

    gates["all_hard_pass"] = all(v["pass"] for v in gates.values()
                                 if isinstance(v, dict) and v.get("hard"))
    return gates


def _run_signal_over_folds(
    sig_cfg: Dict[str, str],
    folds: List[Dict[str, str]],
    cfg, pack,
    vix_c, vix_r, vix_s,
    trigger_conf,
    buy_grace_days: int = 0,
    vanilla: bool = False,
    blend_conf: Optional[Dict[str, Any]] = None,
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
            # Vanilla mode: bypass V2.1 SIDE_DEF_p12 profit_target trigger
            # by setting exit_triggers=None, which triggers build_triggers'
            # legacy fallback (enable_stop_loss + sell_grace_days only).
            # This neutralizes V1.5/V2-era exit-trigger tuning so that
            # signal-quality comparisons are not biased toward incumbent V2.
            strategy_patch = {"exit_triggers": None} if vanilla else None
            sim = _run_sim(
                arm_name=f"{sig_cfg['arm']}__{fold['id']}",
                signal_path=sig_cfg["path"],
                cfg=cfg, pack=pack,
                vix_close_map=vix_c, vix_regime_map=vix_r, vix_smooth_map=vix_s,
                trigger_conf=trigger_conf,
                oos_start=start, oos_end=end,
                buy_grace_days=int(buy_grace_days),
                strategy_patch=strategy_patch,
                blend_conf=blend_conf,
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
    fold_set_name = report["meta"].get("fold_set", "default")
    lines.append(f"# T5 Walk-Forward Results — `{fold_set_name}` fold-set")
    lines.append(f"")
    lines.append(f"**Generated**: {report['meta']['generated_at']}")
    lines.append(f"**Pack**: `{report['meta']['pack_basename']}`")
    lines.append(f"**Fold-set**: `{fold_set_name}`  |  **Folds**: {len(report['meta']['folds'])}  |  "
                 f"**Signals**: {len(report['meta']['signals'])}")
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

    # Aggregate by group — dynamic groups
    all_groups = []
    for sig in report["per_signal"]:
        for g_name in sig["aggregate"]:
            if g_name != "all" and g_name not in all_groups:
                all_groups.append(g_name)

    lines.append("## 2. CAGR aggregate by fold group (%)")
    lines.append("")
    grp_header = "| Signal | All (mean / std / CV) | " + " | ".join(
        f"{g} (mean/std/CV)" for g in all_groups
    ) + " | Worst | Pos/n |"
    grp_sep = "|" + "---|" * (3 + len(all_groups)) + "---|"
    lines.append(grp_header)
    lines.append(grp_sep)
    for sig in report["per_signal"]:
        a = sig["aggregate"]
        def _fmt(s: Dict[str, Any]) -> str:
            if s.get("n", 0) == 0:
                return "—"
            return f"{s['mean']*100:+.2f} / {s['std']*100:.2f} / {s['cv']:.2f}"
        worst = f"{a['all']['min']*100:+.2f}" if np.isfinite(a["all"]["min"]) else "n/a"
        posn = f"{a['all']['pos_count']}/{a['all']['n']}"
        parts = [f"| **{sig['arm']}**", _fmt(a["all"])]
        for g_name in all_groups:
            parts.append(_fmt(a.get(g_name, {"n": 0})))
        parts.extend([worst, posn])
        lines.append(" | ".join(parts) + " |")
    lines.append("")

    baseline_arm = report["meta"].get("baseline_arm", "Baseline_V2")
    lines.append(f"## 3. Gate verdicts (vs baseline = {baseline_arm})")
    lines.append("")
    lines.append("| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | "
                 "G-D<br/>pos≥base | G-E<br/>MDD≤110% | "
                 "G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |")
    lines.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for sig in report["per_signal"]:
        g = sig.get("gates")
        if g is None:
            continue
        def _p(k):
            if k not in g:
                return "—"
            return "✓" if g[k]["pass"] else "✗"
        hard = g.get("all_hard_pass")
        hard_str = "**✓ ALL**" if hard else "**✗ FAIL**" if hard is not None else "—"
        lines.append(
            f"| **{sig['arm']}** | {_p('G_A_cv_le_base')} "
            f"| {_p('G_B_cagr_ge_90pct')} "
            f"| {_p('G_C_worst_ge_base')} "
            f"| {_p('G_D_pos_ge_base')} "
            f"| {_p('G_E_mdd_le_110pct')} "
            f"| {_p('G_F_sharpe_ge_90pct')} "
            f"| {_p('G_G_oos_std_le_base')} "
            f"| {_p('G_H_surge_lift_ge_80pct')} "
            f"| {hard_str} |"
        )
    lines.append("")

    # Gate definitions
    lines.append("### Gate definitions")
    lines.append("")
    lines.append("All gates are **relative to baseline** — baseline always auto-passes.")
    lines.append("")
    lines.append("| Gate | Type | Rule |")
    lines.append("|---|---|---|")
    lines.append("| G-A | Hard | CV(cand) ≤ CV(baseline) + 0.05 (relative stability, 5pp tolerance) |")
    lines.append("| G-B | Hard | mean_CAGR(cand) ≥ mean_CAGR(baseline) × 0.90 (CAGR floor, 10% tolerance) |")
    lines.append("| G-C | Hard | worst_fold_CAGR(cand) ≥ worst_fold_CAGR(baseline) − 0.01 (tail risk, 1pp tolerance) |")
    lines.append("| G-D | Hard | pos_count(cand) ≥ pos_count(baseline) (no fewer positive folds) |")
    lines.append("| G-E | Soft | worst_MDD(cand) ≤ worst_MDD(baseline) × 1.10 (drawdown guard) |")
    lines.append("| G-F | Soft | mean_Sharpe(cand) ≥ mean_Sharpe(baseline) × 0.90 (risk-adj floor) |")
    lines.append("| G-G | Soft | OOS_CAGR_std(cand) ≤ OOS_CAGR_std(baseline) + 0.01 (OOS consistency) |")
    lines.append("| G-H | Soft | Lift_10d(cand) ≥ Lift_10d(baseline) × 0.80 (surge capture, top-decile fwd+20% 10d) |")
    lines.append("")

    # Per-fold full metrics
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
    if fold_set_name == "default":
        lines.append("- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31")
        lines.append("  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)")
        lines.append("- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증")
        lines.append("- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)")
        lines.append("- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증")
        lines.append("- **진정한 OOS 검증**: Step C + live production만 가능")
        lines.append("- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)")
    elif fold_set_name == "rolling":
        lines.append("- **Sliding 8-year windows** (1-year step) test whether performance is stable")
        lines.append("  as the evaluation window shifts through the in-sample period.")
        lines.append("- Each window spans ~2000 trading days → statistically robust per-fold estimates.")
        lines.append("- Overlapping windows provide a smooth performance trend over time:")
        lines.append("  consistent CAGR across all 8 windows → temporally robust signal.")
        lines.append("  CAGR drops in specific windows → period-specific weakness identifiable.")
        lines.append("- **IN-SAMPLE temporal stability audit**, NOT OOS validation.")
        lines.append("  All folds fall within GA training range (2011 → 2026-03).")
        lines.append("  True OOS validation requires Phase B P9_OOS_VALIDATION.")
    elif fold_set_name == "regime":
        lines.append("- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.")
        lines.append("- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.")
        lines.append("- **Mixed** folds capture transition periods and post-train conditions.")
        lines.append("- A production-worthy signal should show positive CAGR in all regime groups.")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="T5 Walk-Forward — multi-fold evaluation")
    parser.add_argument(
        "--signals", default="all",
        help="Comma-separated signal ids or 'all' (default).",
    )
    parser.add_argument(
        "--folds", default="all",
        help="Comma-separated fold ids or 'all' (default).",
    )
    parser.add_argument(
        "--fold-set", default="default",
        choices=tuple(FOLD_SETS.keys()),
        help="Pre-defined fold layout: default (6-fold), rolling (8×8yr sliding), "
             "regime (BULL/SIDE/MIX stratified).",
    )
    parser.add_argument(
        "--buy-grace-days", type=int, default=0,
        help="Idea 2 strict variant. 0 = legacy byte-identical (default). "
             "3 = production-recommended after p3_buy_grace_sweep findings.",
    )
    parser.add_argument(
        "--vanilla", action="store_true",
        help="Vanilla mode: disable V2-era exit-trigger tuning (SIDE_DEF_p12 "
             "profit_target). Signals are evaluated under the pre-v2.1 legacy "
             "stop_loss + sell_grace_days fallback. Use to diagnose whether "
             "V2's lead is due to genuine signal quality vs. portfolio-strategy "
             "home-court advantage.",
    )
    parser.add_argument(
        "--regime-blend", action="store_true",
        help="Enable regime hysteresis + soft alpha blending (regime_blend.py). "
             "Suppresses BULL/SIDE/DEF chattering near VIX thresholds and "
             "interpolates score weights smoothly across transition zones. "
             "Off by default (matches V2 baseline measurement protocol).",
    )
    parser.add_argument(
        "--baseline", default="baseline",
        help="Signal id to use as the hardgate baseline. Default 'baseline' "
             "(= V2 inflated). Use 'p2_oos' to compare against the OOS-clean "
             "P2_BATCH11_OOS reference, neutralising V2's lookahead inflation.",
    )
    args = parser.parse_args()

    fold_set_name = args.fold_set
    base_folds = FOLD_SETS[fold_set_name]

    print("=" * 72)
    print(f"  Step D — T5 Walk-Forward  [fold-set: {fold_set_name}]")
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
        folds = list(base_folds)
    else:
        wanted = {s.strip() for s in args.folds.split(",")}
        folds = [f for f in base_folds if f["id"] in wanted]
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
    if args.vanilla:
        print("[VANILLA MODE] exit_triggers=None — using legacy "
              "stop_loss/sell_grace_days fallback (V2.1 profit_target disabled).")
    blend_conf_to_use: Optional[Dict[str, Any]] = None
    if args.regime_blend:
        # Pull blend widths from config.yaml's `regime` block; fall back to
        # regime_blend.py defaults if missing.
        rg_cfg = conf.get("regime", {}) or {}
        blend_conf_to_use = {
            "regime_blend_enabled": True,
            "bull_side_blend_width": float(rg_cfg.get("bull_side_blend_width", 2.0)),
            "side_def_blend_width":  float(rg_cfg.get("side_def_blend_width",  3.0)),
        }
        print(f"[REGIME BLEND ON] hysteresis + soft alpha blending enabled "
              f"(bull_side_w={blend_conf_to_use['bull_side_blend_width']}, "
              f"side_def_w={blend_conf_to_use['side_def_blend_width']}).")
    for sig_cfg in signals:
        result = _run_signal_over_folds(
            sig_cfg, folds,
            cfg=cfg, pack=pack,
            vix_c=vix_c, vix_r=vix_r, vix_s=vix_s,
            trigger_conf=trigger_conf,
            buy_grace_days=int(args.buy_grace_days),
            vanilla=bool(args.vanilla),
            blend_conf=blend_conf_to_use,
        )
        per_signal.append(result)
    total_elapsed = time.time() - t0

    # ── Compute gates (vs baseline) ────────────────────────────────
    baseline_id = args.baseline
    baseline = next((s for s in per_signal if s["signal_id"] == baseline_id), None)
    if baseline is None and baseline_id != "baseline":
        print(f"[warn] --baseline='{baseline_id}' not in per_signal results — "
              f"falling back to default 'baseline'.")
        baseline = next((s for s in per_signal if s["signal_id"] == "baseline"), None)
    if baseline is not None:
        print(f"[gates] hardgate baseline: {baseline['arm']} (id={baseline['signal_id']})")
        for sig in per_signal:
            sig["gates"] = _compute_gates_v2(
                sig["aggregate"], baseline["aggregate"],
                sig["folds"], baseline["folds"],
            )

    # ── Console summary ───────────────────────────────────────────
    print()
    print("=" * 100)
    print(f"  SUMMARY  [fold-set: {fold_set_name}]")
    print("=" * 100)
    print(f"{'Signal':<22s} {'n':>3s} {'mean':>8s} {'std':>7s} {'CV':>6s} "
          f"{'worst':>8s} {'pos/n':>6s}  A   B   C   D  | E   F   G  |HARD")
    print("-" * 100)
    for sig in per_signal:
        a = sig["aggregate"]["all"]
        g = sig.get("gates") or {}
        def _g(k):
            if k not in g:
                return " — "
            return " ✓ " if g[k]["pass"] else " ✗ "
        hard_ok = g.get("all_hard_pass")
        hard_str = " ALL" if hard_ok else " FAIL" if hard_ok is not None else "  — "
        print(f"{sig['arm']:<22s} {a['n']:>3d} "
              f"{a['mean']*100:>+7.1f}% "
              f"{a['std']*100:>+6.1f}% "
              f"{a['cv']:>6.3f} "
              f"{a['min']*100:>+7.1f}% "
              f"{a['pos_count']:>3d}/{a['n']:<2d}"
              f"{_g('G_A_cv_le_base')}{_g('G_B_cagr_ge_90pct')}"
              f"{_g('G_C_worst_ge_base')}{_g('G_D_pos_ge_base')}"
              f"|{_g('G_E_mdd_le_110pct')}{_g('G_F_sharpe_ge_90pct')}"
              f"{_g('G_G_oos_std_le_base')}{_g('G_H_surge_lift_ge_80pct')}|{hard_str}")

    # ── Regime-group breakdown (if applicable) ────────────────────
    known_regime_groups = {"bull_dom", "side_dom", "mixed"}
    has_regime_groups = any(
        f.get("group") in known_regime_groups for sig in per_signal for f in sig["folds"]
    )
    if has_regime_groups:
        print()
        print("-" * 100)
        print("  REGIME-GROUP BREAKDOWN (mean CAGR %)")
        print("-" * 100)
        print(f"{'Signal':<22s} {'BULL_dom':>10s} {'SIDE_dom':>10s} {'Mixed':>10s}")
        print("-" * 56)
        for sig in per_signal:
            a = sig["aggregate"]
            def _grp(k):
                s = a.get(k, {})
                if s.get("n", 0) == 0:
                    return "     —    "
                return f"{s['mean']*100:>+9.1f}%"
            print(f"{sig['arm']:<22s} {_grp('bull_dom')} {_grp('side_dom')} {_grp('mixed')}")

    print("=" * 100)
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print("=" * 100)

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
            "fold_set": fold_set_name,
            "baseline_id": baseline_id,
            "baseline_arm": baseline["arm"] if baseline is not None else "Baseline_V2",
            "total_elapsed_sec": round(total_elapsed, 1),
            "signals": [{"id": s["id"], "arm": s["arm"], "path": s["path"]} for s in signals],
            "folds": [{"id": f["id"], "start": f["start"], "end": f["end"], "group": f["group"]} for f in folds],
            "protocol": {
                "initial_capital": 100000.0,
                "daily_buy_limit": 1000.0,
                "commission_bps": 10.0, "slippage_bps": 5.0,
                "rebalance_mode": "daily",
                "strategy_stack": "LEGACY_FALLBACK" if args.vanilla else "SIDE_DEF_p12",
                "vanilla_mode": bool(args.vanilla),
                "buy_grace_days": int(args.buy_grace_days),
                "regime_blend": bool(args.regime_blend),
            },
        },
        "per_signal": per_signal,
    }
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=float)
    print(f"[saved] {json_path}")

    _write_markdown(report, md_path)
    print(f"[saved] {md_path}")

    # Exit code: 0 if any non-baseline candidate passes all hard gates, 2 otherwise
    any_pass = False
    for sig in per_signal:
        if sig["signal_id"] == "baseline":
            continue
        g = sig.get("gates") or {}
        if g.get("all_hard_pass"):
            any_pass = True
            break
    return 0 if any_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
