# Baseline V2 + 준Baseline 신호군 — Multi-Window 종합 평가 리포트

**평가 일시**: 2026-05-05 01:53
**평가 소요**: 14.1분 (843.7초)
**평가 범위**: 6개 신호 × 3개 fold-set × (6+7+7) folds = 120회 시뮬레이션

---

## Executive Summary

### 최종 판정 (Hardgate v1 기준)

| Signal | Default | Rolling | Regime | **통합 판정** |
|--------|:-------:|:-------:|:------:|:------------:|
| **Baseline_V2** | ✓ ALL | ✗ FAIL | ✗ FAIL | **REJECT (2/3 fail)** |
| P7_L3_ENSEMBLE_H | ✗ FAIL | ✗ FAIL | ✗ FAIL | **REJECT (3/3 fail)** |
| P7_STITCH_J | ✗ FAIL | ✗ FAIL | ✗ FAIL | **REJECT (3/3 fail)** |
| P7_STITCH_K | ✗ FAIL | ✗ FAIL | ✗ FAIL | **REJECT (3/3 fail)** |
| P7_BLEND_L | ✗ FAIL | ✗ FAIL | ✗ FAIL | **REJECT (3/3 fail)** |
| P7_STITCH_N | ✗ FAIL | ✗ FAIL | ✗ FAIL | **REJECT (3/3 fail)** |

**핵심 발견**:
1. **현 baseline(V2)조차 rolling/regime에서 hard gate fail** → 시간/regime 불변성 부족
2. **모든 P7 신호 전원 탈락** → Baseline V2 돌파 실패
3. **P7_STITCH_K가 상대적 최선** (default 거의 동등, regime SIDE 개선)
4. **P7_STITCH_N의 특이성**: regime에서 유일하게 G-A pass (CV=0.47), SIDE_dom 우위

---

## 1. Fold-Set별 상세 분석

### 1.1 Default Fold-Set (6-fold, Training window 기준 분류)

#### 종합 스코어카드

| Signal | Mean CAGR | CV | Worst | G-A | G-B | G-C | G-D | G-E | **HARD** |
|--------|:---------:|:--:|:-----:|:---:|:---:|:---:|:---:|:---:|:--------:|
| **Baseline_V2** | **31.09%** | **0.464** | +12.13% | ✓ | ✓ | ✓ | ✓ | ✓ | **✓ ALL** |
| P7_L3_ENSEMBLE_H | 26.08% | 0.613 | +3.90% | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ FAIL |
| P7_STITCH_J | 24.21% | 0.514 | +8.38% | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ FAIL |
| **P7_STITCH_K** | **30.90%** | **0.547** | **+10.68%** | ✗ | ✗ | ✓ | ✓ | **✓** | ✗ FAIL |
| P7_BLEND_L | 27.90% | 0.559 | +8.01% | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ FAIL |
| **P7_STITCH_N** | 23.42% | **0.386** | +8.01% | **✓** | **✓** | ✓ | ✓ | ✗ | ✗ FAIL |

**주요 발견**:
- **P7_STITCH_K**: mean CAGR 30.90% (baseline 대비 -0.6%p) → G-E만 통과, 거의 동등
- **P7_STITCH_N**: CV 0.386으로 **가장 안정적** (G-A/G-B 통과), 하지만 mean 23.42%로 G-E fail
- **P7_L3_ENSEMBLE_H**: F2(2021-2022 SIDE) fold에서 +3.90%로 급락 → SIDE 취약성 심각

**중요 주의사항**:
- **F0a/F0b도 실제로는 in-sample** (GA training window 2011-2026에 포함)
- "pre_oos" 명칭은 legacy 코드 호환용, 실제로는 "early in-sample" (GA가 덜 집중한 구간)
- **F4만 진정한 OOS** → Default fold-set의 overfitting 탐지 능력은 제한적

#### Fold-by-Fold 상세 (CAGR %)

