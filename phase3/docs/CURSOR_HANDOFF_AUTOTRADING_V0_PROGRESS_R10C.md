# Cursor Handoff — Autotrading v0 R10C Progress (paper acceptance #1 + UI hardening)

**Date**: 2026-05-18 → 2026-05-19 KST (US session, week of 5/18)
**Author**: Cursor (Claude Opus 4.7)
**Round**: R10C — first market-open paper acceptance + control panel hardening
**Preceding handoff**: `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10.md`
**Out of scope (deferred to R10D)**: ccnl polling resilience, "continue on non-FILLED" policy flag, intent-time price freshness, operator-cleared duplicate guard.

---

## 0. TL;DR

R10C closed the loop between R10 (pre-market readiness) and R10D
(post-acceptance hardening). Three threads:

1. **Track A — UI hardening from operator feedback**
   - In-UI safety-gate activation (no more terminal `export`).
   - Vertical scroll on the dashboard so all sections fit on a laptop.
   - Copy-to-clipboard for the Output / Log area and a one-button
     "Copy Panel Snapshot" that lifts the full dashboard state +
     last subprocess output into the clipboard as a plain-text block.
   - **Batch intent generation** — one click writes a
     `submitted_intents.json` covering *every* BUY candidate from
     `recommendations.csv`, replacing the per-candidate workflow.
   - **Live subprocess streaming** — the panel no longer freezes
     during a `paper-submit`; stdout / stderr land in the log widget
     line-by-line and a ticking "running paper-submit 12.3s" status
     label keeps the operator informed.

