# Cursor Handoff — Autotrading v0 Progress R8 (Cancel-Path)

**Date**: 2026-05-16 KST  
**Author**: Cursor (under operator)  
**Target project**: `/Users/shin-il/PyCharmMiscProject/0316-`  
**Scope**: R8 §0.1 + §11 "Today: Cancel-Path-Only Order" only. The broader
R8 plan (full ccnl state parser as a module, order_manager wait/cancel/
reprice loop, T10 idempotency marker, daily_runner skeleton) is deferred.

---

## 0. TL;DR

```text
✓ KIS paper cancel path is implemented, gated, tested, and proven against
  the existing R7-B open order (APA BUY 1 @ $18.85, ODNO 0000042031).
✓ KIS responded "모의투자 취소주문이 완료 되었습니다." (msg_cd=40630000).
✓ Cash reserve was released exactly back to the pre-R7-B baseline:
  $12,293.08  →  $12,312.12  (Δ +$19.04, the original 18.85 + paper reserve).
✓ APA position remained 42 (no spurious fill on cancel).
✓ ccnl rows: 12 → 13. A new cancel-instruction row (ODNO 0000042493) was
  added with rvse_cncl_dvsn='02' / sll_buy_dvsn_cd_name='매수취소', orgn_odno
  pointing back to 0000042031. The original row's nccs_qty went 1 → 0 but
  the original row's rvse_cncl_dvsn_name did NOT flip — KIS paper expresses
  cancellation as a sibling row, not an in-place edit.
✓ Tests:  R8 cancel(7/7), R7-A applicator(10/10), R6 odno(12/12),
          R5B(9/9), R5A(5/5). All green.

✗ No new parked test orders created (per R8 §0.1 / §2 operator precheck).
✗ Modify (TTTT1003U / VTTT1003U), order_manager loop, T10 idempotency
  marker, daily_runner, reporting — all explicitly deferred.
```

---

## 1. What Was Built

### 1.1 `KisBrokerAdapter.cancel_order(...)`

`phase3/autotrade/kis_broker_adapter.py`

New paper-only adapter method. Public signature:

```python
def cancel_order(
    self,
    *,
    broker_order_id: str,
    symbol: str,
    market: str = "NASD",
    qty: int,
    dry_run: bool = True,
    note: str = "",
) -> CancelResult: ...
```

Returns a structured `CancelResult` dataclass:

```python
@dataclass
class CancelResult:
    broker_order_id: str
    cancel_order_id: Optional[str]
    accepted: bool
    dry_run: bool
    symbol: str
    market: str
    qty: int
    submitted_at: str
    payload: Dict[str, Any]
    raw_response_summary: Dict[str, Any]
    note: str
```

Hard contract:

| Layer | Behavior |
|---|---|
| Default | `dry_run=True` — payload only, no network |
| Paper-only | `env_name != 'paper'` → refused outright |
| Env gate | Real cancel requires `KIS_PAPER_CANCEL_OK=true` (mirrors `KIS_PAPER_SUBMIT_OK`) |
| Input guards | Empty/blank `broker_order_id`, empty `symbol`, `qty<=0` → refused |
| Failure mode | KIS rt_cd!=0 → `accepted=False`, message preserved in `note` (no raise) |
| Audit | `AuditLogger.log(...)` on both dry-run and real paths |
| Buy-only / global_halt / cancel_all_pending | All allowed to cancel — they only block *new* orders |

### 1.2 `EnvConfig.paper_cancel_ok`

`phase3/autotrade/kis_broker_adapter.py`

Added a new env-driven boolean to the frozen dataclass:

```python
paper_cancel_ok: bool   # KIS_PAPER_CANCEL_OK == 'true'
```

`load_env_config()` reads `KIS_PAPER_CANCEL_OK` with the same semantics as
`KIS_PAPER_SUBMIT_OK`. It is independent of `paper_submit_ok` so an
operator can grant cancel-only permission to a cleanup session that is
not allowed to submit new orders.

### 1.3 TR matrix + endpoint

```python
EP_ORDER_RVSECNCL = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
TR["order_cancel"] = {"paper": "VTTT1004U", "live": "TTTT1004U"}
```

### 1.4 Payload contract

