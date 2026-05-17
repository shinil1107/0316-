# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-07T03:29:48
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +12.13 | +45.20 | +14.76 | +43.92 | +45.92 | +31.09 | 0.465 |
| **P9_TRIPLE_SPEC_A** | +23.46 | +11.61 | +38.09 | +23.22 | +34.74 | +50.53 | +30.27 | 0.413 |
| **P9_BAL_SIDE_B** | +21.28 | +10.03 | +39.31 | +16.05 | +34.43 | +47.21 | +28.05 | 0.471 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.09 / 14.44 / 0.46 | +18.38 / 6.25 / 0.34 | +34.63 / 14.06 / 0.41 | +45.92 / 0.00 / 0.00 | +12.13 | 6/6 |
| **P9_TRIPLE_SPEC_A** | +30.27 / 12.49 / 0.41 | +17.53 / 5.92 / 0.34 | +32.02 / 6.37 / 0.20 | +50.53 / 0.00 / 0.00 | +11.61 | 6/6 |
| **P9_BAL_SIDE_B** | +28.05 / 13.22 / 0.47 | +15.66 / 5.62 / 0.36 | +29.93 / 10.01 / 0.33 | +47.21 / 0.00 / 0.00 | +10.03 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **P9_TRIPLE_SPEC_A** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |
| **P9_BAL_SIDE_B** | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | — | **✗ FAIL** |

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
| F0a | pre_oos | 2012-01-01→2014-12-31 | +24.62% | 19.74% | 1.25 | 1.25 | 2.37% | -0.0069 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.13% | 23.96% | 0.73 | 0.51 | 1.21% | +0.0175 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +45.20% | 38.19% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P9_TRIPLE_SPEC_A

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +23.46% | 18.96% | 1.23 | 1.24 | 2.29% | +0.0035 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.61% | 23.43% | 0.73 | 0.50 | 1.29% | -0.0121 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +38.09% | 34.56% | 1.32 | 1.10 | 1.44% | +0.0006 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +23.22% | 23.63% | 0.92 | 0.98 | 1.44% | -0.0262 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +34.74% | 13.68% | 1.59 | 2.54 | 0.92% | +0.0245 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +50.53% | 31.78% | 1.50 | 1.59 | 1.20% | +0.0425 | 275/157/0 |

### P9_BAL_SIDE_B

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +21.28% | 23.00% | 1.13 | 0.93 | 2.06% | +0.0125 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +10.03% | 25.88% | 0.63 | 0.39 | 1.14% | -0.0192 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +39.31% | 35.20% | 1.34 | 1.12 | 1.38% | +0.0081 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +16.05% | 29.57% | 0.71 | 0.54 | 1.41% | -0.0276 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +34.43% | 13.28% | 1.55 | 2.59 | 0.83% | +0.0356 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +47.21% | 31.33% | 1.42 | 1.51 | 1.11% | +0.0446 | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)