# T5 Walk-Forward — Implementation Plan (Phase A)

**Status**: APPROVED (v2) — 2026-03-28
**Predecessor**: P5_RETRAIN_T1b Step C REJECT* (2026-04-23)
**Primary reference**: `baseline_benchmark.md` §5 gate #6 ("fold CAGR std ≤ mean × 0.5")

**v2 변경사항** (2026-03-28): Data-availability 체크 결과 2011 부터 OHLCV/marketcap_reconstructed/financial 모두 확보 확인 → **Pack 을 2011-01-03 → 2026-02-27 로 확장 재빌드**하여 fold 를 4 → **6 개**로 확장. 추가된 F0a (2012-14), F0b (2015-16) 는 **진짜 pre-train OOS** 구간.

---

## 0. 한 줄 요약

**고정된 signal 들을 과거 4 개 market slice 각각에서 Step C 와 동일한 simulator/strategy 로 돌려, 각 signal 의 "구간 간 안정성(CV)" 을 측정한다.** 목적: baseline V2 의 OOS CAGR +56% 가 (a) 체계적 우위인지, (b) BULL-heavy 2024-26 window 특유 운빨인지 판정.

---

## 1. 배경과 진단 연결

직전 단계 요약:
- P5_RETRAIN 3 회 iter (T1 OFF / T1 / T1b) 모두 **단일 1.87 년 OOS slice** 기준 CAGR 에서 baseline(+56.33%) 에 −24 ~ −31 pp 열세 → Step C REJECT
- 한편 in-sample IC_3M 은 baseline (−0.01143) 이 retrain (+0.004~+0.009) 보다 **음수, 더 나쁨**
- **이 두 사실이 양립하려면**: baseline 의 CAGR 우위는 **signal accuracy 가 아닌 다른 요소** (regime timing? BULL window 우연?) 에 크게 의존한다는 가설 → **multi-slice 재검증 필요**

T5 Phase A 는 이 가설을 직접 테스트한다.

---

## 2. 범위 (Phase A vs Phase B)

| 항목 | **Phase A (이번)** | Phase B (나중) |
|---|---|---|
| Signal 재학습 | **안 함** (기존 frozen signal 그대로) | fold 별 GA re-train |
| 측정 대상 | "signal × market regime" 안정성 | "GA training period × OOS" 일반화력 |
| 실행 비용 | 16 sim × ~1 min = 수 분 | 4 folds × 90 min = 6 시간/signal config |
| 결론 가능 | "이 signal 이 시기 운빨이었나?" | "이 GA formula 가 미래 data 에도 잘 학습될까?" |

**권고: Phase A 먼저, 결과에 따라 Phase B 필요 여부 판단.** 이번 계획서는 Phase A 만 다룬다.

**중요한 caveat (v2 개정)**: baseline V2 와 retrain signal 모두 **2017~2024-05 train 데이터로 학습됨**. 이를 기준으로 fold 를 3 종으로 분류:

| fold 그룹 | 기간 | 학습 데이터와 관계 | 해석 |
|---|---|---|---|
| **F0a, F0b** | 2012-01-01 → 2016-12-31 | **pre-train, 완전 OOS** | 신호가 전혀 본 적 없는 과거 데이터. 진짜 temporal generalization 측정. |
| F1, F2, F3 | 2019-01-01 → 2024-05-31 | in-sample | 학습 기간 내 regime slice. "in-sample regime audit". |
| F4 | 2024-06-01 → 2026-02-27 | post-train OOS | Step C 와 동일 (2024-26 BULL-heavy). |

F0a/F0b 추가로 **양쪽 꼬리**에 진짜 OOS 가 생긴다 → regime 편향 없는 generalization 평가 가능.

---

## 3. Fold 설계 (v2: 6-fold)

### 3.1 Fold 정의

| Fold | 기간 | 년수 | 대표 특징 | 2017-24-05 train 대비 |
|---|---|---:|---|:---:|
| **F0a** | 2012-01-01 → 2014-12-31 | 3.00 | 2012-13 QE 랠리 + 2014 에너지 crash 시작 | **pre-train OOS** |
| **F0b** | 2015-01-01 → 2016-12-31 | 2.00 | 2015-16 에너지 bear + 2016 election | **pre-train OOS** |
| F1 | 2019-01-01 → 2020-12-31 | 2.00 | 2019 랠리 + 2020 COVID crash & recovery | in-sample |
| F2 | 2021-01-01 → 2022-12-31 | 2.00 | 2021 meme bull + 2022 bear | in-sample |
| F3 | 2023-01-01 → 2024-05-31 | 1.41 | 2023 AI 랠리 회복 + 2024 전반 bull | in-sample |
| F4 | 2024-06-01 → 2026-02-27 | 1.74 | **Step C 와 동일 OOS (2024-26 bull heavy)** | **post-train OOS** |
| **전체** | 2012-01-01 → 2026-02-27 | 14.16 | 14 년 multi-regime | — |

