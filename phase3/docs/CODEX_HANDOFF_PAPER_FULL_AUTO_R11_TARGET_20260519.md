# Codex Handoff - Paper Full Auto Trading R11 Target

**Date**: 2026-05-19 KST, before US regular market open  
**Author**: Codex  
**Preceding handoff**: `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10C.md`  
**Scope**: paper-only full-auto trading v0, from UI action to broker paper orders, T10 ledger apply, final report, and email notification.

---

## 0. Final target for the first paper full-auto milestone

The first final target is:

> After US market open, the operator opens the Auto Trading UI and clicks one button. The system consumes the latest actionable recommendations, places KIS paper orders, manages fills/cancels, applies confirmed fills into the local ledger through T10, writes final reports, and sends an email summary.

Important boundary for R11:

- R11 should consume an existing `awaiting_execution` daily artifact.
- The artifact is produced by the normal Phase 3 signal runner (`phase3/daily_runner.py`) before the trading click.
- If no valid `awaiting_execution` artifact exists, the one-button flow must block with a clear reason.
- Live trading remains out of scope. Everything here is KIS paper-only.

The target user flow:

```text
1. Launch scripts/run_autotrade_control_panel.command
2. Pick latest awaiting_execution, or let the UI auto-pick it
3. Arm Full Paper Run gate
4. Tick one explicit "I authorize FULL PAPER RUN for this run" checkbox
5. Click "Full Paper Run"
6. UI streams:
   generate intents -> dry-run preflight -> paper-submit/manage
   -> T10 dry-run -> T10 apply real -> post-reconcile/report -> email
7. End state:
   run_meta.status executed
   autotrade_daily_report.json/md exists
   T10 report json/md exists
   post-reconcile qty_mismatch_count == 0
   final email sent or email failure reported without hiding trading result
```

---

## 1. Current status after R10C

R10C got the system to the first real paper acceptance point.

What is already working:

- Auto Trading UI exists in `phase3/autotrade/control_panel.py`.
- UI can load latest run, show gates, show reports, and stream subprocess output.
- UI can generate `submitted_intents.json` for one BUY or all BUY candidates.
- UI can run dry-run preflight.
- UI can run paper submit + manage.
- UI can run T10 dry-run and T10 real apply as separate actions.
- `phase3/autotrade/daily_runner.py --paper-submit --apply-t10` already has the backend skeleton for submit plus apply in one CLI command.
- `phase3/autotrade/t10_applicator.py` is the sole local ledger writer and updates `run_meta.status`.
- `phase3/daily_runner.py` already sends the daily recommendation email through `phase3/mailer.py`.
- Shadow diff and shadow ledger email sections are already integrated into the normal daily email path.

What R10C proved live against KIS paper:

- KIS paper submit path accepted real paper orders.
- At least one order filled (`MRNA` in R10C Run 2).
- `ccnl` polling can fail with `RemoteDisconnected` after broker accept.
- UNKNOWN hard-stop behavior worked.
- T10 apply was correctly blocked because paper-submit ended `rc=2`.
- Cancel recovery through KIS paper worked.

What is still not complete:

- T10 Apply Real has not yet been exercised after a clean all-FILLED paper batch.
- Full Paper Run button is still deliberately disabled.
- Autotrade completion email is not yet wired. The existing mailer sends the pre-trade daily recommendation email, not the post-trade execution/apply summary.
- The one-click UI flow does not yet orchestrate generate-intents -> submit -> apply -> email.

---

## 2. Must-fix items before enabling one-click Full Paper Run

### P0-1. Add ccnl polling resilience

Why:

- R10C saw the same KIS paper `inquire-ccnl` disconnect pattern twice.
- The broker had already accepted the order, so classifying UNKNOWN after one transient polling exception is too brittle for full-auto.

Where:

- `phase3/autotrade/order_manager.py::manage_order`
- the `except Exception` arm around `_now_classify(...)` in the polling loop

Required behavior:

- Only retry polling exceptions after an order has already been accepted.
- Do not retry `place_order` exceptions blindly.
- Retry budget should be small, for example 2 retries with short backoff.
- Each retry should leave an observable audit/log trail.
- If retries fail, preserve today's conservative UNKNOWN hard-stop.

Tests:

- Extend `phase3/tests/test_r8_order_manager.py`.
- Add fixture: first ccnl poll raises, second ccnl poll returns FILLED.
- Add fixture: all retry attempts fail, final state remains UNKNOWN.

Acceptance:

- Simulated R10C APA case no longer turns UNKNOWN on the first transient poll exception.

### P0-2. Add intent-time limit-price freshness

Why:

- R10C showed `reco_price = previous close` is too stale for gap-prone tickers.
- The current batch `limit_pad_pct` is only a manual band-aid.

Where:

- `phase3/autotrade/intents_io.py`
- `phase3/autotrade/control_panel.py`
- likely `phase3/autotrade/kis_broker_adapter.py` if a quote helper needs a clean wrapper

Required behavior:

- Generate intent limits from fresh quote data when the operator enables it.
- For BUY:

```text
limit = max(reco_price * (1 + batch_pad_pct),
            current_ask_or_last * (1 + quote_pad_pct))
```

- If quote lookup fails for one symbol, surface the fallback in the confirmation dialog.
- Clamp or reject negative batch pad in the UI. Negative pad should not be allowed for the full-auto path.
- Confirmation dialog should show old limit, refreshed limit, and quote timestamp for each row or a concise sample plus warnings.

Tests:

- Pure tests for limit computation.
- Control-panel test that quote-refresh route calls the quote helper and falls back safely.

Acceptance:

- For a copied R10C recommendation row, refreshed limit moves toward current ask/last instead of yesterday's close.

### P0-3. Re-validate gates inside dangerous callbacks

Why:

- The UI button matrix requires Arm toggles, but the current `_on_paper_submit()` and `_on_t10_apply()` callbacks inject danger env vars into the subprocess env once invoked.
- Normal UI state prevents accidental invocation, but full-auto should have defense in depth at callback entry.

Where:

- `phase3/autotrade/control_panel.py`

Required behavior:

- Before starting paper submit, recompute `PanelState` and `compute_button_gates`.
- If the current gate says disabled, refuse and show the reason.
- Do the same before T10 apply and Full Paper Run.
- Avoid callback-level env escalation that bypasses the Arm state. Prefer inheriting already-armed `os.environ` or explicitly checking the Arm vars before constructing child env.

Tests:

- Unit test that direct callback-equivalent helper refuses submit/apply when gate state is not armed.
- If Tk callback remains hard to unit-test, extract small pure helpers for "build env for submit/apply only if armed".

Acceptance:

- Directly invoking the action helper cannot bypass the safety matrix.

### P0-4. Implement Full Paper Run coordinator in the UI

Why:

- The final target is one operator click after market open.
- Backend pieces exist, but the UI still requires multiple manual button presses.

Where:

- `phase3/autotrade/control_panel.py`
- optionally a new small helper module, for example `phase3/autotrade/full_paper_run.py`, if keeping the UI file smaller is worth it.

Recommended R11 shape:

```text
Full Paper Run button:
  1. refresh panel state
  2. resolve latest awaiting_execution run_id if run_id empty
  3. generate ALL intents with quote freshness
  4. run daily_runner --dry-run
  5. require rc=0
  6. run daily_runner --paper-submit --apply-t10
     with submit/cancel/apply gates already armed
  7. stream all stdout/stderr into the UI log
  8. refresh panel state
  9. send final autotrade email
  10. display final pass/fail state
```

Design choice:

- Prefer one backend invocation for submit + apply:

```bash
python3 -m phase3.autotrade.daily_runner \
  --profile paper \
  --run-id <RUN_ID> \
  --paper-submit \
  --apply-t10
```

- But keep dry-run as a separate first subprocess so the operator sees a clean preflight before mutation.
- If dry-run fails, do not submit.
- If paper-submit produces any UNKNOWN/open/partial blocker, do not apply T10.
- If T10 apply fails, do post-failure report and email but do not hide the failure.

