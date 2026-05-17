# Shadow → Live Baseline Promotion Protocol

**Created**: 2026-05-08
**Status**: Active draft (first formal version)
**Trigger**: OOS-clean candidates (`P5E_SIDE_FUND_BREAKOUT`, `P5D_SIDE_DEEP`,
`P11_OOS_CLEAN_L3_*`) showing rolling-fold hardgate pass against the in-sample
inflated `Baseline_V2`.

---

## 0. Problem statement

`Baseline_V2` (`V2_GOLDEN_ENS_L3_v1`) was trained through 2026-03-02, so the
last 21 months it is evaluated on are **in-sample for the signal**. Direct
hardgate comparison overstates V2 by ~10pp mean CAGR (default fold-set) and
~17pp on F4. We need a structured promotion protocol that:

1. Validates a candidate's *signal-OOS* performance (not just strategy-OOS).
2. Keeps live capital exposed to the proven baseline during validation.
3. Produces an objective swap decision based on out-of-sample evidence.

---

## 1. Promotion stages

```
[1. Backtest pass]   →   [2. Shadow run]   →   [3. Soft swap]   →   [4. Hard swap]
   (offline)            (live, paper)         (live, partial)      (live, full)
```

### Stage 1 — Backtest pass (offline, fast)
**Pass criteria** (must hold *together* — one cell red ⇒ stop):

| Check | Rule | Source |
|---|---|---|
| Signal-OOS clean | candidate's GA `train_end ≤ 2024-05-31` (or earlier) | `signal_summary.npz` |
| Rolling fold-set hardgate | `cand` passes A/B/C/D vs **Baseline_V2** | `step_d_walk_forward.py --fold-set rolling` |
| Re-baseline hardgate | `cand` passes A/B/C/D vs **P2_BATCH11_OOS** (signal-OOS reference) | same, with `--baseline p2_oos` (TODO) |
| Surge capture | `Lift_10d(cand) ≥ 0.80 × Lift_10d(V2)` | `surge_score_analysis.py` |
| F4 OOS CAGR | `cand` F4 CAGR ≥ V2 F4 CAGR − 5pp | step_d default fold-set |
| Stability | `CV(cand, rolling) ≤ 0.20` (absolute floor, not relative) | rolling fold-set summary |

**Pass output**: candidate is added to `phase3/config.yaml` under
`shadow.frozen_signal` and the operator is notified.

### Stage 2 — Shadow run (live, paper, 30 calendar days)

The existing infrastructure (`daily_runner._run_shadow_pass`, `shadow_diff`,
auto-expire) supports this stage out of the box. Configure in
`phase3/config.yaml`:

```yaml
shadow:
  enabled: true
  frozen_signal: /Users/.../frozen_signal_P5E_SIDE_FUND_BREAKOUT_20260426_152005.npz
  label: "P5E_FUND_BRK"
  start_date: "<today>"
  duration_days: 30
  include_in_email: true
```

**What it does each day**:
1. Scores the candidate signal alongside the live baseline.
2. Generates parallel `recos` (top-N tickers).
3. Compares: top-N overlap rate, rank correlation, regime tag.
4. Saves both artifact directories under `output/daily_runs/` (live + shadow).
5. Includes a summary section in the daily email.

**Stage 2 pass criteria** (evaluated on day 30 from accumulated diff data):

| Check | Rule | Rationale |
|---|---|---|
| Top-N overlap | mean overlap ≥ 25% (V2 vs cand on top-15) | sanity — candidate isn't picking wildly different stocks |
| Rank correlation | mean Spearman ρ ≥ 0.30 | factor agreement — not chasing noise |
| Realised top-15 alpha | `mean(top15_cand_5d_fwd) ≥ mean(top15_V2_5d_fwd)` over 30 days | live evidence: candidate's picks would have outperformed |
| Coverage | candidate produced valid scores ≥ 28/30 days | reliability |
| No data-quality flag | no NaN explosions, no all-zero score days | reliability |

