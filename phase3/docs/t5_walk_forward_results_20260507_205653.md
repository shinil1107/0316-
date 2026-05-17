# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-07T20:56:53
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +27.87 | +11.53 | +34.78 | +9.56 | +48.37 | +43.96 | +29.34 | 0.505 |
| **P8_BULL_DENSE** | +22.37 | +12.81 | +32.61 | +16.28 | +45.41 | +43.53 | +28.84 | 0.439 |
| **P9_TRIPLE_SPEC_A** | +24.20 | +10.98 | +37.24 | +19.05 | +36.10 | +41.55 | +28.19 | 0.388 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +29.34 / 14.81 / 0.50 | +19.70 / 8.17 / 0.41 | +30.90 / 16.08 / 0.52 | +43.96 / 0.00 / 0.00 | +9.56 | 6/6 |
| **P8_BULL_DENSE** | +28.84 / 12.66 / 0.44 | +17.59 / 4.78 / 0.27 | +31.43 / 11.92 / 0.38 | +43.53 / 0.00 / 0.00 | +12.81 | 6/6 |
| **P9_TRIPLE_SPEC_A** | +28.19 / 10.94 / 0.39 | +17.59 / 6.61 / 0.38 | +30.80 / 8.32 / 0.27 | +41.55 / 0.00 / 0.00 | +10.98 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **P8_BULL_DENSE** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **P9_TRIPLE_SPEC_A** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |

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
| F0a | pre_oos | 2012-01-01→2014-12-31 | +27.87% | 17.02% | 1.38 | 1.64 | 2.53% | -0.0069 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.53% | 26.83% | 0.68 | 0.43 | 1.17% | +0.0175 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +34.78% | 39.13% | 1.20 | 0.89 | 1.47% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +9.56% | 27.24% | 0.48 | 0.35 | 0.97% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +48.37% | 15.29% | 1.88 | 3.16 | 0.95% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +43.96% | 28.84% | 1.37 | 1.52 | 1.17% | +0.0244 | 275/157/0 |

### P8_BULL_DENSE

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +22.37% | 19.34% | 1.16 | 1.16 | 2.37% | +0.0125 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.81% | 26.85% | 0.77 | 0.48 | 1.17% | +0.0131 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +32.61% | 35.34% | 1.16 | 0.92 | 1.46% | +0.0204 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +16.28% | 25.87% | 0.69 | 0.63 | 1.10% | +0.0019 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +45.41% | 14.30% | 1.86 | 3.17 | 0.95% | +0.0327 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +43.53% | 31.48% | 1.36 | 1.38 | 1.23% | +0.0319 | 275/157/0 |

### P9_TRIPLE_SPEC_A

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +24.20% | 19.25% | 1.26 | 1.26 | 2.32% | +0.0035 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +10.98% | 24.37% | 0.69 | 0.45 | 1.23% | -0.0121 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +37.24% | 34.50% | 1.29 | 1.08 | 1.39% | +0.0006 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +19.05% | 27.45% | 0.82 | 0.69 | 1.17% | -0.0262 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +36.10% | 13.82% | 1.56 | 2.61 | 0.83% | +0.0245 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +41.55% | 28.58% | 1.38 | 1.45 | 1.10% | +0.0425 | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)