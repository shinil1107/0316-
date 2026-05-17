# Baseline_V2 돌파 전략 분석

**Date**: 2026-05-07  
**Status**: 전략 구상 완료 — 실행 대기

---

## 1. V2_GOLDEN의 해부 결과

### 1.1 구성 레시피

V2_GOLDEN_ENS_L3_v1은 **3개의 이질적 signal을 regime-dependent beta로 블랜딩**한 것:

| Member | Type | k | 역할 | 핵심 특징 |
|--------|------|---|------|-----------|
| **P2_BATCH11** | Phase 1 IC-GA | 12 | Anchor (50-95%) | wb=[14,15,31,33], ws=[1,2,4,6,25] |
| **BULL_GA_V2** | Bull-specialist | 6 | BULL boost (0-2.5%) | wb only [14,16,25] |
| **E2E** | End-to-End GA (simulator) | 15 | Dense regularizer (2.5-15%) | **ALL 36 features** non-zero |

### 1.2 Regime-Dependent Betas

```
BULL: P2=0.95, BULL_GA=0.025, E2E=0.025   ← P2가 지배
SIDE: P2=0.70, BULL_GA=0.15,  E2E=0.15    ← E2E 비중 상승
DEF:  P2=0.85, BULL_GA=0.0,   E2E=0.15    ← BULL_GA 제외
```

### 1.3 V2 wb (BULL) 핵심 Driver

| Feature | wb weight | Cumul% | Batch 7/8 사용 여부 |
|---------|-----------|--------|---------------------|
| [14] pct_from_high_20d | 0.677 | 39.7% | **0/9 signals** ❌ |
| [15] pct_from_low_20d | 0.656 | 78.3% | 1/9 (P8_BULL_DENSE only) |
| [33] free_cf_yield | 0.277 | 94.5% | **0/9 signals** ❌ |

**V2 BULL의 78.3%를 차지하는 features [14,15]를 현대 GA가 완전히 무시하고 있다.**

### 1.4 현대 GA가 수렴하는 features

```
항상 선택: [2] SMA10_SMA50_ratio, [25] eps_growth_q, [26] revenue_growth_q (9/9)
자주 선택: [6] ATR14_pctClose, [11] close_SMA200_ratio, [16] close_VWMA20_ratio (8/9)
가끔 선택: [21] rank_volatility_10d (7/9), [19] rank_volume_10d (4/9)
```

---

## 2. 구조적 문제 진단

### 2.1 Feature Gap (핵심 원인)

V2의 BULL 성능을 만드는 feature [14] `pct_from_high_20d`가 Batch 6/7/8에서 **단 한 번도 선택되지 않음**.

**왜?**
- 현대 GA의 fitness 함수가 IC+Spread+Turnover 복합 최적화를 사용
- `conc_penalty` + `entropy_bonus`가 "안전한" 고빈도 features로 수렴 유도
- `pct_from_high_20d`는 단독 IC가 낮을 수 있으나 **포트폴리오 시뮬레이션에서 강력** (E2E가 발견)
- 원래 P2_BATCH11은 **다른 시대**(2026년 이전)에 **다른 방식**(단순 IC-scoring)으로 학습

### 2.2 E2E Signal의 역할 (밀도 공급원)

E2E signal은 36개 전체 feature에 의미 있는 weight가 있는 **유일한 dense signal**:
- 직접 portfolio CAGR을 최적화하는 End-to-End GA로 생성
- E2E_Val_CAGR = 32.4%, Sharpe = 1.19 (자체로도 우수)
- V2에서 2.5-15% weight로 참여하면서 **모든 feature를 활성화하는 regularizer 역할**

### 2.3 p2_ensemble_composer의 한계

현재 composer는 `weighted_avg(signal1.wb, signal2.wb, signal3.wb)`:
- 입력 signals가 모두 sparse → 출력도 sparse
- Feature gap을 메울 수 없음 (0 × weight = 0)
- V2처럼 "dense regularizer"를 섞는 메커니즘이 없음

---

## 3. 돌파 전략

### Strategy A: "V2 재현 + 업그레이드" (E2E GA v3 실행)

**개념**: V2를 만든 것과 동일한 방식으로, **새로운 E2E GA를 실행**하여 dense signal을 만든 뒤, Batch 8 materials + E2E_v3를 V2-style로 블랜딩

**구체적 계획**:
1. `e2e_ga.py`를 expanded data (15Y)로 재실행 → `E2E_v3` dense signal 생성
2. P2_BATCH11 대신 Batch 8 최강 signal을 anchor로 사용
3. V2-style regime-dependent beta로 블랜딩
4. Feature [14,15,33] 재활성화 가능 (E2E가 발견할 확률 높음)

**장점**: V2 성공 메커니즘 그대로 재현 + 현대 데이터 반영
**단점**: E2E GA budget 필요 (P=30×G=32, ~8시간)
**기대 효과**: ★★★★☆

