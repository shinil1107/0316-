# T5 Walk-Forward Results — `regime` fold-set

**Generated**: 2026-05-05T01:18:38
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `regime`  |  **Folds**: 2  |  **Signals**: 2
**Total sims**: 4

## 1. Per-fold CAGR (%)

| Signal | BULL_3<br/>(bull_dom) | SIDE_2<br/>(side_dom) | mean | CV |
|---|---|---|---|---|---|
| **Baseline_V2** | +43.92 | +14.76 | +29.34 | 0.497 |
| **P7_STITCH_J** | +33.75 | +8.38 | +21.06 | 0.602 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | bull_dom (mean/std/CV) | side_dom (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|
| **Baseline_V2** | +29.34 / 14.58 / 0.50 | +43.92 / 0.00 / 0.00 | +14.76 / 0.00 / 0.00 | +14.76 | 2/2 |
| **P7_STITCH_J** | +21.06 / 12.68 / 0.60 | +33.75 / 0.00 / 0.00 | +8.38 / 0.00 / 0.00 | +8.38 | 2/2 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤0.5 | G-B<br/>CV≤base+5pp | G-C<br/>worst≥0 | G-D<br/>all>0 | G-E<br/>CAGR≥90% | G-F<br/>MDD≤110% | G-G<br/>Sharpe≥90% | G-H<br/>OOS std | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✓ ALL** |
| **P7_STITCH_J** | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **✗ FAIL** |

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
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |

### P7_STITCH_J

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| BULL_3 | bull_dom | 2023-01-01→2024-05-31 | +33.75% | 15.35% | 1.66 | 2.20 | 0.86% | +0.0503 | 271/94/0 |
| SIDE_2 | side_dom | 2021-01-01→2022-12-31 | +8.38% | 26.89% | 0.44 | 0.31 | 1.21% | -0.0011 | 102/362/0 |

---

**Interpretation notes**

- **BULL-dominant** folds (BULL_1-3) isolate uptrend-driven performance.
- **SIDE-dominant** folds (SIDE_1-2) stress-test lateral/range-bound market behavior.
- **Mixed** folds capture transition periods and post-train conditions.
- A production-worthy signal should show positive CAGR in all regime groups.