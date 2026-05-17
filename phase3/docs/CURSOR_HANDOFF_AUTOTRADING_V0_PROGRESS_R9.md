# Cursor Handoff — Autotrading v0 R9 Progress

**Date**: 2026-05-16 KST
**Author**: Cursor (Claude Opus 4.7)
**Round**: R9 — Codex review safety fixes + first execution button
**Source spec**: `phase3_codex_docs/CURSOR_HANDOFF_AUTOTRADING_V0_NEXT_R9.md`
**Preceding handoff**: `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R8_FULL.md`

---

## 0. TL;DR

Codex review on R8 flagged two P1 safety bugs and two P2 completion
gaps. R9 fixes the P1 issues, completes one of the P2 items
(real `daily_runner` wiring), defers the other (`operator_cleared`)
per R9 §6's own "not required before paper acceptance" footnote, and
ships the first operator-facing execution button as scoped by R9 §7.

R9 hardware tally:

| Track | Status | Tests added |
|---|---|---|
| R9-A1 cancel-race fill → never reprice | **DONE** | 4 |
| R9-A2 t10 recovery marker re-block      | **DONE** | 2 |
| R9-B  daily_runner real wiring          | **DONE** | 6 |
| R9-C  control panel (dry-run button)    | **DONE** | 11 |
| R9-C  global_halt entry-gate            | **DONE** | 2 |
| R9-D  operator_cleared OrderStore event | **DEFERRED** (see §6) | — |

Test totals after R9:

```text
149 / 149 OK
```

Breakdown:

| Suite                          | Count | Notes                                |
|--------------------------------|------:|---------------------------------------|
| R5A orchestrator safety        |    11 | regression, untouched                 |
| R5B pre-submit safety          |    13 | regression, untouched                 |
| R6  ODNO normalize             |     8 | regression, untouched                 |
| R7  T10 applicator             |    13 | regression, untouched                 |
| R8  cancel_order adapter       |    13 | regression, untouched                 |
| R8  order_ids (R8-A)           |     6 | regression, untouched                 |
| R8  order_state classify (R8-B)|    15 | regression, untouched                 |
| R8  order_manager (R8-D)       |    13 | regression, untouched                 |
| **R9-A1 cancel-race**          |   **4** | new                                  |
| **R9-C halt @ manage_order**   |   **2** | new                                  |
| R8  t10 idempotency (R8-E)     |    15 | regression, untouched                 |
| **R9-A2 recovery re-block**    |   **2** | new                                  |
| R8  daily_runner (R8-F/G)      |    15 | regression, untouched                 |
| **R9-B  main() real wiring**   |   **6** | new                                  |
| **R9-C  control panel**        |  **11** | new                                  |
| t1 phase1 backward compat      |     2 | regression, untouched                 |
| **Total**                      | **149** |                                       |

---

## 1. R9-A1 — Cancel-race fill must not reprice

### Why

R8 §3 / Codex finding `[P1] Cancel-race fill can be logged as cancelled
and then repriced`. The previous `manage_order` cancel-confirm block
merged three different ccnl outcomes into a single boolean:

```python
cancel_confirmed = (
    bs_post_cancel.state == OrderState.CANCELLED
    or bs_post_cancel.state == OrderState.FILLED          # P1 here
    or (bs_post_cancel.state == OrderState.PARTIALLY_FILLED
        and bs_post_cancel.cancel_row_odno is not None)
)
```

Once `cancel_confirmed` was True the code unconditionally logged a
`CANCELLED` transition, set `terminal_state = OPEN_OR_PENDING`, and
fell through to the reprice eligibility block. For a FILLED cancel-race
the result was:

1. Original BUY *had* filled.
2. JSONL logged it as CANCELLED (wrong).
3. Reprice eligibility: BUY + `terminal_state == OPEN_OR_PENDING` +
   attempts left → submit a new child BUY at limit + step bps.
4. Net effect: we bought twice.

This is exactly the class of bug a paper acceptance test is meant
to catch BEFORE going live.

### What changed

`phase3/autotrade/order_manager.py` — the cancel-confirm block now
follows the R9 §3 decision table verbatim:

| Post-cancel state                                | Action                                                          |
|---|---|
| ccnl re-poll exception                           | log UNKNOWN, return UNKNOWN (no reprice)                       |
| `FILLED`                                         | log FILLED w/ note "cancel-race fill", return FILLED            |
| `PARTIALLY_FILLED` AND `cancel_row_odno is not None` | log PARTIALLY_FILLED, return PARTIALLY_FILLED              |
| Anything else (`OPEN_OR_PENDING`, `UNKNOWN`, `REJECTED`, `PARTIALLY_FILLED` w/o sibling) | log UNKNOWN ("cancel_requested_but_unconfirmed"), return UNKNOWN |
| `CANCELLED`                                      | log CANCELLED, fall through to reprice eligibility              |

The previous flat boolean was deleted. The "CANCELLED log → fall
through" branch is now reached ONLY when the post-cancel ccnl
classifier returns clean CANCELLED (zero fill + cancel sibling).

### Acceptance tests added

`phase3/tests/test_r8_order_manager.py::TestCancelRaceOutcomes`:

```text
test_cancel_race_full_fill_returns_filled_no_reprice
test_cancel_race_partial_fill_with_cancel_sibling_returns_partial_no_reprice
test_cancelled_after_timeout_can_reprice
test_cancel_unconfirmed_open_state_never_reprices
```

The first one is the regression test for the P1 bug. It instruments
`FakeBroker.cancel_order` so that between the manager's cancel send
and its post-cancel ccnl re-poll, the broker is mutated to a "fully
filled, sibling cancel row never landed" shape — i.e. exactly the
real-world race. Property asserted: outcome state is FILLED, no
CANCELLED transition is ever recorded in the OrderStore JSONL, and
`broker.placed` length stays at 1 (no reprice child submission).

The third test ensures the fix did NOT regress the legitimate
"clean CANCELLED → eligible for reprice" path that R8-D's max-attempts
test relies on.

---

## 2. R9-A2 — T10 recovery marker must keep blocking

### Why

R8 §4 / Codex finding `[P1] Recovery marker no longer blocks the next
applicator run`. The R8-E implementation guarded the journal with:

```python
if batch_state.prior_status == "started" and not args.allow_recovery_apply:
```

A crashed apply leaves the latest journal row as `started` (correct).
The recovery branch then *writes a new* `recovery` marker. So on the
NEXT run, the latest status for the same `apply_batch_id` is
`recovery`, the guard's `== "started"` check is False, and the
applicator silently proceeds to write another `started` marker and
mutate `holdings_log.xlsx` — without operator intervention.

That's exactly the scenario R8-E was supposed to make impossible.

### What changed

`phase3/autotrade/t10_applicator.py`:

```python
if (batch_state.prior_status in ("started", "recovery")
        and not args.allow_recovery_apply):
```

Plus a small forking of the operator advice message so the journal
clearly distinguishes "crashed mid-apply" (`prior_started_requires_operator`)
from "operator is being asked to re-confirm a prior recovery"
(`prior_recovery_requires_operator`). The returned rc stays `3` in
both cases so `daily_runner.evaluate_t10_apply` continues to escalate
the run to a hard stop.

`daily_runner.preflight` already enumerated `recovery_rows`, so the
runner-level preflight has been correct since R8-F. The R9-A2 fix
closes the corresponding hole at the `t10_applicator` CLI level so
direct manual invocations are no longer a backdoor.

### Acceptance tests added

`phase3/tests/test_r8_t10_idempotency.py::TestRecoveryMarkerReBlock`:

```text
test_prior_recovery_marker_blocks_without_override
test_prior_recovery_marker_allows_with_allow_recovery_apply
```

The first one drives the full sequence: crash → next-run writes
`recovery` → next-next-run (no override) must still be blocked with
rc=3 AND must append another `recovery` row whose `reason` is
`prior_recovery_requires_operator`. The second one verifies the
override still works exactly once.

---

## 3. R9-B — Real daily_runner wiring

### Why

R8 §5 / Codex finding `[P2] Daily runner is still injectable plumbing,
not an executable runner`. The R8-F `main()` raised a placeholder
`SystemExit` so the only way to invoke the pipeline was a hand-written
script with five injected callables. That's fine for tests, but a
non-starter for the R9-C control panel button.

### What changed

`phase3/autotrade/daily_runner.py`:

1. New `DefaultFactories` dataclass bundling the five injectable
   pipeline steps (`pre_reconcile_fn`, `post_reconcile_fn`,
   `intents_loader`, `manage_loop_fn`, `t10_apply_fn`).

