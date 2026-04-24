# Signal Feature Audit — Factor Tilt Decomposition

**Generated**: 2026-04-23T22:40:33
**Pack**: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
**Signals**: Baseline_V2, P5_RETRAIN, P5_RETRAIN_T1, P5_RETRAIN_T1b

**Purpose**: Identify *factor tilt* that explains Baseline_V2's BULL-regime CAGR advantage despite inferior mean IC (T5 Phase A diagnostic).

## 1. Active features — count & category breakdown

| Signal | #active | Technical short-horizon | Long-horizon momentum | Breakout / trend strength | Fundamental: value | Fundamental: quality | Fundamental: leverage | Fundamental: cash flow | Interaction composites | Σ\|wb\| | Σ\|ws\| | Σ\|wd\| |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** | 15 | 7 | 3 | 1 | 0 | 1 | 1 | 0 | 2 | 1.70 | 1.70 | 2.27 |
| **P5_RETRAIN** | 15 | 8 | 3 | 2 | 0 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| **P5_RETRAIN_T1** | 9 | 4 | 1 | 1 | 1 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| **P5_RETRAIN_T1b** | 11 | 4 | 2 | 1 | 1 | 1 | 1 | 1 | 0 | 1.00 | 1.00 | 1.00 |

## 2. Set analysis — who selects what

| Set | # | Features |
|---|---:|---|
| **Baseline-only** (baseline uses, 0 retrains) | 5 | BBP, ROC, HIGH_20_BREAK, QUAL_ROE_X_BREAKOUT_126, MOM_12M_EX1M_X_QUAL_ROE |
| **Retrain-consensus** (all 3 retrains, not baseline) | 1 | BREAKOUT_126 |
| **Shared-all** (all 4 signals) | 4 | SMA_CROSS, MOM_6M, QUAL_ROE, LEV_DEBT_EQUITY |
| **Rejected-all** (no signal uses) | 11 | ATR_LOW, BREAKOUT_252, SMA50_SLOPE, DIST_FROM_SMA50, VAL_BOOK2PRICE, QUAL_ROE_X_MOM_6M, VAL_EARN_YIELD_X_MOM_6M, CF_FCF_YIELD_X_MOM_6M, VAL_BOOK2PRICE_X_MOM_6M, BREAKOUT_252_X_CF_FCF_YIELD, LEV_DEBT_EQUITY_X_MOM_6M |

## 3. Per-regime category exposure — % of Σ|weight|

### BULL regime

| Signal | Technical short-horizon | Long-horizon momentum | Breakout / trend strength | Fundamental: value | Fundamental: quality | Fundamental: leverage | Fundamental: cash flow | Interaction composites |
|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** |   0.7% |  79.5% |   0.2% |   0.0% |   0.5% |   0.1% |   0.0% |  19.0% |
| **P5_RETRAIN** |  34.5% |  43.0% |   3.8% |   0.0% |  18.8% |   0.0% |   0.0% |   0.0% |
| **P5_RETRAIN_T1** |  45.6% |   4.4% |   4.4% |   5.6% |  40.0% |   0.0% |   0.0% |   0.0% |
| **P5_RETRAIN_T1b** |  40.0% |  16.2% |   2.9% |   0.0% |  38.3% |   0.0% |   2.6% |   0.0% |

### SIDE regime

| Signal | Technical short-horizon | Long-horizon momentum | Breakout / trend strength | Fundamental: value | Fundamental: quality | Fundamental: leverage | Fundamental: cash flow | Interaction composites |
|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** |  89.9% |   3.7% |   1.5% |   0.0% |   2.6% |   1.1% |   0.0% |   1.2% |
| **P5_RETRAIN** |  97.4% |   0.0% |   0.0% |   0.0% |   2.6% |   0.0% |   0.0% |   0.0% |
| **P5_RETRAIN_T1** |  66.5% |   0.0% |   0.0% |   0.0% |  33.5% |   0.0% |   0.0% |   0.0% |
| **P5_RETRAIN_T1b** |  94.0% |   0.0% |   0.0% |   6.0% |   0.0% |   0.0% |   0.0% |   0.0% |

### DEFENSIVE regime

