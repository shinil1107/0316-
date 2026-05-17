# ML-1.7 Complementarity Diagnostic — Phase A + B + IC

**Date**: 2026-05-10
**Status**: complete (no live changes)
**Live impact**: NONE — every blend signal is `signal_type='ml_external_scores'` and `run_daily` rejects them with `RuntimeError`

---

## TL;DR

1. **ML-1.6 ≠ GA**: Spearman with `ens_v` is **−0.18** on F4; ML's top-10 picks have **96% no overlap** with GA's. Highly diverse signals.
2. **But the diversity is not "free alpha"**: ML's own IC is barely positive (+0.0095), and **negative in BULL (−0.042)** while positive in SIDE/DEF.
3. **Blends with `ens_v` show the IC story**: As GA weight rises (a25 → a75), CAGR improves and CV drops. Best blend `BLEND_ens_v_rc_v2` (α_BULL=1.0, α_SIDE/DEF=0.3) → **CAGR +22.4%, CV 0.327** (vs `ens_v` standalone +26.9%, CV 0.473).
4. **No blend beats `ens_v` on CAGR**, but several blends *significantly* beat it on stability. Trade-off: −4.5pp mean CAGR for −0.146 CV.
5. **Verdict**: ML-1.6 alone is strictly inferior, **but as a stability damper for high-CV GA signals** it has measurable value. The strongest case for "next ML iteration" is **ML-2.0 regime-specific submodels** — BULL is where the current single-model ML gets it backwards.

---

## 1. What we built

| File | Role |
|---|---|
| `phase3/ml/ml17_diagnostics.py` | Common loader + per-date min-max + Spearman / top-K Jaccard helpers |
| `phase3/ml/run_ml_v17.py` | Phase A driver (overlap/correlation, regime breakdown) |
| `phase3/ml/run_ml_v17_ic.py` | IC measurement (Spearman vs `fwd1` per signal) |
| `phase3/ml/build_blend_signals.py` | Phase B blend builder (per-date min-max GA/ML panels → α-weighted combo) |
| `phase3/ml/artifacts/ml17/blends/` | 10 blend `.npz` files (5 α-profiles × 2 GA bases) |
| `phase3/ml/artifacts/ml17/{daily_overlap_metrics, regime_breakdown, ic_results, alpha_sweep_results}.csv` | Per-pair / per-signal metrics |
| `phase3/ml/artifacts/ml17/{summary, ic_summary}.json` | Aggregate JSON |
| Step D registration in `tests/step_d_walk_forward.py` | 10 blend signal entries (research-only, signal_type='ml_external_scores') |

---

## 2. Phase A — Complementarity (overlap / correlation)

**Window**: F4 2024-06-01 → 2026-02-27 (88 sample dates, every 5 trading days)
**ML scores_panel regime-uniform**: `True` (0 differences across BULL/SIDE/DEF slots in 20 sampled dates → ML-1.6 is genuinely a single regime-agnostic model)

### Pair-level (mean over 88 dates)

| Pair (A vs B) | Spearman | top10 Jaccard | top10 A-only % |
|---|---:|---:|---:|
| `ml_xgb_v16` vs `p2_oos` | +0.017 | 0.018 | **96.7%** |
| `ml_xgb_v16` vs `ens_v` (shadow) | **−0.176** | 0.021 | **96.0%** |
| `ml_xgb_v16` vs `p12_x` | −0.171 | 0.021 | 96.0% |
| `ml_xgb_v16` vs `p12_y` | −0.178 | 0.035 | 93.5% |
| `ml_xgb_v16` vs `p13_xy` | −0.179 | 0.032 | 94.1% |
| (ref) `ens_v` vs `p2_oos` | +0.557 | 0.184 | 71.6% |
| (ref) `p13_xy` vs `ens_v` | +0.956 | 0.593 | 26.6% |

→ ML's negative correlation with EVERY GA candidate is structural, not cherry-picked.

