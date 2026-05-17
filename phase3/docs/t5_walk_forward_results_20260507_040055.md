# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-07T04:00:55
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 8  |  **Signals**: 2
**Total sims**: 16

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R2<br/>(sliding) | R3<br/>(sliding) | R4<br/>(sliding) | R5<br/>(sliding) | R6<br/>(sliding) | R7<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +19.91 | +20.66 | +25.25 | +27.47 | +24.95 | +30.98 | +32.18 | +35.98 | +27.17 | 0.193 |
| **P9_L3_EQUAL_Q** | +19.12 | +17.10 | +20.11 | +23.94 | +18.69 | +22.73 | +26.10 | +32.10 | +22.49 | 0.204 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +27.17 / 5.26 / 0.19 | +27.17 / 5.26 / 0.19 | +19.91 | 8/8 |
| **P9_L3_EQUAL_Q** | +22.49 / 4.58 / 0.20 | +22.49 / 4.58 / 0.20 | +17.10 | 8/8 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_L3_EQUAL_Q** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |

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

### P9_L3_EQUAL_Q

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +19.12% | 32.33% | 0.74 | 0.59 | 11.24% | +0.0107 | 1496/435/0 |
| R2 | sliding | 2012-01-01→2019-12-31 | +17.10% | 32.18% | 0.92 | 0.53 | 8.50% | +0.0197 | 1613/395/0 |
| R3 | sliding | 2013-01-01→2020-12-31 | +20.11% | 34.97% | 0.93 | 0.57 | 8.02% | +0.0163 | 1501/429/0 |
| R4 | sliding | 2014-01-01→2021-12-31 | +23.94% | 34.96% | 1.05 | 0.68 | 7.64% | +0.0065 | 1356/573/0 |
| R5 | sliding | 2015-01-01→2022-12-31 | +18.69% | 34.75% | 0.84 | 0.54 | 8.56% | +0.0091 | 1126/764/0 |
| R6 | sliding | 2016-01-01→2023-12-31 | +22.73% | 34.98% | 0.98 | 0.65 | 11.04% | +0.0069 | 1105/791/0 |
| R7 | sliding | 2017-01-01→2024-12-31 | +26.10% | 34.61% | 1.05 | 0.75 | 9.67% | +0.0183 | 1125/778/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +32.10% | 34.86% | 1.15 | 0.92 | 11.23% | +0.0134 | 1045/888/0 |

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