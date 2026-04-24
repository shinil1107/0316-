# Phase 5 — Retrain Plan (Stability-Only, OOS-Safe)

**Generated:** 2026-04-23
**Status:** READY — awaiting user to launch GA via `run_phase5_retrain.command`
**Upstream decisions:** see transcript (Step A freeze + Pre-Phase 2 Patches F1/F4/F11/S1)
**Downstream:** `step_c_gate_evaluation.py` vs. `baseline_benchmark.md`

---

## 1. Goal

Retrain the signal under the **patched GA formula** (F1+F4+F11+S1), on a
data window that excludes the Step A OOS holdout, so that the resulting
`frozen_signal_P5_RETRAIN_*.npz` can be evaluated against the frozen
baseline with no training-time contamination.

| Dimension          | Before (V2_GOLDEN_ENS_L3_v1) | Phase 5 retrain                 |
|--------------------|------------------------------|----------------------------------|
| GA formula         | pre-audit (F1=0.08, F11=no mask, F4=global entropy, S1=False) | **patched** (F1=0.04, F11=tradable mask, F4=per-regime, S1=True) |
| Train end          | 2026-03-02 (full history)    | **2024-05-31** (OOS holdout starts 2024-06-01) |
| Meta search        | ON (6 trials)                | **OFF** (single template, stability-only) |
| Stability layer    | ON (9 seeds)                 | **ON (5 seeds)**                 |
| BULL-only biases   | ON                           | **OFF** (Q2 — T2 re-observation) |

---

## 2. Configuration (exact overrides applied by `run_phase5_retrain.py`)

```python
# OOS holdout — 2024-06-01 onward is invariant test set
start_panel_date = datetime(2017, 2, 21)   # engine default; 7+ yr burn-in
end_date         = datetime(2024, 5, 31)   # train cutoff

# Universe — match V1/V2 training recipe (historical S&P 500 expansion)
enable_historical_universe          = True
historical_universe_expand_tickers  = True
enable_coverage_based_universe      = True
enable_panel_cache_fallback_download = False

# Patched formula flags (confirmed defaults post-patch)
entropy_bonus              = 0.04   # F1
enable_cs_rank_features    = True   # S1
# F4 (per-regime entropy/conc_pen) and F11 (tradable mask on spread) are
# code-path patches with no flag — already active whenever evaluate_individual_qresearch runs.

# GA fitness recipe — matched to V1/V2 chain for apples-to-apples
top_quantile                = 0.12
w_ic1                       = 0.34
w_ic3                       = 0.34
w_spread                    = 0.32
factor_corr_penalty_lambda  = 0.10
conc_penalty                = 0.12
weight_cap                  = 0.40
enable_fitness_risk_penalty = True
fitness_downside_vol_lambda = 0.50
fitness_max_neg_spread_ratio_lambda = 0.30

# Q2 — BULL-only biases OFF (observe T2 redesign starting point)
enable_bull_floor_penalty         = False
enable_bull_spread_bonus          = False
enable_bull_factor_min_constraint = False
enable_side_soft_bias             = False

# Meta OFF, stability ON
enable_meta_search            = False
meta_disabled_template_name   = "TPL_BALANCED"
enable_stability_layer        = True
stability_seed_runs           = 5        # user-approved
stability_top_n_seeds         = 4
stability_fast_population     = 100
stability_fast_generations    = 8
stability_refine_population   = 300
stability_refine_generations  = 12

# Final GA after stability refine
ga_population   = 300
ga_generations  = 20

# Reproducibility — fixed seed for deterministic stability behaviour
use_random_seed = False
ga_seed         = 20260428

# Reports / paths (inherit from engine default + config.yaml)
save_dir       = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
fmp_cache_root = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1"
excel_prefix   = "SP500_P5_RETRAIN"
```

### Budget estimate

| Stage                | Calc                       | Evals  |
|----------------------|----------------------------|-------:|
| Stability — fast     | 5 seeds × 100 × 8          |  4,000 |
| Stability — refine   | 300 × 12                   |  3,600 |
| Final GA             | 300 × 20                   |  6,000 |
| **Total**            |                            | **~13,600** |

@ ~0.7–0.9 s/eval (mac M-series) ≈ **2.5–3.4 hours** wall clock.

---

## 3. Runbook — "one-click" execution

### Option A (preferred) — Finder / double-click

Double-click `phase3/run_phase5_retrain.command`. A Terminal window opens
and executes:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/run_phase5_retrain.py
```

Progress streams to the window. When the GA finishes, the last line prints:

```
[P5_RETRAIN] frozen signal saved → /Users/…/output/frozen_signal_P5_RETRAIN_<stamp>.npz
```

### Option B — from Jupyter

Paste into any empty cell of `0315 windows이사.ipynb` and Run:

```python
%run phase3/run_phase5_retrain.py
```

(uses the same Python process as the notebook; output appears inline).

### Option C — terminal

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/run_phase5_retrain.py
```

### Smoke test first (recommended, 1–2 min)