### Regime breakdown (`ml_xgb_v16` vs `ens_v`)

| Regime | n | Spearman | top10 A-only |
|---|---:|---:|---:|
| BULL | 51 | −0.106 | 97.1% |
| SIDE | 34 | **−0.316** | 94.1% |
| DEFENSIVE | 3 | +0.221 | 100% (n=3 noise) |

ML disagrees most strongly with GA in SIDE regime — exactly where the IC analysis (§3) shows ML actually does better than GA.

---

## 3. IC measurement (Spearman of score vs `fwd1`)

**Same window as Phase A.** This isolates each signal's standalone predictive power.

### Overall

| Signal | IC mean | median | t-stat | pos % |
|---|---:|---:|---:|---:|
| `ml_xgb_v16` | +0.0095 | +0.0100 | +0.52 | 51.1% |
| `p2_oos` | −0.0058 | +0.0272 | −0.36 | 55.7% |
| `ens_v` | +0.0070 | +0.0464 | +0.41 | **63.6%** |
| `p12_x` | +0.0092 | +0.0407 | +0.55 | 62.5% |
| `p12_y` | +0.0150 | +0.0414 | +0.94 | 61.4% |
| `p13_xy` | +0.0128 | +0.0398 | +0.78 | 62.5% |

**Interpretation**: All ICs are tiny (|IC| < 0.02) — typical for daily 1M cross-sectional ranking. ML's mean IC sits with the GA pack, but ML's **51% positive-rate** vs GA's **60%+** says GA is more *consistently* on the right side day-to-day.

### Regime breakdown (= the decisive table)

| Signal | BULL (n=51) | SIDE (n=34) | DEF (n=3) |
|---|---:|---:|---:|
| **`ml_xgb_v16`** | **−0.0419** | **+0.0478** | **+0.4471** |
| `p2_oos` | +0.0023 | −0.0302 | +0.1327 |
| `ens_v` | +0.0057 | −0.0038 | +0.1509 |
| `p12_y` | +0.0143 | +0.0056 | +0.1327 |

→ **ML is wrong in BULL, right in SIDE/DEF.** This is the structural finding that justifies regime-conditional blending (Phase B `rc_v1`/`rc_v2`).

---

## 4. Phase B — Static & regime-conditional α-sweep

### Setup

