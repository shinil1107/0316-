# Cursor Handoff - Top-N Movement Mail Labels

- Date: 2026-06-05
- Owner intent: portfolio-level reporting only.
- Scope: add ticker movement labels to the automatic daily email report.
- Do not use this at signal level yet. Do not alter scores, portfolio weights,
  trade actions, exit triggers, autotrading state, holdings, or recommendation
  persistence.

## Current Score History Source

Recent 3-month score history has been rebuilt and is ready:

```text
/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/topn_movement_classifier/score_history_backfill/score_backfill_pack_replay_20260605_212314
```

Daily score files:

```text
/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/topn_movement_classifier/score_history_backfill/score_backfill_pack_replay_20260605_212314/daily_runs/*/scores.csv
```

Coverage:

- 2026-03-04 through 2026-06-04.
- 65 snapshots.
- Missing pandas business days are 2026-04-03 and 2026-05-25, both market
  holidays.
- `^VIX` was available through 2026-06-04. Live daily regime is VIX-based via
  `daily_runner.get_current_vix(...)`; SPY staleness does not block this label
  history.

Related source-of-truth memo:

```text
phase3/docs/topn_movement_classifier/SCORE_HISTORY_SOURCE_OF_TRUTH_20260605.md
```

## Utility Files

Use the existing independent utility:

```text
phase3/topn_movement_classifier/__init__.py
phase3/topn_movement_classifier/classifier.py
phase3/tests/test_topn_movement_classifier.py
```

Main API:

```python
from topn_movement_classifier import (
    MovementConfig,
    classify_topn_movement,
    discover_recent_score_csvs,
    format_movement_email_section,
    load_score_snapshots,
    load_scores_csv,
)
```

If importing outside `phase3/daily_runner.py`, use the package form:

```python
from phase3.topn_movement_classifier import ...
```

## Label Rules

Rank delta is:

```text
past_rank - today_rank
```

Positive means the ticker moved up. Example: rank 54 to rank 4 is `+50`.

Primary labels:

| Label | Default rule |
|---|---|
| `RISING` | 3d rank delta >= +20 OR 5d rank delta >= +30 |
| `FALLING` | 3d rank delta <= -20 OR 5d rank delta <= -30 |
| `SIDEWAYS` | neither rising nor falling |

Tags:

| Tag | Default rule |
|---|---|
| `FAST_RISER` | 1d delta >= +30 OR 3d delta >= +50 |
| `NEW_ENTRY` | today in Top-N and absent from prior 3 Top-N snapshots |
| `CORE_STABLE` | present in Top-N for at least 7 of last 10 snapshots |
| `CHOPPY` | repeated direction flips with enough rank range |
| `REGIME_SWITCHED` | previous snapshot regime differs from today's regime |

Operator-favorite pattern:

```text
RISING + NEW_ENTRY and/or FAST_RISER
```

Examples from current repaired replay latest date:

- `GLW` rank #20: `RISING`, `NEW_ENTRY`, 3d delta +37.
- `COHR` rank #10: `RISING`, `CORE_STABLE`, 3d delta +28.
- `TER` rank #16: `RISING`, `CHOPPY`, 3d delta +21.

## Integration Target

Recommended hook point:

```text
phase3/daily_runner.py
```

Place the label build after `write_daily_run_artifact(...)` succeeds and before
`send_daily_email(...)`.

Reason:

- `write_daily_run_artifact(...)` writes the current run's full `scores.csv`.
- `send_daily_email(...)` is still downstream.
- This avoids changing score generation, recommendation generation, execution
  templates, holdings, or saved recommendations.

Important guardrail:

```text
Do not mutate the `recos` DataFrame used for artifact/execution/state.
Create `recos_for_mail = recos.copy()` and add label columns only to that copy.
Pass the copy into `send_daily_email(...)`.
```

This keeps labels as report-only metadata.

## Suggested Daily Runner Helper

Add a small helper in `daily_runner.py` or a separate tiny module.  Keep failures
non-fatal.

```python
def _attach_movement_labels_for_mail(conf, recos, run_dir, top_n):
    if recos is None or recos.empty:
        return recos, ""

    recos_for_mail = recos.copy()

    try:
        from pathlib import Path
        from topn_movement_classifier import (
            MovementConfig,
            classify_topn_movement,
            discover_recent_score_csvs,
            format_movement_email_section,
            load_score_snapshots,
            load_scores_csv,
        )

        output_daily_runs = Path(conf["paths"]["output_dir"]).expanduser() / "daily_runs"
        backfill_daily_runs = Path(
            "/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/topn_movement_classifier/"
            "score_history_backfill/score_backfill_pack_replay_20260605_212314/daily_runs"
        )

        current_scores_path = Path(run_dir) / "scores.csv"
        today_scores = load_scores_csv(current_scores_path)

        history_paths = []
        if backfill_daily_runs.exists():
            history_paths.extend(sorted(backfill_daily_runs.glob("*/scores.csv")))
        history_paths.extend(
            discover_recent_score_csvs(
                output_daily_runs,
                limit=80,
                suffix="_daily",
                include_shadow=False,
            )
        )

        # Exclude current file from history; today_scores is loaded explicitly.
        history_paths = [p for p in history_paths if Path(p).resolve() != current_scores_path.resolve()]
        history_scores = load_score_snapshots(history_paths)

        labels = classify_topn_movement(
            today_scores=today_scores,
            history_scores=history_scores,
            config=MovementConfig(top_n=int(top_n or 20)),
            target_tickers=recos_for_mail["Ticker"].tolist(),
        )

        label_cols = labels[[
            "ticker",
            "primary_label",
            "tags",
            "delta_rank_1d",
            "delta_rank_3d",
            "delta_rank_5d",
            "delta_rank_10d",
            "topn_presence_10d",
            "reason",
        ]].rename(columns={
            "ticker": "Ticker",
            "primary_label": "MovementLabel",
            "tags": "MovementTags",
            "delta_rank_1d": "RankDelta1d",
            "delta_rank_3d": "RankDelta3d",
            "delta_rank_5d": "RankDelta5d",
            "delta_rank_10d": "RankDelta10d",
            "topn_presence_10d": "TopNPresence10d",
            "reason": "MovementReason",
        })

        recos_for_mail["Ticker"] = recos_for_mail["Ticker"].astype(str).str.upper()
        recos_for_mail = recos_for_mail.merge(label_cols, on="Ticker", how="left")
        movement_text = format_movement_email_section(labels)
        return recos_for_mail, movement_text

    except Exception as e:
        print(f"  [WARN] Movement label build failed: {type(e).__name__}: {e}")
        return recos_for_mail, ""
```

