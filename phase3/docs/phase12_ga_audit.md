# Phase 1/2 — GA Formula & Scoring Logic Audit

**Status:** DRAFT (pre-Phase 2 gate review)
**Scope:** Cell 0 of `0315 windows이사.ipynb` — `evaluate_individual_qresearch` 및 그 의존 함수들
**Goal:** Phase 2 (Walk-forward CV) 착수 전에 기존 GA 공식과 점수측정 로직의 **비합리적 요소 / 숨은 bias / bug-like 거동**을 식별한다. Phase 2 가 분할 평가(fold) 를 도입하면 기존 결함이 증폭되기 때문에 먼저 정리해야 한다.
**Method:** 공식을 수학식으로 재구성하고 edge case, scale comparability, IS/OOS leakage, regime routing consistency 를 수동 검증.

---

## 0. Executive Summary

### 결론
- **치명적 버그는 발견되지 않음** — 공식들은 의도대로 동작하고 있다.
- 단, **설계 수준의 의심스러운 결정** 8건이 확인되며, 이 중 **3건(F1, F4, F11)** 은 Phase 2 이전에 수정을 권장한다.
- 나머지 5건은 의식적 설계일 수 있으므로 **사용자 확인 후 수정 여부 결정**.
- 점수측정(scoring) 레이어는 구조적으로 건전하나 한 가지 **feature scale 일관성** 문제가 존재 (S1).

### 영향도 분포

| Level | 개수 | 대표 항목 |
|---|---|---|
| CRITICAL (must fix before Phase 2) | 1 | F11 — Spread ↔ IC universe 불일치 |
| HIGH (should fix before Phase 2) | 3 | F1 · F4 · S1 |
| MEDIUM (document or fix post-Phase 2) | 4 | F2 · F6 · F7 · F8 |
| LOW (design notes) | 3 | F3 · F5 · F13 |

### 역사적 맥락
현재 live 시그널(V2_GOLDEN_ENS_L3_v1) 의 **anchor (95% weight in BULL, 70% in SIDE, 85% in DEF)** 는 P2_BATCH11 이다. 이는 `enable_cs_rank_features=False` (기본값) 로 훈련되었을 가능성이 높다. 즉 **원시 기술적 피처 (스케일 상이)** + **기본 GA 공식** 로 선발됐다 — 본 감사의 결과는 직접적으로 이 signal 의 한계를 설명한다.

---

## 1. Methodology

### 감사 대상 (함수 단위)

| 카테고리 | 함수 | 위치 (cell 0 line) |
|---|---|---|
| **Fitness base** | `evaluate_individual_qresearch` 본체 | ~5378 ~5700 |
| **Score** | `score_vector_for_day` | 5268 |
| **Regime routing** | `_collapse_diag_regime_to_scoring`, `_scoring_regime_weight_map`, `get_regime_active_weight_vector` | 1083, 4010, 5142 |
| **Metrics** | `_spearman_corr`, `_calc_spread` | 3750, 3804 |
| **Penalties** | `compute_selected_factor_corr_penalty`, `compute_bull_floor_penalty`, `compute_bull_spread_bonus` | 4155, 4195, 4228 |
| **Bonuses** | `compute_side_soft_bias`, `compute_bull_breadth_soft_bias`, `compute_bull_breakout_presence_bonus` | 5164, 5207, 5239 |
| **Diversity** | `_diversity_terms` | 4762 |
| **Feature normalization** | `_rank_to_01`, `_cs_zscore_preserve_nan`, feature block builders | 2560, 2797, 3110+ |
| **Completeness** | `_get_feat_valid`, `_selected_feature_recent_completeness_mask` | 4347, 4364 |

