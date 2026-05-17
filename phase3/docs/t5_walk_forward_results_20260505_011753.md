# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-05T01:17:53
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 1  |  **Signals**: 3
**Total sims**: 3

## 1. Per-fold CAGR (%)

| Signal | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|
| **Baseline_V2** | +45.92 | +45.92 | 0.000 |
| **P7_STITCH_J** | +43.54 | +43.54 | 0.000 |
| **P7_STITCH_K** | +47.52 | +47.52 | 0.000 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +45.92 / 0.00 / 0.00 | +45.92 / 0.00 / 0.00 | +45.92 | 1/1 |
| **P7_STITCH_J** | +43.54 / 0.00 / 0.00 | +43.54 / 0.00 / 0.00 | +43.54 | 1/1 |
| **P7_STITCH_K** | +47.52 / 0.00 / 0.00 | +47.52 / 0.00 / 0.00 | +47.52 | 1/1 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤0.5 | G-B<br/>CV≤base+5pp | G-C<br/>worst≥0 | G-D<br/>all>0 | G-E<br/>CAGR≥90% | G-F<br/>MDD≤110% | G-G<br/>Sharpe≥90% | G-H<br/>OOS std | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✓ ALL** |
| **P7_STITCH_J** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✓ ALL** |
| **P7_STITCH_K** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **✓ ALL** |

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
| F4 | post_oos | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P7_STITCH_J

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F4 | post_oos | 2024-06-01→2026-02-27 | +43.54% | 29.96% | 1.47 | 1.45 | 1.14% | +0.0373 | 275/157/0 |

### P7_STITCH_K

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F4 | post_oos | 2024-06-01→2026-02-27 | +47.52% | 30.05% | 1.45 | 1.58 | 1.09% | +0.0330 | 275/157/0 |

---

**Interpretation notes**

- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).
  CAGR absolute values may be survivorship-biased (delisted names absent from cache);
  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.
- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.
- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.