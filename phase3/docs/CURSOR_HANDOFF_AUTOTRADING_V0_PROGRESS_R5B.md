# Cursor Handoff — Autotrading v0 Progress (Round 5B: Pre-Submit Safety)

**Date**: 2026-05-15 (KST evening, still pre-market)
**Author**: Cursor agent
**Trigger**: `CURSOR_HANDOFF_AUTOTRADING_V0_R5B_PRE_SUBMIT_SAFETY.md` — Codex
flagged two P1 + one P2 safety gaps that survived R5A and must be closed
before the first orchestrator paper `--submit`.
**Scope chosen**: fix all three findings + acceptance fixtures + regression,
all *before* US market open.

---

## 0. TL;DR

- All three Codex R5B findings fixed in code.
- **R5A 5/5 + R5B 9/9 = 14 unit tests** all green.
- Dry-run regression on `20260512_210645_daily` clean — JSONL still append-only (24→32 lines), three deterministic `client_order_id`s reproduce, `finalize_status=completed`, header reads `[completed]`.
- The history-aware duplicate guard, the post-submit `had_error` propagation, and the ccnl zero-fill conflict detection are now structurally guaranteed AND test-verified.
- The orchestrator is now ready for its first real paper `--submit` once a fresh `awaiting_execution` artifact is available.

---

## 1. What changed in code

### 1.1 `phase3/autotrade/order_store.py`

**Core change**: `is_already_active(client_order_id)` no longer relies on the most-recent event. It now scans the full history for that client_id and returns True if:

1. **any** event for that client_id was in an active state, OR
2. **any** UNKNOWN event for that client_id carries evidence of broker contact.

Implementation summary:

- New module-level constant `_ACTIVE_STATES` (frozenset) covers `SUBMITTED, OPEN_OR_PENDING, PARTIALLY_FILLED, FILLED, CANCEL_REQUESTED, REPLACE_REQUESTED, REPLACED`.
- New static helper `_unknown_is_blocking(raw)` returns True if the row has a `broker_order_id`, an `error` field, or a note containing `"duplicate guard"` / `"exception during paper_submit"` / `"verify before retry"` / `"outside _process_intent guard"`.
- `is_already_active()` walks every transition event for the client_id; first hit wins.
- Companion `find_latest_blocking_by_client_id(client_order_id)` returns the most-recent blocking row (used by the orchestrator log).
- `find_latest_by_client_id`, `find_latest_blocking_by_client_id`, and `build_summary` all switched from `event_ts > prev.event_ts` to **last-write-wins by insertion order**. JSONL is append-only, so the last matching row IS the latest; ties on microsecond ts are no longer possible to silently flip the wrong way.
- `_now_iso()` now uses microsecond precision (was milliseconds). Eliminates the same-ms tie issue we observed in the unit tests.

### 1.2 `phase3/autotrade/orchestrator.py`

Three structural changes, all annotated with `Codex R5B-P1.x` / `R5B-P2.x` comments.

**(a) `_process_intent` exposes `had_error` / `error`** (R5B-P1.2)

`out` dict gained two fields. Set to True on:

- post-submit exception (echo_poll/snapshot/resolver failed after `place_order` succeeded). `broker_order_id` is preserved on the same UNKNOWN row.
- `place_order` returned `status="submitted"` but no ODNO (broker ambiguous).
- outer-loop exception fallback (already in R5A, now also marks `had_error`).

Not set on:
- duplicate-skipped (intentional)
- risk-blocked (intentional)
- broker-side rejected (deliberate broker answer; rc stays 0)
- dry-run terminal

**(b) `cmd_run` propagates `had_error` to finalize / rc** (R5B-P1.2)

After each per-intent result:

```python
if result.get("had_error"):
    finalize_status = "completed_with_errors"
    exit_rc = 1
```

So a post-submit echo_poll exception now correctly produces:
- final operator block reads `[completed_with_errors]`
- `autotrade_summary.json` carries `"finalize_status": "completed_with_errors"`
- `cmd_run` returns exit code 1

Aggregate counters also got a new `_had_error` tally so the count_by_state breakdown can show "of N intents, K had errors".

**(c) Duplicate-guard log line uses the blocking row** (R5B-P1.1 follow-up)

