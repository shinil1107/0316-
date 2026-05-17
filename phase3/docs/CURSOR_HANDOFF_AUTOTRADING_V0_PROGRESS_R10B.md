# Autotrading v0 — R10B Progress (Generate Intent File UI)

**Date**: 2026-05-17 KST
**Spec**: `/Users/shin-il/PyCharmMiscProject_codex/docs/CURSOR_HANDOFF_AUTOTRADING_V0_NEXT_R10B_GENERATE_INTENT_UI.md`
**Project**: `/Users/shin-il/PyCharmMiscProject/0316-`
**Goal**: Close the last R10 pre-market gap — the operator can now
prepare `submitted_intents.json` from the UI by picking one BUY
candidate, instead of hand-editing JSON.

Tests: **225 / 225 OK** (R5A–R10: 203, R10B: 22 new)

---

## 1. What R10B Adds

R10 validated `submitted_intents.json` and blocked paper-submit when
it was missing — but it never built the file. R10B adds the
projector:

```text
<run_dir>/recommendations.csv          (quant engine output)
      │
      │  control_panel "Generate Intent File"
      ▼
<run_dir>/submitted_intents.json       (broker-order instruction)
```

The projector is an **explicit operator action**, not an automatic
step. Generating the file does *not* arm any safety gate and does
*not* call the broker. Paper Submit + Manage still requires:

```text
dry-run rc=0
KIS_ENV=paper
KIS_PAPER_SUBMIT_OK=true
KIS_PAPER_CANCEL_OK=true
global_halt off
operator confirmation checkbox
confirmation dialog
```

---

## 2. New / Changed Files

### 2.1 `phase3/autotrade/intents_io.py`  — projector helpers

Added (kept in the same module so R10's validator and R10B's
projector share one contract):

```python
@dataclass(frozen=True)
class BuyCandidate:
    run_id: str
    rec_row_id: int
    ticker: str
    action: str          # BUY / BUY_NEW / BUY_MORE
    reco_shares: int
    reco_price: float
    rank: Optional[int]
    regime: str
    market: str          # default "NASD"
    actionable: bool
    raw_row: Dict[str, str]   # full CSV row for debugging

def recommendations_csv_path(run_dir: Path) -> Path: ...

def load_buy_candidates(
    run_dir: Path, *,
    buy_actions: Tuple[str, ...] = ("BUY", "BUY_NEW", "BUY_MORE"),
    market: str = "NASD",
) -> List[BuyCandidate]: ...

def build_intent_client_order_id(
    *, run_id: str, rec_row_id: int, ticker: str, qty: int,
) -> str:
    """co-<run_id>-<rec_row_id>-B-<qty>-<ticker> (sanitized, capped 80)."""

def candidate_to_intent_row(
    candidate: BuyCandidate, *,
    qty_override: Optional[int] = None,
    limit_price:  Optional[float] = None,
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]: ...

def write_intent_file_from_candidate(
    run_dir: Path, candidate: BuyCandidate, *,
    qty_override: Optional[int] = None,
    limit_price:  Optional[float] = None,
    overwrite: bool = False,
    run_id: Optional[str] = None,
) -> Path: ...
```

Key contracts:

| Rule | Why |
|---|---|
| **BUY filter is local, not from `RecosAction`** | A future rename of action codes in `exits.RecosAction` cannot silently expand the projector into SELL territory. |
| **Filter: Action ∈ {BUY, BUY_NEW, BUY_MORE} ∧ Actionable=True ∧ Shares>0 ∧ Price>0** | Mirrors R10B §3.3 exactly. SELL_GRACE / HOLD / DEFERRED / zero rows can never appear in the UI dropdown. |
| **`build_intent_client_order_id` is separate from `order_store.build_client_order_id`** | The hashed form is the duplicate-guard key inside OrderStore. The human-readable form is the paper-acceptance audit trail. They coexist on purpose; R11 can wire the duplicate check before write. |
| **Sort by rank ASC then rec_row_id** | Stable ordering so the dashboard dropdown is deterministic across refreshes. |
| **`write_intent_file_from_candidate` delegates to `write_submitted_intents`** | One write path → one validator → one file format. |

### 2.2 `phase3/autotrade/control_panel.py` — UI integration

Three pure-helper changes (all unit-tested):

* `PanelState` gained three new fields, all defaulted so existing
  call-sites keep working:

  ```python
  recommendations_csv_exists: bool = False
  recommendations_buy_count:  int = 0
  buy_candidates: List[intents_io.BuyCandidate] = []
  ```

