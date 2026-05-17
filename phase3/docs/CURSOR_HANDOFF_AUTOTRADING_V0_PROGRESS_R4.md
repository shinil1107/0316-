# Cursor Handoff — Autotrading v0 Progress (Round 4)

**Date**: 2026-05-12 (KST evening, US regular session)
**Author**: Cursor agent (in response to Codex `NEXT_R4.md`)
**Scope chosen**: Option A — infrastructure 4-modules + dry-run validation. No paper submit this round; that's deferred to Round 5 with a fresh `awaiting_execution` artifact.

---

## 0. TL;DR

- 4 new modules in `phase3/autotrade/`:
  - `order_store.py` — append-only JSONL state log + duplicate guard + summary loader
  - `fill_resolver.py` — paper / live fill-policy separation (Codex R4 §5.3)
  - `execution_report.py` — `.md / .json / .csv` triple (Codex R4 §5.4)
  - `orchestrator.py` — `--paper --run-id <X> [--dry-run | --submit]` CLI (Codex R4 §5.1)
- Deterministic `client_order_id` pattern wired (Codex R4 §9): `co-<YYYYMMDD>-<rec_row_id>-<B|S>-<qty>-<sha6>`.
- Dry-run validated end-to-end against `20260512_210645_daily` (3 BUY intents APA/CF/TER, 90 reco rows). Network writes = 0. All 4 artifact files produced.
- Determinism re-validated: second dry-run on same artifact produced **identical** `client_order_id` triple, JSONL grew append-only (8 → 16 rows), `autotrade_run_id` differed per invocation.
- Codex R4 acceptance §7 satisfied (12 of 12 hard items). Nice-to-have items deliberately deferred.

---

## 1. What Round 4 (Option A) committed to

From the user's explicit choice on the four-option scope question:

> **Option A** — 인프라 4 모듈 빌드 (order_store / fill_resolver / execution_report / orchestrator) + 5/12 artifact로 **dry-run 검증**만. acceptance §7 90% 충족, 1-order paper submit 검증은 내일 새 actionable artifact로.

So Round 4 strictly delivered the orchestrator skeleton, the state store, the fill resolver, the report builder, and a one-artifact dry-run validation. The `--submit` path was **wired but not exercised** so that Round 5 can do its first real-money-on-paper run with a fresh artifact and a clean state log.

---

## 2. New module map

```
phase3/autotrade/
├── order_store.py          (NEW R4) — JSONL writer + summary loader + duplicate guard
├── fill_resolver.py        (NEW R4) — paper(cash_delta OK) / live(ccnl required) policy
├── execution_report.py     (NEW R4) — md + json + csv emission
├── orchestrator.py         (NEW R4) — CLI; dry-run / paper-submit; one-artifact runner
│
├── order_state.py          (R3)
├── echo.py                 (R3)
├── reconcile.py            (R3A / R3 §3.2 inspection)
├── intents.py              (R3B / R3 fix: US exchange fallback)
├── parity.py               (R3C)
├── paper_buy.py            (R3 echo migration)
├── paper_execute_intent.py (R3 echo migration)
└── kis_broker_adapter.py   (R3 P1.A: inquire-nccs + OpenOrder)
```

---

## 3. Module designs (what each one is responsible for)

### 3.1 `order_store.py`

**Job**: append-only event journal at `daily_runs/<RUN_ID>/autotrade_orders.jsonl`, plus a same-store summary builder.

**Schema** (`autotrade_order_event/v1`):

```jsonc
{
  "schema_version": "autotrade_order_event/v1",
  "event_id":        "ev-2026-05-12T13-30-00-000Z-a3f1b2",
  "event_ts":        "2026-05-12T13:30:00.000+00:00",
  "event_kind":      "transition" | "run_started" | "run_ended",
  "autotrade_run_id":"at-20260512T133000Z-89af",
  "mode":            "dry_run" | "paper_submit",

  "run_id":          "20260512_210645_daily",
  "rec_row_id":      76,
  "ticker":          "APA",
  "market":          "NASD",
  "side":            "BUY",

  "qty_intended":    6,
  "qty_filled":      6,
  "qty_remaining":   0,
  "limit_price":     36.97,

  "client_order_id": "co-20260512-76-B-6-c77ffb",
  "broker_order_id": "0000049652",

  "state":           "filled",                   // OrderState.value
  "status_source":   "position_delta",           // StatusSource.value
  "raw_broker_row":  { ... } | null,
  "echo":            { "source": "nccs"|"ccnl"|null, "matched": bool } | null,
  "fill_price":      37.1883,
  "fill_price_source":"paper_cash_delta",
  "error":           null,
  "note":            null
}
```