**2017-2018 gap**: 학습 warmup 구간 (2017-02-21 부터 학습 시작, feature lookback + warm-up). fold 에 포함하지 않음. (F0b 2016 끝 → F1 2019 시작, 2 년 건너뜀)

### 3.2 설계 근거

- **F0a/F0b (pre-train OOS)**: 데이터 가용성 확인 완료. 2011-01-03 부터 OHLCV 1167 historical S&P 500 coverage 100%, marketcap_reconstructed 1997~, financial annual 1985~. feature warmup 1 년 감안 → 2012 부터 실사용.
- **F0a 만 3 년** (F0b 는 2 년): 2011 의 첫 해는 pack warmup 으로 일부 쓰고, 2012-14 는 3 년 block 으로 묶어 의미 있는 regime window 확보.
- **"2019 부터 시작"**: 2017-2018 은 feature rolling lookback (63d IC lookback 등) warm-up. 학습 시작점 2017-02-21 와 겹치므로 "훈련/평가 섞임 구간" → 제외.
- **2년 단위 분할 (F1/F2)**: 한 fold 안에 BULL + SIDE + DEFENSIVE 가 고루 포함되도록.
- **F3 만 1.4 년**: 학습 cutoff 2024-05-31 에 맞추기 위해.
- **F4 길이 1.74 년**: Step C 와 거의 동일 (Step C 는 2024-06-01 → 2026-04-17 로 1.87 년이나, pack end 가 2026-02-27 로 한정됨).

### 3.3 Survivorship bias 인식 (v2 추가)

F0a/F0b 에서 평가되는 universe 는 "**2011~2016 당시 S&P 500 구성원 중 현재(2026)까지 financial/marketcap 데이터가 남아있는 종목**". 즉 **대체로 survivor 편향된 subset**. 이 bias 의 방향:

- **CAGR 상향 편향**: 상장폐지/합병된 부진 종목 제외 → 평균 CAGR 과대
- **MDD 과소 편향**: 파산 기업의 극단 drawdown 배제
- **IC 는 거의 영향 없음**: rank correlation 은 survivor subset 내에서도 well-defined

**결론**: F0a/F0b 의 **CAGR 절대값은 낙관적**이지만, **baseline vs retrain 비교에는 동일 universe 이므로 bias 상쇄** → relative gate (G6-B, G6-C) 는 여전히 신뢰 가능. 이 성격을 리포트에 명시.

### 3.4 regime 분포 (VIX 기준 사전 추정)

Fold 별 BULL/SIDE/DEF 분포는 실행 시 자동 집계하여 리포트에 포함. Phase A 실행 전 가설:
- F0a (2012-14): 2012-13 저VIX BULL 다수, 2014 하반기 DEFENSIVE 진입
- F0b (2015-16): 에너지/신흥국 공포로 SIDE/DEFENSIVE 비중 큼
- F1 (2019-20): COVID crash 로 DEFENSIVE 비중 큼 (~10-15%), SIDE 많음
- F2 (2021-22): 2022 bear 로 SIDE 우세
- F3 (2023-24-05): BULL 주도
- F4 (2024-26): 이미 측정 — BULL 57%, SIDE 40%, DEF 3%

---

## 4. 측정 대상 Signal (4 종)

| Arm | Frozen signal file | 비고 |
|---|---|---|
| `Baseline_V2` | `frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz` | 현재 live signal |
| `P5_RETRAIN` | `frozen_signal_P5_RETRAIN_20260423_153457.npz` | T1 OFF, BULL 부분 OFF |
| `P5_RETRAIN_T1` | `frozen_signal_P5_RETRAIN_T1_20260423_183119.npz` | T1 w=0.5/0.3, BULL ON |
| `P5_RETRAIN_T1b` | `frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz` | T1 w=0.3/0.2, BULL ON |

총 **4 signals × 6 folds = 24 simulations**. 각 sim 10~20 초 예상 (Step C 에서 2.7~4 초/fold, 14 년 pack 은 per-fold 기준 동일 수준) → **전체 ~8-10 분 내 완주**.

---

## 5. 평가 Protocol (= Step C, fold 별로만 변형)

### 5.1 고정 (Step C 와 동일)

