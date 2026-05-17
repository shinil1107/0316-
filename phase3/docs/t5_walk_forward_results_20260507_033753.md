# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-07T03:37:53
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 8  |  **Signals**: 3
**Total sims**: 24

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R2<br/>(sliding) | R3<br/>(sliding) | R4<br/>(sliding) | R5<br/>(sliding) | R6<br/>(sliding) | R7<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +19.91 | +20.66 | +25.26 | +27.45 | +24.94 | +31.12 | +32.19 | +36.04 | +27.20 | 0.194 |
| **P9_TRIPLE_SPEC_A** | +20.30 | +19.47 | +22.89 | +25.32 | +21.79 | +25.53 | +27.70 | +33.74 | +24.59 | 0.176 |
| **P9_BAL_SIDE_B** | +23.34 | +17.36 | +21.89 | +23.45 | +19.07 | +22.85 | +24.55 | +29.29 | +22.73 | 0.148 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +27.20 / 5.28 / 0.19 | +27.20 / 5.28 / 0.19 | +19.91 | 8/8 |
| **P9_TRIPLE_SPEC_A** | +24.59 / 4.33 / 0.18 | +24.59 / 4.33 / 0.18 | +19.47 | 8/8 |
| **P9_BAL_SIDE_B** | +22.73 / 3.36 / 0.15 | +22.73 / 3.36 / 0.15 | +17.36 | 8/8 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_TRIPLE_SPEC_A** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_BAL_SIDE_B** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |

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
| R3 | sliding | 2013-01-01→2020-12-31 | +25.26% | 38.27% | 1.10 | 0.66 | 9.31% | +0.0150 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +27.45% | 38.42% | 1.16 | 0.71 | 8.77% | +0.0081 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +24.94% | 38.30% | 1.04 | 0.65 | 10.02% | +0.0123 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +31.12% | 38.50% | 1.24 | 0.81 | 14.58% | +0.0117 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +32.19% | 38.08% | 1.23 | 0.85 | 13.74% | +0.0120 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +36.04% | 38.25% | 1.26 | 0.94 | 14.59% | +0.0076 | 1045/888/0 |

### P9_TRIPLE_SPEC_A

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +20.30% | 30.39% | 0.83 | 0.67 | 11.84% | +0.0006 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +19.47% | 30.17% | 1.03 | 0.65 | 9.85% | +0.0095 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +22.89% | 35.11% | 1.03 | 0.65 | 9.47% | +0.0096 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +25.32% | 34.98% | 1.10 | 0.72 | 8.99% | -0.0036 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +21.79% | 34.97% | 0.94 | 0.62 | 9.50% | -0.0006 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +25.53% | 34.77% | 1.07 | 0.73 | 12.78% | -0.0037 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +27.70% | 34.93% | 1.10 | 0.79 | 12.08% | +0.0088 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +33.74% | 34.62% | 1.20 | 0.97 | 13.14% | +0.0051 | 1045/888/0 |

### P9_BAL_SIDE_B

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +23.34% | 32.06% | 0.81 | 0.73 | 13.68% | +0.0000 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +17.36% | 31.64% | 0.93 | 0.55 | 8.23% | +0.0112 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +21.89% | 35.92% | 0.99 | 0.61 | 8.23% | +0.0103 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +23.45% | 35.64% | 1.03 | 0.66 | 7.66% | -0.0033 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +19.07% | 35.75% | 0.85 | 0.53 | 8.61% | -0.0019 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.85% | 35.54% | 1.00 | 0.64 | 11.38% | -0.0053 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +24.55% | 35.67% | 1.01 | 0.69 | 9.84% | +0.0117 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +29.29% | 35.53% | 1.09 | 0.82 | 10.64% | +0.0070 | 1045/888/0 |

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