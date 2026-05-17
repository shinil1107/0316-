# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-05T12:32:29
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 2  |  **Signals**: 2
**Total sims**: 4

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(sliding) | R8<br/>(sliding) | mean | CV |
|---|---|---|---|---|---|
| **Baseline_V2** | +19.91 | +36.04 | +27.97 | 0.288 |
| **P7_STITCH_K** | +17.36 | +33.24 | +25.30 | 0.314 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | sliding (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +27.97 / 8.07 / 0.29 | +27.97 / 8.07 / 0.29 | +19.91 | 2/2 |
| **P7_STITCH_K** | +25.30 / 7.94 / 0.31 | +25.30 / 7.94 / 0.31 | +17.36 | 2/2 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✓ ALL** |
| **P7_STITCH_K** | ✓ | ✓ | ✗ | ✓ | ✓ | ✗ | ✗ | **✗ FAIL** |

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

## 4. Per-fold detail

### Baseline_V2

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +19.91% | 31.46% | 0.83 | 0.63 | 10.19% | +0.0124 | 1496/435/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +36.04% | 38.25% | 1.26 | 0.94 | 14.59% | +0.0076 | 1045/888/0 |

### P7_STITCH_K

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | sliding | 2011-01-01→2018-12-31 | +17.36% | 32.36% | 0.69 | 0.54 | 9.05% | +0.0162 | 1496/435/0 |
| R8 | sliding | 2018-01-01→2026-02-27 | +33.24% | 36.14% | 1.18 | 0.92 | 11.44% | +0.0199 | 1045/888/0 |

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