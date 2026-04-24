# Phase B2 — Regime-Conditional Deployment Penalty (Option 3a) Plan

**Status:** Active (Night 2 target)
**Drafted:** 2026-04-24
**Engine change:** committed to notebook Cell 0 (see §3)
**Batch:** 3 (6 presets, ~12 h overnight)
**Previous batch:** Phase B Batch 1 (scalar profile sweep) — finalized
2026-04-24, see `phase_b_batch_plan.md`.

---

## 1. Why Phase B2

Batch 1 (scalar sweep) outcomes (6-fold T5 walk-forward,
`t5_walk_forward_results_20260424_064258.json`):

| Arm                  | mean CAGR | F2 (SIDE-heavy) | F4 post-OOS | All-gates? |
|----------------------|----------:|----------------:|------------:|:----------:|
| `Baseline_V2`        | +30.60%   | +4.30%          | +50.91%     | YES        |
| `T1b_BULL_INJECTED`  | +29.16%   | +7.39%          | +47.18%     | YES        |
| `P5B_CONSV` (0.40)   | +22.32%   | +1.23%          | +33.95%     | weak       |
| `P5B_PROP`  (0.15)   | +22.83%   | +1.69%          | +38.76%     | weak       |
| `P5B_AGGR`  (0.05)   | +30.06%   | +2.14%          | +51.31%     | **SIDE col.** |

**Observation.** Lowering scalar `w_turnover` / `w_cost` recovers BULL
(F3/F4 CAGR climbs to +51%) but collapses SIDE-dominant folds (F2 drops
to +1-2 %). No Pareto-improvement over `T1b_BULL_INJECTED` was found.
The scalar sweep exhausts its ability to resolve the trade-off because
one weight governs all three regimes simultaneously.

**Hypothesis.** The BULL-concentration Baseline_V2 exploits is *incompatible*
with high-turnover SIDE churn — both drive the same `_dep_turnover`
scalar, so the GA cannot reward BULL concentration without also
rewarding excessive SIDE rotation. A **regime-conditional** penalty
decouples the two: near-zero BULL drag + strong SIDE/DEFENSIVE drag.

---

## 2. Option 3a — MINIMAL Engine Modification

Three options were considered:

| Option | Surface area | Risk | Runtime cost | Exposes |
|--------|-------------:|:----:|:------------:|:--------|
| **3a (MINIMAL)** | +1 helper fn, +6 Config fields, ~30 lines in evaluator | low | 0 % (same compute path) | Per-regime `w_turnover_{bull,side,def}`, `w_cost_{bull,side,def}` |
| 3b (FULL)  | refactor evaluator loop, rebalance-scheduling per regime | medium | +5-10 % | True per-regime rebalance windows |
| 3c (MAXIMAL) | GA fitness rewrite, multi-objective front | high | +20 % | Pareto front with MDD/IC/cost axes |

**Chosen: 3a.** Highest ROI for this specific failure mode (F2 SIDE
collapse) while preserving byte-identical back-compat when all six new
overrides are `None`.

### 2.1 Engine changes (already committed)

`0315 windows이사.ipynb` Cell 0:

1. **Config** — added 6 `Optional[float] = None` fields:
   `w_turnover_bull/side/def`, `w_cost_bull/side/def`.
2. **New helper** — `_compute_deployment_penalties_per_regime(dict, top_n, cost_bps, eval_freq) → (turnovers, cost_drags, counts)` for 3 regimes.
3. **Evaluator** — in `evaluate_individual_qresearch`, picks are routed
   into `_top_n_deploy_by_regime` **alongside** the legacy flat
   `_top_n_deploy_list` (kept for byte-identical scalar-only path).
4. **Fitness combiner** — if *any* of the 6 new fields is set, use the
   per-regime helper and blend:
   ```
   to_pen = w_to_bull*to_rates[BULL] + w_to_side*to_rates[SIDE] + w_to_def*to_rates[DEFENSIVE]
   cost_pen = (analogous)
   ```
   Unset fields fall back to `cfg.w_turnover` / `cfg.w_cost` scalar.
5. **Meta-dict** — added 9 diagnostics
   (`deployment_regime_conditional`, `_turnover_{bull,side,def}`,
   `_cost_drag_{bull,side,def}`, `_rebal_count_{bull,side,def}`).

### 2.2 Back-compat guarantee

- All 6 overrides `None` (current default) → `_regime_cond = False` →
  the legacy `_compute_deployment_penalties` runs on the flat list
  with the exact same inputs as before. Fitness is **bit-for-bit
  identical** to v<1.2.
