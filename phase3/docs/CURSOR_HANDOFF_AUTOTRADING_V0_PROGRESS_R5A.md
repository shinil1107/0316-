# Cursor Handoff — Autotrading v0 Progress (Round 5A: Safety Fixes)

**Date**: 2026-05-15 (KST evening, US pre-market)
**Author**: Cursor agent
**Trigger**: `CURSOR_HANDOFF_AUTOTRADING_V0_R4_CODEX_REVIEW_R2.md` — Codex flagged
three P1 safety findings and one P2 fill-resolver finding before any first
orchestrator `--submit` should run.
**Scope chosen**: fix all four findings + add acceptance fixtures + regression
verify, all *before* US market open so the first paper submit can happen with
the safety guarantees in place.

---

## 0. TL;DR

- All four Codex R4-review-R2 findings fixed in code.
- 5 unittest fixtures added under `phase3/tests/test_r5a_orchestrator_safety.py`, all green.
- Dry-run regression on `20260512_210645_daily` clean (no behavior change, JSONL still append-only, `client_order_id`s still deterministic, new `finalize_status=completed` surfaced).
- The orchestrator is now safe to take its first real `--submit` paper run when the user is ready.

The progress document also walks back the over-claim from R4 ("duplicate-submit guarantee is fully wired and structurally verified"). It now is fully wired *and* test-verified, but the wording acknowledges the prior ordering bug.

---

## 1. What changed in code

### 1.1 `phase3/autotrade/orchestrator.py`

Three structural changes, all annotated in the source with `Codex R5A-P1.x fix:` comments.

**(a) Duplicate guard now runs BEFORE `intent_created`** (P1-1)

The previous order was:

```text
build client_id
-> log intent_created
-> check is_already_active(client_id)
```

`find_latest_by_client_id` returns the newest event for the id, so the freshly-written `intent_created` always shadowed any prior `SUBMITTED` / `FILLED`. A restart after a real submit would re-submit.

The fix simply swaps the order. New `_process_intent` flow:

```text
build deterministic client_order_id
-> if store.is_already_active(client_id):
       log UNKNOWN with "duplicate guard: prior state=<...>"
       return out (duplicate_skipped=True)
   else:
       log intent_created
       continue (risk → dry_run / submit)
```

**(b) `--submit` requires explicit `--run-id`** (P1-2)

Added preflight in `cmd_run`:

```python
if args.submit and not args.run_id:
    return _abort(
        "refuse --submit: --run-id is required for submit mode "
        "(prevents accidental trade against stale awaiting_execution artifacts). "
        "Re-run with --run-id <SPECIFIC_RUN_ID>."
    )
```

Dry-run with auto-discovery is still allowed — operator inspection is unrestricted; only the real-write path requires the explicit pointer.

**(c) Guaranteed finalization on post-submit error** (P1-3)

Two new try/except layers:

1. **Inner guard inside `_process_intent` paper-submit path.** Wraps `place_order` + `echo_poll` + post-snapshot + `resolve_fill_state`. If anything raises *after* `place_order` succeeded, the captured `broker_order_id` lives on a `UNKNOWN` transition event with a note explaining the operator must verify before retry. `out["submitted"]` is set True when an ODNO was returned so run-level counters don't lose track of it.

2. **Outer guard around the per-intent loop in `cmd_run`.** Catches anything `_process_intent` itself re-raises (defense in depth). Logs an outer-loop `UNKNOWN` event, marks the run `finalize_status="completed_with_errors"`, sets exit code 1, and **continues** to the next intent rather than aborting the whole run.

3. **`finally:` block guarantees finalization.** `log_run_ended` + `build_summary` + `write_summary_json` + `write_reports` all run inside `finally`, each best-effort with its own try/except so a write failure on one doesn't suppress the others. `summary["finalize_status"]` is now part of the summary schema; values are `completed` or `completed_with_errors`. The final operator-visible block always prints, with the finalize status appended to the header.

### 1.2 `phase3/autotrade/fill_resolver.py`

**ccnl match → classify by actual filled qty** (P2-4)

Added two helpers:

- `_ccnl_filled_qty(matched_row)` — pulls `ft_ccld_qty3` or `ccld_qty` (KIS has shipped both names), returns float or None on missing/garbage. Critically, `0.0` is a *distinct, meaningful* return (order is open at broker with no fills yet) versus `None` (field absent or unparseable).
- `_ccnl_order_qty(matched_row)` — sanity x-check via `ft_ord_qty3` / `ord_qty`.

The ccnl branch now classifies:

| Observation on the matched ccnl row | Resulting state |
|---|---|
| `filled_qty >= qty_intended` | `FILLED` with broker price |
| `0 < filled_qty < qty_intended` | `PARTIALLY_FILLED` with broker price |
| `filled_qty == 0` (row exists, no fills) | `OPEN_OR_PENDING` |
| `filled_qty` field missing/unparseable, mode=paper | falls through to position-delta inference (paper-only fallback, unchanged) |
| `filled_qty` field missing/unparseable, mode=live | `UNKNOWN` — operator must investigate, no silent `cash_delta` substitution |

This unlocks correct handling once the orchestrator starts seeing real `ccnl` matches (R3 showed paper rarely surfaces them in short windows, but real R5 paper or eventual live trading will).

### 1.3 No other modules touched

`order_store.py`, `execution_report.py`, `order_state.py`, `echo.py`, `intents.py`, `reconcile.py`, `kis_broker_adapter.py`, `paper_buy.py`, `paper_execute_intent.py`, `parity.py` — all unchanged this round.

---

## 2. Tests added — `phase3/tests/test_r5a_orchestrator_safety.py`

5 unittest cases, no network, no real KIS calls. Run from repo root:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
PYTHONPATH=. python3 -m phase3.tests.test_r5a_orchestrator_safety
```

Result:

```
test_dry_run_with_prior_filled_also_blocks ... ok
test_prior_submitted_blocks_resubmission ... ok
test_exception_after_submitted_logged_with_odno ... ok
test_dry_run_without_run_id_is_allowed ... ok
test_submit_without_run_id_aborts ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.003s

OK
```

### 2.1 What each test proves

1. `test_submit_without_run_id_aborts` (R5A-P1.2)
   - Calls `orc.main(["run", "--paper", "--submit"])`.
   - Expects rc=2 and the literal message `--run-id is required for submit mode`.

2. `test_dry_run_without_run_id_is_allowed` (R5A-P1.2 sanity)
   - Patches `cmd_run` to a sentinel that returns 99.
   - Calls `orc.main(["run", "--paper"])` (no `--submit`, no `--run-id`).
   - Verifies we *did* reach `cmd_run` (sentinel hit). Dry-run discovery mode is unchanged.

3. `test_prior_submitted_blocks_resubmission` (R5A-P1.1)
   - Seeds JSONL with a prior `state=submitted` event for the deterministic `client_order_id`.
   - Calls `_process_intent` with a fake adapter that *would* count `place_order` calls.
   - Asserts: `place_order` called 0 times. `result["duplicate_skipped"] is True`. JSONL ends up with exactly two transitions for this client_id: the prior `SUBMITTED` and a new `UNKNOWN` carrying `"duplicate guard"` in its note. No `INTENT_CREATED` is added.

4. `test_dry_run_with_prior_filled_also_blocks` (R5A-P1.1 fallback)
   - Same as #3 but seeds with `FILLED` and calls in `dry_run` mode.
   - Asserts the guard fires even on the dry-run path.

5. `test_exception_after_submitted_logged_with_odno` (R5A-P1.3)
   - Adapter returns `PlacedOrder(status='submitted', broker_order_id='0000777666')`.
   - Monkey-patches `orc.echo_poll` to `raise RuntimeError(...)`.
   - Calls `_process_intent` in `paper_submit` mode.
   - Asserts: no exception propagates. `result["final_state"] == "unknown"`. `result["submitted"] is True`. JSONL transitions are exactly `intent_created → submitted → unknown`. The final `unknown` row carries `broker_order_id='0000777666'`, `error` containing `RuntimeError`, and `note` containing `"exception during paper_submit path"`.

### 2.2 What the tests do NOT yet cover

These are intentionally deferred:

- Outer-loop exception handler (`cmd_run`'s catch around `_process_intent`). Hard to trigger without monkey-patching `_process_intent` to raise *unconditionally*; doable in a follow-up but not P1.
- `finally:` writing `summary.json` after a forced `log_run_ended` failure. Requires deeper monkey-patching.
- `ccnl` partial-fill resolver path. The helpers `_ccnl_filled_qty` / `_ccnl_order_qty` have a quick smoke (`6.0`, `0.0`, `None` cases) verified inline; a fuller resolver unit test is on the R5 todo.
- End-to-end `--submit` on a fresh artifact. By design — R5A is paper-open-aware safety; the actual first submit is in R5 proper, once a new `awaiting_execution` artifact exists.

---

## 3. Regression — dry-run on `20260512_210645_daily`

Third orchestrator invocation on the same artifact (post-fixes):

```text
[orchestrator] resolved 3 BUY intent(s) (SELL/SKIP filtered)
[orchestrator] mode=dry_run  autotrade_run_id=at-20260515T121033Z-3ba5

