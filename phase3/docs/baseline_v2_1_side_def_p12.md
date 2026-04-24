# Baseline v2.1 — `SIDE_DEF_p12` 운영전략 명문화

**Cutover date**: 2026-03-28  
**Source of truth**: `phase3/config.yaml` → `strategy.exit_triggers`  
**Rollback**: 해당 블록 삭제만 하면 legacy baseline (v2.0) 으로 즉시 복귀.

---

## 1. 한 줄 요약

기존 v2.0 (BATCH11 → V2 ENS_L3_v1 신호 전환 후) 운영전략에 **regime-gated
profit-target trim** 레이어를 하나 추가했다. 그 외 모든 로직 (리밸런싱,
entry, stop_loss, sell_grace, 리짐 override 등) 은 **완전 동일**.

---

## 2. v2.0 baseline 대비 델타 (정확히 추가된 것)

### 2.1 변경되지 **않은** 것 (regression-safe)

- `rebalance_mode`, `rebalance_gap_threshold`, `buy_allocation_mode`
- `enable_trim`, `trim_threshold` (3% 리밸런싱 trim)
- `stop_loss_pct` (-15%, BULL regime only)
- `sell_grace_days` (BULL 60d / SIDE 120d / DEF 60d)
- `buy_limit_mode`, `adaptive_deploy_rate`, `target_invest_pct`
- 모든 `regime_overrides` 값
- Entry side (top-N 선정, `entry_score_margin`, rank/weight 산정)

### 2.2 새로 추가된 것: `profit_target` 트리거

`strategy.exit_triggers` 리스트에 아래 한 항목이 추가됨:

```yaml
- type: profit_target
  regimes: [SIDE, DEF]        # BULL 에는 절대 fire 안 함
  params:
    target_pct: 30.0           # 미실현 수익 30% 이상
    action: TRIM               # 전량 매도가 아닌 부분 trim
    partial_pct: 0.12          # 보유 주수의 12% (floor 적용)
    score_gate_enabled: false  # 점수 약화 조건은 끔 (약화 무관하게 익절)
    extension_enabled: true    # 20일 MA 대비 과열 조건 ON
    extension_window: 20
    extension_threshold: 0.20  # 20일 MA 대비 +20% 이상
    min_days_held: 10          # 보유 10일 미만은 스킵
```

즉 이 트리거가 **Fire 하려면 아래 5 조건이 모두 AND** 로 성립해야 한다:

1. 현재 리짐이 `SIDE` 또는 `DEF` (= `DEFENSIVE`/`CRASH`/`BEAR`) 이어야 한다.
   `BULL` 리짐에서는 절대 fire 하지 않는다.
2. 해당 포지션의 미실현 수익률이 **+30.0% 이상**.
3. 해당 포지션을 **10거래일 이상** 보유 중.
4. 현재 종가가 **20일 이동평균선 대비 +20% 이상** 이격.
   (= 20일 MA 기준 상방 돌파가 확인된 과열 영역)
5. 이 포지션에 대해 **30% tier 가 아직 기록되지 않음** (tier-memory 미사용).
   = 동일 포지션 lifecycle 동안 30% tier 에서 trim 은 **단 1회**.

위 5개 중 **하나라도 거짓** 이면 트리거는 침묵한다.

Fire 시 행동:

- 보유 주수의 `max(1, floor(shares * 0.12))` 만큼 **부분 매도** (action=TRIM)
- 매도 후 holdings 의 `ProfitTargetsHit` 컬럼에 `[30.0]` 을 기록
  (다음 날부터 이 포지션에서 30% tier 는 재발화 불가)
- 포지션 자체는 계속 유지 (full close 아님)

### 2.3 Tier memory 영속화 (live deployment 전용 추가)

Backtest sim 에서는 `SimPortfolio.holdings` 가 메모리 dict 라서 tier memory
가 자동으로 보존되지만, live 에서는 매일 `holdings_log.xlsx` 에서 새로 읽기
때문에 별도의 영속화가 필요함. 이를 위해:

- `Current` 시트에 `ProfitTargetsHit` 컬럼 추가 (JSON list 형식, 예: `"[30.0]"`)
- `HoldingsManager.holdings` property 가 해당 컬럼을 파싱해 `set[float]` 으로 반환
- 매수 시 빈 문자열 `""` 로 초기화, TRIM_PROFIT/SELL_PROFIT 체결 시 tier 추가

기존 `holdings_log.xlsx` 에 컬럼이 없어도 `_ensure_schema_up_to_date` 가 첫
실행 시 자동 마이그레이션 (기존 행은 전부 `ProfitTargetsHit=""` 로 시작).

---

## 3. 왜 이 조합인가 (설계 rationale)