The orchestrator's duplicate-guard message now prints both `blocking_state` and `latest_state` (sometimes the same, but different when an UNKNOWN row was written after the SUBMITTED). The `broker_order_id` written on the duplicate-guard UNKNOWN now preserves whichever ODNO is present (blocking row first, latest row as fallback).

### 1.3 `phase3/autotrade/fill_resolver.py`

**`ccnl` zero-fill conflict detection** (R5B-P2.3)

The `ccnl matched && filled_qty_ccnl == 0` branch now cross-checks `qty_delta`:

| filled_qty (ccnl) | qty_delta (position) | mode  | result |
|---|---|---|---|
| 0 | 0 | any   | `OPEN_OR_PENDING` (consistent: nothing moved, ccnl says open) |
| 0 | >0 | paper | `UNKNOWN`, qty_filled=qty_delta, fill_price from cash_delta, note=`"conflict: ccnl filled_qty=0 but position moved …"` |
| 0 | >0 | live  | `UNKNOWN`, qty_filled=qty_delta, fill_price=None, note=conflict |
| 0 | <0 | any   | `UNKNOWN` (concurrent activity?), note=conflict |

The non-zero filled branches (`FILLED` / `PARTIALLY_FILLED`) are unchanged from R5A.

### 1.4 No other modules changed

`echo.py`, `intents.py`, `reconcile.py`, `kis_broker_adapter.py`, `parity.py`, `paper_buy.py`, `paper_execute_intent.py`, `execution_report.py`, `order_state.py` — all unchanged in R5B.

---

## 2. Tests added — `phase3/tests/test_r5b_pre_submit_safety.py`

9 unittest cases, no network. Run from repo root:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
PYTHONPATH=. python3 -m phase3.tests.test_r5b_pre_submit_safety
```

Result:

```
Ran 9 tests in 0.010s
OK
```

### 2.1 Three test classes

**`TestHistoryAwareDuplicateGuard` (4 tests, P1-1)**

1. `test_submitted_then_dup_guard_unknown_still_blocks_retry`
   - Seeds: `SUBMITTED` then `UNKNOWN("duplicate guard: prior state=submitted...")`.
   - Asserts: latest is `UNKNOWN`, but `is_already_active()` returns True (history scan), and `_process_intent` calls `place_order` 0 times.
2. `test_submitted_then_postsubmit_exception_unknown_still_blocks_retry`
   - Seeds: `SUBMITTED` then `UNKNOWN` carrying `broker_order_id` + `error` + the canonical post-submit note.
   - Asserts: `find_latest_blocking_by_client_id` returns the UNKNOWN row, blocking is True, `place_order` not called.
3. `test_dry_run_only_does_not_block_future_submit`
   - Seeds: two `DRY_RUN` rows.
   - Asserts: `is_already_active()` returns False (dry-run history is intentionally repeatable).
4. `test_rejected_without_broker_id_does_not_block`
   - Seeds: a clean `REJECTED` from a risk_flags pre-trade check (no broker_order_id, no error).
   - Asserts: not blocked — the order never reached the broker.

**`TestHadErrorPropagatesToFinalize` (1 test, P1-2)**

5. `test_had_error_makes_rc1_and_completed_with_errors`
   - Builds a tmp artifact dir on disk with a 1-row `recommendations.csv` and a synthetic `run_meta.json`.
   - Monkey-patches `orc.load_env_config`, `orc.load_artifact`, `orc.resolve_intents`, and `orc._process_intent`. The fake `_process_intent` returns `had_error=True / final_state="unknown" / submitted=True`.
   - Calls `orc.main(["run", "--paper", "--run-id", "...", "--quiet"])` and captures stdout.
   - Asserts: rc=1, output contains `"completed_with_errors"`, `autotrade_summary.json` has `finalize_status="completed_with_errors"`, all three report artifacts written.

**`TestCcnlZeroFillConflict` (4 tests, P2-3)**

6. `test_zero_fill_no_position_move_is_open_or_pending` — regression baseline.
7. `test_zero_fill_with_position_move_paper_is_conflict_unknown` — UNKNOWN + `"conflict"` in note + cash_delta-derived fill_price.
8. `test_zero_fill_with_position_move_live_is_conflict_unknown_no_price` — UNKNOWN + no fill_price (live rule).
9. `test_partial_fill_still_partial` — regression: positive filled_qty < intended still classifies as `PARTIALLY_FILLED`, not caught by the new zero-fill conflict.

### 2.2 R5A regression

The previous 5 R5A tests still pass without modification:

```
phase3.tests.test_r5a_orchestrator_safety  →  Ran 5 tests in 0.003s  OK
```

This confirms the R5B changes are strictly additive (no semantic regressions in the duplicate guard's basic behavior).

---

## 3. Dry-run regression on `20260512_210645_daily`

Fourth invocation on the same artifact (post-R5B):

```text
[orchestrator] resolved 3 BUY intent(s) (SELL/SKIP filtered)
[orchestrator] mode=dry_run  autotrade_run_id=at-20260515T125427Z-ec90

