# T1 Deployment-Tuning Retrain — Plan (post-P5_RETRAIN)

**상태**: APPROVED (2026-04-23) — 착수 대기
**선행 문서**:
- `t1_t5_design.md` (원 설계안, Phase 1 코드 hook 완료)
- `phase12_ga_audit.md` (F1/F4/F11/S1 patch 근거)
- `phase5_retrain_plan.md` (Pre-Phase 2 patch 적용한 P5_RETRAIN 계획)
- `baseline_benchmark.md` (고정 OOS baseline)
- `step_c_P5_RETRAIN_20260423_161158.json` (P5_RETRAIN Step C 결과, REJECT)

---

## 0. 왜 이 retrain 인가

P5_RETRAIN (T1 OFF, BULL bias 대부분 OFF) 은 Step C 에서 REJECT:
- Pass: 1/5 (MDD 만 통과)
- Mandatory gate #5 Commission (≤ 0.56%) 실패: 실측 0.79%
- 주 원인 분석:
  1. **BULL bias OFF 로 인한 BULL regime 언더퍼폼** (OOS 의 57% 가 BULL, CAGR −25.8 pp 격차 주요 원인)
  2. **T1 deployment penalty 가 fitness 에 포함되지 않았음** — GA 가 turnover/cost 를 전혀 인식 못함
  3. F11/F1/F4/S1 patch 덕분에 in-sample ↔ OOS 갭은 0.0001 까지 좁혀짐 (patch 효과 실증)

이번 retrain 은 **#1 을 원복 + #2 를 활성화** 하여 patch 효과를 유지하면서 baseline 수준 CAGR + Step C #5 gate 통과를 노린다.

---

## 1. 설계 목표 (3 가지, 우선순위 순)

1. **Step C #5 Commission gate (≤ 0.56%) 통과** — T1 deployment penalty 활성화 목적
2. **Step C #1 CAGR gate (≥ +56.33%) 에 근접** — BULL bias ON 복원으로 CAGR 손실 회수
3. **in-sample ↔ OOS 갭 유지** (P5_RETRAIN 의 0.0001 수준) — patch 효과 보존

부차 목표:
- MDD 개선 유지 (≤ 31.46%, 이왕이면 P5_RETRAIN 수준 ~27%)
- Commission 이 과도하게 낮아 CAGR 희생되지 않도록 T1 weight 균형 탐색

---

## 2. 변경 사항 (P5_RETRAIN 대비)

### 2.1 BULL bias: 전부 OFF → 전부 ON (default 복원)

P5_RETRAIN 에서 **부분적으로만** OFF 했던 게 사후 발견됐다 — 아래 4 개만 OFF, 나머지 2 개는 default True 그대로. 이번엔 **6 개 전부 ON (default)**.

| 플래그 | P5_RETRAIN | T1DEP (이번) | 비고 |
|---|:---:|:---:|---|
| `enable_bull_floor_penalty` | OFF | **ON** | hard floor |
| `enable_bull_spread_bonus` | OFF | **ON** | soft bonus |
| `enable_bull_factor_min_constraint` | OFF | **ON** | structural |
| `enable_side_soft_bias` | OFF | **ON** | soft bias |
| `enable_bull_breadth_soft_bias` | ON (실수) | ON | soft |
| `enable_bull_breakout_presence_bonus` | ON (실수) | ON | soft bonus |

**근거**:
- T1 penalty 효과를 isolate 하려면 다른 변경점을 최소화해야 한다.
- Baseline V2 가 BULL bias ON 에서 학습됐으므로 공정 비교를 위해 동일 조건.
- P5_RETRAIN 에서 BULL 보수화의 CAGR cost (−26 pp) 실측됨.
- T5 없이는 BULL bias 의 quality 를 객관 판정할 근거 없음.
- Q2 "T2 redesign baseline 관찰" 취지는 **T2 착수 시점에 T2 sub-signal 위에서** 재개.

### 2.2 T1 deployment penalty: OFF → ON (초기값 w=0.5/0.3)

Config 오버라이드 3 개를 이번에 최초로 활성화.

```python
enable_deployment_penalty = True
w_turnover                = 0.5      # t1_t5_design v2 권고 초기값
w_cost                    = 0.3      # t1_t5_design v2 권고 초기값
# (고정) deployment_top_n = 30,  deployment_cost_bps = 15.0
```

