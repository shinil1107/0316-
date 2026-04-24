# Step B — Pre-Phase 2 Patch Sanity Check

**Status:** COMPLETED — 2026-03-28
**Script:** `phase3/tests/step_b_post_patch_sanity.py`
**Log:** `/tmp/step_b_run.log`

## 목적

Pre-Phase 2 audit 에서 적용한 4건의 패치 (**F11, F1, F4, S1**) 가

1. `evaluate_individual_qresearch` 를 더 이상 망가뜨리지 않는지 (end-to-end smoke)
2. 각 패치가 **수치적으로** 의도한 효과를 내는지

를 기존 live V2 signal (`V2_GOLDEN_ENS_L3_v1`) 한 종으로 확인. 이는 Phase 5 retrain (Step C) 를 착수해도 되는 게이트 역할을 함.

---

## 실험 설정

| 항목 | 값 |
|---|---|
| Pack | `precompute_qresearch_v4_12_2017-01-03_2026-04-17.npz` (D=2335, N=998, K=36) |
| Signal | `V2_GOLDEN_ENS_L3_v1` (k_active=15) |
| Regime override | `SIDE` on all dates (fitness decomposition 가독성 우선) |
| Mode | `lightweight=True` |

---

## 결과

### Fitness 분해 (post-patch)

```
fit                 = -0.031597
base (IC+Spread)    = +0.004132
  regime_ic1        = +0.003113  (w=0.4)
  regime_ic3        = +0.006903  (w=0.4)
  regime_spmix      = +0.000628  (w=0.2)
entropy             = 0.478898   (per-regime weighted, F4)
entropy_contrib     = 0.04 × 0.4789 = +0.019156
conc_pen            = +0.040198   (per-regime weighted, F4)
corr_pen            = +0.024687
k_pen / bull_*      = 0
```

### 패치별 검증

| 패치 | 기대 효과 | 측정치 | 판정 |
|---|---|---|---|
| **F1** (entropy_bonus 0.08 → 0.04) | \|entropy_contrib\| / \|base\| ≈ 5× (was ≈ 10×) | **4.64×** | PASS |
| **F4** (per-regime entropy/conc_pen) | entropy 값이 old w_avg 경로와 다름 | Δ = **-0.408** (0.886 → 0.479) | ACTIVE |
| **F11** (tradable mask on Spread) | Spread 변화로 V2 의 non-tradable 의존도 측정 | Δ = **+32.6 %** (0.0125 → 0.0165) | PASS (quality-up) |
| **S1** (`enable_cs_rank_features=True` default) | 기본값 반영 | 확인 완료 | PASS |

### 해석

- **F1 PASS**: entropy bonus 기여분이 base alpha 대비 **10× → 4.6×** 로 감소. GA 가 "factor spread" 보다 "real alpha" 를 따라가도록 기울기가 완화됨. 다음 retrain 에서 meta-search (0.02 ~ 0.05) 가 더 낮은 값을 선택할 여지가 열림.
- **F4 ACTIVE**: V2 는 세 regime 이 동일한 mask/weights 를 공유하므로 per-regime blended entropy 가 old w_avg 와 완전히 동일할 수 있는데, 실제로는 `sel_union` 기반 k_eff 가 바뀌면서 `_diversity_terms` 출력이 달라져 Δ = -0.408 이 관측됨. T2 (regime-specialized) 이후에는 이 차이가 더욱 커질 것으로 기대.
- **F11 PASS (quality-up)**: `_calc_spread` 에 `tradable` 마스크를 적용하면 Spread 가 **+32.6 % 상승**. 이는 기존 V2 가 non-tradable (illiquid) 종목에 의해 **알파가 희석되고 있었음** 을 뜻한다. F11 이후 GA 는 non-tradable 표본을 처음부터 제외하고 학습하므로, capacity-aware 하고 더 깨끗한 signal 이 선택될 것으로 기대. (원 audit 가 지적한 "IC ↔ Spread universe 불일치" 문제 해소.)
- **S1 CONFIRMED**: `Config().enable_cs_rank_features = True` 가 기본값으로 반영되어 있음 (코드 레벨). 기존 precompute pack 은 `False` 로 빌드되어 있으므로 S1 효과는 Phase 5 retrain 시 새 pack 을 빌드할 때 나타남. 현 단계에서는 **Config default 반영 확인** 까지만 PASS.

---

## Phase 5 retrain 착수 가능 판단

| 점검 항목 | 결과 |
|---|---|
| `evaluate_individual_qresearch` end-to-end 무오류 | PASS |
| `test_t1_phase1_backward_compat.py` (T1 Phase 1) 재실행 | PASS (absolute fit 이동은 예상된 변경) |
| 4건 patch 각각 수치 검증 | PASS / ACTIVE / PASS / CONFIRMED |

**결론: Phase 5 retrain 착수 조건 충족.** 다음 단계는 패치된 공식으로 GA 를 재훈련하고 Step A 의 고정 OOS 벤치마크 (`baseline_benchmark.md`) 대비 Step C 게이트 4/6 조건을 평가.
