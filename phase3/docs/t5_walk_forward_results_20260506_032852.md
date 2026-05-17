# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-06T03:28:52
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 7  |  **Signals**: 2
**Total sims**: 14

## 1. Per-fold CAGR (%)

| Signal | BULL_1<br/>(bull_dom) | BULL_2<br/>(bull_dom) | BULL_3<br/>(bull_dom) | SIDE_1<br/>(side_dom) | SIDE_2<br/>(side_dom) | MIX_1<br/>(mixed) | MIX_2<br/>(mixed) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +34.34 | +43.92 | +1.67 | +14.76 | +45.18 | +45.92 | +30.06 | 0.528 |
| **P9_TRIPLE_SPEC_A** | +23.46 | +29.05 | +34.74 | +5.85 | +23.22 | +38.05 | +51.10 | +29.35 | 0.447 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | mixed (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +30.06 / 15.86 / 0.53 | +34.29 / 7.88 / 0.23 | +8.22 / 6.54 / 0.80 | +45.55 / 0.37 / 0.01 | +1.67 | 7/7 |
| **P9_TRIPLE_SPEC_A** | +29.35 / 13.11 / 0.45 | +29.08 / 4.61 / 0.16 | +14.53 / 8.69 / 0.60 | +44.57 / 6.53 / 0.15 | +5.85 | 7/7 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_TRIPLE_SPEC_A** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |

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

### P9_TRIPLE_SPEC_A

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +23.46% | 18.96% | 1.23 | 1.24 | 2.29% | +0.0035 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +29.05% | 9.53% | 1.62 | 3.05 | 1.13% | +0.0092 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +34.74% | 13.68% | 1.59 | 2.54 | 0.92% | +0.0245 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +5.85% | 23.43% | 0.41 | 0.25 | 0.96% | +0.0304 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +23.22% | 23.63% | 0.92 | 0.98 | 1.44% | -0.0262 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +38.05% | 34.56% | 1.31 | 1.10 | 1.44% | +0.0006 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +51.10% | 31.61% | 1.51 | 1.62 | 1.20% | +0.0425 | 275/157/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.