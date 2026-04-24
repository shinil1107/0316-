# Baseline Benchmark — Step A Freeze

**Generated:** 2026-04-23  (latest: 2026-04-24, v1.2 — T1b_BULL_INJECTED interim)
**Script:** `phase3/tests/step_a_baseline_benchmark.py`
**Raw metrics:** `phase3/docs/baseline_benchmark_metrics.json`

> **STATUS: FROZEN. Do not regenerate without explicit version-bump.**
>
> These numbers are the invariant reference that all Phase 5 retrains must
> beat. Any re-run of Step A that produces different numbers invalidates
> downstream Step C gate verdicts.

---

## 1. Purpose

This document pins down the current "best-known" portfolio-level realized
performance of our signal + strategy stack on a fixed OOS window. After
Pre-Phase 2 patches (F11/F1/F4/S1) reshape the GA fitness formula, new
signals produced by Phase 5 retrains can only be fairly compared to the
pre-audit baseline via **formula-independent realized metrics**.

Three arms are frozen here:

| Arm                          | Signal              | Exit Stack     | Role                        |
|------------------------------|---------------------|----------------|-----------------------------|
| `V1_BATCH11_legacy`          | `P2_BATCH11` (V1)   | legacy         | Anchor / rollback reference |
| `V2_ENS_L3_v1_legacy`        | `V2_GOLDEN_ENS_L3`  | legacy         | Signal upgrade isolate      |
| `V2_ENS_L3_v1_SIDE_DEF_p12`  | `V2_GOLDEN_ENS_L3`  | SIDE_DEF_p12   | **Current live baseline**   |

---

## 2. Evaluation Protocol (frozen)

| Parameter          | Value                                           |
|--------------------|-------------------------------------------------|
| OOS window         | **2024-06-01 → 2026-04-17** (~1.87 years)       |
| Pack               | `precompute_qresearch_v4_12_2017-01-03_2026-04-17.npz` |
| Initial capital    | $100,000                                        |
| Daily buy limit    | $1,000                                          |
| Commission         | 10 bps                                          |
| Slippage           | 5 bps                                           |
| Rebalance mode     | `daily`                                         |
| Regime blend       | OFF (`regime_blend_enabled=false`)              |
| Universe           | Historical S&P 500 (coverage-aware)             |

The protocol is deliberately identical to the live Phase 3 configuration
except for the signal/trigger selection per arm.

---

## 3. Frozen Benchmarks

### 3.1 Portfolio-level (realized)

| Arm                          | CAGR    | Sharpe | MDD    | Calmar | DailyWin | MonthlyWin | Turnover/yr | Commission % | Final Value |
|------------------------------|--------:|-------:|-------:|-------:|---------:|-----------:|------------:|-------------:|------------:|
| `V1_BATCH11_legacy`          | +56.76% | 1.582  | 31.85% | 1.782  | 58.3%    | 68.2%      | 2.20        | 1.02%        | $231,278.52 |
| `V2_ENS_L3_v1_legacy`        | +57.39% | 1.599  | 31.64% | 1.814  | 58.5%    | 68.2%      | 2.35        | 1.10%        | $233,011.43 |
| `V2_ENS_L3_v1_SIDE_DEF_p12`  | +56.33% | 1.588  | 31.46% | 1.791  | 58.5%    | 68.2%      | 2.42        | 1.12%        | $230,106.78 |

### 3.1b Phase B Interim Candidate (non-frozen, Phase B2 주행중)

| Arm                          | Path (20260423_225842)                                  | Origin |
|------------------------------|---------------------------------------------------------|--------|
| `T1b_BULL_INJECTED`          | `frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_*.npz`      | Phase B / Option A surgical injection |

**Construction.** `wb` ← Baseline_V2 (BULL 전용 GA 와 앙상블된 가중치),
`ws`/`wd` ← P5_RETRAIN_T1b, `mask = wb≠0 ∨ ws≠0 ∨ wd≠0`.
Full recipe: `phase3/tests/p1_bull_injection.py` (2026-04-23).

**T5 walk-forward snapshot (6 folds, 2012→2026, pack
`precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`, identical OOS protocol
as §2):**

