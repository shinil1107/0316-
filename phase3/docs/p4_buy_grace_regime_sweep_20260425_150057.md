# P4 — Regime-Conditional Buy-Grace Sweep · Baseline_V2

**Generated**: 2026-04-25T15:00:57
**Signal**: frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz
**Window**: 2012-01-01 → 2026-02-27
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Protocol**: $100K · $1K/day · 10/5 bps · SIDE_DEF_p12 · daily rebal · variant=d (regime-conditional strict)

**Ref (Δ)**: `g3_flat` (BSD = 3/3/3 — v1.3 production)
**ΔΔ baseline**: `g0_flat` (BSD = 0/0/0 — legacy)

## 1. Headline

| tag | B/S/D | CAGR | Δ ref | ΔΔ g0 | Sharpe | MDD | Calmar | Comm % | Δ Comm vs ref | Final $ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **g0_flat** | 0/0/0 | +30.43% | +0.12pp | +0.00pp | +1.247 | 34.23% | 0.889 | 42.34% | +8.43pp | $4,259,931 |
| **g3_flat** | 3/3/3 | +30.31% | +0.00pp | -0.12pp | +1.222 | 34.07% | 0.890 | 33.91% | +0.00pp | $4,205,207 |
| **RC_a** | 1/3/5 | +30.22% | -0.09pp | -0.21pp | +1.219 | 34.67% | 0.872 | 34.76% | +0.85pp | $4,163,929 |
| **RC_b** | 5/3/3 | +29.82% | -0.49pp | -0.61pp | +1.202 | 35.54% | 0.839 | 31.49% | -2.42pp | $3,987,914 |
| **RC_c** | 3/2/5 | +30.44% | +0.13pp | +0.01pp | +1.226 | 34.15% | 0.891 | 34.97% | +1.06pp | $4,263,796 |
| **RC_d** | 5/2/5 | +29.49% | -0.82pp | -0.94pp | +1.195 | 36.13% | 0.816 | 32.88% | -1.03pp | $3,848,165 |

## 2. Regime breakdown — AnnRet (Δ vs g0_flat)

| tag | B/S/D | BULL | Δ | SIDE | Δ | DEF | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| **g0_flat** | 0/0/0 | +39.11% | +0.00pp | +19.24% | +0.00pp | +90.99% | +0.00pp |
| **g3_flat** | 3/3/3 | +38.72% | -0.39pp | +18.71% | -0.53pp | +108.71% | +17.72pp |
| **RC_a** | 1/3/5 | +38.89% | -0.22pp | +18.03% | -1.21pp | +110.41% | +19.42pp |
| **RC_b** | 5/3/3 | +38.62% | -0.49pp | +17.95% | -1.29pp | +103.45% | +12.46pp |
| **RC_c** | 3/2/5 | +38.97% | -0.14pp | +18.42% | -0.82pp | +111.76% | +20.77pp |
| **RC_d** | 5/2/5 | +38.16% | -0.95pp | +17.70% | -1.54pp | +103.76% | +12.77pp |

## 3. Verdict

- **Verdict against `g3_flat`**: `no_pareto`
- **Score ranking** (top 5; score = ΔCAGR − 0.5×ΔComm − 5×ΔMDD + 2×ΔCalmar):
    - `RC_c`
    - `RC_a`
    - `g0_flat`
    - `RC_b`
    - `RC_d`

---

**Reading guide**

- A regime-conditional config is a *meaningful* improvement over `g3_flat` only if it:
  (i) holds CAGR within −0.10pp of `g3_flat`,
  (ii) further reduces commission, **and**
  (iii) preserves Calmar / MDD.
- If no candidate Pareto-dominates `g3_flat`, the v1.3 production setting (`g=3` flat) remains optimal.
- `score_ranked` is a tie-breaker proxy when no strict Pareto winner exists; treat as suggestive only.