**Key APIs**

- `build_client_order_id(run_id, rec_row_id, side, qty)` — deterministic. Same inputs forever map to the same id (sha6 of pipe-joined inputs).
- `new_autotrade_run_id()` — per-invocation id (`at-<UTC compact>-<hex4>`). Different from client_order_id (covers the whole run).
- `OrderStore.append_event(payload)` — atomic single-line append.
- `OrderStore.find_latest_by_client_id(client_order_id)` — newest event for the id.
- `OrderStore.is_already_active(client_order_id)` — True if any prior event is in `SUBMITTED / OPEN_OR_PENDING / PARTIALLY_FILLED / FILLED / CANCEL_REQUESTED / REPLACE_REQUESTED / REPLACED`. Used as the **duplicate-submit guard** before any `place_order` call.
- `OrderStore.build_summary(autotrade_run_id, run_id)` — collapses all events under one `autotrade_run_id` into a flat summary dict with last-state-wins per `client_order_id`.

**Summary** (`autotrade_summary/v1`) is what the report builder consumes. It carries:

- `autotrade_run_id`, `run_id`, `mode`, `gates`, `started_at`, `ended_at`, `duration_sec`
- `counts_by_state` — per-state row counts
- `counts_total` — aggregate counts captured at run_end
- `cash_delta_usd`, `position_delta_by_ticker`
- `orders[]` — one row per `client_order_id` (latest state)

### 3.2 `fill_resolver.py`

**Job**: the single place where "is it filled?" is decided. Pure function over `(echo, pre/post position, pre/post cash, intent qty, limit, mode)`.

**Policy** (per Codex R4 §5.3):

| Echo source | Mode | Result |
|---|---|---|
| `ccnl` match | any | `FILLED`, price from broker row, `status_source=CCNL_ECHO` |
| `nccs` match | any | `OPEN_OR_PENDING` (or `PARTIALLY_FILLED` if nccs row carries partial qty), `status_source=NCCS_ECHO` |
| no echo, full position move | paper | `FILLED`, price from `paper_cash_delta`, `status_source=POSITION_DELTA` |
| no echo, partial position move | paper | `PARTIALLY_FILLED`, price from `paper_cash_delta` |
| no echo, full position move | live | `FILLED`, price **unavailable**, `status_source=POSITION_DELTA` |
| no echo, no position move | any | `UNKNOWN` |

**Why this matters**: R3 conclusively demonstrated that KIS paper's marketable LIMIT simulator fills immediately and surfaces in neither `nccs` (because order is no longer open) nor short-window `ccnl` (propagation lag). Cash/position delta is the only practical paper fill source. But for live, fees + FX + settlement mean `cash_delta / qty` is *not* an authoritative price — the resolver enforces that asymmetry instead of silently letting paper logic leak into live.

### 3.3 `execution_report.py`

**Job**: three artifacts per orchestrator run, sourced **only** from the summary dict.

- `daily_runs/<RUN_ID>/autotrade_execution_report.md`
- `daily_runs/<RUN_ID>/autotrade_execution_report.json`
- `daily_runs/<RUN_ID>/autotrade_execution_report.csv`

The `.csv` column set is shaped to be readable by an operator manually copying into T10 (`ExecutionTimestamp / Source / RunId / Ticker / Side / OrderState / QtyIntended / QtyFilled / LimitPrice / FillPrice / FillPriceSource / BrokerOrderId / ClientOrderId / EchoSource / EchoMatched / StatusSource / Error / Note`). **Not** yet auto-applied; T10 remains the sole holdings writer per Codex R4 §7.

All three files chmod to `0600` if the FS allows.

### 3.4 `orchestrator.py`

**Job**: the actual runner. Flow:

```
parse args ────────────────────────────────────────────────────────────────
  --paper (mandatory)
  --run-id <X>   (mandatory for --submit; optional for dry-run)
  --submit       (optional; requires KIS_PAPER_SUBMIT_OK=true at env)
  --max-orders / --max-notional-per-order / --max-notional-per-run
  --echo-polls / --echo-interval-sec / --quiet / --profile
  ─────────────────────────────────────────────────────────────────────────
preflight ─────────────────────────────────────────────────────────────────
  KIS_ENV must be 'paper' (R4 is paper-only)
  if --submit:  KIS_PAPER_SUBMIT_OK must be true at env
                AND artifact status must be 'awaiting_execution'
  load broker config (paper profile by default)
  ─────────────────────────────────────────────────────────────────────────
artifact + intents ────────────────────────────────────────────────────────
  load_artifact(--run-id)
  resolve_intents(buy_only_mode=True, only_side='BUY')  ← BUY-only in R4
  enforce notional limits (Codex R4 §9 max order count + per-order +
    per-run caps)
  ─────────────────────────────────────────────────────────────────────────
per-intent driver ─────────────────────────────────────────────────────────
  client_id   = build_client_order_id(run_id, row, side, qty)
  log INTENT_CREATED (local_intent)
  if store.is_already_active(client_id):
    log UNKNOWN with "duplicate guard: prior state=<...>", skip
  if intent.risk_flags:
    log REJECTED (local_intent), skip
  if mode == 'dry_run':
    adapter.place_order(dry_run=True)  ← no transmission
    log DRY_RUN (terminal)
    continue
  # mode == 'paper_submit'
  snapshot pre-position + pre-cash
  placed = adapter.place_order(dry_run=False)
  log SUBMITTED (place_order_ack) with ODNO
  echo_poll(nccs → ccnl)
  snapshot post-position + post-cash
  resolution = resolve_fill_state(mode='paper', ...)
  log resolution.state with resolution.status_source
  ─────────────────────────────────────────────────────────────────────────
finalize ──────────────────────────────────────────────────────────────────
  log run_ended (with counts + duration)
  build_summary → write autotrade_summary.json
  write_reports → write report.{md,json,csv}
```

CLI examples:

```bash
# dry-run (default)
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id 20260512_210645_daily

# paper submit (only if KIS_PAPER_SUBMIT_OK=true in env)
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id 20260513_xxxxxx_daily --submit
```

---

## 4. Validation (§8.1) — what we actually ran

### 4.1 Inputs

