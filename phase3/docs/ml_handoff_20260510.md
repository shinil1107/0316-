# ML Track — Evaluation History & Future Plan (Codex Handoff)

**Date**: 2026-05-10
**Status**: ML-1.6 training complete, evaluation done; ML-2.0+ pending
**Live impact**: NONE — all ML artefacts are research-only (`signal_type='ml_external_scores'`, live `run_daily` blocks them with `RuntimeError`)

---

## 1. Executive Summary

| Version | Date | CAGR (mean, default 6-fold) | CV | Hardgate (vs P2_OOS) | Key Change |
|---|---|---|---|---|---|
| ML-1.0 | 2026-05-07 | +15.6% | 0.800 | 0/3 fold-sets | baseline XGBRegressor, `reg:squarederror` |
| ML-1.5 | 2026-05-07 | +17.5% | 0.426 | 0/3 fold-sets | XGBRanker `rank:pairwise` + regime one-hot + cs-rank post-process |
| ML-1.6 | 2026-05-08 | +18.0% | 0.398 | 0/3 fold-sets | raw score (no rank post-proc), bins=200, deeper trees, `ndcg_exp_gain=False` |
| **GA baseline (P2_OOS)** | — | **+21.5%** | **0.429** | baseline | OOS-clean GA Linear Score |
| **GA best (P11_FUNDB_ANCHOR)** | — | **+26.9%** | **0.473** | 2/3 pass | L3 ensemble, current shadow signal |

**Gap**: ML-1.6 trails P2_OOS by **-3.5pp** on mean CAGR and trails the best GA signal by **-8.9pp**. ML alone cannot replace the GA signal — ensemble or architectural improvements needed.

---

## 2. Detailed Version History

### 2.1 ML-1.0 — XGBoost Baseline (2026-05-07)

**Script**: `phase3/ml/run_ml_v1.py`
**Artefact**: `ml/artifacts/frozen_signal_ML_v1_20260507_221335.npz`
**Architecture**:
- XGBRegressor, `objective="reg:squarederror"`
- Target: cross-sectional rank of `fwd1` (1-month forward return), unit-uniform [0,1]
- 36 technical/fundamental features from precompute pack
- Walk-forward: 6 folds (F0a–F4), 21-day embargo, `min_train_dis=250`
- Hyperparams: `max_depth=4, n_estimators=600, lr=0.05`

**Results**:

| Fold | CAGR | Notes |
|---|---|---|
| F0a | +0.0% | Skipped — insufficient train data (231 < 250 min_train_dis) |
| F0b | +11.3% | Near-parity with V2 (+12.1%) |
| F1 | +27.4% | COVID period |
| F2 | +1.1% | 2022 bear, severe underperformance |
| F3 | +32.6% | best_iter=0 on some folds |
| F4 | +20.9% | Post-OOS |

**Diagnosis** (documented in `docs/ml_v1_v2_breakthrough_assessment.md`):
1. **Squared-error loss → mean reversion**: predictions collapsed to [0.484, 0.583] — nearly constant
2. **No regime feature**: model treats BULL/SIDE/DEFENSIVE identically
3. **F0a data starvation**: min_train_dis too restrictive

---

### 2.2 ML-1.5 — XGBRanker + Regime (2026-05-07)

**Script**: `phase3/ml/run_ml_v15.py`
**Artefact**: `ml/artifacts/frozen_signal_ML_v15_20260507_224123.npz`
**Changes from v1**:
1. `rank:pairwise` loss (XGBRanker) — directly optimises within-day ranking
2. Regime one-hot feature (3-dim) appended to X (total 39 features)
3. Cross-sectional rank post-process on predictions → [0,1] per-date
4. `min_train_dis=200` → F0a fold now trainable
5. `relevance_bins=32`

**Results**:

| Fold | CAGR | Δ vs v1 |
|---|---|---|
| F0a | +13.5% | +13.5pp (was 0%) |
| F0b | +11.4% | +0.1pp |
| F1 | +29.6% | +2.2pp |
| F2 | +7.7% | +6.6pp |
| F3 | +23.4% | **-9.2pp** |
| F4 | +19.4% | -1.6pp |

**Key insight**: v15 improved 4/6 folds but **regressed on F3 and F4** — the cross-sectional rank post-process preserved ordering but discarded magnitude information, weakening the simulator's top-K confidence cushion.

**Hardgate**: 0/3 fold-sets pass (FAIL on G-B: 17.5/21.5 = 81%, needed ≥90%)

