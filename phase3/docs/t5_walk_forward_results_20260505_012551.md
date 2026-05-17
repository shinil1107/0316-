# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-05T01:25:51
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 2
**Total sims**: 12

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +12.13 | +45.17 | +14.76 | +43.92 | +45.92 | +31.09 | 0.464 |
| **P8_SIDE_V3** | +21.43 | +9.47 | +38.79 | +12.60 | +35.87 | +47.95 | +27.69 | 0.511 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.09 / 14.44 / 0.46 | +18.38 / 6.25 / 0.34 | +34.62 / 14.05 / 0.41 | +45.92 / 0.00 / 0.00 | +12.13 | 6/6 |
| **P8_SIDE_V3** | +27.69 / 14.14 / 0.51 | +15.45 / 5.98 / 0.39 | +29.09 / 11.72 / 0.40 | +47.95 / 0.00 / 0.00 | +9.47 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤0.5 | G-B<br/>CV≤base+5pp | G-C<br/>worst≥0 | G-D<br/>all>0 | G-E<br/>CAGR≥90% | G-F<br/>MDD≤110% | G-G<br/>Sharpe≥90% | G-H<br/>OOS std | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **✓ ALL** |
| **P8_SIDE_V3** | ✗ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✗ | **✗ FAIL** |

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
| F0a | pre_oos | 2012-01-01→2014-12-31 | +24.62% | 19.74% | 1.25 | 1.25 | 2.37% | -0.0069 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.13% | 23.96% | 0.73 | 0.51 | 1.21% | +0.0175 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +45.17% | 38.21% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P8_SIDE_V3

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +21.43% | 23.25% | 1.13 | 0.92 | 2.04% | +0.0142 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.47% | 25.48% | 0.60 | 0.37 | 1.14% | -0.0195 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +38.79% | 35.02% | 1.32 | 1.11 | 1.35% | +0.0097 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +12.60% | 32.26% | 0.59 | 0.39 | 1.36% | -0.0277 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +35.87% | 13.31% | 1.61 | 2.70 | 0.82% | +0.0371 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +47.95% | 31.14% | 1.43 | 1.54 | 1.10% | +0.0449 | 275/157/0 |

---

**Interpretation notes**

- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).
  CAGR absolute values may be survivorship-biased (delisted names absent from cache);
  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.
- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.
- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.