# Phase B — Scalar-Sweep Batch (6-run overnight) Plan

**Status**: APPROVED (v1, 2026-04-23)
**Owner**: shin-il
**Parent plan**: `t1_phase2_deployment_tuning_plan.md` (Phase 2 iter2 `P5_RETRAIN_T1b`)
**Prerequisite evidence**: `t5_walk_forward_results_20260423_230025.md` (Option A — BULL tilt transplant)

---

## 1. Motivation

The T5 walk-forward confirmed `P5_RETRAIN_T1b` is *healthier*
than `Baseline_V2` (CV 0.483 vs 0.590, worst-fold +8.66% vs +4.30%,
mean IC +0.0149 vs +0.0036) but gives up **5.64 pp of mean CAGR**
(+24.96% vs +30.60%). The P1 signal audit traced the gap to a
*concentrated long-horizon momentum tilt in Baseline's BULL weights*,
which the T1b fitness had suppressed via the scalar deployment
penalty (`w_turnover=0.3`, `w_cost=0.2`).

P1 Option A (surgical BULL-weight transplant) recovered **74%** of
the CAGR gap (+29.16% vs +24.96%) with only mild SIDE-fold regression
(F2 −1.27 pp, F0b −1.16 pp), proving the hypothesis: **the BULL tilt
is a real, transferable factor exposure gated by the scalar turnover
penalty**.

Phase B operationalises this empirical finding directly in the GA
by sweeping the scalar `w_turnover` / `w_cost` while keeping every
other component of the patched formula (F1/F4/F11/S1, BULL biases,
stability layer, alpha floor) identical to T1b.

## 2. Key engineering decision — scalar-only sweep

`_compute_deployment_penalties()` (notebook L5419-5456) aggregates
turnover across *all* rebalances into a single scalar and applies
`w_turnover` / `w_cost` uniformly. **There is no regime-conditional
path today.** Building one would require

1. Tracking per-rebalance regime labels inside the evaluator loop
2. Splitting `top_n_picks_per_reb` by regime
3. Adding `w_turnover_BULL/SIDE/DEF` fields to `Config`
4. Re-wiring the fitness summation

→ Non-trivial, and carries non-zero risk of breaking the fitness
byte-compatibility chain that T1b was validated against.

Because `regime_weight_bull=0.65` already concentrates 65% of the
IC/Spread reward in BULL, lowering the *scalar* `w_turnover`
disproportionately benefits BULL (where turnover is intrinsically
higher due to momentum rotation). This is exactly the mechanism
Option A reproduces mechanically. Therefore we commit to a
**scalar sweep** for overnight Phase B, with the regime-conditional
engine change deferred to a follow-up (Phase B2) only if the scalar
sweep fails to find a Pareto-improvement over T1b.

## 3. Design — 6 presets

### 3.1 Batch 1: profile sweep on base window

All three runs use the approved window (2012-01-03 → 2024-05-31,
OOS-safe cutoff) and GA seed `20260428` (same as T1b), so the only
differences are `w_turnover` / `w_cost`.

| id    | tag           | w_turnover | w_cost | ratio vs T1b | intent                               |
|-------|---------------|-----------:|-------:|-------------:|--------------------------------------|
| consv | `P5B_CONSV`   |       0.40 |   0.25 |        1.33× | Upper-bound stability test           |
| prop  | `P5B_PROP`    |       0.15 |   0.10 |        0.50× | Option-A mimic; balanced candidate   |
| aggr  | `P5B_AGGR`    |       0.05 |   0.03 |        0.17× | Near-baseline loosening              |

### 3.2 Batch 2: window sweep on `PROP` profile

All three runs use `w_turnover=0.15, w_cost=0.10` and a distinct
GA seed `20260429` (intentionally different from Batch 1 to also
surface seed-variance information).

| id       | tag             | train window                    | delta          | intent                                     |
|----------|-----------------|----------------------------------|---------------:|--------------------------------------------|
| win_base | `P5B_WIN_BASE`  | 2012-01-03 → 2024-05-31          |  base          | Seed-variance re-run of PROP               |
| win_fwd  | `P5B_WIN_FWD`   | 2013-01-03 → 2025-05-31          | +1y forward    | Adds 2025 BULL tape                        |
| win_back | `P5B_WIN_BACK`  | 2011-01-03 → 2023-05-31          | −1y backward   | Adds 2011-2013 recovery tape               |

Each window defines its own training pack; `engine.prepare_inputs`
caches packs per `(start_panel_date, end_date)` so rebuilds happen
automatically (~5-8 min each when the FMP cache already covers the
range, verified via the 2011+ data-availability audit).

## 4. What is held constant across all 6 runs

| Component                        | Value                                     |
|---------------------------------|-------------------------------------------|
| GA formula patches              | F1 (entropy=0.04) + F4 (per-regime) + F11 (tradable mask) + S1 (cs_rank) |
| BULL biases                     | All 6 ON (Config default)                 |
| `top_quantile`                  | 0.12                                      |
| `w_ic1 / w_ic3 / w_spread`      | 0.34 / 0.34 / 0.32                        |
| `factor_corr_penalty_lambda`    | 0.10                                      |
| `conc_penalty`                  | 0.12                                      |
| `weight_cap`                    | 0.40                                      |
| Meta search                     | OFF (`TPL_BALANCED` template)             |
| Stability layer                 | 5 seeds · fast 100×8 · refine 300×12      |
| Final GA                        | population 300 × 20 generations           |
| Alpha floor                     | 0.22                                      |
| `deployment_top_n`              | 30                                        |
| `deployment_cost_bps`           | 15.0                                      |
| `enable_deployment_penalty`     | True                                      |

