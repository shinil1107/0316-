# T5 Walk-Forward Results — `rolling` fold-set

**Generated**: 2026-05-05T01:30:16
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-03-31.npz`
**Fold-set**: `rolling`  |  **Folds**: 7  |  **Signals**: 2
**Total sims**: 14

## 1. Per-fold CAGR (%)

| Signal | R1<br/>(oos) | R2<br/>(oos) | R3<br/>(oos) | R4<br/>(oos) | R5<br/>(oos) | R6<br/>(oos) | R7<br/>(oos) | mean | CV |
|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | +33.95 | +4.13 | +33.16 | +16.03 | +61.30 | +3.50 | +43.94 | +28.00 | 0.706 |
| **P8_SIDE_V3** | +28.96 | +3.76 | +28.31 | +12.27 | +63.60 | -1.99 | +49.52 | +26.35 | 0.842 |

## 2. CAGR aggregate by fold group (%)

| Signal | All (mean / std / CV) | oos (mean/std/CV) | Worst | Pos/n |
|---|---|---|---|---|
| **Baseline_V2** | +28.00 / 19.78 / 0.71 | +28.00 / 19.78 / 0.71 | +3.50 | 7/7 |
| **P8_SIDE_V3** | +26.35 / 22.19 / 0.84 | +26.35 / 22.19 / 0.84 | -1.99 | 6/7 |

## 3. Gate verdicts (vs baseline V2)

| Signal | G-A<br/>CV≤0.5 | G-B<br/>CV≤base+5pp | G-C<br/>worst≥0 | G-D<br/>all>0 | G-E<br/>CAGR≥90% | G-F<br/>MDD≤110% | G-G<br/>Sharpe≥90% | G-H<br/>OOS std | **HARD** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Baseline_V2** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **✗ FAIL** |
| **P8_SIDE_V3** | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ | **✗ FAIL** |

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
| R1 | oos | 2012-01-01→2013-12-31 | +33.95% | 15.92% | 1.66 | 2.13 | 1.34% | +0.0137 | 392/110/0 |
| R2 | oos | 2014-01-01→2015-12-31 | +4.13% | 19.55% | 0.31 | 0.21 | 1.20% | +0.0027 | 417/86/0 |
| R3 | oos | 2016-01-01→2017-12-31 | +33.16% | 10.09% | 1.74 | 3.29 | 1.59% | +0.0280 | 442/61/0 |
| R4 | oos | 2018-01-01→2019-12-31 | +16.03% | 30.33% | 0.81 | 0.53 | 1.34% | +0.0020 | 362/138/0 |
| R5 | oos | 2020-01-01→2021-12-31 | +61.30% | 37.53% | 1.64 | 1.63 | 1.64% | -0.0003 | 135/288/0 |
| R6 | oos | 2022-01-01→2023-12-31 | +3.50% | 22.55% | 0.27 | 0.15 | 0.92% | +0.0171 | 166/304/0 |
| R7 | oos | 2024-01-01→2026-02-27 | +43.94% | 30.77% | 1.39 | 1.43 | 1.47% | +0.0114 | 382/158/0 |

### P8_SIDE_V3

| Fold | Group | Window | CAGR | MDD | Sharpe | Calmar | Comm% | IC_3M | Regime (B/S/D) |
|---|---|---|---|---|---|---|---|---|---|
| R1 | oos | 2012-01-01→2013-12-31 | +28.96% | 15.36% | 1.53 | 1.89 | 1.20% | +0.0376 | 392/110/0 |
| R2 | oos | 2014-01-01→2015-12-31 | +3.76% | 24.21% | 0.29 | 0.16 | 1.12% | +0.0255 | 417/86/0 |
| R3 | oos | 2016-01-01→2017-12-31 | +28.31% | 9.16% | 1.68 | 3.09 | 1.37% | -0.0174 | 442/61/0 |
| R4 | oos | 2018-01-01→2019-12-31 | +12.27% | 31.00% | 0.65 | 0.40 | 1.24% | +0.0024 | 362/138/0 |
| R5 | oos | 2020-01-01→2021-12-31 | +63.60% | 37.90% | 1.65 | 1.68 | 1.71% | -0.0232 | 135/288/0 |
| R6 | oos | 2022-01-01→2023-12-31 | -1.99% | 25.22% | 0.02 | -0.08 | 0.88% | +0.0180 | 166/304/0 |
| R7 | oos | 2024-01-01→2026-02-27 | +49.52% | 31.18% | 1.48 | 1.59 | 1.48% | +0.0332 | 382/158/0 |

---

**Interpretation notes**

- **Rolling 2-year windows** test whether performance is period-dependent.
  A signal with high CV across rolling windows has unstable temporal alpha.
- All windows are OOS with respect to the typical 2019-2024 training period.