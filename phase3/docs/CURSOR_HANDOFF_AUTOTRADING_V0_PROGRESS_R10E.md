# Cursor Handoff — Autotrading v0 R10E (post-market-open hotfixes)

**Date**: 2026-05-19 KST, ~01:00 (post-market-open hotfix round)
**Preceding handoff**: `CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10D.md`
**Trigger**: First true full-batch paper acceptance (run_id `20260519_220825_daily`) revealed three new blocking bugs that did not appear in R10C's single-symbol runs.
**Scope**: paper-only — fix the three observed failures and apply today's three fills through the proper T10 path. The Full Paper Run coordinator and post-trade email (Codex R11A/B) remain out of scope.

---

## 0. TL;DR

Test count: **336 → 372 (all green)**

What changed:

| Item | Effect |
|---|---|
| **R10E-1** Bug 2 — `rec_row_id` plumbing | `OrderIntent` gains a `rec_row_id` field; `intents_io.make_buy_intent_row` writes it as an explicit on-disk field and `daily_runner.default_intents_loader` recovers it from either the new field or (legacy) the client_order_id. `default_manage_loop_fn` threads it through `manage_order`. Was previously hard-coded to 0, blocking every multi-row T10 apply. |
| **R10E-2** Bug 1 — multi-exchange quote fallback | `KisBrokerAdapter.get_quote_with_exchange_fallback` probes NASD → NYSE → AMEX and the control panel's quote_fn closure now uses it. NYSE-listed names (JBL, DOW, etc.) no longer fall back to `fallback_quote_zero` just because intent rows hard-code `market="NASD"`. |
| **R10E-3** Bug 4 — t10 outcome fallback for ccnl_missing | When `inquire-ccnl` no longer returns an ODNO at apply time, `t10_applicator` may now use `autotrade_daily_report.json`'s outcome as the source of truth IF the outcome is a clean exact-qty FILLED. Operator-disable via `--no-outcome-fallback`. |
| **R10E-fill** Today's three fills | Recorded into `holdings_log.xlsx` via the standard T10 path after `autotrade_orders.jsonl` `rec_row_id` was hot-patched on disk from 0 → 73/75/74. `run_meta.status == executed`. |

What R10E live-test confirmed:

- `inquire-ccnl` is unstable on a 2-minute window (we saw it forget ODNO 0000035625 at 14:06 UTC and then return it cleanly at 14:32 UTC).
- KIS paper quote endpoint returns `last=0, ask=0` for NYSE symbols asked under `EXCD=NAS`, but the order-submit and order-history paths apparently auto-route on the broker side (orders for JBL/DOW with `market="NASD"` accepted and filled).
- The combined effect of those two issues plus the R10D-era `rec_row_id=0` placeholder produced today's "overpriced fills + T10 apply abort" failure.

What R10E does NOT touch (still open):

- `HoldingsManager.apply_partial_execution`'s BUY_MORE cost-basis update on `Current` sheet kept JBL's BuyPrice at 301.31 (the pre-existing entry) even though new shares were added at 329.10. Cost basis weighting may need a follow-up; History + CashLedger are correct.
- Recommendation generation still has no exchange column. The R10E fallback covers the quote path; the order-submit path continues to rely on KIS auto-routing.
- The Full Paper Run button is still deliberately disabled.
- Post-trade autotrade email (R11B) is not wired.

---

## 1. Bug 2 — `rec_row_id` plumbing

### Symptom

`autotrade_orders.jsonl` for run_id `20260519_220825_daily` had every `submitted` and `filled` event tagged `rec_row_id=0`, while the `client_order_id` correctly carried `73`, `74`, `75`. `t10_applicator` aborted with:

```
DOW   ODNO=0000035625 filled=0/0 price=0.0000 status=ccnl_missing abort=recommendations.csv has no RecRowId=0
```

Root cause in `phase3/autotrade/daily_runner.py:881` (pre-R10E):

```python
outcome = manage_order(
    intent, ..., rec_row_id=0,  # real intent rows carry their own rec_row_id;
                                 # plumbing that through is a follow-up
)
```

The follow-up was never done; `OrderIntent` had no `rec_row_id` field for the loader to fill in.

### Fix

