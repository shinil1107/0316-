# Autotrading v0 — Round 6 Progress (First Paper Submit)

Date: 2026-05-15 (KST evening, US regular session OPEN)
Mode: KIS paper (`KIS_ENV=paper`, `KIS_PAPER_SUBMIT_OK=true`)
Branch state: R5A + R5B safety stack applied, 14 / 14 prior acceptance tests
green going in.

This round was the first time the orchestrator actually wrote to KIS. It
surfaced one new P1 bug (`echo` ODNO normalization), confirmed the entire
R5B safety stack works under live paper conditions, and produced a clean
T10-ready broker truth.

---

## 1. Run inputs

```
artifact run_id   : 20260515_191533_daily  (today 19:15 KST, status=awaiting_execution)
reco rows         : 88 total
BUY rows          : 3   (APA, AMD, INTC — all BUY_MORE)
preflight (dry)   : finalize_status=completed, rc=0, risk_flags=none, total notional ≈ $904
caps used (live)  : --max-orders 3  --max-notional-per-run 10000  --max-notional-per-order 5000
autotrade_run_id  : at-20260515T134752Z-330c
elapsed           : 75.17 s
```

Command:

```bash
KIS_PAPER_SUBMIT_OK=true PYTHONPATH=. python3 -m phase3.autotrade.orchestrator run \
    --paper --run-id 20260515_191533_daily --submit \
    --max-orders 3 --max-notional-per-run 10000 --max-notional-per-order 5000 --quiet
```

---

## 2. Per-intent outcomes — orchestrator vs broker truth

### As reported by the orchestrator (pre-fix)

| RecRow | Ticker | Qty | Limit | ODNO | PosΔ | CashΔ | resolved state |
|---|---|---|---|---|---|---|---|
| 74 | APA | 4 | $37.92 | `0000041461` | **+4** | −$152.77 | **filled** (paper_cash_delta @ $38.1925) |
| 75 | AMD | 1 | $430.82 | `0000041467` | **+0** | −$435.13 | **unknown** |
| 76 | INTC | 3 | $107.80 | `0000041471` | **+0** | −$326.63 | **unknown** |

`counts = {filled: 1, _submitted_total: 3, unknown: 2}`, `finalize_status = completed`, `rc = 0`.

### Broker truth (verified post-run)

`inquire-ccnl` (issued directly with the same adapter, fix applied):

| ODNO | Ticker | ord_qty | ccld_qty | ord_unpr | **ccld_unpr** |
|---|---|---|---|---|---|
| `0000041461` | APA  | 4 | 4 | $37.92  | **$37.815**  |
| `0000041467` | AMD  | 1 | 1 | $430.82 | **$430.795** |
| `0000041471` | INTC | 3 | 3 | $107.80 | **$107.80**  |

`inquire-nccs` after the run: 0 rows ⇒ no open orders remain. Position
re-query (~3 min later) showed AMD 2→3 and INTC 30→33, confirming the
fills did land — there was a position-API lag at the moment the
orchestrator took its post snapshot.

So **all three orders filled fully**. Two of them were misclassified
as UNKNOWN because the resolver could not match the ODNO across
endpoints (see §3 below).

### Cost reconciliation

```
APA   4 × $37.815  = $151.26
AMD   1 × $430.795 = $430.80
INTC  3 × $107.80  = $323.40
                    ───────
ccld subtotal      = $905.46
cash delta         = $914.53
diff (fees/FX)     = $9.07  (~1.00%)
```

KIS paper appears to apply a flat-ish ~1% overhead on USD buys. Worth
capturing in fill_resolver later (R7+) but not blocking today.

---

## 3. P1 bug discovered & fixed — ODNO normalization

### Symptom
Two of three real fills surfaced as `state = UNKNOWN` despite cash deltas
that exactly matched the limit notionals. Re-running diagnostics showed
`echo_poll → nccs/ccnl` both reported zero matches for the live ODNOs.

### Root cause
KIS overseas returns the same ODNO in two different surface forms:

* `place_order` response  →  `"0000041461"`   (zero-padded, 10 chars)
* `inquire-ccnl` rows      →  `"41461"`        (leading zeros stripped)
* `inquire-nccs` rows      →  same, leading zeros stripped

`echo.py` and `KisBrokerAdapter.find_open_order` both compared with plain
`str == str`, so every overseas ODNO would mismatch for as long as the
order remained in nccs/ccnl with the trimmed form. R3 had previously
attributed the same pattern to "paper marketable LIMIT fills before they
reach ccnl"; that hypothesis is wrong — the fills were always there,
just with a different ODNO surface form.

### Fix

* `phase3/autotrade/echo.py`
  * Introduced `_norm_odno(v: Any) -> str` (`str.strip().lstrip("0") or "0"`).
  * `_try_nccs` / `_try_ccnl` now normalize both the request ODNO and the
    candidate ODNO before comparing.
* `phase3/autotrade/kis_broker_adapter.py`
  * `find_open_order(broker_order_id, ...)` applies the same
    normalization on both sides.