2. New default implementations, all heavy-import-lazy:

   - `resolve_profile_paths(profile)` — reuses
     `reconcile._resolve_config_path` + `cache_health.load_config`
     so there is exactly one source of truth for `output_dir` and
     `holdings_log`.

   - `default_pre_reconcile_fn(ctx)` / `default_post_reconcile_fn(ctx)`
     — load local state + broker state, call `reconcile.reconcile(...)`,
     project the result into the smaller `ReconcileSummary` shape the
     hard-stop evaluator needs.

   - `default_intents_loader(ctx)` — reads
     `<run_dir>/submitted_intents.json` and projects BUY-side rows
     into `OrderIntent`. Always returns `[]` in dry-run mode.

   - `default_manage_loop_fn(ctx, intents)` — instantiates the real
     KIS paper `KisBrokerAdapter` + a per-run `OrderStore`, runs
     `manage_order(...)` per intent, short-circuits on the first
     non-FILLED outcome. Refuses to run if `KIS_ENV != paper`.

   - `default_t10_apply_fn(ctx, apply_mode)` — calls
     `t10_applicator.cmd_apply(...)` in-process with the right argv
     based on `apply_mode`, captures rc/stdout/stderr into a dict.

3. New `main(argv, *, factories=None, paths_resolver=None, stdout=None,
   stderr=None)` that:

   - Enforces the env-gate matrix BEFORE any side effect:
     - `--paper-submit` requires `KIS_PAPER_SUBMIT_OK=true`.
     - `--paper-submit` ALSO requires `KIS_PAPER_CANCEL_OK=true`
       (otherwise the manage loop's cancel/reprice path would
       dry-run and force every order into UNKNOWN — the gate fails
       loudly instead of silently degrading).
     - `--apply-t10` requires `--paper-submit` *and*
       `AUTOTRADE_T10_APPLY_OK=true`.
   - Resolves profile paths via the (injectable) resolver.
   - Builds factories (defaults if `factories=None`) and calls
     `run_daily(...)`.
   - Prints `[daily_runner] rc=<rc> run_id=<id>`, the run mode,
     and `report.md` / `report.json` paths to stdout.
   - On hard stop prints the where + reason to stderr.
   - Returns the int rc (never raises).

The keyword-only `factories=` / `paths_resolver=` / `stdout=` /
`stderr=` arguments are the test seam: they let the R9-B tests drive
`main()` with fake-safe wiring and an in-memory stdout/stderr capture.

### Defense-in-depth: halt check in `run_daily`

`run_daily` now reads `global_halt.read_halt()` immediately before
the manage-loop step. If halted, it short-circuits to a HardStop with
`where="manage_loop"` and `rc=2`, writes the daily report, and
returns. `order_manager.manage_order` also re-checks the halt flag
at entry (R9-C). Both layers are needed because the manage loop may
be invoked from non-runner callers too.

### Acceptance tests added

`phase3/tests/test_r8_daily_runner.py`:

```text
TestMainDryRunRealWiring::test_main_dry_run_executes_real_wiring_with_fake_safe_mode
TestMainDryRunRealWiring::test_main_returns_nonzero_on_hard_stop
TestMainEnvGates::test_paper_submit_requires_submit_gate
TestMainEnvGates::test_paper_submit_requires_cancel_gate
TestMainEnvGates::test_apply_t10_without_submit_is_rejected
TestMainEnvGates::test_apply_t10_requires_apply_gate
```

`test_paper_submit_requires_submit_gate` is the most-critical one:
it injects factories that count calls, asserts rc=2, AND asserts that
NONE of the factory callables were invoked when the gate fails — i.e.
the gate fires before any side effect.

---

## 4. R9-C — Control panel + global halt

### Why

R9 §7 / user request: ship the first operator-facing execution
button TODAY. R9 §7 is explicit that the button can do exactly one
thing: dry-run preflight + report, and everything else must be
disabled-with-tooltip.

### What changed

#### 4.1 `phase3/autotrade/global_halt.py` (new)

Single-source-of-truth for the halt flag the STOP button writes.

```text
default path: phase3/autotrade/runtime/global_halt.json
test override: AUTOTRADE_HALT_FILE env var (also accepted by reader)
```

Contract:

```text
file missing                    → not halted
file == {"halt": true, ...}    → halted
file unparseable / not object   → NOT halted (intentional —
                                  a broken file must not silently
                                  freeze trading; operator must
                                  write a well-formed payload)
```

Surface:

```text
read_halt(path?) -> HaltState
is_halted(path?) -> bool
assert_not_halted(*, where, path?) -> None | raises GlobalHaltError
write_halt(*, halt, reason, operator, path?) -> Path
clear_halt(*, operator, path?) -> Path
```

