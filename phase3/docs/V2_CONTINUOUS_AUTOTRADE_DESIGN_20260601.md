# V2 — 무한(hands-off) Auto-Trade 시스템 설계

작성일: 2026-06-01
선행: V1-F(2-fire) / V1-G(SELL) / V1-H(reprice 회복력) / V1-I(호가 점프 + 갭 필터) 완료 후.

> 목표(운영자 원안): **"컴퓨터만 켜두면, 휴장이 아닌 날 알아서 오전 캐시 업데이트 → 개장 시
> auto-trade → 다음 개장일에도 반복. 내가 별도로 멈추지 않는 한, 비정상 상황에서 abort되는 것만
> 아니면 계속 거래하는 무한 시스템."**

---

## 1. V1 완료 평가 — "V1은 이걸로 마무리할 수 있나?"

**결론: 기능(파이프라인) 관점에서 V1은 사실상 완료. 남은 것은 전부 "무한 운영" 속성(V2)이다.**

| 영역 | 상태 | 근거 |
|---|---|---|
| 추천 생성(T7 prefetch, 07:20) | ✅ | 2-fire 분리, 단독 메일, 패널 로그 tail |
| intent 생성 (BUY+SELL 병합) | ✅ | V1-G, 캐노니컬 RecosAction parity |
| BUY 체결 (호가 점프 + chase) | ✅ | V1-I (use_quote + 살짝 낮게 시작 + 갭필터 15%) |
| SELL/STOP_LOSS/TRIM 집행 | ✅ | V1-G, 6/1 STOP_LOSS 실거래 확인 |
| reprice 회복력 / 부분배치 | ✅ | V1-H (continue-on-unfilled, benign skip) |
| T10 적용 + 현금/보유 정합 | ✅ | t10_applicator, reconcile |
| 사후 리포트/메일(R11B) | ✅ | 6/1 clean rc=0 |
| 패널 진행률/로그 | ✅ | v1_status + ScrolledText tail |
| 수동 STOP veto | ✅ | `global_halt.py` (assert_not_halted) |
| **일일 arm(사람 개입)** | ⚠️ 의도적 | V1의 안전장치. V2에서 영속 arm으로 대체 대상 |

**V1에 남은 "버그/미완" 성격의 일은 없음.** 아래는 전부 V2(무한 운영)의 신규 속성이다.

---

## 2. V1 → V2로 가기 위해 닫아야 할 4개의 갭

현재 무한 운영을 막는 것은 4가지뿐이다:

| # | 갭 | 현재 동작 | V2 목표 |
|---|---|---|---|
| G1 | **일일 arm 필요** | 매일 `arm-today` 안 하면 rc=0 skip | **영속(standing) arm** — 멈추기 전까지 유효 |
| G2 | **휴장일 인지 없음** | 평일 휴장(예: 7/4)에도 fire가 돔. 캐시는 전일에 머물고, 개장 안 했으니 호가 stale → 헛발사 | **trading-calendar 게이트** — 비거래일 clean skip |
| G3 | **자동 lockout 없음** | 연속 실패/이상에도 매일 계속 시도 | **자동 정지** — 연속 실패·포트폴리오 급락·데이터 stale 시 global_halt 자동 set |
| G4 | **미발사/슬립 복구 없음** | Mac이 자고 있으면 그 세션은 그냥 누락 | (선택) 깨어난 직후 "오늘 발사 놓쳤나?" 복구 + sleep 방지 |

G1+G2+G3는 **함께** 가야 안전하다 (영속 arm만 켜고 G2/G3가 없으면, 휴장일에도 쏘고 실패해도
계속 쏘는 위험한 시스템이 된다). G4는 그 다음 단계.

---

## 3. 컴포넌트 설계

### 3.1 G1 — 영속 arm (`standing arm`)
- 신규 토큰 `runtime/v1_standing_arm.json` (날짜 없는 단일 파일). 바디: `armed_by`, `armed_at`,
  `note`, `schema_version`. 존재 = "멈추기 전까지 매 거래일 발사 OK".
- 게이트 로직(`require_armed_for_today` 확장 또는 신규 `require_armed_v2`):
  1. `global_halt` set → **skip**(최우선 veto, 이미 존재).
  2. standing arm 파일 존재 → **armed**.
  3. (하위호환) 오늘자 일일 토큰 존재 → armed.
  4. 그 외 → skip(rc=0).
- 패널 버튼: 기존 "Arm today" 옆에 **"Arm continuously / Stop"** 토글. Stop = standing arm 파일
  삭제(+선택적으로 global_halt set).
- CLI: `v1_runner arm-standing` / `disarm-standing`.
- **안전**: standing arm이 있어도 G2(달력)·G3(lockout)·global_halt가 매 fire마다 우선 평가된다.

