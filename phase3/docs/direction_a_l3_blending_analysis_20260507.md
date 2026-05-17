# Direction A: L3 Equal-Weight Blending Analysis

**Date**: 2026-05-07  
**Objective**: Evaluate L3 equal-weight blending of Batch 8 signals as a potential Baseline_V2 replacement

---

## Executive Summary

**Result**: ❌ **L3 equal-weight blending does NOT surpass Baseline_V2**

P9_L3_EQUAL_Q (equal-weight blend of P8_SIDE_V3, P8_BULL_DENSE, P8_BALANCED) **failed hardgates** across all three fold-sets:
- **Default**: mean CAGR -1.3pp lower, G-A and G-C failed
- **Rolling**: mean CAGR -4.7pp lower, G-B, G-C, and G-F failed  
- **Regime**: mean CAGR -1.5pp lower, G-A failed

**Bright spots**: P9_L3_EQUAL_Q showed stronger performance in recent folds (F3, F4) and BULL_3, suggesting potential for near-term live performance, but overall stability and mean CAGR are insufficient for baseline replacement.

---

## 1. Ensemble Construction

### Recipe: P9_L3_EQUAL_Q

```yaml
Tag: P9_L3_EQUAL_Q
Method: L3 equal-weight blending (all 3 regime slots averaged)

wb_components:
  - P8_SIDE_V3     (weight: 1/3)
  - P8_BULL_DENSE  (weight: 1/3)
  - P8_BALANCED    (weight: 1/3)

ws_components:
  - P8_SIDE_V3     (weight: 1/3)
  - P8_BULL_DENSE  (weight: 1/3)
  - P8_BALANCED    (weight: 1/3)

wd_components:
  - P8_SIDE_V3     (weight: 1/3)
  - P8_BULL_DENSE  (weight: 1/3)
  - P8_BALANCED    (weight: 1/3)
```

### Feature Density

| Signal         | wb  | ws  | wd  | mask (union) |
|----------------|-----|-----|-----|--------------|
| P8_SIDE_V3     | 3   | 2   | 4   | ?            |
| P8_BULL_DENSE  | 5   | 7   | 2   | ?            |
| P8_BALANCED    | 3   | 4   | 4   | ?            |
| **P9_L3_EQUAL_Q** | **5** | **8** | **4** | **13** |
| **Baseline_V2**   | **15** | **15** | **15** | **15** |

**Observation**: Despite L3 blending, P9_L3_EQUAL_Q remains sparse (k=13) compared to Baseline_V2 (k=15). The source signals' sparsity (k=3-7 per slot) limits the density gain from averaging.

---

## 2. Performance Comparison: Default Fold-Set

### 2.1 Overall Metrics

| Signal         | n | mean CAGR | std CAGR | CV    | worst CAGR | pos/n | Hardgate |
|----------------|---|-----------|----------|-------|------------|-------|----------|
| Baseline_V2    | 6 | **+31.1%** | +14.4%   | 0.464 | +12.1%     | 6/6   | ✓ ALL    |
| P9_L3_EQUAL_Q  | 6 | +29.8%    | +16.8%   | 0.565 | +9.3%      | 6/6   | ✗ FAIL   |

**Delta**: -1.3pp mean CAGR, +0.101 higher CV (less stable)

### 2.2 Fold-by-Fold Breakdown

| Fold | Group       | Baseline_V2 | P9_L3_EQUAL_Q | Delta   | Winner      |
|------|-------------|-------------|---------------|---------|-------------|
| F0a  | pre_oos     | +24.62%     | +21.20%       | -3.42pp | Baseline    |
| F0b  | pre_oos     | +12.13%     | +11.60%       | -0.53pp | Baseline    |
| F1   | in_sample   | +45.16%     | +36.24%       | -8.92pp | Baseline    |
| F2   | in_sample   | +14.76%     | +9.34%        | -5.42pp | Baseline    |
| F3   | in_sample   | +43.92%     | **+49.73%**   | **+5.81pp** | **P9_L3_Q** |
| F4   | post_oos    | +45.92%     | **+50.65%**   | **+4.73pp** | **P9_L3_Q** |

**Key Insight**: P9_L3_EQUAL_Q wins the two most recent folds (F3, F4) by ~5pp each, suggesting near-term alpha potential. However, it underperforms significantly in F1 and F2 (-8.92pp, -5.42pp), dragging down the overall mean.

### 2.3 Hardgate Verdict (Default)

