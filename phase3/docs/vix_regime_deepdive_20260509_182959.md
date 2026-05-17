# VIX Regime Deep-Dive — 2026-05-09 18:29

- **Window**: 2011-01-03 → 2026-02-27
- **Days**: 3811
- **Forward horizons**: [5, 21]
- **DEF cutoff fixed**: 30.0

## 1. VIX distribution

| stat | value |
|---|---:|
| min | nan |
| p1 | nan |
| p5 | nan |
| p10 | nan |
| p25 | nan |
| p50 | nan |
| mean | nan |
| p75 | nan |
| p90 | nan |
| p95 | nan |
| p99 | nan |
| max | nan |
| std | nan |

## 2. BULL/SIDE/DEF threshold sweep

Forward returns are equal-weight universe close-to-close log-return averages (percent). `B-S` = BULL_mean − SIDE_mean (positive ⇒ BULL identifies higher expected forward return).

| bull_thr | BULL% | SIDE% | DEF% | bull_fwd5d | side_fwd5d | B-S 5d | bull_fwd21d | side_fwd21d | B-S 21d |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 14.00 | 28.2 | 66.0 | 5.8 | -0.080 | +0.142 | -0.222 | -0.179 | +0.327 | -0.506 |
| 15.00 | 36.9 | 57.4 | 5.8 | -0.051 | +0.157 | -0.209 | -0.157 | +0.390 | -0.547 |
| 16.00 | 45.6 | 48.7 | 5.8 | -0.035 | +0.179 | -0.213 | -0.154 | +0.485 | -0.639 |
| 17.00 | 54.2 | 40.1 | 5.8 | -0.051 | +0.247 | -0.299 | -0.172 | +0.647 | -0.819 |
| 17.50 | 58.4 | 35.8 | 5.8 | -0.042 | +0.267 | -0.308 | -0.128 | +0.670 | -0.798 |
| 18.00 | 62.1 | 32.1 | 5.8 | -0.037 | +0.292 | -0.329 | -0.090 | +0.689 | -0.779 |
| 18.50 | 65.2 | 29.1 | 5.8 | -0.017 | +0.282 | -0.299 | -0.045 | +0.671 | -0.716 |
| 19.00 | 68.6 | 25.6 | 5.8 | -0.007 | +0.296 | -0.303 | -0.054 | +0.791 | -0.845 |
| 20.00 | 73.8 | 20.4 | 5.8 | -0.006 | +0.372 | -0.378 | -0.095 | +1.156 | -1.252 |
| 21.00 | 77.2 | 17.1 | 5.8 | +0.007 | +0.386 | -0.379 | -0.085 | +1.352 | -1.437 |
| 22.00 | 80.7 | 13.6 | 5.8 | +0.008 | +0.478 | -0.470 | -0.054 | +1.537 | -1.591 |

## 3. VIX bucket → forward returns

| bucket | days | share | fwd5d log% | win5% | fwd21d log% | win21% |
|---|---:|---:|---:|---:|---:|---:|
| [0, 12) | 285 | 7.5% | +0.249 | 62% | +0.571 | 64% |
| [12, 14) | 790 | 20.7% | -0.199 | 49% | -0.450 | 51% |
| [14, 16) | 662 | 17.4% | +0.039 | 57% | -0.114 | 58% |
| [16, 18) | 630 | 16.5% | -0.042 | 52% | +0.087 | 57% |
| [18, 20) | 447 | 11.7% | +0.154 | 59% | -0.123 | 60% |
| [20, 22) | 260 | 6.8% | +0.161 | 59% | +0.397 | 63% |
| [22, 25) | 269 | 7.1% | +0.354 | 62% | +1.061 | 67% |
| [25, 30) | 249 | 6.5% | +0.611 | 62% | +2.052 | 70% |
| [30, 35) | 138 | 3.6% | +0.497 | 58% | +2.921 | 74% |
| [35, 50) | 62 | 1.6% | +0.084 | 60% | +4.376 | 76% |
| [50, 100) | 18 | 0.5% | +2.495 | 56% | +12.667 | 100% |

## Interpretation

- **Optimal BULL/SIDE cutoff** is the threshold that maximizes the `B-S` spread *while* keeping enough mass in BULL bucket (≥30% of days). 
- A wide `B-S` gap means VIX cleanly separates expected returns; a narrow or negative gap means VIX provides little regime alpha at that boundary.
- The bucket table reveals the underlying structure: forward-return monotonic behaviour vs. VIX, and the practical 'breakpoint' where SIDE-style risk actually emerges.