1. `OrderIntent` (`kis_broker_adapter.py`) — add `rec_row_id: int = 0` with backwards-safe default for probe scripts / unit fixtures that don't care.
2. `make_buy_intent_row` (`intents_io.py`) — accept `rec_row_id` kwarg, write it as an explicit `rec_row_id` field on disk. When the caller omits it, auto-derive from the client_order_id pattern `co-<run_id>-<rid>-B|S-<qty>-<ticker>`.
3. `rec_row_id_from_client_order_id` (`intents_io.py`) — new pure helper. Walks back from the `-B|S-<qty>-<ticker>` tail, so run_ids that themselves contain dashes do not break parsing.
4. `default_intents_loader` (`daily_runner.py`) — read `rec_row_id` from the row field first (post-R10E shape), then fall back to parsing the CID (pre-R10E shape), then `0` (so the loader never crashes on a missing field).
5. `default_manage_loop_fn` (`daily_runner.py`) — pass `int(getattr(intent, "rec_row_id", 0) or 0)` to `manage_order` instead of the hard-coded `0`.

### Tests (`tests/test_r10e_rec_row_id_plumbing.py`, 14 cases)

- `rec_row_id_from_client_order_id` — canonical pattern, run_id with dashes, SELL side, every malformed shape.
- `make_buy_intent_row` — explicit rid persists, auto-derives from CID when omitted, unparseable CID leaves the field absent.
- `candidate_to_intent_row` — `BuyCandidate.rec_row_id` propagates through both single-row and batch helpers.
- `submitted_intents.json` round-trip preserves the field.
- `OrderIntent` carries the field with default 0.
- End-to-end loader behaviour: explicit field wins, legacy row recovers from CID, unparseable falls back to 0.

---

## 2. Bug 1 — multi-exchange quote fallback

### Symptom

`submitted_intents.json` for today's run had:

| Ticker | Real exchange | `_quote_source` | `_quote_ref_price` | Limit | Fill |
|---|---|---|---|---|---|
| MRNA | NASDAQ | `quote_refreshed_below_reco` | 46.18 | 48.11 | 46.095 |
| JBL  | **NYSE** | **`fallback_quote_zero`** | — | 338.73 (reco_close) | 329.10 |
| DOW  | **NYSE** | **`fallback_quote_zero`** | — | 38.56 (reco_close) | 37.85 |

JBL/DOW limits stayed at Friday's close because `KisBrokerAdapter.get_quote(symbol, market="NASD")` returns `last=0, ask=0` for NYSE-listed symbols (KIS paper's quote endpoint actually checks EXCD), and R10D-3 then took the `fallback_quote_zero` branch.

`load_buy_candidates` hard-codes `market="NASD"` for every candidate because `recommendations.csv` has no exchange column, so even NYSE symbols travel as NASD intents.

### Fix

`phase3/autotrade/kis_broker_adapter.py` — new `get_quote_with_exchange_fallback` method on `KisBrokerAdapter`:

```python
def get_quote_with_exchange_fallback(
    self, symbol, *, preferred_market="NASD",
    exchanges=("NASD", "NYSE", "AMEX"),
) -> Optional[Quote]:
    """Probe preferred_market first, then walk the remaining
    exchanges. Treat a Quote as 'good enough' if ask>0 or last>0.
    Any raise or zero-priced response = try the next exchange.
    Returns None only when every exchange in the list either
    raised or returned zero — caller's reco_close path is reached
    only in that one case.
    """
```

`phase3/autotrade/control_panel.py` — `_on_generate_all_intents`'s `quote_fn` closure now calls this method instead of `get_quote`. The R10D-3 helper sees real ask/last and the `fallback_quote_*` paths only fire for genuinely off-exchange / illiquid names.

### Tests (`tests/test_r10e_quote_exchange_fallback.py`, 9 cases)

- NASD hit returns immediately (no extra round trips for AAPL etc.).
- NYSE-listed symbol (JBL, DOW reproducers) falls through to NYSE after NASD zero.
- NASD exception (not just zero) still walks to NYSE.
- All-zero everywhere returns None so the caller's reco_close fallback fires.
- All-raise returns None.
- ask=None but last>0 still acceptable (low-volume off-hours).
- `preferred_market="NYSE"` starts on NYSE without paying a wasted NASD call.
- End-to-end against R10D-3 helper: MRNA/JBL/DOW under today's data shape no longer produce `fallback_quote_zero`; all three carry real refreshed limits.

---

## 3. Bug 4 — t10 outcome fallback

