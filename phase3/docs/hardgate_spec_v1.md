# Hardgate Specification v1 — Baseline Signal Replacement Criteria

**Created**: 2026-05-05
**Status**: Active
**Implements**: Phase A / 주제 #4

---

## Overview

A candidate signal may replace the current production baseline **only** if it
passes every **hard gate** on *every* fold-set evaluation.  Soft gates are
logged and may inform human judgment, but do not block deployment.

All gates are **relative to baseline** — the baseline itself always auto-passes
(since cand == base in that comparison).

---

## Gate Definitions

### Hard Gates (must ALL pass)

| Gate | Rule | Rationale |
|------|------|-----------|
| **G-A** | CV(cand) ≤ CV(baseline) + 0.05 | Relative stability: candidate may be at most 5 pp less stable than baseline. |
| **G-B** | mean_CAGR(cand) ≥ mean_CAGR(baseline) × 0.90 | Performance floor: candidate cannot sacrifice more than 10% of baseline return. |
| **G-C** | worst_fold_CAGR(cand) ≥ worst_fold_CAGR(baseline) − 0.01 | Tail risk: candidate worst fold within 1 pp of baseline worst. |
| **G-D** | pos_count(cand) ≥ pos_count(baseline) | Consistency: no fewer positive folds than baseline. |

### Soft Gates (informational, non-blocking)

| Gate | Rule | Rationale |
|------|------|-----------|
| **G-E** | worst_MDD(cand) ≤ worst_MDD(baseline) × 1.10 | Drawdown guard: candidate should not dramatically increase max drawdown. |
| **G-F** | mean_Sharpe(cand) ≥ mean_Sharpe(baseline) × 0.90 | Risk-adjusted floor: risk-adjusted return should be comparable. |
| **G-G** | OOS_CAGR_std(cand) ≤ OOS_CAGR_std(baseline) + 0.01 | OOS consistency: out-of-sample performance spread within 1 pp. |
| **G-H** | Lift_10d(cand) ≥ Lift_10d(baseline) × 0.80 | Surge capture: candidate must retain ≥80% of baseline's surge prediction lift (top-decile 10d-horizon). Q5/Q1 ratio logged for monotonicity check. |

---

## Evaluation Protocol

### Required Fold Sets

A candidate must pass hard gates on **all three** fold-set evaluations:

| Fold-set | Purpose | Folds |
|----------|---------|-------|
| `default` | In-sample temporal stability (6-fold, all in-sample for B7/B8) | F0a, F0b, F1, F2, F3, F4 |
| `rolling` | In-sample temporal stability (8×8yr sliding windows, 1yr step) | R1–R8 |
| `regime` | Regime-stratified stability (BULL/SIDE/MIX) | BULL_1–3, SIDE_1–2, MIX_1–2 |

### Promotion Decision Matrix

```
ALL hard gates pass on ALL 3 fold-sets  → PROMOTE (candidate becomes baseline)
ALL hard gates pass on 2/3 fold-sets    → CONDITIONAL (manual review required)
ALL hard gates pass on 1/3 fold-sets    → REJECT (candidate is not ready)
Any hard gate fails on all fold-sets    → REJECT
```

### Soft-gate advisory

If a candidate passes all hard gates but fails 2+ soft gates, the promotion
is flagged for manual review with a detailed report of which soft gates failed
and the magnitude of the deficit.

---

## Simulation Parameters (fixed across all evaluations)

| Parameter | Value |
|-----------|-------|
| Initial capital | $100,000 |
| Daily buy limit | $1,000 |
| Commission | 10 bps |
| Slippage | 5 bps |
| Rebalance mode | Daily |
| Strategy stack | SIDE_DEF_p12 |
| Regime blend | OFF |
| Buy-grace-days | 0 (default sweep) |

---

## Changelog

- **v1.2** (2026-05-05): Added G-H surge-capture gate.
  - New soft gate G-H: Lift_10d(cand) ≥ Lift_10d(baseline) × 0.80.
  - Measures top-decile surge prediction power (forward +20%, 10d horizon).
  - Logs Q5/Q1 quintile ratio for monotonicity health check.
  - Data source: surge_score_analysis.py.
- **v1.1** (2026-05-05): Redesign — all gates relative to baseline.
  - Removed absolute gates (old G-A CV≤0.50, old G-C/G-D all-positive).
  - Merged old G-C and G-D (redundant) into new G-C (worst fold ≥ base - 1pp).
  - 4 hard gates (G-A through G-D), 3 soft gates (G-E through G-G).
  - Added 1pp tolerance to OOS std soft gate (G-G).
  - Baseline always auto-passes by construction.
- **v1** (2026-05-05): Initial specification.
  - 5 hard gates (G-A through G-E), 3 soft gates (G-F through G-H).
  - Three fold-set evaluation (default, rolling, regime).
  - Promotion decision matrix codified.
