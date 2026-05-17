# P6 — Execution-Policy Sensitivity Matrix

**Generated**: 2026-04-25T17:50:46
**Window**: `post_oos` (2024-06-01 → 2026-02-27)
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**SPY CAGR (window)**: +15.85%

## CAGR matrix

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | +50.91% | +54.44% | +54.23% | +53.19% | 1.62% | 3.53pp |
| P5C_BULL_FREE | +44.20% | +43.48% | +43.66% | +43.78% | 0.30% | 0.72pp |

## Sharpe matrix

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | 1.517 | 1.587 | 1.583 | 1.562 | 0.032 | 0.070 |
| P5C_BULL_FREE | 1.509 | 1.393 | 1.401 | 1.434 | 0.053 | 0.116 |

## MDD matrix (lower better)

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | 30.29% | 30.03% | 29.99% | 30.10% | 0.13% | 0.30pp |
| P5C_BULL_FREE | 28.63% | 30.45% | 30.36% | 29.81% | 0.84% | 1.82pp |

## Robustness ranking (CAGR std across policies)

| rank | signal | mean CAGR | std CAGR | range CAGR |
|---:|---|---:|---:|---:|
| 1 | P5C_BULL_FREE | +43.78% | 0.30% | 0.72pp |
| 2 | V2_GOLDEN | +53.19% | 1.62% | 3.53pp |

## Best policy per signal

| signal | best policy | best CAGR | Δ vs default | worst policy | worst CAGR |
|---|---|---:|---:|---|---:|
| V2_GOLDEN | +buy_grace=3 | +54.44% | +3.53pp | Default | +50.91% |
| P5C_BULL_FREE | Default | +44.20% | +0.00pp | +buy_grace=3 | +43.48% |

## Policies

- **Default** (`default`) — LEGACY V2 strategy verbatim — control.
- **+buy_grace=3** (`buy_grace_3`) — Signal-domain noise filter (current production).
- **+vt_S_18** (`vt_S_18`) — Portfolio-domain SIDE-only deleveraging (target 18% ann vol).