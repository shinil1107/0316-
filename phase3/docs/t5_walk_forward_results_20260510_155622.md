# T5 Walk-Forward Results — `default` fold-set

**Generated**: 2026-05-10T15:56:22
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `default`  |  **Folds**: 6  |  **Signals**: 4
**Total sims**: 24

## 1. Per-fold CAGR (%)

| Signal | F0a<br/>(pre_oos) | F0b<br/>(pre_oos) | F1<br/>(in_sample) | F2<br/>(in_sample) | F3<br/>(in_sample) | F4<br/>(post_oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|
| **P2_BATCH11_OOS** | +25.38 | +5.99 | +27.31 | +11.24 | +28.13 | +30.28 | +21.39 | 0.433 |
| **ML_XGB_v16** | +13.57 | +11.36 | +28.89 | +9.16 | +24.10 | +20.97 | +18.01 | 0.397 |
| **ML_XGB_v20** | +0.00 | +11.26 | +29.64 | +2.92 | +23.39 | +19.96 | +14.53 | 0.740 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +22.76 | +12.39 | +34.93 | +12.05 | +31.18 | +47.84 | +26.86 | 0.473 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | pre_oos (mean/std/CV) | in_sample (mean/std/CV) | post_oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|---|---|
| **P2_BATCH11_OOS** | +21.39 / 9.27 / 0.43 | +15.69 / 9.70 / 0.62 | +22.23 / 7.77 / 0.35 | +30.28 / 0.00 / 0.00 | +5.99 | 6/6 |
| **ML_XGB_v16** | +18.01 / 7.15 / 0.40 | +12.47 / 1.10 / 0.09 | +20.71 / 8.40 / 0.41 | +20.97 / 0.00 / 0.00 | +9.16 | 6/6 |
| **ML_XGB_v20** | +14.53 / 10.75 / 0.74 | +5.63 / 5.63 / 1.00 | +18.65 / 11.41 / 0.61 | +19.96 / 0.00 / 0.00 | +0.00 | 5/6 |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | +26.86 / 12.71 / 0.47 | +17.58 / 5.18 / 0.29 | +26.05 / 10.02 / 0.38 | +47.84 / 0.00 / 0.00 | +12.05 | 6/6 |

## 3. Gate verdicts (vs baseline = P2_BATCH11_OOS)

| Signal | G-A<br/>CV≤base+5pp | G-B<br/>CAGR≥90% | G-C<br/>worst≥base-1pp | G-D<br/>pos≥base | G-E<br/>MDD≤110% | G-F<br/>Sharpe≥90% | G-G<br/>OOS std+1pp | G-H<br/>Lift≥80% | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **P2_BATCH11_OOS** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | **✓ ALL** |
| **ML_XGB_v16** | ✓ | ✗ | ✓ | ✓ | ✗ | ✗ | ✓ | — | **✗ FAIL** |
| **ML_XGB_v20** | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | — | **✗ FAIL** |
| **P11_OOS_CLEAN_L3_FUNDB_ANCHOR** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | — | **✓ ALL** |

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

### P2_BATCH11_OOS

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +25.38% | 14.43% | 1.46 | 1.76 | 2.82% | -0.0232 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +5.99% | 25.84% | 0.44 | 0.23 | 1.36% | +0.0097 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +27.31% | 34.95% | 1.02 | 0.78 | 1.47% | +0.0141 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +11.24% | 28.72% | 0.53 | 0.39 | 1.39% | +0.0359 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +28.13% | 13.12% | 1.56 | 2.14 | 1.10% | +0.0165 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +30.28% | 26.58% | 1.21 | 1.14 | 1.19% | +0.0068 | 275/157/0 |

### ML_XGB_v16

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +13.57% | 7.70% | 1.47 | 1.76 | 1.16% | +nan | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.36% | 26.58% | 0.68 | 0.43 | 1.17% | +nan | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +28.89% | 39.90% | 0.94 | 0.72 | 1.19% | +nan | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +9.16% | 26.45% | 0.46 | 0.35 | 1.01% | +nan | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +24.10% | 18.52% | 1.29 | 1.30 | 0.92% | +nan | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +20.97% | 25.20% | 0.62 | 0.83 | 1.30% | +nan | 275/157/0 |

### ML_XGB_v20

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +0.00% | 0.00% | 0.00 | 0.00 | 0.00% | +nan | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +11.26% | 17.03% | 0.80 | 0.66 | 1.24% | +nan | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +29.64% | 36.42% | 0.94 | 0.81 | 1.27% | +nan | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +2.92% | 25.98% | 0.24 | 0.11 | 0.96% | +nan | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +23.39% | 17.61% | 1.30 | 1.33 | 0.87% | +nan | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +19.96% | 30.75% | 0.65 | 0.65 | 1.24% | +nan | 275/157/0 |

### P11_OOS_CLEAN_L3_FUNDB_ANCHOR

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| F0a | pre_oos | 2012-01-01→2014-12-31 | +22.76% | 17.55% | 1.26 | 1.30 | 2.54% | -0.0159 | 624/130/0 |
| F0b | pre_oos | 2015-01-01→2016-12-31 | +12.39% | 22.79% | 0.79 | 0.54 | 1.41% | +0.0010 | 376/127/0 |
| F1 | in_sample | 2019-01-01→2020-12-31 | +34.93% | 35.15% | 1.12 | 0.99 | 1.45% | +0.0306 | 238/186/0 |
| F2 | in_sample | 2021-01-01→2022-12-31 | +12.05% | 22.92% | 0.59 | 0.53 | 0.92% | +0.0262 | 102/362/0 |
| F3 | in_sample | 2023-01-01→2024-05-31 | +31.18% | 16.97% | 1.52 | 1.84 | 0.97% | -0.0344 | 271/94/0 |
| F4 | post_oos | 2024-06-01→2026-02-27 | +47.84% | 29.99% | 1.55 | 1.60 | 1.37% | +0.0137 | 275/157/0 |

---

**Interpretation notes**

- **CRITICAL**: Batch 7/8 training window = 2011-01-01 → 2026-03-31
  → **6개 fold 전부 in-sample** (F4도 2026-02까지이므로 포함)
- **F0a/F0b (early in-sample)**: GA가 덜 집중한 구간, 시간 외삽 능력 부분 검증
- **F1-F3 (core in-sample)**: GA 집중 최적화 구간 (2019-2024)
- **F4 (late in-sample)**: Training 끝부분, 최신 패턴 추종력 검증
- **진정한 OOS 검증**: Step C + live production만 가능
- **재정의된 목적**: in-sample temporal stability audit (NOT OOS validation)