# Cursor Handoff — Autotrading v0 R10 Progress (pre-market portion)

**Date**: 2026-05-16 KST (Saturday, US market closed for the weekend)
**Author**: Cursor (Claude Opus 4.7)
**Round**: R10 — operator dashboard + intent-file safety + pre-market readiness
**Source spec**: `phase3_codex_docs/CURSOR_HANDOFF_AUTOTRADING_V0_NEXT_R10_UI_MARKET_OPEN.md`
**Preceding handoff**: `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R9.md`

---

## 0. TL;DR

R10 has two halves:

1. **Track A — Operator UI**: turn R9's minimal button into a proper
   operator dashboard with run/gate/intent/halt/journal visibility,
   five purpose-built action buttons, a Command Preview, and a
   double-click launcher. **DONE this round.**
2. **Track B — Market-open paper acceptance test**: actually run one
   1-share BUY through the full pipeline at next US open. **NOT
   executed yet** (Saturday in KST, US weekend) — runbook is locked
   in this document and the code is ready.

R10 test tally:

| Track | Status | Tests added |
|---|---|---|
| R10-1 intent file validator + writer | DONE | 14 |
| R10-1b daily_runner silent-zero-submit block | DONE | 4 |
| R10-2 control panel (PanelState / gates / preview / Tk) | DONE | 36 |
| R10-3 macOS launcher `.command` | DONE | 3 |
| R10 market-open acceptance | **deferred to next US session** | — |

Tests after R10:

```text
203 / 203 OK
```

Increment vs R9 baseline:

```text
R9 finish: 149 / 149 OK
R10 add:   +54 (intent validation + UI dashboard + launcher contract)
R10 end:   203 / 203 OK
```

---

## 1. Codex review evaluation

R10 §1-§7 from the spec, mapped to action taken in this round:

| Spec section | Verdict | Implementation |
|---|---|---|
| §1 Non-negotiable safety rules (1-14)   | required           | enforced by control panel + daily_runner |
| §2.1 dashboard layout (Run / Safety Gates / Actions / Command Preview / Output) | required | `control_panel.launch_panel` rewritten |
| §2.2 button enablement matrix (5 buttons + STOP + Clear-halt) | required | `compute_button_gates` |
| §2.3 commands UI should run            | required           | `build_command_preview` + `_build_*_argv` |
| §2.4 double-click launcher              | required           | `scripts/run_autotrade_control_panel.command` |
| §2.5 NOT a scheduler                    | required           | no scheduling code added |
| §3.1 baseline regression                | required           | 203/203 OK, full suite |
| §3.2 fresh `awaiting_execution` artifact | **needs operator** | runbook §4.1 below |
| §3.3 `submitted_intents.json` guarantee | required           | new `intents_io` module + paper-submit guard |
| §3.4 local state pre-market checks      | partial            | dashboard surfaces gates/halt/journal; KIS API ping deferred |
| §3.5 dry-run from CLI + UI              | required           | both paths working; smoke-tested locally |
| §3.6 limit price policy                 | operator decision | runbook §4.4 below |
| §4 market-open test A/B/C               | **needs market**   | runbook below, code ready |
| §5 failure playbook                     | required           | copied into runbook §6 below |
| §6 9 acceptance test cases              | required           | all 9 covered in `test_r10_ui_button_gates.py` |
| §7 DoD checklist                        | required           | §9 below maps each line to evidence |

Two deliberate de-prioritisations vs R10 spec, deferred to R11:

- **Auto-generation of `submitted_intents.json` from raw recommendations
  CSV** (R10 §3.3 option 1). R10 ships option 2 (UI-visible validation
  + paper-submit hard-stop) plus a `make_buy_intent_row` /
  `write_submitted_intents` helper so the operator can author one
  by hand or via tiny script. The full recommendation-row mapping
  needs its own design round.
- **`operator_cleared` OrderStore event** (was already deferred from
  R9 §6). Still deferred until R11 scheduler / auto-recovery.

---

## 2. R10-1 — Intent file validation (R10 §3.3)

### Why

R9 §3.3 of the R10 spec stated:

> daily_runner.default_intents_loader only reads submitted_intents.json.
> If submitted_intents.json is missing, --paper-submit may submit
> zero orders.

