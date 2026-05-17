# Autotrading v0 — Round 7-A Progress (T10 Applicator)

Date: 2026-05-15 (KST evening, US regular session OPEN)
Mode: KIS paper (`KIS_ENV=paper`)
Branch state going in: R5A + R5B + R6 (36 / 36 tests green).

## 0. Scope split for R7

R7 originally bundled two workstreams (see
`CURSOR_HANDOFF_AUTOTRADING_V0_NEXT_R7.md`):

| Workstream | Status |
|---|---|
| **A. T10 applicator** — broker-confirmed paper fills → `holdings_log.xlsx` | **this round** |
| **B. NCCS live probe + cancel** — deliberately non-marketable LIMIT, observe nccs, cancel | **deferred to R7-B** |

Reason for the split is Codex's own §8 recommendation: do applicator
first; only run the nccs probe once a paper cancel path is proven. Going
straight into B without a cancel helper would intentionally leave open
orders in the broker without a deterministic way to back out, which the
R7 §2 hard safety rules forbid.

R7-B will land in its own round once
`KisBrokerAdapter.cancel_order(...)` is implemented and either has its
own acceptance tests or an explicit "operator has KIS UI open" SOP.

## 1. Deliverables (Workstream A)

```
phase3/autotrade/t10_applicator.py             (new — 700 lines, CLI + apply pipeline)
phase3/tests/test_r7_t10_applicator.py         (new — 10 cases)
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R7.md   (this file)
```

No production code under `phase3/autotrade/*` outside the new module was
touched in R7-A. The applicator re-uses three proven primitives:

* `holdings_manager.HoldingsManager.apply_partial_execution`
* `holdings_manager.HoldingsManager.record_cash_event`
* `run_artifact.record_execution_artifact`

— the exact three functions the existing GUI T10 flow calls in
`launcher.py:_apply` (~line 962). The applicator is therefore a non-GUI
peer of T10, not a parallel rewrite.

## 2. CLI

```bash
# Dry-run (default; immutable on disk):
PYTHONPATH=. python3 -m phase3.autotrade.t10_applicator \
    --run-id 20260515_191533_daily --dry-run

# Real apply (requires both --apply and the env gate):
AUTOTRADE_T10_APPLY_OK=true PYTHONPATH=. python3 -m \
    phase3.autotrade.t10_applicator \
    --run-id 20260515_191533_daily --apply
```

Flags:

| Flag | Default | Behavior |
|---|---|---|
| `--run-id` | (required) | artifact id under `paths.output_dir/daily_runs/` |
| `--profile` | `paper` | only `paper` is accepted in R7-A (hard-stop on anything else) |
| `--dry-run` | on | mutex with `--apply` |
| `--apply` | off | also requires `AUTOTRADE_T10_APPLY_OK=true` |
| `--allow-partial` | off | apply rows where `ccld_qty < ord_qty` |
| `--allow-duplicate-apply` | off | debug; bypass `execution_applied.csv` guard |

## 3. Pipeline

```
recommendations.csv + autotrade_orders.jsonl + KIS inquire-ccnl
  → Resolution[] (per submitted intent)
  → policy filter (6 abort checks, BUY-only allowlist)
  → executed_df
  → [if --apply]
       HoldingsManager.apply_partial_execution(applied_df, trigger_type="AUTOTRADE")
       HoldingsManager.record_cash_event("BUY_MORE", -cost, "TKR Nsh ODNO=…")
       run_artifact.record_execution_artifact(run_dir, executed_df,
           source="AUTOTRADE", total_checkable_count=N, …)
  → always: autotrade_t10_apply_report.{md,json}
  → dry-run only: autotrade_t10_apply_preview.csv
```

Source of truth (per R7 §3.2): broker via `KisBrokerAdapter.get_order_history()`,
matched on `_norm_odno()` (R6 ODNO normalization helper). The pre-fix
`autotrade_execution_report.*` files in the artifact are explicitly
**not** read; that history is preserved as the orchestrator-side audit
trail.

## 4. Safety gates (per R7 §2)

| Rule | Enforcement |
|---|---|
| paper-only | `--profile` choices = `("paper",)`; hard-stop otherwise |
| no live route | KIS env is asserted paper via `cfg.is_paper` |
| no secrets | only env / config / .env paths used; nothing logged from `.env` |
| dry-run by default | `cmd_apply` flips to dry-run if neither flag is given |
| `--run-id` required | argparse `required=True` |
| no JSONL overwrite | applicator only reads `autotrade_orders.jsonl` |
| BUY-only | explicit `BUY_ACTIONS` allowlist; SELL/TRIM rows blocked |
| abort on ambiguity | missing ccnl, `ccld_qty=0`, partial w/o flag → abort whole batch |
| idempotent | `execution_applied.csv` duplicate `RecRowId` aborts |
| record before mutate | report writes happen for every code path, including aborts |