Confirmed against KIS Developers official LLM sample
`examples_llm/overseas_stock/order_rvsecncl/order_rvsecncl.py`
(`koreainvestment/open-trading-api` repo, 2025-07-01):

```text
POST /uapi/overseas-stock/v1/trading/order-rvsecncl
TR_ID: VTTT1004U (paper) / TTTT1004U (live)

Body (10 fields, all required):
  CANO              = self.cfg.account_no
  ACNT_PRDT_CD      = self.cfg.account_product_code
  OVRS_EXCG_CD      = "NASD" | "NYSE" | "AMEX" | ...
  PDNO              = ticker
  ORGN_ODNO         = original order ODNO (zero-padded form from place_order ack)
  RVSE_CNCL_DVSN_CD = "01"=modify / "02"=cancel
  ORD_QTY           = qty to cancel
  OVRS_ORD_UNPR     = "0" on cancel
  MGCO_APTM_ODNO    = "" (운용사지정주문번호, usually empty)
  ORD_SVR_DVSN_CD   = "0"
```

> **Gotcha discovered live**: the geongi-im/kis-us-auto-trading reference
> implementation omits both `RVSE_CNCL_DVSN_CD` and `MGCO_APTM_ODNO`.
> The first live attempt with that 8-field body was rejected by KIS with
> `rt_cd=1 (HTTP 500): 정정취소구분코드를 확인해주세요.` Adding the two
> missing fields per the official examples_llm sample fixed it on the
> retry. Future modify path (R8-deferred) must use the same 10-field
> shape with `RVSE_CNCL_DVSN_CD="01"` and a real `OVRS_ORD_UNPR`.

### 1.5 Tests

`phase3/tests/test_r8_cancel_order.py` — 7 unit tests, all using a fake
HTTP layer (zero network):

```text
TestDryRunNoNetwork.test_dry_run_records_payload_without_calling_http
TestRealRequiresEnvGate.test_real_cancel_without_gate_is_refused
TestRealSendsPayloadAndTrId.test_real_cancel_with_gate_sends_expected_payload
TestEmptyOdnoRefused.test_blank_broker_order_id_is_refused
TestNonPaperEnvRefused.test_live_env_is_refused_outright
TestBrokerRejectionSurfaced.test_kis_rt_cd_nonzero_returns_accepted_false_with_note
TestQtyValidation.test_zero_qty_refused
```

Regression: R7-A (10/10), R6 (12/12), R5B (9/9), R5A (5/5) all green.

---

## 2. Live Paper Acceptance Test

### 2.1 Target order (re-used from R7-B, NOT a new test order)

```text
symbol       : APA
side         : BUY
qty          : 1
limit        : $18.85
ODNO         : 0000042031
raw ODNO     : 42031
ord_gno_brno : 00950
state pre    : OPEN_OR_PENDING for ~1h 21m
```

### 2.2 Pre-cancel snapshot

```text
@ KST 01:13:20

ccnl row for ODNO 42031:
  odno                = '42031'
  ft_ord_qty          = '1'
  ft_ccld_qty         = '0'
  nccs_qty            = '1'
  rvse_cncl_dvsn      = '00'
  rvse_cncl_dvsn_name = '보통'
  prcs_stat_name      = ''
  rjct_rson_name      = ''
  ft_ord_unpr3        = '18.85000000'
  ft_ccld_unpr3       = '0.00000000'

cash.available  = $12,293.08
APA position    = 42 @ $38.5370
ccnl total rows = 12
```

### 2.3 Dry-run payload (reviewed before live cancel)

```text
endpoint : /uapi/overseas-stock/v1/trading/order-rvsecncl
tr_id    : VTTT1004U
body     : CANO=50182047  ACNT_PRDT_CD=01  OVRS_EXCG_CD=NASD  PDNO=APA
           ORGN_ODNO=0000042031  RVSE_CNCL_DVSN_CD=02  ORD_QTY=1
           OVRS_ORD_UNPR=0  MGCO_APTM_ODNO=""  ORD_SVR_DVSN_CD=0
```

### 2.4 Live cancel attempts

**Attempt 1** (8-field body, geongi-im reference shape):

```text
rt_cd=1 (HTTP 500): 정정취소구분코드를 확인해주세요.
```

→ KIS rejected. cancel_order surfaced `accepted=False` cleanly (no raise),
preserved the KIS message in `note`, did not retry. Fix: switch to the
KIS official 10-field shape.