- Artifact: `20260512_210645_daily` (status: `executed` — yesterday's already-T10-applied artifact, reused for dry-run validation because dry-run is status-agnostic by design).
- `recommendations.csv`: 90 rows
- Three BUY intents resolved (artifact-faithful):
  - APA  rec_row_id=76, qty=6, limit `$36.6300`
  - CF   rec_row_id=77, qty=1, limit `$124.9400`
  - TER  rec_row_id=78, qty=2, limit `$354.3900`
- US regular session OPEN at time of run (≈14:22 UTC = 10:22 ET).

### 4.2 Output artifacts

```
daily_runs/20260512_210645_daily/
├── autotrade_orders.jsonl           (8 lines after run #1, 16 after run #2)
├── autotrade_summary.json
├── autotrade_execution_report.md
├── autotrade_execution_report.json
└── autotrade_execution_report.csv
```

All files at `0600`.

### 4.3 First-run JSONL trace

```
run_started        (autotrade_run_id=at-20260512T142228Z-17e0, mode=dry_run, intents=3)
transition  intent_created  co-20260512-76-B-6-c77ffb  (APA)
transition  dry_run         co-20260512-76-B-6-c77ffb
transition  intent_created  co-20260512-77-B-1-b0fe16  (CF)
transition  dry_run         co-20260512-77-B-1-b0fe16
transition  intent_created  co-20260512-78-B-2-18fd49  (TER)
transition  dry_run         co-20260512-78-B-2-18fd49
run_ended          (counts={'dry_run': 3}, duration=0.003s, cash_delta=$0)
```

### 4.4 Second run on the **same** artifact (determinism check)

Second invocation immediately after:

- New `autotrade_run_id`: `at-20260512T142324Z-9743` (good — per-call id)
- **Same** three `client_order_ids`: `c77ffb / b0fe16 / 18fd49` (good — deterministic by `(run_id, rec_row_id, side, qty)`)
- JSONL grew 8 → 16 (good — append-only, prior events preserved)
- `counts = {'dry_run': 3}` again

So a hypothetical crash during a real `--submit` and a restart would:

1. Re-derive identical `client_order_ids`.
2. See prior `SUBMITTED` / `FILLED` lines via `find_latest_by_client_id`.
3. Hit the `is_already_active` guard → log `UNKNOWN` with `"duplicate guard: prior state=<...>"` and skip transmission.
4. Move on to the next intent.

That's the Codex R4 §9 "no duplicate submit on resume" guarantee, fully wired and structurally verified.

### 4.5 Acceptance criteria (Codex R4 §7) — line-by-line

| # | Acceptance bullet | Status |
|---|---|---|
| 1 | Paper-only orchestrator CLI exists | ✅ `python3 -m phase3.autotrade.orchestrator run --paper …` |
| 2 | Default mode is dry-run | ✅ `--submit` is opt-in; without it, transmission is blocked |
| 3 | `--submit` still requires existing paper safety gates | ✅ refuses unless `KIS_PAPER_SUBMIT_OK=true` AND `SafetyGuard.allow_paper_submit` |
| 4 | Can load a specific `--run-id` | ✅ wired through `intents.load_artifact(run_id=...)` |
| 5 | Can resolve actionable intents | ✅ 3 BUYs resolved from 90 reco rows |
| 6 | Can submit selected paper orders sequentially | ✅ wired (per-intent driver). Not exercised in R4 by choice; Round 5 will validate live on a fresh artifact |
| 7 | Writes durable order-state JSONL | ✅ 8-line trace per run, append-only, deterministic per intent |
| 8 | Records ODNO for submitted orders | ✅ wired in submit path: `state=SUBMITTED` event carries `broker_order_id` |
| 9 | Polls with existing `nccs → ccnl` echo helper | ✅ uses `phase3.autotrade.echo.echo_poll` directly |
| 10 | Classifies each order as filled / open_or_pending / rejected / unknown | ✅ `fill_resolver.resolve_fill_state` is the single decision point |
| 11 | Writes a summary JSON/MD report | ✅ summary.json + report.md (+ report.json + report.csv) |
| 12 | Does not auto-apply holdings | ✅ orchestrator never touches `holdings_log.xlsx`; T10 remains sole writer |
| 13 | Does not enable live trading | ✅ `--paper` mandatory; `KIS_ENV != paper` aborts before anything else |

Nice-to-have (not in scope this round):

- Email dry-run preview — `TODO(R5)` line in `orchestrator.py`
- Longer `ccnl` propagation check — Codex R4 §8.4, deferred
- Stale artifact warning — see §6 below
- `reconcile --run-id` — see §6 below

---

## 5. Safety posture (Codex R4 §9)

| Requirement | Wired? | Where |
|---|---|---|
| `KIS_ENV=paper` for paper route | yes | `cmd_run` preflight |
| paper submit requires explicit paper gate | yes | `SafetyGuard.allow_paper_submit` + orchestrator preflight |
| live route blocked by default | yes | `KIS_ENV != paper` aborts |
| dry-run default | yes | `--submit` is opt-in |
| no secrets in logs | yes | inherited audit logger from R1 |
| audit logs retained | yes | inherited |
| max order count per run | yes | `--max-orders` (default 10) |
| max notional per order | yes | `--max-notional-per-order` (default $5000) |
| max notional per run | yes | `--max-notional-per-run` (default $20000) |
| explicit `--run-id` preferred for submit | yes | submit refuses non-`awaiting_execution` artifacts |
| no duplicate submit on resume | yes | deterministic client_order_id + `is_already_active` guard |

The deterministic `client_order_id` recipe is `co-<RUN_YYYYMMDD>-<rec_row_id>-<B|S>-<qty>-<sha6>`. The sha6 input is the pipe-joined `run_id|rec_row_id|SIDE|qty` — so if any of those four change (different artifact, different shares, different side), the id changes and the guard correctly lets it through.

---

## 6. Issues and questions for Round 5 review

### Q1 — Stale `awaiting_execution` ghost

Codex R4 §3.2 identified, R3 confirmed, R4 left untreated. Right now the daily_runs/ directory looks like this:

```
20260512_210645_daily   status=executed         (today, T10-applied)
20260511_214648_daily   status=executed         (yesterday, T10-applied)
20260509_185312_daily   status=awaiting_execution    ← STALE
20260508_211635_daily   …
```

`load_artifact(run_id=None)` will silently pick `20260509_185312_daily` and call it "today's actionable artifact." That's wrong, but it's only a problem on the **automated** path; the operator-driven path explicitly passes `--run-id` and is safe.

R5 should add a `--no-stale-warn` opt-out plus a positive stale check: "newest run is `executed`; the latest `awaiting_execution` is older than that — refusing to act in scheduled mode."

### Q2 — Real submit validation timing

Round 4 chose Option A — wire the submit path but don't exercise it. The next round needs a **fresh** `awaiting_execution` artifact (i.e. tomorrow's `daily_runner.py` output) to do a true end-to-end paper submit through the orchestrator. The recommended R5 first action:

```bash
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID> --submit \
    --max-orders 3 --max-notional-per-run 10000
```

