# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-10T16:02:50
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 8  |  **Signals**: 4
**Total sims**: 32

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R2<br/>(sliding) | R3<br/>(sliding) | R4<br/>(sliding) | R5<br/>(sliding) | R6<br/>(sliding) | R7<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **P2_BATCH11_OOS** | +23.59 | +17.85 | +20.88 | +22.84 | +20.29 | +24.99 | +25.71 | +26.42 | +22.82 | 0.122 |
| **ML_XGB_v16** | +7.87 | +10.94 | +13.00 | +15.49 | +13.12 | +20.42 | +15.83 | +18.97 | +14.46 | 0.266 |
| **ML_XGB_v20** | +5.11 | +8.63 | +13.05 | +17.56 | +15.08 | +20.71 | +14.32 | +17.30 | +13.97 | 0.338 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +22.68 | +19.63 | +22.96 | +27.45 | +23.27 | +30.72 | +30.06 | +34.36 | +26.39 | 0.179 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **P2_BATCH11_OOS** | +22.82 / 2.77 / 0.12 | +22.82 / 2.77 / 0.12 | +17.85 | 8/8 |
| **ML_XGB_v16** | +14.46 / 3.85 / 0.27 | +14.46 / 3.85 / 0.27 | +7.87 | 8/8 |
| **ML_XGB_v20** | +13.97 / 4.72 / 0.34 | +13.97 / 4.72 / 0.34 | +5.11 | 8/8 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +26.39 / 4.72 / 0.18 | +26.39 / 4.72 / 0.18 | +19.63 | 8/8 |

## 3. Gate verdicts (vs baseline = P2_BATCH11_OOS)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **P2_BATCH11_OOS** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **ML_XGB_v16** | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | — | **✗ FAIL** |
| **ML_XGB_v20** | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✗ FAIL** |

### Gate definitions

All gates are **relative to baseline** — baseline always auto-passes.

| Gate | Type | Rule |
|---|---|---|
| G-A | Hard | CV(cand) ≤ CV(baseline) + 0.05 (relative stability, 5pp tolerance) |
| G-B | Hard | mean_CAGR(cand) ≥ mean_CAGR(baseline) × 0.90 (CAGR floor, 10% tolerance) |
| G-C | Hard | worst_fold_CAGR(cand) ≥ worst_fold_CAGR(baseline) − 0.01 (tail risk, 1pp tolerance) |
| G-D | Hard | pos_count(cand) ≥ pos_count(baseline) (no fewer positive folds) |
| G-E | Soft | worst_MDD(cand) ≤ worst_MDD(baseline) × 1.10 (drawdown guard) |
| G-F | Soft | mean_Sharpe(cand) ≥ mean_Sharpe(baseline) × 0.90 (risk-adj floor) |
| G-G | Soft | OOS_CAGR_std(cand) ≤ OOS_CAGR_std(baseline) + 0.01 (OOS consistency) |
| G-H | Soft | Lift_10d(cand) ≥ Lift_10d(baseline) × 0.80 (surge capture, top-decile fwd+20% 10d) |

## 4. Per-fold detail

### P2_BATCH11_OOS

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +23.59% | 26.17% | 0.79 | 0.90 | 16.44% | +0.0110 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +17.85% | 26.09% | 1.07 | 0.68 | 10.74% | +0.0069 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +20.88% | 34.61% | 1.03 | 0.60 | 10.48% | +0.0134 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +22.84% | 34.58% | 1.07 | 0.66 | 8.44% | +0.0201 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +20.29% | 34.76% | 0.88 | 0.58 | 9.14% | +0.0278 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +24.99% | 34.65% | 1.04 | 0.72 | 13.53% | +0.0304 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +25.71% | 34.78% | 1.05 | 0.74 | 12.58% | +0.0275 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +26.42% | 34.26% | 1.04 | 0.77 | 12.10% | +0.0175 | 1045/888/0 |

### ML_XGB_v16

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +7.87% | 26.23% | 0.65 | 0.30 | 2.84% | +nan | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +10.94% | 26.23% | 0.82 | 0.42 | 4.11% | +nan | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +13.00% | 39.90% | 0.70 | 0.33 | 4.34% | +nan | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +15.49% | 39.82% | 0.77 | 0.39 | 4.54% | +nan | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +13.12% | 40.01% | 0.63 | 0.33 | 4.60% | +nan | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +20.42% | 39.91% | 0.88 | 0.51 | 7.02% | +nan | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +15.83% | 39.90% | 0.75 | 0.40 | 5.88% | +nan | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +18.97% | 39.90% | 0.64 | 0.48 | 8.29% | +nan | 1045/888/0 |

### ML_XGB_v20

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +5.11% | 27.06% | 0.50 | 0.19 | 1.24% | +nan | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +8.63% | 27.06% | 0.71 | 0.32 | 2.12% | +nan | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +13.05% | 35.66% | 0.71 | 0.37 | 3.03% | +nan | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +17.56% | 35.66% | 0.85 | 0.49 | 4.24% | +nan | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +15.08% | 35.66% | 0.70 | 0.42 | 5.22% | +nan | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +20.71% | 35.61% | 0.88 | 0.58 | 7.23% | +nan | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +14.32% | 36.42% | 0.69 | 0.39 | 5.27% | +nan | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +17.30% | 36.42% | 0.64 | 0.47 | 7.36% | +nan | 1045/888/0 |

### P11_OOS_CLEAN_L3_FUNDB_ANCHOR

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +22.68% | 28.16% | 0.75 | 0.81 | 15.02% | -0.0008 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +19.63% | 27.62% | 1.09 | 0.71 | 10.51% | -0.0007 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +22.96% | 35.33% | 1.03 | 0.65 | 10.15% | +0.0123 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +27.45% | 35.29% | 1.12 | 0.78 | 8.94% | +0.0104 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +23.27% | 35.20% | 0.96 | 0.66 | 9.60% | +0.0197 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +30.72% | 35.14% | 1.17 | 0.87 | 15.51% | +0.0142 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +30.06% | 35.64% | 1.14 | 0.84 | 12.55% | +0.0121 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +34.36% | 35.44% | 1.20 | 0.97 | 14.58% | +0.0064 | 1045/888/0 |

---

**Interpretation notes**

- **Sliding 8-year windows** (1-year step) test whether performance is stable
  as the evaluation window shifts through the in-sample period.
- Each window spans ~2000 trading days → statistically robust per-fold estimates.
- Overlapping windows provide a smooth performance trend over time:
  consistent CAGR across all 8 windows → temporally robust signal.
  CAGR drops in specific windows → period-specific weakness identifiable.
- **IN-SAMPLE temporal stability audit**, NOT OOS validation.
  All folds fall within GA training range (2011 → 2026-03).
  True OOS validation requires Phase B P9_OOS_VALIDATION.