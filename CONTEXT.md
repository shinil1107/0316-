# Quant Engine Project — Full Context Document

> 이 문서는 Codex 등 외부 AI 에이전트에게 프로젝트 맥락을 전달하기 위한 것입니다.
> 프로젝트 경로: `/Users/shin-il/PyCharmMiscProject/0316-`
> GitHub: `git@github.com:shinil1107/0316-.git`

---

## 1. 프로젝트 개요

S&P 500 종목 대상 **퀀트 트레이딩 시스템**. 3단계로 구성:

- **Phase 1**: 기술적 지표 + 펀더멘털 팩터 기반 종목 스코어링 엔진
- **Phase 2**: GA(Genetic Algorithm)로 팩터 가중치 최적화 → frozen signal 생성
- **Phase 3**: 라이브 트레이딩 시스템 (일일 추천, 포트폴리오 관리, 백테스트, 전략 실험)

데이터 소스: FMP(Financial Modeling Prep) API → 로컬 파일 캐시
캐시 경로: `/Users/shin-il/Documents/my stock/cache_fmp_c2_1/`

---

## 2. Phase 1/2 — 퀀트 엔진 (스코어링 & 시그널 최적화)

### 핵심 아키텍처

1. **노트북 Cell 0** (`0315 windows이사.ipynb`): 엔진 코어 — 모든 함수 정의
   - 유니버스 로딩 (S&P 500)
   - OHLCV 다운로드/캐시
   - 기술적 지표 패널 (RSI, MACD, BB, ATR 등 30+개)
   - 소프트 피처 빌더
   - 펀더멘털 팩터 시계열
   - 종목별 패널 빌드 → `prepare_inputs()` → pack dict
   - 스코어링: `_score_vector_for_regime()` — regime별 가중치 벡터로 종목 점수 산출
   - VIX 기반 regime 판별 (BULL/SIDE/DEFENSIVE/CRASH)

2. **GA 최적화**: 팩터 가중치 3벌 (bull/side/defensive) × N개 팩터를 유전 알고리즘으로 탐색
   - 결과물: `frozen_signal_P2_BATCH11_*.npz` — 최적화된 가중치 + 마스크 + 메타데이터

3. **engine/ 모듈**: 노트북 함수를 패키지로 재구성
   - `data_pipeline.py`: `prepare_inputs()` — pack 생성
   - `signal_lab.py`: 시그널 후보 평가/랭킹
   - `search_pipeline.py`: GA 메타서치
   - `report_pipeline.py`: 결과 리포트 생성

### 핵심 데이터 구조

```python
pack = {
    "tickers": ["AAPL", "MSFT", ...],   # N개 종목
    "dates": ["2017-01-03", ...],         # T개 거래일
    "close": np.array(shape=(T, N)),      # 종가 행렬
    "raw_close": np.array(shape=(T, N)),  # 원본 종가
    # + 각종 지표 패널들
}

signal = {
    "wb": np.array(...),  # bull 가중치
    "ws": np.array(...),  # side 가중치
    "wd": np.array(...),  # defensive 가중치
    "mask": np.array(...),  # 팩터 선택 마스크
}
```

### VIX Regime 분류

| VIX 수준 | Regime | 설명 |
|----------|--------|------|
| < 18 | BULL | 강세장 — 공격적 투자 |
| 18~30 | SIDE | 횡보장 — 중립 |
| 30~35 | DEFENSIVE | 방어적 — 보수적 투자 |
| ≥ 35 | CRASH | 폭락장 — 현금 비중 극대화 |

---

## 3. Phase 3 — 라이브 트레이딩 시스템

### 아키텍처 흐름

```
매일 실행 (T7 Live Run)
  ├─ 캐시 갱신 (FMP API → 로컬 파일)
  ├─ VIX 조회 → regime 판별
  ├─ 트리거 체크 (쿨다운/VIX긴급/드리프트)
  ├─ 스코어링 (frozen_signal + 오늘 데이터)
  ├─ regime별 전략 resolve (regime_overrides)
  ├─ 추천 생성 (generate_recommendations)
  │   ├─ STOP_LOSS: PnL < -15% → 즉시 매도
  │   ├─ SELL: grace 기간 초과 → 매도
  │   ├─ SELL_GRACE: top-N 탈락 → 유예 카운트
  │   ├─ TRIM: 과비중 → 부분 매도
  │   ├─ BUY_NEW / BUY_MORE: gap 비례 매수
  │   └─ HOLD / DEFERRED
  ├─ 이메일 발송 (추천표 포함)
  └─ 포트폴리오 로그 기록
```