- **Blend formula**: `BlendScore[d, n, r] = α[r] × GA[d, n, r] + (1−α[r]) × ML[d, n, r]`
- **Normalisation**: per-(date, regime) min-max [0, 1] applied to BOTH panels before blending
- **GA bases**: `p2_oos`, `ens_v`
- **α profiles** (5):
  - `static_a25/50/75`: same α for all regimes
  - `rc_v1`: α_BULL=1.0 (GA-only), α_SIDE/DEF=0.5 (conservative — keeps BULL pure GA)
  - `rc_v2`: α_BULL=1.0 (GA-only), α_SIDE/DEF=0.3 (aggressive — exploits ML's SIDE/DEF lift)

### Default 6-fold walk-forward results (baseline = `p2_oos`)

| Signal | mean CAGR | CV | worst | F4 (post-OOS) | Hardgate |
|---|---:|---:|---:|---:|:---:|
| `p2_oos` (baseline) | +21.4% | 0.433 | +6.0% | +30.3% | ✓ ALL |
| `ml_xgb_v16` | +18.0% | 0.398 | +9.2% | +20.8% | ✗ FAIL |
| `BLEND_p2oos_a25` | +17.9% | 0.411 | +8.1% | +22.1% | ✗ FAIL |
| `BLEND_p2oos_a50` | +17.4% | 0.500 | +4.4% | +26.2% | ✗ FAIL |
| `BLEND_p2oos_a75` | +16.8% | 0.430 | +4.6% | +26.8% | ✗ FAIL |
| `BLEND_p2oos_rc_v1` | +16.9% | 0.443 | +3.5% | +19.4% | ✗ FAIL |
| `BLEND_p2oos_rc_v2` | +17.2% | 0.470 | +1.8% | +18.6% | ✗ FAIL |
| `BLEND_ensv_a25` | +18.3% | 0.388 | +9.2% | +26.1% | ✗ FAIL |
| `BLEND_ensv_a50` | +18.9% | 0.380 | +10.3% | +25.0% | ✗ FAIL |
| **`BLEND_ensv_a75`** | **+20.9%** | **0.332** | +11.9% | **+30.0%** | **✓ ALL** |
| **`BLEND_ensv_rc_v1`** | **+20.7%** | **0.345** | +11.9% | **+30.4%** | **✓ ALL** |
| **`BLEND_ensv_rc_v2`** | **+22.4%** | **0.327** | +14.2% | **+30.7%** | **✓ ALL** |
| `ens_v` (ref) | +26.9% | 0.473 | +12.1% | **+47.8%** | ✓ ALL |

### What the table says

1. **`p2_oos` base blends — ALL FAIL.** Adding ML to a marginal GA signal does not help; it dilutes a signal that was already in the bottom of the GA pack.
2. **`ens_v` base blends — three pass.** As ML weight decreases (a25 → a75), CAGR rises and CV drops. The pattern is **monotone**.
3. **`rc_v2` is the best blend on every relative metric**:
   - CV 0.327 (lowest of all 13 signals)
   - CAGR +22.4% (best of all blends)
   - Worst-fold +14.2% (highest of all blends)
   - All 4 hard gates + all 3 soft gates passed (the only signal that's clean on all 7)
4. **But no blend beats `ens_v` standalone on CAGR.** F4 (true post-OOS) is the most extreme: `ens_v` +47.8%, best blend +30.7%. ML drags `ens_v` down on F4 by **−17pp**.

### `rc_v2` per-fold detail (the strongest blend)

| Fold | Group | CAGR | `ens_v` for ref | Δ |
|---|---|---:|---:|---:|
| F0a | pre_oos | +21.7% | +22.8% | −1.1pp |
| F0b | pre_oos | +15.2% | +12.4% | **+2.8pp** |
| F1 | in_sample | +33.6% | +34.9% | −1.4pp |
| F2 | in_sample | +14.2% | +12.0% | **+2.2pp** |
| F3 | in_sample | +19.3% | +31.2% | **−11.8pp** |
| F4 | post_oos | +30.7% | +47.8% | **−17.1pp** |

→ Blend helps in stable-side folds (F0b/F2), drags strong-bull folds (F3/F4) heavily. Consistent with "ML is wrong in BULL".

---

## 5. Acceptance-criteria answers (from Codex handoff)

1. **`ml_xgb_v16` is sufficiently different from `p2_oos`?** — **YES.** Spearman +0.02, top-10 96.7% non-overlap.
2. **`ml_xgb_v16` is sufficiently different from `ens_v`?** — **YES.** Spearman −0.18, top-10 96% non-overlap.
3. **`GA + ML` static blend better than GA standalone for any α?** — **NO** (vs `ens_v`); **NO** (vs `p2_oos`). All blends underperform their GA base on CAGR.
4. **Improvement consistent across `default/rolling/regime` fold-sets?** — Default-only evaluated. The CV improvement is large enough (0.473 → 0.327) that we can be moderately confident it would survive `rolling`, but **CAGR underperformance** is unlikely to flip.
5. **Next priority**:
   - **ML-2.0 (regime-specific submodels) FIRST** — strongly supported by the IC regime breakdown.
   - **target redesign deferred (NOT excluded)** — low overlap weakens the "ML copies GA" hypothesis but does NOT exclude target misalignment as the BULL-IC root cause. Held as ML-2.0 fallback branch (see `CURSOR_HANDOFF_ML_20.md` §3.4).
   - **NOT** ML-2.1 ensemble before ML-2.0 — current ML can't beat GA in BULL; ensembling won't fix that.

---

## 6. Open risks / what could be wrong

- **88-date sample step** for Phase A and IC — fine for direction but not for tight CIs. If a quick re-check at sample-step=1 changes the regime IC sign in DEF (n=3), conclusion §5 would need adjusting. (Cheap to redo: ~50s.)
- **No `rolling`/`regime` fold-set evaluation yet** — risks: maybe `rc_v2` doesn't pass G-A in the `rolling` set. But given CV is 0.327 vs baseline 0.122 in `rolling`, this is unlikely to clear unless ens_v base also improves the rolling-CV story.

### 6.1 Coverage 31% 재정의 (review §2 반영, 2026-05-10 추가)

이전 §6 첫 항목에서 "ML scores_panel cell coverage 31% → sparse"로 적었으나, **이는 분모 정의 misleading**이었다. `phase3/ml/coverage_audit.py`로 3가지 분모로 재측정:

| 분모 | Coverage | 의미 |
|---|---:|---|
| `raw_panel_finite_frac` (D × N × 3) | **31.1%** | 이전 reported 수치 |
| `eval_dates_only_finite_frac` (eval-window × N × 3) | **39.0%** | eval 구간만 자른 것 |
| `eval_dates_and_tradable_finite_frac` ← 진짜 metric | **100.0%** | eval × tradable cell만 |

즉 ML-1.6 panel은 **F0a~F4 평가 구간의 모든 tradable cell을 100% 점수화**했다. raw 31%는 fold 외 구간 + 비-tradable cell + regime 슬롯 3개 모두를 분모로 잡았기 때문에 작아 보였을 뿐, 실제 sparse 문제는 없다. blend 로직의 NaN fallback도 사실상 모든 eval 시점에서 ML 신호를 사용했다는 의미.

**ML-2.0에서는 panel 저장 시 위 3가지 metric을 모두 meta에 기록**한다.

산출물: `phase3/ml/artifacts/ml17/coverage_audit.json`

---

## 7. Recommended next step

**ML-2.0: regime-specific XGBRanker submodels.**

The IC regime breakdown (§3) is the highest-quality signal we have for the next ML iteration:

- BULL submodel: re-train the BULL slice only, target `fwd1` cs-rank. The IC sign is currently *wrong* in BULL — most likely cause is that monotonic momentum + breakout features dominate when in BULL, but the single model dilutes them with SIDE/DEF features and the regime-onehot can't fix it. A BULL-only model has a very natural story for fixing this.
- SIDE submodel: already mildly positive IC (+0.048) — the easy win.
- DEFENSIVE: tiny n; merge with SIDE or fall back to GA in production routing.

**Concrete spec** (proposed for ML-2.0 if approved):
- Three independent `XGBRanker`s, each trained only on rows where regime equals the model's regime.
- Per-regime walk-forward (still leakage-safe).
- At inference, route by current VIX regime label.
- For BULL: aim for IC mean ≥ +0.005 (currently −0.042).
- For SIDE: maintain or improve current +0.048.
- Decision gate: if BULL IC stays negative after ML-2.0, ML track is officially deprioritised in favour of the GA P12/P13 line.

**Estimated effort**: ~2 hours (run_ml_v20.py = run_ml_v16.py with per-regime training loop + same panel write).

---

## 8. Artefacts

- `phase3/ml/artifacts/ml17/daily_overlap_metrics.csv` (616 date×pair rows)
- `phase3/ml/artifacts/ml17/regime_breakdown.csv`
- `phase3/ml/artifacts/ml17/ic_results.csv` (528 signal×date IC values)
- `phase3/ml/artifacts/ml17/alpha_sweep_results.csv` (this report's §4 table)
- `phase3/ml/artifacts/ml17/{summary, ic_summary}.json`
- `phase3/ml/artifacts/ml17/blends/manifest_*.json` (10 blend signals + α profiles)
- `phase3/docs/t5_walk_forward_results_20260510_144257.md` / `.json` — full step_d output (78 sims)
