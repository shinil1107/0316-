# P6 — Execution-Policy Sensitivity Matrix

**Generated**: 2026-04-25T18:02:54
**Window**: `full` (2012-01-03 → 2026-02-27)
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**SPY CAGR (window)**: +12.58%

## CAGR matrix

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | +slow_deploy | +strict_trim | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | +30.43% | +30.31% | +30.79% | +29.31% | +30.18% | +30.21% | 0.49% | 1.48pp |
| T1b_BULL_INJECTED | +28.79% | +28.29% | +28.34% | +26.61% | +28.31% | +28.07% | 0.75% | 2.18pp |
| P5C_BULL_FREE | +25.46% | +25.19% | +25.92% | +24.23% | +25.00% | +25.16% | 0.56% | 1.68pp |
| P5C_SIDE_HEAVY | +20.32% | +19.41% | +19.32% | +18.44% | +19.39% | +19.38% | 0.60% | 1.88pp |
| P6_ENSEMBLE_A | +28.08% | +27.69% | +27.66% | +26.66% | +27.47% | +27.51% | 0.47% | 1.43pp |

## Sharpe matrix

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | +slow_deploy | +strict_trim | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | 1.247 | 1.222 | 1.246 | 1.217 | 1.225 | 1.231 | 0.013 | 0.030 |
| T1b_BULL_INJECTED | 1.207 | 1.177 | 1.191 | 1.152 | 1.183 | 1.182 | 0.018 | 0.054 |
| P5C_BULL_FREE | 1.141 | 1.097 | 1.137 | 1.086 | 1.095 | 1.111 | 0.023 | 0.055 |
| P5C_SIDE_HEAVY | 1.059 | 0.997 | 1.014 | 0.997 | 1.001 | 1.013 | 0.024 | 0.062 |
| P6_ENSEMBLE_A | 1.206 | 1.175 | 1.180 | 1.168 | 1.175 | 1.181 | 0.013 | 0.038 |

## MDD matrix (lower better)

| signal \ policy | Default | +buy_grace=3 | +vt_S_18 | +slow_deploy | +strict_trim | mean | std | range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V2_GOLDEN | 34.23% | 34.07% | 34.03% | 33.33% | 34.17% | 33.97% | 0.32% | 0.89pp |
| T1b_BULL_INJECTED | 35.75% | 35.33% | 34.98% | 33.93% | 35.35% | 35.07% | 0.62% | 1.82pp |
| P5C_BULL_FREE | 36.16% | 37.76% | 37.04% | 36.87% | 37.76% | 37.12% | 0.60% | 1.60pp |
| P5C_SIDE_HEAVY | 33.96% | 35.28% | 34.98% | 33.89% | 35.30% | 34.68% | 0.63% | 1.40pp |
| P6_ENSEMBLE_A | 35.34% | 34.90% | 35.23% | 34.24% | 35.68% | 35.08% | 0.49% | 1.44pp |

## Robustness ranking (CAGR std across policies)

| rank | signal | mean CAGR | std CAGR | range CAGR |
|---:|---|---:|---:|---:|
| 1 | P6_ENSEMBLE_A | +27.51% | 0.47% | 1.43pp |
| 2 | V2_GOLDEN | +30.21% | 0.49% | 1.48pp |
| 3 | P5C_BULL_FREE | +25.16% | 0.56% | 1.68pp |
| 4 | P5C_SIDE_HEAVY | +19.38% | 0.60% | 1.88pp |
| 5 | T1b_BULL_INJECTED | +28.07% | 0.75% | 2.18pp |

## Best policy per signal

| signal | best policy | best CAGR | Δ vs default | worst policy | worst CAGR |
|---|---|---:|---:|---|---:|
| V2_GOLDEN | +vt_S_18 | +30.79% | +0.37pp | +slow_deploy | +29.31% |
| T1b_BULL_INJECTED | Default | +28.79% | +0.00pp | +slow_deploy | +26.61% |
| P5C_BULL_FREE | +vt_S_18 | +25.92% | +0.46pp | +slow_deploy | +24.23% |
| P5C_SIDE_HEAVY | Default | +20.32% | +0.00pp | +slow_deploy | +18.44% |
| P6_ENSEMBLE_A | Default | +28.08% | +0.00pp | +slow_deploy | +26.66% |

## Policies

- **Default** (`default`) — LEGACY V2 strategy verbatim — control.
- **+buy_grace=3** (`buy_grace_3`) — Signal-domain noise filter (current production).
- **+vt_S_18** (`vt_S_18`) — Portfolio-domain SIDE-only deleveraging (target 18% ann vol).
- **+slow_deploy** (`slow_deploy`) — Halve deploy rate across all regimes — slower position build-up.
- **+strict_trim** (`strict_trim`) — Trim more aggressively (0.03 → 0.015) — over-trade dimension.