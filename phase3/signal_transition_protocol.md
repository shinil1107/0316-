# Phase 3 — Signal cutover (lean protocol)

Replace `paths.frozen_signal` in `phase3/config.yaml` with a new candidate
`.npz`. No ceremony. Use **E21 Signal Compare** (phase12 launcher) as the
sole evidence gate.

## Steps

1. Run **E21 Signal Compare** — baseline = current golden, candidate = new
   `.npz`. Keep both Phase 1/2 IC and Phase 3 backtest checkboxes ON. The
   tool runs in `daily` mode which is what live uses.
2. Open the generated `signal_compare_<A>_vs_<B>_*.xlsx` and read the
   `Decision` sheet. Candidate is promotable when:
   - Phase 3 Net Sharpe: `Δ ≥ 0` (ties OK)
   - Phase 3 Max Drawdown: `Δ ≤ +1pp` (tolerance for noise)
   - Phase 3 CAGR: `Δ ≥ −1pp`
   - Phase 3 SIDE Sharpe: `Δ ≥ 0` (Codex §5.7 — fixing BATCH11's SIDE
     weakness is the whole point)
   - Phase 1/2 IC_1M Mean: no regime's IC drops by more than `−0.005`.
   These thresholds are heuristic; adjust in the Excel if you have a
   reason to.
3. Copy the candidate artifact to a stable filename first, e.g.
   `frozen_signal_V<n>_GOLDEN_<desc>_<YYYYMMDD>.npz`, so the transition
   stays reproducible after the experiment directory is cleaned up.
4. Edit `phase3/config.yaml`:
   ```yaml
   paths:
     frozen_signal: /.../frozen_signal_V<n>_GOLDEN_<desc>_<YYYYMMDD>.npz
   ```
5. Commit with message:
   ```
   phase3: cutover v<n-1> → v<n> (<candidate label>)
   
   E21 report: signal_compare_V<n-1>_vs_V<n>_<timestamp>.xlsx
   Phase 3 Δ Sharpe=<>, Δ MDD=<>pp, Δ CAGR=<>pp, Δ SIDE Sharpe=<>
   ```
6. Keep the E21 Excel in `phase3/signal_transitions/` for audit.

## Rollback

Before cutover, backup:
```
cp phase3/config.yaml phase3/config.yaml.bak_$(date +%Y%m%d)
```
Rollback = restore the backup (or `git revert`). No holdings manipulation
needed; `holdings_log.xlsx` is signal-agnostic.

## Note on rebalance mode

Live uses `strategy.rebalance_mode: daily` — E21's daily backtest
reproduces live behavior. Only add an event-driven run if you plan to flip
`rebalance_mode` to `event_driven` as part of the cutover.

## Current transition record

| From | To | Artifact | Status |
|---|---|---|---|
| V1 BATCH11 | V2 ENS_L3_v1 | `frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz` | **cutover 2026-04-19** |

### V1 → V2 cutover (2026-04-19)

- Evidence xlsx (`phase3/signal_transitions/`):
  - `signal_compare_V1_BATCH11_vs_V2_ENS_L3_v1_20260419_143744.xlsx`
  - `phase3_pnl_compare_V1_BATCH11_vs_V2_ENS_L3_v1_20260419_143744.xlsx`
- Config backup: `phase3/config.yaml.bak_20260419`
- V1 rollback path (kept in `config.yaml` comment):
  `frozen_signal_P2_BATCH11_20260406_043415.npz`

Δ headline (daily-mode full window, 2017-01-03 → 2026-04-17):

| Metric | V1 BATCH11 | V2 ENS_L3_v1 | Δ |
|---|---:|---:|---:|
| Global Net Sharpe | 1.2629 | **1.2805** | +0.0176 |
| Global CAGR | 34.73% | **35.62%** | +0.89pp |
| Global Calmar | 0.9884 | **0.9973** | +0.0089 |
| Global MDD | 35.14% | 35.72% | +0.58pp |
| **SIDE Sharpe** | 0.7364 | **0.8101** | +0.074 |
| **SIDE Calmar** | 0.8420 | **1.0257** | +0.184 |
| SIDE MDD | 26.98% | **24.73%** | −2.25pp |
| Global Total Return | 14.83× | **15.83×** | +6.7% |
| BULL Sharpe | 2.0349 | 2.0096 | −0.025 |
| DEF Sharpe | 1.0033 | 0.9729 | −0.030 |

Trade accepted: small BULL/DEF Sharpe bleed (~1.5% / 3%) for large SIDE
improvement (+10% Sharpe, +22% Calmar, −8% MDD in SIDE regime). Net global
Sharpe / CAGR / Calmar / WinRate all improve. MDD still sits at ~35pp —
this is a separate problem targeted by the subsequent E20 Exit Rule Sweep,
not by signal choice.

### Known dormant config

`paths.regime_signal_paths.BULL` still points to the old
`frozen_signal_BULL_GA_20260417_232732.npz` (pre-V2). Because
`regime_compose.enabled: false`, this is unused by Phase 3 Live. If you
later enable `regime_compose`, refresh that entry or rely on the fallback
to `paths.frozen_signal` (V2 ensemble).