Then near email send:

```python
recos_for_mail, movement_text = _attach_movement_labels_for_mail(
    conf=conf,
    recos=recos,
    run_dir=run_dir,
    top_n=conf.get("portfolio", {}).get("top_n", 20),
)

send_daily_email(
    conf, triggers, recos_for_mail, vix_close, regime, hm, health,
    computed_daily_limit=daily_limit,
    universe_delta_text=universe_delta_text,
    shadow_text=shadow_email_text,
    movement_text=movement_text,  # add this optional arg
)
```

## Mailer Changes

Recommended small change in `phase3/mailer.py`:

1. Add optional `movement_text: str = ""` to `send_daily_email(...)`.
2. Add optional `movement_text: str = ""` to `_build_trigger_body(...)`.
3. Insert `movement_text` near existing universe/shadow sections.
4. Add a compact inline badge to ticker rows if movement columns exist.

Minimal section-only implementation:

```python
def _build_trigger_body(..., shadow_text: str = "", movement_text: str = ""):
    ...
    if universe_delta_text:
        lines.append("")
        lines.append(universe_delta_text)
        lines.append("")

    if movement_text:
        lines.append("")
        lines.append(movement_text)
        lines.append("")
```

Better ticker-level implementation:

```python
def _movement_badge(row) -> str:
    label = str(row.get("MovementLabel", "") or "")
    tags = str(row.get("MovementTags", "") or "")
    if not label:
        return ""

    d1 = row.get("RankDelta1d")
    d3 = row.get("RankDelta3d")
    d5 = row.get("RankDelta5d")

    def fmt(x):
        try:
            if pd.isna(x):
                return "n/a"
            return f"{float(x):+.0f}"
        except Exception:
            return "n/a"

    hot = label == "RISING" or "NEW_ENTRY" in tags or "FAST_RISER" in tags
    watch = label == "FALLING"
    if not hot and not watch and "CORE_STABLE" not in tags:
        return ""

    bits = [label]
    if tags:
        bits.append(tags)
    bits.append(f"d1={fmt(d1)} d3={fmt(d3)} d5={fmt(d5)}")
    return "  [" + " | ".join(bits) + "]"
```

Then append `_movement_badge(r)` to each ticker line in BUY/SELL/HOLD/watch
sections.

## Display Recommendation

For the email, use both:

- Inline badge beside each ticker row.
- Compact summary section from `format_movement_email_section(labels)`.

Example inline:

```text
[ ] BUY_MORE  GLW      2 shares @ $86.10 -> use ~$172  [RISING | NEW_ENTRY | d1=+3 d3=+37 d5=+13]
```

Example section:

```text
[Top-N Movement Labels]
  Rising/New:
       GLW  #20  d1=  +3 d3= +37 d5= +13 [NEW_ENTRY]
      COHR  #10  d1=  +1 d3= +28 d5=  +6 [CORE_STABLE]
       TER  #16  d1=  -2 d3= +21 d5= +14 [CHOPPY]
  Falling/Watch:
    (none)
  Stable Core:
       STX   #1  d1=  +1 d3=  +1 d5=  +3 [CORE_STABLE]
```

## History Source Policy

Initial period:

- Use backfilled source-of-truth daily runs plus new live `output/daily_runs`.
- Prefer the current live run for today's score.

Later, after enough live daily scores accumulate:

- Cursor may remove the hardcoded backfill fallback and rely only on
  `<output_dir>/daily_runs/*_daily/scores.csv`.

Do not include by default:

- `_shadow` runs.
- `_dryrun` runs.
- archived runs, unless manually doing recovery/research.

## Tests / Validation

Existing unit test:

```bash
python3 -m unittest phase3.tests.test_topn_movement_classifier
```

`pytest` target also exists, if pytest is installed:

```bash
python3 -m pytest phase3/tests/test_topn_movement_classifier.py
```

Suggested Cursor smoke checks:

1. Run one dry-run daily flow with mail disabled or suppressed.
2. Confirm `scores.csv` is written before label attachment.
3. Confirm `recos_for_mail` has movement columns.
4. Confirm `recommendations.csv`, `execution_template.csv`, holdings, and
   autotrading intent files are unchanged by label attachment.
5. Confirm email body includes inline labels and/or the movement summary.

## Do Not Do Yet

- Do not feed labels back into signal scores.
- Do not use labels as buy/sell filters.
- Do not change GA/ML objective.
- Do not alter autotrading gates.
- Do not persist labels into holdings state.

This feature is a reporting lens first. Signal-level rank-velocity research can
be a later lab after the operator has observed the labels in live mail for a few
weeks.

