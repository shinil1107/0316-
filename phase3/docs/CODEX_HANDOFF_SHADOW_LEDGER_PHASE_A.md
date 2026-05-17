# CODEX HANDOFF - Stateful Shadow Ledger Phase A/B/C/D

Date: 2026-05-17

Owner/context:
- This note records the Codex-side implementation of a stateful shadow portfolio ledger.
- Phase A added standalone replay.
- Phase B added a daily-run-friendly `update-latest` integration seam.
- Phase B.1 adopted the tool into the original project and wired it into `daily_runner.py`.
- Phase C added control-panel visibility and a manual update button.
- Phase D added stateful ledger summary text to daily email/report flow.
- Current implementation exists in both the Codex mirror and the production/original project.
- Mirror path: `/Users/shin-il/PyCharmMiscProject_codex/0316-`
- Original project path: `/Users/shin-il/PyCharmMiscProject/0316-`

## 1. Why This Exists

The existing daily shadow run compares live vs shadow scores/recommendations day by day, but it does not keep a separate shadow portfolio state.

That means a 30-day comparison like `Baseline V2 vs ens_v/P11 shadow` can be misleading because shadow recommendations were previously generated against the live holdings state, not a shadow-specific holdings ledger.

Phase A adds an independent replay tool:

```text
Daily baseline scores -> virtual baseline account -> baseline NAV/trades
Daily shadow scores   -> virtual shadow account   -> shadow NAV/trades
```

Both ledgers start from the same initial holdings/cash, then diverge based on their own score-driven recommendations.

Phase B adds a safer operational bridge:

```text
Existing daily/shadow artifacts -> shadow_ledger update-latest
                               -> stable latest pointer + latest summary
```

This lets the daily pipeline or UI consume the most recent stateful comparison without directly mutating production holdings, broker ledgers, or daily artifacts by default.

Phase B.1 connects the original `daily_runner.py`:

```text
daily_runner baseline artifact
-> shadow artifact
-> shadow_ledger update-latest
-> output/shadow_ledgers/<label>/latest_pointer.json
```

The integration is non-blocking. If shadow ledger replay fails, the daily run prints a warning but does not invalidate the daily recommendation artifact.

Phase C/D adds operator visibility:

```text
Autotrade Control Panel
-> Stateful Shadow Ledger section
-> Update Shadow Ledger button
-> Show Latest Summary button

Daily email shadow section
-> existing shadow diff summary
-> stateful baseline-vs-shadow NAV/return/MDD summary
```

## 2. Implemented File

Added:

```text
phase3/shadow_ledger.py
```

This is a standalone CLI replay/update module. It does not mutate:

- `holdings_log.xlsx`
- autotrade order ledgers
- daily run artifacts
- broker state

By default it only reads existing `daily_runs/*_daily` and `daily_runs/*_shadow` artifacts, then writes replay outputs to a separate `shadow_ledgers` folder.

Phase B has one optional mutation flag:

```text
--write-artifact-summary
```

When this flag is provided to `update-latest`, the tool also writes a small `shadow_ledger_summary.json/md` into the latest shadow artifact folder. This is intentionally off by default.

## 3. Core Assumption

Shadow fills are synthetic:

```text
Every actionable recommendation is assumed to fill 100% at artifact Price.
```

Default cost model:

```text
commission_bps = 10
slippage_bps   = 5
```

This is intentional for Phase A. The goal is not broker-realistic execution. The goal is signal/portfolio comparison under a consistent fill assumption.

## 4. Implementation Summary

The replay tool:

1. Discovers paired daily/shadow artifact folders.
2. Filters by `shadow_diff_summary.json.label`, default `P11_FUNDB_ANCHOR`.
3. Uses the first baseline artifact's `portfolio_before.csv` as initial holdings.
4. Uses the first baseline artifact's `run_meta.json.cash_balance` and `total_capital` when available.
5. Builds two independent `SimPortfolio` instances:
   - baseline ledger
   - shadow ledger
6. For each date:
   - loads the ledger-specific `scores.csv`
   - marks virtual holdings to artifact prices
   - resolves regime strategy via `resolve_strategy()`
   - applies an independent in-memory `buy_grace_days` filter
   - calls existing `generate_recommendations()`
   - applies recommendations through `SimPortfolio.apply_actions()`
7. Writes daily NAV, trades, final holdings, and comparison summaries.