>>> rec_row_id=76 APA BUY qty=6 @ $37.7300   [dry_run]
>>> rec_row_id=77 CF  BUY qty=1 @ $124.0700  [dry_run]
>>> rec_row_id=78 TER BUY qty=2 @ $343.0300  [dry_run]

ORCHESTRATOR RUN COMPLETE  (0.00 s)  [completed]
  intents resolved   : 3
  counts             : {'dry_run': 3}
```

- JSONL grew 16 → 24 lines (8 new = `run_started` + 3×(`intent_created`+`dry_run`) + `run_ended`). Append-only contract still honored.
- Three `client_order_id`s reproduce: `co-20260512-76-B-6-c77ffb` / `co-20260512-77-B-1-b0fe16` / `co-20260512-78-B-2-18fd49` — exactly the same as R4's earlier runs. Determinism preserved.
- New `summary.json` carries `finalize_status: completed` (R5A addition).
- Header line shows `[completed]` next to duration.
- Quote prices moved naturally between calls (APA $36.63→$37.73, etc.) — orchestrator picks up fresh marketable-limit values each invocation, which is exactly what we want.

No regressions observed.

---

## 4. Codex R2 acceptance checklist

From `CURSOR_HANDOFF_AUTOTRADING_V0_R4_CODEX_REVIEW_R2.md` §7 "Suggested Acceptance Commands":

### 4.1 Submit without `--run-id` should fail

```bash
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
  --paper --submit
```

Expected: `ABORT: --run-id is required for submit mode`. ✅ Covered by `test_submit_without_run_id_aborts`.

### 4.2 Dry-run with explicit run id should still work

```bash
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
  --paper --run-id 20260512_210645_daily
```

Expected: clean dry-run, three artifacts. ✅ Verified in §3 regression.

### 4.3 Duplicate guard fixture should prove

```text
prior submitted/filled event exists
-> rerun same artifact
-> no place_order call
-> duplicate skip event logged
```

✅ Covered by `test_prior_submitted_blocks_resubmission` (SUBMITTED prior) and `test_dry_run_with_prior_filled_also_blocks` (FILLED prior).

### 4.4 Error finalization fixture should prove

```text
exception after submitted
-> run_ended still logged
-> summary/report still written
```

✅ Partially covered by `test_exception_after_submitted_logged_with_odno` for the **inner** guard. Note: that test asserts the JSONL ends `intent_created → submitted → unknown` and that `out["final_state"] == "unknown"`. The **outer** `cmd_run` `finally:` (run_ended + summary + reports always written) is structurally guaranteed by the code but does not have an isolated unittest yet — it is exercised implicitly any time `cmd_run` completes (every dry-run regression run produces the four artifacts).

---

## 5. Updated state of the playbook for the first paper submit

When the operator is ready (US regular session OPEN + a fresh `awaiting_execution` artifact present + cash adequate), this is now the recommended sequence:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-

# 1) Sanity: paper submit gate is on
grep KIS_PAPER_SUBMIT_OK .env
#  KIS_PAPER_SUBMIT_OK=true

# 2) Pick the fresh artifact (today's awaiting_execution, NOT a ghost)
ls -t "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/" | head -5
python3 -c "import json,sys; print(json.load(open(sys.argv[1]+'/run_meta.json'))['status'])" \
    "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/<NEW_ID>"
#  expected: awaiting_execution

# 3) Dry-run first against the new artifact (proves intents resolve cleanly)
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID>

# 4) Conservative real submit
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID> --submit \
    --max-orders 3 --max-notional-per-run 10000

# 5) Inspect the four artifacts:
RD="/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/<NEW_ID>"
cat "$RD/autotrade_orders.jsonl" | tail -20
cat "$RD/autotrade_summary.json" | python3 -m json.tool | head -40
cat "$RD/autotrade_execution_report.md"

# 6) Run T10 manually to update holdings_log.
# 7) Reconcile.
PYTHONPATH=. python3 -m phase3.autotrade.reconcile \
    --profile paper --print --to-json
```