This is the silent-zero-submit risk. An operator could click Paper
Submit, see rc=0, and conclude "happy path" while no order was placed.
Worse, the next US session day's report would also show rc=0 and the
operator would assume the previous trade had succeeded somehow.

### What changed

#### 2.1 New module `phase3/autotrade/intents_io.py`

```text
SCHEMA_VERSION = "intents/v1"

IntentFileStatus(state, reason, path, intent_count, buy_count, rows)
    state in {"missing", "unreadable", "malformed", "empty", "ok"}

validate_submitted_intents(run_dir) -> IntentFileStatus
make_buy_intent_row(...) -> dict
write_submitted_intents(run_dir, intents, *, run_id, overwrite=False)
```

Wire format:

```json
{
  "schema_version": "intents/v1",
  "run_id": "20260516_001",
  "generated_at": "...",
  "intents": [
    {"client_order_id": "...", "symbol": "APA", "market": "NASD",
     "side": "BUY", "qty": 1, "ord_type": "LIMIT", "limit_price": 18.85}
  ]
}
```

Bare-list `[{...}, ...]` is also accepted for backwards compatibility.
Every row goes through a single `_validate_row` predicate that enforces:

- side == "BUY" (R10 is BUY-only)
- ord_type == "LIMIT" (no market orders, R10 §1.2)
- qty > 0
- limit_price > 0
- all required keys present and the correct type

`write_submitted_intents` refuses to overwrite an existing file unless
`overwrite=True` — preventing the OrderStore duplicate-guard footgun
where two different intent payloads share a run_id.

#### 2.2 daily_runner `paper-submit` guard

`default_intents_loader` was rewritten to:

- return `[]` in dry-run mode (unchanged)
- in paper-submit mode, call `intents_io.validate_submitted_intents`
  and raise `RuntimeError(...)` on anything but `state == "ok"`

`run_daily` wraps `intents_loader(ctx)` in its own try/except and
converts a raise into a HardStop with `where="intents_loader"`, rc=2,
and the reason text from the validator. The daily report renders that
in the operator-action-first section, and the runner returns rc=2
without ever calling `manage_loop_fn`.

### Tests

`phase3/tests/test_r10_intent_file_validation.py`:

```text
TestIntentFileValidator (10 tests)
  missing | malformed_json | malformed_object | empty | bare_list_accepted
  ok | sell_row_rejected_as_malformed | market_order_rejected
  zero_qty_rejected | missing_limit_price_rejected

TestIntentFileWriter (4 tests)
  make_buy_intent_row_validates / rejects_zero_qty
  write_then_read_roundtrip / refuses_overwrite_by_default

TestDailyRunnerPaperSubmitGuard (4 tests)
  missing_intents_blocks_paper_submit
  empty_intents_blocks_paper_submit
  malformed_intents_blocks_paper_submit
  dry_run_does_not_require_intents_file
```

The three blocking tests assert that the factory's `manage_loop_fn` /
`t10_apply_fn` were never invoked when paper-submit was blocked at the
intent step — i.e. the guard fires before any side effect.

---

## 3. R10-2 — Operator dashboard

### Why

R9 shipped a tiny "Dry Run Preflight" button with no run-state
visibility, no env-gate display, no command preview, no per-button
disable-reason, and no confirmation checkboxes. R10 §2 made all of
those mandatory because the user explicitly asked to *see* the
"execution button" before pressing it.

### What changed

`phase3/autotrade/control_panel.py` was rewritten end-to-end:

#### 3.1 Pure helpers (the testable surface)

```text
PanelState     — one frame of dashboard data
LastReport     — most recent daily report on disk + rc summary
GateStatus     — one Safety Gates row
T10JournalStatus — open-started / recovery batch markers
ButtonGate     — one (enabled, reason) tuple per button

compute_panel_state(*, output_dir, run_id, env, halt_path)
compute_button_gates(state, *, dry_run_rc_clean, submit_outcome_clean,
                              confirm_submit_checked, confirm_apply_checked)
build_command_preview(button_id, *, run_id, profile)
submit_outcome_is_clean(run_dir) -> (bool, reason)
```

Three of those (`compute_panel_state`, `compute_button_gates`,
`build_command_preview`) carry the entire dashboard contract. Every
R10 §6 acceptance case is enforced through them, so the Tk loop is
just rendering — there is no business logic embedded in widget
callbacks.