If 4/5 pass → advance to **soft swap**. If 3/5 → **extend shadow 15 days**.
If <3/5 → **abort, archive shadow report**.

### Stage 3 — Soft swap (live, partial allocation, 30 calendar days)

Switch to a **two-signal active blend** — candidate gets 30% of new buys,
baseline keeps 70%.

**How**: introduce a new `active_blend` block in `config.yaml`:

```yaml
active_signal:
  primary:
    frozen_signal: /Users/.../frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz
    weight: 0.70
  secondary:
    frozen_signal: /Users/.../frozen_signal_P5E_SIDE_FUND_BREAKOUT_*.npz
    weight: 0.30
```

> **NOTE**: `daily_runner` does not yet implement `active_signal.secondary`
> blending. This is a follow-up engineering task. Until that lands, Stage 3
> can be approximated by *manually* allocating 30% of new daily-buy budget
> against the candidate's top picks (via a parallel paper account).

**Stage 3 pass criteria** (after 30 days):

- Soft-blend account beats V2-only paper account by ≥ 0pp (i.e. doesn't
  underperform). Tie acceptable since OOS-clean is structurally safer.
- No live operational issues (data, latency, scoring).

### Stage 4 — Hard swap (full live)

Replace baseline:

```yaml
strategy:
  frozen_signal: /Users/.../frozen_signal_<NEW_BASELINE>.npz
```

Archive the old baseline path under `archive/old_baselines/` and bump the
`baseline_version` (currently V2 → V3). Update
`phase3/docs/baseline_history.md` with the swap event, dates, and the rolling-
fold hardgate evidence.

---

## 2. Quick-start checklist for `P5E_SIDE_FUND_BREAKOUT`

| Step | Action | ETA | Owner |
|---|---|---|---|
| 1.1 | Confirm rolling hardgate ALL ✓ vs V2 | done (2026-05-08) | quant |
| 1.2 | Surge `Lift_10d ≥ 0.80×V2` | done — 1.76x vs 1.72x ✓ | quant |
| 1.3 | F4 OOS CAGR sanity (≥ V2 F4 − 5pp) | **F4=+46.4% vs V2 F4=+47.2% ✓** | quant |
| 1.4 | CV(rolling) ≤ 0.20 | **0.146 ✓** | quant |
| 1.5 | Re-baseline hardgate (vs P2_BATCH11_OOS) — needs CLI flag | **TODO** (1 day) | quant |
| 2.1 | Shadow config update + start | T+1 | ops |
| 2.2 | Daily diff review (30 days) | T+1..T+30 | ops |
| 2.3 | Stage 2 pass evaluation | T+30 | quant + ops |
| 3.1 | `active_signal.secondary` engineering | 1 week | dev |
| 3.2 | Soft swap (70/30) live | T+37..T+67 | ops |
| 4.1 | Hard swap | T+67 | ops |

---

## 3. Abort / rollback

- At Stage 2 or 3, if the candidate underperforms baseline by **≥ 5pp annualised**
  in the live window, immediately revert to baseline-only and archive the
  shadow/blend report. The candidate is then either retired or sent back to
  Stage 1 with new factor exposure.
- Stage 4 hard swaps are reversible by re-pointing
  `strategy.frozen_signal` back to V2; this is logged in
  `baseline_history.md`.

---

## 4. Rationale notes

1. The existing shadow infrastructure (paper-paper compare) is the cheapest
   high-fidelity validation we have. Use it.
2. Hardgates measured against the inflated V2 are a *high* bar — passing
   rolling against V2 is meaningful evidence the candidate has real alpha,
   even when default/regime fold-sets fail (those fail because V2's lookahead
   is concentrated in specific windows).
3. Soft-swap (70/30) limits downside to 30% of capital while still letting
   the candidate prove itself. After 30 live days the data dominates the
   decision.
4. The whole protocol is **opinionated against rapid promotion** — better
   to leave V2 in place an extra month than to swap on backtest evidence
   alone, given that V2 is already known to have lookahead inflation we
   cannot fully audit.