**초기값 근거 (t1_t5_design §3)**:
- `w_turnover=0.5`: GA 의 기존 penalty 규모 (conc_pen≈0.12, corr_pen≈0.03, risk_pen≈0.03) 대비 중간 강도. turnover × 0.5 ≈ 0.15 ~ 0.35 범위 예상.
- `w_cost=0.3`: turnover 와 일부 정보 중복이므로 낮게. cost_drag × 0.3 ≈ 0.007 ~ 0.015 범위.

이 초기값이 Step C 를 통과 못하면 다음 iteration 에서 grid (e.g., {0.3, 0.5, 0.7} × {0.2, 0.3, 0.4}) sweep 으로 확장.

### 2.3 GA budget +20% (population 위주)

| 파라미터 | P5_RETRAIN | T1DEP | Δ |
|---|---:|---:|---:|
| `stability_fast_population`   | 100 | **120** | +20% |
| `stability_fast_generations`  | 8   | 8       | (same) |
| `stability_refine_population` | 300 | **360** | +20% |
| `stability_refine_generations`| 12  | 12      | (same) |
| `ga_population`               | 300 | **360** | +20% |
| `ga_generations`              | 20  | 20      | (same) |

**근거**:
- 사용자 요청대로 budget +20%, population 위주.
- population 증가는 **다양성 확보 + 최적점 발견 확률 증가** (generations 증가는 수렴 속도에 더 큰 영향).
- P5_RETRAIN 이 refine 단계에서 gen=3 까지 still improving 이었던 점 감안, population 쪽 확장이 안전한 선택.

**예상 실행시간**: ~82 min × 1.20 ≈ **~98 min** (stability seeds 5 고정).

### 2.4 유지 사항 (명시적으로 변경 X)

- F11 patch (tradable mask on spread): 코드 고정, 유지
- F4 patch (per-regime entropy/conc_pen): 코드 고정, 유지
- F1 patch (entropy_bonus = 0.04): 유지
- S1 patch (enable_cs_rank_features = True): 유지
- Train window: 2017-02-21 → 2024-05-31 (OOS 홀드아웃 2024-06-01~)
- GA seed: 20260428 (deterministic)
- Meta-search: OFF (single template TPL_BALANCED)
- Stability seeds: 5
- engine `_SUBPROCESS_FAST_SEEDS = False` (macOS Tk 런처 fork 방지)
- Evaluation protocol (commission 10 bps, slippage 5 bps, capital $100K, daily buy $1K, daily rebalance, regime-blend OFF)

### 2.5 미변경 사항 (중요 — 의도적 보류)

- **regime-aware execution cost**: 보류 (regime-blind 유지)
  - 근거: T1 효과 isolation + 현 시점 cost_bps_r 근거 부족 + T2 시점 자연 통합.
  - signature 확장도 이번엔 **하지 않음** (불필요한 API churn 최소화).

- **T5 temporal CV**: 이번 retrain scope 아님.
  - T5 는 별도 Phase 로 계획 (t1_t5_design §4 Phase 2).

- **OOS holdout enforcement in GA**: 이번엔 prepare_inputs 수준에서 end_date=2024-05-31 로 잘라 간접 보장. GA 내부 `oos_holdout_cutoff` 는 사용 X.

---

## 3. 전체 Config overrides (최종)

```python
PHASE5_OVERRIDES = {
    # Window (OOS-safe)
    "start_panel_date": datetime(2017, 2, 21),
    "end_date":         datetime(2024, 5, 31),

    # Universe
    "enable_historical_universe":          True,
    "historical_universe_expand_tickers":  True,
    "enable_coverage_based_universe":      True,
    "enable_panel_cache_fallback_download": False,

    # GA fitness recipe
    "top_quantile":                0.12,
    "w_ic1":                       0.34,
    "w_ic3":                       0.34,
    "w_spread":                    0.32,
    "factor_corr_penalty_lambda":  0.10,
    "conc_penalty":                0.12,
    "weight_cap":                  0.40,
    "enable_fitness_risk_penalty": True,
    "fitness_downside_vol_lambda": 0.50,
    "fitness_max_neg_spread_ratio_lambda": 0.30,

    # ── T1 deployment penalty (NEW — activated) ──
    "enable_deployment_penalty": True,
    "w_turnover":                0.5,
    "w_cost":                    0.3,
    # deployment_top_n=30, deployment_cost_bps=15.0 은 default 유지

    # ── BULL biases ── (P5_RETRAIN 의 OFF overrides 제거 → default True 복원)
    # 명시적 재설정 없음; Config default 가 ON 이므로 인위적으로 끄지 않는다.

    # Meta OFF, stability ON (5 seeds, budget +20%)
    "enable_meta_search":           False,
    "meta_disabled_template_name":  "TPL_BALANCED",
    "enable_stability_layer":       True,
    "stability_seed_runs":          5,
    "stability_top_n_seeds":        4,
    "stability_fast_population":    120,   # +20%
    "stability_fast_generations":   8,
    "stability_refine_population":  360,   # +20%
    "stability_refine_generations": 12,

    # Final GA
    "ga_population":  360,                 # +20%
    "ga_generations": 20,

    # Reproducibility
    "use_random_seed": False,
    "ga_seed":         20260428,

    # Reports
    "excel_prefix": "SP500_P5_T1DEP",
}
```