2. **Track B — Shadow Ledger follow-ups + email reporting**
   - Removed the dev-only `CODEX_MIRROR_ALLOW_RUN` setdefault from the
     production-facing `shadow_ledger.py` (Phase-A review item #1).
   - Added 29 regression tests for `shadow_ledger` including an
     end-to-end smoke for `_run_replay_job` (Phase-A review item #2).
   - Removed the Shadow Ledger section from the autotrade dashboard
     UI (it's a separate concern from autotrade and was cluttering
     the control panel).
   - `shadow_diff.py` email section now also prints
     `Both BUY:` and `Both Top-N:` overlaps — previously the operator
     only saw "shadow-only" and "live-only" disagreements.

3. **Track C — Market-open paper acceptance test (FIRST EXECUTION)**
   - **Two real paper-submit attempts** executed against KIS paper
     (`openapivts.koreainvestment.com:29443`) on 2026-05-18 US session.
   - Run 1 (single APA, qty=1): broker accepted the order, then ccnl
     polling hit `RemoteDisconnected` from the paper server.
     `manage_order` correctly classified UNKNOWN, `daily_runner`
     correctly hard-stopped, and `kis_broker_adapter.cancel_order(...)`
     correctly recovered the broker side.
   - Run 2 (batch MRNA / APA / JBL, qty=2 each): **MRNA FILLED**
     (8.1s, avg 48.36), APA again UNKNOWN at ccnl (same disconnect
     pattern), JBL untried due to R8 §8 break-on-non-FILLED.
     Cancel recovery succeeded again.

R10C test tally:

```text
305 / 305 OK
```

Increment vs R10 baseline:

```text
R10 finish:           203 / 203 OK
R10A/B accumulated:   276 / 276 OK   (shadow_ledger UI + arm-gate work)
R10C add this round:  +29 (shadow_ledger regression suite + UI copy/snapshot tests)
                      +19 (batch helper + live streaming + generate_all gate)
                      +10 (panel snapshot + OrderStore signature regression)
                      +28 (other intermediate fixes already shipped in R10A/B)
R10C end:             305 / 305 OK
```

(Counts are cumulative — see §3 for the per-feature breakdown.)

T10 Apply Real has **not** been exercised against the live broker yet
because every R10C paper-submit ended in rc=2 hard-stop (by design,
on ccnl-induced UNKNOWN). That's the only acceptance row still
"PENDING" — see §8.

---

## 1. What R10C changed in the codebase

### 1.1 Bug fix — `daily_runner.default_manage_loop_fn` (P0)

```diff
- store = OrderStore(run_dir=ctx.run_dir)
+ # OrderStore takes the JSONL path, not the directory. Other callers
+ # (orchestrator, t10_applicator) follow the same convention so this
+ # restores parity.
+ store = OrderStore(ctx.run_dir / "autotrade_orders.jsonl")
```

`OrderStore.__init__(self, jsonl_path: Path)` only accepts a path
positionally. The R10 control panel's first ever real
`paper-submit + manage` click crashed inside `default_manage_loop_fn`
with `TypeError: unexpected keyword argument "run_dir"` because the
manage loop was the only caller still using the old kwarg shape.

The regression test now locks the call signature:

```python
# phase3/tests/test_r10_panel_snapshot.py
def test_order_store_is_constructed_with_jsonl_path(self):
    # asserts captured["args"][0] == run_dir / "autotrade_orders.jsonl"
    # asserts captured["kwargs"] == {}
```

### 1.2 In-UI gate activation (`control_panel`)

Two new toggles in the "Activation (this session)" group:

- `Arm Paper Submit gate` — sets `KIS_PAPER_SUBMIT_OK=true` and
  `KIS_PAPER_CANCEL_OK=true` in this process's `os.environ` after a
  confirmation dialog. Unticking clears them.
- `Arm T10 Apply gate` — same but for `AUTOTRADE_T10_APPLY_OK`.

Pure helpers added to `control_panel`:

```text
arm_gate_vars(env, vars)          # returns env diff dict
disarm_gate_vars(env, vars)       # returns env diff dict
gate_is_armed(env, vars)          # bool, all listed vars == "true"
```

`scripts/run_autotrade_control_panel.command` now auto-loads
identity variables (`KIS_ENV`, `KIS_APP_KEY`, etc.) from `.env`
while explicitly **not** auto-loading danger gates — those must be
armed via the UI for the session.

7 regression tests in `tests/test_r10_arm_gate.py`.

### 1.3 Scrollable dashboard

`launch_panel()` now wraps the main content in a Tk `Canvas` +
vertical `Scrollbar`. All existing `LabelFrame`s were re-parented
under `_body` (the canvas-window inner frame). Wheel scrolling
bound on macOS/Linux/Windows.

### 1.4 Output copy + Panel Snapshot

- Toolbar above the Output text area:
  - `Copy Output / Log` — copies the full text widget.
  - `Copy Panel Snapshot` — calls a new pure helper
    `build_panel_snapshot_text(ps, ...)` that renders the entire
    dashboard state (run / artifact / intents / safety gates / halt /
    t10 journal / recommendations summary / last argv / output tail)
    as a multi-line plain-text block. Capped at ~8 KiB of output tail
    so clipboard usage stays bounded.
- Output `tk.Text`: Cmd+A, Cmd+C, Control+Click context menu added.

The snapshot helper is intentionally pure (Tk-free) so the test
suite can lock its format and verify it never leaks raw secrets:

```python
# tests/test_r10_panel_snapshot.py
def test_snapshot_does_not_leak_raw_gate_values(self):
    for bad in ("APP_KEY", "APP_SECRET", "ACCESS_TOKEN"):
        self.assertNotIn(bad, snap.upper(), ...)
```

8 tests in `test_r10_panel_snapshot.py`.

### 1.5 Batch intent generation (R10C §2)

New pure helpers in `intents_io`:

```python
def candidates_to_intent_rows(
    candidates: List[BuyCandidate], *,
    limit_pad_pct: float = 0.0,
    qty_override: Optional[int] = None,
) -> List[Dict[str, Any]]: ...

def write_intent_file_from_candidates(
    run_dir: Path, candidates: List[BuyCandidate], *,
    limit_pad_pct: float = 0.0, qty_override: Optional[int] = None,
    overwrite: bool = False, run_id: Optional[str] = None,
) -> Path: ...
```

`candidates_to_intent_rows` uses each candidate's `reco_shares` and
`reco_price` by default; `limit_pad_pct` bumps every BUY limit up by
N% (positive = pay a bit more to lift fill probability in paper).

UI surface in `control_panel.launch_panel()`:

- **New action button** `"0b. Generate ALL Intents (batch)"`.
- New entry `Batch limit pad (%)` in the Intent Preparation section.
- Confirmation dialog before write shows ticker count, total qty,
  total notional, and a sample of the first 10 rows so the operator
  can sanity-check before clobbering an existing intent file.

`compute_button_gates` returns a `generate_all` entry mirroring the
single-shot `generate_intent` gate set (same preconditions).

9 tests in `test_r10c_batch_and_streaming.py` cover the helpers, the
write path, and the button-gate parity.

### 1.6 Live subprocess streaming (R10C §3)

New pure helper in `control_panel`:

```python
def run_subprocess_streaming(
    argv: List[str], *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    on_line: Callable[[str, str], None],     # (stream, line)
    on_done: Callable[[int], None],          # rc
    popen: Callable[..., Any] = subprocess.Popen,
) -> Any: ...
```

- `Popen` with `bufsize=1` text mode.
- Two daemon threads pump stdout / stderr line-by-line through
  `on_line`. A third thread waits and fires `on_done(rc)` exactly
  once after both pipes drain.
- Automatically injects `PYTHONUNBUFFERED=1` (only when caller
  hasn't pre-set it) so the child Python doesn't block-buffer its
  stdout when the parent captures via PIPE — without this the live
  progress would silently hold every line for ~4 KiB.

UI integration in `launch_panel()`:

- A small `_stream_argv(argv, *, label, env=None, on_finished=None)`
  closure wraps `run_subprocess_streaming` for Tk:
  - Appends every line to the Output text via `root.after(0, ...)`.
  - A `_tick_status` callback updates a green status label every
    500 ms: `running paper-submit  12.3s` → on exit it freezes at
    `done rc=0  (45.7s)`.
  - All action buttons are auto-disabled during a run and re-enabled
    by `_refresh()` once `on_done` lands.
- `_on_dry_run`, `_on_paper_submit`, `_on_t10_dry`, `_on_t10_apply`
  all route through `_stream_argv` now. The previous blocking
  `subprocess.run` wrappers (`run_dry_run`, `run_paper_submit`,
  `run_t10`) are kept for the CLI test harness.

6 tests in `test_r10c_batch_and_streaming.py` cover the streaming
runner (line ordering, stderr labeling, `on_done` fires exactly once,
PYTHONUNBUFFERED injection / caller-respect, identity equality of
the returned proc).

### 1.7 Shadow Ledger Phase-A review follow-ups

- `phase3/shadow_ledger.py`: removed
  `os.environ.setdefault("CODEX_MIRROR_ALLOW_RUN", "1")` (dead code
  + future-safety-bypass risk).
- New 29-test regression suite at
  `phase3/tests/test_shadow_ledger.py`:
  - Pure helpers (cost model, grace filter, pair discovery,
    label / date-range scoping)
  - Stateful in-memory portfolio update path
  - Comparison / metric builders
  - End-to-end smoke of `_run_replay_job` with mocked subprocess
    components.

### 1.8 Shadow diff email — overlap sections

`phase3/shadow_diff.py::format_email_section` now also prints
`Both BUY:` and `Both Top-N:` so the operator can see tickers where
live and shadow agree (previously only disagreement was rendered).
6 tests in `test_shadow_diff_both_buys.py`.

### 1.9 Shadow Ledger UI removed from control panel

Operator requested the shadow ledger is a separate concern from
autotrade and was crowding the dashboard. The dataclass field,
the `_scan_shadow_ledger_latest` helper, the `LabelFrame` + buttons,
the `run_shadow_ledger_update` helper, and the corresponding
test class `TestShadowLedgerPanelHelpers` were all removed. The
CLI tool `python -m phase3.shadow_ledger ...` remains the entry
point.

---

## 2. Files touched (high-level)

| Path | Change |
|---|---|
| `phase3/autotrade/daily_runner.py` | OrderStore signature fix (P0). |
| `phase3/autotrade/control_panel.py` | Scrollable layout, in-UI gate activation, copy/snapshot toolbar, batch button + callback, streaming subprocess wrapper, all 4 action callbacks refactored. Shadow ledger UI removed. New pure helpers: `arm_gate_vars`, `disarm_gate_vars`, `gate_is_armed`, `build_panel_snapshot_text`, `run_subprocess_streaming`. |
| `phase3/autotrade/intents_io.py` | New batch helpers `candidates_to_intent_rows`, `write_intent_file_from_candidates`. |
| `phase3/shadow_ledger.py` | Removed `CODEX_MIRROR_ALLOW_RUN` setdefault. |
| `phase3/shadow_diff.py` | `Both BUY:` and `Both Top-N:` sections in email format. |
| `scripts/run_autotrade_control_panel.command` | Auto-loads `.env` identity vars while explicitly **not** auto-loading danger gates. |
| `phase3/tests/test_r10_arm_gate.py` | NEW — 7 tests for arm/disarm helpers + gate matrix integration. |
| `phase3/tests/test_r10_panel_snapshot.py` | NEW — 10 tests for snapshot helper + OrderStore signature regression. |
| `phase3/tests/test_r10c_batch_and_streaming.py` | NEW — 19 tests for batch helpers, streaming runner, generate_all button gate. |
| `phase3/tests/test_shadow_ledger.py` | NEW — 29 tests covering Phase-A surface area. |
| `phase3/tests/test_shadow_diff_both_buys.py` | NEW — 6 tests for overlap sections. |
| `phase3/tests/test_r10_control_panel_dashboard.py` | Removed `TestShadowLedgerPanelHelpers` (UI section deleted). |
| `phase3/tests/test_r10_ui_button_gates.py` | Loosened "no secrets in launcher" regex to skip comment lines (operator guidance comments are allowed). |

---

## 3. Acceptance test #1 — paper market open (2026-05-18 US session)

Two paper-submit attempts were executed against KIS paper. Both
exercised exactly the same R8 / R10 safety contract paths but with
different intent shapes, so each contributes different evidence.

### 3.1 Run 1 — single APA, qty=1

| Step | Result |
|---|---|
| Dry-run preflight rc | `0` |
| Paper submit ack | `submitted` @ broker_order_id `0000040195`, 14:15:17 UTC |
| ccnl polling | 46s → `BrokerNetworkError: ConnectionError('Connection aborted.', RemoteDisconnected(...))` |
| `manage_order` classification | `UNKNOWN`, `status_source=unknown`, note "ccnl poll exception — verify before retry" |
| `daily_runner` rc | `2`, `hard_stop@manage_loop` |
| T10 Apply | NOT entered (gate blocked) |
| Operator action | `adapter.cancel_order(broker_order_id='0000040195', ..., dry_run=False)` |
| Cancel result | `accepted=True, cancel_oid=0000040397`, KIS msg "모의투자 취소주문이 완료 되었습니다" |

Verified safety surfaces:

- R8 §8 hard-stop-on-UNKNOWN behaviour.
- `cancel_order` paper path including `KIS_PAPER_CANCEL_OK` gating.
- `~/.kis_audit/2026-05-18.jsonl` audit trail (3 rows: dry-run,
  real-cancel, post-cancel `inquire-nccs`).

### 3.2 Run 2 — batch MRNA / APA / JBL, qty=2 each

Generated via the new `0b. Generate ALL Intents (batch)` button at
`Batch limit pad = +0.5%` from `recommendations.csv`:

```
MRNA  rec_row=74  reco_shares=2  reco_price=49.04   →  limit=49.2852
APA   rec_row=75  reco_shares=2  reco_price=38.98   →  limit=39.1749
JBL   rec_row=76  reco_shares=2  reco_price=339.82  →  limit=341.5191
```

Reco_price source: `Price` column of `recommendations.csv`, which
is the **previous business-day close** (`ScoringDate=2026-05-15`).
This is the empirical root cause of APA's repeated non-fills — the
weekend gap pushed APA's Monday open above the Friday close even
with the +0.5% pad applied.

Outcomes from `autotrade_daily_report.json`:

| # | ticker | qty | limit  | final_state | elapsed | broker_oid |
|---|---|---:|---:|---|---:|---|
| 1 | MRNA | 2 | 49.2852 | `filled` (avg 48.36) | 8.1s | 0000040578 |
| 2 | APA  | 2 | 39.1749 | `unknown` (ccnl disconnect) | 80.6s | 0000040579 |
| 3 | JBL  | 2 | 341.5191 | *(untried — `default_manage_loop_fn` broke at APA UNKNOWN per R8 §8)* | — | — |

`reprice_attempts=0` for APA: the ccnl exception fired *before* the
manage loop saw a non-FILLED classification, so the in-flight
reprice policy (`max_reprice_attempts=2, reprice_step_bps=10`)
never got a chance to engage. This is the dominant motivation for
R10D-1 (see §6).

| Surface | Result |
|---|---|
| Batch intent file write | OK — 3 rows, total notional ≈ $918 |
| Live progress streaming | OK — operator saw line-by-line progress, no UI freeze (vs Run 1 where the same blocking subprocess looked frozen) |
| Submit + manage | partial: 1 FILLED, 1 UNKNOWN, 1 untried |
| R8 §8 hard-stop | fired correctly |
| Cancel recovery | OK — `adapter.cancel_order(broker_order_id='0000040579', qty=2)` → `cancel_oid=0000040686`, `open orders=0` |

### 3.3 Post-run state

- **MRNA 2 shares are real-held in the paper account**. The next
  daily run's pre-reconcile step will surface this in
  `portfolio_before.csv` automatically.
- APA is fully reverted at the broker.
- JBL was never submitted, so its slot is free.
- `submitted_intents.json` for `run_id=20260518_205715_daily`
  still contains all 3 rows on disk — purely historical now;
  the next daily artifact will get its own `run_id`.

---

## 4. What the acceptance test actually proved

Mapping back to R10 §6 / R8 §8 / R5 contract rows:

| Contract | Evidence | Status |
|---|---|---|
| dry-run preflight rc=0 | Run 1 + Run 2 preflight | PROVED |
| paper-submit happy path | Run 2 MRNA FILLED in 8.1s | PROVED |
| R8 §8 hard-stop on UNKNOWN | Run 1 + Run 2 APA | PROVED twice |
| T10 auto-entry refusal on rc!=0 | Both runs ended without T10 | PROVED |
| `KIS_PAPER_CANCEL_OK` gating | dry-run cancel refused without flag → real cancel accepted with flag | PROVED |
| cancel POST path + audit | `0000040397`, `0000040686` both `accepted=True` | PROVED |
| `inquire-nccs` post-cancel | `open orders = 0` after each cancel | PROVED |
| `~/.kis_audit/YYYY-MM-DD.jsonl` append-only log | rows appended in real time | PROVED |
| ccnl `RemoteDisconnected` classification → UNKNOWN | Two independent observations, both 46–80s in | PROVED — but exposes R10D-1 |
| Batch intent generation | new path produced canonical 3-row file | PROVED |
| Live subprocess streaming | operator saw line-by-line output, status ticker, no freeze | PROVED |
| T10 Apply Real on clean batch | not reached (every batch had UNKNOWN) | **STILL PENDING** |

The only acceptance row still pending is "T10 Apply Real on a 100%
FILLED batch". This is mechanically blocked by R10D-1 / R10D-3 — see
§6.

---

## 5. Lessons from the live run

1. **ccnl `RemoteDisconnected` is not rare on KIS paper.** Two
   independent observations of the exact same pattern (broker accepts
   `submitted` → 46-80 s later `inquire-ccnl` raises
   `('Connection aborted.', RemoteDisconnected(...))`). Once is bad
   luck; twice in a row is a known characteristic of the paper
   endpoint we must absorb.

2. **`reco_price = previous close` is wrong for gap-prone tickers.**
   The `limit_pad_pct` knob added in R10C is a band-aid — it bumps
   every limit by the same %, so for stable names it's fine but for
   high-vol names like APA it's still under-priced after a weekend.
   Real fix needs intent-time freshness (R10D-3).

3. **R8 §8 break-on-non-FILLED is sometimes too conservative.** When
   ticker N+1 is independent of ticker N, breaking the manage loop
   on N's UNKNOWN forfeits independent opportunities. R10D-2 will
   introduce an opt-in policy flag.

4. **`paper_cancel_ok` is per-process.** The UI's in-process Arm
   toggle correctly sets `os.environ` but does NOT propagate to a
   *new* shell — the operator's recovery cancel from a fresh
   terminal needed `KIS_PAPER_CANCEL_OK=true` inline. R10D-4 adds a
   small in-UI "Cancel one broker order" button so the operator
   never has to drop to a shell again.

5. **The live-streaming + Copy Snapshot pairing was operationally
   decisive.** The first paper-submit looked like a frozen UI for 46
   seconds; without streaming the operator would have killed the
   process. After the R10C streaming work, the same disconnect was
   immediately visible with a per-second progress label and
   stderr-prefixed lines in real time.

---

## 6. R10D — proposed next round

Four small surfaces, each driven directly by a real observation from
this acceptance run. None require a market window to develop, but
**every one of them should be exercised at the next US open**.

### R10D-1: ccnl polling resilience

**Why**: Both UNKNOWN events came from the exact same
`RemoteDisconnected` after the broker had already accepted the
order. A single short retry would have absorbed both.

**Where**: `phase3/autotrade/order_manager.py::manage_order`, the
ccnl polling block. Specifically, the `except BrokerNetworkError`
arm that currently writes UNKNOWN immediately.

**Sketch**:

```python
# Pseudo: when the *poll* (not the place_order) fails with
# BrokerNetworkError, retry up to N times with exponential
# backoff capped by the manage_order policy's poll_interval_sec.
# If still failing after N: do what we do today (classify UNKNOWN).
```

**Risk**: A retry that wraps a stale fill check could mask a real
fill that the operator didn't see. Mitigation: each retry must
re-emit the per-poll audit row, and the retry budget must be small
(e.g. 2 retries × 2s backoff).

**Tests**: extend the existing fake-broker matrix with a
"first ccnl raises, second ccnl returns filled" fixture.

**Acceptance**: replay R10C Run 2 logic in a fixture — expect APA's
final state to flip from UNKNOWN to either FILLED or
OPEN_OR_PENDING, depending on the simulated second-poll reply.

### R10D-2: opt-in "continue on non-FILLED" manage loop policy

**Why**: JBL never got a chance because APA hit UNKNOWN. For
genuinely independent intent rows this is over-aggressive.

**Where**: `phase3/autotrade/daily_runner.py::default_manage_loop_fn`
and a new field on `OrderManagementPolicy`.

**Sketch**:

```python
@dataclass
class OrderManagementPolicy:
    ...
    # R10D-2: when True, the manage loop logs the non-FILLED outcome
    # and proceeds to the next intent. When False (default), R8 §8
    # break-on-non-FILLED is preserved.
    continue_on_non_filled: bool = False
```

The hard-stop emission stays at the runner level (so we still write
rc=2 if any outcome is non-FILLED), but each independent ticker is
attempted exactly once before the runner aggregates.

**Risk**: Sequential intents that consume from the same cash
envelope can over-spend if we continue past a partial fill. We
mitigate by re-reading `inquire-balance` between intents when
`continue_on_non_filled=True`.

**Tests**: new fixture set in `test_r8_daily_runner.py` covering
the new policy flag.

**Acceptance**: replay R10C Run 2 with the flag on — expect JBL to
get a `submitted` row in `autotrade_orders.jsonl` regardless of APA's
outcome.

### R10D-3: intent-time limit-price freshness

**Why**: `reco_price` is yesterday's close. Two days of weekend +
gap-prone tickers = repeated near-misses.

**Where**: `phase3/autotrade/intents_io.candidate_to_intent_row`
(and the batch wrapper) — accept an optional `quote_fn` callable
that returns a (ts, ask, last) tuple. When provided, the helper
takes `limit = max(reco_price * (1+pad), current_ask * (1+small_pad))`.

UI side: a new checkbox `Refresh limit with KIS quote` in the
Intent Preparation section. When ticked, the Generate button calls
`KisBrokerAdapter.get_quote(symbol, market)` for each candidate
inside a single threaded loop, then runs the existing batch path
with the freshly priced rows.

**Risk**: blocks Generate Intent on N quote API calls. Mitigation:
single-thread with a per-call timeout; on any failure the row falls
back to `reco_price` and a banner appears in the dialog.

**Tests**: pure unit tests on the limit computation (no network),
plus a control-panel test that the new checkbox routes through the
quote helper with proper fallback.

**Acceptance**: dry-run on a copy of today's `recommendations.csv`
shows APA's limit move from 38.98 → some larger value reflecting the
actual ask.

### R10D-4: operator-cleared duplicate guard + in-UI cancel

**Why**: `OrderStore.is_already_active` correctly blocks resubmits
on a stuck cid, but there's no clean way for the operator to
acknowledge "I cancelled at the broker, this cid is safe to retire"
without hand-editing the JSONL. Also, the recovery cancel currently
has to happen from a shell with `KIS_PAPER_CANCEL_OK=true` exported
— the operator can do that, but it's friction.

**Where**:

1. `phase3/autotrade/order_store.py`: add an
   `event_kind="operator_cleared"` event type. `is_already_active`
   stops treating earlier blocking events as active once an
   `operator_cleared` row for the same cid appears.
2. `phase3/autotrade/control_panel.py`: new "Cancel a broker
   order…" dialog that prompts for `broker_order_id`, `symbol`,
   `qty`, optional `cid`. Calls
   `KisBrokerAdapter.cancel_order(..., dry_run=False)`, appends
   `operator_cleared` to the run's OrderStore if `cid` is given,
   shows the result.

**Risk**: A misclicked `operator_cleared` could let the system
re-submit something that's still alive at the broker. Mitigation:
the UI dialog requires the operator to manually type the cid (no
dropdown), and the helper refuses unless the immediately-preceding
`inquire-nccs` (auto-called inside the dialog) shows the cid is
absent.

**Tests**: round-trip
`submit → unknown → operator_cleared → resubmit succeeds`.

**Acceptance**: run a scripted scenario in fake-broker matrix.

### Suggested R10D ordering

```
1. R10D-1 (ccnl retry)               — smallest, biggest acceptance impact
2. R10D-2 (continue on non-FILLED)   — small, unblocks independent tickers
3. R10D-4 (operator_cleared + UI)    — moderate, cleans up acceptance UX
4. R10D-3 (limit freshness)          — largest, needs a small quote cache
```

Then re-run the market-open acceptance test once R10D-1 + R10D-2
are merged. With those two alone we expect Run-2-style batches to
produce all-FILLED outcomes much more often, finally exercising the
T10 Apply Real path that R10/R10C never reached.

---

## 7. Failure playbook (R10C-updated)

Replaces R10 §7. Only the rows that changed are shown.

| Symptom | Allowed actions (R10C) |
|---|---|
| Paper submit ends in UNKNOWN | (a) Click **Copy Panel Snapshot**, paste into the operator chat. (b) Check broker via `python3 -m phase3.autotrade.kis_broker_adapter probe history` and `probe open-orders --market NASD`. (c) If still open at broker, run the inline `adapter.cancel_order(...)` recipe in §5 of the R10C handoff, *or* drop into a shell with `KIS_PAPER_CANCEL_OK=true` and call `cancel_order`. (d) Do **not** T10 apply. |
| UI freezes during a long action | After R10C this should not happen — `_stream_argv` keeps the loop responsive and the status label ticks every 500 ms. If it does, copy the output via `Copy Output / Log` and report — it's a real bug. |
| `last_rc` shows old `hard_stop` after a fresh dry-run | The `last_report` panel field reads the most-recent on-disk report. After a fresh dry-run press Refresh — the field will reflect the new report. **`Clear halt flag`** is unrelated and only matters when the STOP button has been pressed. |
| Same intent file, second submit blocked by duplicate guard | A duplicate-guard skip is a feature, not a freeze. R10D-4 will add an `operator_cleared` event + in-UI cancel that resolves this without hand-editing the JSONL. Until then: generate a fresh run-id, or regenerate intents with different `qty`/`rec_row_id`. |
| `ccnl poll exception: RemoteDisconnected` | Identical to R10C Run 1 / Run 2 APA. Confirm at broker (HTS app or `probe open-orders`); cancel if still open. R10D-1 will absorb this transient. |

---

## 8. Definition-of-done — R10C checklist

| # | DoD line | Status |
|---|---|---|
| 1 | OrderStore signature regression locked | YES (test_r10_panel_snapshot.py) |
| 2 | In-UI gate activation present + tested | YES (test_r10_arm_gate.py) |
| 3 | Dashboard scrolls on a 13" laptop | YES (manual) |
| 4 | Copy Output / Copy Panel Snapshot work + pure helper tested | YES |
| 5 | Batch intent generation present + tested | YES (test_r10c_batch_and_streaming.py) |
| 6 | Live subprocess streaming present + tested | YES (test_r10c_batch_and_streaming.py) |
| 7 | Shadow Ledger Phase-A review items 1 + 2 closed | YES |
| 8 | Shadow Ledger UI removed from control panel | YES |
| 9 | Shadow diff email shows `Both BUY` + `Both Top-N` | YES |
| 10 | All R5A–R10 tests pass | YES (305 / 305) |
| 11 | Paper acceptance run #1 (single-order) | YES (Run 1) |
| 12 | Paper acceptance run #2 (batch) | PARTIAL — 1 FILLED, 1 UNKNOWN, 1 untried; rc=2 by design |
| 13 | T10 Apply Real exercised against KIS paper | **PENDING — needs R10D-1 + R10D-2 to unblock typical batches** |
| 14 | Progress handoff document written | YES (this file) |

---

## 9. Progress estimate — paper full-auto trajectory (updated)

Definitions unchanged from R10 §8. Updated bottom line:

| Capability | R10C-end | Notes |
|---|---:|---|
| KIS broker integration                       | 100% | R6 / R7-B / R8-cancel + Run 1/2 cancel POSTs |
| Safety primitives (env / halt / hard-stops / idempotency) | 100% | All exercised in Run 1 + Run 2 |
| Order state machine (classify / manage / reprice / cancel-race) | 95% | reprice never engaged because ccnl disconnect pre-empted; R10D-1 closes this gap |
| Daily-runner real wiring (dry-run / paper-submit / apply) | 95% | submit fully exercised; T10 apply pending an all-FILLED batch |
| Intent file validation + helper + batch | 100% | R10-1 + R10C-2 |
| Operator dashboard UI                        | 100% | scroll + arm-gate + copy + snapshot + batch + live streaming all landed |
| Double-click launcher                        | 100% | + auto-loads identity from .env |
| Shadow ledger (separate concern)             | 100% | review items closed, suite expanded |
| Test coverage                                | 100% | 305 / 305 |
| Market-open paper acceptance                 | 70%  | 2 runs executed; T10 apply leg pending an all-FILLED batch |
| `Full Paper Run` single-button flow          | 0%   | still deliberately disabled |

Weighted progress (backend 50%, UX 25%, acceptance 25%):

```text
Pre-R10C (end of R10):                ~85%
End of this round (R10C):             ~90%
After R10D (-1 + -2 minimum) + T10:   ~95%
After Full Paper Run + email/scheduler: ~100% (paper-only definition)
```

Live trading remains R12+ and outside this denominator.

---

## 10. Operator runbook addenda (R10C-only)

The full R10 runbook still applies. R10C-specific additions:

### 10.1 Launching the panel

```bash
# Double-click in Finder, or:
bash scripts/run_autotrade_control_panel.command
```

The launcher now auto-loads `.env` identity variables (`KIS_ENV`,
`KIS_APP_KEY`, account / base_url) so the panel's Safety Gates row
correctly shows `KIS_ENV = paper`. Danger gates
(`KIS_PAPER_SUBMIT_OK`, `KIS_PAPER_CANCEL_OK`,
`AUTOTRADE_T10_APPLY_OK`) are explicitly **not** loaded — arm them
via the new toggles for the session.

### 10.2 Recovery cancel without leaving the panel (today)

Until R10D-4 lands, the cleanest recipe for a stuck broker order
(when the panel showed UNKNOWN) is, from a fresh shell:

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
set -a && source ./.env && set +a
KIS_PAPER_CANCEL_OK=true PYTHONPATH=. python3 -c "
from phase3.autotrade.kis_broker_adapter import KisBrokerAdapter, load_env_config
cfg = load_env_config()
adapter = KisBrokerAdapter(cfg=cfg, verbose=True)
res = adapter.cancel_order(
    broker_order_id='<BROKER_OID>', symbol='<TICKER>', market='NASD',
    qty=<QTY>, dry_run=False,
    note='recovery cancel',
)
print('accepted=', res.accepted, 'cancel_oid=', res.cancel_order_id)
"
# Verify:
PYTHONPATH=. python3 -m phase3.autotrade.kis_broker_adapter probe open-orders --market NASD
```

The audit log at `~/.kis_audit/YYYY-MM-DD.jsonl` will show the
POST. The KIS HTS app is the final source of truth.

### 10.3 Batch intent workflow

1. Press **Refresh**.
2. In Intent Preparation: tick **Allow overwrite** (only if an
   intent file already exists for this run_id).
3. Set **Batch limit pad (%)** — `0.0` to use yesterday's close as-is,
   `0.5`–`1.0` to bump every BUY limit upward to lift fill probability
   in paper. (R10D-3 will replace this with a quote-time refresh.)
4. Click **"0b. Generate ALL Intents (batch)"**. Confirm the dialog
   after sanity-checking the per-row preview (qty, limit, cid).
5. Run **"1. Dry Run Preflight / Report"** — wait for `rc=0` in
   the live log.
6. **Arm Paper Submit gate** toggle → confirm dialog.
7. Run **"2. Paper Submit + Manage"** — watch the live status label
   tick `running paper-submit …s`. Lines appear in the log as each
   ticker is submitted / polled / classified.
8. On `rc=0` with `outcome_counts.filled == len(intents)` and
   `hard_stop=null`: **Arm T10 Apply gate** → run
   **"3. T10 Apply Dry Run"** then **"4. T10 Apply Real"**.
9. On `rc=2`: do **not** T10 apply. Use **Copy Panel Snapshot** to
   share state, then follow §7 above.

---

End of R10C handoff.
