# OOS 용어 혼용 문제 정리 — 왜 V2 baseline이 OOS 검증된 줄로 잘못 알고있었나

**작성일**: 2026-05-07  
**대상**: `frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz` (현재 live)  
**결론**: 현재 baseline의 "Post-OOS F4" 라벨은 **시그널 자체에 대한 OOS가 아님**. Strategy 파라미터 OOS와 Signal OOS가 코드베이스에서 동일한 단어로 호명되고 있어 혼동 발생.

---

## 1. 두 종류의 "OOS"

| 종류 | 정의 | V2에서 cutoff |
|---|---|---|
| **Strategy-OOS** | 포트폴리오 strategy 파라미터 (top_n, regime cash %, exit triggers, etc.) 가 보지 않은 구간 | 2024-06-01 onwards |
| **Signal-OOS** | GA 시그널 자체가 학습 데이터에 포함하지 않은 구간 | **2026-03-02 onwards** (P2_BATCH11 기준) |

두 개념은 명확히 다르며, 둘 중 하나만 OOS여도 다른 하나는 In-sample일 수 있음.

---

## 2. P2_BATCH11 (V2의 dominant component) 의 실제 학습 구간

```python
# 0315 windows이사.ipynb cell ~12270, archive_experiment_configs.py L2126 참조
PHASE2_BATCH11_BASE_CFG: {
    "start_panel_date": datetime(2017, 2, 21),
    "end_date":         datetime(2026, 3, 2),    # ← 학습 데이터의 마지막 일자
    ...
}
```

→ **2017-02-21 ~ 2026-03-02 (~9년)** 전 구간이 GA의 in-sample 학습 panel.  
→ Walk-forward의 모든 fold (F0a, F0b, F1, F2, F3, **F4**) 가 이 구간 안에 있음.  
→ 즉 P2_BATCH11 시그널 입장에서 **F4 (2024-06-01 ~ 2026-02-27) 도 in-sample**.

---

## 3. 코드베이스의 잘못된 OOS 라벨링 (혼동 원인)

다음 위치에서 "OOS" = "Strategy-OOS" 의미로 사용되었으나, 라벨만 보면 Signal-OOS로 오해할 수 있음:

| 위치 | 코드/문구 | 실제 의미 |
|---|---|---|
| `phase3/tests/step_c_gate_evaluation.py:60` | `OOS_START = "2024-06-01"` | Strategy-OOS만 |
| `phase3/tests/step_a_baseline_benchmark.py` | "OOS_END uses pack's end" | Strategy-OOS만 |
| `phase3/launcher.py:2192` | "OOS holdout: 2024-06-01 → pack end (Step C)" | Strategy-OOS만 |
| `phase3/docs/hardgate_default_*.md` | "Post-OOS (F4) matches the Step C window" | Strategy-OOS만 (P2_BATCH11 기준 실제로는 in-sample) |
| `phase3/docs/t5_walk_forward_results_*.md` | "Post-OOS (F4)" | Strategy-OOS만 |
| `step_d_walk_forward.py` fold definition | F4 group="post_oos" | Strategy-OOS만 |

---

## 4. 인지 시점

- 2026-04-23: `run_phase5_retrain.py` 작성 시 "OOS-safe cutoff" 명시 (TRAIN_END = 2024-05-31). 그러나 P5_RETRAIN은 별도 retrain 스택. P2_BATCH11/V2 baseline 자체에는 적용 X.
- 2026-05-05: `phase_a_comprehensive_analysis_20260505.md` — "Survivorship bias" 언급 (in-sample 인식이 부분적이지만 명시되지 않음).
- 2026-05-07: `direction_a_l3_blending_analysis_20260507.md` line 310 — "Generate new signals with a true OOS validation window (post-2026-03-31)". 인지는 했지만 V2 자체에 적용 안 됨.
- 2026-05-07: 사용자 명시적 지적 — *"v2 baseline의 거의 대부분의 성능을 차지하는 p2_batch11 의 학습구간이 26년 3월까지라 사실상 post_oos 구간이 없는 수준이다"*.

