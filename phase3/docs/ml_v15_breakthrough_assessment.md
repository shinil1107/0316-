# ML-1.5 vs V2 — Breakthrough Assessment (2026-05-07)

## TL;DR

**ML-1.5 (XGBRanker + regime feat + cs-rank post-proc)**: 단독 CAGR로는 V2의 56% 수준으로
여전히 V2를 단독으로 못 이김. **그러나 안정성(CV)은 V2를 능가**하고, F0a 학습이 가능해진
점에서 *큰 진전*. ML-2 ensemble 또는 ML-1.6 tuning으로 V2 돌파 시도 가능.

| | Baseline_V2 | ML_v1 | ML_v15 | v15 - v1 |
|---|---|---|---|---|
| Mean CAGR | **+31.1%** | +15.6% | +17.5% | +1.9pp |
| CV (stability) | 0.464 | 0.800 | **0.426** ⭐ | **V2보다 우수** |
| Worst fold | +12.1% | +0.0% | +7.7% | +7.7pp |
| pos / n | 6/6 | 5/6 | **6/6** ⭐ | F0a 학습 성공 |
| Hardgate | ALL | FAIL (4 hard) | FAIL (2 hard) | A,D 통과 |

## ML-1.5의 3가지 개선 + 그 효과

### 1. `rank:pairwise` (XGBRanker) 도입
- val_NDCG@32 = 0.148 ~ 0.201 — 모든 fold에서 정상 학습
- best_iter 44~302 (v1는 0~47, F3=0이었던 문제 해소)
- raw 예측 분포 spread: **[-2.30, 1.16]** (v1는 [0.484, 0.583]) → 후처리 후 [0.000, 1.000] 풀 spread

