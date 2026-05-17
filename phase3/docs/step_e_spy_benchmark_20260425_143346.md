# Step E — SPY Benchmark + G7 Gates

**Generated**: 2026-04-25T14:33:46

## 1. SPY Buy-and-Hold (no cost)

| Window | Range | CAGR | Sharpe | MDD | Calmar | Total Ret |
|---|---|---:|---:|---:|---:|---:|
| **FULL** | 2012-01-03 → 2025-12-31 | +12.73% | +0.802 | 34.10% | 0.373 | +434.8% |
| **F0a** | 2012-01-03 → 2014-12-31 | +17.30% | +1.423 | 9.69% | 1.786 | +61.2% |
| **F0b** | 2015-01-02 → 2016-12-30 | +4.33% | +0.366 | 14.35% | 0.302 | +8.8% |
| **F1** | 2019-01-02 → 2020-12-31 | +22.30% | +0.922 | 34.10% | 0.654 | +49.4% |
| **F2** | 2021-01-04 → 2022-12-30 | +1.85% | +0.191 | 25.36% | 0.073 | +3.7% |
| **F3** | 2023-01-03 → 2024-05-31 | +26.03% | +1.895 | 10.29% | 2.530 | +38.5% |
| **F4** | 2024-06-03 → 2025-12-31 | +17.64% | +1.020 | 19.00% | 0.928 | +29.2% |

## 2. G7 Gate Verdicts (per signal)

| Signal | G7-A (CAGR≥SPY) | G7-B (Sharpe≥SPY) | G7-C (per-fold ≥5/6) | G7-D (IR≥0.5 proxy) | mean α (pp) | wins/total |
|---|:---:|:---:|:---:|:---:|---:|:---:|
| **Baseline_V2** | — | — | ✓ | ✓ | +16.25 | 6/6 |
| **T1b_BULL_INJECTED** | — | — | ✓ | ✓ | +14.11 | 6/6 |
| **P6_ENSEMBLE_C** | — | — | ✓ | ✓ | +13.93 | 6/6 |

### Per-fold alpha breakdown

| Signal | F0a | F0b | F1 | F2 | F3 | F4 |
|---|---:|---:|---:|---:|---:|---:|
| **Baseline_V2** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **T1b_BULL_INJECTED** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **P6_ENSEMBLE_C** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

---

**Notes**

- SPY is buy-and-hold from window start to window end at daily close, no costs/slippage.
- G7-A/B require a strategy *full-period* metric; if absent (e.g. only walk-forward folds were provided), they are skipped.
- G7-D is a *per-fold-CAGR* proxy IR, not a true daily-return IR. Treat as directional signal only.