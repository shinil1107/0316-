# V1-I 설계 검토 — Pre-market 가격 기반 갭(gap) 대응

- 작성일: 2026-05-30 (KST)
- 상태: **검토/설계 단계 (미구현)** — 나중에 참고용
- 발단: 2026-05-29 22:35 KST 자동 fire에서 HPE가 reco 종가(38.21) 대비 +10.5% 갭업(시장가 42.23)
  → reprice 슬리피지 천장에 막혀 미체결 → 전체 배치 abort
- 직전 임시 조치(V1-H, 구현 완료): 슬리피지 천장 1.2%→12%, reprice 4→12회,
  미체결 시 다음 티커로 continue + t10 benign-skip. (`V1-H` 변경분 참고)

---

## 0. TL;DR

- **FMP는 pre/post-market 가격을 제공한다** (엔드포인트 확인 완료, 아래 §3).
- 그러나 "갭 대응"의 본질적 해법은 두 갈래로 나뉜다:
  1. **갭 필터링(risk control)** — 너무 크게 갭한 종목은 *아예 사지 않는다*. 이게 백테스트
     parity상 가장 중요하다(§2). pre-market 없이 **22:35 submit 시점의 KIS 라이브 호가**만으로도
     구현 가능 (신규 인프라 0).
  2. **초기 지정가 개선(fill rate)** — 개장 전/직후 가격을 미리 반영해 초기 limit을
     시장가 근처로 잡아 reprice 횟수·노출시간을 줄인다. 여기서 pre-market fetch가 의미를 가진다.
- **권장 순서**: 먼저 (1) 갭 필터를 22:35 fire에 KIS 호가로 붙이는 게 비용 대비 효과 최고.
  pre-market 전용 fire(§5-B)는 "장 시작 전 사전 통보/사전 제외"가 필요할 때의 풀버전.

---

## 1. 문제의 구조 (왜 갭이 우리를 때리는가)

### 1.1 타임라인
```
07:20 KST  T7 prefetch   — 추천 생성. 입력은 "직전 미국장 종가" 캐시.
                            recommendations.csv 의 Price = 그 종가.
22:30 KST  US 개장 (EDT 기준; EST면 23:30)
22:35 KST  trade fire     — submitted_intents.json 의 limit = reco 종가(+pad).
                            개장 직후 라이브 호가로 reprice 추격.
```

### 1.2 백테스트 vs 라이브의 체결가 괴리 (parity 핵심)
`simulator.py: apply_actions()` 는 **신호 당일 종가**(`close_arr[di]`)에 + 5bps 슬리피지로
체결한다. 즉 백테스트는 "신호가 난 그 날 종가에 산다"를 가정한다.

라이브는 신호(전일 종가 기준)를 **다음 세션 개장 직후**에 집행한다. 종목이 밤사이 뉴스로
+10.5% 갭업하면, 라이브 진입가는 백테스트가 가정한 가격보다 +10.5% 불리하다.

> **결론(중요): 큰 갭을 추격해서 사는 것은 백테스트가 검증한 행동이 아니다.**
> 백테스트는 +12% 비싼 가격에 산 적이 없다. 따라서 큰 갭 종목을 12% 천장까지 쫓아가 체결시키는
> 현재 V1-H 동작은 "체결률"은 올리지만 **전략 parity를 깨는 방향**일 수 있다.
> → 갭 필터(사지 않기)는 parity 관점에서 오히려 **더 충실한** 선택이다.

### 1.3 V1-H(12% 천장)의 잔여 리스크
- 12% 천장은 "체결은 시키자"는 공격적 선택. 모멘텀 추격이라 진입 단가가 나빠질 수 있다.
- 즉 V1-H는 "거래를 못 하는 것보다는 낫다"는 절충이지, 갭 자체의 해법은 아니다.
- V1-I는 이 천장을 **낮추고**(예: 3~5%로 환원), 대신 "갭이 크면 안 산다"로 정책을 바꾸는 게
  더 건강한 균형일 수 있다. (열린 결정사항 §8)