Phase B additionally:

1. Adds an `update-latest` subcommand.
2. Allows `--start` and `--shadow-label` to default from `phase3/config.yaml`:
   - `shadow.start_date`
   - `shadow.label`
3. Replays through the latest paired daily/shadow artifact.
4. Writes stable latest files under:
   - `<output_root>/<shadow_label>/latest_pointer.json`
   - `<output_root>/<shadow_label>/latest_compare_summary.md`
5. Adds portfolio comparison outputs:
   - `nav_compare.csv`
   - `holdings_compare_final.csv`
   - `shadow_only_holdings_final.csv`
   - `baseline_only_holdings_final.csv`
6. Supports `--min-runs` so automation can fail early if not enough paired history exists.

Phase B.1 original `daily_runner.py` integration:

1. Adds `_run_shadow_ledger_update()` as a non-blocking helper.
2. Calls it after `_run_shadow_pass()` only when the fresh shadow artifact folder exists.
3. Uses the same `phase3/config.yaml` and existing `paths.output_dir`.
4. Writes stateful ledger outputs to:
   - `<paths.output_dir>/shadow_ledgers/<shadow.label>/`
5. Leaves `--write-artifact-summary` disabled.
6. Supports optional config keys under `shadow` without requiring them:
   - `ledger_enabled` default `true`
   - `ledger_min_runs` default `1`
   - `ledger_commission_bps` default `10.0`
   - `ledger_slippage_bps` default `5.0`
   - `ledger_timeout_sec` default `600`

Phase C control panel integration:

1. Adds a `Stateful Shadow Ledger` section to `phase3/autotrade/control_panel.py`.
2. Shows latest pointer path and latest comparison summary.
3. Adds `Update Shadow Ledger` button:
   - runs `phase3/shadow_ledger.py update-latest`
   - uses paper profile's `phase3/config.yaml` and `paths.output_dir`
   - writes to `<paths.output_dir>/shadow_ledgers`
4. Adds `Show Latest Summary` button:
   - displays `latest_compare_summary.md` in the panel output area
   - falls back to `latest_pointer.json` if markdown is missing
5. Adds unit-test coverage for latest-pointer scanning and update command construction.

Phase D daily email/report integration:

1. `_run_shadow_ledger_update()` now returns compact summary text after a successful update.
2. `run_daily()` appends this text to existing `shadow_email_text`.
3. `mailer.py` already supports `shadow_text`; empty recommendation emails now include `shadow_text` too.
4. The email section includes:
   - label
   - window and run count
   - baseline final NAV/return
   - shadow final NAV/return
   - NAV delta, return delta, MDD delta
   - replay output directory

Important reuse:

- `daily_runner.generate_recommendations`
- `daily_runner.build_engine_cfg`
- `simulator.SimPortfolio`
- `simulator.resolve_strategy`
- `simulator._compute_daily_limit`

This keeps Phase A minimally invasive and aligned with current production recommendation logic.

## 5. CLI Usage

Basic usage:

```bash
python3 phase3/shadow_ledger.py replay \
  --start 2026-05-08 \
  --end 2026-06-07 \
  --shadow-label P11_FUNDB_ANCHOR
```

Explicit artifact root:

```bash
python3 phase3/shadow_ledger.py replay \
  --start 2026-05-08 \
  --end 2026-05-17 \
  --shadow-label P11_FUNDB_ANCHOR \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs"
```

Smoke-test output root:

```bash
python3 phase3/shadow_ledger.py replay \
  --start 2026-05-08 \
  --end 2026-05-17 \
  --shadow-label P11_FUNDB_ANCHOR \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root /tmp/shadow_ledger_phase_a_smoke2
```

Phase B latest update:

```bash
python3 phase3/shadow_ledger.py update-latest \
  --config "/Users/shin-il/PyCharmMiscProject/0316-/phase3/config.yaml" \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root /tmp/shadow_ledger_phase_b2 \
  --min-runs 1 \
  --commission-bps 10 \
  --slippage-bps 5
```

Optional write-back into latest shadow artifact:

```bash
python3 phase3/shadow_ledger.py update-latest \
  --config "/Users/shin-il/PyCharmMiscProject/0316-/phase3/config.yaml" \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers" \
  --write-artifact-summary
```

Do not enable `--write-artifact-summary` until the team is comfortable with writing small summary files into daily artifact folders.

