# Autotrade V0 — R8 Full Progress Handoff

Date: 2026-05-16
Round: R8 (full §11 "After Today's Cancel Test" slice)
Scope: §11 items 1, 2, 5, 6, 7, 8 (items 3 and 4 — `cancel_order()` adapter
method and the manual paper cancel acceptance test — landed in
`CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R8_CANCEL.md` the day before).

## TL;DR

We promoted the autotrade pipeline from "submit one order, then hand
off to manual T10" to a single-command paper daily flow with hard
stops, idempotent T10 apply, and an email-ready daily report. Every
new module is unit-tested against fakes; nothing in this round
touched the live broker.

- **Tests**: **124 / 124 green** across `R5A → R5B → R6 → R7-A → R8-cancel`
  plus the new `R8-A` / `R8-B` / `R8-D` / `R8-E` / `R8-F/G` suites.
- **Files added**: 6 modules + 5 tests + 1 doc.
- **Files changed**: 4 modules + 1 test (R8-A migration), plus the R8
  CANCEL handoff doc remains the source of truth for the cancel path.

## What landed by section of R8 §11

| #  | Item                                                  | Status            | Module(s)                                    |
|----|-------------------------------------------------------|-------------------|----------------------------------------------|
| 1  | `order_ids.py` normalization + `get_order()` fix      | done (R8-A)       | `phase3/autotrade/order_ids.py`              |
| 2  | `order_state.py` ccnl parser                          | done (R8-B)       | `phase3/autotrade/order_state.py`            |
| 3  | `cancel_order()` adapter method                       | done day-1        | (R8-cancel handoff)                          |
| 4  | manual paper cancel acceptance test                   | done day-1        | (R8-cancel handoff)                          |
| 5  | `order_manager.py` wait/cancel/reprice loop           | done (R8-D)       | `phase3/autotrade/order_manager.py`          |
| 6  | T10 applicator idempotency + recovery                 | done (R8-E)       | `phase3/autotrade/t10_apply_journal.py`,<br>`phase3/autotrade/t10_applicator.py` |
| 7  | `daily_runner.py` skeleton + hard stops              | done (R8-F)       | `phase3/autotrade/daily_runner.py`           |
| 8  | daily report (operator-action-first + email-ready)   | done (R8-G)       | `phase3/autotrade/daily_runner.py`           |

## R8-A — centralized ODNO normalization

### Why

KIS overseas inconsistently pads ODNOs:

- `POST /order` returns padded (`"0000041461"`)
- `GET /inquire-ccnl` and `GET /inquire-nccs` return stripped (`"41461"`)
- `POST /order-rvsecncl` requires the original padded form

R6 fixed the echo path. R7-A duplicated the helper. R8-A consolidates.

### Changes

- New `phase3/autotrade/order_ids.py` exporting `normalize_odno(value)`
  and `odnos_match(a, b)`.
- `phase3/autotrade/echo.py`: `_norm_odno` is now a re-export of
  `normalize_odno` (R6 callers keep working).
- `phase3/autotrade/t10_applicator.py`: local helper deleted, imports
  the centralized name as `_norm_odno`.
- `KisBrokerAdapter.get_order(broker_order_id)` (mandatory R8 §1.3
  fix) now goes through `normalize_odno`. Previously it compared with
  `str.strip() ==` and would miss every paper fill where the caller
  passed the padded form from the place ack.
- `KisBrokerAdapter.find_open_order(...)` migrated off the inline
  `lstrip("0") or "0"` snippet for symmetry.

### Tests (`phase3/tests/test_r8_order_ids.py`)

- 9 spec / 5 match / 3 back-compat alias / 5 `get_order()` contract = **21 / 21**.

## R8-B — ccnl row → 6-state classifier

### Why

R7-B confirmed `inquire-ccnl` is the only reliable paper source. R8
day-1 discovered the paper cancel contract: KIS does NOT mutate the
original row to mark it cancelled; it appends a sibling row with
`orgn_odno = target` + `rvse_cncl_dvsn = "02"`. The future
order_manager / daily_runner need a single canonical classifier they
can both trust.

### Changes

Added to `phase3/autotrade/order_state.py`:

- `BrokerOrderState` frozen dataclass — qty / price / state / sibling
  metadata; `state` is a `phase3.autotrade.order_state.OrderState` value.
- `classify_ccnl_row(target_row, *, all_rows, position_delta, target_odno)`
  — decision table in priority order:
  1. `reject_reason` present → REJECTED
  2. sibling cancel row exists AND `filled_qty <= 0` → CANCELLED
  3. `ordered>0, filled>=ordered` → FILLED
  4. `ordered>0, 0<filled<ordered` → PARTIALLY_FILLED (also exposes
     `cancel_row_odno` if a cancel sibling exists, so callers can
     tell "partial fill, remainder cancelled" apart from "partial
     fill, remainder still working")
  5. `filled==0` + `position_delta != 0` → UNKNOWN (R5B-P2.3 keeps
     its grey-area protection)
  6. `filled==0, remaining>0` → OPEN_OR_PENDING
  7. anything else → UNKNOWN
- `classify_from_full_ccnl(rows, target_odno, position_delta)` —
  finds the target row by normalized ODNO and runs the classifier.
- Field alias maps cover `ft_ord_qty3 / ft_ord_qty / ord_qty`,
  `ft_ccld_qty3 / ft_ccld_qty / tot_ccld_qty / ccld_qty`, etc.

The sibling-cancel detector accepts any of the three observed markers:
`rvse_cncl_dvsn == "02"`, `rvse_cncl_dvsn_name.endswith("취소")`,
or `sll_buy_dvsn_cd_name.endswith("취소")`.

### Tests (`phase3/tests/test_r8_order_state.py`)

- 6 main interpretation cases
- 5 cancel-sibling contract cases (including padded `orgn_odno` and
  partial-fill-before-cancel grey area)
- 3 field alias cases
- 3 `classify_from_full_ccnl` integration cases (incl. real R8-day1
  shape replay)
= **17 / 17**.

## R8-D — wait / cancel / reprice loop

### Why

R8 §6 asks for the missing piece between "submit ack" and "T10 apply":
poll → cancel on timeout → optional BUY reprice → stop on UNKNOWN.
Without it, a stuck OPEN order silently rides into T10 apply and gets
misclassified.

### Design

`phase3/autotrade/order_manager.py`:

- `OrderManagementPolicy` (frozen):

  ```python
  poll_interval_sec: 5.0
  max_wait_sec: 120.0
  max_reprice_attempts: 2
  reprice_step_bps: 10.0
  max_total_slippage_bps: 35.0
  cancel_before_reprice: True
  cancel_confirm_wait_sec: 3.0
  allow_market_order: False   # never overridden in R8
  ```

- `reprice_limit_buy(...)`: `new = min(cur*(1+step), original*(1+cap))`,
  rounded to 4 decimals. `reprice_would_improve(...)` short-circuits
  when the ceiling has been reached so we don't resubmit at the same
  limit.

- `manage_order(intent, *, adapter, store, policy, ...)`:
  - refuses non-LIMIT orders outright
  - fires the order_store duplicate guard before any submit
  - polls ccnl after `place_order`, classifying each round via
    `classify_from_full_ccnl`
  - FILLED → record + return
  - PARTIALLY_FILLED → record + cancel remainder + stop (no reprice
    on partial in v0)
  - UNKNOWN / REJECTED → record blocking event + stop
  - OPEN_OR_PENDING beyond `max_wait_sec` → cancel
    - cancel rejected by adapter → UNKNOWN
    - cancel `dry_run=True` → CANCEL_REQUESTED (unconfirmed; no reprice)
    - cancel confirmed (CANCELLED / FILLED / PARTIAL+sibling) → if
      BUY + attempts remain + ceiling not reached → reprice
  - reprice uses a child `client_order_id` of the form
    `<parent>:rp<n>` — stable across crashes, so the order_store
    duplicate guard still works after a recovery pass

- `time_provider` and `sleep_fn` are injected. The whole acceptance
  suite runs in milliseconds against a deterministic `FakeClock`.

### Tests (`phase3/tests/test_r8_order_manager.py`)

- immediate full fill (no cancel, no reprice)
- timeout → cancel confirmed → reprice → fill at new limit
- 3 timeouts → CANCELLED at `max_reprice_attempts` boundary
- partial fill → cancel remainder → PARTIALLY_FILLED terminal
- UNKNOWN (position moved with zero ccnl fill) → no cancel, blocking
  event in the store
- cancel `dry_run` → CANCEL_REQUESTED + no reprice
- cancel rejected by broker → UNKNOWN + no reprice
- place_order rejected → REJECTED + no poll
- duplicate guard fires before submit
- 3 reprice-math purity tests
- market order refusal
= **13 / 13**.

The FakeBroker emits the same stripped-ODNO rows we see in real ccnl
and adds the right sibling cancel row on cancel (mirrors the R8-day1
production trace).

## R8-E — T10 applicator idempotency + recovery

### Why

R7-A's apply path mutated `holdings_log.xlsx` and the cash ledger
BEFORE writing `execution_applied.csv`. A crash between those two
writes left no record-of-truth, and a naive rerun double-counted the
fills.

### New module `phase3/autotrade/t10_apply_journal.py`

- `compute_apply_batch_id(run_id, items)` — deterministic
  SHA-256(16-hex) over `(run_id, RecRowId, ODNO, ticker, qty, price)`
  sorted to be order-independent.
- Journal file: `daily_runs/<RUN_ID>/autotrade_t10_apply_attempts.jsonl`
  with statuses `started`, `applied`, `recovery`, `aborted`.
- `write_marker(...)`, `read_journal(...)`, `latest_status_for_batch(...)`,
  `inspect_batch(...)` — append-only, partial-line tolerant.
- `backup_holdings_log(...)` copies `holdings_log.xlsx` into the run
  dir BEFORE mutation, named
  `holdings_log.preapply.<run_id>.<batch_id>.xlsx`. Existing backups
  are never overwritten.
- `local_duplicate_present(executed_df, history_df, today_str)` —
  defense-in-depth check that the same `(Date, Ticker, Action, Shares,
  Price)` quadruple is not already in `History` for today.

### `t10_applicator` integration

`cmd_apply` apply path now:

1. computes `batch_id`
2. inspects the journal for prior status
   - `applied` → abort with `rc=2`, writes `aborted` marker
   - `started` without `applied` → enters recovery mode, writes
     `recovery` marker, returns `rc=3` unless `--allow-recovery-apply`
3. runs `local_duplicate_present` against `hm.load_history()`. If a
   same-day same-price-shares row already exists and
   `--allow-duplicate-apply` is not set → abort with `rc=2`
4. backs up `holdings_log.xlsx` → writes `started` marker
5. runs the original R7-A mutation path
6. records the artifact + writes `applied` marker

New CLI flag: `--allow-recovery-apply`. The R7-A `_make_args` fixture
predates this flag; tests that use it still pass because R7-A only
exercises the dry-run branch.

### Tests (`phase3/tests/test_r8_t10_idempotency.py`)

- 3 batch-id determinism tests
- 3 journal IO tests
- 3 local-duplicate-detect tests
- 2 local-history-blocks-rerun tests (with + without override)
- 1 normal-apply tests (started + applied + backup created)
- 1 applied-marker blocks-rerun test
- 2 started-without-applied recovery tests (rc=3 path + override path)
= **15 / 15**.

R7-A regression: still **10 / 10** (no change in behaviour for
dry-run or for clean apply, which is what R7-A exercised).

## R8-F — `daily_runner.py` single-command flow

### Why

R8 §F asks for one command that takes an artifact run_id and walks
it through preflight → reconcile → submit/manage → T10 apply →
reconcile → report. The pipeline must surface every R8 §8 hard stop
to the operator without doing anything destructive on its own.

### Design

`phase3/autotrade/daily_runner.py`:

- `DailyRunContext` dataclass — all CLI inputs as plain data.
- `HardStop` dataclass — surfaced verbatim into the daily report.
- `DailyRunResult` dataclass — final summary returned by `run_daily`.
- `run_daily(ctx, *, pre_reconcile_fn, post_reconcile_fn,
  manage_loop_fn, intents_loader, t10_apply_fn)` — orchestrates the
  whole pipeline with **every step injectable** so the acceptance
  suite swaps in fakes with zero network.

Steps (each can hard-stop the run):

1. `preflight(ctx)`:
   - `--run-id` required
   - `run_dir` exists
   - `run_meta.json` parseable + status == `awaiting_execution`
   - **no open `started` journal markers and no `recovery` markers**
     in the t10 journal (unless `--allow-recovery-apply`)

2. `evaluate_reconcile(pre)`:
   - any `qty_mismatch_count > 0` → rc=2

3. `manage_loop_fn(ctx, intents)` (only when `--paper-submit`):
   - `evaluate_outcomes(...)` aborts if any UNKNOWN or stale
     OPEN/CANCEL_REQUESTED

4. `t10_apply_fn(ctx, False)` — dry-run pass, always run
5. `t10_apply_fn(ctx, True)` — apply pass, only when `--apply-t10` +
   `--paper-submit` + `AUTOTRADE_T10_APPLY_OK=true`
   - `evaluate_t10_apply(...)` distinguishes rc=2 (policy) from rc=3
     (recovery)
6. `evaluate_reconcile(post)` (only when apply happened):
   - any mismatch → rc=4

`--dry-run` is the default (and is auto-set when `--paper-submit` is
not passed). The CLI `main()` function raises an explicit
`SystemExit` instruction message — R8-F deliberately ships **plumbing
only**. The real wiring (real broker adapter, real `reconcile`, real
`t10_applicator.cmd_apply`) is intentionally injected from a thin
script in the next round.

### Daily report (R8-G)

`render_daily_report_json(result)` produces every R8 §9 section:

- `schema_version`, `ts`, `run_id`, `autotrade_run_id`, profile, mode
- **`operator_action_required`** — first thing the report exposes;
  when set, the MD renders an `⚠ Operator action required` section
  BEFORE the orders table (email-ready)
- `hard_stop` (full asdict)
- `pre_reconcile` / `post_reconcile` slices
- `outcome_counts` + per-order `outcomes` table (ticker, side, qty,
  filled, last_limit_price, state, reprice_attempts, note)
- `t10_apply` summaries (`dry_run` always; `apply` when applicable)
- `artifact_paths`
- `cash_drift_usd` (post if applied, else pre)

Files: `daily_runs/<RUN_ID>/autotrade_daily_report.md` +
`autotrade_daily_report.json`.

### Tests (`phase3/tests/test_r8_daily_runner.py`)

- happy-path dry-run produces a clean report
- happy-path paper_submit + apply_t10 + gate ON → success, post
  reconcile section present
- `--apply-t10` without `AUTOTRADE_T10_APPLY_OK=true` → rc=2
- preflight: missing run_id / run_dir / status != awaiting_execution /
  recovery marker (rc=3)
- pre-reconcile: qty_mismatch → rc=2
- manage_loop: UNKNOWN / stale OPEN / cancel_requested → rc=2
- t10_apply rc=3 → rc=3
- post-reconcile mismatch → rc=4
- daily report shape: all R8 §9 sections present
- MD ordering: operator-action section precedes orders table
= **15 / 15**.

## Test summary

| Suite                                             | Tests |
|---------------------------------------------------|-------|
| `test_r5a_orchestrator_safety`                    | 7     |
| `test_r5b_pre_submit_safety`                      | 19    |
| `test_r6_odno_normalize`                          | 12    |
| `test_r7_t10_applicator`                          | 10    |
| `test_r8_cancel_order`                            | 7     |
| `test_r8_order_ids`        (new)                  | 21    |
| `test_r8_order_state`      (new)                  | 17    |
| `test_r8_order_manager`    (new)                  | 13    |
| `test_r8_t10_idempotency`  (new)                  | 15    |
| `test_r8_daily_runner`     (new)                  | 15    |
| **Total**                                         | **124 / 124 OK** |

Run command:

```bash
PYTHONPATH=. python3 -m unittest \
  phase3.tests.test_r5a_orchestrator_safety \
  phase3.tests.test_r5b_pre_submit_safety \
  phase3.tests.test_r6_odno_normalize \
  phase3.tests.test_r7_t10_applicator \
  phase3.tests.test_r8_cancel_order \
  phase3.tests.test_r8_order_ids \
  phase3.tests.test_r8_order_state \
  phase3.tests.test_r8_order_manager \
  phase3.tests.test_r8_t10_idempotency \
  phase3.tests.test_r8_daily_runner
```

## File touch summary

### Added

- `phase3/autotrade/order_ids.py`
- `phase3/autotrade/order_manager.py`
- `phase3/autotrade/t10_apply_journal.py`
- `phase3/autotrade/daily_runner.py`
- `phase3/tests/test_r8_order_ids.py`
- `phase3/tests/test_r8_order_state.py`     (R8-B section in same module)
- `phase3/tests/test_r8_order_manager.py`
- `phase3/tests/test_r8_t10_idempotency.py`
- `phase3/tests/test_r8_daily_runner.py`
- `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R8_FULL.md` (this file)

### Modified

- `phase3/autotrade/echo.py`             (R8-A: alias to `normalize_odno`)
- `phase3/autotrade/t10_applicator.py`   (R8-A: import alias; R8-E: idempotency + journal + backup + duplicate detect)
- `phase3/autotrade/kis_broker_adapter.py` (R8-A: `get_order()` + `find_open_order()` normalize)
- `phase3/autotrade/order_state.py`      (R8-B: `BrokerOrderState` + classifier + sibling cancel contract)

## Outstanding for the next round

R8-F ships injectable plumbing; the next round needs the **real
wiring**:

1. `pre_reconcile_fn` / `post_reconcile_fn`: thin adapter that calls
   `phase3.autotrade.reconcile.reconcile(...)` against
   `KisBrokerAdapter` + `HoldingsManager` and projects the result
   into `daily_runner.ReconcileSummary`.
2. `manage_loop_fn`: load intents from `recommendations.csv` (BUY
   rows only in R8), build `OrderIntent`s, call `manage_order(...)`.
3. `t10_apply_fn`: spawn `t10_applicator.cmd_apply(...)` with the
   live `make_adapter` / `make_hm` / `record_execution_artifact`.
4. CLI: replace the placeholder `SystemExit` in `daily_runner.main`
   with the wiring above, gated by env vars.
5. Live paper acceptance test (analog to the R8-day1 cancel test):
   one tiny BUY intent through the entire pipeline, including
   `--apply-t10`. Record the produced
   `autotrade_daily_report.md/json` and the
   `autotrade_t10_apply_attempts.jsonl` so we have a reference shape
   on disk.
6. Cron / scheduler wrapper (deferred per R8 §10).

## Operator runbook (after live wiring lands)

```bash
# clean dry-run (default)
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
  --profile paper --run-id 20260516_191533_daily --dry-run

# submit + manage only (no T10 apply)
KIS_PAPER_SUBMIT_OK=true KIS_PAPER_CANCEL_OK=true \
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
  --profile paper --run-id 20260516_191533_daily --paper-submit

# full daily: submit + manage + T10 apply
KIS_PAPER_SUBMIT_OK=true KIS_PAPER_CANCEL_OK=true AUTOTRADE_T10_APPLY_OK=true \
PYTHONPATH=. python3 -m phase3.autotrade.daily_runner \
  --profile paper --run-id 20260516_191533_daily --paper-submit --apply-t10
```

If the runner aborts with rc=3, inspect
`daily_runs/<RUN_ID>/autotrade_t10_apply_attempts.jsonl`. Cross-check
the listed `applicable_rec_ids` and the
`holdings_log.preapply.<RUN_ID>.<batch_id>.xlsx` backup against the
current `holdings_log.xlsx`. Only pass `--allow-recovery-apply` once
you have manually confirmed the prior attempt did NOT already mutate
the local Excel.

rc semantics:

| rc | meaning                                                          |
|----|------------------------------------------------------------------|
| 0  | clean run                                                        |
| 2  | policy / configuration hard stop (preflight, pre-reconcile, manage_loop, t10 policy abort, missing env gate) |
| 3  | t10 applicator recovery mode (operator must inspect before retry) |
| 4  | post-apply reconcile mismatch (local Excel and broker disagree after apply) |