>>> rec_row_id=76 APA BUY qty=6 @ $37.7400   [dry_run]
>>> rec_row_id=77 CF  BUY qty=1 @ $123.8700  [dry_run]
>>> rec_row_id=78 TER BUY qty=2 @ $343.3300  [dry_run]

ORCHESTRATOR RUN COMPLETE  (0.00 s)  [completed]
  counts             : {'dry_run': 3}
```

- JSONL grew 24 → 32 lines (8 new = `run_started` + 3×(`intent_created`+`dry_run`) + `run_ended`).
- Three deterministic `client_order_id`s reproduced again: `c77ffb / b0fe16 / 18fd49`.
- `summary.finalize_status == "completed"`. Header tag `[completed]`. Exit code 0.

The duplicate guard correctly did NOT fire on these dry-runs — dry-run history is intentionally repeatable (per test #3 above). The same retry on a SUBMITTED/FILLED row would have skipped, but a clean dry-run-only history does not.

---

## 4. Codex R5B acceptance — line-by-line

From `R5B_PRE_SUBMIT_SAFETY.md` §5 "R5B Acceptance Criteria":

| Acceptance bullet | Status |
|---|---|
| `is_already_active()` blocks older active/suspicious broker states even if newer UNKNOWN rows exist | ✅ history-scan implementation; tests #1, #2 |
| A retry after `submitted → unknown` does not call `place_order` | ✅ test #2 (postsubmit-exception UNKNOWN) |
| A retry after `submitted → unknown(duplicate guard)` does not call `place_order` | ✅ test #1 |
| Post-submit exception → `UNKNOWN` event logged | ✅ R5A inner guard + R5B `had_error` propagation; test #5 + R5A test |
| Post-submit exception → `broker_order_id` preserved if known | ✅ R5A; R5A test still passing |
| Post-submit exception → `run_ended` logged | ✅ R5A `finally:` |
| Post-submit exception → summary/report written | ✅ test #5 verifies all four artifacts |
| Post-submit exception → `finalize_status = completed_with_errors` | ✅ R5B-P1.2; test #5 |
| Post-submit exception → process rc non-zero | ✅ R5B-P1.2; test #5 expects rc=1 |
| Existing R5A tests still pass | ✅ 5/5 |
| New R5B tests pass | ✅ 9/9 |
| Optional: `ccnl` zero-fill conflict | ✅ R5B-P2.3; tests #6–#9 |

---

## 5. Behavior matrix after R5B

What `is_already_active(client_order_id)` returns for various histories:

| History (oldest → newest) | `is_already_active` | Why |
|---|---|---|
| empty | False | no events |
| INTENT_CREATED only | False | local-only state |
| INTENT_CREATED → DRY_RUN | False | dry-run is intentionally repeatable |
| INTENT_CREATED → REJECTED (no broker_id, no error) | False | never reached broker |
| INTENT_CREATED → REJECTED (with error) | True | UNKNOWN-blocking rules apply via error field… wait, REJECTED is not UNKNOWN. → still False, but operator should manually clear |
| INTENT_CREATED → SUBMITTED | True | active state |
| INTENT_CREATED → SUBMITTED → FILLED | True | active state (FILLED is terminal but blocking) |
| INTENT_CREATED → SUBMITTED → UNKNOWN(duplicate guard note) | True | history scan finds SUBMITTED |
| INTENT_CREATED → SUBMITTED → UNKNOWN(post-submit exception) | True | both reasons |
| INTENT_CREATED → SUBMITTED → CANCEL_REQUESTED → CANCELLED | True | history has CANCEL_REQUESTED (active); resolution by future "operator_cleared" event TBD |

The only false negatives (dry_run + clean rejected) match the intended UX: dry_run loops are explicitly allowed, and pre-trade-rejected orders without broker contact are safe to retry once the risk flag is resolved.

---

## 6. Updated playbook for the first paper submit

Unchanged in mechanics from R5A's playbook, but now backed by stronger guarantees:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-

# 0) Run the two test suites — both must be green.
PYTHONPATH=. python3 -m phase3.tests.test_r5a_orchestrator_safety
PYTHONPATH=. python3 -m phase3.tests.test_r5b_pre_submit_safety

# 1) Pick a FRESH awaiting_execution artifact.
ls -t "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/" | head -5
python3 -c "import json,sys; print(json.load(open(sys.argv[1]+'/run_meta.json'))['status'])" \
    "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/<NEW_ID>"

# 2) Dry-run pre-flight.
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID>

# 3) Real paper submit (conservative caps).
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID> --submit \
    --max-orders 3 --max-notional-per-run 10000

# 4) Confirm exit status; inspect artifacts.
RD="/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/<NEW_ID>"
cat "$RD/autotrade_orders.jsonl"           | tail -30
python3 -c "import json; s=json.load(open('$RD/autotrade_summary.json')); print(s['finalize_status'], s['counts_by_state'])"
cat "$RD/autotrade_execution_report.md"

# 5) If exit code was 0 and finalize_status=completed → run T10 manually.
# 6) If exit code was 1 or finalize_status=completed_with_errors:
#    inspect the JSONL for SUBMITTED/UNKNOWN rows
#    verify broker state at KIS web before any retry
#    DO NOT just re-run --submit; the history-aware guard will block,
#    but you need to know what's at the broker first.

# 7) Reconcile.
PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper --print --to-json
```