| Signal | Technical short-horizon | Long-horizon momentum | Breakout / trend strength | Fundamental: value | Fundamental: quality | Fundamental: leverage | Fundamental: cash flow | Interaction composites |
|---|---|---|---|---|---|---|---|---|
| **Baseline_V2** |  60.9% |   1.4% |   0.8% |   0.0% |  12.9% |  23.0% |   0.0% |   0.9% |
| **P5_RETRAIN** |  59.5% |   0.0% |   0.0% |   0.0% |  16.9% |  23.6% |   0.0% |   0.0% |
| **P5_RETRAIN_T1** |   0.0% |   0.0% |   0.0% |   0.0% |  50.0% |  50.0% |   0.0% |   0.0% |
| **P5_RETRAIN_T1b** |   0.0% |   0.0% |   0.0% |   0.0% |  50.0% |  50.0% |   0.0% |   0.0% |

## 4. Full feature matrix (36 × 4 signals)

Active = ●   Inactive = ·   (wb = BULL weight)

| # | Category | Feature | Base active | P5 active | T1 active | T1b active | wb base | wb T1b |
|---|---|---|:---:|:---:|:---:|:---:|---|---|
|  0 | tech_short | `RSI` | · | · | · | ● | +0.000 | +0.000 |
|  1 | tech_short | `MACD` | ● | ● | · | · | +0.001 | +0.000 |
|  2 | tech_short | `SMA_CROSS` | ● | ● | ● | ● | +0.005 | +0.400 |
|  3 | tech_short | `BBP` | ● | · | · | · | +0.000 | +0.000 |
|  4 | tech_short | `CCI` | ● | ● | · | · | +0.002 | +0.000 |
|  5 | tech_short | `VOL_SPIKE` | · | ● | · | ● | +0.000 | +0.000 |
|  6 | tech_short | `STOCH` | ● | ● | · | ● | -0.001 | +0.000 |
|  7 | tech_short | `OBV_POS` | · | ● | · | · | +0.000 | +0.000 |
|  8 | tech_short | `ATR_LOW` | · | · | · | · | +0.000 | +0.000 |
|  9 | tech_short | `MFI` | · | ● | · | · | +0.000 | +0.000 |
| 10 | tech_short | `ADX` | · | · | ● | · | +0.000 | +0.000 |
| 11 | tech_short | `WILLR` | ● | ● | ● | · | +0.003 | +0.000 |
| 12 | tech_short | `ROC` | ● | · | · | · | +0.001 | +0.000 |
| 13 | tech_short | `VWAP_ABOVE` | · | · | ● | · | +0.000 | +0.000 |
| 14 | mom_long | `MOM_3M` | ● | ● | · | · | +0.677 | +0.000 |
| 15 | mom_long | `MOM_6M` | ● | ● | ● | ● | +0.656 | +0.033 |
| 16 | mom_long | `MOM_12M_EX1M` | ● | ● | · | ● | +0.020 | +0.129 |
| 17 | breakout | `BREAKOUT_252` | · | · | · | · | +0.000 | +0.000 |
| 18 | breakout | `RSI_TREND` | · | ● | · | · | +0.000 | +0.000 |
| 19 | breakout | `SMA50_SLOPE` | · | · | · | · | +0.000 | +0.000 |
| 20 | breakout | `BREAKOUT_126` | · | ● | ● | ● | +0.000 | +0.029 |
| 21 | breakout | `DIST_FROM_SMA50` | · | · | · | · | +0.000 | +0.000 |
| 22 | breakout | `HIGH_20_BREAK` | ● | · | · | · | +0.003 | +0.000 |
| 23 | fund_value | `VAL_EARN_YIELD` | · | · | ● | ● | +0.000 | +0.000 |
| 24 | fund_value | `VAL_BOOK2PRICE` | · | · | · | · | +0.000 | +0.000 |
| 25 | fund_quality | `QUAL_ROE` | ● | ● | ● | ● | -0.009 | +0.383 |
| 26 | fund_leverage | `LEV_DEBT_EQUITY` | ● | ● | ● | ● | -0.002 | +0.000 |
| 27 | fund_cashflow | `CF_FCF_YIELD` | · | · | · | ● | +0.000 | +0.026 |
| 28 | interact | `QUAL_ROE_X_MOM_6M` | · | · | · | · | +0.000 | +0.000 |
| 29 | interact | `VAL_EARN_YIELD_X_MOM_6M` | · | · | · | · | +0.000 | +0.000 |
| 30 | interact | `CF_FCF_YIELD_X_MOM_6M` | · | · | · | · | +0.000 | +0.000 |
| 31 | interact | `QUAL_ROE_X_BREAKOUT_126` | ● | · | · | · | +0.047 | +0.000 |
| 32 | interact | `VAL_BOOK2PRICE_X_MOM_6M` | · | · | · | · | +0.000 | +0.000 |
| 33 | interact | `MOM_12M_EX1M_X_QUAL_ROE` | ● | · | · | · | +0.277 | +0.000 |
| 34 | interact | `BREAKOUT_252_X_CF_FCF_YIELD` | · | · | · | · | +0.000 | +0.000 |
| 35 | interact | `LEV_DEBT_EQUITY_X_MOM_6M` | · | · | · | · | +0.000 | +0.000 |