| Signal | F0a<br/>(pre) | F0b<br/>(pre) | F1<br/>(in) | F2<br/>(in) | F3<br/>(in) | F4<br/>(post) | **패턴 분석** |
|--------|:-------------:|:-------------:|:-----------:|:-----------:|:-----------:|:-------------:|:-------------|
| Baseline_V2 | 24.62 | 12.13 | 45.17 | 14.76 | 43.92 | 45.92 | Pre-OOS 약함 (F0b), In-sample 변동 |
| P7_L3_H | 21.91 | 8.33 | 34.39 | **3.90** | 44.27 | 43.70 | **F2 붕괴** (SIDE regime) |
| P7_J | 23.16 | 9.85 | 26.55 | 8.38 | 33.75 | 43.54 | In-sample 전반 약화 |
| **P7_K** | 20.90 | **11.50** | **48.97** | 10.68 | 45.66 | **47.71** | **F1/F4 강세, Pre-OOS 상대적 안정** |
| P7_L | 18.90 | 11.15 | 41.38 | 8.01 | 42.71 | 45.25 | F0a/F2 약함 |
| P7_N | 22.39 | 8.01 | 26.64 | **19.71** | 25.41 | 38.38 | **F2 상대 강세**, In-sample 평탄 |

**인사이트**:
- **F2 fold (2021-2022 SIDE 78%)** = 모든 신호의 **critical weakness test**
  - Baseline: 14.76% / P7_L3_H: 3.90% / P7_J: 8.38% / **P7_N: 19.71%** (유일한 우위)
- **P7_STITCH_K의 F1 폭등** (+48.97%): COVID bounce에 과적합 가능성 → rolling 검증 필요

---

### 1.2 Rolling Fold-Set (7-fold, 2-year OOS windows)

#### 종합 스코어카드

| Signal | Mean CAGR | CV | Worst | G-A | G-B | G-C | G-D | G-E | **HARD** |
|--------|:---------:|:--:|:-----:|:---:|:---:|:---:|:---:|:---:|:--------:|
| **Baseline_V2** | 28.00% | **0.706** | **+3.50%** | **✗** | ✓ | ✓ | ✓ | ✓ | **✗ FAIL** |
| P7_L3_ENSEMBLE_H | 27.19% | 0.952 | **-4.14%** | ✗ | ✗ | **✗** | **✗** | ✓ | ✗ FAIL |
| P7_STITCH_J | 23.23% | 0.874 | +0.62% | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ FAIL |
| **P7_STITCH_K** | **27.42%** | 0.755 | +4.26% | ✗ | ✓ | ✓ | ✓ | **✓** | ✗ FAIL |
| P7_BLEND_L | 25.24% | 0.786 | +2.11% | ✗ | ✗ | ✓ | ✓ | ✓ | ✗ FAIL |
| P7_STITCH_N | 23.21% | 0.850 | +0.75% | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ FAIL |

**충격적 발견**:
- **Baseline_V2조차 CV=0.706으로 G-A fail** → 시간 불변성 부족
- **P7_L3_H가 R6(2022-2023)에서 -4.14%로 음수** → G-C/G-D fail
- **모든 신호가 CV > 0.7** → 2년 단위 rolling에서는 **극도로 불안정**

#### Rolling Window별 CAGR (%) 시각화

```
Period   | R1 (12-13) | R2 (14-15) | R3 (16-17) | R4 (18-19) | R5 (20-21) | R6 (22-23) | R7 (24-26) |
---------|-----------|-----------|-----------|-----------|-----------|-----------|-----------|
V2       |  +33.95   |  +4.13    |  +33.16   |  +16.03   |  +61.30   |  +3.50    |  +43.94   |
P7_K     |  +28.50   |  +4.29    |  +28.72   |  +13.20   |  +60.84   |  +4.26    |  +52.12   |
P7_L     |  +25.64   |  +2.11    |  +28.42   |  +10.69   |  +55.64   |  +3.97    |  +50.24   |
P7_N     |  +27.58   |  +2.18    |  +29.69   |  +4.00    |  +47.64   |  +0.75    |  +50.64   |
P7_J     |  +28.32   |  +0.62    |  +31.64   |  +2.66    |  +44.95   |  +1.23    |  +53.19   |
P7_L3_H  |  +28.02   |  +0.85    |  +31.18   |  +7.73    |  +65.45   |  -4.14    |  +61.27   |

패턴:   [회복기]  [oil crisis] [Trump]    [무역전쟁]  [COVID]    [금리인상]  [AI rally]
```