| 파라미터                  | 선택 근거                                                                  |
|---------------------------|-----------------------------------------------------------------------------|
| `regimes=[SIDE,DEF]`      | BULL 리짐의 강세 추세를 자르는 행동은 CAGR 에 마이너스. SIDE/DEF 에서만 부분 익절. |
| `target_pct=30`           | D4 v2 sweep 에서 25/30/35% 중 30 이 가장 robust (CAGR+Sharpe 양쪽).         |
| `partial_pct=0.12`        | v5 micro sweep 에서 p05~p15 모두 CAGR plateau. p12 가 MDD/Calmar 최적.       |
| `extension=+20% vs MA20`  | "과열 시점에서만 익절" 로 추가 상승 여지 최대한 보존.                         |
| `min_days_held=10`        | 단기 뉴스-드리븐 rally 에 섣불리 trim 되는 것 방지.                           |
| `score_gate_enabled=false`| 점수 약화 여부와 무관하게 "기술적 과열 + 수익 확정" 이 주목적.                 |
| `action=TRIM` (not SELL)  | 포지션을 유지하며 리스크만 축소 = 추세 잔존 시 upside 포기 최소화.           |
| tier memory (30% 1회)     | 같은 날 반복 firing / 매일 firing 방지. lifecycle 당 1 trim 원칙.             |

---

## 4. 성과 지표 (D4 v5 micro-sweep 결과)

| 지표             | v2.0 baseline (BASE_explicit) | v2.1 SIDE_DEF_p12 | Δ        |
|------------------|-------------------------------|--------------------|----------|
| CAGR             | 35.92%                        | **37.65%**         | +1.73pp  |
| Sharpe           | 1.266                         | **1.326**          | +0.060   |
| MaxDrawdown      | 36.36%                        | **35.67%**         | -0.69pp  |
| Calmar Ratio     | 0.988                         | **1.056**          | +0.068   |

**해석**: 단순 익절 컷이 아니다. CRASH(=DEF) 에서 trim 한 자본이 SIDE/BULL
전환기에 재투입되어 "capital recycling" 효과가 발생, 전체 CAGR 을 끌어올림.

---

## 5. 실제 운영 시나리오 예시

아래는 **새로 추가된 동작만** 기술. BUY/HOLD/STOP_LOSS/SELL_GRACE 등
기존 로직은 v2.0 과 완전 동일.

### 시나리오 A — 정석 발화 (SIDE 리짐, 과열 rally)

- **Day 0 (SIDE)**: `AAPL` 진입. BuyPrice=$150, 10주 매수.
- **Day 15 (SIDE)**: 주가 $200 (+33%), 20일 MA $160 (가격/MA = 1.25 = +25%).
  - 조건 체크:
    - [O] SIDE 리짐
    - [O] PnL +33% ≥ 30
    - [O] days_held=15 ≥ 10
    - [O] extension +25% ≥ 20
    - [O] 30% tier 미기록
  - → **TRIM_PROFIT @ $200 × 1주** (floor(10 × 0.12) = 1주)
  - After: 9주 보유, `ProfitTargetsHit=[30.0]` 기록
- **Day 16 (SIDE)**: 주가 $210 (+40% from entry), MA20 $165 (+27%).
  - 조건 체크:
    - [X] 30% tier 이미 기록 → 침묵
  - 추가 trim 없음. 포지션 9주 그대로.
- **Day 30 (SIDE)**: 주가 $250 (+67%), 계속 과열.
  - 여전히 30% tier 때문에 trim 안 함. 상승 잔여 익익 전부 반영.

**결과**: 초기 12% 만 확정 익절, 나머지 88% 는 추세에 맡김.
v2.0 에서는 이런 상황에서 trim 전혀 없었음.

### 시나리오 B — BULL 전환 → 트리거 완전 비활성화

- **Day 0 (SIDE)**: `MSFT` 진입. PnL 0%, 15주.
- **Day 20 (SIDE)**: PnL +35%, extension +22%.
  - → Day 15/20 에서 30% tier trim 1회 발생 (시나리오 A 와 동일 로직). 13주 보유.
- **Day 25**: VIX 하락 → 리짐이 **BULL** 로 전환.
- **Day 30 (BULL)**: PnL +50%, extension +30%.
  - 조건 체크:
    - [X] regime=BULL → 트리거 regimes list 에 BULL 없음 → 즉시 skip
  - 추가 trim 없음. BULL 이 지속되는 동안 이 트리거는 완전히 봉인된다.

**설계 의도**: BULL 상승 추세는 건드리지 않는다. 새로운 tier 기록 자체가
없으므로 SIDE 로 재전환 시에도 (같은 30% tier 에서) 다시 fire 한다.

### 시나리오 C — 극단 과열이지만 보유 기간 미달