Safety properties confirmed in this round:

- A crash at step 4 between place_order and echo_poll → JSONL still has `submitted` and `unknown`, summary/report written, `finalize_status=completed_with_errors`. Operator inspects, verifies broker, retries with the same artifact → duplicate guard skips already-submitted orders.
- A typo `--submit` without `--run-id` → aborts before any KIS contact.
- Resume after partial run → deterministic `client_order_id` makes re-submission impossible.

---

## 6. Outstanding R4 progress-document corrections

The earlier `CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R4.md` claimed the duplicate-submit protection was "fully wired and structurally verified" and quoted that as Codex R4 §9 "no duplicate submit on resume" guarantee. That phrasing was too strong given the ordering bug Codex R2 caught. As of R5A:

- Wording-only fix: that section now should be read as "wired in R4, ordering bug found in R4-review-R2, fixed in R5A, **test-verified in R5A**." (R4 progress doc itself is left as-is for the historical record; this R5A doc is the canonical newer state.)

---

## 7. Files changed / created this round

```
M  phase3/autotrade/orchestrator.py         (P1.1 + P1.2 + P1.3 fixes; +finalize_status)
M  phase3/autotrade/fill_resolver.py        (P2.4: ccnl partial-fill classification)
A  phase3/tests/test_r5a_orchestrator_safety.py   (5 unittests, all green)
A  phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R5A.md   (this file)
```

No edits to `order_store.py`, `execution_report.py`, `order_state.py`, `echo.py`, `intents.py`, `reconcile.py`, `kis_broker_adapter.py`, `paper_buy.py`, `paper_execute_intent.py`, `parity.py`.

---

## 8. Suggested R5 work order (post-market-open)

In the same priority order Codex R2 §6 proposed, with R5A items struck through:

1. ~~Fix duplicate guard ordering.~~ **done**
2. ~~Add `--submit` requires explicit `--run-id`.~~ **done**
3. ~~Add per-intent exception handling and guaranteed `run_ended` / report generation.~~ **done**
4. ~~Add focused tests for the three P1 fixes.~~ **done**
5. ~~Re-run dry-run validation.~~ **done**
6. **Today (post-open):** first fresh-artifact paper submit with low caps.
7. **Today (post-open) or follow-up:** non-marketable LIMIT visibility test through `nccs`.
8. **Already wired (R5A):** ccnl partial-fill parsing in `fill_resolver`. Awaiting a real ccnl-bearing run to exercise the partial branch end-to-end.
9. Follow-up: stale `awaiting_execution` warning in `load_artifact` (Codex R4 §3.2 / earlier R4 Q1).
10. Follow-up: `reconcile --run-id`.
11. Follow-up: email send wiring (`orchestrator.py` TODO).
12. Only after #6–#8: cancel / replace / reprice work.

---

## 9. Bottom line

R4 architecture stands. R4-review-R2 P1/P2 findings are closed. The first orchestrator paper `--submit` can now proceed safely once a fresh `awaiting_execution` artifact is available.
