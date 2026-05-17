# P6_ENSEMBLE_A vs Baseline_V2 — Regime Breakdown A/B

**Generated**: 2026-04-25T14:20:02
**Window**: 2012-01-01 → 2026-02-27
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Protocol**: $100K init · $1K/day buy limit · 10/5 bps cost · SIDE_DEF_p12 · blend OFF · daily rebal

## 1. Headline metrics (A = ENSEMBLE, B = Baseline)

| Metric | ENSEMBLE_A | Baseline_V2 | Δ (A − B) |
|---|---:|---:|---:|
| CAGR                   | +30.43% | +30.43% | +0.00% = |
| Total Return           | +4159.93% | +4159.93% | +0.00% = |
| Net Sharpe             | +1.247 | +1.247 | +0.000 = |
| Max Drawdown           | 34.23% | 34.23% | 0.00% = |
| Calmar                 | +0.889 | +0.889 | +0.000 = |
| Daily Win-rate         | 56.76% | 56.76% | 0.00% = |
| Monthly Win-rate       | 69.23% | 69.23% | 0.00% = |
| Commission %           | 42.34% | 42.34% | 0.00% = |
| Final $                | 4,259,931 | 4,259,931 | 0 = |

## 2. Regime breakdown (ENSEMBLE_A)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +39.11% | +1.826 | 14.44% | +2.709 | 58.06% |
| SIDE | 1136 | 118 | +19.24% | +0.659 | 23.63% | +0.814 | 54.31% |
| DEF | 141 | 58 | +90.99% | +1.187 | 28.49% | +3.193 | 55.32% |

## 3. Regime breakdown (Baseline_V2)

| Regime | Days | MaxStreak | AnnRet | Sharpe | MDD | Calmar | WinRate |
|---|---:|---:|---:|---:|---:|---:|---:|
| BULL | 2282 | 310 | +39.11% | +1.826 | 14.44% | +2.709 | 58.06% |
| SIDE | 1136 | 118 | +19.24% | +0.659 | 23.63% | +0.814 | 54.31% |
| DEF | 141 | 58 | +90.99% | +1.187 | 28.49% | +3.193 | 55.32% |

## 4. Regime delta (ENSEMBLE_A − Baseline_V2)

| Regime | ΔAnnRet | ΔSharpe | ΔMDD | ΔCalmar | ΔWinRate |
|---|---:|---:|---:|---:|---:|
| BULL | +0.00% | +0.000 | +0.00% | +0.000 | +0.00% |
| SIDE | +0.00% | +0.000 | +0.00% | +0.000 | +0.00% |
| DEF | +0.00% | +0.000 | +0.00% | +0.000 | +0.00% |

---

**Reading guide**

- **§2 / §3 DEF row** answers the key question: does ENSEMBLE_A's DEF_HEAVY wd
  actually deliver better tail-defense than Baseline_V2's DEF slot?
- **§2 SIDE row** validates the F2 walk-forward finding at full-period scale.
- **§4 deltas**: positive numbers = ENSEMBLE_A is better (for AnnRet/Sharpe/Calmar/WinRate)
  and negative numbers = ENSEMBLE_A is better (for MDD).
- Headline CAGR gap: expect ~−2 pp vs Baseline (priced-in from 6-fold).
  Net Sharpe / Calmar / MDD should favour ENSEMBLE_A → that is the trade we want.