### Symptom

At 14:06 UTC (~3 minutes after the manage loop saw DOW fill), `t10_applicator` re-queried `inquire-ccnl` and got `status=ccnl_missing` for ODNO 0000035625 — KIS paper had forgotten the order. Even though `autotrade_daily_report.json` explicitly recorded `final_state=filled, qty_filled=3, avg_fill_price=37.85`, the applicator aborted because its single source of truth (ccnl) was empty.

By 14:32 UTC the same ccnl query returned the full row cleanly. The amnesia window was a few tens of minutes, exactly the kind of transient R10D-1 absorbs in the manage loop.

### Fix

`phase3/autotrade/t10_applicator.py`:

1. New `_OutcomeFallback` dataclass + `_load_outcome_fallbacks(run_dir)` helper that reads `autotrade_daily_report.json` and indexes its `outcomes[]` array by `rec_row_id` (recovered from each row's `client_order_id`).
2. `_resolve_against_ccnl` accepts `outcome_fallbacks: Dict[int, _OutcomeFallback]` and `allow_outcome_fallback: bool`. When ccnl shows the ODNO is missing AND a clean exact-qty FILLED outcome exists, the resolution borrows qty + price from the outcome and stamps `note="ccnl_missing — applied from manage outcome fallback (...)"`. Same path covers the "ccnl row present but filled_qty=0" variant.
3. Conservative guards (any failure ⇒ no fallback, pre-R10E behaviour preserved):
   - `final_state == "filled"` exactly (no partial, no unknown).
   - `qty_filled` exactly equals `recommendations.csv Shares`.
   - `avg_fill_price > 0`.
4. CLI: `--no-outcome-fallback` to disable. Default is ON for paper (this round's whole purpose).
5. `cmd_apply` loads the fallback dict and threads it into `_resolve_against_ccnl`.

### Tests (`tests/test_r10e_t10_outcome_fallback.py`, 13 cases)

- Pre-R10E baseline (fallback disabled) still aborts on ccnl_missing.
- Today's exact DOW scenario passes with fallback enabled.
- Partial / unknown / qty-mismatch / zero-price outcomes refuse to fallback.
- "ccnl row present but filled_qty=0" path also uses the outcome.
- ccnl full-fill ignores the fallback (real ccnl wins).
- Backwards-compat: omitting the dict reproduces pre-R10E behaviour.
- Loader: clean fills indexed, missing report returns `{}`, corrupt JSON returns `{}`, outcomes without `client_order_id` skipped.

---

## 4. Today's fills — applied through the standard T10 path

After landing the three R10E fixes, today's `20260519_220825_daily` was processed end-to-end with the operator's preferred recovery (option A):

1. **Hot-patch on disk**: `autotrade_orders.jsonl` rewritten so every event whose `rec_row_id == 0` adopts the integer parsed from its `client_order_id`. Backup preserved as `autotrade_orders.jsonl.pre_r10e_patch_20260519T142847Z`. Six rows patched (3 submitted + 3 filled). All other fields untouched.
2. **Clean up the manual T10 placeholder**: the operator had previously hand-recorded a `MRNA BUY_NEW 2 @ 48.36 (Trigger=T10_MANUAL)` row from an earlier acceptance attempt. Removed from `Current` / `History` / `CashLedger` after backing up `holdings_log.xlsx` as `holdings_log.pre_r10e_remove_mrna_manual.20260519T143209Z.xlsx`. Without this cleanup the autotrade BUY_MORE would have either duplicated the position or merged into a stale 48.36 cost basis.
3. **Dry-run T10 apply**: reported `would apply MRNA 2 @ 46.095, JBL 2 @ 329.10, DOW 3 @ 37.85`, no aborts. The ccnl_missing fallback path was registered but not actually exercised — by 14:32 UTC `inquire-ccnl` had recovered the rows.
4. **Real T10 apply** with `AUTOTRADE_T10_APPLY_OK=true`: rc=0. `artifact status: awaiting_execution → executed`. `cash_after=$11,600.57  total_after=$106,621.89`.

Final holdings_log state:

```
Current:
  MRNA  2 @ 46.095        (new)
  JBL   6 @ 301.31        (was 4 @ 301.31, merged with +2 @ 329.10; see follow-up below)
  DOW   3 @ 37.85         (new)

History (last 3):
  2026-05-19  MRNA  BUY_MORE  46.095  2   92.19   AUTOTRADE
  2026-05-19  JBL   BUY_MORE  329.10  2  658.20   AUTOTRADE
  2026-05-19  DOW   BUY_NEW    37.85  3  113.55   AUTOTRADE

CashLedger (last 3):
  2026-05-19  BUY_MORE  -92.19   12372.32  MRNA 2sh ODNO=0000035623 run=at-20260519T140424Z-187c
  2026-05-19  BUY_MORE -658.20   11714.12  JBL  2sh ODNO=0000035624 run=at-20260519T140424Z-187c
  2026-05-19  BUY_NEW  -113.55   11600.57  DOW  3sh ODNO=0000035625 run=at-20260519T140424Z-187c
```

### Open follow-up — JBL Current BuyPrice didn't update

`Current` sheet kept JBL at `BuyPrice=301.31, Shares=6`. The correct weighted average is `(4×301.31 + 2×329.10)/6 = 310.57`. History + CashLedger record the new lot's price correctly, but `HoldingsManager.apply_partial_execution` apparently does not re-weight the existing `BuyPrice` on a BUY_MORE. Flagging this as **R10F-1 candidate** — out of scope for the post-market-open hotfix round, but should be confirmed/fixed before continued accumulation on existing positions skews PnL_Pct on the dashboard.

---

## 5. Files changed in R10E

| File | Change |
|---|---|
| `phase3/autotrade/kis_broker_adapter.py` | `OrderIntent.rec_row_id` field; `get_quote_with_exchange_fallback` method |
| `phase3/autotrade/intents_io.py` | `make_buy_intent_row` accepts/writes `rec_row_id`; `rec_row_id_from_client_order_id` helper; `candidate_to_intent_row` forwards `BuyCandidate.rec_row_id` |
| `phase3/autotrade/daily_runner.py` | `default_intents_loader` recovers `rec_row_id`; `default_manage_loop_fn` threads it through `manage_order`; the prior `rec_row_id=0` TODO comment is gone |
| `phase3/autotrade/control_panel.py` | `_on_generate_all_intents`'s quote_fn now uses `get_quote_with_exchange_fallback` |
| `phase3/autotrade/t10_applicator.py` | `_OutcomeFallback` + `_load_outcome_fallbacks`; `_resolve_against_ccnl` accepts the fallback dict; `cmd_apply` loads and threads it; `--no-outcome-fallback` CLI flag |
| `phase3/tests/test_r10e_rec_row_id_plumbing.py` | New, 14 tests |
| `phase3/tests/test_r10e_quote_exchange_fallback.py` | New, 9 tests |
| `phase3/tests/test_r10e_t10_outcome_fallback.py` | New, 13 tests |
| `phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R10E.md` | This document |

Backup files written during today's fill recovery (NOT under git, kept under `output/` next to the live artefacts):

```
output/daily_runs/20260519_220825_daily/autotrade_orders.jsonl.pre_r10e_patch_20260519T142847Z
output/holdings_log.pre_r10e_remove_mrna_manual.20260519T143209Z.xlsx
```

---

## 6. Acceptance status after R10E

R10E is the second real paper acceptance (after R10C's MRNA-only run) and the first one where:

- All three submitted intents reached terminal-good FILLED.
- T10 apply ran cleanly through to `run_meta.status=executed`.
- `execution_applied.csv` exists and reflects the three broker fills.
- `holdings_log.xlsx` was mutated by the standard autotrade pipeline (no manual operator entries this time apart from the pre-existing MRNA cleanup).

Hard-pass criteria from Codex's R11 acceptance list still NOT met automatically:

- No one-click button — still operator-staged.
- No post-trade email.

Both are R11A / R11B work and remain on Codex's plan.

---

## 7. Recommended next round (R11A entry conditions, restated)

Codex's "reliability first, one-click later" sequence still holds, and R10E was an unplanned but necessary detour. Before starting R11A, we now also want:

1. Confirm the JBL cost-basis behaviour (R10F-1) is either intentional or fixed.
2. One more "happy-path" market-open paper run with R10E in place but the existing R10C/R10D manual UX. If it goes clean through `executed` without any out-of-band manual cleanup, R11A is safe to start.

Sequencing reminder: do NOT roll R10F-1, R11A, and R11B into one round. Each opens its own tail of acceptance work.

End of handoff.
