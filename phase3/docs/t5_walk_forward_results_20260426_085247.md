# T5 Walk-Forward Results

**Generated**: 2026-04-26T08:52:47
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +28.89 | +10.45 | +39.31 | +4.30 | +49.75 | +50.91 | +30.60 | 0.590 |
| **P6_ENSEMBLE_C** | +28.76 | +11.49 | +35.57 | +14.80 | +35.06 | +44.53 | +28.37 | 0.414 |
| **P6_ENSEMBLE_D** | +28.16 | +11.54 | +37.74 | +13.98 | +31.80 | +48.51 | +28.62 | 0.450 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | Pre-OOS (F0a,F0b) | In-sample (F1-F3) | Post-OOS (F4) | Worst fold | Pos / n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +30.60 / 18.06 / 0.59 | +19.67 / 9.22 / 0.47 | +31.12 / 19.44 / 0.62 | +50.91 / 0.00 / 0.00 | +4.30 | 6/6 |
| **P6_ENSEMBLE_C** | +28.37 / 11.74 / 0.41 | +20.13 / 8.64 / 0.43 | +28.48 / 9.67 / 0.34 | +44.53 / 0.00 / 0.00 | +11.49 | 6/6 |
| **P6_ENSEMBLE_D** | +28.62 / 12.88 / 0.45 | +19.85 / 8.31 / 0.42 | +27.84 / 10.09 / 0.36 | +48.51 / 0.00 / 0.00 | +11.54 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G6-A (CV≤0.5) | G6-B (CV≤base) | G6-C (worst≥base) | G6-D (all>0) |
|---|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✗ | ✓ | ✓ | ✓ |
| **P6_ENSEMBLE_C** | ✓ | ✓ | ✓ | ✓ |
| **P6_ENSEMBLE_D** | ✓ | ✓ | ✓ | ✓ |

## 4. Per-fold detail

### Baseline_V2

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +28.89% | 14.25% | 1.64 | 2.03 | 2.25% | -0.0073 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +10.45% | 24.34% | 0.67 | 0.43 | 1.15% | +0.0197 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +39.31% | 35.15% | 1.28 | 1.12 | 1.21% | -0.0044 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +4.30% | 23.73% | 0.30 | 0.18 | 0.94% | -0.0091 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +49.75% | 13.33% | 2.08 | 3.73 | 0.92% | +0.0211 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +50.91% | 30.29% | 1.52 | 1.68 | 0.95% | +0.0214 | 275/157/0 |

### P6_ENSEMBLE_C

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +28.76% | 14.36% | 1.64 | 2.00 | 2.20% | -0.0242 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.49% | 20.24% | 0.75 | 0.57 | 1.34% | +0.0014 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +35.57% | 35.90% | 1.20 | 0.99 | 1.46% | +0.0089 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +14.80% | 22.08% | 0.73 | 0.67 | 0.73% | +0.0313 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +35.06% | 13.74% | 1.62 | 2.55 | 0.89% | -0.0460 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +44.53% | 31.22% | 1.41 | 1.43 | 1.03% | +0.0086 | 275/157/0 |

### P6_ENSEMBLE_D

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +28.16% | 19.81% | 1.50 | 1.42 | 2.17% | -0.0183 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.54% | 19.51% | 0.76 | 0.59 | 1.38% | +0.0026 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +37.74% | 35.53% | 1.23 | 1.06 | 1.58% | +0.0099 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +13.98% | 21.00% | 0.68 | 0.67 | 0.71% | +0.0162 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +31.80% | 14.75% | 1.51 | 2.16 | 0.93% | -0.0539 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +48.51% | 31.33% | 1.49 | 1.55 | 1.17% | +0.0104 | 275/157/0 |

---

**Interpretation notes**

- **Pre-OOS (F0a, F0b)** is true out-of-sample (signal never saw 2011-2016 during training).
  CAGR absolute values may be survivorship-biased (delisted names absent from cache);
  **relative** gate G6-B / G6-C remains valid because all signals share the same universe.
- **In-sample (F1-F3)** folds represent regime-conditional audits of training data.
- **Post-OOS (F4)** matches the Step C window — cross-check with baseline_benchmark.md.