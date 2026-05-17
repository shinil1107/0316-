# Phase B2.1 — SIDE Specialist Family

**Status**: prepared, awaiting execution budget (overnight, ~6.5–7.5 h)
**Author**: 2026-04-25
**Predecessors**: Phase B (`phase_b_batch_plan.md`), Phase B2 (`phase_b2_regime_cond_plan.md`)
**Successor (planned)**: SIDE-component ensemble injection (analog of `T1b_BULL_INJECTED`)

## 1. Motivation

V2_GOLDEN's full-period regime decomposition (per `baseline_v2_1_side_def_p12.md`):

| Regime | AnnRet | Days | Note |
|---|---:|---:|---|
| BULL | **+62.4%** | 1820 | strongest |
| **SIDE** | **+18.7%** | 1290 | **weakest** |
| DEFENSIVE | +43.1% | 290 | OK |

The SIDE regime is V2's structural soft spot. Walk-forward fold F2 (2021-01 → 2022-12, predominantly SIDE) collapsed to **+4.14% CAGR** vs the +31% pooled mean. This single-fold collapse drives ~½ of the CV(0.60) instability that gates G6-A (CV≤0.5) keeps failing.

We tried two execution-layer fixes:
1. `buy_grace_days=3` — adopted (small turnover/MDD win, ≈0 SIDE alpha lift).
2. `vt_S_18` (regime-conditional vol-targeting) — full-period showed +0.48 pp CAGR but **6-fold walk-forward (today, 2026-04-25)**: mean Δ = **−0.07 pp**, F0a Δ = −0.33 pp, F4 Δ = −0.22 pp. Engagement was real (e.g. F2: 266 SIDE rebal days w/ vt active) but the SIDE deleveraging cancelled out across folds. **Verdict: vt_S_18 does NOT generalise — not promoted.**

Conclusion: the SIDE gap is a **signal-quality** problem, not an execution-layer problem. We need a fresh GA-trained signal that is structurally optimised for SIDE and can be injected as the SIDE component of an ensemble — exactly the recipe used for `T1b_BULL_INJECTED` (BULL specialist).

## 2. Design

Three complementary GA presets spanning the SIDE-specialisation design space, each ~2.0–2.5 h.  All share `SEED_D = 20260502`, base GA recipe from `run_phase5_retrain.py` (BULL biases ON, patched fitness, stability layer 5×, ga_population=300/gen=20).

### 2.1 Penalty matrix

`_rc(w_to_bsd, w_co_bsd)` builds per-regime overrides for `w_turnover` / `w_cost`. Tuple = (BULL, SIDE, DEFENSIVE).

| id          | w_turnover (B/S/D) | w_cost (B/S/D)     | window                | hypothesis |
|-------------|--------------------|--------------------|-----------------------|------------|
| `side_pure` | (0.00, 0.70, 0.00) | (0.00, 0.40, 0.00) | 2012-01-03 → 2024-05-31 | Maximum SIDE concentration. BULL/DEF turnover zeroed → GA gradient is dominated by SIDE-fold IC and SIDE-fold churn. Risk: signal degenerates outside SIDE. |
| `side_deep` | (0.02, 0.60, 0.20) | (0.01, 0.35, 0.10) | 2012-01-03 → 2024-05-31 | Harder SIDE penalty than `P5C_SIDE_HEAVY` (0.50/0.30) but preserves token BULL/DEF awareness so the signal stays a sane all-rounder. |
| `side_win`  | (0.05, 0.50, 0.30) | (0.02, 0.30, 0.15) | **2014-01-03** → 2024-05-31 | Re-train the previously best-performing SIDE-heavy recipe on a SIDE-richer window (drops the 2012-13 BULL recovery tape that biases factor selection toward momentum). |

### 2.2 Why these three

- `side_pure` is the **upper bound** ("what does pure SIDE optimisation look like?"). Likely overfits, but tells us the ceiling.
- `side_deep` is the **proposed best** ("calibrated harder SIDE within all-rounder constraint").
- `side_win` is the **window-shift control** ("does the previous best recipe improve when fed a SIDE-richer training distribution?"). Independent dimension from the others — useful for ensembling.

### 2.3 Full-period vs walk-forward acceptance

