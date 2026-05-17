# T5 Walk-Forward Results

**Generated**: 2026-04-28T19:53:24
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +12.13 | +45.17 | +14.76 | +43.92 | +45.92 | +31.09 | 0.464 |
| **P7_ENSEMBLE_F** | +25.55 | +9.87 | +39.49 | +26.19 | +35.12 | +46.48 | +30.45 | 0.385 |
| **P7_ENSEMBLE_G** | +24.66 | +9.99 | +37.50 | +7.60 | +43.32 | +46.97 | +28.34 | 0.546 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | Pre-OOS (F0a,F0b) | In-sample (F1-F3) | Post-OOS (F4) | Worst fold | Pos / n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.09 / 14.44 / 0.46 | +18.38 / 6.25 / 0.34 | +34.62 / 14.05 / 0.41 | +45.92 / 0.00 / 0.00 | +12.13 | 6/6 |
| **P7_ENSEMBLE_F** | +30.45 / 11.73 / 0.39 | +17.71 / 7.84 / 0.44 | +33.60 / 5.53 / 0.16 | +46.48 / 0.00 / 0.00 | +9.87 | 6/6 |
| **P7_ENSEMBLE_G** | +28.34 / 15.47 / 0.55 | +17.33 / 7.34 / 0.42 | +29.47 / 15.65 / 0.53 | +46.97 / 0.00 / 0.00 | +7.60 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G6-A (CV≤0.5) | G6-B (CV≤base) | G6-C (worst≥base) | G6-D (all>0) |
|---|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ |
| **P7_ENSEMBLE_F** | ✓ | ✓ | ✗ | ✓ |
| **P7_ENSEMBLE_G** | ✗ | ✗ | ✗ | ✓ |

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

### P7_ENSEMBLE_F

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +25.55% | 21.00% | 1.26 | 1.22 | 2.42% | -0.0111 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.87% | 23.36% | 0.62 | 0.42 | 1.32% | -0.0040 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +39.49% | 36.91% | 1.35 | 1.07 | 1.51% | -0.0207 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +26.19% | 23.93% | 1.01 | 1.09 | 1.48% | -0.0287 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +35.12% | 14.05% | 1.57 | 2.50 | 0.98% | +0.0134 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +46.48% | 30.98% | 1.43 | 1.50 | 1.20% | +0.0351 | 275/157/0 |

### P7_ENSEMBLE_G

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +24.66% | 21.06% | 1.21 | 1.17 | 2.43% | -0.0081 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.99% | 23.82% | 0.63 | 0.42 | 1.31% | -0.0128 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +37.50% | 37.92% | 1.28 | 0.99 | 1.57% | -0.0361 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +7.60% | 24.75% | 0.42 | 0.31 | 1.16% | -0.0324 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.32% | 14.01% | 1.80 | 3.09 | 1.06% | -0.0024 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +46.97% | 31.41% | 1.41 | 1.50 | 1.28% | +0.0321 | 275/157/0 |

---

**Interpretation notes**

- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).
  CAGR absolute values may be survivorship-biased (delisted names absent from cache);
  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.
- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.
- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.