| Gate | Rule                                              | Baseline_V2 | P9_L3_EQUAL_Q | Result |
|------|---------------------------------------------------|-------------|---------------|--------|
| G-A  | mean CAGR ≥ baseline - 1pp                        | ✓           | **✗ (-1.3pp)** | FAIL   |
| G-B  | CV ≤ baseline + 0.05                              | ✓           | ✓             | PASS   |
| G-C  | worst fold CAGR ≥ baseline - 1pp                  | ✓           | **✗ (-2.8pp)** | FAIL   |
| G-D  | pos_count ≥ baseline                              | ✓           | ✓             | PASS   |
| G-E  | worst MDD ≤ baseline × 1.10 (soft)                | ✓           | ✓             | PASS   |
| G-F  | mean Sharpe ≥ baseline × 0.90 (soft)              | ✓           | ✓             | PASS   |
| G-G  | OOS CAGR std ≤ baseline + 0.01 (soft)             | ✓           | ✗             | SOFT   |

**Verdict**: **FAIL** (2 hard gates failed: G-A, G-C)

---

## 3. Performance Comparison: Rolling Fold-Set (8-Year Sliding Windows)

### 3.1 Overall Metrics

| Signal         | n | mean CAGR | std CAGR | CV    | worst CAGR | pos/n | Hardgate |
|----------------|---|-----------|----------|-------|------------|-------|----------|
| Baseline_V2    | 8 | **+27.2%** | +5.3%    | 0.193 | +19.9%     | 8/8   | ✓ ALL    |
| P9_L3_EQUAL_Q  | 8 | +22.5%    | +4.6%    | 0.204 | +17.1%     | 8/8   | ✗ FAIL   |

**Delta**: **-4.7pp** mean CAGR (largest gap across all fold-sets), +0.011 higher CV

### 3.2 Fold-by-Fold Breakdown

| Fold | Window              | Baseline_V2 | P9_L3_EQUAL_Q | Delta   |
|------|---------------------|-------------|---------------|---------|
| R1   | 2011-01-01 → 2018-12-31 | +19.91%     | +17.10%       | -2.81pp |
| R2   | 2012-01-01 → 2019-12-31 | +20.66%     | +18.47%       | -2.19pp |
| R3   | 2013-01-01 → 2020-12-31 | +25.25%     | +20.11%       | -5.14pp |
| R4   | 2014-01-01 → 2021-12-31 | +27.47%     | +23.94%       | -3.53pp |
| R5   | 2015-01-01 → 2022-12-31 | +24.95%     | +18.69%       | -6.26pp |
| R6   | 2016-01-01 → 2023-12-31 | +30.98%     | +22.73%       | -8.25pp |
| R7   | 2017-01-01 → 2024-12-31 | +32.18%     | +26.10%       | -6.08pp |
| R8   | 2018-01-01 → 2026-02-27 | +34.26%     | +32.10%       | -2.16pp |

**Key Insight**: P9_L3_EQUAL_Q underperforms Baseline_V2 in **all 8 rolling windows**, with gaps ranging from -2.16pp to -8.25pp. The largest deficits occur in R5-R7 (2015-2024 periods), which include high-volatility SIDE-dominant regimes.

### 3.3 Hardgate Verdict (Rolling)

| Gate | Rule                                              | Baseline_V2 | P9_L3_EQUAL_Q | Result |
|------|---------------------------------------------------|-------------|---------------|--------|
| G-A  | mean CAGR ≥ baseline - 1pp                        | ✓           | ✓             | PASS   |
| G-B  | CV ≤ baseline + 0.05                              | ✓           | **✗ (+0.011)** | FAIL   |
| G-C  | worst fold CAGR ≥ baseline - 1pp                  | ✓           | **✗ (-2.8pp)** | FAIL   |
| G-D  | pos_count ≥ baseline                              | ✓           | ✓             | PASS   |
| G-E  | worst MDD ≤ baseline × 1.10 (soft)                | ✓           | ✓             | PASS   |
| G-F  | mean Sharpe ≥ baseline × 0.90 (soft)              | ✓           | **✗**         | SOFT   |
| G-G  | OOS CAGR std ≤ baseline + 0.01 (soft)             | ✗           | ✗             | SOFT   |

**Verdict**: **FAIL** (3 gates failed: G-B, G-C hard; G-F soft)

---

## 4. Performance Comparison: Regime Fold-Set

### 4.1 Overall Metrics

| Signal         | n | mean CAGR | std CAGR | CV    | worst CAGR | pos/n | Hardgate |
|----------------|---|-----------|----------|-------|------------|-------|----------|
| Baseline_V2    | 7 | **+30.1%** | +15.9%   | 0.528 | +1.7%      | 7/7   | ✓ ALL    |
| P9_L3_EQUAL_Q  | 7 | +28.6%    | +16.8%   | 0.587 | +4.9%      | 7/7   | ✗ FAIL   |

**Delta**: -1.5pp mean CAGR, +0.059 higher CV

### 4.2 Regime-Group Breakdown

