# P6_ENSEMBLE_A vs Baseline_V2 — Regime Breakdown A/B

**Generated**: 2026-04-25T01:38:30
**Window**: 2012-01-01 → 2026-02-27
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Protocol**: $100K init · $1K/day buy limit · 10/5 bps cost · SIDE_DEF_p12 · blend OFF · daily rebal

## 1. Headline metrics (A = ENSEMBLE, B = Baseline)

| Metric | ENSEMBLE_A | Baseline_V2 | Δ (A − B) |
|---|---:|---:|---:|
| CAGR                   | +28.08% | +30.43% | -2.35% ↓ |
| Total Return           | +3196.89% | +4159.93% | -963.04% ↓ |
| Net Sharpe             | +1.206 | +1.247 | -0.040 ↓ |
| Max Drawdown           | 35.34% | 34.23% | +1.11% ↓ |
| Calmar                 | +0.795 | +0.889 | -0.094 ↓ |
| Daily Win-rate         | 56.73% | 56.76% | -0.03% ↓ |
| Monthly Win-rate       | 68.64% | 69.23% | -0.59% ↓ |
| Commission %           | 40.66% | 42.34% | -1.68% ↑ |
| Final $                | 3,296,894 | 4,259,931 | -963,036 ↓ |

## 2. Regime breakdown (ENSEMBLE_A)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +35.68% | +1.791 | 14.22% | +2.508 | 57.62% |
| SIDE | 1136 | 118 | +18.93% | +0.675 | 21.38% | +0.885 | 55.28% |
| DEF | 141 | 58 | +75.43% | +1.035 | 29.90% | +2.523 | 53.90% |

## 3. Regime breakdown (Baseline_V2)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +39.11% | +1.826 | 14.44% | +2.709 | 58.06% |
| SIDE | 1136 | 118 | +19.24% | +0.659 | 23.63% | +0.814 | 54.31% |
| DEF | 141 | 58 | +90.99% | +1.187 | 28.49% | +3.193 | 55.32% |

## 4. Regime delta (ENSEMBLE_A − Baseline_V2)

| Regime | ΔAnnRet | ΔSharpe | ΔMDD | ΔCalmar | ΔWinRate |
|---|---:|---:|---:|---:|---:|
| BULL | -3.43% | -0.035 | -0.22% | -0.200 | -0.44% |
| SIDE | -0.31% | +0.016 | -2.25% | +0.071 | +0.97% |
| DEF | -15.56% | -0.152 | +1.41% | -0.671 | -1.42% |

---

**Reading guide**

- **§2 / §3 DEF row** answers the key question: does ENSEMBLE_A's DEF_HEAVY wd
  actually deliver better tail-defense than Baseline_V2's DEF slot?
- **§2 SIDE row** validates the F2 walk-forward finding at full-period scale.
- **§4 deltas**: positive numbers = ENSEMBLE_A is better (for AnnRet/Sharpe/Calmar/WinRate)
  and negative numbers = ENSEMBLE_A is better (for MDD).
- Headline CAGR gap: expect ~−2 pp vs Baseline (priced-in from 6-fold).
  Net Sharpe / Calmar / MDD should favour ENSEMBLE_A → that is the trade we want.