* `compute_panel_state(...)` now scans `recommendations.csv` for
  the selected run and surfaces the BUY-candidate list.

* `compute_button_gates(...)` accepts a new
  `overwrite_intents_checked: bool = False` and exposes a
  `generate_intent` gate. Disabled-reasons map 1:1 to R10B §3.1:

  | Reason surfaced | Trigger |
  |---|---|
  | `no run_id selected` | `run_id` blank |
  | `artifact status is '<x>', need 'awaiting_execution'` | `run_meta.status` ≠ `awaiting_execution` |
  | `recommendations.csv missing for this run` | CSV absent |
  | `no BUY candidates in recommendations.csv` | CSV present but no eligible rows |
  | `submitted_intents.json already exists (... BUY) — tick 'allow overwrite' to replace it` | File already valid, overwrite box unchecked |
  | `global_halt is ON` | Halt flag set |

* `build_command_preview("generate_intent", ...)` returns a
  description, NOT a shell command — generation happens
  in-process, no subprocess, no broker call. The preview area
  shows:

  ```text
  (writes daily_runs/<run_id>/submitted_intents.json from selected
   BUY candidate; no broker call)
  ```

Tk loop additions (untested, gated behind `launch_panel()` which
is `# pragma: no cover`):

* New **Intent Preparation** section between Safety Gates and
  Actions:

  ```text
  Intent Preparation
    recommendations.csv: exists, BUY candidates=N | missing
    BUY candidate:        [ #75  APA   BUY_MORE  reco_shares=2 price=38.98 rank=4 ▼ ]
    Qty override:         [ 1 ]
    Limit price:          [ 38.98 ]
    [ ] Allow overwrite of existing submitted_intents.json
  ```

  When the operator selects a candidate, the Limit price entry is
  prefilled with the reco price. Both qty and limit remain
  editable up to the press of **0. Generate Intent File**.

* Action row now has 6 buttons:

  ```text
  0. Generate Intent File
  1. Dry Run Preflight / Report
  2. Paper Submit + Manage
  3. T10 Apply Dry Run
  4. T10 Apply Real
  5. Full Paper Run (R11)            (disabled in R10)
  ```

* The Generate handler refuses silently to overwrite — when
  `submitted_intents.json` already exists, the operator MUST
  both tick `Allow overwrite` AND confirm a modal dialog.

* On success, the handler refreshes the dashboard so the
  `submitted_intents.json: missing` blocker on Paper Submit
  disappears and the operator can proceed to dry-run.

---

## 3. Tests

New file `phase3/tests/test_r10b_generate_intents_ui.py` — 22
tests organized into five groups:

| Group | Tests | Covers |
|---|---|---|
| `TestLoadBuyCandidatesFilter` | 5 | BUY-only filter, zero-row drop, Actionable=False drop, missing CSV, rank/rec_row_id sort |
| `TestCandidateToIntentRow` | 6 | qty_override=1, zero-qty reject, zero-limit reject, default reco fallback, deterministic client_order_id, sanitization |
| `TestWriteIntentFile` | 3 | valid output shape, overwrite refusal, explicit overwrite |
| `TestPanelStateAndGenerateGate` | 7 | candidate surfacing in `PanelState`, generate-button enable/disable matrix, post-write blocker clearance |
| `TestGenerateIntentCommandPreview` | 1 | preview is description (no shell, no daily_runner reference) |

Run:

```text
PYTHONPATH=. python3 -m unittest phase3.tests.test_r10b_generate_intents_ui
Ran 22 tests in 0.009s
OK
```

Full regression:

```text
PYTHONPATH=. python3 -m unittest discover -s phase3/tests -p 'test_*.py'
Ran 225 tests in 0.147s
OK
```

---

## 4. Definition of Done — Checklist

R10B §8 said R10B is done when:

| # | Requirement | Status |
|---|---|---|
| 1 | UI has Generate Intent File button | DONE (`_on_generate_intent` in `control_panel.launch_panel`) |
| 2 | UI shows BUY candidates from recommendations.csv | DONE (Combobox repopulated by `_refresh()`) |
| 3 | UI defaults first test to one selected BUY candidate and qty=1 | DONE (qty default "1", dropdown auto-selects first row) |
| 4 | UI writes valid submitted_intents.json through intents_io | DONE (`write_intent_file_from_candidate` → `write_submitted_intents`) |
| 5 | UI refuses overwrite unless operator confirms | DONE (checkbox + modal askokcancel) |
| 6 | UI refreshes and shows intents OK after generation | DONE (handler ends with `_refresh()`) |
| 7 | Paper Submit remains gated by dry-run/env/halt/checkbox | DONE — R10 gates unchanged |
| 8 | No actual broker call during generation | DONE — pure file write |
| 9 | Tests for candidate filtering and file writing pass | DONE — 22 R10B tests |
| 10 | Existing 203 R10 tests still pass | DONE — 225/225 |
| 11 | Cursor writes phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10B.md | DONE — this file |