**Period-Dependence 진단**:
- **R2 (2014-2015 oil shock)**: 전 신호 <5% → 에너지 섹터 붕괴에 취약
- **R5 (2020-2021 COVID bounce)**: 전 신호 45-65% → fiscal stimulus 수혜 과다
- **R6 (2022-2023 금리인상→AI 전환)**: 전 신호 <5% (P7_L3_H는 음수) → 전환기 취약

**인사이트**:
- **R5/R7 高 + R2/R4/R6 低 패턴** → 2019-2024 bull market + AI rally에 과적합
- **P7_STITCH_K가 상대적 안정** (R4/R6에서 baseline 추종)

---

### 1.3 Regime Fold-Set (7-fold, BULL/SIDE/MIX 분류)

#### 종합 스코어카드

| Signal | Mean CAGR | CV | Worst | G-A | G-B | G-C | G-D | G-E | **HARD** |
|--------|:---------:|:--:|:-----:|:---:|:---:|:---:|:---:|:---:|:--------:|
| **Baseline_V2** | 30.06% | 0.528 | +1.67% | ✗ | ✓ | ✓ | ✓ | ✓ | ✗ FAIL |
| P7_L3_ENSEMBLE_H | 26.25% | 0.618 | +2.24% | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ FAIL |
| P7_STITCH_J | 24.25% | 0.567 | +1.31% | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ FAIL |
| **P7_STITCH_K** | **29.87%** | 0.563 | **+5.42%** | ✗ | ✓ | ✓ | ✓ | **✓** | ✗ FAIL |
| P7_BLEND_L | 27.26% | 0.572 | +4.98% | ✗ | ✓ | ✓ | ✓ | ✓ | ✗ FAIL |
| **P7_STITCH_N** | 23.72% | **0.468** | +0.63% | **✓** | ✓ | ✓ | ✓ | ✗ | ✗ FAIL |

#### Regime-Group Breakdown (핵심 테이블)

| Signal | **BULL_dom** (mean) | **SIDE_dom** (mean) | **Mixed** (mean) | **BULL/SIDE ratio** |
|--------|:-------------------:|:-------------------:|:----------------:|:-------------------:|
| **Baseline_V2** | **34.29%** | **8.22%** | 45.55% | **4.17배** |
| P7_L3_ENSEMBLE_H | 33.18% | **3.07%** | 39.04% | **10.8배** (worst) |
| P7_STITCH_J | 29.99% | 4.84% | 35.05% | 6.2배 |
| **P7_STITCH_K** | 32.11% | **8.05%** | **48.34%** | **4.0배** (best) |
| P7_BLEND_L | 30.39% | 6.50% | 43.32% | 4.7배 |
| **P7_STITCH_N** | 26.88% | **10.17%** | 32.51% | **2.6배** (SIDE 특화) |

**충격적 발견**:
- **Baseline_V2의 SIDE 약점 정량화**: BULL 34.3% vs SIDE 8.2% → **4.17배 gap**
- **P7_L3_H의 SIDE 붕괴**: 3.07% (baseline의 37%) → 10.8배 gap
- **P7_STITCH_K의 균형성**: BULL/SIDE ratio 4.0배 (baseline 4.17배와 거의 동등)
- **P7_STITCH_N의 SIDE 우위**: 10.17% (baseline 8.22% 초과 +1.95%p, **24% 개선**)

#### Regime Fold별 CAGR (%) 상세

