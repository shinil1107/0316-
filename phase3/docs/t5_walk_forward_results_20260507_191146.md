# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-07T19:11:46
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 7  |  **Signals**: 4
**Total sims**: 28

## 1. Per-fold CAGR (%)

| Signal | BULL_1<br/>(bull_dom) | BULL_2<br/>(bull_dom) | BULL_3<br/>(bull_dom) | SIDE_1<br/>(side_dom) | SIDE_2<br/>(side_dom) | MIX_1<br/>(mixed) | MIX_2<br/>(mixed) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +34.34 | +43.92 | +1.67 | +14.76 | +45.17 | +45.92 | +30.06 | 0.528 |
| **P10_CROSS_ERA_EQ** | +17.17 | +26.29 | +40.23 | +3.10 | +3.09 | +29.81 | +43.73 | +23.35 | 0.650 |
| **P10_CROSS_ERA_V2H** | +17.80 | +24.26 | +39.41 | +2.38 | +8.31 | +28.39 | +41.04 | +23.09 | 0.589 |
| **P10_CROSS_ERA_FULL** | +18.13 | +26.85 | +40.00 | +3.11 | +4.59 | +31.44 | +47.07 | +24.46 | 0.637 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | mixed (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +30.06 / 15.86 / 0.53 | +34.29 / 7.88 / 0.23 | +8.22 / 6.54 / 0.80 | +45.55 / 0.37 / 0.01 | +1.67 | 7/7 |
| **P10_CROSS_ERA_EQ** | +23.35 / 15.16 / 0.65 | +27.90 / 9.48 / 0.34 | +3.09 / 0.00 / 0.00 | +36.77 / 6.96 / 0.19 | +3.09 | 7/7 |
| **P10_CROSS_ERA_V2H** | +23.09 / 13.59 / 0.59 | +27.16 / 9.06 / 0.33 | +5.35 / 2.96 / 0.55 | +34.72 / 6.33 / 0.18 | +2.38 | 7/7 |
| **P10_CROSS_ERA_FULL** | +24.46 / 15.57 / 0.64 | +28.33 / 8.99 / 0.32 | +3.85 / 0.74 / 0.19 | +39.25 / 7.81 / 0.20 | +3.11 | 7/7 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P10_CROSS_ERA_EQ** | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P10_CROSS_ERA_V2H** | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P10_CROSS_ERA_FULL** | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |

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
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +24.62% | 19.74% | 1.25 | 1.25 | 2.37% | -0.0069 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +34.34% | 8.98% | 1.85 | 3.83 | 1.15% | +0.0257 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +1.67% | 23.96% | 0.18 | 0.07 | 0.93% | +0.0446 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +45.17% | 38.21% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P10_CROSS_ERA_EQ

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +17.17% | 20.09% | 1.00 | 0.85 | 2.16% | +0.0117 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +26.29% | 8.19% | 1.58 | 3.21 | 1.05% | +0.0120 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +40.23% | 12.83% | 1.84 | 3.14 | 0.87% | +0.0403 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +3.10% | 23.48% | 0.27 | 0.13 | 0.87% | +0.0564 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +3.09% | 31.23% | 0.25 | 0.10 | 1.15% | -0.0163 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +29.81% | 36.57% | 1.09 | 0.82 | 1.31% | +0.0070 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +43.73% | 29.47% | 1.41 | 1.48 | 1.05% | +0.0319 | 275/157/0 |

### P10_CROSS_ERA_V2H

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +17.80% | 20.53% | 1.04 | 0.87 | 2.18% | +0.0095 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +24.26% | 8.46% | 1.48 | 2.87 | 1.05% | +0.0141 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +39.41% | 12.39% | 1.86 | 3.18 | 0.86% | +0.0386 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +2.38% | 23.44% | 0.23 | 0.10 | 0.87% | +0.0571 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +8.31% | 26.89% | 0.45 | 0.31 | 1.12% | -0.0184 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +28.39% | 35.98% | 1.06 | 0.79 | 1.31% | +0.0043 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +41.04% | 29.05% | 1.39 | 1.41 | 1.10% | +0.0290 | 275/157/0 |

### P10_CROSS_ERA_FULL

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +18.13% | 20.63% | 1.05 | 0.88 | 2.18% | +0.0116 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +26.85% | 8.87% | 1.59 | 3.03 | 1.06% | +0.0128 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +40.00% | 12.38% | 1.86 | 3.23 | 0.88% | +0.0385 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +3.11% | 22.86% | 0.27 | 0.14 | 0.87% | +0.0564 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +4.59% | 27.66% | 0.31 | 0.17 | 1.16% | -0.0138 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +31.44% | 35.86% | 1.14 | 0.88 | 1.33% | +0.0069 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +47.07% | 29.07% | 1.50 | 1.62 | 1.09% | +0.0321 | 275/157/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.