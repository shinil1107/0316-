# OOS-Clean Baseline Breakthrough Findings — 2026-05-08

**Author**: quant-research
**Status**: validated, shadow run launched
**Trigger**: identification of `Baseline_V2`'s in-sample lookahead inflation
(P2_BATCH11 trained through 2026-03-02; "OOS" was strategy-OOS only).

---

## TL;DR

1. `Baseline_V2`'s F4 CAGR (+47.23%) is **+16.9pp inflated** vs the same
   training-config-OOS-cut version (`P2_BATCH11_OOS`, +30.28%).
2. When we re-baseline the hardgate to `P2_BATCH11_OOS` (signal-OOS clean),
   **5 OOS-clean candidates pass 2/3 fold-set hardgates** — the same score
   `Baseline_V2` itself scores against this fairer baseline.
3. The strongest candidate is **`P11_OOS_CLEAN_L3_FUNDB_ANCHOR`**
   (P5E_FUND_BRK : P5D_SIDE_DEEP : P2_BATCH11_OOS = 2:1:1 L3 ensemble),
   passing default+regime hardgates and trailing rolling only by +0.7pp CV.
4. Shadow run started 2026-05-08 (30d). On pass, advance to soft swap.
5. `P5E_SIDE_FUND_BREAKOUT` is the strongest *single* OOS-clean signal:
   Lift_10d 1.76x > V2's 1.72x; F4 CAGR +46.41%.
6. ML signals (XGB v15/v16) score 0/3 on OOS-clean hardgates — magnitude
   of mean CAGR gap (~9pp) is too large for current architecture; need
   ML-2.0 (regime-specific submodels) or feature-engineering breakthrough.

---

## 1. Lookahead bias quantification

| | P2_BATCH11 (V2 member) | P2_BATCH11_OOS | Δ |
|---|---|---|---|
| Train end | 2026-03-02 | 2024-05-31 | -21 months |
| F4 CAGR | +47.23% | +30.28% | **-16.9pp** |
| Mean CAGR (default) | +31.40% | +21.46% | -9.9pp |
| F0a CAGR (V2 pre-train) | +27.41% | +28.18% | +0.8pp |

V2 vs P2_OOS in F0a is *flat* — V2's extra 21 months of training did **not**
improve its true extrapolation, only its in-sample retrieval on F1/F3/F4.

---

## 2. Signal-OOS hardgate evaluation (baseline = `P2_BATCH11_OOS`)

`step_d_walk_forward.py --baseline p2_oos --fold-set {default,rolling,regime}`

### default (6-fold) — V2 in-sample for all folds

| Signal | mean | CV | worst | Hardgate |
|---|---|---|---|---|
| Baseline_V2 (inflated) | +31.1% | 0.464 | +12.1% | ✓ ALL |
| **P2_BATCH11_OOS** (baseline) | +21.5% | 0.429 | +6.0% | ✓ ALL (auto) |
| **P11_FUNDB_ANCHOR** | **+26.9%** | 0.473 | **+12.1%** | **✓ ALL** ★ |
| P11_OOS_CLEAN_L3_EQ | +23.7% | 0.485 | +9.9% | ✗ G-A |
| P11_REGIME_SPEC | +22.8% | 0.526 | +7.3% | ✗ G-A |
| P5E_SIDE_FUND_BREAKOUT | +27.0% | 0.508 | +8.0% | ✗ G-A (0.508 > 0.479) |
| P5D_SIDE_DEEP | +23.8% | 0.459 | +4.8% | ✗ G-C (4.8 < 5.0) |
| P5E_SIDE_TECH | +21.2% | 0.436 | +5.0% | ✗ G-C |
| ML_XGB_v16 | +18.0% | 0.398 | +9.2% | ✗ G-B (18.0 < 19.4) |
| ML_XGB_v15 | +17.5% | 0.426 | +7.7% | ✗ G-B |

### rolling (8×8yr sliding)

| Signal | mean | CV | worst | Hardgate |
|---|---|---|---|---|
| Baseline_V2 (inflated) | +27.2% | 0.194 | +19.9% | **✗ G-A** (CV 0.194 > 0.172) |
| **P2_BATCH11_OOS** (baseline) | +22.8% | 0.122 | +17.8% | ✓ ALL (auto) |
| P11_FUNDB_ANCHOR | +26.4% | 0.179 | +19.6% | ✗ G-A (0.179 > 0.172) ⚠ marginal |
| **P11_OOS_CLEAN_L3_EQ** | +24.5% | 0.152 | +18.9% | **✓ ALL** |
| **P11_REGIME_SPEC** | +24.7% | 0.163 | +18.9% | **✓ ALL** |
| **P5E_SIDE_FUND_BREAKOUT** | +26.9% | 0.146 | +21.7% | **✓ ALL** ★ |
| **P5D_SIDE_DEEP** | +25.9% | 0.141 | +20.9% | **✓ ALL** |
| **P5C_DEF_HEAVY** | +22.2% | 0.132 | +17.2% | **✓ ALL** |
| ML_XGB_v15/v16 | +14.5% / +15.2% | 0.222 / 0.264 | +7.9% / +8.9% | ✗ G-A,B |