**Feature importance** (documented in `docs/ml_v15_breakthrough_assessment.md`):
Top: `BREAKOUT_252`, `ATR_LOW`, `RG_DEFENSIVE`, `VAL_BOOK2PRICE`, `SMA_CROSS`, `QUAL_ROE` — aligns with GA's best factor compositions.

---

### 2.3 ML-1.6 — Raw Scores + Deeper Trees (2026-05-08)

**Script**: `phase3/ml/run_ml_v16.py`
**Artefact**: `ml/artifacts/frozen_signal_ML_v16_20260508_175944.npz`
**Changes from v1.5**:
1. **No cross-sectional rank post-process**: stores raw ranker scores → per-date min-max normalisation
2. **Wider relevance bins**: 32 → 200 (finer ordering resolution)
3. **Deeper trees**: `max_depth=7` (was 6), `n_estimators=1200` (was 800), `lr=0.04` (was 0.05), `min_child_weight=25`
4. **NDCG fix**: `ndcg_exp_gain=False` (required for bins > 31)

**Results**:

| Fold | CAGR | Δ vs v1.5 |
|---|---|---|
| F0a | +13.5% | +0.0pp |
| F0b | +11.3% | -0.1pp |
| F1 | +28.9% | -0.7pp |
| F2 | +9.2% | +1.5pp |
| F3 | +24.1% | +0.7pp |
| F4 | +21.0% | +1.6pp |

