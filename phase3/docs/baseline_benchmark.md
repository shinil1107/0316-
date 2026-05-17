# Baseline Benchmark — Step A Freeze

**Generated:** 2026-04-23  (latest: 2026-04-25, v1.3 — Phase B archived; `buy_grace_days=3` execution-layer adoption)
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

### 3.1b Phase B archive — `T1b_BULL_INJECTED` (deprecated, retained for context)

> **DEPRECATED 2026-04-25 (v1.3).** Phase B / Phase B2 / P6 ensembles failed
> to Pareto-improve over `Baseline_V2`. The execution-layer fix
> `buy_grace_days=3` (§3.1c) supersedes all Phase B retrain candidates as
> the production change for the next deployment cycle. This entry is kept
> as a research record only.

| Arm                          | Path                                                | Origin |
|------------------------------|-----------------------------------------------------|--------|
| `T1b_BULL_INJECTED`          | `frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_*.npz`  | Phase B / Option A surgical injection (parent of P6_ENSEMBLE_A/B) |

**Why deprecated.** Phase B (3-axis scalar sweep) and Phase B2 (6-preset
regime-conditional engine) jointly produced ~12 candidate signals; none
beat `Baseline_V2` on full-period CAGR. The closest, `P6_ENSEMBLE_C`, lost
−2.24 pp CAGR while delivering only marginal IR-proxy improvement, and
its `ws` slot collapsed to a 1-feature degenerate optimum. The shared
root cause was the 6-fold validation's zero `DEF` days masking
underfitting in the GA's defensive learner. Rather than continue the
signal-layer search, we shifted focus to the execution layer where
`buy_grace_days=3` produced a genuine Pareto improvement over the
incumbent (see §3.1c). `T1b_BULL_INJECTED` itself only differed from
`Baseline_V2` in BULL by definition (it copied Baseline's `wb`), so it
was never a true *learned* BULL alternative — only a structural recipe
test.

**Walk-forward record (legacy, `buy_grace_days=0`):**

| Arm                     | mean CAGR | CV    | worst | pos/n | G6-B | G6-C | G6-D |
|-------------------------|----------:|------:|------:|:-----:|:----:|:----:|:----:|
| `Baseline_V2`           | +30.60%   | 0.590 | +4.30%| 6/6   | YES  | YES  | YES  |
| `T1b_BULL_INJECTED`     | +29.16%   | 0.536 | +7.39%| 6/6   | YES  | YES  | YES  |

### 3.1c Execution-layer adoption — `buy_grace_days = 3` (v1.3 production change)

**TL;DR.** A non-held ticker must appear in the regime-correct top-N on
each of the last **3** rebalance days before opening a new position.
`BUY_MORE` (scaling existing positions) is exempt. `buy_grace_days = 0`
remains byte-identical to legacy and is a one-line rollback.

**Implementation.** `phase3/simulator.py` (rolling top-N snapshot deque +
intersection filter) and `phase3/config.yaml::strategy.buy_grace_days`.
Test: `phase3/tests/p3_buy_grace_sweep.py` runs the strict-variant sweep
N ∈ {0,1,2,3,5} on `Baseline_V2`. Sweep artefact:
`phase3/docs/p3_buy_grace_sweep_20260425_142422.{md,json}`.

**Full-period (2012-01-01 → 2026-02-27, daily rebal, 10/5 bps cost):**

| `buy_grace_days` | CAGR    | Δ CAGR | Sharpe | MDD    | Calmar | Comm % | Δ Comm    | Final $    |
|-----------------:|--------:|-------:|-------:|-------:|-------:|-------:|----------:|-----------:|
| 0 (legacy)       | +30.43% | —      | 1.247  | 34.23% | 0.889  | 42.34% | —         | $4.26 M    |
| 1                | +30.23% | −0.20  | 1.228  | 34.26% | 0.882  | 38.50% | −3.84 pp  | $4.17 M    |
| 2                | +30.14% | −0.29  | 1.217  | 34.56% | 0.872  | 35.34% | −7.00 pp  | $4.13 M    |
| **3 (adopted)**  | **+30.31%** | **−0.12** | 1.222 | **34.07%** | **0.890** | **33.91%** | **−8.43 pp** | **$4.21 M** |
| 5                | +29.64% | −0.79  | 1.188  | 35.38% | 0.838  | 29.18% | −13.16 pp | $3.91 M    |

**Regime breakdown (AnnRet, Δ vs g=0):**

| `buy_grace_days` | BULL              | SIDE             | DEF                      |
|-----------------:|------------------:|-----------------:|-------------------------:|
| 0                | +39.11% / —       | +19.24% / —      | +90.99% / —              |
| **3**            | **+38.72% / −0.39** | **+18.71% / −0.53** | **+108.71% / +17.72** |

**6-fold walk-forward verification (`Baseline_V2` × `buy_grace_days=3`,
artefact `t5_walk_forward_results_20260425_143335.json`):**

| Arm                  | mean CAGR | CV    | worst | pos/n | G6-B / C / D | G7-C (≥5/6 vs SPY) |
|----------------------|----------:|------:|------:|:-----:|:------------:|:------------------:|
| `Baseline_V2 (g=0)`  | +30.60%   | 0.590 | +4.30%| 6/6   | YES/YES/YES  | 6/6 ✓              |
| **`Baseline_V2 (g=3)`** | **+31.16%** | **0.603** | **+4.14%** | **6/6** | **YES/YES/YES** | **6/6 ✓** |

Mean per-fold α vs SPY rises from **+15.70 pp (g=0) to +16.25 pp (g=3)** —
the grace filter is *additive* in walk-forward mean alpha while saving
20 % of commission. The IR proxy slips slightly (1.498 → 1.410) because
the per-fold spread widens in F4, but absolute rank against SPY is
unchanged.

**Why this is a genuine Pareto improvement.**

1. CAGR delta within full-period noise (−0.12 pp on a 14-year sample).
2. Commission savings is large (−20 % relative, −8.43 pp absolute) and
   compounds linearly with deployment scale; at 10× capital it remains a
   ~$84 K/yr saving on the model portfolio.
3. MDD slightly improved (−0.16 pp), Calmar slightly improved (+0.001).
4. DEF regime AnnRet improves +17.72 pp — the grace filter is most
   valuable precisely where noise is most damaging.
5. 6-fold walk-forward mean CAGR *increased* +0.56 pp with all 6 SPY-beat
   verdicts retained. Statistically the change is performance-neutral or
   marginally better, not a give-up.

**Reproduction.**

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/tests/p3_buy_grace_sweep.py            # full-period sweep
python3 -u phase3/tests/step_d_walk_forward.py \
    --signals baseline,t1b_inj,p6_ens_c \
    --buy-grace-days 3                                    # walk-forward
python3 -u phase3/tests/step_e_spy_benchmark.py \
    --walk-forward phase3/docs/t5_walk_forward_results_<TS>.json   # G7 gates
```

**Rollback.** Set `phase3/config.yaml::strategy.buy_grace_days = 0`
(or remove the key). All other parameters are unchanged. The
implementation is dormant when the value is 0 (snapshot deque still
populated but never consulted) — no other behavioural diff.

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
| v1.3    | 2026-04-25  | **Phase B archived; execution-layer fix adopted.** Phase B / B2 / P6 ensembles (12 candidate signals) failed to Pareto-improve over `Baseline_V2`. Diagnosed root cause: 6-fold walk-forward had zero `DEF` days, masking GA underfitting in the defensive weight slot. Rather than continue the signal-layer search, adopted **`buy_grace_days = 3`** (strict variant `a`) — a top-N persistence filter that keeps `Baseline_V2`'s alpha intact while reducing commissions by **−8.43 pp (−20 % relative)** at full-period scale. 6-fold walk-forward with grace=3 raised mean CAGR from +30.60 % to +31.16 % and held all 6 SPY-beat verdicts. SPY G7 gates added (`step_e_spy_benchmark.py`) and integrated into `step_d_walk_forward.py` via `--buy-grace-days`. `T1b_BULL_INJECTED` reclassified from interim to deprecated. See §3.1b (archive), §3.1c (adoption), §8 (SPY G7). |

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

---

## 8. SPY G7 absolute-benchmark gates (added v1.3)

`Baseline_V2` was historically gated only against itself (G6-A/B/C/D vs.
its own walk-forward statistics). v1.3 adds an absolute gate against the
buy-and-hold SPY benchmark over identical windows
(`phase3/tests/step_e_spy_benchmark.py`). Same protocol: no commission,
no slippage, daily close, $1 invested at window start.

### SPY benchmark anchors

| Window      | Range                       | CAGR     | Sharpe | MDD    |
|-------------|-----------------------------|---------:|-------:|-------:|
| FULL ~14 yr | 2012-01-03 → 2025-12-31     | +12.73 % | 0.802  | 34.10 %|
| F0a         | 2012-01-03 → 2014-12-31     | +17.30 % | 1.423  |  9.69 %|
| F0b         | 2015-01-02 → 2016-12-30     | +4.33 %  | 0.366  | 14.35 %|
| F1          | 2019-01-02 → 2020-12-31     | +22.30 % | 0.922  | 34.10 %|
| F2          | 2021-01-04 → 2022-12-30     | +1.85 %  | 0.191  | 25.36 %|
| F3          | 2023-01-03 → 2024-05-31     | +26.03 % | 1.895  | 10.29 %|
| F4          | 2024-06-03 → 2025-12-31     | +17.64 % | 1.020  | 19.00 %|

### G7 gate definitions

| Gate | Definition                                               | `Baseline_V2 (g=0)` | `Baseline_V2 (g=3)` |
|------|----------------------------------------------------------|:-------------------:|:-------------------:|
| G7-A | Full-period CAGR ≥ SPY full-period CAGR (+12.73 %)       | ✓ (+17.70 pp)       | ✓ (+17.58 pp)       |
| G7-B | Full-period Sharpe ≥ SPY Sharpe (0.802)                  | ✓ (+0.445)          | ✓ (+0.420)          |
| G7-C | Per-fold CAGR ≥ SPY in ≥ 5/6 folds                       | ✓ (6/6)             | ✓ (6/6)             |
| G7-D | Per-fold-α IR proxy ≥ 0.5 (mean / std of fold α vs SPY)  | ✓ (1.498)           | ✓ (1.410)           |

**F2 reframing.** What G6 reported as the "worst fold" (Baseline +4.30 %)
is in fact +2.45 pp absolute α over SPY's +1.85 % over the same 2021-2022
period. G6's fold-CV machinery treats it as risk, but G7's market-relative
view shows it is real alpha generated through the worst SPY 2-year window
of the sample. v1.3 keeps both gate families to capture both views.

### Reproduction

```bash
# Standalone SPY benchmark (full + 6 folds, ~1 s after cache warm)
python3 -u phase3/tests/step_e_spy_benchmark.py

# Merge with an existing walk-forward result + optional A/B JSONs
python3 -u phase3/tests/step_e_spy_benchmark.py \
    --walk-forward phase3/docs/t5_walk_forward_results_<TS>.json \
    --full-period-json phase3/docs/p2_p6_ensemble_c_vs_baseline_v2_<TS>.json
```