### 검증 기준
1. **Dimensional consistency** — 덧셈 항들의 단위/스케일이 호환되는가
2. **Universe consistency** — IC, Spread, Turnover 가 같은 투자가능 집합 위에서 계산되는가
3. **Regime routing consistency** — per-regime 가중치가 일관되게 적용되는가
4. **Edge case safety** — k=0, 1, NaN, empty tradable 등에서 blow-up 하지 않는가
5. **Lookahead safety** — 훈련 시점에 미래 정보가 새지 않는가

---

## 2. Findings — GA Fitness Formula

### F1 · HIGH · Penalty/Bonus scale dominates Base signal

**위치:** `evaluate_individual_qresearch` final fit assembly

**현 공식 요약:**
```
fit = w_ic1·regime_ic1 + w_ic3·regime_ic3 + w_spread·regime_spmix   (= base)
    + entropy_bonus · entropy                # 0.08 × [0,1]
    − conc_pen                                # 0.12 × max(0, maxw − 1/k)
    − k_pen − corr_pen − bull_floor_pen
    + bull_spread_bonus                       # max(0, sp − thr) × λ  (unbounded)
    + side_soft_adj + bull_breadth_adj + bull_breakout_adj   # ±fixed step
    − risk_pen − neg_spread_pen
    − to_pen − cost_pen                       # NEW T1
```

**수치 예 (V2_GOLDEN_ENS_L3_v1 / 1-year test pack):**
| Term | 관측값 |
|---|---|
| `base` (IC·Spread 합성) | ≈ +0.020 ~ +0.050 |
| `entropy_bonus × entropy` | 0.08 × 0.9 ≈ +0.072 |
| `conc_pen` | ≈ +0.020 ~ +0.040 |
| `bull_spread_bonus` | 0 ~ +0.3 (unbounded) |
| `side/bull breadth` | ±0.01 ~ ±0.05 step |

**문제:**
- **실제 알파 개선**(IC 0.030 → 0.040, quant 기준 대형 개선) 은 fit 에 +0.004 (w_ic1=0.4×0.01) 만 기여
- **엔트로피 개선**(0.85 → 0.95) 은 fit 에 +0.008 기여
- **diversity 가 알파를 압도한다** — GA 는 진짜 신호보다 "여러 팩터 분산" 을 선호하는 편향을 가진다

**Evidence:** P5 Signal Health Report 에서 기간별 IC 하락이 관찰됨에도 여전히 fit 이 높게 평가된 이유 중 하나. "공식 상" fit 은 높지만 실제 edge 는 미미 → 과적합의 형태가 아니라 **잘못된 목적 함수**.

**Recommendation:**
- `entropy_bonus` 를 `base` scale 에 맞춰 0.08 → 0.02~0.03 로 downscale
- 또는 entropy 를 **hard constraint** (e.g., ent ≥ 0.6 필수, 미달시 -큰수) 로 전환하고 fit 에서 제거
- **Phase 2 이전 처리 권장** — 분할 평가 시 base 가 더 작아지기 때문에 bonus 지배 효과가 심해짐

---

### F2 · MEDIUM · Silent fallback: regime-weighted → simple mean

**위치:** `evaluate_individual_qresearch` ~line 5513-5525

```python
if np.isfinite(regime_ic1):
    base += w_ic1 * regime_ic1
elif np.isfinite(mic1):
    base += w_ic1 * mic1
```

**문제:**
- `regime_ic1` 이 NaN 이 되는 경우 = 모든 regime 의 per-regime mean IC 가 NaN (가중평균이 정의 안됨) 이거나, regime_weight 합이 0
- 이때 조용히 `mic1` (전체 mean) 으로 전환됨 → **집계 방식이 다른 두 후보가 같은 fit scale 로 비교**됨
- 실질 위험: 작은 k_used 에서 특정 regime 에만 데이터가 있을 때 발생

**Observation:** 실전에선 regime_ic1 이 거의 항상 finite 이므로 fallback 발동 빈도는 낮을 것으로 추정. 그러나 **침묵**이 문제 — 발동 여부가 diag meta 에 기록되지 않는다.

