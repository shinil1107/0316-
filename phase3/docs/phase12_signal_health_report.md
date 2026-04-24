# Phase 1/2 Signal Health Report — V1 vs V2 Deployment Diagnostic

**작성일**: 2026-03-28
**대상 Signal**:
- V1: `frozen_signal_P2_BATCH11_20260406_043415.npz` (이전 golden, 현재 rollback 경로)
- V2: `frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz` (현재 live)
**데이터 범위**: 2015-12-23 ~ 2026-04-17 (pack `precompute_qresearch_v4_12`)
**평가 cadence**: Weekly (EVAL_STEP=5 trading days), 468 rebalance dates
**Portfolio 가정**: Top-30 long-only, 평균 cost 15 bps/trade

---

## 0. Executive Summary

| 항목 | 진단 | 심각도 |
|---|---|---|
| **P5** — Signal edge 의 시간적 drift (overfit to older regime) | Q1(2016-18) 강세 → Q3-Q4(2020-24) 반전 → Q5(2024-26) edge ≈ 0 | 🔴 HIGH |
| **P1** — Weekly rebalance 시 turnover 및 cost drag | SIDE/DEF 가중치 기준 연 cost 약 5% | 🔴 HIGH |
| **P4** — 일일 rank churn | SIDE 0.30, DEF 0.39 (일별 상당 polling churn) | 🟠 MEDIUM |
| **P3** — Top-30 의 유동성 제약 | Median $ADV ~$250M, $5M 미만 비중 0% | 🟢 LOW |
| **V1 vs V2 비교** | 앙상블 overlay 가 deployment 측면 거의 개선 없음 (SIDE 에서는 소폭 악화) | 🟠 MEDIUM |

**종합**: 현재 live signal 은 "사용 불가" 수준은 아니나 **edge 가 얇아지고 있어 재훈련이 필요한 상태**. Phase 3 exit 방어막(stop-loss, grace, `SIDE_DEF_p12`)이 signal 약점을 일부 상쇄하여 CAGR 양수를 유지하고 있으나, **P5 drift 가 계속되면 향후 1-2년 내 net alpha 가 소멸할 위험**이 있음.

---

## 1. 측정 방법론

### 1.1 Signal 점수 재구성

각 날짜 `di` 와 종목 `n` 에 대해:
```
score[di, n] = Σ_k  active_w[k] × feat[k, di, n] × feat_valid[k, di, n]
```
- `active_w[k] = weight_vector[k] × mask[k]` (선택되지 않은 indicator 는 0)
- Coverage threshold: 선택된 indicator 중 valid 비율이 50% 미만이면 score=NaN
- Regime 별 가중치 3종 (BULL/SIDE/DEF) 를 **각각 독립 적용**하여 비교

### 1.2 Engine 재구현 신뢰성 검증

`engine._score_vector_for_regime` (notebook Cell 0 의 실제 live 채점 함수) 대비 6개 sample date 에서 측정:

| Regime | Top-30 겹침 | Spearman ρ |
|---|---|---|
| SIDE | 25-26 / 30 | 0.98-0.99 |
| DEF  | 28-30 / 30 | 0.998-0.999 |
| BULL | 12-23 / 30 | 0.92-0.97 |

→ Rank-based 측정(IC, turnover, churn)에서 **실제 engine 과 사실상 동일**. BULL 의 낮은 겹침은 k_active 가 작아 tie-break 영향이 크기 때문이지 rank 상관 자체는 여전히 높음.

### 1.3 지표 정의

| 지표 | 수식 | 의미 |
|---|---|---|
| **MeanIC** | daily Spearman(score, fwd1) 평균 | Cross-sectional 예측력 |
| **Spread10%** | mean(top-10% fwd1) − mean(bot-10% fwd1) | Long-short decile 차이 |
| **IR** | MeanIC / std(IC) | Info ratio |
| **AnnTO** | 52 × mean(1 − \|top30_t ∩ top30_{t-1}\|/30) | 연환산 one-way 회전율 |
| **Cost Drag** | AnnTO × COST_BPS | 회전율 × 비용 계산 연 %손실 |
| **Rank Churn** | 1 − Spearman(rank_t, rank_{t-1}) 평균 | Rank 안정성 (0=no churn) |

---

## 2. 전체 기간 성과 (Full-period)

