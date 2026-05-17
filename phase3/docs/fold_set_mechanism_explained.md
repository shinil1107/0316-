# Multi-Window + Regime-Stratified Fold-Set 메커니즘 상세 설명

**작성**: 2026-05-05
**Phase A 인프라 강화 산출물**

---

## 1. 개요

기존 `default` 6-fold 평가는 **시간축 선형 분할**(pre-OOS/in-sample/post-OOS)에 기반하여 신호의 안정성을 검증했으나, 다음 두 가지 **구조적 맹점**이 존재했습니다:

1. **Period-Dependence Blind Spot**: 특정 기간(예: 2019-2024 bull market)에 과적합된 신호를 탐지하지 못함
2. **Regime-Dependence Blind Spot**: BULL/SIDE/DEFENSIVE 시장 국면별 성능 편차를 정량화하지 못함

**Phase A 인프라 강화**는 이 두 맹점을 해소하기 위해 `rolling` 및 `regime` fold-set를 추가하여, 신호의 **시간 불변성(temporal robustness)**과 **regime 불변성(regime robustness)**을 동시에 검증합니다.

---

## 2. 세 가지 Fold-Set 비교

### 2.1 Default Fold-Set (기존)

**설계 원칙**: Training window(2011-2026, 15년) 기준 early in-sample / core in-sample / post-train OOS 분류ㅐㅐ

**중요**: 현재 GA (Batch 7/8)는 `2011-01-01 → 2026-03-31` 전체를 training window로 사용 → F0a/F0b도 **in-sample**

| Fold | Window | Group | Regime 특성 | 실제 분류 | 목적 |
|------|--------|-------|-------------|----------|------|
| F0a | 2012-2014 | pre_oos | BULL 82% | **early in-sample** | Training window 앞부분 안정성 |
| F0b | 2015-2016 | pre_oos | SIDE 40%, 변동성 高 | **early in-sample** | Training window 앞부분 (SIDE stress) |
| F1 | 2019-2020 | in_sample | COVID 혼합 (BULL 56%/SIDE 44%) | **core in-sample** | GA 집중 최적화 구간 (극단 변동성) |
| F2 | 2021-2022 | in_sample | SIDE 78% | **core in-sample** | GA 집중 최적화 구간 (금리 인상 SIDE) |
| F3 | 2023-2024/05 | in_sample | BULL 74% | **core in-sample** | GA 집중 최적화 구간 (AI rally BULL) |
| F4 | 2024/06-현재 | post_oos | Mixed | **true OOS** | 유일한 진정한 OOS (production과 동일) |

**장점**:
- F0a/F0b (early in-sample): GA가 덜 집중한 구간 → 시간 외삽 능력 부분 검증
- F4 (late in-sample): Training window 끝부분 → 최신 패턴 추종력 검증
- 시간축 전체(2012-2026) 안정성 감사 → temporal stability audit

**치명적 한계**:
- **6개 fold 전부 in-sample** (B7/B8 training ~ 2026-03-31)
- **진정한 OOS 검증 불가** → overfitting 탐지 능력 없음
- F4를 "post_oos"라고 명명했으나 실제로는 **late in-sample**

**재정의된 목적**:
- Default fold-set = "in-sample temporal stability audit" (NOT OOS validation)
- 진정한 OOS = Step C (production simulation) + live trading만 신뢰
- Rolling/Regime fold-set이 더 중요 (temporal/regime overfitting 탐지)

**Phase B 개선 방향**:
- 2024-05 cutoff GA 학습 → F4가 진정한 OOS (1.7년)
- 또는 2008-2010 pre-OOS fold 추가 (pack 재빌드 필요)

---

### 2.2 Rolling Fold-Set — 8-Year Sliding Windows (개편)

**설계 원칙**: 8-year sliding windows (1-year step) — **in-sample temporal stability audit**

기존 7×2-year non-overlapping 설계를 폐기하고 8-year sliding windows로 전환.
이유: 2-year window (500 거래일)는 통계적으로 취약하며, GA training이 2011→2026-03 전체를 커버하므로 모든 fold가 in-sample — OOS를 가장하는 것은 부적절.

**핵심 재정의**: Rolling fold-set은 **in-sample temporal stability audit**이지 OOS validation이 아님.
진정한 OOS validation은 별도 cutoff GA (Phase B P9_OOS_VALIDATION)가 필요.