- Scalar-only `cfg.w_turnover = X, cfg.w_cost = Y` unchanged.

### 2.3 Smoke-test (passed 2026-04-24)

```
[OK] Config has all 6 new fields defaulting to None
[OK] per-regime turnovers = {'BULL': 0.3, 'SIDE': 0.6, 'DEFENSIVE': 0.0}
[OK] per-regime cost_drag = {'BULL': 0.01404, 'SIDE': 0.01872, 'DEFENSIVE': 0.0}
[OK] legacy scalar path    = to=0.5500 cost=0.0429
[OK] scalar-only cfg construction works
[OK] regime-conditional cfg construction works
```

---

## 3. Batch 3 — 6 Preset Design

All presets share:
- train window: **2012-01-03 → 2024-05-31** (base, fixed — regime axis
  only)
- GA seed: **20260501** (`SEED_C`, distinct from A / B)
- all other Config flags identical to `T1b` / `run_phase5_retrain.py`
  (patched formula F1/F4/F11/S1, BULL biases all ON, stability 5 seeds,
  pop 100/300/300, final 300×20)

### 3.1 A-axis — BULL-concentration tier

| id | tag | `w_turnover (B,S,D)` | `w_cost (B,S,D)` | intent |
|---|---|---|---|---|
| `mild` | `P5C_MILD` | (0.10, 0.30, 0.40) | (0.05, 0.20, 0.25) | BULL relaxed, SIDE/DEF at T1b baseline |
| `balanced` | `P5C_BALANCED` | (0.15, 0.25, 0.35) | (0.10, 0.15, 0.20) | Regime-tier version of P5B_PROP |
| `deep` | `P5C_DEEP` | (0.00, 0.40, 0.50) | (0.00, 0.25, 0.30) | BULL fully free + aggressive SIDE/DEF suppression |

### 3.2 B-axis — protection-direction tier

| id | tag | `w_turnover (B,S,D)` | `w_cost (B,S,D)` | intent |
|---|---|---|---|---|
| `bull_free` | `P5C_BULL_FREE` | (0.00, 0.30, 0.40) | (0.00, 0.20, 0.25) | MILD with BULL absolute zero |
| `def_heavy` | `P5C_DEF_HEAVY` | (0.05, 0.15, 0.60) | (0.02, 0.10, 0.35) | Isolate DEF as primary cost driver |
| `side_heavy` | `P5C_SIDE_HEAVY` | (0.05, 0.50, 0.30) | (0.02, 0.30, 0.15) | Direct attack on F2 SIDE collapse |

### 3.3 Hypotheses

| Preset | Expected behaviour | Success criterion (vs `T1b_BULL_INJECTED`) |
|---|---|---|
| `mild` | Modest BULL recovery, small SIDE loss | mean CAGR ≥ 28%, F2 ≥ 6% |
| `balanced` | Middle ground; likely closest to T1b_INJ | mean CAGR ≥ 29%, F2 ≥ 6% |
| `deep` | Highest BULL (Baseline-like) if BULL tilt is learnable here | mean CAGR ≥ 30%, F2 ≥ 4% |
| `bull_free` | Like `mild` with stronger BULL | mean CAGR ≥ 29%, F2 ≥ 5% |
| `def_heavy` | Tests whether DEF (not SIDE) dominates cost | F2 ≥ 6% **and** F0b/F3 improved |
| `side_heavy` | If `side_heavy.F2 > mild.F2` → SIDE churn is the real problem, not DEF | F2 ≥ 8% |

**Winner rule.** Pareto-dominance over `T1b_BULL_INJECTED` on the joint
(mean CAGR, F2, MDD-proxy std, all-gates pass) criterion. If multiple
Pareto-dominate, pick the one with the highest F4 (post-OOS) CAGR.

### 3.4 Why 6 presets?

The 2-D design space (BULL relaxation × SIDE/DEF emphasis) gains more
from diverse sampling than from fine-grained 1-D sweeps. 6 runs cover:

- 3 orthogonal BULL-relaxation levels (A-axis: 0.00 / 0.10 / 0.15)
- 3 orthogonal protection directions (B-axis: balanced / DEF-only / SIDE-only)

Adjacent presets share one coordinate so differences attributable to a
single axis are identifiable.

---

## 4. Runtime Budget