| Signal / Regime | MeanIC | Spread% | IR | PosIC% | AnnTO% | Cost%/yr | Rank Churn |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1_BATCH11 / BULL | -0.0035 | -0.16 | -0.018 | 52.3 | 1129 | 1.69 | 0.065 |
| V1_BATCH11 / SIDE | +0.0051 | -0.13 | +0.034 | 53.3 | 3541 | 5.31 | 0.258 |
| V1_BATCH11 / DEF  | +0.0083 | +0.36 | +0.066 | 53.3 | 3364 | 5.05 | 0.404 |
| V2_ENS_L3 / BULL  | -0.0035 | -0.15 | -0.018 | 52.3 | 1126 | 1.69 | 0.064 |
| V2_ENS_L3 / SIDE  | +0.0034 | -0.16 | +0.023 | 52.0 | 3731 | 5.60 | 0.299 |
| V2_ENS_L3 / DEF   | +0.0088 | +0.40 | +0.070 | 52.7 | 3325 | 4.99 | 0.390 |

### 관찰
- **BULL**: 양 signal 모두 MeanIC 음수 (IR −0.018) 로 **사실상 예측력 없음**. k_active 가 작아(V1:12, V2:15 → BULL anchor 95% 비중) weight vector 가 희박.
- **SIDE**: 두 signal 모두 MeanIC 양수이나 Spread 음수. GA 가 bottom 10% 도 top 10% 만큼 좋게 평가하는 "weak discriminator" 상태.
- **DEF**: Spread +0.36~0.40% 로 IC 와 방향 일치. 3개 regime 중 가장 본 기능을 수행.
- **연 Cost drag 5%+** (SIDE/DEF): annual alpha 가 +5-10% 수준이라면 비용만으로 절반이 사라짐.

---

## 3. P5 Time-Drift (Primary Finding)

### 3.1 Quintile MeanIC (Q1 = 2016-12~2018-10 오래된 순, Q5 = 2024-05~2026-03 최근)

| Signal / Regime | Q1 | Q2 | Q3 | Q4 | **Q5** | 1st 평균 | 2nd 평균 | IC drop% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V1/BULL | +0.0214 | **−0.0370** | -0.0038 | -0.0073 | +0.0092 | +0.0011 | -0.0081 | +815% |
| V1/SIDE | **+0.0260** | +0.0071 | -0.0066 | -0.0023 | +0.0014 | +0.0121 | -0.0018 | +115% |
| V1/DEF  | +0.0197 | **+0.0487** | -0.0071 | +0.0114 | **−0.0311** | +0.0247 | -0.0080 | +132% |
| V2/BULL | +0.0211 | **−0.0369** | -0.0046 | -0.0070 | +0.0100 | +0.0007 | -0.0076 | +1242% |
| V2/SIDE | +0.0234 | +0.0005 | -0.0041 | -0.0052 | +0.0024 | +0.0095 | -0.0027 | +128% |
| V2/DEF  | +0.0220 | **+0.0488** | -0.0077 | +0.0104 | **−0.0295** | +0.0262 | -0.0085 | +132% |

### 3.2 Quintile Spread10% (%)

| Signal / Regime | Q1 | Q2 | Q3 | Q4 | **Q5** | SP drop% |
|---|---:|---:|---:|---:|---:|---:|
| V1/SIDE | +0.55 | +0.21 | **-0.67** | -0.23 | **-0.51** | +485% |
| V1/DEF  | +0.35 | **+1.45** | -0.42 | +0.47 | -0.04 | +80% |
| V2/SIDE | +0.52 | +0.15 | **-0.62** | -0.40 | **-0.47** | +519% |
| V2/DEF  | +0.31 | **+1.52** | -0.41 | +0.46 | +0.12 | +71% |

### 3.3 해석

**Q2 (2018-10 ~ 2020-08) 황금기 현상**: DEF 가중치가 이 구간에서 IC +0.049, Spread +1.5% 기록. 이 기간은 **2020-02-03 COVID 크래시 + V-shaped 회복**을 포함. Defensive factor exposure 가 이 단일 위기에서 폭발적 성과 → full-history IR 을 혼자 끌어올림. **Single-event overfit** 가능성 높음.

**Q3 이후 edge 반전**: 2020-09 이후 SIDE/DEF 모두 Spread 음수 구간이 다수. Q5 (최근 2년) 에서 DEF MeanIC **-0.0295** 은 **신호가 완전히 역방향 작동**하고 있다는 의미. 이게 **in-sample 기간** 이라는 점에 주목 — 실제 OOS 는 더 나쁠 가능성.

