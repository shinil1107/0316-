# Cursor Handoff — Autotrading v0 Progress (Round 3)

**Date**: 2026-05-12
**Author**: Cursor (Claude Opus 4.7)
**Predecessor handoffs**:
- `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS.md` (round 1 — Step 1+2)
- `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R2.md` (round 2 — Step 3A–4 + first intent-matched 3-BUY day)
- Codex review notes consumed this round:
  - `…_codex/docs/CURSOR_HANDOFF_AUTOTRADING_V0_NEXT_R3.md` (input for this round)

**Goal of round 3** (per Codex `NEXT_R3.md`):
1. Stabilize post-submit *order-state visibility* before any
   cancel/replace/reprice work.
2. Wire `inquire-nccs` so newly-submitted ODNOs can be looked up while
   the order is still open/pending.
3. Migrate echo polling from "ccnl only" to "nccs first → ccnl fallback".
4. Investigate what `run_meta.status` becomes after T10 applies an
   actionable artifact.
5. Define an explicit order state model in code (dataclass-only, no
   persistence yet).

All round-3 work happened **on paper only** with the existing two-gate
safety model (`KIS_ENV=paper` + `KIS_PAPER_SUBMIT_OK=true` for any real
transmission). No live capital path was touched.

---

## 1. Scope summary

| Codex R3 item | Severity | Status |
|---|:--:|:--:|
| P1.A — wire `inquire-nccs` read-only (`get_open_orders`, CLI probe) | P1 | DONE |
| P1.B — echo polling: `nccs` first → `ccnl` fallback | P1 | DONE |
| P2.A — post-T10 artifact state transition inspection | P2 | DONE (docs only, no code change) |
| P2.B — order state model (dataclass + transitions) | P2 | DONE (types-only, no persistence) |
| P3   — cash-drift ledger reconciliation tool | P3 | DEFERRED (still out of order pipeline) |
| Round-3 §8 acceptance: ODNO visibility decision | P1 | **ACHIEVED via path 2** (see §6) |

Round-3 §6 "do not do" list was respected:
no `--all-buys`, no auto holdings write, no auto-apply,
no retry-budget bump, no live capital, no auto-reprice.

---

## 2. P1.A — `inquire-nccs` integration

### 2.1 Reference

Codex R3 §3 P1 prescribed the new endpoint, and the official KIS LLM
sample (`koreainvestment/open-trading-api/examples_llm/overseas_stock/
inquire_nccs/inquire_nccs.py`) confirmed:

- Path: `/uapi/overseas-stock/v1/trading/inquire-nccs`
- TR_ID (live): `TTTS3018R`
- Paper TR_ID inferred from the standard `T → V` prefix mirror used by
  every other overseas TR in this codebase: `VTTS3018R`. Verified live
  in §2.4.
- Required params: `CANO`, `ACNT_PRDT_CD`, `OVRS_EXCG_CD`, `SORT_SQN`,
  `CTX_AREA_FK200`, `CTX_AREA_NK200`.
- `OVRS_EXCG_CD` must NOT be blank — `NASD` returns the US
  aggregate (NASD+NYSE+AMEX); any other code returns only that
  single exchange. This is the same quirk balance/ccnl have.
- Paging follows the standard CTX + `tr_cont` F/M pattern.

### 2.2 Adapter changes (`phase3/autotrade/kis_broker_adapter.py`)

- Added `EP_INQ_NCCS` constant and `TR["inquire_nccs"]` matrix.
- New `OpenOrder` dataclass — normalized view with keys the echo / state
  model can rely on:
  ```python
  OpenOrder(
      broker_order_id, ord_dt, ord_tmd,
      symbol, market, side,
      qty_order, qty_filled, qty_remaining,
      limit_price, status_text,
      raw,                    # original KIS row preserved for audit
  )
  ```
- `KisBrokerAdapter.get_open_orders(market="NASD", sort_order="DS",
  max_pages=20, return_raw=False)` — paging-aware, same pattern as
  `get_order_history()`. Returns `List[OpenOrder]` by default, or raw
  KIS dicts when `return_raw=True`.