### 1.4 "왜 단계별 chase인가? 그냥 호가로 바로 점프하면 안 되나" (2026-06-01 추가)

운영자 질문: *"submit 시점에 현재 호가를 받아올 수 있으면, 단계별 chase 없이 처음부터
호가 근처로 limit을 내면 되는 것 아닌가?"* — **맞다. 그리고 그 모드는 이미 코드에 있다.**

**사실관계 (코드 확인):**
- `kis_broker_adapter.get_quote_with_exchange_fallback()` 가 `last/bid/ask` 를 반환한다.
  manage loop의 reprice는 **이미 매 rung마다 이 라이브 호가를 호출**한다(`quote_chase`).
  즉 22:35 서브프로세스에는 어댑터+호가가 이미 살아 있다. (22:30 개장 직후라 ask/last가
  실시간 값. 장 마감 후엔 `base`=전일종가로 폴백.)
- `run_generate_intents(use_quote=True, quote_only=True)` 를 켜면 **초기 limit을 라이브
  ask 근처**(`ask × (1+quote_pad)`)로 낸다 — `candidates_to_intent_rows`의 `quote_only` 모드.
- **그러나 22:35 fire는 `use_quote=False`가 기본값**이라, 현재는 BUY·SELL 모두 **전일 종가**로
  첫 주문을 내고 chase 사다리가 전부 따라잡는다.

**그래서 5/29 HPE의 실제 시퀀스:**
```
38.21(전일종가)로 첫 submit → 60초 대기 → 미체결 → 취소
   → 그제서야 reprice #1이 라이브 호가(42.23) 조회 → 천장(38.6685)에 막힘 → 끝
```
"죽을 걸 아는 첫 주문을 전일 종가로 내보내는" 낭비가 핵심. 호가에서 출발했으면 첫 submit에
대부분 체결됐을 것이고 chase 사다리는 *드문 fallback*이 됐을 것이다.

**단, 세 가지 단서 (중요):**

1. **"호가로 점프"는 _체결 속도_ 문제를 풀지, _비싸게 사는(parity)_ 문제를 풀지 않는다.**
   +10.5% 갭한 종목을 라이브 ask로 바로 내면 = **그 +10.5%에 사버리는 것**. §1.2의 parity
   문제는 그대로 남는다. → "점프"(체결 효율)와 "갭 필터"(안 사기, parity)는 **서로 다른
   질문에 대한 답**이며 **둘 다** 필요하다.

2. **그래도 짧은 chase는 fallback으로 남겨야 한다.** 호가를 읽은 순간과 주문이 broker에
   얹히는 순간 사이에 ask가 한 틱 올라가면 옛 ask에 건 limit은 미체결될 수 있다. →
   **호가에서 출발(점프) + 작은 pad + 짧은 1~2 rung**이 이상적. 전일종가에서 12 rung 기어오르기 ✗.

3. **그러면 V1-H의 12% 천장도 낮출 수 있다.** 현재 12% 천장은 "전일종가→시장까지 기어오를
   여유"였다. 호가에서 출발하면 그 여유가 불필요해지고, 천장의 의미가 "호가를 얼마나 더
   추격할지"(예: 1~2%)로 바뀐다.

**더 깔끔한 목표 아키텍처 (현재 "전일종가에서 기어오르기" + V1-H "12% 천장 반창고"를 대체):**
```
초기 limit = 라이브 ask × (1 + 작은 pad)             ← 점프 (use_quote=True, quote_only=True)
갭 필터    = (ask − reco)/reco > 임계 → BUY 미제출       ← 안 사기 (parity, §6)
짧은 chase = drift 대비 1~2 rung, 낮은 천장(1~2%)       ← fallback (V1-H 천장 환원)
SELL/STOP_LOSS = 항상 호가 기준 + 무조건 집행             ← 갭 필터 제외
```