**Recommendation:**
- `meta["base_agg_mode"]` 필드 추가: `"regime_weighted" | "simple_mean_fallback" | "ic1_fallback_only"`
- Phase 5 retrain 시 fallback 발생 빈도 모니터링
- **Phase 2 이전 처리 불필요** — 진단만 추가

---

### F3 · LOW · `conc_pen` asymmetry at boundary

**위치:** `conc_pen = conc_penalty × max(0, maxw − 1/k_used)` (line 5532)

**관찰:**
- k=1, maxw=1: pen = 0 (명시적 single factor 는 처벌 안 함 — `k_pen` 이 처리)
- k=2, weights=(0.9, 0.1): maxw=0.9, 1/k=0.5, pen = 0.12 × 0.4 = 0.048 (base 의 ~200%)
- k=10, weights uniform: maxw=0.1, 1/k=0.1, pen = 0

**평가:** 공식 자체는 합리적. **이슈는 F1 과 연동 (pen scale 이 base 보다 크다)**. F1 해결 시 함께 해소됨.

---

### F4 · HIGH · Entropy 는 regime-averaged weight vector 에서 계산

**위치:** line 5527

```python
w_avg = (active_w_bull + active_w_side + active_w_def) / 3.0
div = _diversity_terms(sel_union, w_avg)
```

**문제:**
- `sel_union = mask_bull | mask_side | mask_def` → 모든 regime 합집합 (k 가 과대)
- `w_avg` = 세 regime 가중치 단순 평균 — 한 regime 에만 활성화된 팩터는 weight 가 1/3 로 축소되어 "낮은 기여" 로 계산됨
- **Consequence:** GA 는 **모든 regime 에서 공통 활성** 인 팩터 조합을 선호하는 편향 → regime specialization 이 억제됨 (Phase 2 T2 목표와 정반대)

**수치 예:**
```
Cand A: mask_bull=[F1,F2], mask_side=[F1,F2], mask_def=[F1,F2]  (공통)
        → w_avg entropy high (F1,F2 각각 0.5 avg weight, k=2 기준 entropy=1.0)

Cand B: mask_bull=[F1,F2], mask_side=[F3,F4], mask_def=[F5,F6]  (완전 분리)
        → sel_union={F1..F6}, k=6, 각 팩터 avg weight ≈ 0.083
        → entropy much higher on k=6 but per-regime entropy may be zero
```
두 후보의 **실제 다양성 의미가 다름** 에도 entropy_bonus 는 둘 모두 유리하게 평가.

**Recommendation:**
- Entropy 를 per-regime 별도 계산 후 regime-weight 로 가중평균
  ```
  ent_bull = _diversity_terms(mask_bull, active_w_bull)["entropy"]
  ent_side = _diversity_terms(mask_side, active_w_side)["entropy"]
  ent_def  = _diversity_terms(mask_def,  active_w_def)["entropy"]
  entropy = w_map["BULL"]*ent_bull + w_map["SIDE"]*ent_side + w_map["DEFENSIVE"]*ent_def
  ```
- `conc_pen` 도 동일 방식으로 per-regime maxw 가중평균
- **Phase 2 이전 처리 권장** — T2 (regime specialization) 의 개념적 근거를 확보

---

### F5 · LOW · `_diversity_terms` normalization

**위치:** `_diversity_terms` (line 4762)

```python
ent_norm = ent / log(k)   # 0~1
```

**관찰:** k 별 최대 entropy 가 다르므로 log(k) 정규화는 타당. 단, entropy=1.0 의 의미가 k=4 와 k=20 에서 다름 (동일하게 "완전 균등"이지만 정보량 해석 상이). F4 수정 시 together 처리 권장.

---

### F6 · MEDIUM · BULL-only asymmetric penalty/bonus design