## 6. Output Files

Default output root:

```text
<config.paths.output_dir>/shadow_ledgers/<shadow_label>/replay_<start>_<end>/
```

Files:

```text
baseline_daily_nav.csv
shadow_daily_nav.csv
nav_compare.csv
baseline_trades.csv
shadow_trades.csv
baseline_holdings_final.csv
shadow_holdings_final.csv
holdings_compare_final.csv
shadow_only_holdings_final.csv
baseline_only_holdings_final.csv
compare_summary.json
compare_summary.md
```

Phase B stable latest files:

```text
<config.paths.output_dir>/shadow_ledgers/<shadow_label>/latest_pointer.json
<config.paths.output_dir>/shadow_ledgers/<shadow_label>/latest_compare_summary.md
```

The pointer contains:

```text
latest_date
latest_run_id
replay_dir
compare_summary_path
nav_compare_path
holdings_compare_path
baseline_final_nav
shadow_final_nav
nav_delta
return_delta_pct
```

## 7. Verified Smoke Test

Command run in Codex mirror:

```bash
python3 -m py_compile phase3/shadow_ledger.py

python3 phase3/shadow_ledger.py replay \
  --start 2026-05-08 \
  --end 2026-05-17 \
  --shadow-label P11_FUNDB_ANCHOR \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root /tmp/shadow_ledger_phase_a_smoke2 \
  --commission-bps 10 \
  --slippage-bps 5
```

Result:

```text
Replay complete: 7 paired runs
Output: /private/tmp/shadow_ledger_phase_a_smoke2/P11_FUNDB_ANCHOR/replay_20260508_20260517
Shadow vs baseline: NAV delta $79.49, return delta +0.0749 pp
```

Important interpretation:

- This is a short 7-run smoke test, not a performance conclusion.
- It confirms the stateful replay pipeline runs end to end.
- Weekend/repeated score artifacts are deduped by shadow score signature unless `--include-duplicate-scores` is passed.

Phase B command run in Codex mirror:

```bash
python3 -m py_compile phase3/shadow_ledger.py

python3 phase3/shadow_ledger.py update-latest \
  --config "/Users/shin-il/PyCharmMiscProject/0316-/phase3/config.yaml" \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root /tmp/shadow_ledger_phase_b2 \
  --min-runs 1 \
  --commission-bps 10 \
  --slippage-bps 5
```

Result:

```text
Latest shadow ledger updated: 7 paired runs
Replay output: /private/tmp/shadow_ledger_phase_b2/P11_FUNDB_ANCHOR/replay_20260508_20260517
Latest pointer: /private/tmp/shadow_ledger_phase_b2/P11_FUNDB_ANCHOR/latest_pointer.json
Shadow vs baseline: NAV delta $79.49, return delta +0.0749 pp
```

Phase C/D verification in original project:

```bash
python3 -m py_compile \
  phase3/daily_runner.py \
  phase3/shadow_ledger.py \
  phase3/mailer.py \
  phase3/autotrade/control_panel.py \
  phase3/tests/test_r10_control_panel_dashboard.py

python3 -m unittest \
  phase3.tests.test_r9_control_panel \
  phase3.tests.test_r10_control_panel_dashboard

python3 phase3/shadow_ledger.py update-latest \
  --config phase3/config.yaml \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers" \
  --min-runs 1
```

Result:

```text
Ran 28 tests in 0.013s
OK

Latest shadow ledger updated: 7 paired runs
Latest pointer: /Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers/P11_FUNDB_ANCHOR/latest_pointer.json
Shadow vs baseline: NAV delta $79.66, return delta +0.0750 pp
```

## 8. Codex Mirror vs Original Project

Implementation now exists in:

```text
/Users/shin-il/PyCharmMiscProject_codex/0316-/phase3/shadow_ledger.py
/Users/shin-il/PyCharmMiscProject_codex/0316-/phase3/docs/CODEX_HANDOFF_SHADOW_LEDGER_PHASE_A.md
/Users/shin-il/PyCharmMiscProject/0316-/phase3/shadow_ledger.py
/Users/shin-il/PyCharmMiscProject/0316-/phase3/docs/CODEX_HANDOFF_SHADOW_LEDGER_PHASE_A.md
```

The Codex mirror remains useful for experimentation. The original project is now wired for operational daily updates.

