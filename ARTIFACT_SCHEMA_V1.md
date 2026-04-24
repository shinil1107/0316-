# Artifact Schema v1

## Purpose

Artifact layer v1 records a reproducible snapshot of each Phase 3 daily run.

It is designed to answer:

- what input state was used
- what signal and config were used
- what scoring date was actually used
- what recommendations were generated
- what the portfolio looked like before recommendation output

This layer does **not** replace the Excel-based operational state in
`holdings_log.xlsx`.
It complements it with append-only run evidence for audit, debugging, and
Phase 4 automation readiness.

## Scope

Artifact schema v1 covers:

- T7 / `run_daily()` output snapshots
- daily run metadata
- recommendation generation snapshots

It does **not** yet fully cover:

- T10 execution reconciliation writeback
- broker order lifecycle
- database-backed storage

## Storage Location

Artifacts are stored under:

```text
<output_dir>/daily_runs/<run_id>/
```

Where:

- `<output_dir>` comes from `phase3/config.yaml`
- `<run_id>` is timestamp-based and unique per run

Example:

```text
/Users/.../output/daily_runs/20260412_162530_daily/
```

## Design Rules

1. Keep artifacts human-readable.
2. Use JSON for structured metadata and CSV for tables.
3. Prefer additive snapshots over mutating prior records.
4. Redact sensitive config values when saving config snapshots.
5. Do not change the existing Excel workflow for v1.

## Run Status Semantics

`run_meta.json.status` uses these values in v1:

- `generated`: run completed but is not awaiting manual execution
- `awaiting_execution`: actionable recommendations were generated and are waiting for manual execution
- `error`: run failed before normal artifact generation completed

Future versions may add:

- `partially_executed`
- `executed`
- `cancelled`

## Required Files

Every successful v1 run should create these files:

- `run_meta.json`
- `config_snapshot.json`
- `signal_snapshot.json`
- `market_snapshot.json`
- `portfolio_before.csv`
- `portfolio_after_price_refresh.csv`
- `scores.csv`
- `recommendations.csv`
- `recommendation_summary.json`
- `execution_template.csv`

## File Definitions

### `run_meta.json`

Top-level metadata for the run.

Required fields:

- `schema_version`
- `run_id`
- `run_timestamp`
- `phase`
- `mode`
- `rebalance_mode`
- `status`
- `trigger_actionable`
- `trigger_list`
- `trigger_str`
- `regime`
- `vix_close`
- `scoring_date`
- `frozen_signal_path`
- `daily_buy_limit`
- `cash_balance`
- `holdings_value`
- `total_capital`
- `recommendation_count`
- `action_counts`

Optional fields:

- `health_overall`
- `post_refresh_stale_pct`
- `error`

### `config_snapshot.json`

Redacted configuration snapshot for reproducibility.

Recommended structure:

- `paths`
- `portfolio`
- `regime`
- `triggers`
- `strategy_base`
- `strategy_resolved`

Sensitive keys like `password`, `secret`, `token`, `api_key`, `apikey`
must be redacted.

### `signal_snapshot.json`

Signal identity and quality summary.

Recommended fields:

- `signal_path`
- `signal_file`
- `signal_exists`
- `signal_mtime`
- `signal_summary`

### `market_snapshot.json`

Summary of scoring context.

Recommended fields:

- `scoring_date`
- `scoring_index`
- `score_regime`
- `selected_factor_count`
- `valid_ticker_count`
- `ticker_count`
- `scored_count`
- `top_score`
- `median_score`
- `min_positive_score`

### `portfolio_before.csv`

Current holdings snapshot before price refresh.

### `portfolio_after_price_refresh.csv`

Current holdings snapshot after `update_current_prices()`.

### `scores.csv`

Scored universe snapshot.

Required metadata columns:

- `RunId`
- `ScoringDate`
- `Regime`

Recommended business columns:

- `Ticker`
- `Score`
- `Price`

### `recommendations.csv`

Full recommendation output for the run.

Required metadata columns:

- `RunId`
- `RecRowId`
- `ScoringDate`
- `Actionable`

Then preserve existing recommendation columns:

- `Date`
- `Ticker`
- `Action`
- `Score`
- `TargetPct`
- `ActualPct`
- `GapPct`
- `Price`
- `Shares`
- `Capital`
- `Regime`
- `GraceCount`

### `recommendation_summary.json`

Compact recommendation aggregate.

Recommended fields:

- `counts`
- `buy_capital_total`
- `sell_value_total`
- `net_capital_delta`

### `execution_template.csv`

Placeholder template for manual execution reconciliation.

Recommended columns:

- `RunId`
- `RecRowId`
- `Ticker`
- `Action`
- `RecommendedShares`
- `RecommendedPrice`
- `RecommendedCapital`
- `ExecuteFlag`
- `ExecutedShares`
- `ExecutedPrice`
- `ExecutionNote`

## Example Run Folder

```text
daily_runs/
  20260412_162530_daily/
    run_meta.json
    config_snapshot.json
    signal_snapshot.json
    market_snapshot.json
    portfolio_before.csv
    portfolio_after_price_refresh.csv
    scores.csv
    recommendations.csv
    recommendation_summary.json
    execution_template.csv
```

## Out of Scope for v1

- broker API execution artifacts
- SQLite or database migration
- replacing Excel as source of truth
- execution reconciliation status updates from T10

## Expected Evolution

v2 is expected to connect T10 manual execution flow back to the run folder with:

- `execution_meta.json`
- `execution_applied.csv`
- `portfolio_after_execution.csv`
- run status transitions such as `partially_executed` and `executed`