**Mean CAGR**: +18.0% (vs v1.5 +17.5%, vs P2_OOS +21.5%)
**CV**: 0.398 (best stability of all ML versions, better than P2_OOS's 0.429)

**Walk-forward evaluation across all 3 fold-sets**:

| Fold-set | Mean CAGR | CV | Hardgate |
|---|---|---|---|
| default (6-fold) | +18.0% | 0.398 | FAIL (G-B: 18.0 < 19.4 threshold) |
| rolling (8×8yr) | +14.5% | 0.264 | FAIL (G-A, G-B) |
| regime (7-fold) | +18.8% | 0.548 | FAIL (G-B) |

**Conclusion**: ML-1.6 is the best ML version so far. CV is excellent (0.398 < any GA signal), but CAGR is 16% below P2_OOS threshold. The monolithic XGBRanker architecture cannot close the gap.

---

## 3. Known Issues & Bug Fixes

| Issue | Version | Fix |
|---|---|---|
| `reg:squarederror` predictions collapse to mean | v1 | Switched to `rank:pairwise` in v1.5 |
| F0a fold skip (insufficient train data) | v1 | `min_train_dis=200` in v1.5 |
| Rank post-process kills magnitude info | v1.5 | Per-date min-max normalisation in v1.6 |
| `XGBoostError: Relevance degrees > 31` | v1.6 | `ndcg_exp_gain=False` |
| F3 `best_iter=0` (no learning) | v1 | Fixed by rank:pairwise in v1.5 (best_iter=44–302) |

---

## 4. Architecture Overview

```
precompute_qresearch_v4_12_*.npz
    ↓
data_panel.py  →  (D×N rows) × 36 features + 3 regime one-hot = 39 dims
    ↓
targets.py     →  cross-sectional rank of fwd1 per date → y ∈ [0,1]
    ↓
walk_forward.py →  leakage-safe train/eval splits (21-day embargo)
    ↓
models/xgb.py  →  XGBRanker (rank:pairwise) with early stopping
    ↓
score_panel.py →  predictions → (D, N, 3-regime) panel → frozen_signal_ML_*.npz
    ↓
step_d_walk_forward.py  →  simulation-based CAGR/MDD/Sharpe evaluation
```

**Feature set** (36 base + 3 regime one-hot):
- Technical: `MOM_3M`, `MOM_6M`, `RSI_14`, `ADX`, `ATR`, `STOCH`, `WILLR`, `SMA_CROSS`, `SMA50_SLOPE`, `DIST_FROM_SMA50`, `BREAKOUT_126`, `BREAKOUT_252`, etc.
- Fundamental: `VAL_BOOK2PRICE`, `VAL_EARN_YIELD`, `CF_FCF_YIELD`, `QUAL_ROE`, `LEV_DEBT_EQUITY`, etc.
- Cross-products: `ROE×BREAKOUT_126`, `MOM×ATR`, etc. (16 of 36)
- Regime: `RG_BULL`, `RG_SIDE`, `RG_DEFENSIVE` (one-hot, v1.5+)

**Live safety**: triple-guard
1. Artefact in `phase3/ml/artifacts/` (separate from live cache)
2. `signal_type='ml_external_scores'` flag in npz
3. `run_daily` raises `RuntimeError` if ML signal detected

---

## 5. Files Inventory

### Scripts
| File | Purpose |
|---|---|
| `ml/run_ml_v1.py` | v1 runner (XGBRegressor, deprecated) |
| `ml/run_ml_v15.py` | v1.5 runner (XGBRanker + cs-rank) |
| `ml/run_ml_v16.py` | v1.6 runner (XGBRanker + raw scores) — **current best** |
| `ml/data_panel.py` | Feature matrix builder from precompute pack |
| `ml/targets.py` | Cross-sectional rank target computation |
| `ml/walk_forward.py` | Leakage-safe fold splitting (default 6-fold) |
| `ml/score_panel.py` | ML predictions → frozen signal npz |
| `ml/models/xgb.py` | XGBoost wrapper (fit/predict/importance) |

### Artefacts
| File | Version | Size |
|---|---|---|
| `ml/artifacts/frozen_signal_ML_v1_smoke_20260507_220655.npz` | v1 smoke | 661K |
| `ml/artifacts/frozen_signal_ML_v1_20260507_221335.npz` | v1 full | 3.0M |
| `ml/artifacts/frozen_signal_ML_v15_20260507_224123.npz` | v1.5 full | 3.5M |
| `ml/artifacts/frozen_signal_ML_v16_20260508_175944.npz` | v1.6 full | 5.3M |

### Evaluation Reports
| File | Content |
|---|---|
| `docs/ml_v1_v2_breakthrough_assessment.md` | v1 diagnosis & v1.5 roadmap |
| `docs/ml_v15_breakthrough_assessment.md` | v1.5 results & v1.6/ML-2 roadmap |
| `docs/oos_clean_baseline_breakthrough_findings_20260508.md` | Comprehensive OOS-clean comparison (includes ML v15/v16) |
| `docs/t5_walk_forward_results_20260507_221556.*` | v1 step_d eval (vs V2) |
| `docs/t5_walk_forward_results_20260507_224323.*` | v1 + v1.5 step_d eval (vs V2) |
| `docs/t5_walk_forward_results_20260508_184327.*` | v1.5 + v1.6 (default, vs P2_OOS) |
| `docs/t5_walk_forward_results_20260508_190913.*` | v1.5 + v1.6 (rolling, vs P2_OOS) |
| `docs/t5_walk_forward_results_20260508_181835.*` | v1.5 + v1.6 (default, vs V2) |

### step_d Registration

In `tests/step_d_walk_forward.py`:
- Line 162: `ml_xgb_v16` (active, in primary SIGNALS list)
- Line 258: `ml_xgb_v1` (legacy, in extended SIGNALS list)
- Line 260: `ml_xgb_v15` (legacy, in extended SIGNALS list)

---

## 6. Performance Gap Analysis

### Why ML can't match GA (yet)

| Factor | GA Linear Score | ML (XGBRanker) |
|---|---|---|
| Regime handling | Separate weight vectors per regime (BULL/SIDE/DEF) | Single model + regime one-hot input |
| Factor selection | GA meta-search discovers optimal factor subsets per regime | All 36 features always included |
| Weight optimisation | GA optimises factor weights directly for IC/CAGR objective | Tree splits approximate feature weighting |
| Ensemble | L3 weighted average of multiple GA signals | Single XGBRanker model |
| Stability | CV 0.43–0.51 (moderate) | CV 0.40 (better) |
| Magnitude | Full [0,1] spread from design | Raw scores need normalisation |

**Core bottleneck**: The monolithic XGBRanker treats all regimes in one model. GA's regime-conditional architecture (3 separate weight vectors) is a structural advantage that regime one-hot features cannot fully replicate.

### F4 (post-OOS) comparison

| Signal | F4 CAGR |
|---|---|
| P11_FUNDB_ANCHOR (shadow) | +47.8% |
| P5E_FUND_BREAKOUT | +46.4% |
| P2_BATCH11_OOS (baseline) | +30.3% |
| ML_XGB_v16 | +21.0% |
| ML_XGB_v15 | +19.4% |
| ML_XGB_v1 | +20.9% |

ML's F4 is consistently ~21%, roughly 2/3 of the OOS-clean baseline and less than half of the best GA ensemble.

---

## 7. Future Plan (ML-2.0+)

### Phase ML-2.0: Regime-Specific Submodels (HIGH PRIORITY)

**Objective**: Close the regime gap — train separate XGBRanker per regime.

**Implementation**:
1. Split training data by regime label (BULL/SIDE/DEFENSIVE)
2. Train 3 independent XGBRanker models, each with regime-appropriate features
3. At inference, route to the correct model based on current VIX regime
4. Each model can learn different feature importances (e.g., momentum in BULL, value in SIDE)

**Expected impact**: +3–5pp mean CAGR (hypothesis: closes most of the P2_OOS gap)
**Effort**: ~2 hours (modify `run_ml_v16.py` to loop per regime)

**Risk**: DEFENSIVE regime has very few training days → model may underfit.
Mitigation: fall back to full-regime model for DEFENSIVE, or use SIDE+DEFENSIVE merged.

### Phase ML-2.1: GA-ML Ensemble

**Objective**: Combine GA's superior CAGR with ML's superior stability.

**Implementation**:
```
FinalScore = α × GA_score + (1−α) × ML_score
```
- α = 0.5 (start), optimise via grid search on validation folds
- GA_score = P11_FUNDB_ANCHOR (best OOS-clean GA signal)
- ML_score = ML v2.0 regime-specific model

**Expected impact**: If ML-2.0 reaches ~22% CAGR, ensemble could reach ~25–28% (diversification benefit from low correlation).
**Effort**: ~30 min (use existing `p2_ensemble_composer.py` with ML signal as a component)

### Phase ML-2.2: Feature Engineering

**Objective**: Improve raw feature quality.

Candidates:
1. **Drop redundant cross-product features** (16 of 36 are engineered) — trees naturally learn interactions
2. **Add fundamental calendar features** (earnings date proximity, sector momentum)
3. **Add macro features** (yield curve slope, credit spread, VIX term structure)
4. **Temporal features** (day-of-week, month, days-since-last-earnings)
5. **Lagged return features** (1d, 5d, 10d raw returns as additional momentum signals)

### Phase ML-3.0: GA as Meta-Optimizer (STRETCH)

**Objective**: Use GA to optimise the ML blend.

**Implementation**: GA searches over:
- Per-regime α (blend weight between GA and ML)
- ML model selection (which XGB checkpoint per fold)
- Feature subset selection for ML

This is the most powerful option but requires the most development.

### Phase ML-2.3: LightGBM Comparison

**Objective**: Check if model family matters.

**Implementation**: Drop-in replacement using `lightgbm.LGBMRanker` with `lambdarank` objective. Same features, same walk-forward splits.

**Effort**: ~1 hour
**Expected impact**: Marginal (usually within ±1pp of XGBoost)

---

## 8. Recommended Execution Order

| Priority | Phase | Expected CAGR gain | Effort | Dependencies |
|---|---|---|---|---|
| 1 | **ML-2.0** (regime-specific submodels) | +3–5pp | 2h | None |
| 2 | **ML-2.1** (GA-ML ensemble) | +2–3pp additional | 30min | ML-2.0 |
| 3 | **ML-2.2** (feature engineering) | +1–2pp | 4h | ML-2.0 |
| 4 | **ML-2.3** (LightGBM) | ±1pp | 1h | ML-2.0 |
| 5 | **ML-3.0** (GA meta-optimizer) | unknown | 8h+ | ML-2.1 validated |

**Decision gate after ML-2.1**:
- If ensemble CAGR ≥ 25% (passes hardgate vs P2_OOS): proceed to shadow run
- If ensemble CAGR 20–25%: feature engineering (ML-2.2) before shadow
- If ensemble CAGR < 20%: ML track deprioritised, focus on GA ensembles

---

## 9. Other Pending (Non-ML) Items

| Item | Status | Notes |
|---|---|---|
| Shadow run (P11_FUNDB_ANCHOR, day 2/30) | Active | Daily email reports include shadow BUY/SELL |
| Shadow candidates: P12_X (BULL_INJ_FUNDB_ANCHOR), P12_Y (TRIPLE_SPEC_OOS) | Queued | Registered for next shadow cycle |
| P13_X_PLUS_Y ensemble | Built | `frozen_signal_P13_X_PLUS_Y_20260509_182539.npz` — evaluation pending |
| Regime blend (VIX 17.2–18.8 transition zone) | Live | `config.yaml` + `config_real.yaml` updated |
| Hardgate spec v1.3 (dual-baseline) | Pending | Formalise P2_OOS + V2 dual evaluation |

---

## 10. Quick-Start for Resuming ML Work

```bash
# Run ML-2.0 (regime-specific) — create run_ml_v20.py first
cd /Users/shin-il/PyCharmMiscProject
python3 -u phase3/ml/run_ml_v20.py --label v20

# Evaluate against OOS-clean baseline
python3 phase3/tests/step_d_walk_forward.py \
    --baseline p2_oos \
    --signals ml_xgb_v20 \
    --fold-set default rolling regime

# If promising, build GA-ML ensemble
python3 phase3/tests/p2_ensemble_composer.py --preset ML_ENS
```

**Precompute pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
(located in `/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/`)