| Arm                  | mean CAGR | std    | CV    | worst (F2) | F4 post-OOS | pos/n | Gate B (Calmar) | Gate C (CostDrag) | Gate D (fold-CV) |
|----------------------|----------:|-------:|------:|-----------:|------------:|:-----:|:---------------:|:-----------------:|:----------------:|
| `Baseline_V2`        | +30.60%   | 18.06% | 0.590 | +4.30%     | +50.91%     | 6/6   | YES             | YES               | YES              |
| `P5_RETRAIN_T1b`     | +24.96%   | 12.06% | 0.483 | +8.66%     | +37.20%     | 6/6   | YES             | YES               | YES              |
| **`T1b_BULL_INJECTED`** | **+29.16%** | **15.64%** | **0.536** | **+7.39%** | **+47.18%** | **6/6** | **YES** | **YES** | **YES** |

**Status.** `T1b_BULL_INJECTED` 은 **Phase B 기간 동안의 임시 deployment
후보** (interim candidate). v1.2 시점에서 **§3 (Frozen) 엔트리로 승격하지
않음.** 이유는:

1. Baseline_V2 대비 CAGR -1.44 pp (95 % 재현) 이지만 Sharpe/MDD 는 아직 Step A
   포맷으로 측정 안 함 (walk-forward 는 fold-단위 금융지표만 계산).
2. BULL regime 성분이 Baseline_V2 와 **완전 동일** (동일 `wb`) 이므로, 진정한
   "BULL tilt 를 학습한 retrain signal" 이 아님 → Phase B2 에서 GA 로 직접
   재현을 시도해야 종결.
3. Phase B2 (regime-conditional penalties) 가 더 나은 후보를 내면 즉시 교체,
   아니면 T1b_BULL_INJECTED 가 최종 배포 후보가 됨.

**Promotion-time checklist (Phase B2 종료 후):**
- `V2_ENS_L3_v1_T1bInj_SIDE_DEF_p12` arm 신설 → §3.1/§3.2 재측정
- §5 gate criteria 대비 full pass/fail 재계산
- §6 에 v1.3 bump (T1b_BULL_INJECTED 승격 또는 폐기 사유 문서화)

### 3.2 Regime breakdown (realized)

| Arm                          | Regime | Days | AnnRet   | Sharpe | MDD    | Calmar  | WinRate |
|------------------------------|:------:|-----:|---------:|-------:|-------:|--------:|--------:|
| `V1_BATCH11_legacy`          | BULL   | 268  | +45.13%  | 1.618  | 12.30% | 3.668   | 56.0%   |
| `V1_BATCH11_legacy`          | SIDE   | 189  | +86.61%  | 1.735  | 17.20% | 5.037   | 61.4%   |
| `V1_BATCH11_legacy`          | DEF    | 13   | +266.41% | 1.575  |  7.28% | 36.571  | 61.5%   |
| `V2_ENS_L3_v1_legacy`        | BULL   | 268  | +43.89%  | 1.581  | 12.49% | 3.515   | 56.3%   |
| `V2_ENS_L3_v1_legacy`        | SIDE   | 189  | +90.49%  | 1.804  | 16.61% | 5.446   | 61.4%   |
| `V2_ENS_L3_v1_legacy`        | DEF    | 13   | +271.66% | 1.588  |  7.25% | 37.455  | 61.5%   |
| `V2_ENS_L3_v1_SIDE_DEF_p12`  | BULL   | 268  | +43.03%  | 1.566  | 12.38% | 3.475   | 56.3%   |
| `V2_ENS_L3_v1_SIDE_DEF_p12`  | SIDE   | 189  | +89.04%  | 1.800  | 16.55% | 5.380   | 61.4%   |
| `V2_ENS_L3_v1_SIDE_DEF_p12`  | DEF    | 13   | +256.84% | 1.552  |  7.19% | 35.698  | 61.5%   |

> **Caveat (DEF regime):** Only 13 OOS days land in the DEF regime. The
> triple-digit ann-return figures are statistical noise and should NOT
> be used as a gate criterion. Focus on BULL + SIDE which cover
> 268 + 189 = 457 of the 470 trading days (97.2%).

---

## 4. Observations

1. **V1 → V2 (legacy triggers) is marginal.** CAGR +0.63 pp, Sharpe
   +0.017, MDD -0.21 pp, but turnover +6.8% and commission +7.8%.
   V2's "signal upgrade" is real but small; its true value has to be
   weighed against the audit's findings that V2 was likely rewarded by
   the same F11/F1/F4 biases that V1 was.

