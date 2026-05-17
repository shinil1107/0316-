# Cursor Handoff — Autotrade v0 Progress (Step 1+2 closed)

**Version**: v0.2 (2026-05-11)
**Status**: **Step 1 (skeleton) + Step 2 (read-only REST) DONE.** Step 3/4 대기.
**Blueprint reference**: `/Users/shin-il/PyCharmMiscProject_codex/docs/CURSOR_HANDOFF_AUTOTRADING_V0.md` (§17 KIS specifics)
**Code base**: `/Users/shin-il/PyCharmMiscProject/0316-/phase3/autotrade/`

이 문서는 ML 트랙 hold 후 시작한 autotrade v0의 첫 두 단계 결과를 정리하고, 다음 단계 (reconcile / order intent dry-run / paper live buy) 진입 전 codex 검토를 받기 위한 핸드오프다. 모든 결정의 근거와 실측 데이터를 그대로 남겨, 검토 후 방향이 바뀌더라도 되돌릴 수 있게 했다.

---

## 0. 한 줄 요약

KIS 모의서버에 대해 **token / quote / positions / cash / order-history** 5개 read-only path가 paging까지 포함해 안전하게 작동한다. **실 거래로 가는 모든 경로는 3중 잠금 (KIS_ENV=live + dry_run=False + KIS_CONFIRM_LIVE=true)** 으로 막혀 있고, 모든 호출은 secret 자동 마스킹된 jsonl audit으로 흐른다. **다음은 broker-as-source-of-truth를 코드로 구체화하는 reconcile report**다.

---

## 1. 진행 상태 매트릭스

| Step | 범위 | 상태 | 검증 방법 |
|---|---|---|---|
| Step 1 | env 로더 + token cache 구조 + audit jsonl + safety guard + mock adapter | DONE | `--self-check` (no network) |
| Step 2 | KIS REST: token / quote / positions / cash / ccnl + paging + rate-limit retry | DONE | `probe all` (실 모의서버 호출) |
| Step 3A | Local-vs-broker reconcile report (positions/cash) | 대기 | (예정) |
| Step 3B | 추천 CSV → KIS 주문 payload **dry-run** 생성 | 대기 | (예정) |
| Step 4 | Paper에서 1주 LIMIT BUY 실제 round-trip 시범 | 대기 | (예정, confirm 필요) |
| Step 5+ | 운영 자동화 + reconcile auto-trigger + LIVE 단계 | 후순위 | (예정) |

Codex blueprint의 Stage 0 (Read-only broker sync)에 정확히 해당하는 부분이 Step 1+2다. Stage 1 (Semi-auto execution)이 Step 3+4에 해당한다.

---

## 2. 산출물 — 파일 / 책임

```
0316-/
├── .env                                 # secrets (mode 600, gitignored by default-deny `*`)
├── .env.example                         # template, whitelisted in .gitignore
├── .gitignore                           # whitelist: phase3/autotrade/*.py, .env.example
└── phase3/autotrade/
    ├── __init__.py                      # package entry, safety principle docstring
    └── kis_broker_adapter.py            # 단일 파일, ~1,480 lines, 12 섹션
```

### `kis_broker_adapter.py` 내부 구조

| 섹션 | 책임 | 핵심 외부 인터페이스 |
|---|---|---|
| 1 | KIS endpoint 상수 (paper/live host, path, TR_ID 매트릭스) | `KIS_HOSTS`, `TR`, `EP_*` |
| 2 | `EnvConfig` — .env 로드 + 검증 | `load_env_config()` |
| 3 | 마스킹 — substring 매칭 (`secret/token/password/appkey/bearer` 등) | `_mask_secret()`, `_mask_obj()` |
| 4 | `AuditLogger` — per-day jsonl append | `AuditLogger.log(...)` |
| 5 | `TokenCache` — env 불일치 거부, 만료 margin 30분 | `TokenCache.load/save/clear` |
| 6 | `SafetyGuard` — buy_only / global_halt / **live 3중 잠금** | `assert_can_submit()`, `allow_live_order()` |
| 7 | typed `BrokerError` 계층 | `BrokerAuthError/NetworkError/ResponseError` |
| 8 | broker 데이터클래스 | `Quote`, `Position`, `CashBalance`, `OrderIntent`, `PlacedOrder` |
| 9a | `_Http` — self-throttle + rate-limit retry + audit + verbose | `_Http.call(...)` |
| 9b | `KisBrokerAdapter` — 실 REST 호출 (Step 2 wired) | `ensure_token`, `get_quote`, `get_positions`, `get_cash`, `get_order_history` |
| 10 | `MockBrokerAdapter` — 메모리 시뮬, zero-network | 동일 시그니처 |
| 11 | `_self_check()` — Step 1 검증, 시크릿 leak grep 자동 검사 | `--self-check` |
| 12 | CLI `probe token/quote/positions/cash/history/all` | `--probe ...` |