Cursor is usually working in the original project:

```text
/Users/shin-il/PyCharmMiscProject/0316-
```

Recommendation:

- Keep Codex mirror for experimental implementations and smoke tests.
- Keep original as the operational source of truth.
- If Cursor changes `daily_runner.py`, preserve the non-blocking `_run_shadow_ledger_update()` call after shadow artifact creation.

## 9. Known Limitations

Phase A/B/C/D is production-wired but still synthetic:

- It is connected to original `daily_runner.py` after shadow artifact creation.
- It has not been exercised through a full fresh daily run in this Codex session to avoid creating an extra operational daily artifact.
- It has a control-panel section/button, but the Tkinter UI itself was not opened in this Codex session.
- It does not write Excel outputs.
- It does not use real broker fills.
- It does not model partial fills, cancel/reprice, or pre-market price adjustments.

Current history support:

- `buy_grace_days` is implemented independently per ledger.
- `sell_grace` state flows through `SimPortfolio.save_recommendations()`.
- Dynamic exit `history` is passed as `None`; triggers that need deep price/score history may not fully activate in this replay mode yet.

## 10. Suggested Next Steps

Completed - Original-project adoption:

1. `phase3/shadow_ledger.py` copied into original project.
2. This handoff doc copied into original `phase3/docs`.
3. Original `phase3/daily_runner.py` now calls `_run_shadow_ledger_update()` after fresh shadow artifact creation.
4. `py_compile` passed for original `daily_runner.py` and `shadow_ledger.py`.
5. Original `shadow_ledger.py update-latest` smoke passed with `/tmp` output.

Completed - Phase C/D operator visibility:

1. Control panel has a `Stateful Shadow Ledger` section.
2. Control panel can manually run `update-latest`.
3. Control panel can display the latest markdown summary.
4. Daily email shadow text includes stateful ledger NAV/return/MDD summary after successful update.
5. Real output latest pointer generated:
   - `/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers/P11_FUNDB_ANCHOR/latest_pointer.json`
6. `unittest` coverage for R9/R10 control panel passed: 28 tests.

Next - Daily integration validation:

1. On the next normal daily run, confirm console output includes `[Shadow Ledger] Updating stateful baseline vs shadow ledger...`.
2. Confirm `<paths.output_dir>/shadow_ledgers/<shadow.label>/latest_pointer.json` timestamp updates.
3. Confirm the daily email includes both:
   - shadow recommendation diff
   - `[Stateful Shadow Ledger]` section
4. Confirm daily run remains successful even if the ledger update emits a warning.

Next - Reporting polish:

1. Add per-ticker attribution:
   - shadow-only holdings
   - baseline-only holdings
   - top contributors to NAV delta
2. Add turnover and trade-count comparison by date.
3. Add drawdown path comparison.
4. Once stable, decide whether to enable `--write-artifact-summary` so the latest shadow artifact directly contains a small stateful-ledger summary.

## 11. Cursor Prompt Draft

If Cursor should continue this work in the original project, use:

```text
Read phase3/docs/CODEX_HANDOFF_SHADOW_LEDGER_PHASE_A.md.

Goal:
Review and continue Codex's Phase A/B/C/D stateful shadow ledger original-project integration.

Tasks:
1. Read phase3/shadow_ledger.py and the _run_shadow_ledger_update() helper in phase3/daily_runner.py.
2. Preserve the non-blocking behavior: shadow ledger failures must warn, not break daily artifacts.
3. Preserve control-panel Shadow Ledger buttons in phase3/autotrade/control_panel.py.
4. Preserve daily email [Stateful Shadow Ledger] section.
5. Run py_compile.
6. Run unittest:
   python3 -m unittest phase3.tests.test_r9_control_panel phase3.tests.test_r10_control_panel_dashboard
7. Run update-latest with:
   --config phase3/config.yaml
   --daily-runs-dir "<real output>/daily_runs"
   --output-root /tmp/shadow_ledger_phase_b_smoke
   --min-runs 1
8. Confirm latest_pointer.json and latest_compare_summary.md are generated.
9. On the next real daily run, confirm latest_pointer.json updates automatically under <paths.output_dir>/shadow_ledgers/<shadow.label>/.
10. Do not enable --write-artifact-summary yet unless explicitly requested.
11. Do not mutate holdings_log.xlsx or autotrade ledgers.
```