UI safety contract:

- Keep Full Paper Run disabled until:
  - `run_id` exists
  - artifact status is `awaiting_execution`
  - `recommendations.csv` exists and has BUY candidates
  - global halt is off
  - T10 journal is clean
  - `KIS_ENV == paper`
  - paper submit, cancel, and T10 apply gates are armed
  - operator checked `I authorize FULL PAPER RUN`
- Full Paper Run should still ask one final confirmation dialog showing:
  - run_id
  - number of BUY intents
  - estimated notional
  - quote-refresh warnings
  - exact command stages

Tests:

- Button matrix tests for `full_paper_run`.
- Fake streaming runner test for successful stage sequence.
- Fake streaming runner test that dry-run rc != 0 blocks submit.
- Fake report test that submit rc=2 blocks apply.

Acceptance:

- One click performs the full staged sequence in fake tests.
- During live paper test, one click either reaches `executed` or stops safely with a complete report and no hidden side effects.

### P0-5. Add post-trade autotrade email

Why:

- `phase3/daily_runner.py` sends the daily recommendation email.
- The paper full-auto goal needs a final "what happened after trading" email.

Where:

- Prefer a small new helper:
  - `phase3/autotrade/autotrade_mailer.py`
- Reuse SMTP credential resolution from `phase3/mailer.py` if possible.
- Or add a function to `phase3/mailer.py` if the project prefers one mailer module.

Required email contents:

- Subject:

```text
[Paper Autotrade] 2026-05-19 rc=0 FILLED=<N> APPLY=OK
```

or on failure:

```text
[Paper Autotrade WARN] 2026-05-19 rc=2 hard_stop@manage_loop
```

- Body:
  - run_id
  - autotrade_run_id
  - mode
  - order outcome counts
  - filled rows with ticker, qty, avg fill, broker order id
  - hard_stop if any
  - T10 dry-run/apply rc
  - pre/post reconcile counts
  - paths to `autotrade_daily_report.md/json`
  - T10 report path
  - next operator action if failed

Required behavior:

- Email failure must not change trading rc.
- Email failure should be visible in UI log and final report if practical.

Tests:

- Pure body formatter test.
- SMTP sender mocked test.
- Full Paper Run stage test that email is called exactly once on success and once on hard-stop if configured.

Acceptance:

- After full paper run, operator receives final execution/apply summary email.

---

## 3. Strongly recommended P1 items

### P1-1. Continue-on-non-FILLED policy, opt-in only

R10C Run 2 skipped JBL because APA went UNKNOWN. That is conservative and safe, but full-auto may want independent tickers to continue.

Do not enable by default until balance refresh is implemented.

Required safeguards:

- Re-read broker balance between intents.
- Preserve run-level rc=2 if any ticker is non-FILLED.
- T10 apply remains blocked unless final outcome set is clean.
- UI label should make the policy obvious: "attempt remaining independent tickers even if one fails".

### P1-2. In-UI cancel and operator-cleared duplicate guard

The current recovery cancel path requires a shell. Full-auto operations should keep recovery inside the panel.

Required pieces:

- "Cancel broker order" dialog in `control_panel.py`.
- `OrderStore` event kind like `operator_cleared`.
- Guard that refuses operator-cleared unless fresh open-orders check shows the broker order is absent.

### P1-3. Stream drain guarantee

`run_subprocess_streaming()` currently joins stdout/stderr pump threads with a 1 second timeout before calling `on_done`. This is probably fine for normal output, but the contract says on_done fires after both pipes drain. Tighten this before relying on final snapshot/log email.

### P1-4. Full run idempotency state

Full Paper Run should write a lightweight stage marker so a UI crash can resume or at least explain what happened.

Suggested stage file:

```text
daily_runs/<RUN_ID>/full_paper_run_state.json
```

Fields:

- run_id
- started_at / finished_at
- current_stage
- stage rc values
- final rc
- email_sent
- report paths