**Rolling 1-year window**: 50-rebal(≈1년) rolling MeanIC 기준, V2/SIDE 는 **55.3%의 1년 구간이 음수 MeanIC**. V1 도 유사. 즉 "어느 시점에서 시작하든 1년 보유 후 edge 확률 절반 이하" 상태.

---

## 4. V1 vs V2 비교 (Delta Analysis)

| Regime | ΔMeanIC | ΔSpread% | ΔQ5_IC | ΔAnnTO% | ΔCost%/yr | ΔChurn |
|---|---:|---:|---:|---:|---:|---:|
| BULL | ~0 | +0.01 | +0.0008 | -2 | 0 | -0.001 |
| **SIDE** | **-0.0017** | **-0.03** | +0.0009 | **+191** | **+0.29** | **+0.041** |
| DEF  | +0.0005 | +0.04 | +0.0016 | -39 | -0.06 | -0.013 |

### 주요 관찰
1. **BULL 영역**: V2 앙상블의 β_BULL = [0.95, 0.025, 0.025] 이므로 V2 BULL ≈ V1 BULL. 수치 차이가 noise 수준.
2. **DEF 영역**: V2 가 미세 개선 (ΔCost -0.06%/yr, ΔMeanIC +0.0005). E2E specialist 멤버의 defensive factor 가 약간 기여.
3. **SIDE 영역**: V2 가 **오히려 악화**.
   - ΔMeanIC -0.0017 (edge 감소)
   - ΔAnnTO **+191%** (churn 증가)
   - ΔCost **+0.29%/yr** (비용 증가)
   - ΔChurn +0.041

### 함의
- **V2 앙상블 전환은 SIDE 에서 net-negative**. BULL_GA_V2 + E2E specialist 가 SIDE regime 에서는 noise 주입에 가까움.
- P5 drift pattern 이 V1/V2 **완전히 동일** → 앙상블은 P5 를 못 고침. 문제는 **anchor(BATCH11) 자체의 GA 학습 방식**.
- 현 시점에서 V1 롤백의 deployment 측면 손실은 거의 없고 오히려 SIDE 에서는 소폭 이득.

---

## 5. P3 Capacity

| Signal | Median $ADV | 10th %ile $ADV | ADV < $5M 비중 |
|---|---:|---:|---:|
| V1_BATCH11 | ~$248M | ~$92M | 0.0% |
| V2_ENS_L3 | ~$256M | ~$96M | 0.0% |

→ **유동성 제약 거의 없음**. S&P 500 universe 를 사용하고 있어 저유동성 종목 편입 위험은 현실적으로 0. T1 scope 에서 P3 제외 가능.

---

## 6. 최근 live run 의 sector concentration 증거 (2026-03-28)

오늘자 BUY 추천 15종목 중:
- **유틸리티 11종** (EIX, CNP, SO, CMS, FE, DTE, WEC, SRE, EVRG, ED, PCG) = 73%
- **필수소비재 3종** (HSY, CL, KO) = 20%
- **헬스케어 1종** (GILD) = 7%

→ **Defensive basket 100%**. Score 분산도 60.7-69.3 으로 좁게 뭉침 (8.6 point). Signal 이 "이 그룹 내에서 구분" 하는 discriminative power 가 약함을 시사.

섹터 집중이 이렇게 심한 건 두 가지 의미:
- (a) Signal 이 defensive factor 를 강하게 학습 (Covid Q2 편향 잔존 가능성)
- (b) 단일 macro risk (금리, 규제) 에 portfolio 취약

**T1 fitness 에 sector-concentration penalty 포함 필요성의 실시간 증거**.

---

## 7. 원인 분석 — 왜 지금에서야 발견됐나

1. **기존 평가지표가 time-drift 를 희석**
   CAGR / Sharpe / MDD 는 전체 기간 평균. 앞 구간 강세 + 뒤 구간 약세가 상쇄되어 전체 수치는 양호해 보임. Rolling 3-year Sharpe 같은 drift chart 가 기본 리포트에 없었음.

2. **GA fitness 에 temporal validation 부재**
   `evaluate_individual_qresearch` 의 base fitness:
   ```
   fit = w_ic1 × MeanIC_1M + w_ic3 × MeanIC_3M + w_spread × Spread − penalties
   ```
   시간 분할 없음, walk-forward 없음. GA 는 Q1-Q5 고른 signal 과 Q1-Q2 만 강한 signal 을 **동일하게 평가** → 과거 regime overfit 을 걸러내지 못함.