#### 3.2 Button enablement matrix (R10 §2.2 verbatim)

```text
Dry Run Preflight
    enabled when: run_id is present

Paper Submit + Manage
    enabled when ALL of:
      - run_id present
      - artifact_status == "awaiting_execution"
      - submitted_intents.json state == "ok" AND buy_count > 0
      - KIS_ENV == "paper"
      - KIS_PAPER_SUBMIT_OK == "true"
      - KIS_PAPER_CANCEL_OK == "true"
      - global_halt is OFF
      - dry-run in this UI session returned rc=0
      - "I authorize PAPER SUBMIT" checkbox ticked

T10 Apply Dry Run
    enabled when ALL of:
      - run_id present
      - latest daily report shows FILLED-only outcomes (no UNKNOWN
         / OPEN_OR_PENDING / CANCEL_REQUESTED / PARTIALLY_FILLED)
      - T10 journal has no unresolved started/recovery markers
      - global_halt is OFF

T10 Apply Real
    enabled when ALL of:
      - run_id present
      - same clean-outcome predicate as T10 Apply Dry Run
      - AUTOTRADE_T10_APPLY_OK == "true"
      - T10 journal has no unresolved started/recovery markers
      - global_halt is OFF
      - "I authorize T10 REAL APPLY" checkbox ticked

Full Paper Run
    HARD-DISABLED in R10 §2.2. The disabled label points the
    operator at running the two buttons sequentially instead.
    Will be enabled in R11 only after one clean R10 acceptance.

STOP / Emergency Halt        : always enabled
Clear halt flag              : always enabled
```

When any precondition is missing, the button's `disabled — <reason>`
label is rendered next to it in grey so the operator can see exactly
what is blocking. There are no clickable disabled buttons.

Confirmation checkbox state is cleared after every Paper Submit and
T10 Apply attempt — the operator cannot leave it ticked between
sessions.

#### 3.3 Command Preview

`build_command_preview(button_id, run_id, profile)` produces the exact
shell line the panel will execute, including:

- the working directory hint (`cd /Users/.../0316- && `)
- `PYTHONPATH=.`
- the explicit `python3 -m phase3.autotrade.daily_runner` (or
  `t10_applicator`) command
- env-gate placeholders like `KIS_PAPER_SUBMIT_OK=true` rendered as
  *placeholders*, never as `os.environ[...]` actual values

This is `secrets-safe` by construction: the preview text never
includes anything read from the operator's shell — only the literal
string `=true` is shown. The test `test_command_preview_does_not_leak_secret_values`
locks the contract by setting `KIS_APPKEY` in the test process and
asserting the value does not appear in the preview.

#### 3.4 Tk layout (R10 §2.1)

```text
[ Refresh ] [ Use latest awaiting_execution ]  [ Clear halt ] [ STOP ]

Run ID: [_______________________________]

┌ Run ────────────────────────────────────────────────────────────┐
│ Artifact status:       awaiting_execution                       │
│ Run dir:               /Users/.../daily_runs/20260516_001       │
│ submitted_intents.json: OK  (intents=1 buy=1)                   │
│ Latest daily report:    /Users/.../autotrade_daily_report.md    │
│ Latest report summary:  rc=0 OK                                 │
└─────────────────────────────────────────────────────────────────┘

┌ Safety Gates ───────────────────────────────────────────────────┐
│ KIS_ENV:               paper                OK                  │
│ KIS_PAPER_SUBMIT_OK:   (unset/false)        BLOCK   Required... │
│ KIS_PAPER_CANCEL_OK:   (unset/false)        BLOCK   Required... │
│ AUTOTRADE_T10_APPLY_OK:(unset/false)        BLOCK   Required... │
│ global_halt:           halted=False  reason=(none)              │
│ T10 journal:           clean                                    │
└─────────────────────────────────────────────────────────────────┘

┌ Actions ────────────────────────────────────────────────────────┐
│ [1. Dry Run Preflight / Report]                                 │
│ [2. Paper Submit + Manage]      disabled — KIS_PAPER_SUBMIT_OK…│
│ [3. T10 Apply Dry Run]          disabled — no clean paper-submit…│
│ [4. T10 Apply Real]             disabled — AUTOTRADE_T10_APPLY_OK…│
│ [5. Full Paper Run (R11)]       disabled — enable in R11…       │
└─────────────────────────────────────────────────────────────────┘

[x] I authorize PAPER SUBMIT for this run    [ ] I authorize T10 …

┌ Command Preview ────────────────────────────────────────────────┐
│ $ PYTHONPATH=. python3 -m phase3.autotrade.daily_runner …       │
└─────────────────────────────────────────────────────────────────┘

┌ Output / Log ───────────────────────────────────────────────────┐
│ $ python3 -m phase3.autotrade.daily_runner …                    │
│ [daily_runner] rc=0 run_id=20260516_001                         │
│ [daily_runner] mode=dry-run                                     │
│ [daily_runner] report.md = …                                    │
│ [daily_runner] report.json = …                                  │
└─────────────────────────────────────────────────────────────────┘
```