```bash
python3 -u phase3/run_phase5_retrain.py --dry-run
```

Runs one stability fast seed with `ga_population=20, ga_generations=3`
to validate that the engine+pack+cfg combo is wired correctly before
committing to the full 3 h run.

---

## 4. Artifacts produced

| Path                                                                                   | Purpose                                           |
|----------------------------------------------------------------------------------------|---------------------------------------------------|
| `.../output/precompute_qresearch_v4_12_2017-02-21_2024-05-31.npz`                      | Training pack (auto-built if missing, ≤5 min)     |
| `.../output/frozen_signal_P5_RETRAIN_<stamp>.npz`                                      | **Phase 5 signal** (input to Step C)              |
| `.../output/SP500_P5_RETRAIN_<stamp>.xlsx` *(only if reports=True)*                    | GA internals, seed-stability, factor usage        |
| `phase3/docs/phase5_retrain_log_<stamp>.json`                                          | Human-readable run summary (cfg diff, seed, MeanIC/Spread/PosICRatio, wall time) |

After GA completes, **paste the `frozen_signal_P5_RETRAIN_*.npz` path to
Cursor** and Step C will run automatically.

---

## 5. Step C — gate evaluation (post-GA)

Command:

```bash
python3 -u phase3/tests/step_c_gate_evaluation.py \
    --signal /Users/.../output/frozen_signal_P5_RETRAIN_<stamp>.npz \
    --arm-name P5_RETRAIN
```

What it does:

1. Loads the Phase 5 signal.
2. Runs `simulator.run_simulation` over **2024-06-01 → pack_end**
   with the `SIDE_DEF_p12` trigger stack (current live baseline).
3. Computes realized metrics (CAGR / MDD / Calmar / Sharpe / Turnover /
   Commission % / OOS realized IC).
4. Compares against `baseline_benchmark_metrics.json :: V2_ENS_L3_v1_SIDE_DEF_p12`.
5. Emits a **Gate Report** with the 6 criteria from
   `baseline_benchmark.md §5`:

   | # | Criterion                 | Gate                               | Mandatory? |
   |---|---------------------------|------------------------------------|:----------:|
   | 1 | OOS CAGR                  | ≥ +56.33%                          |            |
   | 2 | OOS MDD                   | ≤ 34.61%                           |    yes     |
   | 3 | OOS Calmar                | ≥ 1.881                            |            |
   | 4 | OOS realized IC           | ≥ baseline_IC + 0.005              |            |
   | 5 | Realized cost drag        | ≤ 0.56%                            |    yes     |
   | 6 | Temporal stability (T5)   | fold CAGR std ≤ mean × 0.5         |            |

   **Pass rule:** ≥ 4 of 6 AND both mandatory (#2, #5) satisfied.

   *(Criterion #6 (T5 walk-forward) is deferred — Step C emits
   "DEFERRED" for it. The 4-of-6 rule still applies on the remaining 5,
   i.e. effectively "≥ 3 of 5 with both mandatory".)*

6. Appends a row to `phase3/docs/phase5_step_c_results.jsonl` for
   historical comparison if you retrain multiple times.

---

## 6. Decision matrix after Step C

| Verdict | Meaning                                               | Next action                                   |
|---------|-------------------------------------------------------|-----------------------------------------------|
| PROMOTE | ≥ 4/6 AND #2 + #5 pass                                | Update `phase3/config.yaml :: paths.frozen_signal`. Cutover memo. |
| HOLD    | 2–3 of 6 OR missing exactly one mandatory             | Diagnose which gate missed. Re-run with adjusted budget (refine_population ↑) or formula tweak. |
| REJECT  | ≤ 1 of 6 OR missing both mandatories                  | Revisit Pre-Phase 2 Patches; the retrain did not reproduce baseline quality. Escalate.          |

No decisions are executed automatically — Step C only reports; promotion
requires a separate user-confirmed config edit.

---

## 7. Why stability-only (no meta search)?

Per user decision (transcript): meta-search costs ≈ 4× more evaluations
for marginal Sharpe gains on previous batches. The audit-patched
formula already compresses the search space (F1 lowered entropy weight,
F4 localizes diversity pressure per regime). A **single template +
5-seed stability** is a cheap-but-strong starting point:

- If Phase 5 passes the gate → meta search was unnecessary (we save 4×
  compute on all future retrains).
- If Phase 5 marginally misses → re-run with meta ON (keep the same
  pack — all deltas are formula-free).

This "retrain small first, escalate second" ordering is consistent
with the transcript's Q1 approach ("gradual entropy lowering") and
Q5 ("caller mask before deeper refactor").

---

## 8. Version Control

| Version | Date        | Change                                          |
|---------|-------------|-------------------------------------------------|
| v1.0    | 2026-04-23  | Initial plan. Stability-only, 2024-05-31 cutoff, 5 seeds. |

Any deviation from this plan at execution time (e.g. meta turned ON,
different cutoff) MUST be documented as v1.1 before relaunching — the
Step C verdict is only meaningful against the configuration recorded
here.