This should be append-safe or atomically rewritten.

### P1-5. Launcher documentation cleanup

`scripts/run_autotrade_control_panel.command` now supports in-UI arming, but comments still emphasize terminal exports. Update comments so operator guidance matches current flow.

---

## 4. What not to do in R11

Avoid these until paper full-auto is accepted:

- Do not add live trading.
- Do not add scheduler/cron/launchd.
- Do not add FMP premarket strategy logic yet.
- Do not refactor the whole autotrade package.
- Do not combine signal generation and broker execution into one mega-click yet.
- Do not relax T10 apply gates for partial/unknown orders.

Premarket/FMP, auto-retraining, and broader modularization are important, but they should come after paper full-auto has a reliable acceptance loop.

---

## 5. Suggested implementation order

### Round R10D - reliability hardening

1. ccnl polling retry.
2. quote-fresh limit generation.
3. callback-level danger gate revalidation.
4. stream drain guarantee.

Exit criteria:

- Unit tests pass.
- Fake ccnl transient failure no longer causes immediate UNKNOWN.
- Batch intent generation can refresh limits from quotes or clearly warn on fallback.

### Round R11A - one-click coordinator

1. Extract or implement Full Paper Run stage coordinator.
2. Enable `full_paper_run` gate in `compute_button_gates`.
3. Wire UI button to staged streaming.
4. Add fake-stage tests.

Exit criteria:

- Full Paper Run is enabled only when all gates are clean.
- Fake success path runs all stages.
- Fake dry-run failure blocks submit.
- Fake submit failure blocks T10 apply.

### Round R11B - final email

1. Add autotrade email body formatter.
2. Add mocked SMTP tests.
3. Call email sender at the end of Full Paper Run.
4. Include report paths and failure instructions.

Exit criteria:

- Success email includes fills and T10 apply status.
- Failure email includes hard_stop and next action.

### Round R11C - market-open paper acceptance

1. Use a fresh `awaiting_execution` artifact.
2. Launch panel.
3. Click Full Paper Run once.
4. Verify final artifacts and email.
5. Re-click or retry intentionally to verify idempotency blocks duplicate apply/order risk.

Exit criteria:

- `run_meta.status == executed`
- `autotrade_daily_report.json` rc is 0
- T10 apply report rc is 0
- post-reconcile qty mismatch is 0
- email sent
- no open broker paper orders remain unexpectedly

---

## 6. Market-open runbook before Full Paper Run exists

If market opens before R11 is implemented, use the current R10C manual staged flow:

1. Launch:

```bash
bash scripts/run_autotrade_control_panel.command
```

2. Press `Refresh`.
3. Pick latest `awaiting_execution`.
4. Generate all intents with a conservative positive batch pad.
5. Run `Dry Run Preflight / Report`.
6. If rc=0, arm Paper Submit gate and tick authorize.
7. Run `Paper Submit + Manage`.
8. If and only if all orders are FILLED and report rc=0:
   - arm T10 Apply gate
   - run T10 Apply Dry Run
   - run T10 Apply Real
9. If any UNKNOWN/open/partial/cancel-unconfirmed:
   - do not T10 apply
   - Copy Panel Snapshot
   - inspect broker open orders
   - cancel/recover as needed
   - preserve reports for debugging

This is not the final target, but it is the safest current live paper path.

---

## 7. Acceptance checklist for the final one-click milestone

Hard pass criteria:

- One button click can run from valid `awaiting_execution` artifact to completed paper apply.
- No broker submit occurs if dry-run fails.
- No T10 apply occurs unless paper-submit outcomes are clean.
- No duplicate apply occurs on repeated clicks.
- No duplicate broker order occurs on repeated clicks.
- Any UNKNOWN produces a clear hard-stop, report, and email.
- Global halt blocks before broker mutation.
- KIS env must be paper.
- Submit/cancel/apply danger gates must be armed in the UI session.
- The final email is sent after success and attempted after failure.

Artifacts that must exist after success:

```text
daily_runs/<RUN_ID>/submitted_intents.json
daily_runs/<RUN_ID>/autotrade_orders.jsonl
daily_runs/<RUN_ID>/autotrade_daily_report.md
daily_runs/<RUN_ID>/autotrade_daily_report.json
daily_runs/<RUN_ID>/t10_apply_report.md
daily_runs/<RUN_ID>/t10_apply_report.json
daily_runs/<RUN_ID>/run_meta.json              # status executed
```

Operator-visible final UI state:

```text
Full Paper Run: done rc=0
paper-submit: FILLED only
T10 apply: rc=0
post-reconcile: qty_mismatch_count=0
email: sent
```

---

## 8. File map

Current main surfaces:

| File | Role |
|---|---|
| `phase3/daily_runner.py` | Generates recommendations/artifacts and sends pre-trade daily email. |
| `phase3/mailer.py` | Gmail SMTP helper for daily recommendation email. |
| `phase3/autotrade/control_panel.py` | Operator UI, gates, buttons, streaming, intent generation. |
| `phase3/autotrade/daily_runner.py` | Paper submit/manage/T10 apply/report orchestration backend. |
| `phase3/autotrade/order_manager.py` | Per-order state machine and ccnl polling. |
| `phase3/autotrade/intents_io.py` | `submitted_intents.json` validation/writing. |
| `phase3/autotrade/t10_applicator.py` | Applies confirmed fills to local holdings log and updates artifact status. |
| `phase3/autotrade/order_store.py` | Append-only order state JSONL and duplicate guard. |
| `phase3/autotrade/reconcile.py` | Broker/local reconcile used before and after apply. |
| `scripts/run_autotrade_control_panel.command` | Double-click launcher. |

Likely new/changed R11 surfaces:

| File | Change |
|---|---|
| `phase3/autotrade/order_manager.py` | ccnl retry budget. |
| `phase3/autotrade/intents_io.py` | quote-fresh limit helper. |
| `phase3/autotrade/control_panel.py` | Full Paper Run UI, callback revalidation, staged streaming. |
| `phase3/autotrade/autotrade_mailer.py` | New post-trade email formatter/sender, if not added to `mailer.py`. |
| `phase3/tests/test_r8_order_manager.py` | Retry tests. |
| `phase3/tests/test_r10c_batch_and_streaming.py` | Quote freshness / streaming drain tests. |
| `phase3/tests/test_r10_ui_button_gates.py` | Full Paper Run gate tests. |
| `phase3/tests/test_r11_full_paper_run.py` | New end-to-end fake stage coordinator tests. |
| `phase3/tests/test_r11_autotrade_mailer.py` | New email formatter/sender tests. |

---

## 9. Known risks

1. **KIS paper ccnl instability**
   - Mitigation: retry polling exceptions, preserve UNKNOWN after budget exhausted.

2. **Stale limit price**
   - Mitigation: quote-fresh limit at intent generation; reject negative pad in full-auto.

3. **Over-applying local ledger**
   - Mitigation: keep T10 apply idempotency, journal checks, and duplicate apply block.

4. **Duplicate broker submit after UI crash**
   - Mitigation: keep OrderStore duplicate guard; add stage marker and operator-cleared flow later.

5. **Email failure hiding trading result**
   - Mitigation: email failure must be non-fatal but visible in UI/report.

6. **Scope creep before acceptance**
   - Mitigation: do not add live mode, scheduler, FMP, retraining, or broad modularization until R11 acceptance passes.

---

## 10. Recommended next action

The next coding round should not start with the Full Paper Run button. It should first close the two reliability failures directly observed in R10C:

```text
R10D-1 ccnl polling retry
R10D-3 quote-fresh limits
```

Then enable the one-click coordinator:

```text
R11A Full Paper Run staged UI
R11B post-trade email
R11C market-open paper acceptance
```

This order gives the highest chance that the first real one-click paper full-auto run reaches `executed` instead of stopping on a preventable UNKNOWN or stale limit.