| Fold | Window | Group | 시장 환경 | 거래일 수 |
|------|--------|-------|----------|----------|
| R1 | 2011-01-01 → 2018-12-31 | sliding | Post-crisis 회복 → 금리 정상화 | ~2000 |
| R2 | 2012-01-01 → 2019-12-31 | sliding | 장기 상승장 → 무역전쟁 | ~2000 |
| R3 | 2013-01-01 → 2020-12-31 | sliding | Bull + COVID 충격 포함 | ~2000 |
| R4 | 2014-01-01 → 2021-12-31 | sliding | Oil shock → COVID bounce → 금리 인상 | ~2000 |
| R5 | 2015-01-01 → 2022-12-31 | sliding | 횡보 + Trump rally + 금리 인상 | ~2000 |
| R6 | 2016-01-01 → 2023-12-31 | sliding | Trump rally → AI rally 전환 | ~2000 |
| R7 | 2017-01-01 → 2024-12-31 | sliding | 무역전쟁 → AI rally 정점 | ~2000 |
| R8 | 2018-01-01 → PACK_END | sliding | 금리 정상화 → 최신 구간 | ~1750 |

**Why 8-year sliding windows?**
- **통계적 견고성**: 8년 ≈ ~2000 거래일 → 고도로 안정적인 fold-level 추정치 (2yr=500일은 불안정)
- **Overlapping = smooth trend**: 1년 step으로 슬라이딩 → 성능 변동의 smooth curve 도출
  - 모든 8개 window에서 일관된 CAGR → 시간적으로 robust한 신호
  - 특정 window에서 CAGR 급락 → period-specific weakness 식별 가능
- **In-sample 명시**: 모든 fold가 GA training range(2011→2026-03) 안에 있음을 명확히 표시 (group="sliding")

**측정 지표**:
- **Sliding CV**: 8개 sliding window 간 CAGR의 CV
  - CV < 0.3 → 시간 불변적 alpha (highly robust)
  - CV 0.3-0.5 → 수용 가능한 변동 (acceptable)
  - CV > 0.5 → 특정 기간에 성능 편차 과다
- **CAGR trend slope**: R1→R8 CAGR의 추세선 기울기
  - 양의 기울기 → 최근 구간에서 개선 (favorable)
  - 음의 기울기 → 최근 구간에서 약화 (concerning, possible decay)

**탐지 사례**:
- **Period-specific weakness**: R3(COVID 포함)에서만 급락 → 극단 변동성에 취약
- **Regime shift sensitivity**: R5/R6(금리 인상기)에서 CAGR 하락 → 매크로 환경 전환에 약함
- **Temporal decay**: R1→R8으로 갈수록 CAGR 단조 감소 → 신호의 alpha decay 가능성

---

### 2.3 Regime Fold-Set (신규)

**설계 원칙**: VIX regime(BULL/SIDE/DEF) 분포를 기준으로 fold를 수동 분류 → **regime 조건부 성능**을 직접 측정

| Fold | Window | Group | Regime 분포 | 특성 |
|------|--------|-------|-------------|------|
| BULL_1 | 2012-2014 | bull_dom | BULL 82% / SIDE 18% | Post-crisis 장기 상승장 |
| BULL_2 | 2016/07-2018/01 | bull_dom | BULL 82% / SIDE 18% | Trump rally 핵심 구간 |
| BULL_3 | 2023-2024/05 | bull_dom | BULL 74% / SIDE 26% | AI rally |
| SIDE_1 | 2015-2016/06 | side_dom | SIDE 40% / BULL 32% / DEF 28% | 고변동성 횡보장 (oil shock) |
| SIDE_2 | 2021-2022 | side_dom | SIDE 78% / BULL 22% | 금리 인상기 |
| MIX_1 | 2019-2020 | mixed | BULL 56% / SIDE 44% | COVID 극단 변동성 |
| MIX_2 | 2024/06-현재 | mixed | BULL/SIDE 혼재 | 실시간 검증 |

**Regime 분류 기준 (VIX 기반)**:
```python
if VIX < 15:  regime = "BULL"    # 저변동성 상승장
elif VIX < 20:  regime = "SIDE"  # 중변동성 횡보장
else:  regime = "DEF"            # 고변동성 방어장
```

**측정 지표**:
- **BULL_dom mean CAGR**: BULL_1/2/3의 평균 → BULL regime 전용 성능
- **SIDE_dom mean CAGR**: SIDE_1/2의 평균 → SIDE regime 전용 성능
- **Regime gap ratio**: BULL_dom / SIDE_dom — 비율이 5배 이상이면 SIDE 취약

**Why BULL/SIDE 비율이 중요한가?**
- **Baseline_V2의 SIDE 약점**: SIDE_dom 평균 8.2% vs BULL_dom 평균 34.3% (4.2배 gap)
  - 2015-2016(SIDE_1): +1.7% (worst fold) → SIDE regime에서 거의 flat
  - 2021-2022(SIDE_2): +14.8% → BULL carry-over effect로 간신히 양수
- **SIDE specialist 필요성**: P8_SIDE_V3, P7 SIDE stitched 등은 이 gap을 줄이기 위한 전략

