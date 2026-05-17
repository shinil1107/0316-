# ML-1 vs V2 Baseline — Breakthrough Assessment (2026-05-07)

## TL;DR

**ML_XGB_v1 baseline은 V2 baseline 대비 절반 수준의 성능** (mean CAGR 15.9% vs 31.1%).
하지만 이는 **3가지 명확한 구조적 한계** 때문이며, 모두 ML-1.5에서 즉시 개선 가능함.
**돌파 가능성은 충분히 있음** — 다만 ML-1 baseline 그대로는 절대 V2를 못 이김.

| | Baseline_V2 | ML_XGB_v1 | gap |
|---|---|---|---|
| Mean CAGR (6 folds) | **+31.1%** | +15.9% | -15.2pp |
| CV (stability) | 0.464 | 0.794 | +0.33 (worse) |
| pos / n | 6/6 | 5/6 | F0a 학습 실패 |
| Hardgate | **ALL pass** | **FAIL (4 hard fails)** | A,B,C,D 모두 |
| Post-OOS F4 (2024-06~) | +45.92% | +23.76% | -22.2pp |

## Per-fold detail

| Fold | Group | V2 | ML | gap | 비고 |
|---|---|---|---|---|---|
| F0a | pre_oos | +24.6% | **+0.0%** | -24.6pp | ML 학습 데이터 부족 (231 dis < 250 min) → 패널 비어있음, 매수 불가 |
| F0b | pre_oos | +12.1% | +11.3% | -0.8pp | **거의 동등** ⭐ |
| F1  | in_sample | +45.2% | +27.4% | -17.8pp | COVID 변동성 |
| F2  | in_sample | +14.8% | +0.8%  | -14.0pp | 2022 약세장 |
| F3  | in_sample | +43.9% | +32.0% | -11.9pp | best_iter=0 (학습 안 됨) |
| F4  | post_oos | +45.9% | +23.8% | -22.2pp | post-pack 외삽 |

## 진단 — 왜 못 이겼는가?

### 1. **Squared-error loss의 평균회귀 문제** (치명적)

XGBoost를 `objective="reg:squarederror"`로 학습 → unit-uniform [0,1] rank target에 대해
모델이 평균 0.5에 수렴하는 conditional mean을 학습함.

**증거**:
- 예측 분포: **[0.484, 0.583]** (mean=0.509, std≈0.01)
- val_RMSE: 0.262 ~ 0.267 ← Constant 0.5 predictor의 RMSE = sqrt(1/12) = **0.289**
- 즉 모델은 constant predictor 대비 **2~10% 개선**에 그침

**의미**:
- top-K 종목 선택 시 score 차이가 거의 없어 사실상 random selection에 가까움
- IC가 NaN으로 뜨는 것도 같은 원인 (분산이 너무 작음)

**해결 (ML-1.5)**:
- `objective="rank:pairwise"` (XGBRanker) — pair별 ordering loss로 IC 직접 최적화
- 또는 prediction을 cross-sectional rank로 post-process하여 spread 복원

### 2. **Regime feature 누락**

현재 `feature_matrix_for_dates`는 `regime_idx`를 메타데이터로만 보관, X에 포함 안 됨.
→ **모든 regime을 동일하게 취급**, V2의 regime-conditional 로직을 모방 못함.

**의미**:
- V2는 BULL/SIDE/DEFENSIVE에 따라 다른 weight vector → ML은 단일 model
- F2 (2022 SIDE-heavy)에서 ML이 +0.8%로 처참 — V2의 +14.8% 대비 -14pp

**해결 (ML-1.5)**:
- regime one-hot 3-dim을 X에 concat (총 39 feature)
- 또는 regime별 별도 model (3 trees) 학습

### 3. **F0a fold 학습 데이터 부족**

`min_train_dis=250` 가드로 인해 F0a (eval 시작 2012-01-01) skip
→ ML 패널의 F0a 영역이 비어있어 sim에서 score=NaN → 매수 0건 → CAGR 0%

**해결**:
- `train_history_start`를 더 이전으로 (pack은 2011-01-03부터지만 처음 21일은 embargo로 사용 불가)
- 또는 F0a를 평가에서 제외 (보고서에서만 명시)
- 또는 `min_train_dis=200`로 완화

## V2 돌파 가능성 평가

### 긍정 신호

1. **F0b에서 V2와 거의 동등 (+11.3% vs +12.1%)** — 모델이 학습이 잘 된 fold에서는 V2를 따라잡을 잠재력 있음
2. **F4 (가장 미래 시점)에서 +23.8% CAGR 달성** — squared-error 한계에도 양의 수익. rank loss + regime feature면 V2의 +45.9%에 근접 가능
3. **Top features (gain)**: `DIST_FROM_SMA50`, `VAL_BOOK2PRICE`, `ROE×BREAKOUT_126`, `ADX`, `FCF_YIELD` — V2의 핵심 indicators와 일치 → ML이 정확한 signal을 *발견*은 함, *활용*을 못함
4. **학습 시간**: 6 folds 24초 — 빠른 iteration 가능 (vs GA 16시간)

### 부정 신호 / 리스크

1. **F3에서 best_iter=0** — 어떤 학습으로도 val loss 개선 못함. 추가 진단 필요
2. **OOS_IC가 NaN** — exit_pos가 작동 못함 (별개 버그 의심)
3. **현재 36 feature 중 16개 cross-product feature** → 트리 모델에 redundant 가능. 1차 feature만 쓰는 것도 검토

## 권장 진행 경로

### Phase ML-1.5 (즉시, 1~2시간 작업) — V2 돌파 가능성 1차 검증

1. **`rank:pairwise` loss로 교체**
   - XGBoost의 `XGBRanker` 사용, group 단위 = 1 day의 cross-section
   - prediction이 unbounded score → cross-sectional rank로 변환 후 panel 저장
2. **Regime one-hot feature 추가**
   - X에 3-dim 추가, total 39 feature
3. **`min_train_dis=200` + F0a 라벨링**
   - F0a fold도 평가 가능하게

→ Re-run step_d → ML vs V2 재비교

### 의사결정 분기

- **ML-1.5 결과가 V2의 80%+ (mean CAGR ≥ 25%)**: ML-2 (GA + ML 0.5/0.5 ensemble) 진행
- **ML-1.5 결과가 V2의 50~80%**: ML-1.6에서 hyperparameter tuning + LightGBM 비교
- **ML-1.5 결과가 V2의 50% 미만**: 입력 feature pipeline 자체 검토 (feat panel quality, regime classifier)

### Phase ML-2 (선택)
0.5 × GA-V2 + 0.5 × ML score → 두 직교적 signal의 ensemble. ML이 V2에 ≥80%면 ensemble로 V2 단독 대비 +α 가능성.

### Phase ML-3 (선택)
GA를 meta-optimizer로 — ML score 위에서 blend α, regime별 ML weight를 GA로 최적화.

## Live 격리 상태 확인

ML 시그널은 다음 3중 가드로 격리됨:
- ✓ artefact 위치: `phase3/ml/artifacts/` (live cache 분리)
- ✓ `load_frozen_signal`: `signal_type='ml_external_scores'` 인식
- ✓ `run_daily`: ML 시그널 발견 시 `RuntimeError` (live 진입 차단)

**결론**: 이 검증은 100% offline. live 모의/실투자 영향 0.
