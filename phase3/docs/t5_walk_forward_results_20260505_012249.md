# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-05T01:22:49
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 7  |  **Signals**: 2
**Total sims**: 14

## 1. Per-fold CAGR (%)

| Signal | BULL_1<br/>(bull_dom) | BULL_2<br/>(bull_dom) | BULL_3<br/>(bull_dom) | SIDE_1<br/>(side_dom) | SIDE_2<br/>(side_dom) | MIX_1<br/>(mixed) | MIX_2<br/>(mixed) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +34.34 | +43.92 | +1.67 | +14.76 | +45.16 | +45.92 | +30.06 | 0.527 |
| **P8_SIDE_V3** | +21.43 | +31.60 | +35.87 | +3.52 | +12.58 | +38.79 | +47.95 | +27.39 | 0.529 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | mixed (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +30.06 / 15.85 / 0.53 | +34.29 / 7.88 / 0.23 | +8.22 / 6.54 / 0.80 | +45.54 / 0.38 / 0.01 | +1.67 | 7/7 |
| **P8_SIDE_V3** | +27.39 / 14.48 / 0.53 | +29.63 / 6.06 / 0.20 | +8.05 / 4.53 / 0.56 | +43.37 / 4.58 / 0.11 | +3.52 | 7/7 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤0.5 | G-B<br/>CV≤base+5pp | G-C<br/>worst≥0 | G-D<br/>all>0 | G-E<br/>CAGR≥90% | G-F<br/>MDD≤110% | G-G<br/>Sharpe≥90% | G-H<br/>OOS std | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✗ FAIL** |
| **P8_SIDE_V3** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✗ FAIL** |

### Gate definitions

| Gate | Type | Rule |
|---|---|---|
| G-A | Hard | CV(CAGR across all folds) ≤ 0.50 |
| G-B | Hard | CV(cand) ≤ CV(baseline) + 0.05 |
| G-C | Hard | worst-fold CAGR ≥ 0 (no negative folds) |
| G-D | Hard | every fold CAGR > 0 |
| G-E | Hard | mean_CAGR(cand) ≥ 90% of mean_CAGR(baseline) |
| G-F | Soft | worst MDD(cand) ≤ 110% of worst MDD(baseline) |
| G-G | Soft | mean Sharpe(cand) ≥ 90% of mean Sharpe(baseline) |
| G-H | Soft | OOS CAGR std(cand) ≤ OOS CAGR std(baseline) |

## 4. Per-fold detail

### Baseline_V2

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +24.62% | 19.74% | 1.25 | 1.25 | 2.37% | -0.0069 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +34.34% | 8.98% | 1.85 | 3.83 | 1.15% | +0.0257 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +1.67% | 23.96% | 0.18 | 0.07 | 0.93% | +0.0446 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +45.16% | 38.21% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P8_SIDE_V3

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_1 | bull_dom | 2012-01-01→2014-12-31 | +21.43% | 23.25% | 1.13 | 0.92 | 2.04% | +0.0142 | 624/130/0 |
| BULL_2 | bull_dom | 2016-07-01→2018-01-31 | +31.60% | 9.72% | 1.65 | 3.25 | 0.97% | +0.0053 | 395/4/0 |
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +35.87% | 13.31% | 1.61 | 2.70 | 0.82% | +0.0371 | 271/94/0 |
| SIDE_1 | side_dom | 2015-01-01→2016-06-30 | +3.52% | 25.48% | 0.28 | 0.14 | 0.84% | +0.0297 | 253/123/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +12.58% | 32.33% | 0.59 | 0.39 | 1.36% | -0.0277 | 102/362/0 |
| MIX_1 | mixed | 2019-01-01→2020-12-31 | +38.79% | 35.02% | 1.32 | 1.11 | 1.35% | +0.0097 | 238/186/0 |
| MIX_2 | mixed | 2024-06-01→2026-02-27 | +47.95% | 31.14% | 1.43 | 1.54 | 1.10% | +0.0449 | 275/157/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.