#### 4.2 `phase3/autotrade/order_manager.py` — entry-gate

`manage_order(...)` now reads `global_halt.read_halt()` as step 0a,
*before* the market-order refusal and before the duplicate guard.
If halted, returns `ManagedOrderOutcome(final_state=REJECTED, ...)`
with the halt reason + path in `note`. No broker call is made.

#### 4.3 `phase3/autotrade/control_panel.py` (new)

Tiny Tkinter panel that wraps `daily_runner`. Layout matches R9 §7:

```text
Autotrade Control Panel — R9 (paper-only)

[Run ID: ____________]  [Use latest awaiting_execution]

[1. Dry Run Preflight / Report]           ENABLED
[2. Paper Submit + Manage]                disabled (tooltip)
[3. T10 Apply Dry Run]                    disabled (tooltip)
[4. Full Paper Run]                       disabled (tooltip)
[STOP / Emergency Halt]    [Clear halt flag]

Status: <one-line state>
<scrolling text panel for daily_runner stdout/stderr>
```

How the Dry Run button works:

```text
1. read run_id from textbox (or "Use latest awaiting_execution"
   to auto-fill from runs whose run_meta.status == awaiting_execution)
2. shell out to: python -m phase3.autotrade.daily_runner \
       --profile paper --run-id <RUN_ID> --dry-run
3. capture rc + stdout + stderr
4. render them into the text panel + status bar
```

How STOP works:

```text
1. ask("are you sure")
2. write phase3/autotrade/runtime/global_halt.json with halt=true
3. show the path in the status bar
4. DOES NOT KILL THE SUBPROCESS — the daily_runner / order_manager
   check the halt flag at every manage_order entry and refuse to
   submit, which is the safe semantic
```

The Tk loop itself is not unit-tested. The five correctness-critical
helpers are:

```text
_list_run_candidates(output_dir)
_latest_awaiting_execution_run_id(output_dir)
_load_run_meta(output_dir, run_id)
_build_dry_run_argv(run_id, *, profile)
_write_halt_flag / _clear_halt_flag
run_dry_run(run_id, *, subprocess_run=...)
```

…and those are all covered by `test_r9_control_panel.py`.

### Acceptance tests added

`phase3/tests/test_r9_control_panel.py` (11 tests):

```text
TestLatestAwaitingExecution
  test_picks_newest_awaiting_execution
  test_returns_none_when_no_awaiting
  test_skips_unparseable_meta
  test_missing_daily_runs_dir_returns_none

TestBuildDryRunArgv
  test_argv_shape
  test_blank_run_id_rejected
  test_paper_only_profile

TestHaltFlagRoundTrip
  test_write_halt_blocks_until_cleared
  test_unparseable_halt_file_does_not_block

TestRunDryRunSubprocess
  test_run_dry_run_invokes_correct_argv_and_captures
  test_run_dry_run_surfaces_nonzero_rc
```

Plus two `manage_order`-side tests in
`test_r8_order_manager.py::TestGlobalHaltBlocksManageOrder`:

```text
test_halt_set_returns_rejected_with_note
test_halt_cleared_allows_submit
```