### 현재 확정 전략 프로필 (F5_side_g120)

**백테스트 성과**: CAGR 34.09%, Sharpe 1.244, MDD 35.05%, Calmar 0.973

| Regime | Grace Days | Deploy Rate | Stop-Loss | Invest% |
|--------|-----------|-------------|-----------|---------|
| **BULL** | 60 | 20% | ON | 98% |
| **SIDE** | 120 | 10% | OFF | 97% |
| **DEF** | 60 | 10% | OFF | 97% |

#### 전략 파라미터 설명

- **sell_grace_days**: top-N에서 탈락한 종목을 즉시 매도하지 않고 N일간 유예. 불필요한 턴오버 방지
- **adaptive_deploy_rate**: 미투자 현금 중 매일 투입하는 비율. `budget = (현금 - 목표현금) × deploy_rate`
- **enable_stop_loss**: PnL이 -15% 이하면 강제 매도. DEF에서는 OFF (반등 수익 보호)
- **target_invest_pct**: 목표 투자 비중. 나머지는 현금 보유

#### Regime별 전략 분기 (resolve_strategy)

`config.yaml`의 `strategy.regime_overrides`에 정의. 시뮬레이터/라이브 모두에서:
1. 매일 현재 regime 확인
2. base strategy + regime override를 merge → effective strategy 생성
3. effective strategy로 추천 생성

### 매매 로직 상세

**매수 (Gap-Proportional Buy)**:
1. 종목별 target weight 계산 (score 비례, max_weight_cap 적용)
2. 현재 weight와의 gap 계산
3. gap > threshold인 종목들에 대해 gap 비례로 daily budget 배분
4. 주 단위 절사 (fractional share 미지원)

**매도 (Grace Period)**:
1. 보유 종목이 top-N에서 탈락 → SELL_GRACE 시작
2. 매일 GraceCount + 1
3. GraceCount > sell_grace_days → SELL 실행
4. 유예 중 다시 top-N 복귀 → 카운트 리셋

**Adaptive Daily Buy Limit**:
```
total = holdings_value + cash
target_cash = total × (1 - target_invest_pct)
uninvested = max(cash - target_cash, 0)
daily_limit = max(uninvested × deploy_rate, min_limit)
```

---

## 4. 백테스트 & 전략 실험 프레임워크

### simulator.py

- `SimPortfolio`: 인메모리 포트폴리오 클래스 (HoldingsManager의 시뮬레이션 버전)
- `run_simulation()`: 2017~현재 일일 시뮬레이션 루프
  - 매일: 가격 업데이트 → regime 판별 → 스코어링 → resolve_strategy → 추천 → 매매 적용
- `compute_metrics()`: CAGR, Sharpe, MDD, Calmar + regime별 분리 지표 (AnnRet, Sharpe, MDD, Calmar, WinRate, MaxStreak)

### phase3_lab.py

- 여러 전략 변형(arm)을 하나의 data pack으로 비교 실험
- `BASELINE_STRATEGY` → `make_strategy(overrides)` → `run_simulation()`
- Sweep 세대별 진화:
  - **V1 (SWEEP_ARMS)**: grace period 탐색 (10~30일), deploy/SL 조합 15개
  - **V2 (SWEEP_V2_ARMS)**: grace 포화점 (45/60/90) + 첫 regime 분기 시도
  - **V3 (SWEEP_V3_ARMS)**: grace=60 고정 + deploy/SL만 regime별 조절 10개
  - **V4 (SWEEP_V4_ARMS)**: 최적 조합 확인 — F5_side_g120이 최종 우승

### 실험에서 발견된 핵심 인사이트

1. **Grace period는 모든 regime에서 길수록 좋다** (짧으면 SIDE에서 -5% 손실)
2. **SIDE regime이 가장 취약** — 횡보장에서 "가만히 있는 것"이 최선
3. **DEF에서 stop-loss OFF가 확정적 우위** (+23%p DEF_Ann%)
4. **Deploy rate는 BULL에서만 유의미** (15→20% = +1.3%p)
5. **target_invest_pct, gap_threshold 등은 2차 변수** (영향 미미)

---

## 5. 파일 구조 및 역할

### Phase 1/2 (engine/)

| 파일 | 역할 |
|------|------|
| `0315 windows이사.ipynb` | 엔진 코어 (Cell 0) + 실험 노트북 |
| `engine/__init__.py` | 패키지 진입점 |
| `engine/data_pipeline.py` | 데이터 로딩, pack 생성 |
| `engine/signal_lab.py` | 시그널 평가/랭킹 |
| `engine/search_pipeline.py` | GA 메타서치 |
| `engine/report_pipeline.py` | 백테스트 리포트 |
| `engine/run_engine.py` | 오케스트레이션 |
| `engine/data_trust_layer.py` | 데이터 신뢰도 검증 |
| `engine/cache_fallback.py` | 캐시 복구 |
| `engine/runtime_context.py` | 런타임 설정 |

