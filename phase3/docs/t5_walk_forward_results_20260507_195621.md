# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-07T19:56:21
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 4
**Total sims**: 24

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +26.27 | +12.20 | +41.03 | +15.42 | +45.11 | +46.90 | +31.15 | 0.448 |
| **P10_CROSS_ERA_EQ** | +17.35 | +9.50 | +28.49 | +4.25 | +42.88 | +44.43 | +24.48 | 0.632 |
| **P10_CROSS_ERA_V2H** | +17.76 | +9.26 | +27.42 | +8.17 | +41.53 | +43.07 | +24.53 | 0.573 |
| **P10_CROSS_ERA_FULL** | +18.33 | +9.88 | +29.44 | +6.07 | +41.25 | +48.32 | +25.55 | 0.610 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.15 / 13.97 / 0.45 | +19.24 / 7.04 / 0.37 | +33.85 / 13.14 / 0.39 | +46.90 / 0.00 / 0.00 | +12.20 | 6/6 |
| **P10_CROSS_ERA_EQ** | +24.48 / 15.47 / 0.63 | +13.43 / 3.92 / 0.29 | +25.20 / 15.94 / 0.63 | +44.43 / 0.00 / 0.00 | +4.25 | 6/6 |
| **P10_CROSS_ERA_V2H** | +24.53 / 14.07 / 0.57 | +13.51 / 4.25 / 0.31 | +25.71 / 13.67 / 0.53 | +43.07 / 0.00 / 0.00 | +8.17 | 6/6 |
| **P10_CROSS_ERA_FULL** | +25.55 / 15.58 / 0.61 | +14.10 / 4.22 / 0.30 | +25.59 / 14.62 / 0.57 | +48.32 / 0.00 / 0.00 | +6.07 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **P10_CROSS_ERA_EQ** | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ | — | **✗ FAIL** |
| **P10_CROSS_ERA_V2H** | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ | — | **✗ FAIL** |
| **P10_CROSS_ERA_FULL** | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | — | **✗ FAIL** |

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
| F0a | pre_oos | 2012-01-01→2014-12-31 | +26.27% | 17.63% | 1.33 | 1.49 | 2.43% | -0.0069 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.20% | 24.10% | 0.73 | 0.51 | 1.20% | +0.0175 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +41.03% | 37.04% | 1.38 | 1.11 | 1.50% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +15.42% | 21.63% | 0.68 | 0.71 | 1.22% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +45.11% | 13.55% | 1.87 | 3.33 | 1.04% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +46.90% | 31.05% | 1.42 | 1.51 | 1.13% | +0.0244 | 275/157/0 |

### P10_CROSS_ERA_EQ

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +17.35% | 20.30% | 1.01 | 0.85 | 2.16% | +0.0117 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.50% | 23.70% | 0.64 | 0.40 | 1.13% | +0.0110 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +28.49% | 36.70% | 1.05 | 0.78 | 1.31% | +0.0070 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +4.25% | 30.31% | 0.29 | 0.14 | 1.13% | -0.0163 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +42.88% | 12.36% | 1.99 | 3.47 | 0.87% | +0.0403 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +44.43% | 29.90% | 1.42 | 1.49 | 1.05% | +0.0319 | 275/157/0 |

### P10_CROSS_ERA_V2H

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +17.76% | 20.69% | 1.04 | 0.86 | 2.19% | +0.0095 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.26% | 23.39% | 0.63 | 0.40 | 1.13% | +0.0159 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +27.42% | 36.08% | 1.03 | 0.76 | 1.32% | +0.0043 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +8.17% | 26.96% | 0.44 | 0.30 | 1.11% | -0.0184 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +41.53% | 12.11% | 1.96 | 3.43 | 0.88% | +0.0386 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +43.07% | 29.40% | 1.42 | 1.47 | 1.09% | +0.0290 | 275/157/0 |

### P10_CROSS_ERA_FULL

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +18.33% | 20.37% | 1.06 | 0.90 | 2.18% | +0.0116 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.88% | 22.88% | 0.67 | 0.43 | 1.14% | +0.0083 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +29.44% | 36.09% | 1.08 | 0.82 | 1.33% | +0.0069 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +6.07% | 27.04% | 0.37 | 0.22 | 1.12% | -0.0138 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +41.25% | 11.93% | 1.93 | 3.46 | 0.89% | +0.0385 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +48.32% | 29.28% | 1.52 | 1.65 | 1.08% | +0.0321 | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)