## 5. BULL-regime weight delta (baseline − T1b) — shared active features

Rows with positive Δwb mean **baseline weighs this factor more heavily in BULL** than T1b. Negative Δwb means retrain weighs more heavily.  `BvsS_base = wb_base − ws_base` quantifies **how much baseline amplifies this factor in BULL vs SIDE** (internal tilt).

| Feature | Cat | wb base | wb T1b | Δwb | ws base | ws T1b | Δws | BvsS base | BvsS T1b |
|---|---|---|---|---|---|---|---|---|---|
| `MOM_6M` | mom_long | +0.656 | +0.033 | **+0.623** | -0.008 | +0.000 | -0.008 | +0.664 | +0.033 |
| `SMA_CROSS` | tech_short | +0.005 | +0.400 | **-0.395** | +0.436 | +0.400 | +0.036 | -0.431 | +0.000 |
| `QUAL_ROE` | fund_quality | -0.009 | +0.383 | **-0.391** | +0.044 | +0.000 | +0.044 | -0.053 | +0.383 |
| `MOM_12M_EX1M` | mom_long | +0.020 | +0.129 | **-0.109** | -0.021 | +0.000 | -0.021 | +0.041 | +0.129 |
| `LEV_DEBT_EQUITY` | fund_leverage | -0.002 | +0.000 | **-0.002** | -0.018 | +0.000 | -0.018 | +0.017 | +0.000 |
| `STOCH` | tech_short | -0.001 | +0.000 | **-0.001** | +0.596 | +0.400 | +0.196 | -0.597 | -0.400 |

## 6. Interpretation (automated scaffold)

- **Active count**: baseline uses 15, T1b uses 11.
- **Technical short-horizon**: baseline picks 7, T1b picks 4  (Δ = +3).
- **Long-horizon momentum**: baseline picks 3, T1b picks 2  (Δ = +1).
- **Fundamental: value**: baseline picks 0, T1b picks 1  (Δ = -1).
- **Fundamental: cash flow**: baseline picks 0, T1b picks 1  (Δ = -1).
- **Interaction composites**: baseline picks 2, T1b picks 0  (Δ = +2).

- **Baseline's internal BULL-vs-SIDE tilt (% points, top 3 categories)**:
    - Long-horizon momentum: **+75.7pp** (BULL exposure ↑ vs SIDE)
    - Interaction composites: **+17.8pp** (BULL exposure ↑ vs SIDE)
    - Fundamental: value: **+0.0pp** (BULL exposure ↑ vs SIDE)

- **Top 3 features with largest positive Δwb (baseline > T1b in BULL)**:
    - `MOM_6M` (mom_long): Δwb = +0.623

- **Top 3 features with largest negative Δwb (T1b > baseline in BULL)**:
    - `SMA_CROSS` (tech_short): Δwb = -0.395
    - `QUAL_ROE` (fund_quality): Δwb = -0.391
    - `MOM_12M_EX1M` (mom_long): Δwb = -0.109

---

## 7. Takeaway scaffold (human to fill in)

1. **Dominant factor family in baseline BULL**: see §3 BULL row, identify the 1-2 categories baseline allocates disproportionally high % to.
2. **Baseline-exclusive features**: see §2 baseline_only set. These are candidate features to *inject* into T1b (via mask union + weight copy).
3. **Shared features with wb_delta > 0**: baseline weighs them heavier in BULL; candidate for partial weight-blending (Option A in T2 blend).
4. **Pure BULL-specific**: features where `BvsS_base` is large positive but retrains keep small → asymmetric amplification is the baseline's trick.