### Phase 3 (phase3/)

| 파일 | 역할 |
|------|------|
| `phase3/config.yaml` | 전략/포트폴리오/이메일 설정 (regime_overrides 포함) |
| `phase3/config.local.yaml` | (git 미추적) Gmail 앱 비밀번호 |
| `phase3/daily_runner.py` | 일일 실행 핵심 로직: 스코어링→추천→저장 |
| `phase3/simulator.py` | 백테스트 시뮬레이터 + regime-aware strategy resolution |
| `phase3/phase3_lab.py` | 전략 실험 프레임워크 (multi-arm sweep) |
| `phase3/launcher.py` | Tkinter GUI (T1~T17 버튼) |
| `phase3/holdings_manager.py` | 포트폴리오 상태 관리 (Excel 기반) |
| `phase3/mailer.py` | Gmail 알림 (추천표/포트폴리오 현황) |
| `phase3/cache_health.py` | 캐시 건강 체크 |
| `phase3/engine_loader.py` | 노트북 Cell 0 동적 로딩 |
| `phase3/dashboard.py` | Streamlit 대시보드 (실험적) |
| `phase3/run_phase3.command` | macOS 실행 스크립트 |

### 유틸리티

| 파일 | 역할 |
|------|------|
| `fmp_cache_updater.py` | FMP 캐시 일괄 업데이트 CLI |
| `fmp_daily_updater.ipynb` | 캐시 업데이터 노트북 |
| `archive/` | 과거 실험 설정/레거시 코드 보관 |

---

## 6. 핵심 함수 시그니처

### daily_runner.py

```python
def check_triggers(cfg, vix_close, regime, holdings_mgr, pack, signal, force=False) -> List[str]
def generate_recommendations(cfg, scores_df, regime, vix_close, holdings_mgr,
                             total_capital, daily_buy_limit=0.0, strategy_conf=None) -> pd.DataFrame
def run_daily(dry_run=False, force=False, config_path=None)
```

### simulator.py

```python
def resolve_strategy(base_conf: dict, regime: str) -> dict
def run_simulation(engine, cfg, pack, signal, vix_close_by_date, vix_regime_by_date,
                   initial_capital=100000.0, daily_buy_limit=1000.0,
                   strategy_conf=None, trigger_conf=None, rebalance_mode="event_driven",
                   commission_bps=10.0, slippage_bps=5.0,
                   start_date=None, end_date=None, progress_fn=None) -> Dict
def compute_metrics(daily_ts: pd.DataFrame, initial_capital: float, total_commission: float = 0.0) -> dict
```

### phase3_lab.py

```python
BASELINE_STRATEGY = { ... }  # F5_side_g120 기반
def make_strategy(overrides: dict, base: dict = None) -> dict
def run_lab(arms, start_date, end_date, initial_capital, daily_buy_limit,
            rebalance_mode="daily", ...) -> {"results", "comparison", "regime_comparison"}
```

---

## 7. 현재 상태 및 남은 과제

### 완료된 것
- Phase 1/2 엔진 + GA 최적화 (BATCH11 frozen signal)
- Phase 3 라이브 시스템 전체 구현
- V1~V4 전략 sweep → F5_side_g120 확정
- Regime-adaptive 아키텍처 (resolve_strategy)
- 매일 스코어링 + 추천표 이메일 (trigger 없어도 Preview 발송)
- GitHub 배포 완료

### 운영 중
- Phase 3 실전 테스트 단계 (3개월 모의투자 후 Phase 4 본격 자산 투입 예정)
- 소규모 실자본으로 운용 중 (현재 보유: TPL, CL)
- 라이브 리밸런스 모드: **daily** (백테스트와 동일, event-driven/cooldown 미사용)
- Regime: SIDE (VIX=19.2)

### 잠재적 개선 영역
- SIDE regime 성과 추가 개선 (현재 SIDE_Ann=21.4% — 가장 취약)
- 더 긴 백테스트 기간 (2010~) 또는 out-of-sample 검증
- 실시간 가격 피드 연동 (현재 EOD 기반)
- 트랜잭션 비용 모델 고도화 (현재 flat bps)
- Phase 1/2 signal 품질 향상 연구 (현재 BATCH11 → 상위 signal 탐색)