### Live verification (post-fix)

```text
APA  odno=0000041461  matched=True  source=ccnl  (ord 4 / ccld 4 @ $37.815)
AMD  odno=0000041467  matched=True  source=ccnl  (ord 1 / ccld 1 @ $430.795)
INTC odno=0000041471  matched=True  source=ccnl  (ord 3 / ccld 3 @ $107.80)
```

All three ODNOs now match `echo_poll` immediately against the broker.

---

## 4. What the R5B safety stack did correctly today

Despite the misclassification:

* `--submit` required `--run-id` — preflight check stopped any accidental
  stale-artifact pickup (R5A P1-2). The fresh artifact was loaded
  explicitly.
* Deterministic client_order_ids were stamped on every event
  (`co-20260515-74-B-4-...`, etc.).
* History-aware duplicate guard (R5B P1-1) is in place: had the run
  re-attempted any of these three intents, `is_already_active()` would
  have skipped them because each has a `SUBMITTED` event in the JSONL,
  regardless of any later UNKNOWN tail event.
* The `try / finally` in `cmd_run` (R5A P1-3) generated all four
  artifacts (`autotrade_orders.jsonl`, `autotrade_summary.json`,
  `autotrade_execution_report.md / .json / .csv`) even though two
  intents resolved to UNKNOWN.
* `had_error` was *not* raised (R5B P1-2) — those intents went through a
  clean broker ack, an empty echo, and a deterministic resolver verdict.
  UNKNOWN here is a conservative classification, not a programming
  error. The run correctly finalized as `completed` / `rc=0`.

---

## 5. Acceptance tests

### New — `phase3/tests/test_r6_odno_normalize.py` (12 cases)

* `TestNormOdno` (4): zero-pad → raw, raw → raw, `"0"` preservation,
  empty/None handling, idempotence.
* `TestTryNccsPaddingMismatch` (3): nccs match across both ODNO surface
  forms, unrelated rows do not false-match.
* `TestTryCcnlPaddingMismatch` (2): same for ccnl.
* `TestEchoPollMatchesViaNccsThenCcnl` (3): full `echo_poll` succeeds via
  nccs (preferred) and via ccnl fallback, and stays `matched=False` when
  the ODNO is truly absent.

### Regression — all prior suites still green

```
phase3.tests.test_r5a_orchestrator_safety   →  5/5 OK
phase3.tests.test_r5b_pre_submit_safety     →  9/9 OK
phase3.tests.test_r6_odno_normalize         → 12/12 OK
                                              ─────
                                              26/26 OK
```

No production code other than `echo.py` and the one helper in
`kis_broker_adapter.py` was touched.

---

## 6. T10 application (operator → manual)

Use these three rows as the canonical T10 inputs for
`run_id = 20260515_191533_daily`:

| RecRow | Ticker | Action    | Qty | Fill Price | Notional |
|---|---|---|---|---|---|
| 74 | APA  | BUY_MORE | 4 | **$37.815**  | $151.26 |
| 75 | AMD  | BUY_MORE | 1 | **$430.795** | $430.80 |
| 76 | INTC | BUY_MORE | 3 | **$107.80**  | $323.40 |

* "Fill Price" is `ft_ccld_unpr3` straight from `inquire-ccnl`.
* "Notional" is qty × fill price (no fees).
* After T10 apply, mark the artifact `status = executed` so the next
  daily run won't re-pick it (R3 §3.2 ghost prevention).
* The orchestrator's own report files (`autotrade_execution_report.*`)
  in the artifact directory reflect the pre-fix view — leave them in
  place for the audit trail; do not retroactively overwrite. The
  authoritative source for T10 is the table above (verified via
  `inquire-ccnl` post-fix).

---

## 7. Files touched

```
phase3/autotrade/echo.py
phase3/autotrade/kis_broker_adapter.py
phase3/tests/test_r6_odno_normalize.py        (new, 12 cases)
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R6.md   (this)
```

No env / config / safety-gate changes. `.env` still has
`KIS_PAPER_SUBMIT_OK=false` by default; today's run set it inline.

---

## 8. Suggested next round (R7)

* Walk all today's ODNOs (and any from R3/R4 paper trials) through the
  resolver with the fix on, to confirm there are no other latent
  UNKNOWNs that should have been `FILLED`. Optional: a small
  `reclassify_run.py` operator helper that re-resolves a JSONL run by
  re-querying broker truth and appends corrective transitions —
  audit-only, no new orders.
* Account for the ~1% paper fee/FX observed today in `fill_resolver`
  (cash-derived fill price). Maybe expose it as a single
  `paper_fee_pct` setting so reconciliation doesn't drift.
* Discuss whether `auto_finalize_status` should be downgraded to
  `completed_with_unknowns` (distinct from `completed_with_errors`)
  when any per-intent state ends in `UNKNOWN`. Today's run finished as
  `completed`; an operator scanning the summary won't see the
  "investigate me" signal otherwise.