- `KisBrokerAdapter._normalize_open_order(raw)` static — Korean-name
  key resolution with multi-alias fallback. Defends against the case
  where KIS only emits `qty_order` and we have to derive remaining as
  `ord_qty - ft_ccld_qty`.
- `KisBrokerAdapter.find_open_order(odno, market="NASD")` convenience.
- `MockBrokerAdapter` mirrors `get_open_orders` / `find_open_order`
  (returns `[]` / `None`; mock fills are immediate so no order is ever
  "open"). Logged via audit for self-check completeness.

### 2.3 CLI probe

- New target `open-orders` in the existing `python3 -m phase3.autotrade
  .kis_broker_adapter probe …` CLI.
- Output: count + per-row line including ODNO, symbol, market, side,
  order/filled/remaining qty, limit price, ord_dt/ord_tmd, status text.
- Empty list is a valid outcome — printed with a hint instructing the
  operator to inspect after a fresh submit if they need a non-empty
  case.

### 2.4 First live call

```bash
PYTHONPATH=. python3 -m phase3.autotrade.kis_broker_adapter probe open-orders --market NASD --quiet
```

Result (2026-05-12 13:23 UTC, pre-market):

```
PROBE: open orders  market=NASD  (GET inquire-nccs)
  open orders = 0
```

- HTTP 200, `rt_cd=0`. **TR_ID `VTTS3018R` is accepted by paper.**
- Empty list expected since there were no pending orders at the moment.

---

## 3. P1.B — echo polling: `nccs` first → `ccnl` fallback

### 3.1 New module `phase3/autotrade/echo.py`

- Centralizes the post-submit echo helper that was previously inlined
  in both `paper_buy.py` and `paper_execute_intent.py`. Both now call
  the same function and produce identical JSON dumps for the echo
  step.
- `echo_poll(adapter, broker_order_id, *, market, max_polls,
  interval_sec, on_attempt)` — calls `inquire-nccs` first, falls back
  to `inquire-ccnl` only when nccs did not match, stops at the first
  successful match.
- Result shape (matches Codex R3 §3 P1.B suggestion exactly):
  ```python
  {
      "matched": bool,
      "source":  "nccs" | "ccnl" | None,
      "broker_order_id": str,
      "matched_row": Dict[str, Any] | None,  # raw + injected "_normalized"
      "attempts": List[{
          "i": int,
          "ts": str,                # UTC ISO-8601
          "nccs_rows": int | -1,    # -1 on call failure
          "nccs_error": str | None,
          "ccnl_rows": int | None | -1,
          "ccnl_error": str | None,
          "matched_source": "nccs" | "ccnl" | None,
      }]
  }
  ```
- `attempt_stdout_line(attempt, max_polls)` — formatter for verbose
  stdout, so caller scripts don't grow ad-hoc printing logic. Renders
  e.g. `[echo 1/4] nccs=0rows   ccnl=14rows`.

### 3.2 `paper_buy.py` migration

- Removed inline `echo_poll`.
- New step-4 echo block uses `echo.echo_poll(market=resolved_market)`
  and records `echo_matched` / `echo_missing` step entries with the
  full `attempts` list and resolved `source`.

### 3.3 `paper_execute_intent.py` migration