**위치:** `compute_bull_floor_penalty`, `compute_bull_spread_bonus`, `compute_bull_breadth_*`, `compute_bull_breakout_*` (line 4195, 4228, 5207, 5239) — `compute_side_soft_bias` (line 5164) 는 SIDE 용 존재하나 제한적

**현상:**
- BULL regime 전용 floor penalty + spread bonus + breadth/breakout bonus 가 다수 존재
- SIDE 는 core factor count 기반 step bonus 만 존재
- DEF 는 **전용 조정 항이 전무**

**해석:** 과거 "DEF CAGR > BULL CAGR" 문제(사용자 언급) 를 사후 보정하려고 도입된 것으로 추정. 즉 **공식 자체가 특정 진단을 교정하는 ad-hoc 패치** 로 성장함.

**위험:**
- BULL 쪽에만 강한 정책이 걸려 있어 GA 는 "BULL 에서 잘 나와야 한다" 는 강한 prior 를 내재화한다
- 실전에서 SIDE regime 이 >50% 인데 SIDE 품질은 암묵적으로만 평가
- DEF 는 baseline IC 외 어떤 품질 보장도 없음

**Recommendation:**
- 이 변수들의 default `enable_*` flag 를 모두 OFF 로 놓고 baseline 을 먼저 돌린 뒤
- 명시적으로 "어떤 regime 에 어떤 prior 를 걸고 싶은지" 사용자 승인 후 재활성화
- **Phase 2 이전 처리 불필요** — T2 (regime-specialization) 도입 시 함께 재설계

---

### F7 · MEDIUM · `bull_spread_bonus` 가 unbounded

**위치:** `compute_bull_spread_bonus` (line 4228)

```python
bonus = max(0, bull_spread − threshold) × λ
```

**문제:**
- 상한 없음. 특정 기간에 BULL regime 이 짧고 spread 가 일시적으로 큰 값(예 0.3) 나오면 bonus = 0.3 × λ → 예: λ=1.0 일 경우 bonus=0.3 (base 의 6-10배)
- GA 는 "BULL 기간 spread 를 극단적으로 키우는 조합" 을 선호 → **비정상 이벤트에 과적합** 유도

**Recommendation:**
- `bonus = min(max_bonus_cap, max(0, sp − thr) × λ)` 형태로 cap 도입
- 또는 tanh/logistic 으로 soft cap
- F1 해결과 병행 (scale 문제의 일부)

---

### F8 · MEDIUM · Step-function soft-bias 의 불연속성

**위치:** `compute_side_soft_bias`, `compute_bull_breadth_soft_bias`, `compute_bull_breakout_presence_bonus`

```python
if count >= threshold: return +bonus
else:                  return −penalty
```

**문제:**
- count=threshold−1 ↔ count=threshold 의 fit 격차가 `bonus + penalty` 의 큰 값 (예: 0.08 + 0.04 = 0.12) → **base 보다 큰 불연속**
- GA mutation 이 이 경계를 자주 넘나들면 fit landscape 가 단층화 → local optimum trap

**Recommendation:**
- Piecewise-linear ramp: `bonus × min(1, count/threshold)` 형태로 매끄럽게
- 또는 threshold −1, threshold, threshold+1 3단계 구분
- F1 해결 후 병행

---

### F9 · LOW · `risk_pen` 은 raw 단위

**위치:** line 5549-5557

```python
_downside_vol = std(negative returns)   # raw return units
risk_pen = fitness_downside_vol_lambda × _downside_vol
```

**평가:** λ=0.5 × vol=0.03 → pen=0.015 이 base 와 비슷한 스케일. 공식은 합리적. **현재 default OFF (`enable_fitness_risk_penalty=False`)** 이므로 신호에는 영향 없음. F1/F4 과 무관.

---

### F10 · MEDIUM · Turnover 를 top-quintile 이 아닌 top-N 으로 측정 (T1 신규)

**위치:** 새로 추가한 `_compute_deployment_penalties` (Phase 1 hook)