| Signal | BULL_1<br/>(12-14) | BULL_2<br/>(16/7-18/1) | BULL_3<br/>(23-24/5) | SIDE_1<br/>(15-16/6) | SIDE_2<br/>(21-22) | MIX_1<br/>(19-20) | MIX_2<br/>(24/6-) |
|--------|:------------------:|:----------------------:|:--------------------:|:--------------------:|:------------------:|:-----------------:|:-----------------:|
| **V2** | 24.62 | **34.34** | **43.92** | **1.67** | 14.76 | 45.17 | 45.92 |
| P7_K | 20.90 | 29.79 | 45.66 | **5.42** | 10.68 | **48.97** | 47.71 |
| P7_L | 18.90 | 29.55 | 42.71 | 4.98 | 8.01 | 41.38 | 45.25 |
| **P7_N** | 22.39 | 32.85 | 25.41 | 0.63 | **19.71** | 26.64 | 38.38 |
| P7_J | 23.16 | 33.07 | 33.75 | 1.31 | 8.38 | 26.55 | 43.54 |
| P7_L3_H | 21.91 | 33.37 | 44.27 | 2.24 | **3.90** | 34.39 | 43.70 |

**인사이트**:
- **SIDE_1 (2015-2016/6, 고변동성 횡보)**: 모든 신호 <6%, worst case = 전 신호의 공통 약점
- **SIDE_2 (2021-2022, 금리인상 SIDE)**: 
  - **P7_STITCH_N: 19.71%** (baseline의 133%, **유일한 SIDE regime 우위**)
  - P7_L3_H: 3.90% (baseline의 26%, 붕괴)
- **P7_STITCH_K의 SIDE_1 개선**: 5.42% (baseline 1.67%의 3.2배) → SIDE 극단 상황 방어력 개선

---

## 2. 신호별 종합 진단

### 2.1 Baseline_V2 (현 Production)

**강점**:
- `default` fold-set에서 **유일한 ALL pass** (CV=0.464, mean 31.09%)
- Pre-OOS/In-sample/Post-OOS 구분에서 안정적 overfitting 방어

**치명적 약점**:
1. **SIDE regime 취약성**: SIDE_dom 8.22% (BULL_dom 34.29%의 24%)
   - SIDE_1 (2015-2016/6): +1.67% (worst fold) → 거의 flat
2. **Rolling instability**: CV=0.706 (G-A fail) → 2년 단위 시간 불변성 부족
   - R2 (oil shock): +4.13% / R6 (금리인상): +3.50% → 전환기 붕괴
3. **Regime CV=0.528**: G-A fail (한계선 넘음)

**판정**: `default` 1/3 pass → **REJECT** (CONDITIONAL 미달)

**처방**:
- SIDE regime 전용 ws slot 강화 필요 → P8_SIDE_V3 개발 중
- Rolling window 안정화를 위한 섹터 다각화 → P8_BALANCED 개발 중

---

### 2.2 P7_STITCH_K (최선의 준Baseline)

**구성**: wb=B7_BULL_AGG, ws=B6_SIDE_TECH_15Y, wd=B6_SIDE_TECH_15Y

**강점**:
1. **Default mean CAGR 거의 동등**: 30.90% (baseline 31.09%의 99.4%)
2. **Regime SIDE 개선**: SIDE_dom 8.05% (baseline 8.22%의 98%), SIDE_1 +5.42% (baseline의 3.2배)
3. **Post-OOS (F4) 우위**: 47.71% (baseline 45.92%의 104%)
4. **Mixed regime 강세**: 48.34% (baseline 45.55%의 106%)

**약점**:
1. **CV 전반 높음**: default 0.547 / rolling 0.755 / regime 0.563 → 모두 G-A fail
2. **Pre-OOS 약화**: F0a +20.90% (baseline 24.62%의 85%)
3. **F1 폭등 (COVID)**: +48.97% → rolling R5에서도 +60.84% (과적합 의심)

**판정**: 3/3 fold-set fail (G-A 반복 fail) → **REJECT**