### 3.2 G2 — trading-calendar 게이트 (`trading_calendar.py`)
- 신규 import-light 모듈. NYSE 정규 휴장일을 하드코딩(연 1회 갱신) + 주말.
  - 고정/계산 가능 휴일: 신정, MLK, Presidents, Good Friday, Memorial, Juneteenth,
    Independence, Labor, Thanksgiving, Christmas (관측일 규칙 포함).
  - (선택) 반일장(조기 마감)은 일단 정상 거래일로 취급 — 개장은 하므로.
- API: `is_trading_day(d_et: date) -> bool`, `next_trading_day(...)`.
- 두 fire 모두 시작 직후 게이트:
  - **거래일 판정은 ET(미 동부) 날짜 기준** (KST가 아니라). 22:35 KST = 당일 09:35 ET.
  - 비거래일 → `rc=0` clean skip + 로그 1줄("holiday/weekend — skip"). 메일 없음(소음 방지).
- 테스트: 알려진 2026 휴장일 셋 + 주말 + 관측일(예: 7/4가 토요일이면 7/3 휴장) 픽스.

### 3.3 G3 — 자동 lockout (`auto_halt` 평가기)
무한 시스템의 핵심 안전장치. 매 fire **종료 시** 상태를 평가해, 임계 초과면 `global_halt`를
자동 set(=다음 fire부터 자동 skip) + 운영자 경보 메일.

평가 신호(초기 후보):
| 신호 | 트리거(예시, 결정 필요) | 의미 |
|---|---|---|
| 연속 실패 | 최근 N=**3**회 fire가 rc≠0 | 구조적 고장 (코드/계정/네트워크) |
| 포트폴리오 급락 | 1일 -**?**% 또는 누적 -**?**% | 전략/시장 이상, 사람이 봐야 함 |
| 데이터 stale | T7 캐시가 마지막 거래일보다 오래됨 | FMP 장애 → 잘못된 추천 방지 |
| 정합 drift | reconcile qty_mismatch>0 또는 cash_drift 과대 | 브로커 vs 로컬 불일치 |

- 상태 이력: `runtime/v1_fire_history.jsonl`(append) — fire별 rc/run_id/포트폴리오값/타임스탬프.
- 평가기: `auto_halt.evaluate(history, portfolio) -> Optional[HaltDecision]`. 순수함수 → 테스트 용이.
- lockout이 걸리면 운영자가 원인 확인 후 패널 "Clear halt"로 해제(이미 `clear_halt` 존재).
- **이게 운영자가 말한 "비정상적 상황에서 abort"의 자동화 버전.**

### 3.4 G4 — 미발사 복구 + sleep 방지 (선택, 후순위)
- **sleep 방지**: `pmset`/`caffeinate`로 발사 시간대만 깨어있게. 또는 launchd
  `StartCalendarInterval`은 슬립 중 놓친 fire를 깨어난 직후 1회 실행해주는 특성 활용(확인 필요).
- **미발사 복구**: 깨어난 직후 게이트가 "오늘 ET 거래일인데 22:35 fire 기록이 없다 + 아직
  장중이다" → 1회 따라잡기. 단 **장 마감 후엔 절대 따라잡지 않음**(stale 진입 방지).
- G4는 신뢰성 향상이지 정확성 필수는 아니므로 G1~G3 안정화 후.

---

## 4. 운영자 결정 필요 (구현 전)

1. **영속 arm 해제 방식**: Stop 누르면 (a) standing arm 파일만 삭제, (b) + global_halt도 set
   (확실한 정지)? → 권장 (a), 긴급정지는 별도 STOP 버튼(global_halt).
2. **연속 실패 lockout 임계**: N회? (권장 3)
3. **포트폴리오 급락 lockout**: 1일 낙폭 임계 %? 누적 낙폭 임계 %? (예: 1일 -8%, 누적 -20%)
   — 모의계좌 초기화 후 기준값(시작 자본)으로 잡을지.
4. **데이터 stale 임계**: 캐시가 마지막 거래일보다 며칠 뒤지면 halt? (권장 1거래일 초과 시)
5. **반일장 처리**: 정상 거래일로 둘지(권장) 별도 취급할지.
6. **G4(슬립/복구) 포함 시점**: G1~G3와 같이 / 나중에.

---

## 5. 권장 구현 순서 (3 PR)

- **V2-A (달력 + 영속 arm)**: `trading_calendar.py` + standing-arm 게이트 + 패널 토글 + 테스트.
  → 이걸로 "휴장 아닌 날 알아서 발사, 매일 arm 불필요"가 달성된다(원안의 핵심).
- **V2-B (자동 lockout)**: `v1_fire_history.jsonl` + `auto_halt.evaluate` + fire 종료 hook +
  경보 메일 + 테스트. → "비정상 시 자동 abort"가 달성된다.