**현 설계:**
- Phase1 구현에서 top-N=30 (=Phase3 포트폴리오 사이즈) 으로 turnover 계산
- 반면 base Spread/IC 은 top-quintile (q=0.15, N ~ 125 종목) 기반

**문제:**
- GA 는 top-quintile 을 최적화하지만 T1 은 top-30 (더 엄격한 집중) 를 평가
- 두 평가가 다른 subset 에서 일어나 불일치

**Recommendation:**
- 의도적 설계 (live deployment = top-30 이므로 옳음) — 문서로 명시
- F11 과 함께 Spread 를 top-N 으로 통일하는 안 고려 (복잡해짐)
- 추가 진단 메트릭으로 "top-quintile turnover" 도 별도 수집하면 비교 가능

---

### F11 · CRITICAL · Spread 가 IC 와 다른 universe 에서 계산됨

**위치:** `_calc_spread` (line 3804) vs IC 계산 (line 5450-5451)

**IC:**
```python
m1 = tradable[di] & np.isfinite(svec) & np.isfinite(fwd1[di])
ic1 = _spearman_corr(svec[m1], fwd1[di][m1])
```

**Spread:**
```python
def _calc_spread(score, fwd, q):
    m = np.isfinite(s) & np.isfinite(r)   # ← tradable 필터 없음!
    ...
```

**결과:**
- IC 는 투자가능(`tradable`) 종목만 사용
- Spread 는 `tradable=False` 여도 score/fwd 가 finite 이면 포함
- 비유동/거래정지 종목이 score 상위권이면 **실제 투자 불가능한 spread 를 fit 에 반영**

**심각도:** Spread 가 base 의 20% 가중치 (`w_spread=0.20`) 를 차지. 더 큰 문제는 **GA 가 "illiquid gem" 을 선호하도록 유도**될 수 있다는 점.

**Evidence:** V2 signal 의 P3 capacity 분석 (phase12_signal_health_report.md) 에서 low-ADV 종목 편향 확인됨 — 이 finding 이 원인의 일부일 가능성이 매우 높다.

**Fix:**
```python
# In evaluate_individual_qresearch per-date loop:
svec_tr = np.where(tradable[di], svec, np.nan)   # zero-out untraded
sp1 = _calc_spread(svec_tr, fwd1[di], q=q)
sp3 = _calc_spread(svec_tr, fwd3[di], q=q)
```
또는 `_calc_spread` 에 `tradable` 인자 추가.

**시급도:** **Phase 2 이전 필수 수정**. Phase 2 는 per-fold 로 Spread 를 재계산하므로 이 버그가 모든 fold 에 내재됨.

---

### F12 · LOW · `_calc_spread` top/bot overlap when k==n

**위치:** `_calc_spread` line 3818

```python
bot = rr[order[-k:]] if k < n else rr[order]
```

Edge case: n ≤ 1/q 이면 top 과 bot 이 동일 → spread=0. 현실에서 D≤10 종목만 투자가능한 경우는 거의 없지만 초반 기간/작은 universe 에서 가능.

**Fix:** `if k >= n // 2` 면 NaN 반환. **Low priority.**

---

### F13 · LOW · Meta-search perturbs regime_weight → 비교 왜곡

**위치:** `meta_regime_profile_candidates` (line 593) `REG_A..F`

**관찰:**
- Meta-search 가 `regime_weight_bull/side/def` 를 trial 별로 변경
- 이 가중치는 `_scoring_regime_weight_map` 을 통해 `regime_ic1/ic3/spmix` 로 들어감 → **trial 별로 fit 의 정의가 다름**
- Stability layer 는 여러 trial 의 top-N 을 seed 다중 실행 → fitness 직접 비교

**평가:** 설계상 의도된 것이지만, Stability layer 가 이를 순수 fitness 로 비교하면 편향. 현재는 meta-level 에서 "best trial" 을 먼저 뽑은 뒤 seed 재실행이므로 비교적 안전.