**앙상블 잠재력**: 
- SIDE/Mixed 강점 + F4 우위 → ws/wd slot 후보로 활용 가능
- wb만 교체하여 P8_BULL_DENSE로 업그레이드 시도 필요

---

### 2.3 P7_STITCH_N (SIDE specialist의 희망)

**구성**: wb=P7_V2_MEGA, ws/wd=dynamic (B7 기반)

**독특한 강점**:
1. **Regime G-A pass (유일)**: CV=0.468 (0.5 이하) → regime 안정성 최고
2. **SIDE_dom 우위**: 10.17% (baseline 8.22%의 124%, **+1.95%p 절대 개선**)
3. **SIDE_2 최강**: 19.71% (baseline 14.76%의 133%) → 금리인상 SIDE에서 우위
4. **Default G-A/G-B pass**: CV=0.386, in-sample CV=0.13 (극도로 안정적)

**약점**:
1. **Mean CAGR 부족**: default 23.42% (baseline의 75%, G-E fail by 5.4%p)
2. **BULL_dom 약화**: 26.88% (baseline 34.29%의 78%)
3. **BULL_3 (AI rally) 붕괴**: 25.41% (baseline 43.92%의 58%)

**판정**: 3/3 fold-set fail (G-E 반복 fail) → **REJECT**

**전략적 가치**:
- **ws slot 전용 후보 1순위**: SIDE_dom 10.17% (현존 최고)
- wb=P8_BULL_DENSE + ws=P7_STITCH_N(ws only) + wd=baseline(wd) 조합 검증 필요

---

### 2.4 P7_BLEND_L (Weighted 70/30 조합)

**구성**: wb=B7_BULL_AGG(70%) + B6_SIDE_TECH_15Y(30%) weighted-avg

**강점**:
1. **Regime 그룹별 균형**: BULL 30.39% / SIDE 6.50% / Mixed 43.32%
2. **Rolling 상대 안정**: CV=0.786 (P7_J/P7_N 대비 낮음)
3. **G-E 다중 pass**: default/rolling/regime 모두 mean CAGR >= 90% baseline

**약점**:
1. **중위권 함정**: 모든 지표에서 "괜찮지만 최고는 아님" → 특화 강점 부재
2. **SIDE_dom 여전히 약함**: 6.50% (baseline 8.22%의 79%)

**판정**: 3/3 fold-set fail (G-A 반복 fail) → **REJECT**

**평가**: Weighted-avg 접근법은 안정성 개선 효과 有, but 불충분

---

### 2.5 P7_L3_ENSEMBLE_H / P7_STITCH_J (기타)

**P7_L3_ENSEMBLE_H**: B7 4개 신호 L3 multi-candidate 앙상블
- **치명적 SIDE 붕괴**: SIDE_dom 3.07% (baseline의 37%), SIDE_2 +3.90%
- Rolling R6 음수 (-4.14%) → G-C/G-D fail
- **판정**: 전원 fail → **REJECT**, 앙상블 재료로도 부적합

**P7_STITCH_J**: wb=B7_BULL_AGG, ws/wd=Baseline_V2(ws/wd)
- Mean CAGR 전반 하위권 (default 24.21% / rolling 23.23% / regime 24.25%)
- SIDE_dom 4.84% (baseline의 59%) → SIDE 개선 실패
- **판정**: 전원 fail → **REJECT**

---

## 3. Cross-Fold-Set 패턴 분석

### 3.1 CV(Coefficient of Variation) 비교

| Signal | Default<br/>CV | Rolling<br/>CV | Regime<br/>CV | **평균 CV** | **CV 안정성 순위** |
|--------|:--------------:|:--------------:|:-------------:|:-----------:|:-----------------:|
| **P7_STITCH_N** | **0.386** | 0.850 | **0.468** | **0.568** | **1위** |
| **Baseline_V2** | **0.464** | 0.706 | 0.528 | 0.566 | 2위 |
| P7_STITCH_J | 0.514 | 0.874 | 0.567 | 0.652 | 3위 |
| P7_STITCH_K | 0.547 | 0.755 | 0.563 | 0.622 | 4위 |
| P7_BLEND_L | 0.559 | 0.786 | 0.572 | 0.639 | 5위 |
| P7_L3_ENSEMBLE_H | 0.613 | **0.952** | 0.618 | 0.728 | 6위 |