---

## 5. 영향도 평가

### V2 baseline의 walk-forward 결과 재해석

기존 결과 (`t5_walk_forward_results_20260507_*.json`):
- F0a (pre-OOS, 2012-2014): in-sample(P2_BATCH11) — 평가 시점 universe와 다름
- F0b (pre-OOS, 2015-2016): in-sample(P2_BATCH11)
- F1, F2, F3 (in-sample): 명시적 in-sample
- **F4 (post-OOS, 2024-06~2026-02): 실제로는 in-sample (P2_BATCH11 학습 구간 내)**

→ V2 baseline의 F4 CAGR +45.92% 는 **시그널 입장에서 in-sample fit이 일부 반영된 수치**일 수 있음.  
→ ML_v15 의 F4 CAGR +18.50% 와 **공정한 비교가 아님** (ML은 fold별 strict OOS).

### Live performance와의 관계

- 2026-03-02 이후 live 데이터는 **시그널 입장에서도 진짜 OOS**.
- 그러나 live 기간이 약 2개월에 불과하여 통계적 유의성 부족.
- 현재 live (paper / real) 의 좋은 performance는 진짜 OOS 검증된 신호.

---

## 6. 시정 조치

### 6.1 Quick fix: terminology 명시화

코드/문서에서 "OOS" → 다음 2종 라벨로 분리:
- `strategy_oos` (Strategy 파라미터의 OOS)
- `signal_oos` (Signal GA 학습의 OOS)

### 6.2 Signal-OOS 검증 retrain (현재 진행 중)

`phase3/run_p2_batch11_oos.py` 작성 — P2_BATCH11 동일 config + `end_date=2024-05-31`:
- Train window: 2017-02-21 ~ 2024-05-31
- F4 (2024-06-01 ~ 2026-02-27) → **진짜 signal-OOS** 가 됨
- 동일 ga_pop=400, ga_gen=12, stability 8 seeds, meta search ON, original BULL pools 그대로
- 출력: `frozen_signal_P2_BATCH11_OOS_<stamp>.npz`
- 비교: F4에서 P2_BATCH11_OOS vs ML_v15 (apple-to-apple post-OOS)

### 6.3 V2 ENS_L3 ecosystem retrain (옵션)

P2_BATCH11_OOS만으로는 V2 ensemble 전체 재현 X. 만약 P2 alone과 V2 ensemble의 F4 gap이 커지면 BULL_GA_V2 + E2E 도 동일 cut으로 재학습 필요.

### 6.4 Walk-forward fold semantics 보강

`step_d_walk_forward.py` fold 정의에 다음 메타데이터 추가 권장:
```python
{
    "id": "F4",
    "group": "post_oos",
    "strategy_oos": True,   # Strategy 파라미터 OOS
    "signal_oos_min_train_end": "2024-05-31",  # 이보다 학습 길면 in-sample
}
```

신호별 학습 cut과 비교해 자동으로 `(strategy_oos, signal_oos)` 두 라벨을 출력.

---

## 7. 향후 ML 비교 protocol

향후 모든 baseline-vs-ML 비교는 **양쪽 모두 동일한 cut으로 학습**해야 공정:

| 시그널 | 권장 cut |
|---|---|
| ML_v15 | F0a 학습은 di < di_F0a − embargo; F1 학습은 di < di_F1 − embargo; ... (현재 그렇게 구현됨) |
| P2_BATCH11_OOS | end_date = 2024-05-31 (F4 비교 전용) |
| V2 ENS_L3_OOS (옵션) | P2 + BULL + E2E 모두 동일 cut으로 재학습 후 동일 L3 가중 적용 |

이렇게 해야 ML의 강점/약점이 fair-fight에서 드러남.
