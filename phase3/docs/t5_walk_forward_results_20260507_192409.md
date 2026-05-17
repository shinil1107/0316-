# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-07T19:24:09
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 8  |  **Signals**: 4
**Total sims**: 32

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R2<br/>(sliding) | R3<br/>(sliding) | R4<br/>(sliding) | R5<br/>(sliding) | R6<br/>(sliding) | R7<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +19.91 | +20.66 | +25.25 | +27.47 | +24.95 | +30.98 | +32.18 | +35.98 | +27.17 | 0.193 |
| **P10_CROSS_ERA_EQ** | +15.21 | +14.81 | +17.68 | +20.78 | +16.01 | +21.24 | +22.88 | +28.46 | +19.63 | 0.222 |
| **P10_CROSS_ERA_V2H** | +14.71 | +14.62 | +17.13 | +20.47 | +17.53 | +22.56 | +25.09 | +30.71 | +20.35 | 0.256 |
| **P10_CROSS_ERA_FULL** | +15.15 | +15.54 | +18.47 | +21.41 | +17.26 | +22.50 | +24.24 | +29.91 | +20.56 | 0.228 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +27.17 / 5.26 / 0.19 | +27.17 / 5.26 / 0.19 | +19.91 | 8/8 |
| **P10_CROSS_ERA_EQ** | +19.63 / 4.36 / 0.22 | +19.63 / 4.36 / 0.22 | +14.81 | 8/8 |
| **P10_CROSS_ERA_V2H** | +20.35 / 5.22 / 0.26 | +20.35 / 5.22 / 0.26 | +14.62 | 8/8 |
| **P10_CROSS_ERA_FULL** | +20.56 / 4.68 / 0.23 | +20.56 / 4.68 / 0.23 | +15.15 | 8/8 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P10_CROSS_ERA_EQ** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P10_CROSS_ERA_V2H** | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P10_CROSS_ERA_FULL** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |

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

### Baseline_V2

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +19.91% | 31.46% | 0.83 | 0.63 | 10.19% | +0.0124 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +20.66% | 31.42% | 1.06 | 0.66 | 9.80% | +0.0116 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +25.25% | 38.24% | 1.10 | 0.66 | 9.31% | +0.0150 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +27.47% | 38.43% | 1.16 | 0.71 | 8.77% | +0.0081 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +24.95% | 38.33% | 1.04 | 0.65 | 10.02% | +0.0123 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +30.98% | 38.51% | 1.23 | 0.80 | 14.52% | +0.0117 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +32.18% | 38.10% | 1.23 | 0.84 | 13.72% | +0.0120 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +35.98% | 38.25% | 1.26 | 0.94 | 14.57% | +0.0076 | 1045/888/0 |

### P10_CROSS_ERA_EQ

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +15.21% | 30.80% | 0.64 | 0.49 | 8.86% | +0.0156 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +14.81% | 29.86% | 0.86 | 0.50 | 7.70% | +0.0198 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +17.68% | 35.65% | 0.87 | 0.50 | 7.33% | +0.0185 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +20.78% | 35.86% | 0.97 | 0.58 | 6.76% | +0.0080 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +16.01% | 35.45% | 0.77 | 0.45 | 7.30% | +0.0106 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +21.24% | 35.72% | 0.96 | 0.59 | 9.57% | +0.0089 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +22.88% | 36.42% | 0.99 | 0.63 | 8.18% | +0.0159 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +28.46% | 36.45% | 1.09 | 0.78 | 8.97% | +0.0115 | 1045/888/0 |

### P10_CROSS_ERA_V2H

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +14.71% | 30.75% | 0.64 | 0.48 | 8.77% | +0.0173 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +14.62% | 29.32% | 0.86 | 0.50 | 7.70% | +0.0194 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +17.13% | 35.52% | 0.85 | 0.48 | 7.33% | +0.0189 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +20.47% | 35.62% | 0.97 | 0.57 | 6.70% | +0.0082 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +17.53% | 35.46% | 0.84 | 0.49 | 7.26% | +0.0109 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.56% | 35.60% | 1.02 | 0.63 | 9.63% | +0.0095 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +25.09% | 36.07% | 1.07 | 0.70 | 8.65% | +0.0145 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +30.71% | 36.46% | 1.17 | 0.84 | 10.06% | +0.0103 | 1045/888/0 |

### P10_CROSS_ERA_FULL

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +15.15% | 30.04% | 0.65 | 0.50 | 8.91% | +0.0153 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +15.54% | 29.31% | 0.89 | 0.53 | 7.79% | +0.0194 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +18.47% | 35.30% | 0.90 | 0.52 | 7.36% | +0.0178 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +21.41% | 35.50% | 1.00 | 0.60 | 7.03% | +0.0082 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +17.26% | 35.50% | 0.82 | 0.49 | 7.63% | +0.0110 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.50% | 35.42% | 1.01 | 0.64 | 10.12% | +0.0090 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +24.24% | 36.30% | 1.04 | 0.67 | 8.75% | +0.0167 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +29.91% | 35.89% | 1.14 | 0.83 | 9.52% | +0.0114 | 1045/888/0 |

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