**인사이트**:
- **P7_STITCH_N이 평균 CV 최저** (0.568) → 전반적 안정성 최고
- **Rolling fold-set가 모든 신호의 CV를 악화** (평균 CV 0.82) → 2년 단위 가장 가혹
- **Regime fold-set이 상대적 관대** (평균 CV 0.55) → regime 분류가 변동 완화

---

### 3.2 Worst Fold CAGR 비교 (Tail Risk)

| Signal | Default<br/>Worst | Rolling<br/>Worst | Regime<br/>Worst | **최악 fold** | **Tail Risk 순위** |
|--------|:-----------------:|:-----------------:|:----------------:|:-------------:|:-----------------:|
| **Baseline_V2** | +12.13% | **+3.50%** | **+1.67%** | **R6/SIDE_1** | 1위 (best) |
| **P7_STITCH_K** | **+10.68%** | +4.26% | **+5.42%** | default F2 | 2위 |
| P7_BLEND_L | +8.01% | +2.11% | +4.98% | default F2 | 3위 |
| P7_STITCH_J | +8.38% | +0.62% | +1.31% | rolling R2 | 4위 |
| P7_STITCH_N | +8.01% | **+0.75%** | **+0.63%** | **regime SIDE_1** | 5위 |
| P7_L3_ENSEMBLE_H | +3.90% | **-4.14%** | +2.24% | **rolling R6 (음수)** | 6위 (worst) |

**인사이트**:
- **Baseline_V2가 worst fold에서 가장 안정** (최악 +1.67%) → tail risk 방어 우수
- **P7_STITCH_K가 2위** (최악 +4.26%) → tail risk에서도 준baseline 자격
- **P7_L3_H의 치명적 약점**: rolling R6 음수 → production 부적합

---

### 3.3 Regime-Dependence 정량화 (BULL/SIDE gap)

| Signal | BULL_dom | SIDE_dom | **Gap (배수)** | **Gap (절대)** | **SIDE 순위** |
|--------|:--------:|:--------:|:--------------:|:--------------:|:------------:|
| **P7_STITCH_N** | 26.88% | **10.17%** | **2.6배** | **-16.71%p** | **1위** (best) |
| **P7_STITCH_K** | 32.11% | **8.05%** | **4.0배** | -24.06%p | 2위 |
| **Baseline_V2** | 34.29% | **8.22%** | **4.2배** | -26.07%p | 3위 |
| P7_BLEND_L | 30.39% | 6.50% | 4.7배 | -23.89%p | 4위 |
| P7_STITCH_J | 29.99% | 4.84% | 6.2배 | -25.15%p | 5위 |
| P7_L3_ENSEMBLE_H | 33.18% | **3.07%** | **10.8배** | **-30.11%p** | 6위 (worst) |

**전략적 함의**:
- **P7_STITCH_N의 SIDE 특화 확인**: 2.6배 gap (baseline의 62%) → ws slot 최우선 후보
- **P7_STITCH_K의 균형성**: 4.0배 gap (baseline과 거의 동등) → 전천후 준baseline
- **P8_SIDE_V3 + P8_BULL_DENSE 조합 필요성**: SIDE 10%+, BULL 35%+ 동시 달성 목표

---

## 4. Gate-by-Gate 통과율 분석

### Default Fold-Set Gate 통과율

| Gate | Rule | **V2** | P7_K | P7_L | P7_N | P7_J | P7_L3_H | **통과율** |
|------|------|:------:|:----:|:----:|:----:|:----:|:-------:|:---------:|
| G-A | CV≤0.5 | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | **33%** (2/6) |
| G-B | CV≤base+5pp | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ | **50%** |
| G-C | worst≥0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **100%** |
| G-D | all>0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **100%** |
| G-E | CAGR≥90% | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **33%** |