| 항목 | 값 |
|---|---|
| Pack | **`precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`** (v2: 새로 재빌드) |
| Initial capital | $100,000 |
| Daily buy limit | $1,000 |
| Commission / Slippage | 10 / 5 bps |
| Rebalance mode | daily |
| Strategy stack | `SIDE_DEF_P12` (live) |
| Regime blend | OFF |
| Universe | Historical S&P 500 (coverage-aware) |

### 5.2 Fold 별 변경 사항

- Signal 은 고정 (한 signal 이 모든 fold 에 공통 적용)
- Sim window: fold 기간
- **포트폴리오 reset**: 각 fold 시작 시 capital $100K, 포지션 0 로 초기화 (fold 간 누적 효과 제거)

### 5.3 수집 metric (fold 당)

- **수익성**: CAGR, Total_Return, Final_Value
- **위험**: MDD, Sharpe, Calmar, Daily_Win_Rate
- **비용**: Commission%, Turnover/yr, Rebalance_Days
- **Signal quality**: realized IC_1M, IC_3M, Spread_1M, Spread_3M
- **Regime 분포**: 해당 fold 의 BULL/SIDE/DEF 일수

### 5.4 Aggregate metric (signal 당, 집계)

fold 그룹별로 집계 (전체 + pre-train OOS + in-sample + post-train OOS):

- **Mean CAGR** (fold 별 CAGR 산술 평균)
- **Std CAGR**
- **CV CAGR = std / mean** ← gate #6 의 핵심
- **Worst-fold CAGR** (min across folds)
- **Range CAGR** (max − min)
- **Pass count**: CAGR > 0 인 fold 수 (6 기준)
- **추가**: pre-train OOS 평균 (F0a/F0b) vs post-train OOS (F4) 비교 — temporal drift 측정

---

## 6. Gate 정의

### 6.1 Gate #6 공식 (baseline_benchmark.md 기존 정의)

"**fold CAGR std ≤ mean × 0.5**" 

= CV ≤ 0.5. 즉 CAGR 의 fold 간 변동이 평균의 절반 이하이면 "안정적".

### 6.2 Phase A 의 비교 게이트 (4 signal 교차 판정)

| Gate | 기준 | 해석 |
|---|---|---|
| G6-A (absolute) | CV ≤ 0.5 | 절대 안정성 (기존 정의) |
| G6-B (relative) | CV(candidate) ≤ CV(baseline) | "baseline 보다 안정" 최소 요구 |
| G6-C (worst-fold) | worst-fold CAGR(candidate) ≥ worst-fold CAGR(baseline) | 최악 구간 방어력 |
| G6-D (all-positive) | 모든 fold CAGR > 0 | 기본 강건성 |

- Step C gate verdict 에 **G6-B 통과만** 가산: 기존 Step C 의 "PROMOTE* needs T5" 에서 T5 위치 채움
- G6-A / G6-C / G6-D 는 monitoring 성격 (리포트에만 포함)

### 6.3 Phase A 단독 판정 규칙

Phase A 만으로는 신호 promote/reject 최종 결정 안 함 — **Step C 결과와 결합해 재판정**:

| Step C (기존) | + Phase A | 최종 판정 |
|---|---|---|
| PROMOTE* (3+ pass + mand) | G6-B pass | **PROMOTE** (확정) |
| REJECT* (mand fail) | G6-B pass 이고 G6-C baseline dominate | **HOLD** (defensive variant 로 보존) |
| REJECT* | G6-B fail | **REJECT** (확정) |
| HOLD* | G6-B pass | **PROMOTE** (T5 효과로 상향) |

---

## 7. 예상 결과 & 가설별 해석

### 7.1 가설 1: Baseline 은 2024-26 BULL-heavy 운빨 (user 주요 가설)

**예상 증거**: baseline V2 F4 (2024-26) CAGR 56%, F1~F3 CAGR 훨씬 낮음 (예: 20~30%). CV > 0.5, worst-fold 낮음.

**있을 경우 행동**: retrain signal 의 G6-B 에서 유리 → defensive variant 로 promote 고려. Phase B 로 본격 재학습 iteration 돌입.

### 7.2 가설 2: Baseline 은 모든 fold 에서 견고

**예상 증거**: baseline V2 F1~F4 CAGR 모두 40%+, CV < 0.5, worst-fold 35%+

**있을 경우 행동**: baseline 의 알려지지 않은 구조적 장점이 있다고 인정. retrain 방향을 **"baseline 의 매커니즘 이해 및 복제"** 로 선회. T2 (regime specialization) 는 연기.