## 5. Runtime budget

Single GA run ≈ 2.5 – 3 h on Apple Silicon (M-series) plus 5 – 8 min
for pack rebuild when the window cache is cold. Total for 6 runs
sequential: **≈ 15 – 18 hours**. Split as 3 + 3 across two nights
(`--batch 1` then `--batch 2`) to stay within a realistic overnight
window and to make Batch 2 **adaptive** on Batch 1's winner if we
later decide to override the default PROP profile in Batch 2.

## 6. Execution — recommended 2-night flow

### Night 1 — Batch 1 (profile sweep)

```
cd /Users/shin-il/PyCharmMiscProject/0316-
./phase3/run_phase5_batch_b.command 1
```

Runs `P5B_CONSV`, `P5B_PROP`, `P5B_AGGR` back-to-back. Expected
finish: +9 h from start.

### Morning — evaluate

```
python3 -u phase3/tests/step_d_walk_forward.py \
    --signals baseline,t1b,t1b_inj,p5b_consv,p5b_prop,p5b_aggr
```

(Add the 3 new signal paths to `SIGNALS` in `step_d_walk_forward.py`
before running.)

Pick the winner using the G6-B/C/D gate stack + IC stability:

- If `PROP` dominates → Night 2 proceeds as planned (PROP on 3 windows).
- If `AGGR` dominates → **Override** Batch 2 to use AGGR profile
  (edit `run_phase5_batch_b.py::BATCH_CONFIGS` entries `win_*`
  `overrides` dict to `{"w_turnover": 0.05, "w_cost": 0.03}`)
  before Night 2 kicks off.
- If `CONSV` dominates → **Abort Phase B**; revisit whether BULL
  tilt recovery is feasible under current fitness structure.

### Night 2 — Batch 2 (window sweep on winner)

```
./phase3/run_phase5_batch_b.command 2
```

Runs `P5B_WIN_BASE`, `P5B_WIN_FWD`, `P5B_WIN_BACK` back-to-back.

### Morning — final evaluation

Run 6-fold walk-forward against all 9 signals (baseline, T1b, T1b_INJ,
3 Batch-1 signals, 3 Batch-2 signals). The window-sweep folds
(`win_fwd`, `win_back`) will be evaluated *including* their previously
out-of-sample pre-train folds; expect slight re-classification of
folds F0a/F0b/F4 between the 3 window variants and document in the
final report.

## 7. Fail-soft behaviour & progress log

On each preset, the orchestrator

1. Writes `phase3/docs/phase_b_batch_progress_<stamp>.json` *before*
   starting so the user can `tail -n0 -f` to see progress.
2. Captures any Exception, logs traceback, and **continues** to the
   next preset. This avoids a single failure (e.g., a transient FMP
   cache miss) killing the whole overnight.
3. Updates the progress JSON after each preset with `status=ok|failed`,
   `elapsed_sec`, frozen-signal path, run-log path.

## 8. Launcher integration

`launcher.py` gets a new button `T27 Phase B Batch (scalar sweep)`
next to T25/T26 that opens a popup with

- Radio: Batch 1 / Batch 2 / Full (1+2) / Dry-run smoke
- Confirm → spawns `run_phase5_batch_b.command <arg>` via `_run_task`
  (same plumbing as T25 / T26).

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Scalar sweep finds no Pareto-improvement over T1b | Trigger Phase B2 — regime-conditional engine change. Scope already drafted in §2. |
| Long wall-time pins the laptop (~18 h total) | Run overnight split 3+3. `caffeinate -i` recommended (see §10). |
| Pack rebuild failures (FMP cache gap) | `rebuild_pack_walk_forward.py` was validated over 2011+; the three Phase B windows stay inside that range. Fail-soft skips affected runs. |
| Baseline's BULL advantage is ensemble-scale | Out of scope for Phase B (single-GA). Phase B3 would rebuild BULL_GA + ensemble as baseline did — queued but not in this plan. |
| PROP choice may be suboptimal for Batch 2 | Adaptive override in Night-2 morning (see §6). |

## 10. Operational notes

- Recommend prefix with `caffeinate -i -s` on mac to prevent display
  sleep killing a long run:
  `caffeinate -i -s ./phase3/run_phase5_batch_b.command 1`
- Progress JSON file (`phase_b_batch_progress_<stamp>.json`) exposes
  `current` + `completed` + `failed` — safe to inspect while running.
- Each successful preset auto-writes its own per-run log via the
  existing `run_phase5_retrain.py` → `docs/phase5_retrain_log_*.json`
  chain; these are unchanged Phase-5 artefacts.

## 11. Post-Phase-B decision record (to be filled)

After Night 2, capture

- Winner(s) that PASS G6-A/B/C/D vs baseline
- Any that PASS while also improving CAGR vs T1b
- Whether to promote to temporary baseline candidate
  (updating `config.yaml::paths.frozen_signal` or
   `regime_signal_paths.BULL`)
- Whether Phase B2 (regime-conditional engine change) is still needed

## 12. Change log

| Date         | Version | Change |
|--------------|---------|--------|
| 2026-04-23   | v1      | Initial plan, user approved Q-A (proposed ratios), Q-B (T1b_BULL_INJECTED as temp baseline candidate), Q-C (Y+Z split), 6-run scalar sweep (3+3) |