### Strategy B: "Feature-Forced GA" (강제 feature 주입)

**개념**: GA에 "반드시 포함해야 할 feature set"을 지정하여 feature gap을 강제 해소

**구체적 계획**:
1. GA 초기 population에 feature [14,15,33]을 **강제 포함** (elite lock)
2. Mutation에서 해당 features는 삭제 불가 (보호)
3. 나머지 features는 자유 탐색
4. 이로써 "V2-style momentum features" + "modern GA의 fundamental features" 결합

**장점**: 직접적으로 feature gap 해결, 빠른 실행
**단점**: GA 자유도 제한 → 과적합 위험
**기대 효과**: ★★★☆☆

### Strategy C: "Hybrid Injection" (V2 anchor + Modern L3)

**개념**: V2의 wb를 유지하면서 ws/wd만 Batch 8 L3 blend로 교체

**구체적 계획**:
1. wb ← **Baseline_V2 그대로** (proven momentum, [14,15,33])
2. ws ← L3 blend of Batch 8 SIDE specialists
3. wd ← L3 blend of Batch 8 balanced/defensive signals
4. Mask = V2 mask ∪ Batch 8 features (최대 밀도 확보)

**장점**: V2 BULL 성능 100% 보존 + SIDE/DEF 현대화
**단점**: BULL 성능 돌파 불가 (V2와 동일)
**기대 효과**: ★★☆☆☆ (V2 대비 marginal, SIDE/DEF에서만 차이)

### Strategy D: "Cross-Era L3" (P2_BATCH11 + E2E + Batch 8)

**개념**: V2를 만든 원조 멤버들(P2_BATCH11, E2E)에 Batch 8을 추가하여 4-member L3 블랜딩

**구체적 계획**:
1. wb = 0.60*P2_BATCH11 + 0.10*E2E + 0.15*P8_BULL_DENSE + 0.15*P8_BALANCED
2. ws = 0.40*P2_BATCH11 + 0.15*E2E + 0.25*P8_SIDE_V3 + 0.20*P8_BALANCED
3. wd = 0.50*P2_BATCH11 + 0.20*E2E + 0.15*P8_SIDE_V3 + 0.15*P8_BALANCED
4. L2 normalize per-slot

**장점**: V2의 feature coverage 계승 + Batch 8의 현대적 pattern 추가
**단점**: V2 멤버들의 오래된 데이터 학습 → 미래 적합성 불확실
**기대 효과**: ★★★☆☆

### Strategy E: "E2E GA + Feature Lock" (ML 보류 대안)

**개념**: E2E GA를 재실행하되, 초기 seed를 V2 style로 설정 + 핵심 features를 보호

**구체적 계획**:
1. `e2e_ga.py`에 `forced_features=[14,15,33,25,26]` 옵션 추가
2. 초기 population의 50%를 V2-like structure로 seeding
3. 나머지 50%는 random (diversity)
4. Walk-forward windows: 2011-2026 (full 15Y)
5. Budget: P=40, G=40 (~12시간)

**장점**: Feature gap 해소 + E2E의 dense output + 강제 diversity
**단점**: 구현 필요 + 12시간 compute
**기대 효과**: ★★★★★ (최고 기대치)

---

## 4. 추천 실행 순서

### Phase 1: 즉시 실행 가능 (0-1시간)

**Strategy D (Cross-Era L3)**: 이미 있는 signals로 조합만 하면 되므로 즉시 테스트 가능.
V2의 원조 멤버 + Batch 8 blend가 V2를 넘을 수 있는지 확인.

### Phase 2: 단기 실행 (8-12시간)

**Strategy A or E (E2E GA 재실행)**: 새로운 dense signal 생성 필요.
- E2E GA를 expanded 15Y data로 재실행
- Feature [14,15,33] 보호 옵션 추가 (Strategy E)
- 생성된 E2E_v3로 cross-era 블랜딩

### Phase 3 (보류): ML 활용

**User가 언급한 ML 접근**:
- Gradient boosting으로 feature importance → 최적 feature subset 발견
- Neural-net weight initialization → dense signal 생성
- Bayesian optimization으로 beta 튜닝
- 현재는 보류하되, E2E GA가 실패하면 활성화

---

## 5. 핵심 인사이트 정리

1. **V2의 해자 = "E2E dense regularizer" + "P2_BATCH11의 momentum features"**
2. **현대 GA가 feature [14] 를 선택하지 않는 이유**: IC-기반 fitness에서 단독 predictive power가 낮지만, 포트폴리오 시뮬레이션에서는 강력 (CAGR optimization vs IC optimization의 차이)
3. **돌파 핵심**: "dense signal 재생산" + "momentum features 재활성화"
4. **가장 유망한 경로**: E2E GA 재실행 (Strategy A/E)

---

**End of Strategy Analysis**
Generated: 2026-05-07 04:10 AM (UTC+9)