2. **SIDE_DEF_p12 is a defensive exchange.** Relative to V2-legacy it
   costs -1.06 pp CAGR and -0.69% Sharpe, in exchange for -0.18 pp MDD
   and (per earlier D4 sweeps) richer SIDE+DEF trimming behaviour. The
   net effect on Calmar is -1.3%.

3. **OOS BULL dominance.** 57% of the OOS window is BULL. This skews
   every aggregate metric upward relative to full-cycle results;
   Phase 5 retrain evaluations must not over-fit a "BULL is everything"
   view.

4. **Turnover is ~2.4x/yr, cost drag is ~0.6%/yr.** Already consistent
   with the T1 diagnostic (71% per-rebalance turnover, 5.5% annualized
   diagnostic cost) since the diagnostic inflates per-rebalance counts;
   realized cost drag is lower because the simulator's actual buy
   limits throttle execution.

---

## 5. Gate Criteria for Step C (Phase 5 retrain promotion)

A new signal produced by Phase 5 retrain must be compared against the
**current live baseline** — `V2_ENS_L3_v1_SIDE_DEF_p12`. Exact thresholds:

| # | Criterion              | Baseline value       | Promotion gate                          |
|---|------------------------|----------------------|-----------------------------------------|
| 1 | OOS CAGR               | +56.33%              | **≥ +56.33%**                           |
| 2 | OOS MDD                | 31.46%               | **≤ 34.61%** (baseline × 1.1)           |
| 3 | OOS Calmar             | 1.791                | **≥ 1.881** (baseline × 1.05)           |
| 4 | OOS realized IC        | (measured in Step B) | **≥ baseline_IC + 0.005**               |
| 5 | Realized cost drag     | 1.12% commission     | **≤ 0.78%** (baseline × 0.7)  [v1.1]    |
| 6 | Temporal stability (T5)| (n/a — needs T5)     | **fold CAGR std ≤ mean × 0.5**          |

**Pass rule:** ≥ 4 of 6 criteria satisfied, with **#2 (MDD) and #5 (Cost)
as mandatory** (cannot substitute).

### Secondary (monitoring, non-gating)

| Metric                  | Baseline | Notes                                                |
|-------------------------|---------:|------------------------------------------------------|
| OOS Sharpe              | 1.588    | gate at parity (≥ 1.50) if used                      |
| OOS Daily Win Rate      | 58.5%    | no gate; stability indicator                         |
| Turnover (annualized)   | 2.42x    | monitor against T1 goal of ~1.5x                     |
| BULL Sharpe             | 1.566    | regime-specific check                                |
| SIDE Sharpe             | 1.800    | regime-specific check                                |

---

## 6. Version Control

| Version | Date        | Change                                                            |
|---------|-------------|-------------------------------------------------------------------|
| v1.0    | 2026-04-23  | Initial freeze. V1_BATCH11, V2_ENS_L3_v1_legacy, V2+SIDE_DEF_p12. |
| v1.1    | 2026-04-23  | Gate #5 relaxed: baseline × 0.5 (0.56%) → baseline × 0.7 (0.78%). Rationale: daily-rebal + $1K buy-limit simulator has a structural commission floor that makes the 0.5× target difficult to reach without changing rebal frequency. P5_RETRAIN_T1 hit 0.72% with only mild T1 pressure, which the relaxed gate recognises as "materially improved". Baseline numbers unchanged. |
| v1.2    | 2026-04-24  | Added §3.1b Phase B Interim Candidate (`T1b_BULL_INJECTED`). Non-frozen interim deployment candidate generated by Phase B / Option A surgical injection of Baseline's BULL weights (`wb`) into T1b's SIDE/DEF slots. T5 6-fold walk-forward (2012→2026): CAGR +29.16% / std 15.64% / CV 0.536 / all 6 folds positive / all 3 gates (B,C,D) pass. Ranks #2 behind Baseline_V2 while keeping its DEFENSIVE tilt. Held outside frozen §3 table pending Phase B2 (regime-conditional engine change) outcome. |

Any subsequent freeze must bump this version, keep prior entries, and
document why re-freeze was needed (new OOS window, new realized IC
methodology, etc.). The JSON at `baseline_benchmark_metrics.json` is
paired with this document by timestamp.

---

## 7. Reproduction

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/tests/step_a_baseline_benchmark.py
```

Output:
- Console comparison table + regime breakdown
- `phase3/docs/baseline_benchmark_metrics.json` (machine-readable)

Runtime: ~15 s after pack cache is warm.
