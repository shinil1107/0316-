# Top-N Movement Classifier Handoff

- Date: `2026-06-05`
- Scope: portfolio-level labeling utility for daily recommendation email.
- Guardrail: no live config, autotrading, signal registry, or mailer hook was changed.

## Goal

Add readable labels to today's recommendation/report table so the operator can
quickly spot names that suddenly moved up into Top-N, such as the recent DELL/HPE
style wins.

This is intentionally a portfolio/reporting utility first.  It does not alter
signal scores, portfolio weights, buy/sell actions, or trigger behavior.

## New Files

- `phase3/topn_movement_classifier/__init__.py`
- `phase3/topn_movement_classifier/classifier.py`
- `phase3/tests/test_topn_movement_classifier.py`

## Main API

```python
from phase3.topn_movement_classifier import (
    MovementConfig,
    classify_topn_movement,
    discover_recent_score_csvs,
    load_score_snapshots,
    format_movement_email_section,
)
```

Typical daily email integration shape:

```python
paths = discover_recent_score_csvs(output_dir / "daily_runs", limit=12)
snapshots = load_score_snapshots(paths)
labels = classify_topn_movement(
    today_scores=snapshots[-1],
    history_scores=snapshots[:-1],
    config=MovementConfig(top_n=top_n),
)
section = format_movement_email_section(labels)
```

If Cursor wants labels for every row in `recommendations.csv`, including held
tickers outside current Top-N, pass those tickers explicitly:

```python
labels = classify_topn_movement(
    today_scores=today_scores,
    history_scores=history_scores,
    config=MovementConfig(top_n=top_n),
    target_tickers=recommendations["Ticker"].tolist(),
)
```

## Label Rules

Rank delta is defined as:

```text
past_rank - today_rank
```

Positive means the ticker moved up.  Example: rank 54 to rank 4 is `+50`.

Default primary labels:

| Label | Meaning | Default rule |
|---|---|---|
| `RISING` | Meaningful rank improvement | `3d delta >= +20` or `5d delta >= +30` |
| `FALLING` | Meaningful rank deterioration | `3d delta <= -20` or `5d delta <= -30` |
| `SIDEWAYS` | No strong directional move | neither rising nor falling |

Default tags:

| Tag | Meaning |
|---|---|
| `FAST_RISER` | `1d delta >= +30` or `3d delta >= +50` |
| `NEW_ENTRY` | Today in Top-N and absent from prior Top-N snapshots |
| `CORE_STABLE` | Present in Top-N for at least 7 of last 10 snapshots |
| `CHOPPY` | Rank direction flips repeatedly with a wide enough rank range |
| `REGIME_SWITCHED` | Previous snapshot regime differs from today's regime |

The operator-favorite signal is therefore:

```text
primary_label=RISING and tags include NEW_ENTRY,FAST_RISER
```

## Data Requirement

For real `+30/+50` detection, use full `scores.csv` snapshots, not only prior
recommendations or prior Top-N sets.  The current daily artifact writer already
stores full ranked scores at:

```text
<output_dir>/daily_runs/<RUN_ID>/scores.csv
```

Top-N-only history can detect `NEW_ENTRY`, but cannot reliably say whether the
move was `+30`, `+50`, etc.

## Score History Backfill

If recent portfolio reset/archive leaves too few `daily_runs/*/scores.csv`
snapshots, use the independent backfill utility:

```bash
python3 phase3/topn_movement_classifier/backfill_score_history.py \
  --mode strict \
  --days 92
```

Default output is repo-local and research-only:

```text
phase3/docs/topn_movement_classifier/score_history_backfill/score_backfill_<mode>_<timestamp>/daily_runs/*/scores.csv
```

Modes:

- `strict`: rebuilds a one-year live-style panel ending at each historical
  scoring date, then scores that date.  This is closest to "only use cache up to
  that date".
- `pack_replay`: builds one range pack and replays date slices.  Faster, useful
  for diagnostics, but less literal.

The generated directories intentionally mimic `daily_runs/<RUN_ID>/scores.csv`,
so the classifier can consume them through `load_score_snapshots(...)`.

## Score History Merge/Archive Recovery

If cache replay has gaps, merge replayed scores with archived daily runs:

```bash
python3 phase3/topn_movement_classifier/merge_score_history_sources.py \
  --start-date 2026-03-04 \
  --end-date 2026-06-04 \
  --replay-root phase3/docs/topn_movement_classifier/score_history_backfill/<RUN>/daily_runs \
  --archive-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/_archive/reset_20260603T140259Z/daily_runs" \
  --archive-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output_real/daily_runs" \
  --archive-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs"
```