**왜 지금까지 `use_quote`가 꺼져 있었나 (역사적 맥락):**
- 초창기엔 "워커 프로세스에서 라이브 어댑터를 띄우는 게 실패면(failure surface) 늘어난다"는
  보수적 판단 + "reco 종가 + 0% pad"가 안전 기본값이었다(`run_generate_intents` docstring).
- 또 R10E에서 NYSE 종목 호가가 `ask=0`으로 와서 전일종가로 폴백 → 과대 limit 버그가 있었다
  (이후 `get_quote_with_exchange_fallback`의 다중거래소 폴백으로 수정됨).
- **그러나 manage loop는 이미 reprice에서 라이브 호가를 쓰고 있으므로**, intent 생성에서도
  쓰는 추가 리스크는 이제 작다. → `use_quote=True` 전환의 기술적 장벽은 사실상 없음.

> **요약**: 운영자 직관대로 "호가로 바로 점프"가 가능하고 코드도 이미 있다(`use_quote`).
> 이게 §5-A(갭 필터)와 **결합**되면 5-A의 더 강한 버전이 된다: 호가로 점프하되, 갭이 크면
> 아예 안 산다. 5-B(pre-market fire)는 여전히 "개장 전 사전 제외/통보"가 필요할 때의 풀버전.

---

## 2. 두 가지 독립 기능으로 분리해서 보기

| 기능 | 목적 | 필요 데이터 | pre-market 필요? |
|---|---|---|---|
| **A. 갭 필터** | 큰 갭 종목을 intent에서 제외 (risk/ parity) | 개장 직전·직후 "현재가" 1개 | 선택 (라이브 호가로도 가능) |
| **B. 초기 지정가 개선** | limit을 시장가 근처로 잡아 reprice 최소화 | 개장 직전 "예상 체결가" | 있으면 유리 |

두 기능은 **같은 가격 입력**을 쓰지만 목적이 다르다. A는 "거를지 말지(boolean)", B는 "얼마에
낼지(price)". A만 먼저 해도 5/29 사고는 막힌다(HPE를 그냥 스킵).

---

## 3. FMP pre/post-market 가용성 (확인 완료)

2026-05-30 FMP 공식 문서 확인 결과, pre/post-market 전용 엔드포인트가 존재한다. (일반
`/stable/quote` 는 정규장 시간에만 갱신됨.)

| 용도 | 엔드포인트 | 비고 |
|---|---|---|
| 단일 종목 시간외 호가 | `/stable/aftermarket-quote?symbol=AAPL` | bid/ask/volume |
| **배치 시간외 호가** | `/stable/batch-aftermarket-quote?symbols=AAPL,MSFT` | **N종목 한 번에** — 우리가 쓸 것 |
| 시간외 체결 | `/stable/aftermarket-trade?symbol=AAPL` | last trade |
| pre/post 통합 | `/stable/pre-post-market` (구 `/api/v4/pre-post-market/AAPL`) | bid/ask |

- 우리 코드의 FMP 접근 패턴은 `fmp_cache_updater.py: fmp_get_json()` 과 동일하게
  `?apikey=` 파라미터 방식. base는 `https://financialmodelingprep.com`.
- **확인 필요(미검증)**:
  1. 위 시간외 엔드포인트가 **현재 우리 플랜(FMP_API_KEY)에서 열려 있는지** — 일부는 상위 플랜 전용일 수 있음.
     → 실제 키로 `batch-aftermarket-quote` 1콜 찔러보고 200/403 확인 필요.
  2. **pre-market(개장 전)** 값이 채워지는지. 문서가 "aftermarket"을 강조하는데, pre-market도
     같은 엔드포인트로 나오는지(시각대에 따라) 실측 필요.
  3. 응답 스키마(필드명: `bid`,`ask`,`price`,`timestamp` 등) → 파서 작성용.