Every value-bearing row uses a `tk.StringVar` that the `Refresh`
button rewrites by calling `compute_panel_state(...)` again, so the
state is always re-read from disk + env at the moment of refresh.

### Tests

R10 added two new control-panel suites that lock the dashboard
contract:

```text
phase3/tests/test_r10_ui_button_gates.py        (21 tests)
phase3/tests/test_r10_control_panel_dashboard.py(15 tests)
```

…on top of the R9 suite (`test_r9_control_panel.py`, 11 tests) which
still covers the older subprocess wrapper + halt round-trip.

All R10 §6 acceptance cases are covered by name:

```text
missing_submitted_intents_disables_submit          ✓
zero_intents_disables_submit                        ✓
dry_run_rc_zero_enables_submit_when_env_gates_true  ✓
global_halt_disables_submit                         ✓
submit_button_requires_confirmation                 ✓
t10_apply_button_requires_latest_submit_success     ✓
t10_apply_button_blocks_unknown_outcome             ✓
command_preview_matches_executed_command            ✓
launcher_command_contains_no_secrets                ✓
```

---

## 4. R10-3 — macOS double-click launcher

`scripts/run_autotrade_control_panel.command` is a 25-line zsh wrapper
that:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
export PYTHONPATH="."
exec python3 -m phase3.autotrade.control_panel
```

…and explicitly **does not** set any of the safety-gate env vars. The
operator must export `KIS_PAPER_SUBMIT_OK` / `KIS_PAPER_CANCEL_OK` /
`AUTOTRADE_T10_APPLY_OK` in a shell before launching to authorize
those actions. The dashboard then reads them at refresh time.

Three contract tests in `test_r10_ui_button_gates.py`:

```text
test_launcher_exists_and_is_executable
test_launcher_command_contains_no_secrets   # forbids KIS_APPKEY=,
                                            # SUBMIT/CANCEL/APPLY=,
                                            # and the demo key from
                                            # the project context
test_launcher_runs_control_panel_module     # locks the module path
```

Double-click flow on macOS:

```text
Finder → scripts/ → run_autotrade_control_panel.command (double-click)
    → Terminal opens
    → echoes the repo path and python version
    → launches the Tk dashboard window
```

---

## 5. Files touched

| File | Kind | Why |
|---|---|---|
| `phase3/autotrade/intents_io.py`              | new      | R10-1 validator + writer + IntentFileStatus |
| `phase3/autotrade/daily_runner.py`            | modified | R10-1b silent-zero-submit guard + hard-stop |
| `phase3/autotrade/control_panel.py`           | rewritten | R10-2 dashboard, gates, preview, halt round-trip |
| `scripts/run_autotrade_control_panel.command` | new (+x) | R10-3 double-click launcher |
| `phase3/tests/test_r10_intent_file_validation.py` | new   | 18 tests (validator + writer + paper-submit guard) |
| `phase3/tests/test_r10_ui_button_gates.py`    | new      | 21 tests (button matrix + preview + launcher contract) |
| `phase3/tests/test_r10_control_panel_dashboard.py` | new | 15 tests (PanelState, LastReport, submit-clean predicate) |
| `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10.md` | new | this document |

No production code outside `phase3/autotrade/` and `scripts/` was
touched. `.env` was not touched. No git commits were made.

---

## 6. Market-open paper acceptance runbook (R10 §4)

**Status: not yet executed** — Saturday in KST, US market closed for
the weekend. Run this on the next US regular session.

### 6.1 Authoring the test artifact

Before the next session opens, do this in a shell:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
export PYTHONPATH=.
RUN_ID="$(date +%Y%m%d_R10A)"

mkdir -p "<output_dir>/daily_runs/$RUN_ID"
cat > "<output_dir>/daily_runs/$RUN_ID/run_meta.json" <<EOF
{
  "schema_version": "artifact/v1",
  "run_id": "$RUN_ID",
  "status": "awaiting_execution"
}
EOF

python3 - <<'PY'
import os
from phase3.autotrade import intents_io
run_id = os.environ["RUN_ID"]
run_dir = f"<output_dir>/daily_runs/{run_id}"
row = intents_io.make_buy_intent_row(
    client_order_id=f"co-{run_id}-B-1-r10a",
    symbol="APA",                    # operator picks ticker
    qty=1,
    limit_price=18.85,                # operator picks fresh limit at T-30m
)
print(intents_io.write_submitted_intents(run_dir, [row], run_id=run_id))
PY
```