`.env` 스키마 (계약):
```
KIS_APP_KEY=...           # required
KIS_APP_SECRET=...        # required, b64 raw, never logged plaintext
KIS_ACCOUNT_NO=NNNNNNNN   # 8 digits, validated
KIS_ACCOUNT_PRODUCT_CODE=01
KIS_ENV=paper|live        # default paper
KIS_CONFIRM_LIVE=false    # 'true' (literal) required for live transmission
KIS_TOKEN_CACHE_PATH=     # default ~/.kis_token_cache.json
KIS_LOG_DIR=              # default ~/.kis_audit/
```

---

## 3. Step 1 — 스켈레톤 (no network)

목표: secrets를 다루는 표면 / 안전 표면 / 인터페이스 표면을 **모두 갖춘 상태에서 zero-network 검증이 가능해야 한다**.

`--self-check` 통과 항목:
1. `.env` 로드 + 마스킹된 dict 출력
2. token cache 빈 상태 OK
3. audit jsonl 한 줄 append → 직후 **재독해서 raw secret 포함 시 exit code 4** (실시간 leak 가드)
4. `SafetyGuard.allow_live_order(dry_run=False) == False` (paper + confirm_live=false)
5. `SafetyGuard.assert_can_submit(side='SELL', ...)` → `SafetyError` (buy_only_mode)
6. Mock 어댑터 round-trip: AAPL 3주 매수 시뮬레이션 → 포지션/캐시 정합
7. 실 어댑터 `place_order(dry_run=True)`가 그대로 dry-run 단락

검출/수정한 보안 버그 1건:
- 초기 `_mask_obj`는 **정확한 키 이름**만 마스킹 → `sample_secret` 같은 사용자 정의 키는 평문 누출
- substring 매칭으로 강화 + self-check 끝에 **audit 라인 재독해 평문 검색** 추가
- 노출됐던 jsonl은 삭제, 마스킹 적용 후 재검증 통과

---

## 4. Step 2 — REST wired (read-only)

### 4.1 인프라 추가 — `_Http`

- `requests.Session` 한 개 재사용
- self-throttle: `min_interval_ms=350` (≈ 2.8 TPS, KIS paper ~ 4 TPS 한계 아래)
- **rate-limit 자동 1회 retry**: `rt_cd != 0` + 메시지에 `초당 / 거래건수 / rate limit / exceeded / too many` 중 하나 포함 시 1.1s sleep 후 재시도
- HTTP 5xx + valid JSON + `rt_cd != 0` → `BrokerResponseError` (application error)
  - 순수 transport 실패 + rt_cd 없음 → `BrokerNetworkError`
  - 이 분리가 없으면 KIS의 application-level rate-limit이 transport error로 잘못 분류됨
- 모든 호출은 audit jsonl에 `{ts, env, endpoint, method, request_id, dry_run, http_status, latency_ms, request_summary, response_summary, error?}`로 기록
- 응답 헤더의 `tr_cont`는 body에 `_tr_cont` 키로 surfaced (paging 신호)

### 4.2 wired endpoints

| 기능 | path | TR_ID (paper) | 메서드 |
|---|---|---|---|
| Token | `POST /oauth2/tokenP` | — | `ensure_token()` |
| Quote | `GET /uapi/overseas-price/v1/quotations/price` | `HHDFS00000300` | `get_quote(symbol, market)` |
| Balance | `GET /uapi/overseas-stock/v1/trading/inquire-balance` | `VTTS3012R` | `get_positions(market)`, `get_positions_all_us()` |
| Cash | `GET /uapi/overseas-stock/v1/trading/inquire-psamount` | `VTTS3007R` | `get_cash(market, ref_symbol)` |
| Order history | `GET /uapi/overseas-stock/v1/trading/inquire-ccnl` | `VTTS3035R` | `get_order_history(start, end)` |