**Recommendation:** 문서화 만, 수정 불필요.

---

## 3. Findings — Scoring Logic

### S1 · HIGH · Technical feature scale 불일치

**위치:** `build_precompute_panel` (line 3110+)

**현상:**
- **Fundamental features:** 항상 `_rank_to_01` 로 균등 [0,1] 로 정규화
- **Interaction features:** 항상 z-score + rank_to_01 로 정규화
- **Technical features:** `enable_cs_rank_features=True` 일 때만 rank_to_01 정규화. **Default False** → raw 값 사용

**결과 (`enable_cs_rank_features=False` 일 때):**
- Tech feature 들은 sigmoid 또는 raw 형태 (코드 주석: "sigmoid compression" 을 없애려고 cs_rank 도입 언급)
- Fund/Interaction 은 [0,1] 균등, Tech 는 대략 (0,1) 이지만 분포 달라질 수 있음
- `score = Σ wv[k] × feat[k]` 는 스케일이 가장 큰 팩터가 지배

**영향:**
- 특정 tech 팩터(예: 강한 모멘텀 지표) 가 weight 와 무관하게 score 를 지배
- GA 는 "적은 weight 로도 score 를 장악하는 팩터" 를 선호 → **표면적으로는 다양화된 것처럼 보이나 실질은 단일 팩터 driven**

**Evidence:** P5 Signal Health Report 에서 V2 signal 이 특정 regime (DEF) 에서 과도한 의존성을 보인 근본 원인일 가능성 있음.

**Recommendation:**
- Phase 5 retrain 시 `enable_cs_rank_features=True` 로 고정
- 또는 `score_vector_for_day` 내부에서 per-date z-score / rank 정규화를 **GA-independent** 로 강제
- **Phase 2 이전 처리 권장** — 분할 평가 전에 feature scale 일관화 필요

---

### S2 · MEDIUM · Rolling-IC adaptive weight 의 NaN 처리

**위치:** `score_vector_for_day` line 5295

```python
_ic_mult = np.where(np.isfinite(_ic_col), np.maximum(_ic_col, _ic_floor), 1.0)
wv = wv * _ic_mult
```

**관찰:**
- Recent IC 가 NaN (롤링 윈도우 데이터 부족) → multiplier=1.0 (base weight 유지)
- Recent IC 가 음수 → `np.maximum(negative, floor)` 에 의해 clamp
  - `floor=0`: 음수 IC 팩터는 weight 0 (사실상 제거)
  - `floor=0.05`: 실제 IC=-0.03 이어도 0.05 취급 → **잘못된 팩터 boost**

**위험:** Default `enable_rolling_ic_adaptive=False` 이면 무관. 활성화된 경우 floor 설정값이 품질에 중요.

**Recommendation:** 기능 사용 시 floor=0 권장. 문서화.

---

### S3 · LOW · Score cache key rounding

**위치:** `_score_cache_key` (line 5262)

```python
wv = tuple(np.round(w, 12).tolist())
```

**평가:** 12-decimal rounding, 충돌 확률 극히 낮음. 단, GA 가 random seed 고정으로 deterministic replay 할 때 12-decimal 이하 차이가 우연히 같으면 캐시 히트 → 미세한 비결정성 가능. 현실적 영향 없음.

---

### S4 · OK · Chronic completeness 필터

**위치:** `_selected_feature_recent_completeness_mask` (line 4364)

**설계:**
- 최근 lookback (default 252일) 안에서 선택된 팩터들이 모두 finite 인 비율
- min_ratio=0.95, min_valid_days=126 조건 → 장기적으로 결측이 많은 종목 exclude

**평가:** 합리적. Survivorship bias 우려는 있으나 min_valid_days 강제로 신규 종목은 처음 6개월은 제외됨 — 의도적.

**Action:** 없음.

---

### S5 · LOW · `_collapse_diag_regime_to_scoring` 는 4→3 매핑

