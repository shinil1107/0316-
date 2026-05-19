# Cursor Handoff — Autotrading v0 R10D (Reliability hardening)

**Date**: 2026-05-19 KST, before US regular open
**Preceding handoff**: `CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10C.md`
**Codex review document**: `CODEX_HANDOFF_PAPER_FULL_AUTO_R11_TARGET_20260519.md`
**Scope**: paper-only — close every reliability failure observed in
the first R10C live paper acceptance before enabling the one-click
Full Paper Run button (R11A).

This round implements **R10D-1**, **R10D-3**, **P0-3**, and **P1-3**
from the Codex R11 target document, plus the two small "보강"
items called out by the cursor reviewer (intent-row `_quote_source`
metadata and launcher comment cleanup).

R11A (Full Paper Run coordinator) and R11B (post-trade autotrade
email) are explicitly **not** in this round — Codex's
"reliability first, one-click later" sequence is preserved.

---

## 0. TL;DR

Test count: **305 → 336 (all green)**

What changed:

| Item | Effect |
|---|---|
| **R10D-1** ccnl polling retry | KIS paper transient disconnects (the R10C APA failure mode) no longer flip a managed order to UNKNOWN on the first exception. Retry budget = 2 with 2 s backoff, every retry leaves an audit row in `autotrade_orders.jsonl`. |
| **R10D-3** quote-fresh limits | Generate-ALL-Intents now optionally lifts each BUY limit toward the broker's current ask. Negative pads refused. Per-row metadata (`_quote_source`, `_quote_ref_price`, `_quote_asof`) persisted into `submitted_intents.json` for audit. |
| **P0-3** callback gate revalidation | `_on_paper_submit` / `_on_t10_apply` no longer synthesise danger env vars; instead they recompute the button matrix at callback entry and refuse if the action is currently disabled. The Arm toggles are the *only* path that sets the danger env. |
| **P1-3** stream drain guarantee | `run_subprocess_streaming` now joins its stdout/stderr pump threads without a timeout, so `on_done` never fires before the final lines hit the UI log. |

What did **not** change:

- No live mode. KIS_ENV must still be paper everywhere.
- No scheduler / cron / launchd.
- No FMP premarket strategy.
- No Full Paper Run button yet — staged R10C workflow is the
  operational path for tonight's market-open acceptance.
- No post-trade email yet — daily recommendation email path
  (`phase3/mailer.py`) is unchanged.

---

## 1. R10D-1 — ccnl polling retry

### Problem from R10C

R10C Run 1 and Run 2 both showed the same pattern:

```
[paper] HH:MM:00 GET inquire-ccnl tr=VTTS3035R status=200 ... 203 ms
[paper] HH:MM:30 ConnectionError('Connection aborted.',
                      RemoteDisconnected('Remote end closed
                      connection without response'))
```

A single transient on `inquire-ccnl` after broker accept flipped
the managed order to UNKNOWN → run rc=2 hard_stop → T10 apply
blocked → entire batch dead even though the broker still held the
order.

### Fix

`phase3/autotrade/order_manager.py`:

1. New pure helper `_now_classify_with_retry()`:
   - Wraps `_now_classify()` with `retry_count` retries on any
     `Exception`. Each failed attempt calls a caller-supplied
     `log_attempt(attempt_idx, max_attempts, exc)` so audit rows
     stay in `OrderStore`.
   - `retry_count=0` reproduces pre-R10D behaviour exactly.
   - `retry_count<0` is clamped to 0 defensively.
   - A broken `log_attempt` callback is swallowed silently — the
     original ccnl exception path always wins.
2. `OrderManagementPolicy` gains two fields with backwards-safe
   defaults:
   ```python
   ccnl_poll_retry_count: int = 2
   ccnl_poll_retry_backoff_sec: float = 2.0
   ```
3. The two ccnl polling sites in `manage_order` — the main poll
   (line ~456) and the cancel-confirm re-poll (line ~786) — both
   route through `_now_classify_with_retry`. Each retry writes an
   audit transition row with `state=UNKNOWN`, the retry counter in
   the note, and `extra={"ccnl_retry_phase": "main"|"post_cancel"}`.