Live TR_ID는 `TR[...]['live']`에 모두 등록되어 있음. 모의/실전 분기는 `EnvConfig.is_paper`로 한 곳에서 결정.

### 4.3 Paging 처리

`inquire-balance` / `inquire-ccnl`은 30 rows/page로 잘려서 옴.

```
첫 호출:  CTX_AREA_FK200='', CTX_AREA_NK200=''
응답 헤더: tr_cont = 'F' or 'M' (more) | 'D' or 'E' (last)
응답 body: ctx_area_fk200, ctx_area_nk200
다음 호출: 위 ctx 값 + 헤더 tr_cont='N'
```

이 protocol을 `get_positions` / `get_order_history` 둘 다에 loop로 구현. `max_pages` 안전 캡 (50 / 20). 진행 페이지 수는 verbose 라인에서 그대로 확인 가능.

### 4.4 모의-특이 동작 발견

- **OVRS_EXCG_CD가 paper에서는 사실상 무시됨**: NASD/NYSE/AMEX 어떤 키로 부르든 동일 종목셋 반환. → `get_positions_all_us()`는 paper에서 NASD 1회 + paging + dedup으로 처리.
- **체결내역 paper 제약**: PDNO / SLL_BUY_DVSN / CCLD_NCCS_DVSN / OVRS_EXCG_CD / SORT_SQN 모두 wildcard or `00` or `%` or `DS`만 받음. 코드에서 paper 분기로 강제.

### 4.5 실제 호출 결과 (2026-05-11 12:21~12:26 KST)

| 항목 | 결과 | 비고 |
|---|---|---|
| `ensure_token` | `eyJ0***6nA`, 24h TTL, mode 600 cache | 152ms |
| `get_quote('AAPL')` | last=$293.32 | 시간외 (base 가격으로 fallback 동작 확인) |
| `get_positions(market='NASD')` | **83 종목** | 3 페이지 follow, 약 8초 |
| `get_cash(ref='AAPL')` | available = **USD $15,499.68** | psamount는 price/symbol 필수, ref quote 자동 조회 |
| `get_order_history(7d)` | **14건** (odno/pdno/side/qty/px 정상) | side 01=매도, 02=매수 |
| Rate-limit 자동 복구 | 1차 probe에서 발동, 2차 probe에서 throttle만으로 100% 성공 | backoff 1.1s |
| Audit jsonl 누출 검사 | `clean / clean` (2 keys grep) | 자동 |
| Paper 평균 latency | 2.5~7s | 운영 설계 시 캐싱 필수 |

샘플 verbose 한 줄:
```
[paper] 12:21:13  GET  inquire-balance     tr=VTTS3012R       status=200  rt_cd=0    5869ms  OVRS_EXCG_CD=NASD  모의투자 조회가 계속 됩니다.
```

---

## 5. 보안 / 안전 장치 인벤토리

| 보호 | 위치 | 동작 |
|---|---|---|
| `.env` 절대 commit 안 됨 | repo `.gitignore` `*` default-deny + 명시 화이트리스트 | `git check-ignore -v .env` → ignored |
| `.env` 파일 권한 | `chmod 600` | 소유자만 read/write |
| Token cache 권한 | tmp file → `chmod 600` → atomic replace | 소유자만 |
| env 검증 | `load_env_config` | required 변수 누락 / 형식 오류 → `EnvConfigError` |
| Secret 마스킹 | `_mask_obj` substring 매칭 | 콘솔 + audit jsonl 양쪽 |
| Token env 일치 검사 | `TokenCache.load` | paper/live env 다르면 캐시 거부 (cross-env 토큰 사용 봉쇄) |
| BUY-only 강제 | `SafetyGuard.assert_can_submit` | SELL은 명시적으로 unlock 필요 |
| Global halt | `SafetyState.global_halt` | True면 모든 주문 거부 |
| **Live 3중 잠금** | `SafetyGuard.allow_live_order` | `KIS_ENV=live AND dry_run=False AND KIS_CONFIRM_LIVE=true` 셋 모두 True여야만 전송 |
| Audit shape sanitiser | `_describe_shape` | response를 count로만 기록 → 종목/가격 PII 최소화 |
| Auto-leak guard | `_self_check` 마지막 단계 | audit line에 raw secret 발견 시 exit 4 |