### regime (BULL/SIDE/MIX 7-fold)

| Signal | mean | CV | Hardgate |
|---|---|---|---|
| Baseline_V2 (inflated) | +30.06% | 0.528 | ✓ ALL |
| **P2_BATCH11_OOS** (baseline) | +21.18% | 0.570 | ✓ ALL (auto) |
| **P11_FUNDB_ANCHOR** | **+26.21%** | **0.516** | **✓ ALL** ★ |
| **P11_OOS_CLEAN_L3_EQ** | +23.35% | 0.534 | **✓ ALL** |
| **P11_REGIME_SPEC** | +22.66% | 0.579 | **✓ ALL** |
| **P5E_SIDE_FUND_BREAKOUT** | +26.78% | 0.539 | **✓ ALL** |
| **P5D_SIDE_DEEP** | +24.36% | 0.529 | **✓ ALL** |
| **P5E_SIDE_TECH** | +21.63% | 0.547 | **✓ ALL** |
| P5C_DEF_HEAVY/BALANCED | +23.66% | 0.694 | ✗ G-A |
| ML_XGB_v15/v16 | +17.99% / +18.75% | 0.556 / 0.548 | ✗ G-B |

### Aggregate score

| Signal | default | rolling | regime | **Score** |
|---|:---:|:---:|:---:|:---:|
| Baseline_V2 (inflated) | ✓ | **✗** | ✓ | 2/3 |
| **P11_FUNDB_ANCHOR** | **✓** | ✗* | **✓** | **2/3** |
| **P5E_SIDE_FUND_BREAKOUT** | ✗ | **✓** | **✓** | **2/3** |
| **P11_OOS_CLEAN_L3_EQ** | ✗ | **✓** | **✓** | **2/3** |
| **P11_REGIME_SPEC** | ✗ | **✓** | **✓** | **2/3** |
| **P5D_SIDE_DEEP** | ✗ | **✓** | **✓** | **2/3** |
| P5C_DEF_HEAVY | ✗ | ✓ | ✗ | 1/3 |
| P5E_SIDE_TECH | ✗ | ✗ | ✓ | 1/3 |
| ML_XGB_v15 / v16 | ✗ | ✗ | ✗ | 0/3 |

*P11_FUNDB_ANCHOR rolling fail by 0.7pp on G-A (CV 0.179 vs 0.172 threshold).

**No signal scores 3/3. V2 itself is 2/3. Per `hardgate_spec_v1.md`, all
2/3 candidates are "CONDITIONAL — manual review required".**

---

## 3. Surge capture (G-H, soft gate)

10-day horizon, +20% threshold (`surge_score_analysis.py`):

| Signal | Lift_10d | vs V2 (1.72x) | Q5/Q1 ratio | Notes |
|---|---|---|---|---|
| Baseline_V2 | 1.72x | — | 0.65/0.51 = 1.27 | reference |
| **P5E_SIDE_FUND_BREAKOUT** | **1.76x** | +0.04x ✓ | 0.69/0.61 = 1.13 | **stronger** |
| P5D_SIDE_DEEP | 1.66x | -0.06x ✓ | 0.65/0.62 = 1.05 | OK |
| P5C_BALANCED | 1.57x | -0.15x ✓ | 0.63/0.67 = 0.94 | OK |
| P5E_SIDE_TECH | 1.55x | -0.17x ✓ | 0.59/0.83 = 0.71 | inverted! |
| P5_RETRAIN_T1b | 1.32x | -0.40x ✗ | 0.55/0.67 = 0.82 | weak |
| P2_BATCH11_OOS | 1.24x | -0.48x ✗ | 0.60/0.44 = 1.36 | **weak** |
| P5C_DEF_HEAVY | 1.22x | -0.50x ✗ | 0.48/0.89 = 0.54 | inverted |

**Insight**: P2_BATCH11_OOS has good ranking-monotonicity (Q5/Q1=1.36) but
weak top-decile lift. P5E_FUND_BRK uniquely *exceeds* V2 on lift, indicating
genuine surge-capture alpha — not just defensible rank ordering.

---

## 4. Why P5E / P5D outperform — Factor structure (Q3 insights)