3. **In-sample only 평가**
   Pack 범위 2015-12 ~ 2026-04 와 signal 생성일 2026-04-19 가 중복. 진정한 OOS test 가 없었음. Q5 (최근 2년) 가 edge 0 인데 이게 **in-sample** 이라는 점이 특히 심각.

4. **Phase 3 몰두로 인한 signal 층 blackbox 화**
   최근 수 주간 D2/D4/D5 exit 설계, `SIDE_DEF_p12` baseline 수립에 집중. Signal 층은 "ENS_L3 로 완성" 가정. 건강 체크 없었음.

5. **D4 sweep 이 간접 힌트를 제공했음**
   D4 sweep 에서 CAGR 개선이 **exit 튜닝에서만 발생** (signal 고정). 이는 "signal 이 plateau, exit management 가 유일한 개선 여지" 라는 신호였으나 명시적으로 해석하지 못함.

6. **초기 성공 수치가 방심을 유도**
   GA report 의 Invest_MeanIC 0.022, Spread 1.6%, PosICRatio 60% 가 매력적인 수치여서 신호 내부 시간 분해를 안 함. 막상 해보니 Q1 0.04 vs Q5 0.001 의 극심한 불균형.

---

## 8. 함의 및 다음 단계

### 8.1 즉시 조치 (단기)
- **Live 운영 계속**: 긴급 중단 필요 없음. Phase 3 방어막이 작동 중.
- **Sector concentration 수동 관리**: 일 단위 추천에서 한 섹터 60% 이상이면 분산 조치 고려.
- **Rolling Sharpe chart 추가**: Phase 3 리포트에 최근 1년 / 3년 rolling Sharpe 표시를 기본 채널로 추가 (이건 tactical, 본 연구와 별도 bite-size 작업).

### 8.2 구조적 개선 (중기, 본 보고서의 핵심 권고)

**T1 (Deployment-Aware Objective)**:
- GA fitness 에 turnover / cost-adjusted spread / sector-concentration penalty 추가
- Proxy-based, 빠른 구현 가능 (~1주)

**T5 (Walk-Forward Validation)**:
- GA fitness 에 **temporal fold validation** 강제
- `fit = mean(IC across folds) − λ × std(IC across folds)` 형태
- P5 의 근본 처방. 구현 더 무거움 (~2주)

**T1 과 T5 는 엄밀히 분리 불가능**: turnover penalty 만 추가해도 P5 는 안 풀림. Walk-forward 가 필요. 반대로 walk-forward 만 있어도 cost drag 문제는 안 풀림. **병행 설계 권고**.

### 8.3 중장기 재훈련 결정

- **T1 + T5 infrastructure 완비 후** 새 signal 재훈련
- 재훈련 시 V1(단일) 및 V2(앙상블) 두 architecture 모두 평가하여 ensemble 의 실제 효용 재검증
- 신 signal 의 Q5(OOS-proxy) IC / Spread 가 Q1-Q4 대비 유지되는지가 PASS 기준

### 8.4 중단 기준

아래 중 하나라도 발생하면 signal 층 긴급 재훈련 착수:
- Live 30-day rolling Sharpe < 0 이 60일 이상 지속
- Live 90-day MDD > -20%
- Phase 3 live vs backtest CAGR deviation > 30% (negative) over 6 months

---

## 9. 부록: 측정 스크립트 재현

현재 이 보고서는 **일회성 진단 스크립트** (`_t10_diagnostic.py`, `_t10_sanity.py`, `_t10_v1v2.py` — 작업 후 삭제됨) 의 결과물.
재실행 필요 시 `engine/data_pipeline.py` 의 pack cache 와 frozen signal NPZ 를 직접 로드하는 경량 스크립트로 10분 내 재작성 가능.

핵심 상수:
- PACK_PATH: `precompute_qresearch_v4_12_2015-12-23_2026-04-17.npz`
- TOP_N = 30, EVAL_STEP = 5 (weekly), COST_BPS = 15
- WARMUP = 252 trading days (1 year indicator warmup)

---

## 변경 이력

| 일자 | 변경 내용 |
|---|---|
| 2026-03-28 | 초안 작성. V1/V2 비교 진단 결과, P5 finding, T1/T5 권고 정리 |
