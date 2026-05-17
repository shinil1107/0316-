# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-07T20:08:38
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 8  |  **Signals**: 4
**Total sims**: 32

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R2<br/>(sliding) | R3<br/>(sliding) | R4<br/>(sliding) | R5<br/>(sliding) | R6<br/>(sliding) | R7<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +20.88 | +21.16 | +25.12 | +26.81 | +23.86 | +30.11 | +30.97 | +35.24 | +26.77 | 0.176 |
| **P10_CROSS_ERA_EQ** | +15.23 | +15.04 | +17.37 | +20.55 | +15.62 | +21.06 | +22.11 | +26.66 | +19.21 | 0.201 |
| **P10_CROSS_ERA_V2H** | +14.81 | +14.70 | +16.75 | +20.67 | +18.01 | +22.83 | +24.34 | +27.84 | +19.99 | 0.222 |
| **P10_CROSS_ERA_FULL** | +15.36 | +15.54 | +18.10 | +21.23 | +17.40 | +22.59 | +23.34 | +27.90 | +20.18 | 0.202 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +26.77 / 4.72 / 0.18 | +26.77 / 4.72 / 0.18 | +20.88 | 8/8 |
| **P10_CROSS_ERA_EQ** | +19.21 / 3.85 / 0.20 | +19.21 / 3.85 / 0.20 | +15.04 | 8/8 |
| **P10_CROSS_ERA_V2H** | +19.99 / 4.45 / 0.22 | +19.99 / 4.45 / 0.22 | +14.70 | 8/8 |
| **P10_CROSS_ERA_FULL** | +20.18 / 4.08 / 0.20 | +20.18 / 4.08 / 0.20 | +15.36 | 8/8 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P10_CROSS_ERA_EQ** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P10_CROSS_ERA_V2H** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
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
| R1 | sliding | 2011-01-01→2018-12-31 | +20.88% | 31.53% | 0.85 | 0.66 | 10.78% | +0.0124 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +21.16% | 31.66% | 1.08 | 0.67 | 10.08% | +0.0116 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +25.12% | 37.47% | 1.10 | 0.67 | 9.83% | +0.0150 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +26.81% | 37.75% | 1.14 | 0.71 | 8.70% | +0.0081 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +23.86% | 37.55% | 1.01 | 0.64 | 9.77% | +0.0123 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +30.11% | 37.79% | 1.21 | 0.80 | 14.04% | +0.0117 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +30.97% | 37.36% | 1.20 | 0.83 | 13.05% | +0.0120 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +35.24% | 37.58% | 1.24 | 0.94 | 13.78% | +0.0076 | 1045/888/0 |

### P10_CROSS_ERA_EQ

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +15.23% | 30.89% | 0.64 | 0.49 | 8.90% | +0.0156 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +15.04% | 29.70% | 0.87 | 0.51 | 7.71% | +0.0198 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +17.37% | 35.79% | 0.86 | 0.49 | 7.34% | +0.0185 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +20.55% | 36.01% | 0.97 | 0.57 | 6.78% | +0.0080 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +15.62% | 36.09% | 0.76 | 0.43 | 7.16% | +0.0106 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +21.06% | 35.85% | 0.96 | 0.59 | 9.53% | +0.0089 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +22.11% | 36.61% | 0.96 | 0.60 | 8.20% | +0.0159 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +26.66% | 36.43% | 1.05 | 0.73 | 8.48% | +0.0115 | 1045/888/0 |

### P10_CROSS_ERA_V2H

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +14.81% | 30.68% | 0.64 | 0.48 | 8.81% | +0.0173 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +14.70% | 29.30% | 0.86 | 0.50 | 7.72% | +0.0194 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +16.75% | 35.51% | 0.84 | 0.47 | 7.33% | +0.0189 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +20.67% | 35.58% | 0.97 | 0.58 | 6.77% | +0.0082 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +18.01% | 35.49% | 0.85 | 0.51 | 7.28% | +0.0109 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.83% | 35.64% | 1.03 | 0.64 | 9.73% | +0.0095 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +24.34% | 36.01% | 1.05 | 0.68 | 8.66% | +0.0145 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +27.84% | 36.47% | 1.09 | 0.76 | 9.15% | +0.0103 | 1045/888/0 |

### P10_CROSS_ERA_FULL

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +15.36% | 30.16% | 0.65 | 0.51 | 8.98% | +0.0153 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +15.54% | 29.14% | 0.89 | 0.53 | 7.80% | +0.0194 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +18.10% | 35.56% | 0.88 | 0.51 | 7.38% | +0.0178 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +21.23% | 35.50% | 0.99 | 0.60 | 7.01% | +0.0082 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +17.40% | 35.50% | 0.83 | 0.49 | 7.72% | +0.0110 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.59% | 35.58% | 1.01 | 0.64 | 10.22% | +0.0090 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +23.34% | 35.77% | 1.01 | 0.65 | 8.76% | +0.0167 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +27.90% | 36.06% | 1.09 | 0.77 | 9.26% | +0.0114 | 1045/888/0 |

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