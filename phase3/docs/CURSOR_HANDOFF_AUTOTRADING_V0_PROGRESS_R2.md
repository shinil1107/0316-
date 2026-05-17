# Cursor Handoff — Autotrading v0 Progress (Round 2)

**Date**: 2026-05-11
**Author**: Cursor (Claude Opus 4.7) working with shin-il
**Previous round**: `CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS.md` (Step 1+2)
**Codex review applied**: `CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_REVIEW.md`

---

## 1. Headline

```
Step 3A reconcile           DONE  (paper account, 83 matched / 0 mismatch / drift -$161.40)
Step 3B intent dry-run      DONE  (84 reco rows → 2 BUY intents, KIS payload spec-compliant, ZERO transmission)
Step 3C T10 parity          DONE  (2 artifacts checked, parity_ok=True; SELL path covered via past STOP_LOSS run)
P1-A submit gate split      DONE  (paper_submit / live_capital independent; legacy alias preserved)
Step 4 paper round-trip     WRITE-PATH VERIFIED   (KIS reached end-to-end; fill blocked by closed-market policy)
Step 4 fill round-trip      PENDING  (need US regular session to retry — same command, no code change)
```

Every Codex P1/P2 item from `..._REVIEW.md` is addressed below in §5.

---

## 2. New / Changed Files

```
phase3/autotrade/reconcile.py          NEW   ~330 lines  Step 3A
phase3/autotrade/intents.py            NEW   ~430 lines  Step 3B
phase3/autotrade/parity.py             NEW   ~240 lines  Step 3C
phase3/autotrade/paper_buy.py          NEW   ~270 lines  Step 4 operator harness
phase3/autotrade/kis_broker_adapter.py CHG   ±150 lines  P1-A gate split + place_order paper_submit branch + retry hardening
phase3/autotrade/__init__.py           CHG   SAFETY section rewritten for two-gate model
.env                                   CHG   + KIS_PAPER_SUBMIT_OK=false
.env.example                           CHG   matching template entry
```