---

## 6. Codex blueprint 매핑

Blueprint §17.9 (KIS v0 첫 구현 범위) 기준 체크:

| Blueprint 요구 | 우리 상태 |
|---|---|
| env 로더 (paper/live + 필수 4종 + 선택 2종) | ✓ §2 `EnvConfig` |
| token 발급/캐시 (만료 margin, env 일치) | ✓ §5 `TokenCache` + §9b `ensure_token` |
| overseas quote / balance / order status (read-only) | ✓ Step 2 |
| dry_run guard (모든 주문 경로 default True) | ✓ `place_order(dry_run=True)` 기본값 |
| confirm_live 게이트 (env + flag + 함수 인자 3중) | ✓ `SafetyGuard.allow_live_order` |
| jsonl 감사 로그 (per-day, 마스킹) | ✓ `AuditLogger` |
| 단일 파일 허용 | ✓ `kis_broker_adapter.py` |

Blueprint §17.6 (dry_run vs confirm_live):
- `dry_run` = 호출 사이트가 끄지 않으면 전송 안 함
- `confirm_live` = env-level human ack (.env 또는 운영자가 명시적으로 켬)
- 둘 다 켜져야 real order. 우리 구현은 이 정의를 그대로 enforce.

Blueprint §17.8 (paper 지원 범위 사전 확인 gate):
- ✓ paper에서 token / quote / balance / psamount / ccnl 동작 확인 (이 문서 §4.5)
- 미확인: paper에서 **실제 주문 접수 (order)** — Step 4에서 확인 예정

---

## 7. 알려진 제약 / 결정 사항 (codex 검토 포인트)

### 7.1 결정한 것

1. **`.env` 위치는 `0316-/` repo root**, `.gitignore`의 default-deny `*`가 자동 막음. 사용자 home으로 옮기는 옵션은 명시적 컨벤션 위반 우려 + path resolution 복잡도 증가로 채택 안 함.
2. **Audit/token cache는 home 디렉터리** (`~/.kis_audit/`, `~/.kis_token_cache.json`). repo 밖에 두어 백업 동기화 / git 우연 commit 위험 zero. `.env`로 override 가능.
3. **Mock adapter 별도 클래스**: 동일 인터페이스, 호출 사이트가 mode flag로 선택. 테스트와 dev iteration이 zero-network로 완전히 닫힘.
4. **단일 파일 1,480 lines**: blueprint §17.1 권장대로 유지. 1,500 line 근접하므로 Step 3 진입 시 (a) `audit/safety/types` 분리 또는 (b) 그대로 두고 모듈 import 도입 둘 중 결정 필요. **codex 의견 요청**.
5. **Buy-only가 default**: blueprint Stage 2 정책. 사용자가 SELL을 풀려면 명시적으로 `SafetyState.buy_only_mode=False` 설정해야 함.

### 7.2 발견한 paper 제약 (Step 3+ 설계에 영향)

| 항목 | 제약 | 코드 영향 |
|---|---|---|
| 주문 가격 | paper는 `ORD_DVSN=00` (LIMIT)만 가능 | `OrderIntent.order_type` MARKET → paper에서는 LIMIT 강제 변환 필요. limit price는 직전 quote.last 사용 권장. |
| 매수 TR_ID | `VTTT1002U` | TR 매트릭스 등록됨 |
| 매도 TR_ID | `VTTT1001U` (참고: 일부 문서엔 `VTTT1006U`로 잘못 나옴, **검증된 값은 1001**) | TR 매트릭스 등록됨 |
| OVRS_EXCG_CD | paper에서 무시됨 (NASD만으로 충분) | `get_positions_all_us()` paper 분기 적용 완료 |
| `inquire-ccnl` 필터 | paper는 wildcard만 | 코드에서 paper 분기 강제 완료 |
| 응답 latency | 평균 2.5~7s | 운영 흐름은 호출 횟수 최소화 + 캐싱 전제 |
| Rate limit | ~4 TPS, 메시지 한국어 | self-throttle 350ms + 1회 retry로 흡수 |