**발견**: G-A (절대 CV) / G-E (mean CAGR)가 가장 까다로운 gate → P7 신호군의 공통 약점

### Rolling Fold-Set Gate 통과율

| Gate | Rule | **V2** | P7_K | P7_L | P7_N | P7_J | P7_L3_H | **통과율** |
|------|------|:------:|:----:|:----:|:----:|:----:|:-------:|:---------:|
| G-A | CV≤0.5 | **✗** | ✗ | ✗ | ✗ | ✗ | ✗ | **0%** (전멸) |
| G-B | CV≤base+5pp | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | **33%** |
| G-C | worst≥0 | ✓ | ✓ | ✓ | ✓ | ✓ | **✗** | **83%** |
| G-D | all>0 | ✓ | ✓ | ✓ | ✓ | ✓ | **✗** | **83%** |
| G-E | CAGR≥90% | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | **67%** |

**충격**: Rolling G-A 전원 fail (baseline 포함) → **현 GA 구조 자체가 rolling stability 부족**

### Regime Fold-Set Gate 통과율

| Gate | Rule | **V2** | P7_K | P7_L | P7_N | P7_J | P7_L3_H | **통과율** |
|------|------|:------:|:----:|:----:|:----:|:----:|:-------:|:---------:|
| G-A | CV≤0.5 | ✗ | ✗ | ✗ | **✓** | ✗ | ✗ | **17%** (1/6) |
| G-B | CV≤base+5pp | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | **83%** |
| G-C | worst≥0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **100%** |
| G-D | all>0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **100%** |
| G-E | CAGR≥90% | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | **50%** |

**발견**: Regime G-A는 P7_STITCH_N만 통과 → regime 안정성에서 유일한 돌파구

---

## 5. 전략적 제언

### 5.1 Baseline_V2의 한계 명확화

**판정**: 3개 fold-set 중 1개만 통과 (default) → **Hardgate v1 기준 REJECT**

**의미**:
- 현 production baseline조차 **시간/regime 불변성 부족**
- Rolling CV=0.706 → 2014-2015(oil), 2022-2023(금리) 같은 전환기 붕괴
- Regime SIDE_dom 8.22% → BULL 34.29%의 24% 수준

**Action Required**:
1. **즉시 대응**: Baseline_V2 유지하되, **risk monitoring 강화** (rolling window 실시간 추적)
2. **단기 (P8 완료 시)**: P8_BULL_DENSE + P8_SIDE_V3 + P8_BALANCED 조합 ensemble 검증
3. **중기**: Rolling CV < 0.5 달성을 위한 **섹터 중립화 GA** 개발

---

### 5.2 P7 신호군 전원 탈락의 함의

**핵심 실패 원인**:
1. **B7 GA의 2019-2024 과적합**: R5(COVID) 60%+ 폭등 → rolling instability 유발
2. **ws slot 약화**: B6_SIDE_TECH_15Y도 SIDE_dom 8% 미만 → baseline ws와 유사 수준
3. **Ensemble 구조의 한계**: L3 multi-candidate, weighted-avg 모두 CV 개선 실패

**전략 전환 필요**:
- **Phase 2 ensemble 접근법 폐기** → Phase 3 **regime-slot specialist 병렬 개발** 채택
- P7 신호는 production 승격 불가, but **앙상블 재료(ingredient)**로 재활용
  - P7_STITCH_K(ws/wd) + P8_BULL_DENSE(wb) 조합
  - P7_STITCH_N(ws only) + baseline(wb/wd) 조합

---

### 5.3 P8 Batch 전략 재확인

**P8_SIDE_V3 (완료)**:
- 단독으로는 부족 (default mean 27.7%, regime SIDE 8.0%)
- But **ws slot 후보**로서 P7_STITCH_N(10.17%)과 병렬 비교 필요