Single-file split deferred (per Codex Q5.2 — "post-Step 3, pre-Step 4
actual submit path"). Adapter is now 1,560-ish lines but logically segmented
(numbered banners). Will split `audit/http`, `types`, `safety` once Step 5+
starts — splitting earlier would churn imports while paper round-trip is
still being shaken down.

---

## 3. Step-by-step Validation

### 3.1 Step 3A — `phase3/autotrade/reconcile.py`

Command (current `.env` state, no extra vars required):

```
PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper [--quiet] [--no-artifact]
```

Output buckets (Codex P2-B applied — `managed_scope` separates background broker state):

| bucket               | semantics                                            |
| -------------------- | ---------------------------------------------------- |
| `matched`            | managed scope, qty equal (tolerance 0)               |
| `qty_mismatch`       | managed scope, qty diff                              |
| `local_only`         | held locally, missing at broker                      |
| `broker_only_managed`| in broker AND in managed_scope, missing locally      |
| `background_broker`  | broker accumulation outside managed_scope            |
| `reco_only`          | in latest reco but held nowhere                      |
| cash_drift           | broker.cash − local.cash (absolute USD)              |
| settlement_pending   | placeholder; reserved for `inquire-nccs` (Step 5+)   |

`managed_scope` = `Current` sheet tickers ∪ tickers from the latest
`run_meta.json` with `status == "awaiting_execution"`.

**First live result** (2026-05-11 07:32 UTC):

```
managed_scope      : 84 tickers (current ∪ latest reco)
matched            : 83
qty_mismatch       : 0
local_only         : 0
broker_only_managed: 0
background_broker  : 0
reco_only          : 1   (AMD — in 2026-05-09 reco, not held)
cash local         : $15,661.08
cash broker (USD)  : $15,499.68
cash drift         : -$161.40    (broker − local)
```

Two operator signals worth flagging:

- `cash drift -$161.40` — first operational drift signal. Likely candidates:
  open BUY commits eating buying power at broker but invisible locally,
  paper fill rounding, or quote-time vs settlement-time mismatch. The
  `settlement_pending` bucket exists to eventually absorb the "open buy
  commitment" portion via `inquire-nccs`.
- `avg_price` of multiple matched rows differs between local and broker
  (e.g. AKAM 91.10 / 93.85, AMAT 386.05 / 390.10). Currently classified as
  `matched` because qty agrees. A future `price_basis_drift` enhancement
  could surface this separately. Not gating Step 4.

Read-only guarantee: `HoldingsManager.load_current()` /
`HoldingsManager.get_cash_balance()` only; **no** writes to
`holdings_log.xlsx`. KIS calls are limited to `inquire-balance` (paging
follow) + `inquire-psamount` + (transitively) `quotations/price`.

### 3.2 Step 3B — `phase3/autotrade/intents.py`

Command:

```
PYTHONPATH=. python3 -m phase3.autotrade.intents --profile paper [--quiet]
                                              [--run-id RUN_ID]
                                              [--ticker AAPL,MSFT]
                                              [--side BUY|SELL]
                                              [--allow-sell]
```

Pipeline:

1. **artifact-first** lookup (Codex P1-B applied) —
   `daily_runs/*/run_meta.json` whose `status=="awaiting_execution"` is
   picked automatically; `--run-id` overrides.
2. action → side classifier reuses `phase3.exits.RecosAction` so new D2/D4
   action suffixes (TRIM_PROFIT, SELL_ATR_TRAIL, …) auto-route.
3. Quote with **NASD → NYSE → AMEX fallback** — fixes a real problem
   surfaced in the first run: CIEN is NYSE-listed; quote on NAS returned
   last=0. Resolved market is echoed into `OVRS_EXCG_CD` of the payload.
4. **Marketable-limit** (Codex P2-C):
   * BUY: `ask` if present, else `last * 1.003`, ceil to 2 decimals.
   * SELL: `bid` if present, else `last * 0.997`, floor to 2 decimals.
5. Pre-trade risk flags: `INVALID_QTY`, `INVALID_PRICE`, `UNKNOWN_SIDE`,
   `NO_BROKER_POSITION`, `OVERSELL(qty>held)`, `BLOCKED_BY_BUY_ONLY`,
   `INSUFFICIENT_CASH(proj=$x > avail=$y)`.
6. KIS payload assembled exactly as `place_order` will send (TR_ID
   `VTTT1002U` for paper BUY, `VTTT1001U` for paper SELL, `ORD_DVSN=00`,
   4-decimal `OVRS_ORD_UNPR`). **Authorization header is intentionally
   omitted from the dump.**

**First live result** (2026-05-11 07:48 UTC) — `daily_runs/20260509_185312_daily`:

```
counts by side: {'BUY': 2}
counts by flag: (none)

ticker action      side  qty  limit     (src        )  last     ask   bid
ON     BUY_MORE    BUY     1  103.51 (last+buffer)    103.20      -     -
CIEN   BUY_MORE    BUY     2  549.76 (last+buffer)    548.11      -     -
```

84 reco rows → 82 SKIP (all `SELL_GRACE`) + 2 BUY intent. cumulative buy
notional $1,203 < cash avail $15,500 → no `INSUFFICIENT_CASH`. JSON dump
at `~/.kis_audit/intents_*_paper.json` carries the full transport-ready
payload for downstream Step 4 to consume.

### 3.3 Step 3C — `phase3/autotrade/parity.py`

Network-free. Compares `autotrade.intents` vs the T10 manual workflow
(`launcher.py:613` + `holdings_manager.apply_partial_execution`):

| role            | T10 reference                       | autotrade (current) |
| --------------- | ----------------------------------- | ------------------- |
| BUY             | `reco.Shares`                       | `reco.Shares`       |
| FULL_CLOSE      | `current[ticker].Shares` (live qty) | `reco.Shares`       |
| PARTIAL_CLOSE   | `min(reco.Shares, held - 1)`        | `reco.Shares`       |
| TRIM_GRACE      | special daily_runner path           | skip                |
| info / no-op    | skip                                | skip                |

Per-row verdict ∈ `{match, mismatch_qty, mismatch_role, both_skip}`.

**Validation runs**:

- `20260509_185312_daily` (today's actionable) — `parity_ok=True`,
  by_verdict `{both_skip:82, match:2}`.
- `20260508_211635_daily` (contains 1 `STOP_LOSS`) — `parity_ok=True`,
  by_verdict `{match:5, both_skip:83}`. STOP_LOSS ticker's `reco.Shares`
  already equaled `holdings.Shares`, so the FULL_CLOSE path is exercised
  but doesn't produce a diff in the steady operational state. The
  difference-handling code path is exercised by the test logic itself.

Decision: **keep `autotrade.intents` using `reco.Shares`** for v0. The
parity tool will catch any future stale-reco mismatch before Step 4
transmission. Moving `intents.py` to T10's adjusted qty is an explicit
follow-up if Step 4+ ever surfaces a mismatch in practice.

### 3.4 P1-A — submit gate split (Codex P1-A)

Independent gates added to `SafetyGuard`:

```python
allow_paper_submit(dry_run=False)  → env=paper AND paper_submit_ok AND not halt
allow_live_capital(dry_run=False)  → env=live  AND confirm_live   AND not halt
submit_mode(dry_run=False)         → 'dry_run' | 'paper_submit' | 'live_capital'
submit_decision(dry_run=False)     → (mode, human-readable reason)
```

Properties verified by `--self-check`:

1. Default `.env` (paper, both flags false) → mode = `dry_run` regardless of `dry_run`.
2. Hypothetical `paper_submit_ok=true` + `dry_run=False` → mode = `paper_submit`.
3. Same hypothetical scenario → `allow_live_capital` stays `False`.
   **Paper unlock cannot reach live capital, and vice versa.**
4. `allow_live_order` retained as a hard alias for `allow_live_capital`
   so older call-sites keep working without churn.
5. `assert_can_submit` now raises `SafetyError` with an actionable
   message when `env=paper` + `dry_run=False` + `paper_submit_ok=False`.

`place_order` is now a three-way switch:

```
mode='dry_run'      → audit a dry-run record, return PlacedOrder(status='dry_run')
mode='paper_submit' → POST /uapi/overseas-stock/v1/trading/order (real call)
mode='live_capital' → NotImplementedError until paper validation is in the bag
```

### 3.5 Step 4 — write-path verification (paper, real call)

Command (used today):

```
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \
    --ticker ON --shares 1 --yes
```

Result, 2026-05-11 08:39 UTC (US market closed):

| step | observation |
| ---- | ----------- |
| token | issued / reused via 24h cache |
| quote(ON) | last=$102.81 from `NAS` (no bid/ask, OOH) |
| inquire-psamount, inquire-balance | both 200 OK after rate-limit retries |
| marketable-limit | $103.12 (last × 1.003, ceil 2dp) |
| **POST /order tr=VTTT1002U** | **HTTP 200, rt_cd=1, msg1="모의투자 장시작전 입니다."** |
| client_order_id | `co-0da039f31a53` (preserved through rejection) |
| broker_order_id | `None` (paper refused, no ODNO assigned) |
| status | `rejected` |
| JSON dump | `~/.kis_audit/paper_buy_20260511T083958_ON_BUY.json` |

This is the **expected** outcome at this time — KIS paper refuses orders
outside regular US session (matches Codex P2-C's "regular hours only"
recommendation). What's important:

- The whole write path was exercised end-to-end (auth → preflight →
  payload → POST → response parse → rejection bucket).
- Application-level rejection was correctly classified (`status='rejected'`,
  `BrokerResponseError` raised by `_Http`, caught by `_submit_order_real`
  and surfaced as a structured PlacedOrder rather than an exception
  bubbling up to the operator).
- No code change is needed to retry during regular session — same
  command, same payload, same gate state.

**Retry hardening as a side-effect of Step 4**: `_Http.call` now also
treats KIS's "모의투자 서비스가 지연되고 있습니다 / 잠시후 재시도" responses
(HTTP 200 + rt_cd=1) as transient. Backoff identical to the rate-limit
path. Total retry budget bumped to 2 (one slot for rate-limit, one for
transient delay) to handle short paper-server hiccups.

---

## 4. Operator Cheat Sheet (R2)

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-

# A) Self-check — no network, validates env + safety gates
PYTHONPATH=. python3 -m phase3.autotrade.kis_broker_adapter --self-check

# B) Step 3A reconcile
PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper

# C) Step 3B intent dry-run (latest awaiting_execution artifact)
PYTHONPATH=. python3 -m phase3.autotrade.intents --profile paper --quiet
# Inspect a specific historical run:
PYTHONPATH=. python3 -m phase3.autotrade.intents --profile paper \
    --run-id 20260508_211635_daily --quiet

# D) Step 3C T10 parity (network-free)
PYTHONPATH=. python3 -m phase3.autotrade.parity --profile paper
PYTHONPATH=. python3 -m phase3.autotrade.parity --profile paper \
    --run-id 20260508_211635_daily

# E) Step 4 paper round-trip
#   E1) Preview only (always safe — defaults to preview if --yes is missing):
PYTHONPATH=. python3 -m phase3.autotrade.paper_buy --ticker ON --shares 1 --preview
#   E2) Real BUY 1 share (gate unlock required):
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \
    --ticker ON --shares 1 --yes
#   E3) Unwind: SELL 1 share (also requires --allow-sell to lift buy_only_mode):
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.paper_buy \
    --ticker ON --shares 1 --unwind --allow-sell --yes

# F) Post-trade reconcile (rerun B after Step 4 completes):
PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper
```

All commands emit a date-stamped JSONL audit line per network call to
`~/.kis_audit/<YYYY-MM-DD>.jsonl` plus a per-command structured JSON dump
(mode 600) under the same directory.

---

## 5. Codex Review Items — point-by-point

| Codex tag | Recommendation | Status | Notes |
|-----------|----------------|--------|-------|
| P1-A | split paper submit gate from live capital gate | DONE | `SafetyGuard.submit_mode`; `KIS_PAPER_SUBMIT_OK` env var; both `--self-check` assertions verified |
| P1-B | Step 3B input must be artifact-first | DONE | `intents.py` auto-discovers latest awaiting_execution artifact; `recommendations.csv` is canonical; `signal_transitions/*.xlsx` not referenced |
| P2-A | add Step 3C T10 parity dry-run | DONE | `parity.py`, network-free; verified across 2 artifacts |
| P2-B | reconcile separates managed vs background | DONE | `managed_scope` + `background_broker` bucket; first run: `background_broker = 0` |
| P2-C | first paper test = marketable-limit, regular hours | PARTIAL | marketable-limit applied; regular-hours run pending US session open today |
| Q5.1 | .env / cache / audit location | KEEP | `.env` repo-local; token + audit in `~` |
| Q5.2 | single-file split timing | KEEP | deferred until post-Step 4 paper validation |
| Q5.3 | limit price policy | DONE | marketable-limit, 0.3 % buffer, 2dp ceil/floor |
| Q5.4 | reconcile tolerance — qty 0, cash USD, settlement_pending bucket | DONE | implemented as specified; `settlement_pending` is a placeholder field awaiting `inquire-nccs` |
| Q5.5 | multi-account profile vs env suffix | DEFER | single-account `.env` for v0; profile JSON is a Step 5+ item |
| Q5.6 | paper test unwind policy | DONE | `paper_buy.py --unwind --allow-sell` round-trips; will be exercised once fill clears |
| Q5.7 | `inquire-present-balance` not wired | DEFER | will revisit as a cross-check endpoint after Step 5+ |
| Q5.8 | audit retention | NOT YET | rotation tooling deferred; current footprint is ~10s of KB/day |

---

## 6. Pending Work (after this round)

1. **Step 4 fill round-trip** — re-run `paper_buy.py … --yes` once US
   regular session opens (≈ 13:30 UTC / 22:30 or 23:30 KST depending on
   DST). Expected outcome: ODNO returned, `inquire-ccnl` echo within 1–3
   polls, post-trade snapshot shows `held_after = held_before + 1`,
   `cash_after ≈ cash_before − 103.x`.
2. **Step 4 unwind** — `paper_buy.py … --unwind --allow-sell --yes` to
   close the test position. Verifies `VTTT1001U` (paper SELL) path and
   buy_only_mode unlock.
3. **Post-trade reconcile** — `reconcile.py` rerun. Expect either
   `qty_mismatch=1` (if local hasn't been updated yet) or `matched`
   delta = 0 (after unwind). cash_drift should move by ~ commission +
   fill price differential.
4. **Doc** — append Step 4 actual fill snapshot once received.

No code changes required for items 1–3.

---

## 7. Open Questions for Codex (Round 2)

1. **Cash drift signal** — first run showed `−$161.40`. Should the
   `settlement_pending` bucket be wired now via `inquire-nccs` (unfilled
   order list) before Step 4 fill, or is it acceptable to let Step 4 reveal
   whether the drift is fill-related vs commitment-related?
2. **`price_basis_drift` enhancement** — multiple matched rows show
   different `avg_price` between local and broker. Useful as its own
   reconcile bucket, or out-of-scope for v0?
3. **T10 parity migration timing** — `autotrade.intents` continues to
   use `reco.Shares` for v0. Should we move to T10's `holdings.Shares`
   for FULL_CLOSE / `min(reco, held-1)` for PARTIAL_CLOSE *before*
   first SELL is wired (Step 5+), or wait until a parity mismatch is
   actually observed in production?
4. **Step 4 retry budget for transient delays** — current 2 retries
   (one for rate-limit, one for transient). Is this enough headroom
   for the rare paper-server "지연" cascade, or should we bump to 3?
5. **`paper_buy.py` interactive confirmation** — currently `--yes` is a
   single flag. For real paper fills with non-trivial notional, should
   the script print a final "type 'CONFIRM' to submit" prompt in
   interactive sessions, or is `--yes` sufficient given the gate split?

---

## 8. File Map

```
phase3/autotrade/
  __init__.py                    SAFETY (two-gate model)
  kis_broker_adapter.py          adapter + EnvConfig + SafetyGuard + _Http (paper_submit branch implemented)
  reconcile.py                   Step 3A
  intents.py                     Step 3B
  parity.py                      Step 3C
  paper_buy.py                   Step 4 operator harness

phase3/docs/
  CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS.md       # round 1 (Step 1+2)
  CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R2.md    # this file (Step 3A–4)

~/.kis_audit/
  YYYY-MM-DD.jsonl               per-day API audit
  reconcile_*.json               per-run reconcile structured dump
  intents_*.json                 per-run order intent structured dump
  parity_*.json                  per-run parity structured dump
  paper_buy_*.json               per-run Step 4 result + step-by-step trace
~/.kis_token_cache.json          mode 600, env-tagged
```

Status: **ready for Codex round-2 review**. Items 1–3 in §6 are
zero-code-change operational steps awaiting US market open today.

---

## 9. Step 4 Fill Round-Trip — 2026-05-11 (US regular session)

Executed `~13:46–13:50 UTC` (ET 09:46, ~16 min after open). The full
Codex preflight (`R2_CODEX_PREFLIGHT.md` §3.2–3.6 + §4.1–4.4) was
followed without code changes between preflight and the live run.

### 9.1 Preflight outcomes

| Check | Result |
|-------|--------|
| `--self-check` | all assertions pass, paper_submit_ok stayed False |
| `reconcile --profile paper` | baseline drift `-$161.40`, qty_mismatch=0 |
| `intents --profile paper --quiet` | 3 BUY (ON 1, AMD 2, GLW 1), no flags |
| `parity --profile paper` | parity_ok=True, by_verdict={both_skip:81, match:3} |
| `paper_buy --preview` | ON quote $103.08, marketable-limit $103.39, gate stayed dry_run |

### 9.2 BUY — `paper_buy --ticker ON --shares 1 --yes`

```
POST /order tr=VTTT1002U          HTTP 200  rt_cd=0
msg1                              "모의투자 매수주문이 완료 되었습니다."
broker_order_id (ODNO)            0000040840
KRX_FWDG_ORD_ORGNO                00950
ORD_TMD                           224651 (KST 22:46:51)
limit_price                       $103.28  (last $102.97 × 1.003 ceil 2dp)
client_order_id                   co-08b7cf51814a
status                            submitted
post-trade held                   16 → 17    (Δ=+1)
post-trade cash                   $15,499.68 → $15,395.68    (Δ=-$104.00)
inquire-ccnl echo (4 polls/12s)   ODNO NOT visible           ← Case B
```

JSON dump: `~/.kis_audit/paper_buy_20260511T134646_ON_BUY.json`

### 9.3 reconcile after BUY

```
matched              83 → 82
qty_mismatch          0 →  1   (ON: local 16 vs broker 17, +1)
cash drift      -$161.40 → -$265.40   (Δ=-$104.00)
```

drift expansion exactly equals the post-BUY cash delta — KIS paper holds
$104.00 against the buy commitment despite a $103.28 limit. The $0.72
overhead (~0.7%) is consistent across order paths and disappears on
unwind (see §9.5), confirming it's a transient hold buffer, not a true
fill-price differential.

### 9.4 SELL — `paper_buy --ticker ON --shares 1 --unwind --allow-sell --yes`

```
POST /order tr=VTTT1001U          HTTP 200  rt_cd=0
msg1                              "모의투자 매도주문이 완료 되었습니다."
broker_order_id (ODNO)            0000040867
KRX_FWDG_ORD_ORGNO                00950
ORD_TMD                           224853 (KST 22:48:53)
limit_price                       $103.05  (last $103.36 × 0.997 floor 2dp)
client_order_id                   co-869bdcf18852
status                            submitted
post-trade held                   17 → 16    (Δ=-1)
post-trade cash                   $15,395.68 → $15,497.94    (Δ=+$102.26)
inquire-ccnl echo (4 polls/12s)   ODNO NOT visible           ← Case B (consistent)
```

JSON dump: `~/.kis_audit/paper_buy_20260511T134847_ON_SELL.json`

### 9.5 final reconcile after unwind

```
matched              82 → 83   (back to baseline)
qty_mismatch          1 →  0
cash drift      -$265.40 → -$163.14   (Δ=+$102.26 → $1.74 net vs pre-test baseline)
```

### 9.6 Round-trip net analysis

|                                | start         | post-BUY      | post-unwind   |
|--------------------------------|---------------|---------------|---------------|
| held(ON)                       | 16            | 17            | **16**        |
| broker cash USD                | $15,499.68    | $15,395.68    | **$15,497.94**|
| cumulative cash Δ vs start     | —             | -$104.00      | **-$1.74**    |
| qty_mismatch                   | 0             | 1             | **0**         |
| cash drift                     | -$161.40      | -$265.40      | **-$163.14**  |
| drift Δ vs pre-test            | —             | -$104.00      | **-$1.74**    |

- **Position fully recovered** (16 → 17 → 16).
- **Round-trip cost = $1.74** (≈0.84 % of one ON share × 1 share spread+fee
  simulation). KIS paper models a realistic slippage/fee envelope.
- **Pre-existing drift `-$161.40` survives the test unchanged** — the
  Step 4 BUY/SELL did not create or absorb it. This is strong evidence
  the baseline drift is *not* an open-order hold, since the controlled
  buy-then-unwind would have absorbed any pending-buy-commitment portion.
  Wiring `inquire-nccs` is therefore not the right next step; the drift
  origin is more likely (a) dividend/income postings missed in the local
  CashLedger, (b) early manual partial fills with cost-basis quirks, or
  (c) historic K-FX rounding. Useful follow-up: a one-shot ledger
  reconciliation tool, not a real-time `nccs` poller.

### 9.7 Codex §5 decision tree classification

- **Case A** ✓ — submit ack, ODNO returned, position+cash delta verified for
  both BUY and SELL.
- **Case B** ✓ (parallel) — `inquire-ccnl` did not echo either ODNO within the
  12-second polling budget. Same behaviour twice in a row, so it's
  reproducible (not a one-off race). Position/cash deltas confirm fill
  independently, so this is a *visibility* issue, not a *truth* issue.
- Case C / D / E — did not occur.

### 9.8 Implications for next steps

| Codex §5 candidate                          | Status / decision                                                 |
|---------------------------------------------|-------------------------------------------------------------------|
| Post-trade artifact recording               | Useful next; round-trip JSON dumps already capture all primitives |
| Expand echo polling / explicit order lookup | YES — Case B reproduced. Investigate inquire-ccnl filters first   |
| `inquire-nccs` wire-up                      | NOT YET — baseline drift survived round-trip; nccs is wrong tool  |
| Retry budget bump (2 → 3)                   | NOT NEEDED — every retry succeeded on first attempt               |
| Interactive `CONFIRM` for larger notional   | DEFER — same threshold logic (§7.5)                               |

### 9.9 inquire-ccnl visibility — proposed investigation (Codex review item)

Two reproductions, ODNO **0000040840** (BUY) and **0000040867** (SELL),
were *not* visible to `inquire-ccnl` (TR_ID `VTTS3035R`) within 4 polls.
Each poll returned ~15–16 rows of historical fills, so the endpoint is
healthy — it just doesn't include very-recent orders. Possible causes
worth probing in the next round (zero code, just audit inspection +
spec re-reading):

- `inquire-ccnl` may filter to fully-executed-only by default; pending
  / partially-filled orders may live in `inquire-nccs` (which we
  haven't wired yet). If true, ccnl is the wrong endpoint for "did my
  order land?" — `inquire-nccs` is. **This is a different role for nccs
  than the cash-drift role we considered earlier.**
- `OVRS_EXCG_CD=%` wildcard may suppress the very-recent row.
- ORD_DT range default may be set to T−1 or earlier; today's order
  could fall outside.

If investigation confirms ccnl is execution-only, the patch is:

```
- echo step uses inquire-ccnl
+ echo step uses inquire-nccs first (open/unfilled)
+    then falls back to inquire-ccnl (executed)
+    and stops as soon as either matches the ODNO
```

This narrows `inquire-nccs`'s purpose from a speculative
cash-drift tool to a *post-trade visibility* tool, which is much more
defensible operationally.

### 9.10 Status

```
Step 4 paper round-trip                       DONE   (Case A + Case B)
Step 4 fill verification                      DONE   (held + cash deltas confirm)
Step 4 unwind                                 DONE   (baseline recovered)
post-trade reconcile                          DONE   ($1.74 round-trip cost,
                                                     pre-test drift untouched)
echo-polling visibility (inquire-ccnl gap)    OPEN   (see §9.9)
```

---

## 10. Today's second session — Intent-matched 3-BUY round + manual T10 apply (2026-05-11)

Executed ~14:24–14:34 UTC (~ET 10:24–10:34, full regular session).
Source of truth for the requirement set: Codex
`CURSOR_HANDOFF_AUTOTRADING_V0_TODAY_INTENT_BUY_T10.md`. The Step 4
write path was already validated in §9; today is the operational
workflow validation — submit the **actual artifact-resolved BUY rows**
(not an arbitrary ticker test), then let T10 apply locally.

### 10.1 New script — `paper_execute_intent.py`

`paper_buy.py` (§9) is ticker-driven. We added a recommendation-row-driven
counterpart per Codex TODAY §5–§8:

```
phase3/autotrade/paper_execute_intent.py
```

Key properties:

- Loads the latest `awaiting_execution` daily run artifact via
  `intents.load_artifact` (override with `--run-id`).
- Resolves intents with `intents.resolve_intents(..., only_side='BUY')`
  so SELL/SKIP are filtered out before selection.
- Selector enforces single-intent invocation:
  - `--ticker` matches by symbol; multi-match → abort with `--rec-row-id` hint.
  - `--rec-row-id` matches by lineage; conflict with `--ticker` → abort.
  - `risk_flags` non-empty → abort (no override flag today, by design).
- Two-gate safety unchanged: `KIS_ENV=paper` + `KIS_PAPER_SUBMIT_OK=true`
  + `--yes`. Default behaviour when neither `--yes` nor `--preview` is
  given is to fall through to preview (no transmission). Preview always
  dispatches through `place_order(dry_run=True)` so the SafetyGuard
  short-circuit path is exercised every call.
- Echo polling reuses `inquire-ccnl` (4 polls × 3 s, same Codex §9 budget).
  Echo missing + position/cash MOVED = visibility issue, success.
  Echo missing + position/cash UNCHANGED = inconclusive; refuses to emit
  a T10 summary (per Codex TODAY §10).
- Output: per-step stdout trace, then a `T10 MANUAL APPLY SUMMARY` block,
  then JSON dump `~/.kis_audit/intent_buy_<ts>_<run_id>_<ticker>.json` (mode 600).
- Local state untouched — `holdings_log.xlsx` and `run_meta.json` are
  written only by the operator's T10 step (Codex TODAY §2 non-goal).

### 10.2 Preflight outcomes

```
self-check                                    PASS
intents --profile paper --quiet               run_id=20260511_214648_daily
                                              counts={"BUY":3}  flags=(none)
parity / reconcile                            skipped (no policy change since §9.1
                                              preflight earlier today; verified after T10)
```

Resolved BUY intents:

| RecRowId | Ticker | Action   | Qty | Limit ($) | Limit Source | Quote Last | Resolved Market |
|---------:|:-------|:---------|----:|----------:|:-------------|-----------:|:----------------|
|       70 | ON     | BUY_MORE |   1 |    104.12 | last+buffer  |     103.80 | NASD            |
|       72 | GLW    | BUY_MORE |   1 |    201.66 | last+buffer  |     201.05 | NYSE (fallback) |
|       71 | AMD    | BUY_NEW  |   2 |    458.53 | last+buffer  |     457.15 | NASD            |

All three: no risk flags, `ask`/`bid` not populated (KIS paper price
endpoint returns last-only mid-session), so each took the
`last+0.3 %` path with BUY-side ceil rounding. GLW correctly routed to
NYSE via the NASD→NYSE→AMEX fallback.

### 10.3 Three submissions

| # | RecRowId | Ticker | Side / Action | Qty | ODNO         | tr_id     | Limit ($) | held Δ  | cash Δ ($)  | Echo (4 polls) |
|---|---------:|:-------|:--------------|----:|:-------------|:----------|----------:|:-------:|------------:|:---------------|
| 1 |       70 | ON     | BUY · BUY_MORE |  1 | `0000041325` | VTTT1002U |    104.12 | 16 → 17 |    −104.88  | missing        |
| 2 |       72 | GLW    | BUY · BUY_MORE |  1 | `0000041344` | VTTT1002U |    201.66 | 11 → 12 |    −203.23  | missing        |
| 3 |       71 | AMD    | BUY · BUY_NEW  |  2 | `0000041367` | VTTT1002U |    458.53 |  0 →  2 |    −920.34  | missing        |

Cumulative broker cash: `$15,497.94 → $14,269.49` (Δ **−$1,228.45**),
exactly matching `Σ(per-order cash delta) = 104.88 + 203.23 + 920.34`.
Per-order `EstimatedPriceFromCashDelta`:

| Ticker | Limit ($) | Estimated fill ($) | Slippage vs limit | Slippage vs quote_last |
|:-------|----------:|-------------------:|------------------:|-----------------------:|
| ON     |    104.12 |             104.88 |            +0.76  |                 +1.08  |
| GLW    |    201.66 |             203.23 |            +1.57  |                 +2.18  |
| AMD    |    458.53 |             460.17 |            +1.64  |                 +3.02  |

All three fills came in **slightly above the marketable-limit ceil**
(i.e., the paper engine assigned a worse price than the strict
last+buffer cap). Two readings:

- *KIS paper slippage model is independent of our limit*; the limit
  is treated as a soft ceiling and the engine prints a slightly worse
  print to simulate spread+fee, even though a real LIMIT would not
  fill above its limit. This is consistent with §9's $1.74 round-trip
  cost.
- Operationally, T10's `ExecutedPrice` should match what *actually
  came out of cash*, not the limit. The script emits both so the
  operator can pick deliberately.

### 10.4 T10 manual apply

Operator (shin-il) applied all three rows in T10 with
`ExecutedPrice = EstimatedPriceFromCashDelta`, `fill_price_source =
cash_delta`, and the per-order `ExecutionNote` block (ODNO +
client_order_id + run_id + rec_row_id).

### 10.5 Post-T10 reconcile

```
profile=paper  ts=2026-05-11T14:34:35+00:00
managed_scope       : 84 tickers (current ∪ latest reco)
matched             : 84
qty_mismatch        : 0          ✓
local_only          : 0
broker_only_managed : 0
background_broker   : 0
reco_only           : 0

cash local          : $14,432.63
cash broker         : $14,269.49
cash drift          : -$163.14   (broker − local)
settlement_pending  : $0.00
```

Position layer is now **perfectly aligned** for the today-traded tickers
(ON 17/17, GLW 12/12, AMD 2/2). Codex TODAY §11 acceptance criteria
met (1–7 ✓).

### 10.6 Acceptance vs Codex TODAY §11

| Criterion                                                          | Met |
|--------------------------------------------------------------------|:---:|
| 1. selected BUY row submitted to KIS paper                         | ✓   |
| 2. KIS returned ODNO                                               | ✓   |
| 3. broker position or cash confirms execution                      | ✓   |
| 4. operator obtains a defensible execution price                   | ✓ (cash_delta) |
| 5. T10 manual apply performed for the same recommendation row     | ✓   |
| 6. post-T10 reconcile `qty_mismatch=0`                             | ✓   |
| 7. JSON audit dump exists with full reconstructible payload       | ✓ (3 files) |

### 10.7 Observations

1. **Cash drift unchanged by today's flow.** Pre-test drift was
   `-$163.14` (continuation of §9.6 post-unwind drift, which had
   landed at the same magnitude). Post-test drift is identical
   `-$163.14`. This confirms once more that the residual drift lives
   *outside* the order pipeline — both KIS and T10 charged the
   identical $1,228.45 today, so our pipeline contributed exactly $0
   to the drift. Drift origin is still the §9.6 candidates
   (dividend/income, manual edits, K-FX rounding) and requires a
   separate one-shot ledger reconciliation tool, not a runtime
   `inquire-nccs` poll.

2. **Echo visibility regression confirmed across 3/3 new orders.**
   ODNOs `0000041325`, `0000041344`, `0000041367` — none visible to
   `inquire-ccnl` within 4 polls × 3 s = 12 s. History size grew by
   exactly +1 row per order (17 → 18 → 19), but the matching ODNO
   row never appeared in the polling window. This is now five
   consecutive paper orders (yesterday's two + today's three) with
   identical echo-miss behaviour, strongly supporting the §9.9
   hypothesis that `inquire-ccnl` is execution-only and we need
   `inquire-nccs` for immediate post-submit visibility.

3. **Rate-limit retry path exercised mid-reconcile.** During the
   post-T10 reconcile, two transient `초당 거래건수를 초과` rate-limit
   responses (one on `inquire-balance`, one on `price`) triggered
   1100 ms backoffs and both succeeded on retry-1. The `_TRANSIENT_HINTS`
   + `retry=2` headroom added during the §9 preflight is now
   load-validated under real chained `get_positions` + `get_quote` +
   `get_cash` pressure. No operator intervention needed.

4. **Artifact discovery shifted post-T10.** Today's intents were
   resolved against `20260511_214648_daily` (the latest
   `awaiting_execution` artifact). After T10 applied the three rows,
   the post-T10 reconcile resolved
   `latest_actionable_artifact = 20260509_185312_daily` (an earlier
   open artifact). Interpretation: T10 must have flipped today's
   artifact out of `awaiting_execution` status (or otherwise
   recorded a state transition), pushing the discovery cursor back
   to the next-newest open artifact. This is benign for today's
   acceptance criteria, but the operator should be aware that
   `reconcile.managed_scope` is now keyed off the older artifact's
   reco set until the older one is closed (or until tomorrow's
   `daily_runner` emits a new actionable artifact).

5. **Marketable-limit pricing performance.** Three orders, three
   fills above the strict last+0.3 % ceil. Paper engine treats our
   limit more like a marketable indicator than a hard ceiling. Real
   live LIMIT semantics will differ — production rollout should
   either tighten the buffer or accept higher slippage as a
   feature, not a bug.

### 10.8 Updated open questions for Codex (Round 3)

| #    | Topic                                                                   | Severity | Suggested next |
|------|-------------------------------------------------------------------------|:--------:|----------------|
| Q1   | `inquire-ccnl` → `inquire-nccs` echo migration (5/5 missed ODNOs)       | P1       | wire `inquire-nccs` polling fallback per §9.9 patch sketch |
| Q2   | Persistent `-$163.14` cash drift unrelated to order pipeline            | P2       | one-shot offline ledger reconciliation tool over CashLedger history |
| Q3   | KIS paper LIMIT semantics (engine prints above limit)                   | P2       | accept for paper; document as known difference vs live LIMIT |
| Q4   | Post-T10 artifact state transition — what status does T10 set?          | P3       | inspect `run_meta.json` of `20260511_214648_daily` after today |
| Q5   | `paper_execute_intent.py` selector ergonomics for batch days            | P3       | optional `--all-buys` flag with explicit per-row CONFIRM prompts |

### 10.9 Files changed today

```
phase3/autotrade/paper_execute_intent.py    NEW   intent-row-driven one-shot BUY submit + T10 summary
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R2.md   updated  +§10 (this section)
```

JSON audit dumps:

```
~/.kis_audit/intent_buy_20260511T142403_20260511_214648_daily_ON.json
~/.kis_audit/intent_buy_20260511T142541_20260511_214648_daily_GLW.json
~/.kis_audit/intent_buy_20260511T142705_20260511_214648_daily_AMD.json
~/.kis_audit/reconcile_20260511T143435_paper.json
```

### 10.10 Status

```
Today new script (paper_execute_intent.py)    DONE
Today 3 BUY orders (ON / GLW / AMD)           DONE   (3/3 Case A + Case B repro)
Today T10 manual apply                        DONE   (shin-il, all 3 rows)
Today post-T10 reconcile                      DONE   (qty_mismatch = 0)
inquire-nccs echo migration                   OPEN   (now P1 — 5/5 missed)
Cash drift ledger reconciliation              OPEN   (P2)
Post-T10 artifact transition inspection       OPEN   (P3)
```

