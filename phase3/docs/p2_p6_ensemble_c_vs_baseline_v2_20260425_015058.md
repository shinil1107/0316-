# P6_ENSEMBLE_A vs Baseline_V2 — Regime Breakdown A/B

**Generated**: 2026-04-25T01:50:58
**Window**: 2012-01-01 → 2026-02-27
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Protocol**: $100K init · $1K/day buy limit · 10/5 bps cost · SIDE_DEF_p12 · blend OFF · daily rebal

## 1. Headline metrics (A = ENSEMBLE, B = Baseline)

| Metric | ENSEMBLE_A | Baseline_V2 | Δ (A − B) |
|---|---:|---:|---:|
| CAGR                   | +28.18% | +30.43% | -2.24% ↓ |
| Total Return           | +3234.10% | +4159.93% | -925.83% ↓ |
| Net Sharpe             | +1.210 | +1.247 | -0.037 ↓ |
| Max Drawdown           | 35.25% | 34.23% | +1.02% ↓ |
| Calmar                 | +0.800 | +0.889 | -0.089 ↓ |
| Daily Win-rate         | 56.84% | 56.76% | +0.08% ↑ |
| Monthly Win-rate       | 68.05% | 69.23% | -1.18% ↓ |
| Commission %           | 41.29% | 42.34% | -1.05% ↑ |
| Final $                | 3,334,102 | 4,259,931 | -925,829 ↓ |

## 2. Regime breakdown (ENSEMBLE_A)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +35.65% | +1.790 | 14.91% | +2.392 | 57.76% |
| SIDE | 1136 | 118 | +19.29% | +0.687 | 22.09% | +0.873 | 55.37% |
| DEF | 141 | 58 | +75.07% | +1.032 | 29.83% | +2.517 | 53.90% |

## 3. Regime breakdown (Baseline_V2)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +39.11% | +1.826 | 14.44% | +2.709 | 58.06% |
| SIDE | 1136 | 118 | +19.24% | +0.659 | 23.63% | +0.814 | 54.31% |
| DEF | 141 | 58 | +90.99% | +1.187 | 28.49% | +3.193 | 55.32% |

## 4. Regime delta (ENSEMBLE_A − Baseline_V2)

| Regime | ΔAnnRet | ΔSharpe | ΔMDD | ΔCalmar | ΔWinRate |
|---|---:|---:|---:|---:|---:|
| BULL | -3.46% | -0.036 | +0.47% | -0.317 | -0.30% |
| SIDE | +0.05% | +0.027 | -1.54% | +0.059 | +1.06% |
| DEF | -15.92% | -0.155 | +1.34% | -0.676 | -1.42% |

---

**Reading guide**

- **§2 / §3 DEF row** answers the key question: does ENSEMBLE_A's DEF_HEAVY wd
  actually deliver better tail-defense than Baseline_V2's DEF slot?
- **§2 SIDE row** validates the F2 walk-forward finding at full-period scale.
- **§4 deltas**: positive numbers = ENSEMBLE_A is better (for AnnRet/Sharpe/Calmar/WinRate)
  and negative numbers = ENSEMBLE_A is better (for MDD).
- Headline CAGR gap: expect ~−2 pp vs Baseline (priced-in from 6-fold).
  Net Sharpe / Calmar / MDD should favour ENSEMBLE_A → that is the trade we want.