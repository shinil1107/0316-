# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-10T15:56:41
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 7  |  **Signals**: 4
**Total sims**: 28

## 1. Per-fold CAGR (%)

| Signal | BULL_1<br/>(bull_dom) | BULL_2<br/>(bull_dom) | BULL_3<br/>(bull_dom) | SIDE_1<br/>(side_dom) | SIDE_2<br/>(side_dom) | MIX_1<br/>(mixed) | MIX_2<br/>(mixed) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **P2_BATCH11_OOS** | +25.38 | +30.27 | +28.13 | -4.39 | +11.65 | +27.31 | +30.28 | +21.23 | 0.567 |
| **ML_XGB_v16** | +13.58 | +32.71 | +24.10 | +1.79 | +9.16 | +28.89 | +20.97 | +18.74 | 0.548 |
| **ML_XGB_v20** | +0.00 | +29.71 | +23.39 | +4.71 | +2.92 | +29.64 | +19.96 | +15.76 | 0.758 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +22.76 | +30.43 | +31.18 | +4.28 | +12.05 | +34.93 | +47.84 | +26.21 | 0.516 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | mixed (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **P2_BATCH11_OOS** | +21.23 / 12.03 / 0.57 | +27.93 / 2.00 / 0.07 | +3.63 / 8.02 / 2.21 | +28.79 / 1.48 / 0.05 | -4.39 | 6/7 |
| **ML_XGB_v16** | +18.74 / 10.26 / 0.55 | +23.46 / 7.82 / 0.33 | +5.47 / 3.68 / 0.67 | +24.93 / 3.96 / 0.16 | +1.79 | 7/7 |
| **ML_XGB_v20** | +15.76 / 11.94 / 0.76 | +17.70 / 12.78 / 0.72 | +3.81 / 0.89 / 0.23 | +24.80 / 4.84 / 0.20 | +0.00 | 6/7 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +26.21 / 13.52 / 0.52 | +28.12 / 3.80 / 0.14 | +8.17 / 3.88 / 0.48 | +41.38 / 6.46 / 0.16 | +4.28 | 7/7 |

## 3. Gate verdicts (vs baseline = P2_BATCH11_OOS)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **P2_BATCH11_OOS** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **ML_XGB_v16** | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ | ✗ | — | **✗ FAIL** |
| **ML_XGB_v20** | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |

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
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +25.38% | 14.43% | 1.46 | 1.76 | 2.82% | -0.0232 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +30.27% | 5.39% | 2.16 | 5.61 | 1.29% | +0.0483 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +28.13% | 13.12% | 1.56 | 2.14 | 1.10% | +0.0165 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | -4.39% | 25.84% | -0.18 | -0.17 | 1.06% | +0.0227 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +11.65% | 28.55% | 0.54 | 0.41 | 1.39% | +0.0359 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +27.31% | 34.95% | 1.02 | 0.78 | 1.47% | +0.0141 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +30.28% | 26.58% | 1.21 | 1.14 | 1.19% | +0.0068 | 275/157/0 |

### ML_XGB_v16

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +13.58% | 7.70% | 1.47 | 1.76 | 1.16% | +nan | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +32.71% | 4.24% | 2.52 | 7.72 | 0.29% | +nan | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +24.10% | 18.52% | 1.29 | 1.30 | 0.92% | +nan | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +1.79% | 26.58% | 0.19 | 0.07 | 0.92% | +nan | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +9.16% | 26.45% | 0.46 | 0.35 | 1.01% | +nan | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +28.89% | 39.90% | 0.94 | 0.72 | 1.19% | +nan | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +20.97% | 25.20% | 0.62 | 0.83 | 1.30% | +nan | 275/157/0 |

### ML_XGB_v20

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +0.00% | 0.00% | 0.00 | 0.00 | 0.00% | +nan | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +29.71% | 5.82% | 2.39 | 5.10 | 0.35% | +nan | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +23.39% | 17.61% | 1.30 | 1.33 | 0.87% | +nan | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +4.71% | 17.18% | 0.38 | 0.27 | 0.93% | +nan | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +2.92% | 25.98% | 0.24 | 0.11 | 0.96% | +nan | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +29.64% | 36.42% | 0.94 | 0.81 | 1.27% | +nan | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +19.96% | 30.75% | 0.65 | 0.65 | 1.24% | +nan | 275/157/0 |

### P11_OOS_CLEAN_L3_FUNDB_ANCHOR

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +22.76% | 17.55% | 1.26 | 1.30 | 2.54% | -0.0159 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +30.43% | 7.32% | 1.84 | 4.16 | 1.17% | +0.0240 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +31.18% | 16.97% | 1.52 | 1.84 | 0.97% | -0.0344 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +4.28% | 22.79% | 0.33 | 0.19 | 1.05% | +0.0260 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +12.05% | 22.92% | 0.59 | 0.53 | 0.92% | +0.0262 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +34.93% | 35.15% | 1.12 | 0.99 | 1.45% | +0.0306 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +47.84% | 29.99% | 1.55 | 1.60 | 1.37% | +0.0137 | 275/157/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.