If even one submitted intent in the batch fails its policy check
(missing ccnl, zero fill, partial without flag, duplicate id, non-BUY
action), the **whole batch aborts** with `rc=1` (per R7 §3.2
"conservative apply"). The operator can then re-run with the
appropriate flag (`--allow-partial`, `--allow-duplicate-apply`) or fix
the artifact and try again.

## 5. Acceptance tests — `phase3/tests/test_r7_t10_applicator.py`

Synthetic `run_dir`s on tempfile + fake adapter + fake HoldingsManager +
fake artifact recorder. No network. No real Excel touched.

| # | Case | Result |
|---|---|---|
| 1 | ODNO normalization: padded place_order ODNO matches raw ccnl row | OK |
| 2 | Dry-run writes preview + reports, no mutation to disk / hm / recorder | OK |
| 3 | `--apply` without `AUTOTRADE_T10_APPLY_OK=true` returns rc=2 | OK |
| 4 | Pre-existing `RecRowId` in `execution_applied.csv` aborts | OK |
| 5 | Submitted ODNO missing from ccnl aborts batch | OK |
| 6 | `ccld_qty = 0` aborts batch | OK |
| 7a | Partial fill aborts by default | OK |
| 7b | Partial fill applies under `--allow-partial` | OK |
| 8 | Full-fill `--apply` mutates fake hm, records cash event, calls recorder, status flips to `executed` | OK |
| 9 | Mixed-checkable artifact (BUY + SELL) becomes `partially_executed` because R7-A only applies BUY | OK |

Regression:

```
test_r5a_orchestrator_safety   →  5 / 5
test_r5b_pre_submit_safety     →  9 / 9
test_r6_odno_normalize         → 12 / 12
test_r7_t10_applicator         → 10 / 10
                                 ───────
                                 36 / 36
```

## 6. Live validation against the R6 artifact

### Dry-run

```text
[t10_applicator] config       = …/phase3/config.yaml
[t10_applicator] holdings_log = …/output/holdings_log.xlsx
[t10_applicator] run_dir      = …/daily_runs/20260515_191533_daily
[t10_applicator] mode         = dry_run
[broker truth]
  APA   ODNO=0000041461 filled=4/4 price=37.8150  status=fully_filled
  AMD   ODNO=0000041467 filled=1/1 price=430.7950 status=fully_filled
  INTC  ODNO=0000041471 filled=3/3 price=107.8000 status=fully_filled
[would apply]
  BUY_MORE APA   4 @ 37.8150 (ODNO=0000041461)
  BUY_MORE AMD   1 @ 430.7950 (ODNO=0000041467)
  BUY_MORE INTC  3 @ 107.8000 (ODNO=0000041471)
```

Disk after dry-run: only `autotrade_t10_apply_preview.csv`,
`autotrade_t10_apply_report.md`, `autotrade_t10_apply_report.json`
appeared. `run_meta.status` stayed `awaiting_execution`. No
`execution_applied.csv`. `holdings_log.xlsx` mtime unchanged.

### Real apply

Pre-apply local snapshot:

```
APA  : 38 sh   AMD : 2 sh   INTC: 30 sh
cash : $13,369.97   portfolio_value: $97,735.91
```

Command and output:

```bash
AUTOTRADE_T10_APPLY_OK=true PYTHONPATH=. python3 -m \
    phase3.autotrade.t10_applicator \
    --run-id 20260515_191533_daily --apply
```

```text
[t10_applicator] mode = apply
[broker truth]                       (same as dry-run)
[t10_applicator] applied 3 broker-confirmed executions  source=AUTOTRADE
[t10_applicator] artifact status: awaiting_execution -> executed
[t10_applicator] cash_after=$12,464.51  total_after=$110,850.95
[t10_applicator] next: reconcile --run-id 20260515_191533_daily
```

Post-apply local snapshot:

```
APA  : 42 sh (+4)   AMD : 3 sh (+1)   INTC: 33 sh (+3)
cash : $12,464.51                     ($13,369.97 − $905.46)
total: $110,850.95
```

`$905.46 = 4 × 37.815 + 1 × 430.795 + 3 × 107.80` — matches broker truth
exactly.

### Reconcile (post-apply)