### 7.3 의식적으로 보류한 것

- 본 단계에서 **현금/포지션 종합 조회 API** (`inquire-present-balance`, `VTRP6504R`)는 wiring하지 않았다. `inquire-psamount`로 buying power, `inquire-balance`로 holdings를 따로 가져오는 게 분리 책임에 더 적합하다고 판단. codex 의견 필요.
- 호가 (`inquire-asking-price`)와 일봉 (`dailyprice`)도 보류. 주문 가격 산정은 우선 직전 quote의 `last`만으로 충분.

---

## 8. 남은 과제 — 우선순위 별

### 8.1 Step 3A — Local-vs-broker reconcile report (다음)

**왜**: blueprint 원칙 1 (브로커 = source of truth) 의 첫 코드화. 현재 phase3는 `holdings_log.xlsx` 등 로컬 상태를 진실로 다루고 있으므로, **두 상태의 차이를 보이는 것 자체가 첫 운영 가치**.

**범위**:
- `phase3/autotrade/reconcile.py` 신설
- Input: 로컬 holdings 소스 (phase3 안에서 가장 신뢰할 만한 파일 — 정찰 필요), 브로커 실측 positions/cash
- Output: stdout 표 + `~/.kis_audit/reconcile_YYYY-MM-DD.json` (운영 자동화의 baseline)
- 버킷:
  - `local_only` — 로컬에는 있는데 브로커에 없음
  - `broker_only` — 브로커에는 있는데 로컬에 없음
  - `qty_mismatch` — 양쪽에 있지만 수량 불일치 (tolerance 0)
  - `cash_drift` — 로컬 추정 cash vs 브로커 실측 USD
- 비목표: 자동 보정. 단순 리포트만.

**예상 산출**: 첫 실행 시 paper 83종목 vs 로컬 보유종목 차이를 한 번에 시각화. 사용자가 "어디가 어긋났는지" 한눈에 확인 가능.

### 8.2 Step 3B — Order intent dry-run

**왜**: blueprint 원칙 2 (추천 ≠ 주문). 추천 CSV → KIS 주문 payload 변환을 **전송 없이** 검증.