4. The final terminal UNKNOWN row (after the retry budget is
   exhausted) carries the retry count in its note so the operator
   can immediately see in the panel snapshot how many transient
   retries we burned before giving up.

The conservative UNKNOWN → hard_stop behaviour is preserved when
all retries fail; only the first one or two transient blips are
absorbed.

### Tests (`phase3/tests/test_r10d_ccnl_retry.py`, 8 cases)

- `_now_classify_with_retry`:
  - zero-retries one-shot regression
  - second call succeeds after one transient
  - all retries fail re-raise the last exception
  - broken `log_attempt` swallowed
  - negative `retry_count` clamped to zero
- end-to-end `manage_order` against a `_FlakyBroker`:
  - one transient then FILLED (R10C APA reproducer)
  - all retries fail → UNKNOWN + 3 retry audit rows + terminal
    "after 2 retries" row
  - `retry_count=0` preserves R10C-and-earlier UNKNOWN behaviour

---

## 2. R10D-3 — quote-fresh limit prices

### Problem from R10C

R10C used `reco_price = previous business-day close` for the BUY
limit. APA closed at $38.98 on 2026-05-15, then gapped to ~$40 on
2026-05-18 — paper limit at $39.17 (after batch pad +0.5%) sat
forever, three reprices didn't help, eventually hit ccnl
transient + UNKNOWN.

### Fix

`phase3/autotrade/intents_io.py`:

1. New `IntentBuildWarning` dataclass — one per row that fell back.
2. `candidates_to_intent_rows()` and
   `write_intent_file_from_candidates()` gain three new optional
   keyword arguments:
   - `quote_fn: Callable[[symbol, market], Quote-like or None]`
   - `quote_pad_pct: float = 0.1` (positive % added to the broker's
     ask before comparison; UI rejects negative)
   - `warnings_out: Optional[List[IntentBuildWarning]] = None`
3. The picking rule per candidate is:
   ```text
   reco_padded   = reco_price * (1 + batch_pad/100)
   quote_ref     = ask or last (whichever is positive)
   quote_padded  = quote_ref * (1 + quote_pad/100)
   chosen_limit  = max(reco_padded, quote_padded)
   ```
4. The chosen limit is annotated on the row via
   `_quote_source ∈ { "reco_close", "quote_refreshed",
   "quote_refreshed_below_reco", "fallback_quote_fail",
   "fallback_quote_zero" }`, plus `_quote_ref_price` and
   `_quote_asof` when a real quote was used.
5. All fallback paths populate `warnings_out` with the ticker and
   the human-readable reason so the UI can show the operator
   exactly which rows used yesterday's close.
6. `_validate_row` already tolerates extra keys, so the
   `_quote_*` metadata round-trips through `submitted_intents.json`
   unchanged. The post-trade audit JSONL / future post-trade email
   can pick it up to explain why a fill happened where it did.

### UI surface (`phase3/autotrade/control_panel.py`)

Intent Preparation now has:

- `[ ] Refresh limits with live KIS quote (R10D-3)`
- `Quote pad (%): 0.1` (default; negative refused)
- Confirmation dialog before write now shows per-row
  `src=<_quote_source>` and a "Quote-refresh fallback warnings"
  block listing each fallback ticker and reason.
- The Output / Log pane after write shows the same per-row
  source + a closing warnings list.

When the checkbox is on, the callback constructs a real
`KisBrokerAdapter` against the paper env (or refuses with a
messagebox if KIS_ENV is not paper) and reuses one instance for
all quote calls so the OAuth token is amortised. We write the rows
that we computed in the preview step — calling
`write_intent_file_from_candidates` again would re-issue every
quote call (extra network, possibly different fallback shape).

### Tests (`phase3/tests/test_r10d_quote_fresh_limits.py`, 12 cases)

