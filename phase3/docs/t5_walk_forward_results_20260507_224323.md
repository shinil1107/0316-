# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-07T22:43:23
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 3
**Total sims**: 18

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +24.62 | +12.13 | +45.16 | +14.76 | +43.92 | +45.92 | +31.09 | 0.464 |
| **ML_XGB_v1** | +0.00 | +11.31 | +27.37 | +1.11 | +32.63 | +20.92 | +15.55 | 0.800 |
| **ML_XGB_v15** | +13.52 | +11.40 | +29.60 | +7.71 | +23.40 | +19.35 | +17.50 | 0.426 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **Baseline_V2** | +31.09 / 14.44 / 0.46 | +18.38 / 6.25 / 0.34 | +34.61 / 14.05 / 0.41 | +45.92 / 0.00 / 0.00 | +12.13 | 6/6 |
| **ML_XGB_v1** | +15.55 / 12.44 / 0.80 | +5.65 / 5.65 / 1.00 | +20.37 / 13.79 / 0.68 | +20.92 / 0.00 / 0.00 | +0.00 | 5/6 |
| **ML_XGB_v15** | +17.50 / 7.45 / 0.43 | +12.46 / 1.06 / 0.08 | +20.24 / 9.21 / 0.46 | +19.35 / 0.00 / 0.00 | +7.71 | 6/6 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **ML_XGB_v1** | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | — | **✗ FAIL** |
| **ML_XGB_v15** | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ | — | **✗ FAIL** |

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
| F1 | in_sample | 2019-01-01→2020-12-31 | +45.16% | 38.21% | 1.45 | 1.18 | 1.44% | -0.0021 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +14.76% | 21.82% | 0.66 | 0.68 | 1.25% | -0.0088 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +43.92% | 13.98% | 1.85 | 3.14 | 1.03% | +0.0231 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +45.92% | 30.99% | 1.41 | 1.48 | 1.15% | +0.0244 | 275/157/0 |

### ML_XGB_v1

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +0.00% | 0.00% | 0.00 | 0.00 | 0.00% | +nan | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.31% | 23.31% | 0.70 | 0.49 | 1.28% | +nan | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +27.37% | 34.64% | 1.02 | 0.79 | 1.10% | +nan | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +1.11% | 24.69% | 0.16 | 0.04 | 1.00% | +nan | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +32.63% | 16.27% | 1.82 | 2.00 | 1.06% | +nan | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +20.92% | 38.39% | 0.66 | 0.54 | 1.42% | +nan | 275/157/0 |

### ML_XGB_v15

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +13.52% | 7.59% | 1.47 | 1.78 | 1.13% | +nan | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.40% | 28.65% | 0.67 | 0.40 | 1.11% | +nan | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +29.60% | 38.70% | 0.93 | 0.76 | 1.07% | +nan | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +7.71% | 26.17% | 0.41 | 0.29 | 0.93% | +nan | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +23.40% | 18.76% | 1.25 | 1.25 | 0.88% | +nan | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +19.35% | 35.36% | 0.64 | 0.55 | 1.27% | +nan | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)