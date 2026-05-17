# T5 Walk-Forward Results

**Generated**: 2026-04-25T14:33:35
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +28.62 | +10.54 | +39.59 | +4.14 | +49.63 | +54.44 | +31.16 | 0.603 |
| **T1b_BULL_INJECTED** | +27.62 | +10.69 | +36.27 | +7.41 | +43.46 | +48.66 | +29.02 | 0.536 |
| **P6_ENSEMBLE_C** | +29.78 | +7.28 | +38.20 | +17.15 | +31.79 | +48.82 | +28.84 | 0.469 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | Pre-OOS (F0a,F0b) | In-sample (F1-F3) | Post-OOS (F4) | Worst fold | Pos / n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.16 / 18.78 / 0.60 | +19.58 / 9.04 / 0.46 | +31.12 / 19.51 / 0.63 | +54.44 / 0.00 / 0.00 | +4.14 | 6/6 |
| **T1b_BULL_INJECTED** | +29.02 / 15.56 / 0.54 | +19.15 / 8.46 / 0.44 | +29.05 / 15.58 / 0.54 | +48.66 / 0.00 / 0.00 | +7.41 | 6/6 |
| **P6_ENSEMBLE_C** | +28.84 / 13.53 / 0.47 | +18.53 / 11.25 / 0.61 | +29.05 / 8.81 / 0.30 | +48.82 / 0.00 / 0.00 | +7.28 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G6-A (CV≤0.5) | G6-B (CV≤base) | G6-C (worst≥base) | G6-D (all>0) |
|---|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✗ | ✓ | ✓ | ✓ |
| **T1b_BULL_INJECTED** | ✗ | ✓ | ✓ | ✓ |
| **P6_ENSEMBLE_C** | ✓ | ✓ | ✓ | ✓ |

## 4. Per-fold detail

### Baseline_V2

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +28.62% | 14.90% | 1.63 | 1.92 | 1.97% | -0.0073 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +10.54% | 25.51% | 0.66 | 0.41 | 1.03% | +0.0197 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +39.59% | 34.73% | 1.27 | 1.14 | 0.96% | -0.0044 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +4.14% | 26.69% | 0.29 | 0.15 | 0.62% | -0.0091 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +49.63% | 14.64% | 2.02 | 3.39 | 0.81% | +0.0211 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +54.44% | 30.03% | 1.59 | 1.81 | 0.83% | +0.0214 | 275/157/0 |

### T1b_BULL_INJECTED

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +27.62% | 14.71% | 1.56 | 1.88 | 2.00% | -0.0067 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +10.69% | 25.03% | 0.68 | 0.43 | 1.08% | +0.0155 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +36.27% | 36.05% | 1.20 | 1.01 | 1.08% | -0.0178 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +7.41% | 26.61% | 0.42 | 0.28 | 0.76% | -0.0012 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.46% | 15.01% | 1.89 | 2.90 | 0.83% | +0.0219 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +48.66% | 29.92% | 1.49 | 1.63 | 0.82% | +0.0209 | 275/157/0 |

### P6_ENSEMBLE_C

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +29.78% | 15.02% | 1.69 | 1.98 | 1.86% | -0.0242 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +7.28% | 23.91% | 0.51 | 0.30 | 1.16% | +0.0014 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +38.20% | 35.58% | 1.25 | 1.07 | 1.24% | +0.0089 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +17.15% | 21.82% | 0.81 | 0.79 | 0.50% | +0.0313 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +31.79% | 14.52% | 1.50 | 2.19 | 0.80% | -0.0460 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +48.82% | 30.71% | 1.51 | 1.59 | 0.91% | +0.0086 | 275/157/0 |

---

**Interpretation notes**

- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).
  CAGR absolute values may be survivorship-biased (delisted names absent from cache);
  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.
- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.
- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.