Deliberate non-scope (deferred to R11):

* Quote-refresh / ask-based limit fill (R10B §3.2 marked optional;
  fail-closed behaviour preserved by leaving limit editable).
* Pre-write OrderStore duplicate guard (R10B §3.5: "R11").
* Armed double-click launcher (`run_autotrade_control_panel_paper_armed.command`)
  — R10B §4 said "only if requested"; user has not requested it.
* Multi-row intent generation. R10B intentionally stays one-row
  (matches "qty=1, one BUY" first paper acceptance plan).

---

## 5. Updated Pre-Market Checklist

After R10B, the runbook from R10 changes only at step 3:

```text
1.  Launch UI via scripts/run_autotrade_control_panel.command
2.  Use latest awaiting_execution
3.  Generate Intent File for exactly one BUY candidate (qty=1)
4.  Confirm submitted_intents.json: OK (intents=1 buy=1)
5.  Run Dry Run Preflight / Report
6.  Confirm dry-run rc=0 and no hard_stop
7.  Confirm KIS_ENV=paper
8.  Confirm global_halt=False
9.  Confirm T10 journal clean (no started/recovery markers)
10. At market open, arm paper submit/cancel gates in terminal:
        export KIS_PAPER_SUBMIT_OK=true
        export KIS_PAPER_CANCEL_OK=true
    (or use a future armed launcher)
11. Refresh UI
12. (If price drifted) regenerate intent with Allow-overwrite ticked
13. Re-run Dry Run after price update
14. Tick "I authorize PAPER SUBMIT"
15. Press Paper Submit + Manage
16. Verify report shows FILLED + no blockers
17. Press T10 Apply Dry Run
18. Only after reviewing dry-run, set AUTOTRADE_T10_APPLY_OK=true
19. Tick "I authorize T10 REAL APPLY"
20. Press T10 Apply Real
21. Re-run T10 Apply Real to confirm duplicate apply is blocked
```

If the order does not FILL, do NOT T10-apply unless local state
is unchanged (R10B §7).

---

## 6. Are We Done Before Market Open?

**Yes**, for the first paper acceptance test (qty=1, one BUY,
LIMIT). The pre-market deliverables that R8–R10B set out are all
in place:

* R8 — cancel path classification + odno normalization + 124 tests
* R9 — cancel-race fill safety + T10 recovery re-block + global
  halt + minimal control panel + 25 tests
* R10 — operator dashboard + intent file validation + macOS
  launcher + 54 tests
* R10B — recommendations.csv → submitted_intents.json projector +
  22 tests

Only step 12 ("update limit after open") is by design a human
decision, not code.

Caveat from R10B §6:

> Do not paper-submit from a stale Friday price without
> reviewing the limit price after the market opens.

So the safest plan stays: **generate intent before open with a
conservative limit, dry-run, then refresh the limit after open and
regenerate before pressing Paper Submit.**

---

## 7. Returning to ML Performance Improvement

The autotrade path is functionally complete for paper-acceptance.
Suggested re-entry to the ML axis:

* Continue from the last ML checkpoint (the phase3 score/regime
  pipeline that produced this `recommendations.csv`).
* Autotrade work parked at R11 backlog:
  - quote-refresh in Intent Preparation
  - pre-write OrderStore duplicate guard
  - PARTIALLY_FILLED T10 operator-handling
  - armed double-click launcher
  - Full Paper Run (R11 §5.5 single-button)
* When the next paper acceptance test happens, re-enter via
  `scripts/run_autotrade_control_panel.command`; nothing else has
  to change.

---

## 8. File Inventory Diff (R10 → R10B)

```text
phase3/autotrade/intents_io.py            +~180 lines  (BuyCandidate + 4 helpers)
phase3/autotrade/control_panel.py         +~140 lines  (Intent Prep section + handler + state surfacing)
phase3/tests/test_r10b_generate_intents_ui.py        NEW (22 tests, ~430 lines)
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10B.md   NEW (this file)
```

No changes to: `daily_runner.py`, `order_manager.py`, `t10_applicator.py`,
`global_halt.py`, `kis_broker_adapter.py`, `reconcile.py`,
`scripts/run_autotrade_control_panel.command`.

---