---

## 4. Runbook

1. 사용자가 `phase3/launcher.py` 실행 → **T25 P5 Retrain (GA)** 버튼 클릭
   - Dry-run 체크 해제, Force rebuild pack 체크 해제, Auto-run Step C 체크 유지
2. ~98 min 대기 (stability × 5 → refine → final GA pop 360 × gen 20)
3. Step C 자동 chaining (auto-run 켜진 경우) → gate verdict 출력

생성 artifact:
- `/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_P5_RETRAIN_T1_<stamp>.npz`
- `phase3/docs/phase5_retrain_log_<stamp>.json`
- `phase3/docs/step_c_P5_RETRAIN_T1_<stamp>.json`
- `phase3/docs/phase5_step_c_results.jsonl` (누적 append)

---

## 5. Step C gate 기준 (baseline_benchmark.md v1.0 — 변경 없음)

| # | Criterion              | Gate                          | Mandatory |
|---|------------------------|-------------------------------|:---------:|
| 1 | OOS CAGR               | ≥ +56.33%                     |     |
| 2 | OOS MDD                | ≤ 34.61%                      | **Y** |
| 3 | OOS Calmar             | ≥ 1.881                       |     |
| 4 | OOS IC (3M)            | ≥ baseline_IC + 0.005 ≈ 0.032 |     |
| 5 | Commission %           | ≤ 0.56%                       | **Y** |
| 6 | Temporal stability T5  | deferred                      |     |

Pass rule: ≥ 4/6 (T5 deferred 상태에선 ≥ 3/5), 단 **#2 & #5 필수**.

---

## 6. Decision matrix (결과 해석)