| Regime Group | Baseline_V2 | P9_L3_EQUAL_Q | Delta   |
|--------------|-------------|---------------|---------|
| BULL_dom     | +34.3%      | +33.1%        | -1.2pp  |
| SIDE_dom     | +8.2%       | +7.1%         | -1.1pp  |
| Mixed        | +45.6%      | +43.4%        | -2.2pp  |

**Key Insight**: P9_L3_EQUAL_Q underperforms across **all three regime groups**. The SIDE_dom gap is smaller (-1.1pp), consistent with P8_SIDE_V3's contribution, but still insufficient.

### 4.3 Fold-by-Fold Breakdown

| Fold    | Group      | Baseline_V2 | P9_L3_EQUAL_Q | Delta   | Winner      |
|---------|------------|-------------|---------------|---------|-------------|
| BULL_1  | bull_dom   | +24.62%     | +21.20%       | -3.42pp | Baseline    |
| BULL_2  | bull_dom   | +34.34%     | +28.23%       | -6.11pp | Baseline    |
| BULL_3  | bull_dom   | +43.92%     | **+49.73%**   | **+5.81pp** | **P9_L3_Q** |
| SIDE_1  | side_dom   | +1.67%      | +4.90%        | +3.23pp | P9_L3_Q     |
| SIDE_2  | side_dom   | +14.76%     | +9.37%        | -5.39pp | Baseline    |
| MIX_1   | mixed      | +45.18%     | +36.24%       | -8.94pp | Baseline    |
| MIX_2   | mixed      | +45.92%     | **+50.65%**   | **+4.73pp** | **P9_L3_Q** |

**Key Insight**: P9_L3_EQUAL_Q wins in 3 out of 7 folds (BULL_3, SIDE_1, MIX_2), notably including the two most recent mixed/BULL periods. However, large losses in MIX_1 (-8.94pp) and SIDE_2 (-5.39pp) offset these gains.

### 4.4 Hardgate Verdict (Regime)

| Gate | Rule                                              | Baseline_V2 | P9_L3_EQUAL_Q | Result |
|------|---------------------------------------------------|-------------|---------------|--------|
| G-A  | mean CAGR ≥ baseline - 1pp                        | ✓           | **✗ (-1.5pp)** | FAIL   |
| G-B  | CV ≤ baseline + 0.05                              | ✓           | ✓             | PASS   |
| G-C  | worst fold CAGR ≥ baseline - 1pp                  | ✓           | ✓             | PASS   |
| G-D  | pos_count ≥ baseline                              | ✓           | ✓             | PASS   |
| G-E  | worst MDD ≤ baseline × 1.10 (soft)                | ✓           | ✓             | PASS   |
| G-F  | mean Sharpe ≥ baseline × 0.90 (soft)              | ✓           | ✓             | PASS   |
| G-G  | OOS CAGR std ≤ baseline + 0.01 (soft)             | ✗           | ✗             | SOFT   |

**Verdict**: **FAIL** (1 hard gate failed: G-A)

---

## 5. Root Cause Analysis

### 5.1 Structural Limitations