### 3.1 시간대 주의 (DST)
- KST = UTC+9. 미국 동부: EDT(3~11월)=UTC-4, EST(11~3월)=UTC-5.
- **EDT(여름)**: 정규장 22:30–05:00 KST / pre-market 17:00–22:30 KST.
  - 19:00 KST = 06:00 ET → pre-market 이른 구간(유동성 얕음).
  - 22:00 KST = 09:00 ET → 개장 30분 전(pre-market 중 가장 대표성 높음).
- **EST(겨울)**: 개장 23:30 KST. 같은 "19:00 KST"는 개장 4.5h 전이라 더 얕다.
- 사용자가 말한 "19:00 KST쯤"은 **리드타임은 길지만 가격 대표성은 낮다**. 트레이드오프 존재.
  fire 시각은 "개장 N분 전" 상대값으로 잡거나 여름/겨울 분기를 두는 게 안전(기존 07:20/22:35도
  install_v1.sh가 DST 경고만 하고 고정 로컬시각 사용 — 동일한 한계 상속).

---

## 4. 어디서 "현재가"를 가져올 것인가 — FMP vs KIS

| 소스 | 장점 | 단점 |
|---|---|---|
| **KIS 라이브 호가** (`get_quote_with_exchange_fallback`) | 이미 코드에 있음. 정규장 중이면 실제 체결 가능 가격. reprice도 이걸 씀 | **개장 전엔 값이 없을 수 있음**(해외 pre-market 호가 제공 여부 불확실). 22:35엔 이미 개장 |
| **FMP 시간외 호가** | 개장 전(pre-market)에도 값 존재 | 플랜/유동성 의존. KIS 실제 체결가와 미세 괴리 가능 |

**핵심 통찰**: 만약 갭 필터를 **22:35 submit 시점**에 건다면 시장은 이미 열려 있으므로
**KIS 라이브 호가만으로 갭%를 계산**할 수 있다(FMP 불필요). 즉:

```
gap_pct = (kis_live_ref - reco_close) / reco_close * 100
if gap_pct > GAP_FILTER_MAX:   # 예: 7%
    drop_intent(ticker)         # 사지 않음
```

→ **FMP pre-market은 "개장 전에 미리" 거르고 싶을 때만** 필요. 사용자가 원한 "장 시작 전
가격 추정"의 진짜 이득은 (a) 사전 통보, (b) submit/cancel 왕복 낭비 제거, (c) 리드타임 확보다.

---

## 5. 설계안 두 가지

### 5-A. 최소안 — "호가 점프 + submit 시점 갭 필터" (신규 인프라 0, 권장 1순위)
> §1.4 통찰 반영: 단순 갭 필터를 넘어 **초기 limit을 라이브 호가로 점프**시키는 것까지 묶는다.
- 위치: `v1_runner.run_generate_intents` → `intents_io.candidates_to_intent_rows`
  단계에서 `quote_fn`(KIS) 켜고, (a) 초기 limit을 호가 기준으로 잡고 (b) 갭 임계 초과분을 drop.
- 변경점:
  - 22:35 fire의 `use_quote=True, quote_only=True` 로 전환(이미 코드 경로 존재, 현재 off).
    → **초기 limit = 라이브 ask × (1+pad)** 로 나가 첫 submit에 대부분 체결. chase는 fallback화.
  - `candidates_to_intent_rows`에 `gap_filter_max_pct` 인자 추가:
    quote_ref vs reco_price 갭 계산 → 초과 시 그 후보를 제외하고 `IntentBuildWarning` 기록.
  - (선택) V1-H의 `AUTOTRADE_MAX_SLIPPAGE_BPS` 를 1200→100~200으로 환원, 
    `AUTOTRADE_MAX_REPRICE_ATTEMPTS` 도 2~3으로 축소 (호가에서 출발하므로 긴 사다리 불필요).
  - 제외 내역을 R11B 메일/리포트에 "갭 필터로 N종목 제외" 섹션으로 표기.