The "cleared allows submit" test is necessary to prove the halt
gate is a real gate (operating on the flag's current state) rather
than an accidental hard-refuse.

---

## 5. Operator workflow today (R9-C dry-run)

Once `phase3/config.yaml` is loadable and there is at least one
`run_meta.json` with `status: awaiting_execution`:

```bash
# Option A — CLI directly
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
    --profile paper \
    --run-id 20260516_001 \
    --dry-run

# Option B — control panel
PYTHONPATH=. python3 -m phase3.autotrade.control_panel
```

Either path produces:

```text
<output_dir>/daily_runs/<RUN_ID>/
    autotrade_daily_report.md
    autotrade_daily_report.json
```

The control panel surfaces the same paths in its status bar.

Paper-submit and T10-apply paths are wired but stay gated:

```bash
KIS_PAPER_SUBMIT_OK=true KIS_PAPER_CANCEL_OK=true \
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
    --profile paper --run-id <RUN_ID> --paper-submit

KIS_PAPER_SUBMIT_OK=true KIS_PAPER_CANCEL_OK=true \
AUTOTRADE_T10_APPLY_OK=true \
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
    --profile paper --run-id <RUN_ID> --paper-submit --apply-t10
```

The control panel keeps those buttons disabled-with-tooltip per
R9 §10; they will be enabled in R10 after the first clean paper
acceptance test.

---

## 6. Deferred: R9-D `operator_cleared` event

R9 §6 itself contains the rationale:

> This is not required before a single fresh paper acceptance test
> if every attempt uses deterministic child ids, but it is required
> before scheduler / auto-recovery.

The R8-D implementation already uses deterministic child
`client_order_id`s for reprice attempts (`<parent>:rpN`), and
`build_client_order_id(...)` produces deterministic IDs for
fresh-day intents. The duplicate guard is therefore "blocks forever
for the same `client_order_id`" — safe but blunt. R9-C only ships
the dry-run button, so no fresh-day intent will collide with a
prior day's UNKNOWN.

We defer `operator_cleared` to R10 (paper acceptance) or, at the
latest, R11 (scheduler), where it becomes structurally necessary.

---

## 7. Files touched

| File | Kind | Why |
|---|---|---|
| `phase3/autotrade/order_manager.py`        | modified | R9-A1 cancel-race branches + R9-C halt entry-gate |
| `phase3/autotrade/t10_applicator.py`       | modified | R9-A2 recovery re-block |
| `phase3/autotrade/daily_runner.py`         | modified | R9-B `main()` real wiring + R9-C halt defense-in-depth |
| `phase3/autotrade/global_halt.py`          | new      | R9-C halt flag module |
| `phase3/autotrade/control_panel.py`        | new      | R9-C operator UI |
| `phase3/tests/test_r8_order_manager.py`    | modified | +TestCancelRaceOutcomes, +TestGlobalHaltBlocksManageOrder |
| `phase3/tests/test_r8_t10_idempotency.py`  | modified | +TestRecoveryMarkerReBlock |
| `phase3/tests/test_r8_daily_runner.py`     | modified | +TestMainDryRunRealWiring, +TestMainEnvGates |
| `phase3/tests/test_r9_control_panel.py`    | new      | R9-C tests |
| `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R9.md` | new | this document |

No production code outside `phase3/autotrade/` was touched. No
config/.env was touched. No git changes were committed (R8 progress
doc precedent).

---

## 8. R9 Definition of Done — check

R9 §10 DoD vs current state:

| DoD line                                                          | Met?     |
|---|---|
| P1 cancel-race fill bug fixed                                     | YES (§1) |
| P1 recovery marker re-block fixed                                 | YES (§2) |
| daily_runner --dry-run actually executes real safe wiring         | YES (§3) |
| control_panel.py opens and has a working dry-run/report button    | YES (§4) |
| button never submits/cancels/applies in R9-C default mode         | YES (§4) |
| all R5A-R8 tests still green                                      | YES (149/149) |
| new R9 tests green                                                | YES |
| progress handoff written                                          | YES (this doc) |

Nice-to-haves:

| Item                                          | Met? |
|---|---|
| Paper Submit + Manage button visible-disabled | YES |
| Schedule ON/OFF visible-disabled              | n/a (not yet drawn — covered by tooltip on Full Paper Run) |
| global_halt.json STOP button implemented      | YES |

---

## 9. Suggested next round — R10

R10 should be a **paper acceptance test**:

1. Start phase3 cycle → produce an `awaiting_execution` artifact
   with one small BUY intent (1 share of a low-volatility ticker
   like APA or KO).
2. Operator opens the control panel.
3. Click `Use latest awaiting_execution`. Click `Dry Run Preflight /
   Report`. Verify rc=0, daily report rendered, no UNKNOWNs.
4. Enable both `KIS_PAPER_SUBMIT_OK=true` and `KIS_PAPER_CANCEL_OK=true`
   in the shell, run `daily_runner --paper-submit` from CLI.
5. Verify: real `place_order` call, polling, FILLED outcome (or
   clean CANCELLED if the limit didn't hit).
6. Enable `AUTOTRADE_T10_APPLY_OK=true`, run `--paper-submit
   --apply-t10`. Verify: `holdings_log.xlsx` updated, journal
   has both `started` and `applied` markers, post-reconcile clean.
7. Re-run with same `--run-id` and no override. Verify: hard-stop
   on `applied` marker (no double-apply).

Once R10 is green, R10 is also the round in which `operator_cleared`
should land (since the duplicate guard now matters for retry flows
that the operator may legitimately want to clear).

The control panel can then enable the `Paper Submit + Manage` button
and the `T10 Apply Dry Run` button. Schedule ON/OFF is R11.