We will judge each preset using both:
1. **Full-period** (Step C gate set): CAGR / Sharpe / MDD / Calmar / Comm-of-Cap / **regime-by-regime AnnRet table** (most important: SIDE AnnRet).
2. **Walk-forward** (`step_d_walk_forward.py` — t26): per-fold CAGR with primary attention on F2 (the SIDE-collapse fold) and F0b (also SIDE-heavy).

Promotion criterion (any preset):
- F2 CAGR(cand) ≥ F2 CAGR(V2) + 4 pp (vs +4.14% V2 → +8% target)
- Full-period SIDE AnnRet ≥ +25% (vs V2 +18.7%)
- Other regimes' AnnRet ≥ V2 × 0.85 (don't blow up BULL/DEF)
- Combined CAGR ≥ V2 × 0.92 (acceptable as ensemble component, not standalone)

If no preset standalone satisfies, the best one becomes the **SIDE-injection candidate** for the ensemble composer.

## 3. Execution

### 3.1 Budget
- 3 presets × ~2.2 h (typical Phase-B run on Apple Silicon) = ~6.6 h overnight.
- Add ~30 min for pack rebuild if cache is cold.
- Add 5 min × 3 for Step C gate eval per signal.

### 3.2 Trigger
```bash
# Full Batch 4 only
python3 -u phase3/run_phase5_batch_b.py --batch 4

# Or single preset
python3 -u phase3/run_phase5_batch_b.py --runs side_deep

# Smoke first (12 min)
python3 -u phase3/run_phase5_batch_b.py --batch 4 --dry-run
```

### 3.3 Output artifacts (per preset)
- `frozen_signal_P5D_SIDE_<TAG>_<stamp>.npz`  in cache `output/`
- `phase5_retrain_log_<stamp>.json`           in `phase3/docs/`
- Aggregated batch progress JSON              in `phase3/docs/phase_b_batch_progress_<stamp>.json`

## 4. Post-batch evaluation pipeline

After all 3 presets complete:

1. **t26 walk-forward** for the 3 P5D signals + V2 baseline (≈4 min):
   ```bash
   python3 -u phase3/tests/step_d_walk_forward.py \
     --signals baseline,p5d_side_pure,p5d_side_deep,p5d_side_win
   ```
   *(requires adding 3 entries to `SIGNALS` list in step_d_walk_forward.py — trivial, ~6 lines)*

2. **Pick best SIDE specialist** by F2 CAGR + SIDE AnnRet table.

3. **Build ensemble** via `tests/p2_ensemble_composer.py`:
   - `wb` = V2_GOLDEN (BULL component)
   - `ws` = best P5D_SIDE_* (SIDE component, this batch)
   - `wd` = V2_GOLDEN (DEF component)
   - Tag: `P7_ENSEMBLE_V2_PLUS_SIDE_<picked>`

4. **Step C gate eval** + **t26 walk-forward** for the new ensemble vs V2_GOLDEN. If gates pass, proposes promotion.

## 5. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| All 3 SIDE specialists overfit and underperform V2 SIDE | medium | We have the F2/F0b walk-forward as OOS check; ensemble composition further dilutes overfit risk. |
| `side_pure` produces a signal that's pathological in BULL/DEF | high | Acknowledged — `side_pure` is upper-bound research, not necessarily a deployable signal. Used only as ensemble component. |
| Window-shift `side_win` ends up similar to base because shift is small | possible | If true, `side_pure` and `side_deep` provide enough diversity. |
| GA seed `SEED_D` happens to be a low-luck draw | low | Stability layer (5 seeds × 100 fast pop) already mitigates per-run seed variance. |

## 6. Linked artifacts

- This plan: `phase_b2_1_side_specialist_plan.md` (this file)
- Driver: `phase3/run_phase5_batch_b.py`  (3 entries with `batch == 4`)
- Predecessor decisions: `phase_b2_regime_cond_plan.md`, `baseline_benchmark.md` v1.3
- Walk-forward template: `phase3/tests/step_d_walk_forward.py`
- Ensemble composer: `phase3/tests/p2_ensemble_composer.py`
- Most recent vt_S_18 walk-forward (negative result that motivated this): `docs/p5c_vt_S18_walk_forward_<latest>.md`