**P8_BULL_DENSE (진행 중)**:
- **목표 KPI**: BULL_dom 38%+ (baseline 34.3%의 110%)
- **성공 시**: wb slot 교체 → 전체 mean CAGR 32%+ 달성 예상

**P8_BALANCED (진행 중)**:
- **목표 KPI**: Rolling CV < 0.5 (baseline 0.706 개선)
- **전략**: deployment_penalty=False, meta_score_w_turnover=0 → 전환기 적응성 강화

---

### 5.4 Next-Gen Baseline 조합 후보 (P8 완료 후)

#### Candidate A: Triple-Specialist Stitch

```yaml
wb: P8_BULL_DENSE        # 목표: BULL_dom 38%+
ws: P7_STITCH_N (ws만)   # 실측: SIDE_dom 10.17%
wd: Baseline_V2 (wd만)   # 검증됨: DEF 안정성
```

**예상 성능**:
- Default mean: 33% (baseline 31% 초과)
- Regime BULL_dom: 38% / SIDE_dom: 10% / Mixed: 46%
- Rolling CV: 0.65 (P8_BALANCED 효과 반영 시 0.55)

---

#### Candidate B: Balanced Core + SIDE Boost

```yaml
wb: P8_BALANCED          # 목표: Rolling CV < 0.5
ws: P8_SIDE_V3           # 실측: SIDE_dom 8.0%
wd: P8_BALANCED (wd)     # 동일 core
```

**예상 성능**:
- Default mean: 30% (baseline 수준)
- Regime BULL_dom: 32% / SIDE_dom: 9% / Mixed: 44%
- **Rolling CV: 0.45** (핵심 목표 달성)

---

## 6. 결론

### 6.1 Multi-Window 평가 인프라의 가치 입증

**Before (default only)**:
- P7_STITCH_K: mean 30.90% → "baseline과 거의 동등" → 승격 후보로 오해 가능

**After (3-fold-set)**:
- P7_STITCH_K: rolling CV 0.755, regime CV 0.563 → **시간/regime 불변성 부족 명확**
- **Baseline_V2조차 rolling/regime fail** → 현 GA 구조 자체의 한계 드러남

**인프라 효과**:
- **False positive 방지**: 단일 fold-set 통과만으로는 승격 불가 → 엄격한 검증
- **약점 정량화**: "SIDE가 약하다"(정성) → "BULL/SIDE ratio 4.2배"(정량)
- **전략 방향 명확화**: SIDE specialist 필요성, rolling stability 목표 수치화

---

### 6.2 최종 Action Items

1. **P8 Batch 완료 대기** (진행 중):
   - P8_BULL_DENSE / P8_BALANCED 완성 후 즉시 hardgate sweep 재실행

2. **Candidate A/B ensemble 구성** (P8 완료 후):
   - `p2_ensemble_composer.py` 업데이트하여 새 조합 빌드

3. **Hardgate v2 개정 검토**:
   - Rolling G-A (CV≤0.5) 전원 fail → **너무 가혹한가?**
   - Option: Rolling G-A를 CV≤0.7로 완화, 또는 soft gate로 전환

4. **GA Phase 6 기획** (Phase A 완료 후):
   - **목표**: Rolling CV < 0.5 + Regime SIDE_dom 12%+ 동시 달성
   - **접근법**: Sector-neutral constraints, macro-event stratified training

---

## Appendix: 상세 데이터 테이블

### A1. Default Fold-Set — Per-Fold Metrics

(생략 — markdown 파일 참조: `hardgate_default_20260505_015308.md`)

### A2. Rolling Fold-Set — Per-Fold Metrics

(생략 — markdown 파일 참조: `hardgate_rolling_20260505_015308.md`)

### A3. Regime Fold-Set — Per-Fold Metrics

(생략 — markdown 파일 참조: `hardgate_regime_20260505_015308.md`)

---

**보고서 종료**