---

## 3. 세 가지 Fold-Set의 상호 보완성

| Aspect | Default | Rolling | Regime | 통합 효과 |
|--------|---------|---------|--------|----------|
| 시간 불변성 | △ (F0a/F0b로 부분 검증) | ✓✓✓ (8×8yr sliding) | △ | Rolling이 핵심 검증 |
| Regime 불변성 | ✗ | △ | ✓✓✓ | Regime이 핵심 검증 |
| Overfitting 탐지 | ✓✓✓ | ✓ | ✗ | Default가 핵심 검증 |
| Production 대표성 | ✓✓ (F4 = real-time) | ✓ (R8 = 최신 구간 포함) | ✓ (MIX_2 = real-time) | 3개 fold-set 모두 최신 구간 포함 |
| 최악 시나리오 식별 | ✓ (F0b) | ✓✓ (8개 overlapping window → smooth trend) | ✓✓✓ (SIDE_1 = 변동성 극단) | Regime이 가장 정밀 |

**통합 판정 규칙 (Hardgate v1)**:
```
if (ALL hard gates pass on ALL 3 fold-sets):
    verdict = "PROMOTE"     # 현 baseline 교체
elif (ALL hard gates pass on 2/3 fold-sets):
    verdict = "CONDITIONAL" # 인간 리뷰 필요
else:
    verdict = "REJECT"
```

**예시 — P7_STITCH_J**:
- `default`: G-E fail (mean 28.1% < baseline의 90% = 28.0%) → marginal fail
- `rolling`: CV=0.86 (G-A fail), worst fold -1.7% (G-C fail) → clear fail
- `regime`: SIDE_dom 8.4% (baseline 8.2%와 동등) → regime은 pass할 가능성

→ **통합 판정**: 2/3 fail 예상 → REJECT (단, SIDE_dom 동등성은 앙상블 재료로 활용 가능)

---

## 4. 메커니즘 구현 세부사항

### 4.1 Fold 정의 (`step_d_walk_forward.py`)

```python
FOLDS_ROLLING: List[Dict[str, str]] = [
    {"id": "R1", "start": "2011-01-01", "end": "2018-12-31", "group": "sliding"},
    {"id": "R2", "start": "2012-01-01", "end": "2019-12-31", "group": "sliding"},
    # ... R3-R7 (1-year step, 8-year window each)
    {"id": "R8", "start": "2018-01-01", "end": PACK_END_STR,  "group": "sliding"},
]

FOLDS_REGIME: List[Dict[str, str]] = [
    {"id": "BULL_1", "start": "2012-01-01", "end": "2014-12-31", "group": "bull_dom"},
    {"id": "SIDE_1", "start": "2015-01-01", "end": "2016-06-30", "group": "side_dom"},
    # ... 7 folds total
]
```

### 4.2 Regime 분포 계산 (`_regime_distribution`)

각 fold 실행 시 VIX regime map을 사용해 해당 기간의 regime 분포를 계산:

```python
def _regime_distribution(vix_regime_map: Dict[str, str],
                         start: str, end: str) -> Dict[str, int]:
    counts = {"BULL": 0, "SIDE": 0, "DEF": 0}
    for date, regime in vix_regime_map.items():
        if start <= date <= end:
            counts[regime.upper()] += 1
    return counts
```

**출력 예시** (Baseline_V2, SIDE_2 fold):
```
Regime (B/S/D): 102/362/0
→ SIDE 78% (362/464일) → side_dom 그룹 분류 타당성 확인
```

### 4.3 Gate 계산 확장 (`_compute_gates_v2`)

**Hard gates (G-A ~ G-E)**:
```python
g_a = cv_cand <= 0.5                      # 절대 안정성
g_b = cv_cand <= cv_base + 0.05           # 상대 안정성 (5pp 허용)
g_c = worst_fold_cagr >= 0                # 음수 fold 불허
g_d = all_fold_cagr > 0                   # 전수 양수
g_e = mean_cagr >= baseline_mean * 0.90   # CAGR 하한 (10% 허용)
```

**Soft gates (G-F ~ G-H)**:
```python
g_f = worst_mdd <= baseline_mdd * 1.10    # MDD 상한 (10% 허용)
g_g = mean_sharpe >= baseline_sharpe * 0.90  # Sharpe 하한
g_h = oos_cagr_std <= baseline_oos_std    # OOS 일관성
```

### 4.4 Regime 그룹 통계 (`_aggregate` 확장)

`_aggregate`가 fold의 `group` 필드를 동적으로 인식하여 그룹별 통계를 자동 생성:

```python
def _aggregate(folds_results):
    by_group = {}
    for fold_result in folds_results:
        group_name = fold_result.get("group", "unknown")
        by_group.setdefault(group_name, []).append(fold_result["CAGR"])
    
    # 각 그룹별 mean/std/CV 계산
    return {
        "all": _stats(all_cagrs),
        "bull_dom": _stats(by_group.get("bull_dom", [])),
        "side_dom": _stats(by_group.get("side_dom", [])),
        # ...
    }
```

**콘솔 출력**:
```
REGIME-GROUP BREAKDOWN (mean CAGR %)
Signal                   BULL_dom   SIDE_dom      Mixed
--------------------------------------------------------
Baseline_V2                +34.3%      +8.2%     +45.5%
P7_STITCH_J                +30.1%      +8.4%     +43.5%
```

→ **인사이트**: P7_STITCH_J는 BULL에서 4.2%p 열위이지만, SIDE에서는 거의 동등 (+0.2%p) → SIDE specialist로서의 가치 有

---

## 5. 실전 활용 시나리오

### 시나리오 A: BULL specialist 검증

**목표**: P8_BULL_DENSE가 BULL regime에서 baseline을 능가하는지 확인

1. `regime` fold-set 실행
2. **BULL_dom mean CAGR** 비교:
   - Baseline_V2: 34.3%
   - P8_BULL_DENSE: 목표 38%+ (10% 초과 달성)
3. `default` fold-set으로 overfitting 검증 (F0a/F0b pre-OOS에서도 양수 유지)

### 시나리오 B: SIDE regime 취약점 진단

**목표**: 왜 Baseline_V2가 SIDE에서 약한가?

1. `regime` fold-set의 SIDE_1/SIDE_2 세부 메트릭 분석:
   - **SIDE_1** (2015-2016/06): +1.7%, MDD 24% → 횡보장 진입 시 손실 방어 실패
   - **SIDE_2** (2021-2022): +14.8%, IC_3M -0.0088 → BULL carry-over로 양수이나 IC는 음수
2. **진단**: wb(BULL slot)의 factor들이 SIDE regime에서 non-predictive
3. **처방**: ws(SIDE slot) 강화 → P8_SIDE_V3 개발

### 시나리오 C: Temporal stability audit (in-sample)

**목표**: 8-year sliding window별 CAGR이 안정적인지 확인

1. `rolling` fold-set 실행
2. **Fold-by-fold CAGR** 시각화:
   ```
   R1   R2   R3   R4   R5   R6   R7   R8
   +22  +24  +20  +21  +19  +23  +22  +21  ← stable (CV < 0.3)
   +30  +28  +15  +12  +25  +28  +30  +32  ← concerning (drop in R3/R4)
   ```
3. **패턴 분석**:
   - 8개 window 전체에서 일관된 CAGR → temporally robust
   - R3/R4 급락 → 2013-2021 구간의 특정 시장 환경에서 약점 (COVID 전후)
   - R1→R8 단조 감소 추세 → alpha decay 가능성, 신호 유효기한 점검 필요

---

## 6. 한계 및 향후 개선 방향

### 현재 한계

1. **Regime fold 경계 수동 설정**: BULL_2의 종료일(2018-01-31)이 임의적 → 자동화 필요
2. **DEF regime 부재**: 7개 fold 중 DEF-dominant fold 없음 (2020/03 flash crash는 1개월에 불과)
3. **Default fold-set의 OOS 부족**: 6개 fold 중 F4만 true OOS → overfitting 탐지 능력 제한적
4. **Early in-sample bias**: F0a/F0b도 training window에 포함 → "pre-OOS"라는 명칭과 불일치

### 향후 개선 (Phase B 후보)

1. **자동 regime segmentation**: VIX regime change-point detection 알고리즘으로 fold 경계 자동 생성
2. **Sector-stratified fold-set**: 섹터별 성능 편차 검증 (tech/finance/energy 등)
3. **Macro-event fold-set**: 특정 이벤트 기반 fold (QE taper, rate hike cycle, election year 등)
4. **True pre-training OOS fold 추가**: 2008-2010 구간 등 training window 앞 구간 평가 (delisted ticker 확보 시)

---

## 7. 요약 — 세 가지 Fold-Set의 역할

| Fold-Set | 핵심 검증 대상 | 판정 기준 | Pass 시 의미 |
|----------|---------------|----------|-------------|
| **default** | Overfitting | G-B(CV), G-C(worst fold) | Training window 밖에서도 안정적 |
| **rolling** | Temporal stability (in-sample) | G-A(CV<0.5), CAGR trend slope | 8-year sliding windows에서 성능 일관 → temporal robustness |
| **regime** | Regime-dependence | BULL_dom/SIDE_dom gap < 4배 | 모든 시장 국면에서 작동 |

**최종 판정**: 3개 fold-set 모두 통과 → **PROMOTE** (production baseline 교체 승인)