- **V2-C (슬립/미발사 복구)**: caffeinate/pmset + 따라잡기 게이트. → 신뢰성 마감.

V2-A + V2-B가 끝나면 운영자 원안("멈추기 전까지 알아서 도는 시스템")은 **완성**이다.
V2-C는 "Mac이 자도 안 놓친다" 수준의 보강.

---

## 6. 기존 자산 재사용 (신규 인프라 최소화)
- `global_halt.py` — 자동 lockout의 실제 정지 메커니즘 그대로 사용(write_halt/clear_halt 존재).
- `v1_arm.py` — 게이트 패턴/원자적 쓰기/KST 헬퍼 재사용, standing-arm만 추가.
- `v1_status.py` / 패널 tail — V2 fire도 그대로 진행률 표시.
- R11B 메일 — lockout 경보 채널로 재사용.
- 2-fire launchd(plist) — 스케줄 그대로, 게이트만 fire 진입부에 추가.

---

## 부록. 현재 휴장일 동작(설계 근거, 확인됨/추정)
- 주말: 5/31(일)은 arm 토큰 없어서 skip됨(로그 확인). **단 이는 "arm 없음" 때문이지 "주말 인지"
  때문이 아니다** — 영속 arm을 켜면 주말에도 발사 시도하게 되므로 G2가 반드시 필요.
- 평일 휴장: 캐시가 전일에 머묾 → T7은 같은 추천 재생성, 22:35엔 미개장이라 호가 stale →
  주문은 미체결(benign skip, V1-H/I 덕에 안전하지만 무의미한 발사). G2로 깔끔히 차단.

---

## 7. 구현/배포 현황 (2026-06-02 적용)

운영자 확정값으로 **G1~G3 구현·테스트·배포 완료** (G4 슬립/복구는 보류).

### 확정된 파라미터
- 영속 arm 해제 방식: **(a) standing arm 파일 삭제만** (`disarm-standing`). `global_halt`은 건드리지 않음.
- 연속 실패 lockout 임계: **3** (연속 3회 trade-fire `rc!=0`).
- 포트폴리오 급락 lockout: **누적 -20%** (시작 자본 대비; 시작 자본 = history 최초 equity, 리셋 시 재기준).
- 데이터 stale 임계: **1거래일 초과**(scoring 종가가 세션보다 2거래일+ 뒤처지면 trip; 정상 lag는 trip 안 함).
- 반일장: **정상 거래일 취급**(개장은 정상, 우리는 개장 직후만 체결 → 조기 *마감*은 무관, 리스크 없음).

### "lockout"의 의미
이상 징후 감지 시 시스템이 `global_halt`(=STOP)을 **자동으로 latch**. 이후 모든 fire가
거래 없이 clean skip(rc=0). **운영자가 원인 확인 후 패널 Clear를 누를 때까지** 잠김.
= "시스템이 알아서 누른 STOP, 수동 해제 전까지 지속".

### 신규 모듈/파일
- `autotrade/trading_calendar.py` — 주말+NYSE 휴장(Computus 기반 Good Friday, 주말 observance). (G2)
- `autotrade/v1_arm.py` — `write/read/clear_standing_arm`, `today_kst_date`, 게이트에 standing 우선. (G1)
- `autotrade/auto_halt.py` — `record_fire`/`read_history`(`v1_fire_history.jsonl`), `evaluate_lockout`(순수), `apply_lockout`. (G3)
- `v1_runner.run_v1_pipeline` 진입부 게이트 순서: **달력 → global_halt → arm(standing/daily)**. 모두 clean skip(rc=0).
- `v1_runner` trade fire 종료 시: `record_fire_and_evaluate_lockout` → history 기록 + lockout 평가/latch.
- CLI: `arm-standing` / `disarm-standing` / `standing-status`.
- `reset_paper_state.py` — 리셋 시 fire history도 아카이브(낙폭 기준 재anchor).

### 배포 상태(6/2 미장 전)
- launchd 2-fire 재설치(V1-I env 반영) 완료. `standing arm` 장착, `global_halt` clear, 오늘=거래일 → 발사 예정.
- 오늘은 패널 표시용으로 daily 토큰도 같이 장착(belt&suspenders). 게이트는 standing 우선 사용.

### 남은 후속작업(follow-up)
- **패널의 standing-arm 인식**: 현재 패널 readiness는 daily 토큰 기준 → 내일부터 daily 토큰이 없으면
  패널은 "not ready"로 보이지만 **실제 거래는 standing arm으로 정상 진행**됨. 진실 소스는 `standing-status`.
  패널에 standing-arm/lockout 상태 표시 추가 예정.
- **G4 (슬립/미발사 복구)**: caffeinate/pmset + 미발사 따라잡기. (운영자 요청대로 G1~G3 후 별도 진행)