```text
PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper --quiet
```

```text
matched            : 84
qty_mismatch       : 0
local_only         : 0
broker_only_managed: 0
background_broker  : 0
reco_only          : 4  (DOW, GEV, LYB, MRNA — held nowhere, normal)

APA  42/42   AMD 3/3   INTC 33/33

cash local         : $12,464.51
cash broker (USD)  : $12,312.12
cash drift         : $-152.39   (broker − local)
```

* All target positions match broker exactly — applicator-confirmed
  fills are now the single source of truth for `holdings_log.xlsx`.
* `cash drift = -$152.39` includes ~$9.07 paper-fee residue from the
  R6 round (the orchestrator-time cash delta was $914.53 vs ccnl
  notional $905.46) plus prior baseline drift carried from earlier
  manual cycles. This is **out of scope for R7-A**; the fix is the
  R8 "paper fee model" item already on Codex's R7 §8 list.

## 7. Operator playbook (R7-A → ongoing daily flow)

```text
1. orchestrator submits today's BUY intents (R6 R5B safety stack).
2. inspect autotrade_summary.json and the broker (e.g. via R6 echo).
3. dry-run the applicator:
       PYTHONPATH=. python3 -m phase3.autotrade.t10_applicator \
           --run-id <today> --dry-run
   confirm: broker truth, would-apply rows, total notional.
4. if happy:
       AUTOTRADE_T10_APPLY_OK=true PYTHONPATH=. python3 -m \
           phase3.autotrade.t10_applicator --run-id <today> --apply
5. reconcile:
       PYTHONPATH=. python3 -m phase3.autotrade.reconcile --profile paper
   confirm qty_mismatch == 0.
6. (if mixed checkable rows other than BUY) finish manual T10 in the GUI
   for the remaining SELL/TRIM rows. The applicator already left the
   artifact at status="partially_executed" in that case.
```

R7-A does **not** auto-call the orchestrator or reconcile from inside
the applicator. Each step is operator-gated, by design.

## 8. Known gaps / follow-ups

| Item | Round | Notes |
|---|---|---|
| nccs live probe + paper cancel | R7-B | needs `cancel_order` adapter + KIS TR_ID verification |
| paper fee / FX model in cash leg | R8 | reconcile cash drift is the symptom |
| stale `awaiting_execution` artifacts (5/9 ghost, 5/14 untouched) | R8 | needs a tiny "mark expired" applicator or daily_runner change |
| optional JSONL corrective transitions for the R6 UNKNOWN rows | R8 | Codex §3.9; non-blocking, audit-only |
| SELL/TRIM autotrade path | future | applicator allowlist is BUY-only by design today |
| live (non-paper) route | not planned | no live profile in `--profile` choices |

## 9. Files touched

```text
phase3/autotrade/t10_applicator.py                            (new)
phase3/tests/test_r7_t10_applicator.py                        (new)
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R7.md      (this)
```

No env / config / safety-gate changes. `.env` still has
`KIS_PAPER_SUBMIT_OK=false` by default; today's apply set
`AUTOTRADE_T10_APPLY_OK=true` inline only.

---

## Appendix A — R7-B observational probe (2026-05-15 KST 23:38)

Run as a one-shot inline script (not a checked-in module). Intent was
to answer the single question that drove R7-B before building the full
nccs-probe / cancel scaffolding: *does KIS paper actually surface an
open BUY LIMIT in `inquire-nccs`?*

### Probe

```
symbol            : APA
qty               : 1 share
limit             : $18.85   (50% of last $37.71 — guaranteed non-marketable)
ODNO              : 0000042031  (raw "42031")
client_order_id   : r7b-probe-233847
```

### Observations

| Source | Behavior | Notes |
|---|---|---|
| `inquire-nccs` | **0 rows** | Same as R3 and R6 — never returned a row for any of our paper ODNOs, open or filled. |
| `inquire-ccnl` | **matched on attempt 1**, `source=ccnl` | R6 ODNO normalization is what made this work — pad-vs-raw comparison would still fail. |
| `ft_ord_qty3` | `1` | ordered |
| `ft_ccld_qty3` | **`0`** | not filled |
| `nccs_qty` | **`1`** | full quantity still outstanding |
| `ft_ccld_amt3` / `ft_ccld_unpr3` | `0` / `0` | clearly an open row, not a filled one |
| `rjct_rson` / `rjct_rson_name` | empty | accepted, not rejected |
| `prcs_stat_name` | empty | KIS paper does not populate this for live-open BUYs |
| `rvse_cncl_dvsn_name` | `보통` | "normal" → new order (not a modify/cancel) |
| position movement | APA 42 → 42 (Δ0) | order truly open, no fill yet |
| cash | $12,312.12 → $12,293.08 (Δ −$19.04) | broker reserved limit×qty + ~1% margin ($0.19 ≈ 1.01%) |