**범위**:
- `phase3/autotrade/intents.py` 신설
- Input: phase3가 매일 생성하는 추천 (signal_transitions/*.xlsx 또는 최신 run artifact)
- Output:
  - 변환된 `OrderIntent` 리스트
  - paper-LIMIT 변환 결과 (current quote.last 기반 limit price)
  - pre-trade risk check 결과 (notional cap, position cap, buy-only, market hours 등 — blueprint §7.1 일부)
  - 실제 KIS payload (path/headers/body) 출력만, **전송 안 함**
- 비목표: 실 전송 / 실 체결 / fill 반영

**가치**: 운영자가 매일 추천 → 주문 흐름의 정확성과 안전성을 자동 보고서로 확인.

### 8.3 Step 4 — Paper 1주 LIMIT BUY 시범

**왜**: 주문 path (`POST /uapi/overseas-stock/v1/trading/order`)가 paper에서 실제로 접수되는지 확인. blueprint §17.8의 last unknown gate.

**범위**:
- 단일 종목 (예: AAPL), 1주, LIMIT @ 직전 quote.last
- `dry_run=False`, `KIS_CONFIRM_LIVE` 여전히 false (paper이므로 unrelated)
- 운영자가 `--confirm` 플래그 명시 + 사용자 확인 후에만 전송
- 결과: `PlacedOrder` (broker_order_id 포함) → 즉시 `get_order_history()`로 echo 확인
- 후처리: 로컬 mirror 갱신 코드는 **여기서 작성하지 않는다** (Step 5+에서 holdings_manager 연동 시)

**위험**: paper 종목수가 시간이 갈수록 늘어남. 단발성 검증 후 매도해 원복 권장 (sell-side 검증 겸).

### 8.4 Step 5+ — 운영 자동화 / LIVE

Blueprint Stage 2 (auto-buy only) → Stage 3 (controlled full auto) 단계. 우리 trajectory에 매핑하면:
- (a) reconcile 자동 트리거 (daily_runner 후크)
- (b) intent 생성 자동화 + 운영자 승인 UI (CLI for now)
- (c) post-trade reconcile / fill 적용 / 미체결 폴링
- (d) buy 자동화 (SELL 여전히 수동)
- (e) LIVE 진입 (`KIS_ENV=live`, `KIS_CONFIRM_LIVE=true`)

이 트랙은 Step 3+4 결과를 보고 별도 핸드오프를 만든다.

---

## 9. Codex에게 의견을 구하는 포인트

1. **`.env`/cache 위치 결정** — repo root `.env` + home `~/.kis_audit/`는 적절한가? 운영 시 다른 사용자 / 호스트 마이그레이션 시 충돌 가능성?
2. **단일 파일 1,480 lines 분리 시점** — Step 3 진입 전 분리? Step 4 후 분리? 분리 단위 추천?
3. **paper buy 시점에서 limit price 산정 정책** — 직전 quote.last로 충분한가? slippage buffer (e.g. +0.5%) 권장? OOH (Outside Hours) 시 base 가격 사용에 대한 의견?
4. **reconcile tolerance** — qty는 0이 자연스러운데, cash는 환전/수수료 때문에 drift 발생. USD 절대값 tolerance vs 비율 tolerance, 어느 쪽 추천?
5. **multi-account 확장 계획** — 지금 환경변수는 단일 계좌. 향후 동일 broker에 다른 계좌 추가 시 schema는?
6. **paper 실험 종료 후 정리 정책** — Step 4 buy 후 자동 sell로 원복할지, 의도적으로 portfolio 누적 갈지?
7. **inquire-present-balance (VTRP6504R) 미연동 결정** — buying power와 holdings를 따로 보는 게 맞다고 봤는데, 종합 조회를 차라리 한 곳으로 통일하는 게 운영에 더 단순할 수도 있음. 의견?
8. **Audit log retention** — 일별 jsonl이 무한 누적. 90일 자동 만료? S3 like로 따로 분리?

---

## 10. 운영 cheat sheet (참고)

```bash
# 0. 환경
cd /Users/shin-il/PyCharmMiscProject/0316-
export PYTHONPATH=.

# 1. Step 1 자가 검증 (no network)
python3 -m phase3.autotrade.kis_broker_adapter --self-check

# 2. 단일 호출 검증 (실 모의서버)
python3 -m phase3.autotrade.kis_broker_adapter probe token
python3 -m phase3.autotrade.kis_broker_adapter probe quote --symbol NVDA
python3 -m phase3.autotrade.kis_broker_adapter probe positions --all-markets
python3 -m phase3.autotrade.kis_broker_adapter probe cash --symbol AAPL
python3 -m phase3.autotrade.kis_broker_adapter probe history
python3 -m phase3.autotrade.kis_broker_adapter probe all

# 3. 실시간 audit 추적 (별도 터미널)
tail -f ~/.kis_audit/$(date +%Y-%m-%d).jsonl

# 4. token 강제 재발급 (만료 직전 / paper-live 전환 시)
rm ~/.kis_token_cache.json
python3 -m phase3.autotrade.kis_broker_adapter probe token

# 5. gitignore 확인 (.env 실수 commit 방지)
cd 0316- && git check-ignore -v .env
```

---

## 11. 다음 핸드오프 트리거

- Step 3A reconcile 첫 보고서 결과 → 차이 버킷 분포가 의외이면 정책 검토
- Step 3B dry-run에서 추천 → payload 변환 시 검출되는 corner case (음수 qty, 미상장 ticker, 시장 외 종목 등)
- Step 4 paper buy 결과 → KIS 응답 필드 매핑 검증 / fill latency / sell 원복 시 발생하는 결제일 이슈

이 셋 중 하나라도 의도 외 동작이 보이면 코드 진행 멈추고 본 핸드오프를 갱신해서 codex 재검토를 받는다.

---

(end)