**Attempt 2** (10-field body, after RVSE_CNCL_DVSN_CD + MGCO_APTM_ODNO fix):

```text
msg_cd : 40630000
msg1   : "모의투자 취소주문이 완료 되었습니다."
ODNO   : 0000042493   ← new cancel-instruction ODNO
KRX_FWDG_ORD_ORGNO : 00950
ORD_TMD            : 011320
```

→ `accepted=True`, `cancel_order_id='0000042493'`.

### 2.5 Post-cancel verification (T+3s)

#### Original order row (ODNO 42031) — partial diff

```text
ft_ord_qty          : '1' -> '1'           (unchanged — historical record)
ft_ccld_qty         : '0' -> '0'           (still zero fill)
nccs_qty            : '1' -> '0'           <<< CHANGED — open balance released
rvse_cncl_dvsn      : '00' -> '00'         (NOT flipped in-place)
rvse_cncl_dvsn_name : '보통' -> '보통'      (NOT flipped in-place)
```

#### New cancel-instruction row (ODNO 42493)

```text
odno                 = '42493'
orgn_odno            = '42031'         ← points back to the cancelled order
pdno                 = 'APA'
sll_buy_dvsn_cd_name = '매수취소'      ← explicit cancel-of-buy label
ft_ord_qty           = '1'
ft_ccld_qty          = '0'
nccs_qty             = '0'
ft_ord_unpr3         = '0.00000000'    (no price for cancel itself)
rvse_cncl_dvsn       = '02'            ← KIS modify/cancel code "02"=취소
rvse_cncl_dvsn_name  = '취소'
prcs_stat_name       = ''
rjct_rson_name       = ''
ord_tmd              = '011320'        (matches the cancel POST timestamp)
ord_gno_brno         = '00950'
```

#### Account-side

```text
cash.available  $12,293.08 -> $12,312.12     (+$19.04 released)
APA position    42         -> 42             (unchanged — no spurious fill)
ccnl total rows 12         -> 13             (cancel row added)
```

The cash delta matches the original reserve: `$18.85 limit × 1 share = $18.85`
plus the small paper-side reserve buffer KIS held alongside the order. The
restored cash matches the pre-R7-B baseline ($12,312.12) to the cent.

---

## 3. Paper Cancel Detection Contract (for the future R8-B parser)

Important behavioral observation that must feed the eventual
`order_state.py` parser:

```text
KIS paper ccnl does NOT mutate the original order row to mark it
cancelled. Instead, a sibling row is appended:

  - cancel row.odno              = a new, KIS-assigned ODNO
  - cancel row.orgn_odno         = original order ODNO
  - cancel row.rvse_cncl_dvsn    = '02'
  - cancel row.rvse_cncl_dvsn_name = '취소'
  - cancel row.sll_buy_dvsn_cd_name ends with '취소' (e.g. '매수취소')

The original row.nccs_qty drops from >0 to 0 once the cancel is
accepted, but its rvse_cncl_dvsn and rvse_cncl_dvsn_name stay as the
new-order values ('00' / '보통').
```

Therefore a robust paper CCNL state parser must do **both**:

1. Track `nccs_qty` on the original row (open balance → 0 means the order
   is no longer working — but on its own this is ambiguous between
   "filled" and "cancelled").
2. Look for a sibling row whose `orgn_odno` matches the target ODNO,
   `rvse_cncl_dvsn == '02'`, and `nccs_qty == 0`. Presence of this row
   plus no fill change on the original row plus a returned cash reserve
   = `CANCELLED`.
3. Distinguish from `FILLED` by checking the original row's `ft_ccld_qty`
   and the position-delta, not just `nccs_qty`.

R8-B (deferred) should code this contract into a `BrokerOrderState`
classifier.

---

## 4. Files Touched

```text
phase3/autotrade/kis_broker_adapter.py
  + EP_ORDER_RVSECNCL constant
  + TR["order_cancel"] paper/live entry
  + EnvConfig.paper_cancel_ok field + load_env_config() wiring + masked() exposure
  + @dataclass CancelResult
  + KisBrokerAdapter.cancel_order(...) full impl

phase3/tests/test_r8_cancel_order.py    (new, 7 tests)
phase3/docs/CURSOR_HANDOFF_AUTOTRADING_V0_PROGRESS_R8_CANCEL.md   (this file)
```