- Same import + invocation pattern.
- `derive_fill_price` updated: when echo source is `"nccs"` we **do
  not** treat the matched row as a fill (the order is still open from
  nccs's perspective); only `ccnl` rows can be authoritative. cash
  delta remains the practical authoritative source until KIS surfaces
  a fill-price field through one of these endpoints.
- `T10 MANUAL APPLY SUMMARY` now includes `EchoSource` so the
  operator can see at a glance whether visibility came via nccs or
  ccnl or neither.
- JSON dump (`~/.kis_audit/intent_buy_*.json`) `echo_result` block now
  carries the new shape including per-attempt `nccs_rows`/`ccnl_rows`.

### 3.4 Self-check + lint

- `--self-check` passes unchanged (mock adapter mirrors new methods).
- No new linter issues across `kis_broker_adapter.py`, `echo.py`,
  `paper_buy.py`, `paper_execute_intent.py`.

---

## 4. P2.A — post-T10 artifact transition (inspected, no code change)

The R2 §10.7 observation was that today's `intents`-resolved run was
`20260511_214648_daily` but the post-T10 reconcile re-discovered an
*older* `20260509_185312_daily` as latest_actionable. Investigation
this round:

### 4.1 Files inspected (yesterday's artifact, post-T10)

```
daily_runs/20260511_214648_daily/
  run_meta.json                       (modified 23:33:15 KST — T10 save time)
  execution_meta.json                 (NEW — written by T10)
  execution_applied.csv               (NEW — written by T10)
```

### 4.2 `run_meta.status` transition

```
Pre-T10  status = "awaiting_execution"
Post-T10 status = "executed"
```

Other post-T10 fields populated by T10:
```
last_execution_timestamp        2026-05-11T23:33:15.364624+09:00
executed_row_count_total        3
executed_recommendation_count   3
total_checkable_count           3       # actionable rows only
                                        # (BUY_MORE 2 + BUY_NEW 1 = 3)
                                        # SELL_GRACE 69 / DEFERRED 9 / HOLD 3 excluded
```

### 4.3 `execution_meta.json` (T10's own fact file)

```json
{
  "schema_version": "artifact/v1",
  "run_id":         "20260511_214648_daily",
  "source":         "T10",
  "execution_timestamp": "2026-05-11T23:33:15.364624+09:00",
  "execution_status":    "executed",
  "executed_row_count_this_update": 3,
  "executed_row_count_total":       3,
  "executed_recommendation_count":  3,
  "total_checkable_count":          3,
  "cash_balance":   14432.63,
  "total_capital":  109118.74
}
```

### 4.4 `execution_applied.csv` (the manual T10 echo of our broker fills)

```
ExecutionTimestamp,Source,RunId,RecRowId,Ticker,Action,ExecutedPrice,ExecutedShares,…
2026-05-11T23:33:15.364624+09:00,T10,20260511_214648_daily,71,AMD,BUY_NEW,460.17,2,…,920.34
2026-05-11T23:33:15.364624+09:00,T10,20260511_214648_daily,72,GLW,BUY_MORE,203.23,1,…,203.23
2026-05-11T23:33:15.364624+09:00,T10,20260511_214648_daily,70,ON,BUY_MORE,104.88,1,…,104.88
```

The three (RunId, RecRowId, Ticker, Action, ExecutedPrice, ExecutedShares)
tuples match exactly what the `intent_buy_*.json` audit dumps recorded
on the broker side. T10 honored the `Suggested T10 Price = cash_delta`
estimates we provided.

### 4.5 Effect on `reconcile._find_latest_actionable_artifact`

`reconcile.py` discovers only artifacts whose `status ==
"awaiting_execution"`. After T10 flips today's artifact to
`"executed"`, the function falls back to the next-oldest still-open
artifact, which today (2026-05-12) was again `20260509_185312_daily`
(both 5/11 and 5/12 artifacts have been T10-applied since R2). The
behaviour is **correct from a coding standpoint** — the function does
exactly what its name says — but the staleness is a smell.

### 4.6 Recommendation (no code change today)

Codex R3 §3 P2.A offered three patches:
1. continue using only latest `awaiting_execution` (current),
2. ignore stale older actionable artifacts after a newer run has
   executed,
3. support explicit `--run-id` for managed scope.

This round we recommend (3) — adding `--run-id` to `reconcile.py` so
the operator can pin scope when needed. Option (2) is too fuzzy (what
counts as "stale"? T+1 trading day? Calendar day?) and risks hiding
genuine drift. Option (1) lets ghost artifacts dictate scope. Option
(3) is one ~5-line patch and keeps default behaviour identical. We
leave it for Codex R4 to confirm before patching.

---

## 5. P2.B — order state model (`phase3/autotrade/order_state.py`)

Types-only, no persistence, no broker calls.

### 5.1 `OrderState` enum

```
intent_created   dry_run            submitted
open_or_pending  partially_filled   filled
cancel_requested cancelled
replace_requested replaced
rejected         unknown
```

Helpers:
- `OrderState.is_terminal` — `{DRY_RUN, FILLED, CANCELLED, REJECTED}`.
- `OrderState.is_active_at_broker` — `{SUBMITTED, OPEN_OR_PENDING,
  PARTIALLY_FILLED, CANCEL_REQUESTED, REPLACE_REQUESTED}`.

### 5.2 `StatusSource` enum

Audit metadata describing *where* the current state was learned from:
`local_intent`, `place_order_ack`, `nccs_echo`, `ccnl_echo`,
`position_delta`, `cancel_ack`, `replace_ack`, `operator`, `unknown`.

### 5.3 `ALLOWED_TRANSITIONS` + `assert_transition(prev, nxt)`

Directed edges encoded as `Dict[OrderState, FrozenSet[OrderState]]`.
`IllegalStateTransition` is raised when callers attempt a non-edge.
Recovery / operator paths can pass `skip_validation=True` (used inside
`OrderRecord.with_state`).

Notable edges:
- `submitted → {open_or_pending, partially_filled, filled, rejected,
  unknown}` — paper's "submit and immediate fill" path is a legal
  shortcut (no open_or_pending in between).
- `partially_filled → partially_filled` allowed (qty grows).
- `unknown → {open_or_pending, partially_filled, filled, cancelled,
  rejected}` — for crash-recovery and broker-state polls.

### 5.4 `OrderRecord` (frozen dataclass)

Single immutable snapshot. Mutations happen by producing a new
record with `OrderRecord.with_state(new_state, status_source=…, …)`,
which validates the edge and copies all unchanged fields.

Fields per Codex R3 §3 P2:
`run_id`, `rec_row_id`, `ticker`, `market`, `side`, `qty_intended`,
`qty_filled`, `qty_remaining`, `limit_price`, `broker_order_id`,
`client_order_id`, `state`, `status_source`, `submitted_at`,
`last_checked_at`, `raw_broker_row`, `note`.

### 5.5 What stays out of v0 of this module

- No JSONL state log on disk.
- No bridge from `place_order` / echo / cancel results into
  `OrderRecord` (will land with the cancel/replace endpoints, since
  that's where the state mutation surface lives).
- No recovery / restart semantics.

This is on purpose — Codex R3 §3 P2 was explicit: "This can start as
a document or dataclass only. No persistence required yet."

---

## 6. Live market verification — 3 BUY orders + reconcile

Same operational pattern as R2 §10 (operator: shin-il, T10 batch apply).

### 6.1 Today's artifact

```
run_id   = 20260512_210645_daily
status   = awaiting_execution (pre-T10)
recos    = 90 rows
buy_intents (after BUY-only filter) = 3
```

| RecRowId | Ticker | Action   | Qty | Limit ($) | Market |
|---------:|:-------|:---------|----:|----------:|:-------|
|       76 | APA    | BUY_MORE |   6 |     36.97 | NASD   |
|       77 | CF     | BUY_MORE |   1 |    124.86 | NYSE (fallback) |
|       78 | TER    | BUY_MORE |   2 |    353.74 | NASD   |

All three: no risk flags, `last+0.3 %` pricing path (paper price endpoint
returned last-only mid-session), BUY-side ceil rounding.

### 6.2 Submissions

| # | ODNO         | Ticker | Qty | Pre cash → Post cash      | cash Δ ($) | Pre held → Post held | Est. fill ($) | nccs echo | ccnl echo |
|---|:-------------|:-------|----:|:--------------------------|-----------:|:---------------------|--------------:|:----------|:----------|
| 1 | `0000049652` | APA    |   6 | $14,269.50 → $14,046.37   |    −223.13 | 32 → 38              |       37.1883 | 0/4 rows  | miss (14r)|
| 2 | `0000049667` | CF     |   1 | $14,046.37 → $13,920.92   |    −125.45 | 7 → 8                |       125.45  | 0/4 rows  | miss (15r)|
| 3 | `0000049673` | TER    |   2 | $13,920.92 → $13,206.84   |    −714.08 | 4 → 6                |       357.04  | 0/4 rows  | miss (16r)|

Cumulative broker cash Δ: **−$1,062.66**, exactly equal to
`Σ(per-order cash delta) = 223.13 + 125.45 + 714.08`.

### 6.3 Echo visibility — **the key R3 datapoint**

For every single one of the three submissions:
- `inquire-nccs` returned **0 rows** on all 4 polls (12 s polling
  window). The TR_ID was accepted, the call was healthy — there was
  simply no open order to surface, because…
- `inquire-ccnl` returned 14/15/16 rows on all 4 polls but **the
  newly-submitted ODNO was not present in any of them.**
- Position and cash deltas confirm the fills happened (held +6/+1/+2,
  cash −$223.13/−$125.45/−$714.08).

The mechanism that explains all three datapoints simultaneously: **KIS
paper simulates a marketable-LIMIT order as an immediate 100% fill at
a near-spot price.** An order that fills in the same instant it is
acknowledged was never "open" — so `inquire-nccs` (the open-order
list) is structurally empty by the time the next call lands. And
`inquire-ccnl` has the known propagation lag we documented in R2 §9.9,
so the same ODNO doesn't show up in history within our 12 s window
either.

This was the exact second outcome Codex R3 §8 allowed for as a valid
acceptance:

> Newly submitted ODNO is visible through nccs **or the system
> conclusively records that KIS paper does not expose it through
> either endpoint.**

Combined with R2 §9.7 (2 prior orders, same behaviour) and R2 §10.7.2
(3 prior orders, same behaviour), we now have **8 consecutive paper
orders** with the same non-echo pattern. The conclusion is no longer
hypothesis-grade.

### 6.4 Post-T10 reconcile (today)

```
profile=paper  ts=2026-05-12T13:44:25+00:00
managed_scope       : 84 tickers (current ∪ latest reco)
matched             : 84
qty_mismatch        : 0          ✓
local_only          : 0
broker_only_managed : 0
background_broker   : 0
reco_only           : 0

cash local          : $13,369.97
cash broker         : $13,206.84
cash drift          : -$163.13   (broker − local)
settlement_pending  : $0.00
```

- Today's three BUYs are fully aligned in the matched 84 rows
  (APA 38/38, CF 8/8, TER 6/6).
- Cash drift moved by exactly **$0.01** vs R2 §10.5 (−$163.14 →
  −$163.13). For practical purposes the residual is unchanged. As
  documented in R2 §10.7.1, today's pipeline contributed $0 to drift;
  the residual continues to live outside the order pipeline.

### 6.5 Artifact discovery (today, post-T10)

`latest_actionable_artifact = 20260509_185312_daily` again. Same
behaviour as 5/11, same root cause as §4. No code change applied this
round.

---

## 7. R3 §8 acceptance scoreboard

| Codex R3 §8 criterion | Status |
|---|:--:|
| `inquire-nccs` can be called safely in paper | ✓ |
| Newly submitted ODNO is visible via nccs *or* paper conclusively does not expose it through either endpoint | ✓ (path 2 — 8/8 paper orders confirm, root cause = immediate-fill simulation) |
| `paper_execute_intent.py` no longer reports a weak "ccnl echo missing" without checking nccs | ✓ (per-attempt nccs_rows + ccnl_rows in stdout + JSON) |
| T10 artifact state transition understood | ✓ (§4: `awaiting_execution` → `executed`, plus new fact files) |
| No local holdings write path is changed | ✓ (T10 remains sole writer) |
| No live trading path is opened | ✓ (live triple-lock intact) |

---

## 8. Open questions / proposals for Codex Round 4

| # | Topic | Severity | Proposed direction |
|---|---|:--:|---|
| Q1 | nccs is structurally empty for paper marketable LIMIT (immediate-fill simulation). Acceptance was achieved via the "neither endpoint" path. **Will live behave the same way?** | P1 | When `KIS_CONFIRM_LIVE` is eventually unlocked, repeat the test with a single 1-share live order at a non-marketable limit (e.g. last × 0.97) so the order is guaranteed to stay open for ≥10 s. nccs match would then become the primary expectation, not the fallback. Document the live-vs-paper visibility delta before any auto-cancel/auto-reprice logic ships. |
| Q2 | `reconcile._find_latest_actionable_artifact` falls back to ghost old artifacts after newer ones are flipped to `executed`. | P2 | Add `--run-id` override to `reconcile.py`. Default behaviour unchanged. Optional follow-up: report when the discovered artifact is older than the most recent `executed` one (one-line warning, no abort). |
| Q3 | Cash drift `≈ -$163` is now confirmed independent of the order pipeline across two distinct trading days. | P2 | Standalone offline tool: walk the CashLedger, replay all known deltas (dividends, FX, manual edits, fills, fees) and emit per-day drift contributors. **Out of scope for the order pipeline.** |
| Q4 | KIS paper LIMIT semantics fill **above** the limit on marketable orders (R2 §10.7.5 and again on all three R3 fills). | P3 | Two-line note in `intents.py` documenting that live LIMIT will refuse to print above limit; paper is a slippage simulator, not a true LIMIT engine. No code change. |
| Q5 | Order state model (`order_state.py`) exists but no one writes to it yet. | P3 | Decide whether the bridge belongs in (a) `paper_execute_intent.py` producing INTENT_CREATED → SUBMITTED → FILLED records, or (b) a new orchestrator that owns the lifecycle and calls `paper_execute_intent.py` as a primitive. Bridge wiring should land before cancel/replace endpoints. |
| Q6 | Paper visibility gap forces us to use `cash_delta / shares` for fill price. Acceptable for paper, **but live needs broker-confirmed prices** before any auto-write logic. | P1 (live-only) | Survey the per-order detail endpoints KIS exposes (e.g. `inquire-ccnl-history`, `inquire-period-trans`) for an authoritative fill price field. Targeted for live preflight. |

---

## 9. File map

```
phase3/autotrade/
  __init__.py                  unchanged (R2 two-gate docs still apply)
  kis_broker_adapter.py        ADDED EP_INQ_NCCS, TR entry, OpenOrder,
                                 get_open_orders, _normalize_open_order,
                                 find_open_order, _probe_open_orders,
                                 mock parity; CLI `probe open-orders` target
  echo.py                      NEW — phase3/autotrade/echo.py
                                 echo_poll + attempt_stdout_line
                                 (nccs first → ccnl fallback)
  order_state.py               NEW — phase3/autotrade/order_state.py
                                 OrderState / StatusSource enums,
                                 ALLOWED_TRANSITIONS, assert_transition,
                                 OrderRecord (frozen), new_intent helper
  reconcile.py                 unchanged this round (P2.A is doc-only)
  intents.py                   unchanged
  parity.py                    unchanged
  paper_buy.py                 echo step migrated to shared echo.echo_poll;
                                 docstring + step_4/5 text updated to reflect
                                 nccs+ccnl polling
  paper_execute_intent.py      same migration; derive_fill_price now
                                 distinguishes nccs vs ccnl matches;
                                 T10 summary now includes EchoSource

phase3/docs/
  CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS.md       round 1
  CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R2.md    round 2
  CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R3.md    this file

~/.kis_audit/
  YYYY-MM-DD.jsonl                  per-day API audit (incl. inquire-nccs)
  intent_buy_…_APA.json             R3 BUY APA dump (echo schema v2)
  intent_buy_…_CF.json              R3 BUY CF  dump
  intent_buy_…_TER.json             R3 BUY TER dump
  reconcile_20260512T134425_paper.json  R3 post-T10 reconcile
```

JSON audit dumps produced today:
```
~/.kis_audit/intent_buy_20260512T133727_20260512_210645_daily_APA.json
~/.kis_audit/intent_buy_20260512T133849_20260512_210645_daily_CF.json
~/.kis_audit/intent_buy_20260512T133938_20260512_210645_daily_TER.json
~/.kis_audit/reconcile_20260512T134425_paper.json
```

---

## 10. Status

```
P1.A  inquire-nccs adapter + CLI probe           DONE
P1.B  echo_poll(nccs → ccnl) shared helper       DONE
P1.B  paper_buy / paper_execute_intent migration DONE
P2.A  post-T10 artifact transition investigation DONE (no code change)
P2.B  order state model (types-only)             DONE
R3 §8 acceptance                                  ACHIEVED (path 2)
3-BUY round (APA / CF / TER)                     DONE
T10 manual apply (all 3 rows)                    DONE
post-T10 reconcile                               DONE (qty_mismatch = 0)
PROGRESS_R3.md                                    this file
```

Ready for Codex round-4 review. Items in §8 (especially Q1 — live
behaviour expectation and Q6 — broker-confirmed fill price) are the
strongest signals to address before any cancel/replace/reprice work
begins.