| Slot | V2m_P2_BATCH11 (in-sample) | **P5E_FUND_BRK** | **P5D_SIDE_DEEP** |
|---|---|---|---|
| BULL top | MOM_3M=0.40, MOM_6M=0.40 | MOM_6M+SMA_CROSS+DIST_FROM_SMA50 (3-way 33%) | SMA_CROSS=0.35, QUAL_ROE=0.26, SMA50_SLOPE=0.21 |
| SIDE top | STOCH=0.40, SMA_CROSS=0.29 | **VAL_BOOK2PRICE=0.5, CF_FCF_YIELD=0.5** | **VAL_BOOK2PRICE=0.5, CF_FCF_YIELD=0.5** |
| DEF top | WILLR=0.30, LEV_DEBT_EQUITY=0.24 | **LEV_DEBT_EQUITY=0.5, QUAL_ROE=0.5** | **LEV_DEBT_EQUITY=0.5, QUAL_ROE=0.5** |

### Insight #1 — fundamental factors (BookToPrice + FCF_Yield, LevDebtEquity + ROE) are the **real OOS alpha**

Multiple independent GA runs (P5/P5D/P5E with different seeds and configs) all
converge to identical SIDE/DEF factor pairs. This is not random — it's a true
gradient signal in the post-financials-backfill data. V2 (trained 2026-04-06,
before the backfill) captures fundamentals only weakly.

### Insight #2 — V2's MOM_3M+MOM_6M dominance (80%) is the **lookahead inflation channel**

V2's BULL slot is 80% simple short-term momentum. The training data's last 21
months (2024-06 to 2026-03) was momentum-strong, so the GA over-weighted it.
On true OOS forward periods this exposure is fragile.

### Insight #3 — P5E_FUND_BRK's diversified BULL is *why* it survives F4

P5E uses MOM_6M + SMA_CROSS + DIST_FROM_SMA50 in BULL (3-way 33%) — momentum
+ trend + distance. This generalises across regime shifts where V2's pure
MOM exposure does not.

### Insight #4 — V2's "more recent training reflects current trends" is a **fiction**

V2's F0a CAGR (+27%) ≈ P5E's F0a CAGR (+24%) — when both are pre-train, V2
has no advantage. V2's edge is purely in-sample retrieval, not generalisation.

---

## 5. Inflation-adjusted comparison

If V2's mean CAGR is inflated by ~9.9pp (default fold-set, mean) — its
"true OOS expectation" is **+21.5%** (which exactly matches P2_BATCH11_OOS,
the same model retrained to OOS-clean).

Against this corrected V2:

| Candidate | Mean CAGR | vs corrected V2 (+21.5%) |
|---|---|---|
| **P5E_SIDE_FUND_BREAKOUT** | +26.9% | **+5.4pp ★** |
| **P11_FUNDB_ANCHOR** | +26.5% | **+5.0pp ★** |
| **P5D_SIDE_DEEP** | +24.7% | **+3.2pp** |
| Baseline_V2 (corrected) | +21.5% | 0.0pp |

→ The OOS-clean candidates beat V2 substantially when V2's inflation is
removed. The remaining decision is risk: how much to trust the GA's signal-
OOS extrapolation, given that V2's recent training data may genuinely contain
relevant pattern shifts (impossible to verify without more time).

---

## 6. Decision: shadow run launched

**Shadow signal**: `P11_OOS_CLEAN_L3_FUNDB_ANCHOR`
**Start**: 2026-05-08
**Duration**: 30 calendar days
**Config**: `phase3/config.yaml` updated.
**Protocol**: `phase3/docs/shadow_to_live_promotion_protocol.md`

**Why P11_FUNDB_ANCHOR over P5E single signal**:
- Same hardgate score (2/3) as V2 with OOS-clean baseline
- Default fold-set CV 0.473 (vs P5E single 0.508) — better stability
- Diversification: P5E:P5D:P2_OOS = 2:1:1 reduces single-model risk
- Mean CAGR essentially tied (+26.5% vs +26.9%)

---

## 7. Open follow-ups

1. **Ensemble W' (P11_W_v2)**: try regime-specialised weights — wb anchored
   on P2_OOS (composite momentum BULL strength), ws/wd on P5E (fundamental
   value). Potentially further reduces F4 risk.
2. **ML-2.0**: regime-specific submodels (one ranker per regime), meta-blender.
   Current monolithic XGBRanker can't close the 9pp gap to GA on its own.
3. **More OOS-clean sources**: scan P6/P7/P8 batches for any signals with
   `train_end ≤ 2024-05-31`. Some Phase 6 retrains may also be eligible.
4. **Re-baseline hardgate spec**: formalise the dual-baseline evaluation
   (hardgate vs both V2 AND P2_OOS) in `hardgate_spec_v1.md` v1.3.