No existing tests were modified. No live route was added.

---

## 5. Test Matrix

| Suite | Result |
|---|---|
| `test_r8_cancel_order` (new) | 7 / 7 PASS |
| `test_r7_t10_applicator` | 10 / 10 PASS |
| `test_r6_odno_normalize` | 12 / 12 PASS |
| `test_r5b_pre_submit_safety` | 9 / 9 PASS |
| `test_r5a_orchestrator_safety` | 5 / 5 PASS |

Total: **43 / 43 green** across R5A → R8.

---

## 6. R8 Today-Only Definition Of Done — Checklist

Per R8 §12:

| Criterion | Status |
|---|---|
| existing APA ODNO 0000042031 was inspected | ✓ (§2.2) |
| ccnl open/pending state was confirmed before cancel | ✓ (`ft_ccld_qty=0`, `nccs_qty=1`) |
| cancel_order dry-run payload was reviewed | ✓ (§2.3) |
| actual paper cancel ran only with `KIS_PAPER_CANCEL_OK=true` | ✓ (env-gated, attempt 2) |
| post-cancel ccnl/position/cash evidence was captured | ✓ (§2.5) |
| no new parked order was created without explicit operator approval | ✓ (R7-B order re-used; no new order) |
| progress doc was written | ✓ (this file) |

---

## 7. What R8 Did NOT Build (Explicitly Deferred)

Per R8 §10 / §11 "After Today's Cancel Test":

```text
- phase3/autotrade/order_ids.py shared normalize_odno helper
  (cancel path did not need centralization; existing echo._norm_odno
   was sufficient for matching the response. Centralize later.)

- phase3/autotrade/order_state.py — full ccnl-based parser module
  (See §3 above for the discovered paper-cancel contract that will
   feed this module's design.)

- KisBrokerAdapter.modify_order(...)   (TR VTTT1003U / TTTT1003U)
  (Same endpoint, RVSE_CNCL_DVSN_CD="01", real OVRS_ORD_UNPR. Easy
   to add once a modify-trigger policy exists.)

- phase3/autotrade/order_manager.py — wait/cancel/reprice loop
- phase3/autotrade/daily_runner.py — single-command daily runner
- T10 applicator idempotency marker / recovery mode
- Reporting / email
- live trading
- launchd/cron scheduling
- live nccs probe (paper nccs already confirmed unreliable in R7-B)
- market orders (R8 §2.4)
- automatic SELL/TRIM
```

---

## 8. Operator Notes

### 8.1 Cleanup state

- The R7-B test order is gone. APA position is unchanged at 42 shares.
- Cash returned to the pre-R7-B baseline ($12,312.12).
- No further manual cleanup is required.

### 8.2 How to run cancel manually (future reference)

Dry-run (safe, no env vars needed):

```bash
cd /Users/shin-il/PyCharmMiscProject/0316-
PYTHONPATH=. python3 -c "
from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter, SafetyState, load_env_config,
)
cfg = load_env_config()
adapter = KisBrokerAdapter(cfg=cfg, safety_state=SafetyState(buy_only_mode=True))
res = adapter.cancel_order(broker_order_id='<ODNO>', symbol='<TICKER>',
                            market='NASD', qty=<N>, dry_run=True)
print(res)
"
```

Real paper cancel (env gate required, no `--submit` style flags exist
yet — that goes in R8-D `order_manager` later):

```bash
KIS_PAPER_CANCEL_OK=true PYTHONPATH=. python3 -c "
from phase3.autotrade.kis_broker_adapter import (
    KisBrokerAdapter, SafetyState, load_env_config,
)
cfg = load_env_config()
adapter = KisBrokerAdapter(cfg=cfg, safety_state=SafetyState(buy_only_mode=True))
res = adapter.cancel_order(broker_order_id='<ODNO>', symbol='<TICKER>',
                            market='NASD', qty=<N>, dry_run=False)
print(res)
"
```

### 8.3 Audit trail

Both dry-run and real cancels are appended to today's audit JSONL under
`~/.kis_audit/<YYYY-MM-DD>.jsonl` with `extra.kind="cancel_dry_run"` or
the real-call HTTP record. Secrets are masked by the standard
`_mask_obj` pipeline.