Replace `<output_dir>` with the path reported by:

```bash
python3 -c "from phase3.autotrade.daily_runner import resolve_profile_paths; \
            print(resolve_profile_paths('paper')['output_dir'])"
```

### 6.2 Pre-market checks (T-60 min)

1. Open the panel:

```bash
open /Users/shin-il/PyCharmMiscProject/0316-/scripts/run_autotrade_control_panel.command
```

2. Click `Use latest awaiting_execution` → run_id auto-fills.
3. Verify the Run section:
   - `artifact_status: awaiting_execution`
   - `submitted_intents.json: OK (intents=1 buy=1)`
   - `T10 journal: clean`
   - `global_halt: halted=False`
4. Click `1. Dry Run Preflight / Report`. Expect:
   - `[daily_runner] rc=0`
   - report.md / report.json paths shown
   - "no hard_stop"

### 6.3 At market open (T+3 to T+10 min)

1. In the same Terminal launched by the `.command` script, before
   you press the panel button, export the submit gates manually:

```bash
export KIS_PAPER_SUBMIT_OK=true
export KIS_PAPER_CANCEL_OK=true
```

2. Click `Refresh` in the panel. The Safety Gates section should now
   show both as `OK`.
3. Tick `I authorize PAPER SUBMIT for this run`.
4. Click `2. Paper Submit + Manage`. Confirm the dialog showing the
   exact command preview. Expect:
   - `rc=0` and `outcome_counts.filled=1` in the Output panel
   - `OrderStore` JSONL has a SUBMITTED → FILLED transition
   - no UNKNOWN, no OPEN_OR_PENDING, no CANCEL_REQUESTED

### 6.4 T10 dry-run + real apply

1. After Paper Submit returns rc=0 with FILLED-only outcomes, the
   `Latest report summary` row updates and the T10 buttons become
   enabled.
2. Click `3. T10 Apply Dry Run`. Expect rc=0 + planned mutation
   summary; holdings_log.xlsx is NOT touched.
3. Manually export:

```bash
export AUTOTRADE_T10_APPLY_OK=true
```

4. Click `Refresh`, tick `I authorize T10 REAL APPLY`.
5. Click `4. T10 Apply Real`. Expect:
   - rc=0
   - T10 journal: one `started` and one `applied` marker
   - holdings_log.xlsx History row appended
   - re-running `4. T10 Apply Real` is BLOCKED (rc=2, "applied marker")
     — this is the idempotency check the R8-E suite already enforces.

### 6.5 R10 acceptance DoD per §7

R10 §7 has 13 lines. As of this write-up:

| # | DoD line                                          | Met?    |
|---|---|---|
| 1 | control_panel shows run/artifact/safety gates visually | YES |
| 2 | UI can refresh and select latest awaiting_execution    | YES |
| 3 | UI clearly shows submitted_intents.json state + count  | YES |
| 4 | UI dry-run button works and shows report paths         | YES (R9 + R10 regression) |
| 5 | UI submit button gated by dry-run + env + halt + intents + confirm | YES |
| 6 | UI T10 apply buttons gated by clean submit + confirm   | YES |
| 7 | scripts/run_autotrade_control_panel.command exists, no secrets | YES |
| 8 | Market-open test with one small paper BUY              | **PENDING (next US session)** |
| 9 | No T10 apply on UNKNOWN/open/cancel-unconfirmed        | YES (code path + tests) |
| 10 | T10 apply idempotency verified by re-running           | YES (R8-E + R9-A2 tests) |
| 11 | New R10 tests pass                                     | YES (54 / 54) |
| 12 | Existing R5A-R9 tests pass                             | YES (149 / 149) |
| 13 | progress handoff written                               | YES (this file) |