- pure limit-pick rule:
  - no `quote_fn` ⇒ `reco_close` path
  - ask above reco ⇒ `quote_refreshed`, padded ask used
  - ask below reco ⇒ `quote_refreshed_below_reco`, reco kept
  - ask=None, last>0 ⇒ falls back to last successfully
  - `quote_fn` raises ⇒ `fallback_quote_fail` + warning
  - quote returns None ⇒ `fallback_quote_fail` + warning
  - quote returns 0-priced Quote ⇒ `fallback_quote_zero` + warning
    (catastrophic limit-to-zero prevented)
  - mixed batch — each row records its own source independently
  - `quote_pad_pct=0` ⇒ raw ask used as limit
- file round-trip:
  - `_quote_source` etc. persist through `submitted_intents.json`
  - validator still accepts rows with the new metadata
  - `warnings_out` propagates through `write_intent_file_from_candidates`

---

## 3. P0-3 — callback-level gate revalidation

### Problem

R10C `_on_paper_submit` and `_on_t10_apply` both did:

```python
env = os.environ.copy()
env[SUBMIT_GATE] = "true"   # <- regardless of Arm state
env[CANCEL_GATE] = "true"
```

The visible button matrix prevented accidental clicks, but
programmatic invocation (a future Full Paper Run coordinator, a
test harness, or a fast-fingered operator beating `_refresh`)
could still bypass the Arm toggle.

### Fix

`phase3/autotrade/control_panel.py`:

1. New `DangerActionDenied(RuntimeError)` with structured
   `.action` and `.reason` attributes.
2. New pure helper `revalidate_danger_action()`:
   - Recomputes `compute_panel_state` + `compute_button_gates`
     from the live `os.environ` at callback entry.
   - Refuses if the action's gate is disabled, surfacing the same
     reason the button matrix would have shown.
   - Allowed actions: `paper_submit`, `t10_apply`, `full_paper_run`
     (R11). Calling it with any other action is a programmer error
     (`ValueError`).
3. `_on_paper_submit` and `_on_t10_apply` now:
   - Call `revalidate_danger_action(...)` first, show the reason
     in a messagebox + `_refresh()` and exit on `DangerActionDenied`.
   - Inherit the already-armed `os.environ` without synthesising
     the danger gate vars themselves. The Arm toggle is now the
     **only** writer of `KIS_PAPER_SUBMIT_OK`, `KIS_PAPER_CANCEL_OK`,
     `AUTOTRADE_T10_APPLY_OK`.

### Tests (`phase3/tests/test_r10d_callback_revalidate.py`, 11 cases)

- paper_submit:
  - passes with armed env + confirm + clean dry-run
  - refused without Arm
  - refused without confirm checkbox
  - refused without clean dry-run
  - refused on blank run_id
  - refused when `output_dir` is None
- t10_apply:
  - passes with clean submit + armed T10 + confirm
  - refused without T10 Arm
  - refused when prior submit outcome not clean
- API contract:
  - non-danger action raises `ValueError`
  - `DangerActionDenied` carries `.action` and `.reason`

---

## 4. P1-3 — stream drain guarantee

`run_subprocess_streaming` previously joined the stdout/stderr
pump threads with a 1.0 s timeout before calling `on_done(rc)`.
That timeout was fine in practice but violated the documented
contract that `on_done` fires after *both* pipes drain. R10D
removes the timeout outright — once `proc.wait()` returns the
child is dead, both readline loops will see EOF, and the join
returns immediately.

This becomes important when R11B (post-trade email) reads the UI
log buffer in `on_done` — without this fix, the last 0–N lines
could still be in the pump queue.

No new tests; the existing `test_r10c_batch_and_streaming.py`
suite already drives the streaming runner with synthetic stdout
and continues to pass.

---

## 5. Operator runbook for tonight's KST 23:00–23:30 acceptance

Order: same R10C staged flow, but with R10D-1 + R10D-3 in
operation.

1. Launch: `bash scripts/run_autotrade_control_panel.command`
2. `Refresh` → pick latest `awaiting_execution` run_id.
3. **NEW**: In Intent Preparation, tick
   `[x] Refresh limits with live KIS quote (R10D-3)`.
   Quote pad = 0.1% (default) or 0.2% if volatility looks high.
