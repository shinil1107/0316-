# Shadow Full-Range Retrain Manifest

Generated: 2026-05-30

Purpose: freeze the exact Shadow recipe before converting the OOS-clean
research signal into a production full-range retrain. This manifest is not a
hook. It is a reproducibility checklist: only the training end/date span should
change unless explicitly approved.

## Why This Exists

The intended change sounds simple: use the same recipe and extend `train_end`.
In practice, a GA retrain can drift if any of these move silently:

- training start/end
- label-safe cutoff versus latest cache date
- seed/randomness mode
- GA budget and meta-search budget
- regime-specific penalty knobs
- feature pools
- composer weights
- output naming and promotion target

This manifest makes the full-range retrain auditable. If the new production
Shadow behaves differently, we can tell whether it is because of the wider
training range or because the recipe accidentally changed.

## Target Signal

Production candidate:

`P11_FUNDB_ANCHOR_FULL_RANGE`

Current research Shadow source:

`/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P11_OOS_CLEAN_L3_FUNDB_ANCHOR_20260508_180824.npz`

Current recipe:

- L3 weighted-average ensemble
- Apply the same component mix to `wb`, `ws`, and `wd`
- Component weights:
  - `P5E_FUND_BRK`: 2.0 input weight, 0.50 normalized
  - `P5D_SIDE_DEEP`: 1.0 input weight, 0.25 normalized
  - `P2_BATCH11_OOS`: 1.0 input weight, 0.25 normalized
- Mask rule: union of non-zero `wb/ws/wd`

Composer reference:

`phase3/tests/p2_ensemble_composer.py`, preset `V`, tag
`P11_OOS_CLEAN_L3_FUNDB_ANCHOR`.

## Full-Range Policy

Keep the recipe fixed. Extend only the training end date.

Two dates must be separated before running:

- `cache_data_end`: latest available cache/pack date, currently expected around
  `2026-05-29`.
- `train_label_end`: latest date with valid forward labels for the objective.
  Because the GA objective uses forward-return terms, this may be earlier than
  `cache_data_end`. The runner/engine may filter NaN forward labels, but the
  resolved effective label span should be written into the run log.

Do not tune recipe weights after seeing the full-range result.

## Leaf 1: P5E_FUND_BRK Full Range

Current source:

`/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P5E_SIDE_FUND_BREAKOUT_20260426_152005.npz`

Current run log:

`phase3/docs/phase5_retrain_log_20260426_152005.json`

Original training window:

- `2012-01-03 -> 2024-05-31`

Full-range retrain window:

- start: `2012-01-03`
- end: resolve at runtime as `train_label_end`

Fixed knobs:

- source runner family: `phase3/run_phase5_batch_b.py`
- batch/run id: `side_fund_brk`
- run tag: `P5E_SIDE_FUND_BREAKOUT_FULL`
- seed: `20260602`
- `w_turnover_bull/side/def`: `(0.05, 0.30, 0.20)`
- `w_cost_bull/side/def`: `(0.03, 0.18, 0.12)`
- side pool: `B5_SIDE_POOL_FUND_BRK`
- `factor_corr_penalty_lambda`: `0.05`
- Batch 5 anti-collapse/budget family unchanged.

Current selected slot shape:

- `wb`: `MOM_6M`, `SMA_CROSS`, `DIST_FROM_SMA50`
- `ws`: `VAL_BOOK2PRICE`, `CF_FCF_YIELD`
- `wd`: `QUAL_ROE`, `LEV_DEBT_EQUITY`

## Leaf 2: P5D_SIDE_DEEP Full Range

Current source:

`/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P5D_SIDE_DEEP_20260426_024004.npz`

Current run log:

`phase3/docs/phase5_retrain_log_20260426_024004.json`

Original training window:

- `2012-01-03 -> 2024-05-31`

Full-range retrain window:

- start: `2012-01-03`
- end: resolve at runtime as `train_label_end`

Fixed knobs:

- source runner family: `phase3/run_phase5_batch_b.py`
- batch/run id: `side_deep`
- run tag: `P5D_SIDE_DEEP_FULL`
- seed: `20260502`
- `w_turnover_bull/side/def`: `(0.02, 0.60, 0.20)`
- `w_cost_bull/side/def`: `(0.01, 0.35, 0.10)`
- Batch 4 SIDE-specialist family unchanged.

Current selected slot shape:

- `wb`: `SMA_CROSS`, `QUAL_ROE`, `SMA50_SLOPE`,
  `DIST_FROM_SMA50`, `OBV_POS`
- `ws`: `VAL_BOOK2PRICE`, `CF_FCF_YIELD`
- `wd`: `QUAL_ROE`, `LEV_DEBT_EQUITY`

## Leaf 3: P2_BATCH11 Full Range

Current OOS-clean source:

`/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P2_BATCH11_OOS_20260508_112505.npz`

Current run log:

`phase3/docs/phase5_retrain_log_20260508_112505.json`

Original OOS-clean training window:

- `2015-01-01 -> 2024-05-31`

Original lookahead/live-ish P2 reference:

- `2017-02-21 -> 2026-03-02`

Full-range retrain window:

- start: `2015-01-01`
- end: resolve at runtime as `train_label_end`

Fixed knobs:

- source runner: `phase3/run_p2_batch11_oos.py`
- run tag: `P2_BATCH11_FULL`
- seed: `20260507`
- original P2_BATCH11 recipe preserved:
  - meta-search ON
  - `ga_population=400`
  - `ga_generations=12`
  - `stability_seed_runs=8`
  - `stability_fast_population=200`
  - `stability_fast_generations=9`
  - `stability_refine_population=600`
  - `stability_refine_generations=11`
  - `bull_spread_bonus_lambda=1.35`
  - `entropy_bonus=0.10`
  - `meta_disabled_template_name="TPL_SPREAD"`
  - `enable_deployment_penalty=False`

Current selected slot shape:

- `wb`: `SMA50_SLOPE`, `MOM_12M_EX1M_X_QUAL_ROE`,
  `VAL_BOOK2PRICE_X_MOM_6M`, `QUAL_ROE`, `DIST_FROM_SMA50`
- `ws`: `STOCH`, `WILLR`, `SMA_CROSS`
- `wd`: `WILLR`, `LEV_DEBT_EQUITY`, `BBP`

## Compose Step

After the three leaves are rebuilt:

- use the same preset `V` composition
- replace the three source paths with the full-range leaf outputs
- keep input weights `2:1:1`
- output tag: `P11_FUNDB_ANCHOR_FULL_RANGE`

Expected final slot shape may change because each leaf is retrained, but the
composer rule must not change.

## Validation Gate

Run these before any promotion:

- default fold / long stateful simulation versus V2, P2_OOS, current Shadow
- stateful artifact ledger for the available recent daily-run window
- axis-shift gate versus current live V2 and current Shadow
- top-N overlap and rank correlation in the latest 20-30 daily dates
- sanity check that all leaves used the intended train start/end, seed, budget,
  and feature-pool overrides

Promotion decision rule:

- full-range Shadow can be a production candidate only if it beats or closely
  matches current V2 on long simulation and does not fail the recent stateful
  ledger check.
- If long simulation improves but recent ledger remains much worse than V2,
  keep it as Shadow and extend observation.