### 2. Regime one-hot feature (39-dim)
- Top-25 importance에 **RG_DEFENSIVE (#3), RG_SIDE (#12), RG_BULL (#19)** 모두 진입
- ranker가 regime 정보를 적극 활용 → V2의 regime-conditional 로직 부분 모방

### 3. `min_train_dis=200`
- F0a fold 학습 가능 → ML_v15 F0a CAGR **+13.5%** (v1 +0.0%)
- pos/n 6/6 달성

## Per-fold 상세

| Fold | V2 | ML_v1 | ML_v15 | v15-V2 | 분석 |
|---|---|---|---|---|---|
| F0a (2012-14) | +24.6% | +0.0% | **+13.5%** | -11.1pp | F0a 학습 성공이 가장 큰 v15 개선 |
| F0b (2015-16) | +12.1% | +11.3% | **+11.4%** | -0.7pp | **거의 동등** ⭐ |
| F1 (2019-20) | +45.2% | +27.4% | +29.6% | -15.6pp | COVID 변동성 — V2의 lookahead가 결정적 |
| F2 (2021-22) | +14.8% | +1.1% | **+7.7%** | -7.1pp | v1 대비 +6.6pp 개선 (regime feat 효과) |
| F3 (2023-24) | +43.9% | +32.6% | +23.4% | -20.5pp | v1보다 후퇴 — rank binning이 magnitude 정보 손실 의심 |
| F4 (2024-26) | +45.9% | +20.9% | +19.4% | -26.6pp | post-OOS, V2의 lookahead가 가장 강한 fold |

### v15가 v1보다 좋아진 fold: F0a, F0b, F1, F2 (4/6)
### v15가 v1보다 나빠진 fold: F3, F4 (2/6)

→ rank post-process가 magnitude 큰 alpha 신호를 평탄화하는 부작용. ML-1.6에서 검증 필요.

## Hardgate 상세

| Gate | V2 | ML_v1 | ML_v15 | 비고 |
|---|---|---|---|---|
| G-A (CV stability) | ✓ | ✗ | **✓** | v15: CV 0.426 < V2 0.464 (V2보다 안정) |
| G-B (CAGR ≥ 90%) | ✓ | ✗ | ✗ | 17.5/31.1 = 56% — 여전히 큰 gap |
| G-C (worst ≥ V2-1pp) | ✓ | ✗ | ✗ | 7.7% < 12.1%-1 = 11.1% |
| G-D (pos count) | ✓ | ✗ | **✓** | F0a 학습 가능해짐 |
| G-E (MDD) | ✓ | ✓ | ✓ | |
| G-F (Sharpe ≥ 90%) | ✓ | ✗ | ✗ | |
| G-G (OOS std) | ✓ | ✓ | ✓ | |

**4 hard 중 2 통과** — v1의 0/4에서 큰 진전.

## V2 baseline 학습 구간 컨텍스트 (재평가)

V2 baseline (`P2_BATCH11`)는 2026-04-06 GA run으로 생성:
- **GA panel**: `2017-02-21 → 2026-03-02` (9년)
- **F1, F2, F3, F4 평가 구간이 모두 V2 학습 구간 안에 포함됨** ← *lookahead bias*
- ML은 strict walk-forward로 leakage-safe (각 fold마다 eval 시작 21일 전까지만 학습)

**진짜 apple-to-apple 비교**는 V2도 walk-forward로 재학습해야 가능.
**현재 -13.6pp gap** 중 일부는 V2의 lookahead advantage (정량 추정 불가).

## Top features (gain) — V2 의 전조 신호?

| Rank | Feature | Note |
|---|---|---|
| 1 | BREAKOUT_252 | V2의 핵심 (P2_BATCH11 활성) |
| 2 | ATR_LOW | 변동성 floor |
| 3 | **RG_DEFENSIVE** | regime feat 적극 활용 |
| 4 | VAL_BOOK2PRICE | value |
| 5 | SMA_CROSS | trend |
| 6 | QUAL_ROE | quality |
| 7 | VAL_EARN_YIELD | value |
| ... | ... | ... |
| 12 | RG_SIDE | regime feat |
| 19 | RG_BULL | regime feat |

ML이 발견한 importance 순위는 V2의 anchor와 매우 유사 → ML이 *옳은 신호*를 학습 중.

## V2 돌파 가능 경로 — 의사결정

### 옵션 A: ML-2 Ensemble (즉시, 30분 내) ⭐ 권장
0.5 × V2_GOLDEN_ENS_L3 + 0.5 × ML_v15
**근거**:
- ML_v15의 CV(0.426) < V2 CV(0.464) → 두 시그널의 **상관성이 낮을 가능성** (ensemble 효과 기대)
- ML이 V2와 다른 fold에서 상대적으로 잘 함 (F2: V2 0.66 Sharpe vs ML 0.41 → ML이 안정성 보강)
- ensemble로 risk-adjusted CAGR 개선 시도

### 옵션 B: ML-1.6 Hyperparameter Tuning (1~2시간)
- max_depth 6→8, learning_rate 0.05→0.03
- RANK_RELEVANCE_BINS 32→64 (rank 정밀도 향상)
- 또는 **rank post-process 제거** — XGBRanker raw output을 그대로 사용
- F3, F4 magnitude 정보 손실 회복 시도

### 옵션 C: ML-1.7 — LightGBM 비교
- GBDT family 다른 구현체 (LightGBM `lambdarank`)
- 같은 input으로 model variance 측정 — XGBoost가 sub-optimal한지 확인

### 옵션 D: ML-3 — GA + ML score blend (GA를 meta-optimizer로)
- 기존 GA로 (V2 score, ML score) 위에서 blend 가중치 + regime별 가중치 최적화
- 가장 큰 효과 가능성, 가장 큰 개발 비용

## 권장 진행 순서

1. **즉시 (옵션 A)**: ML-2 ensemble 0.5/0.5 → step_d로 즉시 검증
   - 결과 V2 초과 → **돌파 성공! ML-3 진행**
   - 결과 V2 미달 → ML-1.6으로 진행
2. **다음 (옵션 B)**: ML-1.6 hyperparameter sweep — 단독 ML 성능 향상 시도
3. **마지막 (옵션 D)**: ML-3 GA-meta optimizer — 가장 강력한 잠재력

## Live 격리 재확인

- ✓ artefact: `phase3/ml/artifacts/frozen_signal_ML_v15_*.npz`
- ✓ load 인식: `signal_type='ml_external_scores'`
- ✓ run_daily 차단: ML 시그널 발견 시 RuntimeError
- 본 검증 100% offline — 모의/실투자 영향 없음