### Retest at T+4min (KST 23:43)

Operator wisely asked whether the empty nccs could be a polling delay.
Re-queried the same ODNO four minutes later, with the order
still open at the broker:

| Probe | `inquire-nccs` | `inquire-ccnl` row (ODNO 42031) | cash | position |
|---|---|---|---|---|
| T+0 s   (23:38) | **0 rows** | matched, `ft_ccld_qty='0'`, `nccs_qty='1'`, `ft_ccld_unpr3='0.00000000'` | $12,293.08 | APA 42 |
| T+295 s (23:43) | **0 rows** | matched, **byte-identical row** | $12,293.08 | APA 42 |

Four minutes is well past any plausible KIS server-side indexing
fan-out, and the ccnl row stayed bit-for-bit the same, so the empty
nccs result is **not a delay** — it's the steady-state behavior of
KIS paper's `inquire-nccs` for our ODNO type.

### Decisive conclusions

1. **`inquire-nccs` is unusable on KIS paper.** Four independent
   observations (R3 post-fill, R6 post-fill, R7-B at T+0 *while still
   open*, R7-B retest at T+4min still open) all returned 0 rows. We
   must not depend on it for paper fill or open-order detection.
2. **`inquire-ccnl` is the single source of truth** *after* R6 ODNO
   normalization. It also distinguishes open from filled inside the
   row itself: `ft_ccld_qty3 == 0` (and `nccs_qty > 0`) → open;
   `ft_ccld_qty3 > 0` → filled / partially filled.
3. **The current `fill_resolver._ccnl_filled_qty` logic is already
   correct** for this paper behavior — it reads exactly the field this
   probe confirmed (`ft_ccld_qty3` with `ft_ccld_qty` / `ccld_qty`
   fallbacks).
4. **Paper reserve margin is ~+1.0%** ($0.19 on $18.85) — matches the
   $9.07 / $905.46 ≈ 1.0% residue from the R6 round. The R8 paper-fee
   model can use this number as the starting estimate.

### What this means for the original R7-B plan

The plan in `…NEXT_R7.md §4` assumed an open paper order would surface
on `inquire-nccs` and that ODNO normalization would let us match it
there. The probe falsifies that assumption for KIS paper. So R7-B as a
self-contained module would still be useful, but it should be reframed:

| Original §4 step | Status |
|---|---|
| "`inquire-nccs` returns the open order and ODNO normalization matches" | **fails on paper** — drop or reframe as a live-only assertion |
| "ccnl visibility" | **works**; reuse R6 echo_poll |
| "ccnl `ft_ccld_qty=0` ⇒ still open" | **valid** — make this the open-detection contract |
| "cancel the order; nccs should drop it" | needs adapter `cancel_order` + KIS paper TR_ID `VTTT1004U`; nccs absence is *not* proof of cancel on paper, must re-poll ccnl and watch `rvse_cncl_dvsn_name` flip to `'취소'` |
| "no position / cash movement after cancel" | still the safety assertion; cash should return from reserve |

### Operator state at end of probe

An open order is currently sitting at the broker:

```
ODNO              : 0000042031   (raw 42031)
symbol            : APA
side / qty / limit: BUY  1  @ $18.85   (limit ≈ 50% of last)
cash reserved     : $19.04
position effect   : none (0 fills)
```

Per the user's go-ahead the operator will handle the lifecycle of this
single share manually: either cancel from the KIS web/app (releases the
$19.04 reserve) or leave it as a parked order (will only fill if APA
prints under $18.85, which is implausible inside the day; otherwise it
expires per KIS GTD policy and reserves are released).

### Net effect on R7-B planning

R7-B as a full module is still worth doing — primarily to add
`cancel_order` to the adapter — but its acceptance criteria need
loosening on the nccs side and tightening on the ccnl side:

* Drop "nccs sees the open order" as a success criterion on paper.
* Add "ccnl row exists with `ft_ccld_qty3 == 0` and `nccs_qty > 0`" as
  the canonical open-order assertion on paper.
* Cancel success criterion on paper: ccnl row's
  `rvse_cncl_dvsn_name` reads `'취소'` (or equivalent) on the next poll,
  AND cash reserve is released.

These adjustments will become the input spec for the eventual R7-B
implementation round.