- **Day 0 (DEF)**: `GOOG` 진입 (방어 진입).
- **Day 5 (DEF)**: PnL +80% (극단 뉴스 rally), extension +50%.
  - 조건 체크:
    - [O] DEF 리짐
    - [O] PnL 80% ≥ 30
    - [X] days_held=5 < 10 → skip
  - 트리거 침묵. (단기 과열에 섣불리 못 잘라냄.)
- **Day 10 (DEF)**: PnL +50%, extension +35%.
  - 조건 체크:
    - [O] days_held=10 ≥ 10 (첫 fire 가능일)
    - [O] PnL ≥ 30, extension ≥ 20
  - → TRIM_PROFIT 발화.

### 시나리오 D — 수익은 크지만 이격 없음 (완만 상승)

- **Day 0 (SIDE)**: `NVDA` 진입.
- **Day 60 (SIDE)**: PnL +40%, 그러나 주가는 20일 MA 와 밀착 (extension +8%).
  - 조건 체크:
    - [O] PnL 40% ≥ 30
    - [O] days_held=60 ≥ 10
    - [X] extension +8% < 20 → skip
  - 트리거 침묵.
- **Day 70 (SIDE)**: 갑작스런 spike → PnL +55%, extension +25%.
  - 모든 조건 충족 → **TRIM_PROFIT** 발화.

**설계 의도**: "30% 넘으면 무조건 익절" 이 아니다. "30% + 과열" 이 함께
성립할 때만 trim. 완만 우상향 추세는 건드리지 않는다.

### 시나리오 E — CRASH 리짐에서 capital recycling

- **Day 0 (SIDE)**: `TSLA` 진입.
- **Day 30 (SIDE → CRASH 전환일)**: VIX spike, 리짐 CRASH (내부 라벨, 외부
  메트릭은 DEF 로 집계) 로 전환. PnL +35%, extension +23%.
  - 조건 체크:
    - [O] DEF (alias: CRASH) — base.py 의 alias map 덕분에 `regimes=["DEF"]`
      가 CRASH/BEAR/DEFENSIVE 모두 매칭.
    - [O] 다른 모든 조건 OK
  - → **TRIM_PROFIT** 발화. 포지션 12% 현금화.
- **Day 31+ (CRASH)**: stop_loss 는 여전히 비활성 (DEF 리짐 override).
  확보한 현금은 `adaptive_deploy_rate=0.1` 에 따라 다음 BUY_NEW 기회 시 재투입.
- **Day 40 (SIDE 복귀)**: 리짐 SIDE 로 복귀, 다른 종목 신규 진입 시 현금이
  이미 확보되어 있어 full-size 매수 가능.

**설계 의도**: "DEF 에서 왜 trim 하지? CRASH 에서 팔고 싶지 않은데" 라는
직관이 실은 잘못된 프레임. CRASH 에서의 부분 익절은 (1) 추세 꺾이기 전 수익
실현 + (2) SIDE/BULL 복귀 시 자본 여력 확보 두 효과로 system-level CAGR 상승.

### 시나리오 F — 전량 매도 트리거 (다른 레이어) 와의 상호작용

- **Day 30 (SIDE)**: `META` PnL +35%, extension +22%. → TRIM_PROFIT 발화, 12% trim, `ProfitTargetsHit=[30]`.
- **Day 45 (SIDE)**: 종목이 top-N 에서 탈락 → `sell_grace` 120일 카운트다운 시작.
- **Day 165 (SIDE)**: grace 만료 → `SELL_GRACE` fire → **전량 매도**.
  - 포지션이 portfolio 에서 제거 → `ProfitTargetsHit` 도 함께 사라짐.
- **Day 200 (SIDE)**: `META` 재진입 (BUY_NEW). 새 lifecycle 이므로
  `ProfitTargetsHit=""` 에서 다시 시작. 30% tier 재발화 가능.

### 시나리오 H — BUY_MORE 추가매수와 tier memory

- **Day 0 (SIDE)**: `AMZN` BUY_NEW. BuyPrice=$100, 10주. `ProfitTargetsHit=""`.
- **Day 20 (SIDE)**: 주가 $135 (+35%), extension +25% → TRIM_PROFIT 1주 trim.
  - 9주 보유, avg_cost=$100 유지, `ProfitTargetsHit=[30.0]`.
- **Day 30 (SIDE)**: 리밸런싱에서 가중치 갭 확대 → BUY_MORE 5주 @ $120.
  - 새 평균단가 = (9×100 + 5×120) / 14 = **$107.14**, 총 14주.
  - `ProfitTargetsHit` 은 **그대로 `[30.0]`** (BUY_MORE 는 컬럼 미터치).