**위치:** line 1083

```
BULL  → BULL
SIDE  → SIDE
BEAR  → DEFENSIVE
CRASH → DEFENSIVE
unknown → SIDE   (default fallback)
```

**평가:** BEAR 와 CRASH 를 DEFENSIVE 로 통합 → 가중치·마스크 각각 1조씩만 유지하면 됨. 합리적. 단 실전 시 CRASH 는 매우 다른 regime (극단 리스크) 인데 BEAR 와 같은 방어 팩터로 대응하는 것이 맞는지는 **별도 검증 필요** (추후 T6 후보).

---

## 4. 교차 검증

### 4.1 Spread vs IC universe 정합성 테스트 (F11)

간단한 실험 제안:
```python
# For the live pack + signal, compute:
A. IC over m = tradable & finite(score) & finite(fwd)
B. Spread over m = finite(score) & finite(fwd)             (현재)
C. Spread over m = tradable & finite(score) & finite(fwd)  (제안)

|B - C| = ?
```
Phase 2 이전 실제로 수치 차이를 확인하여 영향도를 정량화하는 것이 좋다 (5분 스크립트).

### 4.2 Entropy 의 실제 영향도 측정 (F1)

현재 live signal 의 fit 구성을 분해:
```
meta = {
    base: w_ic1*ic1 + w_ic3*ic3 + w_spread*spmix,
    entropy_bonus_contrib: cfg.entropy_bonus * entropy,
    conc_pen, k_pen, corr_pen, bull_floor_pen, bull_spread_bonus,
    side_soft, bull_breadth, bull_breakout,
    risk_pen, neg_spread_pen, to_pen, cost_pen
}
→ |entropy_bonus_contrib| / |base| 비율 계산
→ 1.0 이상이면 entropy 가 base 보다 큰 기여 → F1 확정
```
이미 `meta` 에 conc_pen, entropy 가 있으므로 추출 가능.

---

## 5. 권장 조치 (Before Phase 2)

### 5.1 Must Fix (Phase 2 gate)
| ID | 수정 내용 | 예상 공수 |
|---|---|---|
| **F11** | `_calc_spread` 에 tradable 필터 추가 | 30분 |
| **S1** | retrain 시 `enable_cs_rank_features=True` 강제 (default 변경 or 문서화) | 10분 + retrain 영향 검토 |

### 5.2 Should Fix (Phase 2 gate, 강력 권장)
| ID | 수정 내용 | 예상 공수 |
|---|---|---|
| **F1** | `entropy_bonus` default 0.08 → 0.02 로 조정 or hard-constraint 로 전환 | 1시간 (meta-search candidates 동반 조정) |
| **F4** | Entropy/conc 를 per-regime 평균으로 재정의 | 2시간 |

### 5.3 Document Only (Phase 2 무영향)
| ID | 조치 |
|---|---|
| F2 | `meta["base_agg_mode"]` 추가 |
| F3 | OK as-is (F1 해결 시 자연 해소) |
| F5 | F4 해결 시 함께 처리 |
| F6 | T2 설계 시 재검토 |
| F7 | F1 해결 시 cap 추가 고려 |
| F8 | F1 해결 시 ramp 로 전환 고려 |
| F9, F10, F12, F13 | 문서만 |
| S2, S3, S4, S5 | OK |

### 5.4 진단 Helper 확장 (권고)
`meta` 에 fit 분해 항을 전부 기록하여 향후 자동 regression detect:
```python
meta["fit_decomposition"] = {
    "base": base,
    "entropy_contrib": cfg.entropy_bonus * entropy,
    "conc_pen": -conc_pen,
    ...
}
assert abs(sum(decomp.values()) - fit) < 1e-12
```

---

## 6. Phase 2 과의 상호작용