| 시나리오 | 의미 | 다음 스텝 |
|---|---|---|
| **PROMOTE (≥4 pass + mandatory ok)** | T1 tuning 성공. live cutover 후보. | signal_transition_protocol.md 따라 swap |
| **CONDITIONAL PASS (3/5 pass + mandatory ok + CAGR 아슬하게 FAIL)** | w_turnover/w_cost 미세조정 후 1~2 회 iterate | weight grid sweep 착수 |
| **PARTIAL (mandatory ok + pass 1-2)** | T1 효과는 있으나 수익성 부족 | BULL bias 검토 + T5 조기 착수 |
| **MANDATORY FAIL (#5 Commission FAIL 유지)** | T1 weight 부족. w 상향 iterate. | w_turnover=0.7/w_cost=0.4 로 재실행 |
| **MANDATORY FAIL (#2 MDD FAIL)** | 예상 외 (P5_RETRAIN 이 MDD 개선했으므로) | 원인 진단 후 대응 |

---

## 7. 검증 체크리스트

구현 완료 후 retrain 착수 전에 다음 확인:

- [ ] `run_phase5_retrain.py` PHASE5_OVERRIDES 에 BULL-OFF 4 개 제거됨
- [ ] T1 deployment penalty 3 개 key 추가됨 (enable_deployment_penalty / w_turnover / w_cost)
- [ ] population 3 개 값 +20% 적용 (120 / 360 / 360)
- [ ] RUN_TAG 가 `P5_RETRAIN_T1` (artifact 충돌 방지)
- [ ] `launcher.py` T25 팝업의 plan 테이블이 최신 상태 (BULL ON, T1 ON 반영)
- [ ] `--dry-run` smoke 테스트 통과 (선택, 1-2 분)

---

## 8. 변경 이력

| 일자 | 버전 | 변경 |
|---|---|---|
| 2026-04-23 | v1 | 초안 (P5_RETRAIN Step C REJECT 분석 기반). BULL bias 전부 ON 복원 + T1 activate + budget +20%. |
| 2026-04-23 | v2 | **P5_RETRAIN_T1 (iter 1) Step C REJECT* 분석 기반 iter 2 = T1b 추가.** T1 weight 하향 (0.5→0.3, 0.3→0.2), budget 원복, Step C gate #5 완화 (baseline × 0.5 → × 0.7, baseline_benchmark.md v1.1). |

---

## 9. Iteration 2 (T1b) — 현재 진행

### 9.1 Iter 1 (T1) 결과 요약

| Metric | P5_RETRAIN_T1 | Gate (v1.0) | Pass |
|---|---:|---|:---:|
| CAGR | +24.88% | ≥ +56.33% | NO (−31.45 pp) |
| MDD | 21.24% | ≤ 34.61% | **YES** (mand) |
| Calmar | 1.171 | ≥ 1.881 | NO |
| IC_3M | 0.0349 | ≥ 0.03197 | **YES** (신규 통과) |
| Commission% | 0.72% | ≤ 0.56% | NO (mand) |

**해석**: T1 penalty (w=0.5/0.3) 이 GA 를 over-conservative 해로 몰아 CAGR 가 P5_RETRAIN(30.54%) 보다도 낮아짐. Turnover 는 2.14→2.05 로 4% 만 감소 — 즉 **turnover 압력 대비 return cost 가 비대칭적으로 컸음**. 다만 IC/MDD 는 유의미하게 개선돼 patched formula 는 제대로 작동 중.

### 9.2 Iter 2 변경점

| 항목 | T1 (iter 1) | **T1b (iter 2)** |
|---|:---:|:---:|
| `w_turnover` | 0.5 | **0.3** |
| `w_cost` | 0.3 | **0.2** |
| BULL biases | ON (default) | ON (default) — unchanged |
| `stability_fast_population` | 120 | **100** (reverted) |
| `stability_refine_population` | 360 | **300** (reverted) |
| `ga_population` | 360 | **300** (reverted) |
| RUN_TAG | P5_RETRAIN_T1 | **P5_RETRAIN_T1b** |
| excel_prefix | SP500_P5_T1DEP | SP500_P5_T1DEP_b |
| Gate #5 (v1.1) | baseline × 0.5 = 0.56% | **baseline × 0.7 = 0.78%** |

### 9.3 Iter 2 기대 결과 (hypothesis)

T1 → T1b 는 penalty 를 60% 수준으로 완화한 것:
- `w_turnover × turnover ≈ 0.3 × 2.1 = 0.63` (T1: 0.5 × 2.1 = 1.05)
- `w_cost × cost_drag` 도 67% 수준

**예상 Pareto 이동** (T1 기준):
- CAGR: +24.88% → **+28~32%** (P5_RETRAIN 근처로 회복)
- Commission%: 0.72% → **0.75~0.80%** (소폭 상승 예상)
- MDD: 21.24% → **22~25%** (약간 상승하나 baseline 대비 여전히 우수)
- IC_3M: 0.0349 → **0.030~0.035** (유지 또는 소폭 하락)

### 9.4 Iter 2 Pass 시나리오 예측 (gate v1.1 기준)

| # | Gate | 예상 cand | 통과? |
|---|---|---:|:---:|
| 1 | CAGR ≥ 56.33% | +28~32% | 여전히 NO (갭 존재) |
| 2 | MDD ≤ 34.61% | 22~25% | **YES** (mand) |
| 3 | Calmar ≥ 1.881 | 1.2~1.5 | 미달 |
| 4 | IC_3M ≥ 0.03197 | 0.030~0.035 | **borderline** |
| 5 | Commission ≤ 0.78% (v1.1) | 0.75~0.80% | **borderline** |

**Realistic best case**: 2 pass + mandatory OK (MDD + Commission 아슬 통과) → **HOLD*** verdict. PROMOTE 까지는 어려우나 iter 1 (REJECT) 에서 **1단계 상향** 가능성.

### 9.5 Iter 2 결과에 따른 분기

| 시나리오 | 다음 스텝 |
|---|---|
| **HOLD* (mand OK, 2-3 pass)** | T5 착수 — walk-forward 검증 후 최종 PROMOTE 판단 |
| **PROMOTE* (mand OK, 3+ pass)** | T5 를 PROMOTE 확증용으로 사용 (안전 장치) |
| **REJECT* (mand FAIL)** | T1 poor fit 으로 결론 — 방향 전환 (T2/T5 우선, T1 보류) |

### 9.6 Runbook (변경 없음)

`phase3/launcher.py` → T25 버튼 → 팝업 OK → Auto-run Step C 유지 → ~85분 대기.
Artifact: `frozen_signal_P5_RETRAIN_T1b_<stamp>.npz`