Selection rule:

- Prefer replay snapshots when available.
- Fill missing replay dates with archived `_daily` scores.
- Skip `_shadow` and `_dryrun` by default.
- For duplicate dates, prefer non-empty and fuller score tables.
- Anchor dates by CSV `ScoringDate`, not by run folder date.

2026-06-05 local run:

- Replay output:
  `phase3/docs/topn_movement_classifier/score_history_backfill/score_backfill_pack_replay_20260605_183849`
- Merged output:
  `phase3/docs/topn_movement_classifier/score_history_merged/score_history_merged_20260605_184501`
- Coverage: 62 snapshots from 2026-03-04 through 2026-06-04.
- Source split: 48 replay snapshots + 14 archive snapshots.
- Known caveat: 2026-04-27 and 2026-04-28 only had 85-row archived daily
  score tables available, so those two dates are enough for Top-N movement
  labels but are not complete 500-row score-table reconstructions.

## 2026-06-05 Cache Refill Recheck

After refilling the missing OHLCV cache gap, rerun replay backfill:

```text
phase3/docs/topn_movement_classifier/score_history_backfill/score_backfill_pack_replay_20260605_212314
```

Result:

- Coverage: 65 snapshots from 2026-03-04 through 2026-06-04.
- Missing pandas business days: 2026-04-03 and 2026-05-25, both market holidays.
- This new replay should be treated as the current score-history source of
  truth for movement-label research/reporting.

Why source of truth changed:

- Previous replay had only 48 snapshots because the OHLCV cache missed
  2026-04-13 through 2026-05-05.
- On the 48 overlapping old/new replay dates, dates before the cache gap were
  unchanged, but dates after the gap changed materially because rolling
  features/technical indicators now include the repaired April data.
- From 2026-05-06 onward, old vs new replay top20 overlap dropped sharply on
  several dates; 2026-05-06 was only 3/20.

Archive comparison:

- Archived daily scores reflect the state at the time they were generated.
- After cache repair, archived scores no longer match the recomputed source:
  latest 2026-06-04 archive vs new strict/replay top20 overlap is 19/20, but
  score mean absolute difference is about 5.25 and max difference about 31.35.
- Therefore archived scores are useful as historical evidence, but not as the
  clean source after cache repair.

Strict sanity:

- Strict one-day replay for 2026-06-04:
  `score_backfill_strict_20260605_212539`
- New pack replay vs strict on 2026-06-04:
  top100 overlap 100/100, max score diff 0.01, mean score diff about 0.0021.
- Conclusion: the 65-day pack replay is acceptable for immediate movement-label
  source-of-truth use. A full strict 65-day replay can be run later if a more
  conservative archival artifact is needed.

Regime caveat correction:

- Daily live regime is VIX-based. `phase3/daily_runner.py:get_current_vix`
  reads `cfg.vix_symbol` with default `^VIX` and returns the live `Regime`.
- `^VIX` was available through 2026-06-04 in the engine cache, and
  `get_current_vix(...)` returned VIX 15.4 / BULL on the local check.
- SPY cache staleness should not block score-history source-of-truth replay for
  daily regime labels. SPY remains relevant for benchmark/cache-health warnings,
  not for the live recommendation `Regime` field.

## Suggested Mail Placement

Add the formatted section near the existing universe-delta/shadow sections:

```text
[Top-N Movement Labels]
  Rising/New:
      DELL   #4  d1=+31 d3=+51 d5=+55 [FAST_RISER,NEW_ENTRY]
       HPE   #3  d1=+18 d3=+35 d5=+40 [NEW_ENTRY]
  Falling/Watch:
    ...
  Stable Core:
    ...
```

## Tests

Targeted test:

```bash
python3 -m pytest phase3/tests/test_topn_movement_classifier.py
```

The tests pin:

- `NEW_ENTRY + FAST_RISER` for a DELL/HPE-like Top-N jump.
- `FALLING` labels for portfolio tickers outside current Top-N.
- `CORE_STABLE` and `REGIME_SWITCHED` tags.
- score CSV discovery that excludes shadow runs by default.

## Next Steps For Cursor

1. Decide mail integration point in `phase3/daily_runner.py` or `phase3/mailer.py`.
2. Load recent baseline daily `scores.csv` snapshots from `daily_runs`.
3. Build labels for today's recommendation tickers or today's Top-N.
4. Add `MovementLabel`, `MovementTags`, and `RankDelta3d/5d` columns to the email
   table or append `format_movement_email_section(labels)`.
5. Keep this reporting-only until a later signal-level experiment validates
   rank-velocity features as GA fitness or ML features.