### Phase 2 (T5 walk-forward CV) 가 도입하는 것
- 한 signal 을 여러 time-fold 에 대해 반복 평가 → `mean_fit`, `std_fit`
- New penalty term: `w_fold_std × std(fit_folds)`

### 본 audit 의 findings 가 Phase 2 에 미치는 영향
- **F11**: fold 별 Spread universe 불일치가 증폭 (각 fold 의 illiquid 편향이 다름) → **Phase 2 직전 수정 필수**
- **F1, F4**: 각 fold 의 base 가 더 작아져 (짧은 기간) entropy/conc_pen 지배 효과 심화 → **Phase 2 직전 조정 강력 권장**
- **S1**: Tech feature 스케일이 fold 별로 다르게 작용 (universe 변화) → 사전 정규화 필요
- F2, F6, F7, F8: Phase 2 에서도 문제가 되지만 구조적이므로 추후 T2/T6 에서 해결

### 수정 후 재실험
F11 + F1 + F4 + S1 수정 후 **live V2 signal 재평가** → fit 값이 얼마나 변하는지 측정. 큰 변화가 있다면 V2 의 품질 평가를 근본적으로 재고해야 함.

---

## 7. 미해결 질문 (사용자 승인 필요)

| Q# | 질문 | 권장 안 |
|---|---|---|
| Q1 | `entropy_bonus` 0.08 → 0.02 로 낮출까, hard-constraint 로 전환? | **낮추기 (단계적)** |
| Q2 | F6 BULL-only 편향: 모두 OFF 후 baseline 재관찰? | **OFF 후 재관찰** (T2 에서 재설계) |
| Q3 | S1 `enable_cs_rank_features`: default True 로 변경? | **True 로 변경** (retrain 영향 수용) |
| Q4 | F4 per-regime entropy: 즉시 구현? | **즉시 구현** (F1 과 세트) |
| Q5 | F11 수정 방식: `_calc_spread(tradable=...)` 인자 추가 vs 호출 전 mask? | **호출 전 mask** (깔끔) |

---

## 8. Appendix

### 8.1 분석 대상 함수 line 참조 (current notebook)

| Function | Line |
|---|---|
| `Config` dataclass | 110 |
| `_rank_to_01` | 2560 |
| `_cs_zscore_preserve_nan` | 2797 |
| `build_precompute_panel` | 3110 |
| `_precompute_rolling_factor_ic` | 3759 |
| `_spearman_corr` | 3750 |
| `_calc_spread` | 3804 |
| `_scoring_regime_weight_map` | 4010 |
| `compute_selected_factor_corr_penalty` | 4155 |
| `compute_bull_floor_penalty` | 4195 |
| `compute_bull_spread_bonus` | 4228 |
| `_get_feat_valid` | 4347 |
| `_selected_feature_recent_completeness_mask` | 4364 |
| `_diversity_terms` | 4762 |
| `_get_alpha_floor` | 5103 |
| `get_regime_active_weight_vector` | 5142 |
| `compute_side_soft_bias` | 5164 |
| `compute_bull_breadth_soft_bias` | 5207 |
| `compute_bull_breakout_presence_bonus` | 5239 |
| `score_vector_for_day` | 5268 |
| `_compute_bull_floor_penalty_np` | 5348 |
| `_compute_bull_spread_bonus_np` | 5369 |
| `evaluate_individual_qresearch` | 5378 |
| `_rebals_per_year` (T1 NEW) | near 5378 |
| `_compute_deployment_penalties` (T1 NEW) | near 5378 |

### 8.2 관련 이전 문서
- `phase12_signal_health_report.md` — V1/V2 signal 건강 진단
- `t1_t5_design.md` — T1/T5 통합 설계

### 8.3 Phase 1 변경 이력
Phase 1 (T1 deployment penalty) 은 이 audit 결과를 **반영하지 않은 상태에서** 이미 완료됨. Phase 2 착수 전 **F11, F1, F4, S1 만 먼저 수정** 후 Phase 2 로 진행.
