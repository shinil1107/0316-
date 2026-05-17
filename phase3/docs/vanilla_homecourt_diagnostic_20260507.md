# Vanilla-Mode Home-Court Diagnostic — V2.1 Profit-Target Trigger Effect

**Date**: 2026-05-07  
**Question**: Is V2's lead over Cross-Era L3 candidates due to genuine signal quality, or is the portfolio strategy stack (specifically the V2.1 SIDE_DEF_p12 `profit_target` exit trigger) silently giving V2 a home-court advantage?

**Method**: Re-run walk-forward with `--vanilla` flag, which sets `strategy.exit_triggers = None` and falls back to the pre-v2.1 legacy stack (`enable_stop_loss` + `sell_grace_days`). Compare CAGR, std, CV, and the inter-signal CAGR gap between Production and Vanilla modes.

---

## Default Fold-Set (6 folds, 2012-12 → 2026-02)

| Signal | Production CAGR | Vanilla CAGR | Δ (Van−Prod) |
|---|---:|---:|---:|
| **Baseline_V2** | +31.1% | +31.2% | **+0.1pp** |
| P10_CROSS_ERA_EQ | +23.9% | +24.5% | +0.6pp |
| P10_CROSS_ERA_V2H | +24.0% | +24.5% | +0.5pp |
| P10_CROSS_ERA_FULL | +25.2% | +25.5% | +0.3pp |
| **V2 vs best Cross-Era gap** | **5.9pp** (vs FULL) | **5.7pp** (vs FULL) | **−0.2pp** |

**Per-signal stability**:
| Signal | Prod std/CV | Vanilla std/CV |
|---|---|---|
| Baseline_V2 | 14.4% / 0.464 | 14.0% / 0.448 |
| P10_CROSS_ERA_EQ | 15.2% / 0.633 | 15.5% / 0.632 |

## Rolling Fold-Set (8 sliding 8yr windows)

| Signal | Production CAGR | Vanilla CAGR | Δ (Van−Prod) |
|---|---:|---:|---:|
| **Baseline_V2** | +27.2% | +26.8% | **−0.4pp** |
| P10_CROSS_ERA_EQ | +19.6% | +19.2% | −0.4pp |
| P10_CROSS_ERA_V2H | +20.4% | +20.0% | −0.4pp |
| P10_CROSS_ERA_FULL | +20.6% | +20.2% | −0.4pp |
| **V2 vs best Cross-Era gap** | **6.6pp** | **6.6pp** | **0.0pp** |

---

## Diagnosis

### Hypothesis A — V2 has home-court advantage from V2.1 trigger
**REJECTED.** If V2.1 profit_target trigger were silently boosting V2's score, removing it (vanilla) should have:
1. Reduced V2's CAGR substantially (e.g. >2pp), AND
2. Held Cross-Era's CAGR roughly flat (or improved it).

Observed:
- V2's CAGR moved by ±0.4pp between modes (within noise).
- Cross-Era candidates moved similarly (±0.4-0.6pp).
- The **gap** between V2 and best Cross-Era is essentially identical: **5.7-5.9pp (default), 6.6pp (rolling)**.

### Hypothesis B — V2's lead is genuine signal quality
**SUPPORTED.** The 6-7pp CAGR gap survives the removal of the V2.1 exit-trigger tuning entirely. This means:
- The advantage is in score generation, not exit timing.
- Score quality difference is consistent across both default and rolling fold-sets.
- The profit_target trigger contributes <0.5pp to either signal's CAGR — basically noise-level.

### What about the OTHER V2-era tunings?
- `buy_grace_days=3` from p3 sweep — already neutralized in step_d (default arg = 0). Both Production and Vanilla runs used grace=0.
- `vol_target` from p5 series — not in default config; activated only via explicit override.
- `sector_cap` from p7 — applies to all signals identically (architectural, not signal-specific tuning).

The only remaining V2-era-only tuning that **was** active is the V2.1 `profit_target` trigger, which we just confirmed has negligible effect on the comparison.

---

## Conclusion

**V2's superiority is NOT a portfolio-strategy artifact.**

The Cross-Era L3 ensembles' ~7pp CAGR shortfall is a genuine signal-quality gap, not a measurement bias from V2-era hyperparameter tuning.

### Implications for next steps
1. **Hyperparameter sweeps on Cross-Era won't close the gap.** Even if a per-candidate L3 sweep gave each Cross-Era candidate +0.5-1pp (typical sweep ROI), V2 would still lead by 5-6pp.
2. **Architecture-level changes are warranted.** The current GA-Linear-Score ceiling appears to be at V2's level, and incremental ensemble re-shuffling has hit its limit.
3. **ML extension is justified.** Per the `CURSOR_HANDOFF_ML_TOPOLOGY_EXTENSION.md` plan, Phase ML-1 (XGBoost baseline + walk-forward cache) is the natural next step — the linear hyperplane structure of GA Linear Score may be the bottleneck.
4. **Methodology refinement**: For future baseline-replacement decisions, run the candidate vs. baseline in BOTH modes:
   - **Production stack** (current config) — for deployment readiness.
   - **Vanilla stack** (`--vanilla`) — to verify signal-quality independent of stack tuning.
   If a candidate beats baseline only in Production mode but not Vanilla, the lead is cosmetic (stack-tuned), not real.

---

## Data Files
- Production default: `t5_walk_forward_results_20260507_190816.{json,md}`
- Vanilla default: `t5_walk_forward_results_20260507_195621.{json,md}`
- Production rolling: `t5_walk_forward_results_20260507_192409.{json,md}`
- Vanilla rolling: `t5_walk_forward_results_20260507_200838.{json,md}`
- Production regime: `t5_walk_forward_results_20260507_191146.{json,md}` (regime fold not re-run in vanilla; gap structure expected to be similar based on default+rolling consistency)