- 장점: 당일 구현 가능, FMP 의존 없음, parity에 충실(안 사면 그만), **체결 지연(첫 60s 낭비) 제거**.
- 한계: 개장 후에야 결정 → 리드타임 없음. pre-market 급등은 개장 직후 호가로만 관측.
- 리스크/검증: 개장 직후 호가 변동성(오프닝 옥션) — 첫 limit이 너무 공격적이면 순간 고점 체결
  가능. pad와 짧은 chase로 완충. `use_quote=True` 라이브 1회 검증 필요(과거 R10E ask=0 버그는
  다중거래소 폴백으로 수정됨).

### 5-B. 풀안 — "pre-market 전용 fire" (사용자 원안)
- 신규 launchd: `com.autotrade.v1.premarket` @ 개장 약 60–90분 전(여름 ~21:00–21:30 KST).
- 동작:
  1. 오늘자 T7 run의 `recommendations.csv` 로드(07:20 fire 산출물).
  2. FMP `batch-aftermarket-quote` 로 후보 티커들의 pre-market 가격 일괄 조회.
  3. `gap_pct` 계산 → 리포트 `premarket_gap_report.json` 작성:
     - 종목별 reco_close, premarket_price, gap_pct, 필터 판정(keep/drop).
  4. (옵션) **사전 통보 메일** — "오늘 X종목 갭 과대로 제외 예정" 운영자 통지.
  5. (옵션) 필터 결과를 artifact로 남겨 22:35 fire가 읽어 **drop 목록 적용** + 초기 limit을
     premarket 가격 기반으로 잡음(기능 B).
- 장점: 리드타임 확보, 개장 전 사전 제외(왕복 낭비 0), 운영자 통보.
- 단점: 신규 fire/plist/테스트/실패모드 증가. pre-market 유동성 얕아 가격 노이즈. DST 분기 필요.
- 의존: §3.1 FMP 플랜/스키마 실측 선행.

### 5-A → 5-B 단계적 채택 권장
먼저 5-A로 위험을 즉시 차단하고, 운영하며 "사전 통보/사전 제외"의 실익이 분명해지면 5-B로 확장.

---

## 6. 갭 필터 임계값 설계 (열린 값)

- `GAP_FILTER_MAX_PCT` (예: 5~8%): 이 이상 갭업한 BUY 후보는 제외.
- 방향성:
  - **BUY**: 갭'업'이 문제(비싸게 추격). 갭'다운'은 오히려 싸게 사는 것 → 별도 처리(아래).
  - 갭다운 과대(예: -15%)는 "밤사이 악재" 신호 → 이것도 제외할지 여부는 전략 판단.
    (백테스트는 종가 체결이라 갭다운 이득도 못 누림 → 보수적으론 양방향 큰 갭 모두 제외가 parity에 가까움)
- `SELL`/`STOP_LOSS` 측: 손절·청산은 갭과 무관하게 **반드시 집행**되어야 함
  (못 팔면 리스크가 커짐). → **갭 필터는 BUY에만 적용**. SELL은 V1-H의 천장/추격으로 체결.
- 임계값은 config나 env(`AUTOTRADE_GAP_FILTER_MAX_PCT`)로 노출해 코드 수정 없이 튜닝.

---

## 7. 구현 시 손댈 모듈 (5-A 기준 스케치)

| 모듈 | 변경 |
|---|---|
| `phase3/autotrade/intents_io.py` | `candidates_to_intent_rows`에 `gap_filter_max_pct` 추가; 초과분 drop + warning. 제외 목록 반환 |
| `phase3/autotrade/v1_runner.py` | 22:35 trade fire `use_quote=True`; `GenerateIntentsResult`에 `dropped_by_gap` 카운트; LAUNCHD_ENV에 `AUTOTRADE_GAP_FILTER_MAX_PCT` |
| `launchd/com.autotrade.v1.daily.plist.template` | 동일 env 추가(parity test 동기화) |
| R11B 메일/리포트 | "갭 필터 제외" 섹션 |
| 테스트 | 갭 초과→drop, 경계값, SELL 미적용, 빈 결과 처리, FMP/KIS quote mock |