Items 1-7 and 9-13 are complete. Item 8 requires market hours and is
the only blocker for "R10 fully done".

---

## 7. Failure playbook (R10 §5 condensed)

| Symptom | Allowed actions |
|---|---|
| Dry-run rc != 0 | inspect `autotrade_daily_report.md`, fix the hard_stop reason, repeat. Do not submit. |
| Paper submit ends in UNKNOWN | inspect OrderStore JSONL + last `inquire-ccnl` response. Do not T10 apply. The T10 Apply buttons will stay disabled. |
| Order stays OPEN_OR_PENDING past timeout | let order_manager finish its cancel/reprice loop; if still open, press STOP and write a manual report. |
| T10 apply enters recovery (rc=3) | stop. Inspect `t10_apply_journal.jsonl`, `holdings_log.xlsx`, broker positions. R9-A2 keeps the recovery marker blocking. |
| UI freezes | UI is not the source of truth. Drop to `python3 -m phase3.autotrade.daily_runner ...` in the same shell and inspect the same report files. |

---

## 8. Progress estimate — paper full-auto trajectory

"Paper full auto" = R10 §2.2 *Full Paper Run* button is safe to enable
and a single click executes intent → submit → manage → T10 apply →
post-reconcile → report. Scheduler / launchd is R11+ and is NOT
included in this denominator.

| Capability | R10-end | Notes |
|---|---:|---|
| KIS broker integration                       | 100% | R6/R7-B/R8-cancel live, R8 manage_order paper-tested in fake-broker matrix |
| Safety primitives (env / halt / hard-stops / idempotency) | 100% | R5A/R5B/R8-E/R9-A1/A2/R9-C |
| Order state machine (classify + manage + reprice + cancel-race) | 100% | R8-B/R8-D/R9-A1 |
| Daily-runner real wiring (dry-run + paper-submit + apply) | 100% | R9-B |
| Intent file validation + safe write helper   | 100% | R10-1 |
| Operator dashboard UI                        | 90%  | R10-2 layout complete; per-button "view JSONL" / cancel-confirm visual is R11 |
| Double-click launcher                        | 100% | R10-3 |
| Test coverage                                | 100% | 203/203, all R10 §6 cases by name |
| Market-open paper acceptance                 | 0%   | needs next US session |
| `Full Paper Run` single-button flow          | 0%   | R10 deliberately keeps disabled; enable in R11 after item above |

Weighted progress (backend 50%, UX 25%, acceptance 25%):

```text
Pre-R10 (end of R9):                  ~74%
End of this round (R10 pre-market):   ~85%
After R10 market-open acceptance:     ~92%
After R11 (Full Paper Run + R10 §6     ~98%
  partial-fill handling + operator
  _cleared)
After R11 scheduler/email:           ~100%  (paper-only definition)
```

Live trading is a separate denominator that R12+ addresses.

---

## 9. Suggested next round — R10 acceptance + R11 scope

### R10 finish (market-open)

The next thing the operator should do (when US market opens):

1. follow §6 above
2. on success, append the actual results into this document under a
   "R10 acceptance log" section so we have provenance for the
   `Full Paper Run` enable decision

### R11 (deferred work)

Once R10 acceptance is green, the following items become next-round
priorities, in roughly this order:

1. enable the `Full Paper Run` button (R10 §2.2 says: only after R10
   acceptance)
2. add `operator_cleared` event to OrderStore (was deferred from R9
   §6 and confirmed at the end of R9 progress doc)
3. add a `recommendations.csv → submitted_intents.json` projector so
   the operator no longer has to hand-author intents
4. wire an email-report sender (R10 §8 mentions this)
5. begin launchd dry-run-mode scheduler (R10 §2.5 explicitly forbids
   this in R10)

Live trading is still off-roadmap until R12+ and requires a separate
review pass.