| Preset | Base GA (100/300/300 × 8/12, final 300×20) | Expected |
|---|---:|---:|
| 1 preset | ~2.0–2.5 h (confirmed Batch 1 actuals: 2.0–2.1 h) | ~2 h |
| 6 presets sequential | 6 × ~2 h | **~12 h** |

One overnight session fits. Sleep-while-runs pattern: start after 20:00,
completes before 08:00 next morning. Fail-soft orchestrator ensures one
stuck preset does not block the remaining ones.

---

## 5. Operational Runbook (Night 2)

### 5.1 Pre-launch verification (1 min)

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/run_phase5_batch_b.py --list | grep "\[B3\]"
# expect 6 lines: mild, balanced, deep, bull_free, def_heavy, side_heavy
```

### 5.2 Launch

**Option A — Launcher UI (recommended):**

1. Open Phase 3 launcher.
2. Click `T27 Phase B Batch (B1/B2/B3)`.
3. Select **"Batch 3 only (6× Phase B2 regime-cond) — NEXT OVERNIGHT (~12 h)"**.
4. Leave "Force rebuild training pack" **unchecked** (pack is cached).
5. Click **Start**.

**Option B — CLI:**

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
python3 -u phase3/run_phase5_batch_b.py --batch 3
```

### 5.3 Monitor (optional, during run)

```bash
ls -lt phase3/docs/phase_b_batch_progress_*.json | head -1
# inspect the latest progress JSON for `current.tag`, `completed`, `failed`
```

### 5.4 Morning verification

```bash
ls -lt /Users/shin-il/Documents/my\ stock/cache_fmp_c2_1/output/frozen_signal_P5C_*.npz
# expect 6 new npz files, timestamps within the overnight window
```

Expected filenames (timestamps vary):
- `frozen_signal_P5C_MILD_<stamp>.npz`
- `frozen_signal_P5C_BALANCED_<stamp>.npz`
- `frozen_signal_P5C_DEEP_<stamp>.npz`
- `frozen_signal_P5C_BULL_FREE_<stamp>.npz`
- `frozen_signal_P5C_DEF_HEAVY_<stamp>.npz`
- `frozen_signal_P5C_SIDE_HEAVY_<stamp>.npz`

### 5.5 Morning evaluation (~5 min)

1. Register the 6 signal paths in `phase3/tests/step_d_walk_forward.py`.
2. Run T5 walk-forward on all (Baseline_V2, T1b_BULL_INJECTED, +6 new).
3. Compute the Pareto-dominance check per §3.3.
4. Update `baseline_benchmark.md` with the winner (v1.3 bump).

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|:-:|:-:|---|
| Engine change regresses scalar runs | low | high | §2.2 byte-identical guarantee + smoke test passed |
| All 6 presets collapse SIDE | med | med | `side_heavy` is a direct stress-test; at least one of (mild, balanced, side_heavy) should hold F2 ≥ 6% |
| BULL-free presets lose IC entirely | med | low | Baseline/T1b_INJ both already perform; worst case we keep T1b_INJ as interim |
| Any single GA run hangs | low | low | fail-soft orchestrator continues; the stuck run is logged |
| Overnight runs crash / reboot | very low | med | `run_phase5_batch_b.command` `&`-launch supports resume via `--runs` subset |

---

## 7. Decision Gates

### 7.1 After Batch 3 morning eval

| Case | Decision |
|---|---|
| ≥ 1 preset Pareto-dominates `T1b_BULL_INJECTED` | Promote winner to Step A baseline candidate; update `baseline_benchmark.md` v1.3; consider Step C full gate re-run |
| 0 dominate, but closest preset within 1.0 pp CAGR & Sharpe | Treat as Phase-B-complete; **keep T1b_BULL_INJECTED** as final deployment candidate; escalate to Phase C (signal blending) |
| All 6 underperform T1b_INJ | Engine 3a is insufficient — escalate to 3b (per-regime rebalance scheduling) or freeze T1b_INJ and pivot to T2 (signal-level blending) |

### 7.2 Scope boundaries

**Out of scope for Phase B2:**
- Per-regime rebalance windows (that's 3b)
- Multi-objective Pareto front (3c)
- Adaptive entropy_bonus per regime (was considered, rejected — orthogonal axis)
- Changing stability seed budget (5 seeds; keep fixed to isolate penalty effect)

---

## 8. Version Log

| Version | Date | Change |
|---------|------|--------|
| v1.0    | 2026-04-24 | Initial draft post Batch 1 eval. Captures Option 3a engine changes (notebook Cell 0) + 6 Batch-3 presets + Night 2 runbook. |
