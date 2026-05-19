# CODEX TRANSFER HANDOFF - Original Project Session

Date: 2026-05-18 KST

Purpose:
This document is for starting a new Codex session directly from the original project folder while preserving the current development context.

New session should open here:

```text
/Users/shin-il/PyCharmMiscProject/0316-
```

The previous Codex session was sandboxed under:

```text
/Users/shin-il/PyCharmMiscProject_codex
```

Because the active sandbox root was the `_codex` mirror, every write to the original project required approval. Going forward, use the original project as the main working directory so normal file edits do not require repeated sandbox escalation.

## 1. Project Directory Policy

Use the original project as the source of truth:

```text
/Users/shin-il/PyCharmMiscProject/0316-
```

Use the codex mirror only for scratch experiments or isolated performance-test prototypes:

```text
/Users/shin-il/PyCharmMiscProject_codex/0316-
```

Default reference rule:
When the user asks to inspect code, docs, artifacts, live policy, or current implementation, assume the original project path unless explicitly told otherwise.

Editing rule:
The user has already given broad permission to edit the original project. Still preserve unrelated user/Cursor changes and never revert files unless explicitly asked.

## 2. Current Git State At Transfer

At the time this handoff was written:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
git status --short
```

returned no output.

Meaning:
The original project appeared clean at this transfer point.

## 3. Shadow Ledger Current State

Feature goal:
Maintain an independent stateful virtual portfolio ledger for the live baseline signal and the shadow signal, so a 30-day comparison is not just daily score/recommendation overlap but actual simulated portfolio state comparison.

Implemented file:

```text
phase3/shadow_ledger.py
```

Primary command:

```bash
python3 phase3/shadow_ledger.py update-latest \
  --config phase3/config.yaml \
  --daily-runs-dir "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs" \
  --output-root "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers" \
  --min-runs 1
```

Current latest output:

```text
/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers/P11_FUNDB_ANCHOR/latest_pointer.json
/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers/P11_FUNDB_ANCHOR/latest_compare_summary.md
```

Recent verified result:

```text
Latest shadow ledger updated: 7 paired runs
Shadow vs baseline: NAV delta $79.66, return delta +0.0750 pp
```

Important design:
Shadow fills are synthetic. Every actionable recommendation is assumed to fill 100% at artifact price, with configurable commission/slippage. This is for signal/portfolio comparison, not broker-realistic execution.

## 4. Cursor's Latest Shadow Ledger Corrections

Cursor made three important follow-up corrections after Codex's earlier implementation:

1. Removed production `CODEX_MIRROR_ALLOW_RUN` setting from `phase3/shadow_ledger.py`.

Status:
Confirmed. Production copy now only sets:

```python
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-codex")
```

2. Added regression tests for `shadow_ledger._run_replay_job` / `update-latest`.

Status:
Confirmed. Tests live in:

```text
phase3/tests/test_shadow_ledger.py
```

3. Removed the shadow ledger section from the autotrade UI.

Status:
Confirmed. `phase3/autotrade/control_panel.py` no longer contains the prior `Stateful Shadow Ledger` UI section.

Rationale:
The shadow ledger should update automatically from `daily_runner`; the autotrade execution UI should stay focused on paper submit / T10 apply / safety gates.

## 5. Daily Runner Integration

The original `phase3/daily_runner.py` is wired so that after the shadow pass creates a fresh shadow artifact, it runs the stateful ledger update automatically.

Expected flow:

```text
daily_runner normal run
-> baseline daily artifact
-> shadow artifact
-> shadow_ledger update-latest
-> output/shadow_ledgers/<shadow.label>/latest_pointer.json
```

The integration is intentionally non-blocking:
If the shadow ledger update fails, daily recommendations and artifacts should not be invalidated.

Next live validation:
On the next normal daily run, confirm console output includes:

```text
[Shadow Ledger] Updating stateful baseline vs shadow ledger...
```

Then confirm:

```text
/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/shadow_ledgers/P11_FUNDB_ANCHOR/latest_pointer.json
```

has a fresh timestamp.

## 6. Validation Commands

Run these after opening the new original-folder Codex session:

```bash
python3 -m py_compile \
  phase3/daily_runner.py \
  phase3/shadow_ledger.py \
  phase3/mailer.py \
  phase3/autotrade/control_panel.py \
  phase3/tests/test_shadow_ledger.py
```

```bash
python3 -m unittest phase3.tests.test_shadow_ledger
```

Expected recent result:

```text
Ran 29 tests
OK
```

```bash
python3 -m unittest \
  phase3.tests.test_r9_control_panel \
  phase3.tests.test_r10_control_panel_dashboard
```

Expected recent result:

```text
Ran 26 tests
OK
```

Note:
`pytest` was not installed in the active Python environment, so use `unittest` unless dependencies change.

## 7. Autotrading Context

Autotrading implementation is separate from the shadow ledger.

Important concepts already discussed:

```text
nccs = unfilled/open order inquiry channel
ccnl = executed/filled order inquiry channel
ODNO = KIS broker order number
```

Current high-level autotrading goal:

```text
US market opens
-> program starts automatically
-> today's intent/recommendation is checked
-> paper/live-safe order flow executes
-> pending orders are managed with cancel/reprice rules
-> fills are recorded
-> T10/applicator reflects fills into local holdings
-> report/email is sent
```

Near-term remaining autotrading items:

1. Validate the next market-open paper flow end to end.
2. Confirm cancel/reprice path behavior on real KIS paper responses.
3. Keep T10/applicator idempotency protections intact.
4. Later, design scheduler/autostart as a separate operator layer, not inside broker logic.

## 8. ML / Performance Context

Recent live-policy signal verification used original project policy and real output artifacts.

Important signals:

```text
Baseline_V2
P2_OOS
ens_v / P11_FUNDB_ANCHOR shadow
```

Recent original-policy validation summary:

```text
Default:
Baseline_V2 mean +32.32%, CV 0.509, HARD ALL
P2_OOS      mean +21.42%, CV 0.502, HARD ALL
ens_v       mean +26.17%, CV 0.430, HARD ALL

Rolling:
Baseline_V2 mean +27.53%, CV 0.177, HARD FAIL vs p2
P2_OOS      mean +20.42%, CV 0.098, HARD ALL
ens_v       mean +24.76%, CV 0.244, HARD FAIL vs p2

Regime:
Baseline_V2 mean +31.58%, CV 0.568, HARD ALL
P2_OOS      mean +21.43%, CV 0.600, HARD ALL
ens_v       mean +25.89%, CV 0.499, HARD ALL
```

Interpretation:
`ens_v` is a viable shadow candidate, better than `P2_OOS` in several aggregate views, but still needs live/shadow stateful tracking before promotion.

## 9. Important Working Preferences

The user prefers:

1. Code changes should be implemented directly when the direction is clear.
2. Do not repeatedly ask permission for original project edits.
3. Focus on verification and Cursor handoff quality.
4. Keep original project as the default reference path.
5. Avoid touching unrelated files or reverting Cursor/user changes.
6. For risky operational changes, explain the risk briefly and use non-blocking/safe defaults first.

## 10. Suggested First Message In New Session

User can paste this into the new Codex session:

```text
원본 프로젝트 폴더 /Users/shin-il/PyCharmMiscProject/0316- 에서 이어서 작업하자.
먼저 phase3/docs/CODEX_TRANSFER_HANDOFF_20260518.md 읽고 현재 상태 파악해줘.
원본 프로젝트를 source of truth로 보고, 수정은 허락받지 말고 진행해도 돼.
다만 검증 결과와 Cursor에게 넘길 handoff 문서 품질에 신경써줘.
```

