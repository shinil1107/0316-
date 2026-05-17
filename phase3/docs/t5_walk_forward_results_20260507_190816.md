# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-07T19:08:16
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 4
**Total sims**: 24

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +12.13 | +45.17 | +14.76 | +43.92 | +45.92 | +31.09 | 0.464 |
| **P10_CROSS_ERA_EQ** | +17.17 | +9.60 | +29.81 | +3.09 | +40.23 | +43.73 | +23.94 | 0.633 |
| **P10_CROSS_ERA_V2H** | +17.80 | +9.12 | +28.39 | +8.31 | +39.41 | +41.04 | +24.01 | 0.552 |
| **P10_CROSS_ERA_FULL** | +18.13 | +9.73 | +31.44 | +4.59 | +40.00 | +47.07 | +25.16 | 0.618 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.09 / 14.44 / 0.46 | +18.38 / 6.25 / 0.34 | +34.62 / 14.05 / 0.41 | +45.92 / 0.00 / 0.00 | +12.13 | 6/6 |
| **P10_CROSS_ERA_EQ** | +23.94 / 15.15 / 0.63 | +13.39 / 3.79 / 0.28 | +24.38 / 15.64 / 0.64 | +43.73 / 0.00 / 0.00 | +3.09 | 6/6 |
| **P10_CROSS_ERA_V2H** | +24.01 / 13.25 / 0.55 | +13.46 / 4.34 / 0.32 | +25.37 / 12.88 / 0.51 | +41.04 / 0.00 / 0.00 | +8.31 | 6/6 |
| **P10_CROSS_ERA_FULL** | +25.16 / 15.55 / 0.62 | +13.93 / 4.20 / 0.30 | +25.34 / 15.08 / 0.60 | +47.07 / 0.00 / 0.00 | +4.59 | 6/6 |

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
| F0a | pre_oos | 2012-01-01→2014-12-31 | +24.62% | 19.74% | 1.25 | 1.25 | 2.37% | -0.0069 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.13% | 23.96% | 0.73 | 0.51 | 1.21% | +0.0175 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +45.17% | 38.21% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### P10_CROSS_ERA_EQ

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +17.17% | 20.09% | 1.00 | 0.85 | 2.16% | +0.0117 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.60% | 23.48% | 0.65 | 0.41 | 1.15% | +0.0110 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +29.81% | 36.57% | 1.09 | 0.82 | 1.31% | +0.0070 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +3.09% | 31.23% | 0.25 | 0.10 | 1.15% | -0.0163 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +40.23% | 12.83% | 1.84 | 3.14 | 0.87% | +0.0403 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +43.73% | 29.47% | 1.41 | 1.48 | 1.05% | +0.0319 | 275/157/0 |

### P10_CROSS_ERA_V2H

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +17.80% | 20.53% | 1.04 | 0.87 | 2.18% | +0.0095 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.12% | 23.44% | 0.62 | 0.39 | 1.14% | +0.0159 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +28.39% | 35.98% | 1.06 | 0.79 | 1.31% | +0.0043 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +8.31% | 26.89% | 0.45 | 0.31 | 1.12% | -0.0184 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +39.41% | 12.39% | 1.86 | 3.18 | 0.86% | +0.0386 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +41.04% | 29.05% | 1.39 | 1.41 | 1.10% | +0.0290 | 275/157/0 |

### P10_CROSS_ERA_FULL

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +18.13% | 20.63% | 1.05 | 0.88 | 2.18% | +0.0116 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +9.73% | 22.86% | 0.66 | 0.43 | 1.15% | +0.0083 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +31.44% | 35.86% | 1.14 | 0.88 | 1.33% | +0.0069 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +4.59% | 27.66% | 0.31 | 0.17 | 1.16% | -0.0138 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +40.00% | 12.38% | 1.86 | 3.23 | 0.88% | +0.0385 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +47.07% | 29.07% | 1.50 | 1.62 | 1.09% | +0.0321 | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)