- **Day 35 (SIDE)**: 주가 $140. 새 평단 기준 PnL = (140/107.14 − 1)×100 = **+30.7%**.
  - 조건 체크:
    - [O] PnL ≥ 30
    - [O] 다른 조건들 모두 OK
    - [X] **30.0 tier 이미 기록됨** → skip
  - 트리거 침묵. 재추가 trim 없음.
- **Day 50 (SIDE 유지)**: 주가 $180, 새 평단 기준 PnL +68%.
  - 여전히 `[30.0]` tier 기록으로 인해 skip. 상승분 전체 추세 반영.

**요점**:

- **Tier memory 는 ticker 키 단위** 이지 매수 배치(purchase batch) 단위가 아님.
  따라서 같은 ticker 에 BUY_MORE 가 발생해도 tier memory 는 **결코 리셋되지 않음**.
- 저장 공간도 ticker 당 1개 set 만 존재. "이번 배치용 tier" / "저번 배치용 tier"
  구분은 존재하지 않음.
- 이 설계는 sim 과 live 모두 동일하며, sim 에서 검증된 backtest 결과가 live
  에 그대로 재현되는 근거.
- **Tier 가 리셋되는 유일한 경로**: `SELL` / `STOP_LOSS` / `SELL_GRACE` /
  `SELL_PROFIT` 중 하나로 **전량 매도** 되어 holdings 에서 row 가 제거된 후,
  새로운 BUY_NEW 로 재진입할 때 (= 새 row 가 생기며 `ProfitTargetsHit=""`).

### 시나리오 G — 파트너 리짐 alias 안정성

`config.yaml` 에 `regimes: [SIDE, DEF]` 라고 썼지만 내부적으로 리짐 라벨은
`BULL / SIDE / DEFENSIVE / CRASH / BEAR` 로 세분화되어 있다. `exits/base.py`
의 alias map 에 의해:

```
DEF        → DEF
DEFENSIVE  → DEF
CRASH      → DEF
BEAR       → DEF
```

즉 v2.1 의 `profit_target` 트리거는 BULL 외의 **모든 방어성 리짐** 에서 활성.
Live 에서 보이는 trigger verdict 의 `Regime` 컬럼은 raw label (예: `CRASH`)
로 찍히지만, 메트릭 집계는 `DEF` bucket 으로 묶인다. **이는 기능적 정합성이
보장된 alias 이지 버그가 아니다.**

---

## 6. Live 체결 플로우 (launcher T10)

```
[daily_runner]
    generate_recommendations()
      └─ profit_target trigger fires
      └─ recos 행에 ProfitTier=30.0 기록
    holdings_manager.save_recommendations()
      └─ Recommendations 시트에 ProfitTier 컬럼 포함 저장

[launcher UI]
    사용자가 TRIM_PROFIT 체크 → Apply
    executed_df 에 ProfitTier 전달
    holdings_manager.apply_partial_execution()
      └─ Current 시트 Shares 감소
      └─ ProfitTargetsHit 컬럼에 30.0 merge (JSON list)

[다음 거래일]
    holdings 프로퍼티가 ProfitTargetsHit 파싱 → set {30.0}
    HoldingSnapshot.profit_targets_hit = frozenset({30.0})
    profit_target trigger tier 체크 → 30 이미 기록됨 → skip
```

---

## 7. 모니터링 체크리스트 (운영 중 관찰할 것)

첫 30 거래일 동안:

- [ ] SIDE/DEF 리짐에서 profit_target verdict 가 실제로 fire 하는지
- [ ] 하루에 동일 포지션 2번 이상 fire 하는 경우가 없는지 (= tier memory 정상)
- [ ] `holdings_log.xlsx` → `Current` 시트 → `ProfitTargetsHit` 컬럼이
      `[30.0]` 형태로 채워지는지 육안 확인
- [ ] BULL 리짐 전환 시 이 트리거가 완전 침묵하는지
- [ ] 전체 recos 행 수 / BUY/SELL 빈도가 v2.0 대비 크게 달라지지 않았는지

이상 발견 시 config.yaml 의 `strategy.exit_triggers` 블록 **삭제** 만으로
v2.0 으로 즉시 롤백 가능.

---

## 8. 롤백 플랜

```bash
# 1. config.yaml 백업
cp phase3/config.yaml phase3/config.yaml.v2_1_bak

# 2. exit_triggers 블록 제거 (legacy stop_loss/sell_grace 키로 회귀)
# 3. daily_runner 재실행
```

`ProfitTargetsHit` 컬럼은 `Current` 시트에 남아있어도 v2.0 로직은 그 필드를
무시한다 (`build_holding_snapshots` 는 legacy path 에서 tier 를 읽지만, 트리
거 자체가 없으므로 의미 없음). 즉 데이터 손실 없음.