**Feature Sparsity Bottleneck**:
- All Batch 8 source signals are sparse (k=3-7 per slot)
- L3 blending produces k=13 combined mask (vs Baseline_V2's k=15)
- Averaging sparse vectors yields limited density gains

**Baseline_V2 Advantage**:
- Baseline_V2 itself is an L3 ensemble of 3 highly-tuned signals
- Its k=15 density reflects multi-generation optimization (Batch 4 → Batch 6 → V2_GOLDEN)
- Single-generation Batch 8 signals lack the iterative refinement depth

### 5.2 Performance Gaps by Period

| Period Type         | Baseline_V2 Strength | P9_L3_EQUAL_Q Weakness         |
|---------------------|-----------------------|--------------------------------|
| SIDE-dominant (F2, SIDE_2) | +14.8% mean | +9.4% mean (-5.4pp)        |
| Early BULL (BULL_1, BULL_2) | +29.5% mean | +24.7% mean (-4.8pp)       |
| Mixed volatility (MIX_1)     | +45.2%      | +36.2% (-9.0pp, largest gap) |

**Hypothesis**: P9_L3_EQUAL_Q's equal-weight averaging dilutes the regime-specific strengths that made individual Batch 8 signals competitive in their target regimes. For instance:
- P8_BULL_DENSE's BULL offense (k=5 wb) gets averaged out
- P8_SIDE_V3's SIDE defense (k=2 ws) lacks sufficient weight
- P8_BALANCED's stability (k=3 wb) doesn't compensate for the dilution

### 5.3 Recent Fold Outperformance Paradox

**Why P9_L3_EQUAL_Q wins F3, F4, BULL_3, MIX_2 (+5pp each) but still fails overall**:

1. **Temporal Bias**: These folds (2023-2026) overlap with Batch 8 GA training window (up to 2026-03-31), creating in-sample advantage
2. **Regime Luck**: F3 and BULL_3 are high-momentum BULL periods where P8_BULL_DENSE's aggressive posture (learned from recent data) happens to work
3. **Insufficient Sample**: 2-4 good folds don't outweigh 4-6 underperforming folds across 15 years of history

**Implication**: Near-term live performance might be strong, but long-term OOS robustness is questionable.

---

## 6. Alternative Blending Strategies (Not Yet Tested)

### 6.1 Performance-Weighted Blending

**Concept**: Weight each signal by its regime-specific performance instead of equal-weighting.

```yaml
wb_weights:
  P8_BULL_DENSE: 0.50   # Best BULL performer (BULL_3 +49.7%)
  P8_SIDE_V3:    0.25
  P8_BALANCED:   0.25

ws_weights:
  P8_SIDE_V3:    0.60   # SIDE specialist
  P8_BULL_DENSE: 0.25
  P8_BALANCED:   0.15

wd_weights:
  P8_BALANCED:   0.60   # Most stable (k=4 wd)
  P8_SIDE_V3:    0.30
  P8_BULL_DENSE: 0.10
```

**Rationale**: Amplify regime-specific strengths instead of diluting them.

### 6.2 Hybrid Blending (Baseline_V2 Anchor)

**Concept**: Blend Batch 8 signals with Baseline_V2 to inherit its proven stability.

```yaml
wb_weights:
  Baseline_V2:   0.50   # Anchor
  P8_BULL_DENSE: 0.30   # BULL boost
  P8_BALANCED:   0.20

ws_weights:
  Baseline_V2:   0.60   # Proven SIDE defense
  P8_SIDE_V3:    0.40   # Modern SIDE specialist

wd_weights:
  Baseline_V2:   0.70   # Strong DEF profile
  P8_BALANCED:   0.30
```

**Rationale**: Leverage Baseline_V2's k=15 density and established robustness while injecting Batch 8's near-term alpha.

---

## 7. Conclusions

### 7.1 Direction A Verdict

❌ **L3 equal-weight blending does NOT achieve the primary goal**: surpassing Baseline_V2's mean CAGR while maintaining stability.

**Evidence**:
- **Default**: -1.3pp mean CAGR, G-A and G-C failed
- **Rolling**: -4.7pp mean CAGR (largest gap), G-B, G-C, G-F failed
- **Regime**: -1.5pp mean CAGR, G-A failed

### 7.2 Structural Takeaway

**The Baseline_V2 moat is deeper than expected**:
- Its k=15 density is not easily replicated by single-generation Batch 8 blending
- Equal-weighting dilutes regime-specific strengths without compensating diversity gains
- Multi-generation L3 refinement (V2_GOLDEN's lineage) provides a compounding advantage

### 7.3 Bright Spot

P9_L3_EQUAL_Q's **strong recent performance** (F3, F4, BULL_3, MIX_2 +5pp each) suggests:
- Near-term live alpha potential (next 6-12 months)
- Possible use as a "turbo mode" overlay for short tactical allocations
- But NOT a stable long-term baseline replacement

---

## 8. Recommended Next Steps

### Priority 1: Batch 9 (OOS Window Adjustment)

**Goal**: Generate new signals with a true OOS validation window (post-2026-03-31).

**Approach**:
- Restrict GA training to pre-2024-06-01 (create F4 as true OOS)
- Run 3-5 new candidates with Batch 8 learnings (feature pools, meta-search)
- Evaluate on extended regime fold-set (add 2026-2027 data as it arrives)

**Timeline**: High-priority, aligns with user's stated intent ("batch9으로 넘어갈 예정").

### Priority 2: Hybrid Blending Experiments (Optional)

**If user wants to exhaust Batch 8 materials before Batch 9**:
- Test performance-weighted blending (Section 6.1)
- Test Baseline_V2 anchor blending (Section 6.2)
- Estimated effort: 1-2 hours compute + eval

### Priority 3: Baseline_V2 Beta Fine-Tuning (Lower Priority)

**Goal**: Micro-optimize Baseline_V2's regime thresholds (VIX, profit_target) without full retraining.

**Rationale**: Already eliminated in earlier planning (user chose Direction A over Direction C), but remains an option if Batch 9 proves difficult.

---

## Appendix: Raw Data Links

- **Default fold-set**: `/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/t5_walk_forward_results_20260507_035405.json`
- **Rolling fold-set**: `/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/t5_walk_forward_results_20260507_040055.json`
- **Regime fold-set**: `/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/t5_walk_forward_results_20260507_035610.json`
- **Ensemble signal**: `/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P9_L3_EQUAL_Q_20260507_035153.npz`

---

**End of Report**  
Generated: 2026-05-07 03:52 AM (UTC+9)