5-B 추가 시: `premarket_runner.py`(신규), `com.autotrade.v1.premarket.plist`,
FMP batch-aftermarket 파서, `premarket_gap_report.json` 스키마, install_v1.sh 3-fire 확장.

---

## 8. 열린 결정사항 (사용자 확인 필요)

1. **초기 limit을 호가로 점프(`use_quote=True`)할지** (§1.4): 현재 전일종가 출발 + 긴 chase
   → 호가 출발 + 짧은 chase 로 전환할지. (권장: 전환 — 첫 60s 낭비 제거, 체결 효율↑)
2. **V1-H 천장(12%) 유지 vs 환원**: 호가 점프 + 갭 필터 도입 시 천장을 1~2%로 낮춰 "큰 갭=안
   산다"로 갈지, 12%를 유지해 "그래도 산다"로 갈지. (parity상 점프+필터+낮은 천장 권장)
3. **갭 필터 임계값**: BUY 제외 기준 %(5/7/8?), 갭다운도 제외할지(양방향 vs 업만).
4. **5-A vs 5-B 우선순위**: 즉시 risk 차단(5-A)만 먼저 / 처음부터 pre-market fire(5-B)까지.
5. **fire 시각(5-B 채택 시)**: "개장 N분 전" 상대 vs 고정 KST(여름/겨울 분기).
6. **가격 소스(5-B)**: FMP pre-market 단독 vs FMP로 거르고 KIS로 가격(이중화).

## 9. 사전 검증 TODO (구현 착수 전)
- [ ] **`use_quote=True` 라이브 1회 검증**: 22:35 개장 직후 KIS 호가(ask/last)가 실시간으로
      채워지는지, 오프닝 변동성에서 초기 limit이 합리적인지 (§1.4 핵심 — 가장 먼저 확인).
- [ ] 실제 `FMP_API_KEY`로 `batch-aftermarket-quote` 200 응답/스키마 확인 (§3 미검증분).
- [ ] pre-market(개장 전) 시각대에 값이 채워지는지 실측(1회 수동 콜).
- [ ] KIS 해외 어댑터가 **개장 전** 호가를 주는지 확인(5-A를 개장 전으로 당길 수 있는지 판단용).
- [ ] 백테스트 체결가 정의 재확인: `simulator.py` close 체결 + 5bps (본 문서 §1.2 근거).

---

## 부록 A. 근거 코드 위치
- 백테스트 체결가: `phase3/simulator.py` `apply_actions()` (price_map ← `close_arr[di]`, slippage 5bps).
- intent 가격 생성: `phase3/autotrade/intents_io.py` `candidates_to_intent_rows()`
  (`reco_close` / `quote_only` / floor 모드, `_quote_source` 기록).
- 22:35 fire 가격 옵션: `phase3/autotrade/v1_runner.py` `run_generate_intents(use_quote=...)`
  (현재 unattended 기본 `use_quote=False` — docstring에 reliability 사유 명시).
- reprice 추격/천장: `phase3/autotrade/order_manager.py` `reprice_limit_buy()`,
  manage_order reprice 블록 (`max_total_slippage_bps` 천장, `quote_chase`).
- FMP 호출 패턴: `fmp_cache_updater.py` `fmp_get_json()`.
- V1-H(직전 조치): `LAUNCHD_ENV` / plist의 `AUTOTRADE_MAX_SLIPPAGE_BPS=1200`,
  `AUTOTRADE_MAX_REPRICE_ATTEMPTS=12`, `AUTOTRADE_CONTINUE_ON_UNFILLED=true`;
  `daily_runner.default_manage_loop_fn` continue 로직; `t10_applicator` benign-skip.