4. Click `0b. Generate ALL Intents (batch)`. Confirmation dialog
   now shows per-row `src=`. Verify any `fallback_*` rows are
   acceptable (yesterday's close is still your safety net).
5. Run `Dry Run Preflight / Report`. Require rc=0.
6. In Activation: tick `Arm Paper Submit gate`. Tick the
   "I authorize paper submit" checkbox.
7. `2. Paper Submit + Manage`. R10D-1 will now absorb up to 2
   transient ccnl disconnects per order; watch the log for
   `ccnl poll retry N/3` rows.
8. Iff every order is FILLED and report rc=0:
   - tick `Arm T10 Apply gate`
   - tick "I authorize T10 apply"
   - `T10 Apply Dry Run`, then `T10 Apply Real`.
9. If any UNKNOWN / open / partial:
   - **do not** T10 apply.
   - Use `Copy Panel Snapshot` and `Copy Output / Log` to capture
     state for analysis.
   - Use a read-only KIS probe (`python3 -m
     phase3.autotrade.kis_broker_adapter --probe history`) to
     confirm broker state. Cancel via `cancel-by-odno` if needed.
   - Preserve the run directory so the next round can diagnose.

Expected R10D wins vs R10C:

- APA-style gap-up will refresh its limit to today's ask, so the
  initial paper limit should be in the live market range.
- A single ccnl transient (the R10C dominant failure) is now a
  log line, not a run rc=2.

---

## 6. Acceptance criteria for this round (already met by tests)

- 305 → 336 tests, all green
- `_now_classify_with_retry` handles transient → success and
  exhaust-budget → UNKNOWN with audit rows
- `manage_order` end-to-end absorbs one transient ccnl, retains
  UNKNOWN when retries are exhausted, and stays backwards-compat
  with `retry_count=0`
- Quote refresh path produces correct rows for every
  reco/ask combination + every failure mode
- `revalidate_danger_action` rejects every disabled gate combo
  and accepts every armed combo
- `submitted_intents.json` round-trip preserves quote metadata
- Launcher comment block no longer recommends manual `export
  KIS_PAPER_SUBMIT_OK=true` as the primary path

---

## 7. Pending for R11 (unchanged from Codex's plan)

- **R11A** — Full Paper Run coordinator (one button → generate
  intents with quote refresh → dry-run → paper-submit+apply-t10 →
  reconcile/report → email).
- **R11B** — post-trade autotrade email (subject pattern, body
  with fills + T10 status + report paths + next operator action).
- **R11C** — second market-open acceptance, this time with the
  one-click button.

Sequencing reminder from Codex: do not start R11A until tonight's
R10D acceptance confirms the reliability fixes hold in real
paper. If APA / similar gap-up tickers still fail after R10D-3,
do another reliability round before touching R11A.

---

## 8. Files changed in R10D

| File | Change |
|---|---|
| `phase3/autotrade/order_manager.py` | `_now_classify_with_retry` helper; `OrderManagementPolicy.ccnl_poll_retry_*`; both ccnl poll sites use the retry helper with audit logging |
| `phase3/autotrade/intents_io.py` | `IntentBuildWarning`; `candidates_to_intent_rows`/`write_intent_file_from_candidates` gain `quote_fn` / `quote_pad_pct` / `warnings_out`; rows carry `_quote_source` etc. |
| `phase3/autotrade/control_panel.py` | `DangerActionDenied` + `revalidate_danger_action`; `_on_paper_submit`/`_on_t10_apply` revalidate at entry and stop synthesising danger env vars; Intent Preparation gets quote refresh checkbox + pad input; confirmation/log surface row-level source + warnings; stream drain join without timeout |
| `scripts/run_autotrade_control_panel.command` | Comments now describe the in-UI Arm toggle as the primary arming path |
| `phase3/tests/test_r10d_ccnl_retry.py` | New, 8 tests |
| `phase3/tests/test_r10d_callback_revalidate.py` | New, 11 tests |
| `phase3/tests/test_r10d_quote_fresh_limits.py` | New, 12 tests |

End of handoff.
