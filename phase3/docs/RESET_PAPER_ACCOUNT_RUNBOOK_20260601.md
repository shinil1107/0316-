# 모의투자 계좌 초기화 런북 (KIS paper reset)

작성일: 2026-06-01

> 상황: KIS 모의투자 계좌를 초기 상태(0 보유, 시작 자본)로 리셋할 때, **로컬 상태도 같이
> 초기화**해야 한다. 안 그러면 브로커는 "flat"인데 로컬 `holdings_log.xlsx`는 리셋 전 포트폴리오를
> 그대로 들고 있어, 다음 reconcile이 모든 보유를 `local_only` + 거대한 `cash_drift`로 잡고
> t10/reconcile가 (정상적으로) hard-stop 한다.

---

## 1. 무엇이 로컬 "상태"인가 (격리/초기화 대상)

| 파일/디렉터리 | 내용 | 리셋 시 |
|---|---|---|
| `output/holdings_log.xlsx` | **로컬 단일 진실원**: 보유(Current) + 현금원장(CashLedger) + History | **아카이빙 후 새로 생성** |
| `output/daily_runs/` | 일자별 run 아티팩트(추천, intent, 주문 journal, 리포트) | 아카이빙(과거 이력 보존) |
| `runtime/v1_armed_*.json` | 일일 arm 토큰 | 무해, 그대로 둬도 됨 (원하면 정리) |
| `runtime/global_halt.json` | STOP veto | 리셋 중엔 **set 상태로 유지**(아래 절차) |
| `KIS_TOKEN_CACHE_PATH` | KIS 액세스 토큰 캐시 | **계좌 리셋과 무관**(앱키 동일) — 건드리지 말 것 |

- 현금은 `holdings_log.xlsx`의 `CashLedger` 시트가 단일원천이다. 새 파일은 현금=0으로 생성되므로
  반드시 `INIT` 이벤트로 **브로커 리셋 후 실제 시작 현금**을 시드해야 reconcile drift가 ~0이 된다.
- 주문 dedup/journal은 **run 디렉터리별**(`daily_runs/<run>/autotrade_orders.jsonl`)이라 전역
  오염이 없다. `daily_runs/` 아카이빙으로 충분.

---

## 2. 절차 (순서 중요)

### 2-0. 트레이더 정지
- 패널 STOP(또는 `global_halt` set). 야간 launchd fire가 리셋 도중 돌지 않도록.
- 영속 arm(V2-A 도입 시)이라면 disarm도 함께.

### 2-1. (선택) 현 상태 확인
```bash
python -m phase3.autotrade.reconcile --profile paper   # 리셋 직전 스냅샷
```

### 2-2. 브로커 측 계좌 리셋
- KIS HTS/홈페이지에서 모의계좌 초기화. **리셋 후 실제 시작 현금 금액을 메모**(예: 100,000 USD).

### 2-3. 로컬 상태 아카이빙 + 새 holdings_log 생성
드라이런으로 먼저 계획 확인(아무것도 안 건드림):
```bash
python -m phase3.autotrade.reset_paper_state --initial-cash 100000
```
실제 실행(트레이더 halted 상태여야 함):
```bash
python -m phase3.autotrade.reset_paper_state --initial-cash 100000 --yes
```
- `holdings_log.xlsx` → `output/_archive/reset_<UTC>/`로 이동(삭제 아님, 복구 가능).
- `daily_runs/` → 같은 아카이브로 이동(과거 이력 보존). 보존하려면 `--keep-daily-runs`.
- 새 `holdings_log.xlsx`를 `INIT` 현금 = `--initial-cash`로 생성.
- `--initial-cash`는 **브로커 실제 시작 현금과 일치**시킬 것(불일치 시 그만큼 cash_drift).

### 2-4. 검증 (재가동 전 필수)
```bash
python -m phase3.autotrade.reconcile --profile paper
# 기대: 보유 0개, cash_drift ~ 0 (로컬 INIT == 브로커 시작현금)
```
- drift가 크면 `--initial-cash` 값을 브로커 실제 현금에 맞춰 다시 실행(아카이브에서 복구 후).

### 2-5. 재가동
- `global_halt` clear(패널). 다음 거래일 arm(또는 V2 영속 arm). 끝.

---

## 3. 롤백 (실수 시)
- `reset_paper_state`는 **이동(move)** 만 한다. 잘못했으면 아카이브에서 되돌리면 끝:
```bash
# 예시 — 실제 경로로 치환
mv "output/_archive/reset_<UTC>/holdings_log.xlsx" "output/holdings_log.xlsx"
mv "output/_archive/reset_<UTC>/daily_runs" "output/daily_runs"
```

---

## 4. 한 줄 요약
> STOP → 브로커 리셋(시작현금 메모) → `reset_paper_state --initial-cash <메모값> --yes`
> → `reconcile`로 drift~0 확인 → clear STOP → 재arm.

도구: `phase3/autotrade/reset_paper_state.py` (드라이런 기본, `--yes`로 실행, halt 안 되어 있으면 거부).