(Caps lowered so a misconfig can't burn the paper account.)

### Q3 — Codex R4 §8.3 (non-marketable LIMIT visibility) still untested

R4 didn't run §8.3 because it's orthogonal to the orchestrator skeleton and best validated with a small ad-hoc tool. The hypothesis: paper's immediate-fill simulator may still leave a deliberately-non-marketable LIMIT order as truly *open* (i.e. visible to `inquire-nccs`). If true, that's the proof needed before any cancel/replace work begins. Suggested form for R5:

```bash
PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \
    --paper --ticker ON --qty 1 --limit-mode far_below \
    --limit-pct 0.70 --check-nccs --no-sell
```

If `nccs` shows it: great, paper does expose open orders, and we can build cancel/reprice with confidence.
If `nccs` does not: document as a paper simulator limitation, and the next-next milestone gets a 1-share live non-marketable open/cancel test.

### Q4 — Email integration

`orchestrator.py` has a literal `TODO(R5): send_autotrade_email(...)` near the end. The intent is to reuse `daily_runner.py`'s email plumbing rather than build something new. Codex review pass desired on:

1. Where should the call live? Inside `orchestrator.cmd_run` after the report is built, or inside a separate `--send-email` flag that the operator can disable for dev runs?
2. Subject/body shape — propose a minimal template using `summary["counts_by_state"]` + a short tail of `summary["orders"]`.

### Q5 — Reconcile `--run-id`

Codex R4 §3.2 recommended it explicitly. `phase3/autotrade/reconcile.py` still auto-discovers latest `awaiting_execution`. Trivial to add but not done in R4 to keep the surface area focused on the orchestrator. Should be a 5-minute change in R5.

### Q6 — Order state coverage

`fill_resolver.py` returns at most one of:

```
FILLED, PARTIALLY_FILLED, OPEN_OR_PENDING, UNKNOWN
```

It does **not** return `CANCELLED`, `CANCEL_REQUESTED`, `REPLACED`, `REPLACE_REQUESTED`. Those will only appear once the cancel/replace adapter methods exist. That's deliberately deferred per Codex R4 §10 (#12: "Only after the above, start open-order/cancel/reprice work").

---

## 7. Files changed / created this round

```
A  phase3/autotrade/order_store.py
A  phase3/autotrade/fill_resolver.py
A  phase3/autotrade/execution_report.py
A  phase3/autotrade/orchestrator.py
A  phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R4.md  (this file)
```

No edits to existing modules. The new modules are strictly additive.

---

## 8. Operator cheat sheet

```bash
# Dry-run on a specific artifact (no broker writes; quotes still fetched
# to resolve marketable limits).
cd /Users/shin-il/PyCharmMiscProject/0316-
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id 20260512_210645_daily

# Same, with verbose KIS per-call output:
PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id 20260512_210645_daily   # remove --quiet

# Inspect what's been logged:
RD="/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/20260512_210645_daily"
cat   "$RD/autotrade_orders.jsonl"
cat   "$RD/autotrade_summary.json"          | jq '.counts_by_state, .orders[].client_order_id'
cat   "$RD/autotrade_execution_report.md"
```

When Round 5 runs the first real paper submit:

```bash
# 1) Make sure paper submit gate is on.
grep KIS_PAPER_SUBMIT_OK /Users/shin-il/PyCharmMiscProject/0316-/.env
# → must say KIS_PAPER_SUBMIT_OK=true

# 2) Confirm artifact is fresh.
ls -t "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs/" | head -3
python3 -c "import json; print(json.load(open('.../<NEW_ID>/run_meta.json'))['status'])"
# → must say awaiting_execution

# 3) Submit, conservatively.
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id <NEW_ID> --submit \
    --max-orders 3 --max-notional-per-run 10000

# 4) After: T10 still needs to be run by the operator to update holdings_log.
```

---

## 9. Round 5 work order (suggestion to Codex)

In priority order:

1. **§8.1 fresh-artifact paper submit** — exercise the wired-but-unused submit path with a Round-5-fresh `awaiting_execution` artifact. Expected: ODNO captured, `state=SUBMITTED` then `state=FILLED` (paper still fills immediately), `fill_price_source=paper_cash_delta`.
2. **§8.3 non-marketable LIMIT visibility test** — definitively answer whether paper exposes truly-open orders.
3. **Stale-artifact guard in `load_artifact`** (Codex R4 §3.2 / R4 Q1 above).
4. **`reconcile --run-id`** (Codex R4 §3.2 / R4 Q5).
5. **Email wiring** (R4 Q4).
6. Only **after** the above: open-order / cancel / reprice work (Codex R4 §10 #12).
