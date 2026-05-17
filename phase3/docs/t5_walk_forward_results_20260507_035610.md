# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-07T03:56:10
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 7  |  **Signals**: 2
**Total sims**: 14

## 1. Per-fold CAGR (%)

| Signal | BULL_1<br/>(bull_dom) | BULL_2<br/>(bull_dom) | BULL_3<br/>(bull_dom) | SIDE_1<br/>(side_dom) | SIDE_2<br/>(side_dom) | MIX_1<br/>(mixed) | MIX_2<br/>(mixed) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +34.34 | +43.92 | +1.67 | +14.76 | +45.18 | +45.92 | +30.06 | 0.528 |
| **P9_L3_EQUAL_Q** | +21.20 | +28.23 | +49.73 | +4.90 | +9.37 | +36.24 | +50.65 | +28.62 | 0.587 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | mixed (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +30.06 / 15.86 / 0.53 | +34.29 / 7.88 / 0.23 | +8.22 / 6.54 / 0.80 | +45.55 / 0.37 / 0.01 | +1.67 | 7/7 |
| **P9_L3_EQUAL_Q** | +28.62 / 16.81 / 0.59 | +33.05 / 12.14 / 0.37 | +7.13 / 2.23 / 0.31 | +43.45 / 7.20 / 0.17 | +4.90 | 7/7 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_L3_EQUAL_Q** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✗ FAIL** |

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
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +45.18% | 38.19% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P9_L3_EQUAL_Q

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +21.20% | 20.45% | 1.15 | 1.04 | 2.23% | +0.0162 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +28.23% | 10.58% | 1.53 | 2.67 | 1.05% | +0.0070 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +49.73% | 14.14% | 2.05 | 3.52 | 0.96% | +0.0418 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +4.90% | 24.88% | 0.36 | 0.20 | 0.92% | +0.0502 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +9.37% | 31.69% | 0.48 | 0.30 | 1.32% | -0.0118 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +36.24% | 33.98% | 1.27 | 1.07 | 1.36% | +0.0126 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +50.65% | 31.18% | 1.47 | 1.62 | 1.14% | +0.0368 | 275/157/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.