### 7.3 가설 3: 둘 다 CV 높음 (signal 자체가 noisy)

**예상 증거**: baseline 과 retrain 모두 fold 별 CAGR 변동 큼, 단순 regime-driven

**있을 경우 행동**: signal 선택이 핵심 lever 가 아니고 **exit trigger / rebal frequency 쪽 개선 필요**. Track D iteration 복귀.

### 7.4 가설 4: Retrain (T1b) 이 F1~F3 에서 baseline 보다 우위

**예상 증거**: T1b 는 F1~F3 에서 baseline 보다 CAGR 높으나 F4 에서 역전

**있을 경우 행동**: T1b 는 "최근 BULL 제외 모든 시기에 더 좋은" defensive/diversifying signal. regime-conditional blend (BULL → baseline, 나머지 → T1b) 실험 검토.

---

## 8. 구현 범위 (Phase A, v2)

### 8.1 새 파일 2 개

**(a) `phase3/tests/rebuild_pack_walk_forward.py`** — 14 년 pack 재빌드

역할:
- `engine.prepare_inputs(cfg)` 호출, `start_panel_date=2011-01-03`, `end_date=2026-02-27`
- 기존 동일 범위 pack 있으면 skip / force-rebuild 옵션
- 출력: `precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
- **예상 소요: ~10-15 분** (기존 pack 5 분 × 2)

**(b) `phase3/tests/step_d_walk_forward.py`** — 6-fold 평가

역할:
- 8.1(a) 의 pack 로드
- Step C 의 인프라 (cfg, VIX regime map, simulator wrapper) 재사용
- 6 fold × 4 signal = 24 sim 실행
- Fold 별 metric + signal 별 aggregate 집계 (그룹별: all / pre-train OOS / in-sample / post-train OOS)
- G6-A/B/C/D gate 계산
- 리포트 출력 (console + JSON + markdown)

### 8.2 재사용 (수정 없음)

- `phase3.tests.step_c_gate_evaluation` 의 `_build_cfg`, `_load_vix`, `_realized_oos_ic`, `_run_sim`, `LEGACY_STRATEGY`, `SIDE_DEF_P12_TRIGGERS` (signal_path, window 만 바꿔서 호출)
- `phase3.simulator.run_simulation` 그대로
- `phase3.daily_runner.load_frozen_signal` 그대로

### 8.3 UI 통합

- `phase3/launcher.py` 에 **"T26 Walk-Forward (pack rebuild + eval)"** 버튼 추가
  - 클릭 → 확인 popup → pack rebuild (~15 min) → step_d eval (~10 min) → 리포트 표시
  - 진행 상태 콘솔 로그로 확인

### 8.4 output artifact

| 파일 | 내용 |
|---|---|
| `{save_dir}/precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz` | 새 pack |
| `phase3/docs/t5_walk_forward_results_<stamp>.json` | raw metric per signal × fold |
| `phase3/docs/t5_walk_forward_results_<stamp>.md` | 사람 읽기용 요약 (signal × fold 테이블, CV, gate verdict) |
| `phase3/docs/t5_pack_rebuild_log_<stamp>.json` | pack 재빌드 감사 로그 |
| 콘솔 출력 | 축약 요약 + 최종 verdict 업데이트 |

### 8.5 기타

- **재현성**: 모든 sim 이 deterministic (randomness 없음) → 다시 돌려도 동일 결과
- **성능**: pack rebuild ~15 min + eval ~10 min = **~25 분 총**
- **오류 회복**: 한 signal×fold 에서 실패해도 나머지 계속 진행

---

## 9. 실행 계획 (사용자 액션)

1. 이 계획서 검토 → 사인-오프 (fold 경계 / metric / gate 정의 수정 요청 가능)
2. 내가 구현 → AST + 데이터 sanity 체크
3. 내가 자동 실행 (5 분 내) → 결과 요약 보고
4. 사용자 판정 & 후속 방향 (§7.1~§7.4 분기 맵 기준)

---

## 10. 변경 이력

| 일자 | 버전 | 변경 |
|---|---|---|
| 2026-04-23 | v1 | 초안. Phase A 범위 정의, 4-fold 설계, gate G6-A~D. |
| 2026-03-28 | v2 | **APPROVED**. 데이터 가용성 (2011+) 확인 후 pack 을 2011-01-03 → 2026-02-27 로 확장. fold 4 → 6 개 (F0a/F0b pre-train OOS 추가). Survivorship bias §3.3 추가. 구현 범위 §8 에 pack rebuild script + T26 UI button 포함. |