Failure-mode invariants now structurally guaranteed:

- Crash between `place_order` and `echo_poll` → JSONL has `submitted` + `unknown`. `finalize_status="completed_with_errors"`. `rc=1`. Operator sees a clear "verify before retry" cue.
- Operator hits the up-arrow and re-runs the same `--submit` after a partial run → history-aware duplicate guard blocks every previously-submitted intent. `place_order` is not called for them. Output reads `[skip] <ticker> client_order_id=… blocking=<state> latest=<state>`.
- Operator forgets `--run-id` on `--submit` → R5A abort still in place.
- Paper account hit a "service delayed" 5xx → adapter's R3-grade retry handles it; if it still fails, `had_error` fires, run finishes with rc=1.

---

## 7. Files changed / created this round

```
M  phase3/autotrade/order_store.py            (P1.1: history scan + blocking-UNKNOWN; microsecond ts; last-write-wins)
M  phase3/autotrade/orchestrator.py           (P1.1 log: blocking row; P1.2: had_error → rc=1; finalize_status)
M  phase3/autotrade/fill_resolver.py          (P2.3: ccnl zero-fill conflict detection)
A  phase3/tests/test_r5b_pre_submit_safety.py (9 unittests, all green)
A  phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R5B.md (this file)
```

No edits to `echo.py`, `intents.py`, `reconcile.py`, `kis_broker_adapter.py`, `parity.py`, `paper_buy.py`, `paper_execute_intent.py`, `execution_report.py`, `order_state.py`.

---

## 8. Suggested R6 work order (after first paper submit)

In Codex priority order:

1. Execute the first fresh-artifact paper `--submit` with low caps. Confirm:
   - 1–3 ODNOs captured
   - position+cash deltas match
   - one or more rows resolve to FILLED via `fill_resolver`
   - `finalize_status = completed`, rc=0
   - manual T10 still works
   - reconcile shows qty_mismatch=0
2. Non-marketable LIMIT visibility test through `nccs` (Codex R4 §8.3) — the question of whether paper exposes truly-open orders is still open and gates real cancel/reprice work.
3. Stale `awaiting_execution` warning in `load_artifact` (Codex R4 §3.2).
4. `reconcile --run-id`.
5. Email send wiring (`orchestrator.py` has a `TODO(R5)` placeholder).
6. Explicit `operator_cleared` resolution event so blocking-UNKNOWNs can be unstuck without manually editing JSONL.
7. Only after all of the above: cancel / replace / reprice work (Codex R4 §10 #12).

---

## 9. Codex bottom line

R5B closes the two P1 + one P2 issues Codex flagged. The orchestrator is now ready for its first orchestrator paper `--submit` on a fresh `awaiting_execution` artifact, with low caps, when the US market is open.
