# Cursor Work Order — ML-2.0 Regime-Specific Submodels

**Version**: v1.1 (2026-05-10, codex review 반영)
**Status**: **CLOSED — ML 트랙 보류 (2026-05-11)**. 결과는 `phase3/docs/ml_v20_results_20260510.md` 참조. autotrade v0 구현 완료 후 재개.
**Review base**: `/Users/shin-il/PyCharmMiscProject_codex/docs/CURSOR_HANDOFF_ML_20_REVIEW.md`

이 문서는 ML-1.7 진단(complementarity + IC + α-sweep)이 끝난 시점에서, 다음 단계인 ML-2.0 결정을 codex와 상의하기 위한 handoff다. 현재 결과를 정리하고, ML-2.0의 가설/실행 계획/실패 게이트까지 명시한다.

### Changelog (v1.0 → v1.1, codex review 반영)

1. 1차 게이트를 단일 IC threshold에서 **composite gate** (mean > 0, ΔIC ≥ +0.02, pos-rate ≥ 55%, t-stat > 1.0, sample_step=1 재확인)로 강화 — §3.3, §4.5, §6
2. **Coverage 31%는 분모 분리**: raw whole-panel vs (eval_dates ∩ tradable). 31%가 과해 보일 수 있다는 점 §2.5에 명시
3. **"target redesign 불필요" 결론 약화**: ML-2.0 전에는 안 하지만, 실패 시 fallback branch로 유지 — §3.4 추가
4. ML-2.0 통과 시 평가 범위는 default-only가 아닌 **default + rolling + regime + sample_step=1 재측정** — §4.5
5. `BLEND_ens_v_rc_v2`는 **stability-damper archive candidate**로 명시 — §2.3 끝
6. 해석 가이드 톤 조정: "feature에 BULL signal 없음" → "현재 (feature × target × ranker family × walk-forward setup) 조합으로는 못 잡았음"

---

## 0. 한 줄 요약

`ml_xgb_v16` 단일 모델은 **BULL에서 IC −0.042, SIDE에서 +0.048**로 명확히 *regime-conditional* 하게 잘못 학습되어 있다. blend(α-sweep)는 CV는 줄이지만 CAGR로는 단독 GA를 못 이긴다. **다음 한 단계 = regime별 XGBRanker 별도 학습 (ML-2.0)**. 이 단계가 BULL의 IC 부호를 못 뒤집으면 ML 트랙은 deprioritise 한다.

---

## 1. 지금까지 한 일 (ML-1.0 → ML-1.7)

| Phase | 산출물 | 핵심 결과 |
|---|---|---|
| ML-1.0 | `frozen_signal_ML_v1_*.npz` | XGBRegressor + reg:squarederror — 예측이 [0.484, 0.583]로 평탄, 단독 mean CAGR +15.6% (V2의 절반) |
| ML-1.5 | `frozen_signal_ML_v15_*.npz` | XGBRanker(`rank:pairwise`) + regime one-hot + cs-rank post-process — F0a 학습 가능, +17.5% CAGR, CV 0.426 (V2보다 안정) |
| ML-1.6 | `frozen_signal_ML_v16_20260508_175944.npz` | bins=200, 깊은 트리, raw score (cs-rank 제거) — +18.0% CAGR, CV 0.398. 여전히 0/3 hardgate FAIL vs P2_OOS |
| ML-1.7 | `phase3/ml/{ml17_diagnostics, run_ml_v17, run_ml_v17_ic, build_blend_signals}.py` 외 | 진단 + α-sweep — 이번 handoff의 근거 |

**핵심 보고서**:
- `phase3/docs/ml_handoff_20260510.md` — ML-1.0~1.6 이력 + 단일-모델 한계 정리
- `phase3/docs/ml_v17_complementarity_report_20260510.md` — ML-1.7 진단 종합
- `phase3/docs/t5_walk_forward_results_20260510_144257.md` — ML-1.7 step_d 78 sims

---

## 2. ML-1.7에서 확인된 결정적 사실 (codex가 반드시 알아야 함)

### 2.1 ML과 GA는 매우 다른 신호다

F4 (post-OOS, 2024-06 → 2026-02), 88 sample dates, per-date min-max 정규화 후:

| Pair | Spearman | top10 Jaccard | top10 A-only |
|---|---:|---:|---:|
| `ml_xgb_v16` vs `p2_oos` | +0.017 | 0.018 | 96.7% |
| `ml_xgb_v16` vs `ens_v` (shadow) | **−0.176** | 0.021 | 96.0% |
| `ml_xgb_v16` vs `p13_xy` | −0.179 | 0.032 | 94.1% |
| (ref) `ens_v` vs `p2_oos` | +0.557 | 0.184 | 71.6% |
| (ref) `p13_xy` vs `ens_v` | +0.956 | 0.593 | 26.6% |

GA들끼리는 +0.56~+0.96으로 강한 양의 상관, ML-vs-GA는 모두 음의 상관. 즉 ML은 진짜 다른 종목을 고른다 (top-10 비공통률 96%).

### 2.2 IC regime breakdown (가장 중요)

각 신호의 score를 fwd1 (1M forward return)과 일별 Spearman 측정:

| Signal | overall IC | BULL (n=51) | SIDE (n=34) | DEF (n=3) |
|---|---:|---:|---:|---:|
| **`ml_xgb_v16`** | +0.0095 | **−0.0419** | **+0.0478** | **+0.4471** |
| `p2_oos` | −0.0058 | +0.0023 | −0.0302 | +0.1327 |
| `ens_v` | +0.0070 | +0.0057 | −0.0038 | +0.1509 |
| `p12_y` | +0.0150 | +0.0143 | +0.0056 | +0.1327 |

→ ML-1.6 단일 모델은 **BULL에서 부호가 거꾸로** 학습됐다. SIDE/DEF는 GA를 능가. 이 비대칭이 ML-2.0의 직접적 motivation이다.

### 2.3 Blend (α-sweep) 결과

- α profiles: `static_a25/a50/a75`, `rc_v1` (BULL=GA-only, SIDE/DEF=0.5/0.5), `rc_v2` (BULL=GA-only, SIDE/DEF=0.3/0.3)
- GA bases: `p2_oos`, `ens_v`
- 평가: default 6-fold, baseline = `p2_oos`

| Signal | mean CAGR | CV | F4 | Hardgate |
|---|---:|---:|---:|:---:|
| `p2_oos` (baseline) | +21.4% | 0.433 | +30.3% | ALL |
| `ml_xgb_v16` | +18.0% | 0.398 | +20.8% | FAIL |
| (모든 `p2_oos` 베이스 blend, 5종) | +16.8~17.9% | 0.41~0.50 | 18.6~26.8% | FAIL |
| `BLEND_ensv_a75` | +20.9% | 0.332 | +30.0% | ALL |
| `BLEND_ensv_rc_v1` | +20.7% | 0.345 | +30.4% | ALL |
| **`BLEND_ensv_rc_v2`** | **+22.4%** | **0.327** | +30.7% | **ALL** |
| `ens_v` 단독 (ref) | +26.9% | 0.473 | **+47.8%** | ALL |

요점:
1. **모든 `p2_oos` 베이스 blend는 FAIL** — 약한 GA에 ML을 더해도 못 살림.
2. **`ens_v` 베이스 blend 3종 PASS** — `rc_v2`가 CV 0.327 (전체 1위), CAGR +22.4% (blend 1위).
3. **하지만 어떤 blend도 `ens_v` 단독 (+26.9%)을 CAGR로 못 이김.** F4 격차가 가장 큼: ens_v 단독 +47.8% vs rc_v2 +30.7% = **−17.1pp**.
4. fold별로 보면 blend가 BULL-strong fold (F3, F4)에서 ens_v를 깎아먹고 stable-side fold (F0b, F2)에서만 ens_v를 살린다 — 2.2의 IC 부호 패턴과 정확히 일치.

### 2.4 ML-1.6 panel은 진짜 regime-uniform이다

`scores_panel` shape (D, N, 3)의 3 regime slot에 **동일한 예측이 복제 저장됨** (sample 20 dates 검증, 0 differences). 즉 regime one-hot을 입력으로 받았어도 출력은 regime-agnostic. 이것이 ML-2.0의 사전 검증 데이터이며, 단일 모델 가설의 한계점을 명확히 보여준다.

### 2.5 Coverage 31%는 그대로 해석하면 안 됨 (review 반영)

ML-1.7 보고서에서 reported된 panel coverage **31.14%**는 `(D × N × 3)` 전체 분모 기준이다. 이 분모에는:

- ML이 평가하지 않은 fold 외 구간
- 비-tradable cell (우리가 어차피 사용 안 하는 cell)
- regime 슬롯 3개 모두 (ML-1.6은 동일 복제라 사실상 N×D 정보량)

이 모두 포함되어 있어 "ML이 sparse하다"는 결론이 과장될 수 있다. **실제 알고 싶은 것은 "eval 날짜 × tradable universe 안에서 ML이 점수화한 비율"**.

ML-2.0에서는 panel 저장 후 다음 3가지 coverage 모두 보고:
- `raw_panel_finite_frac` (현재 31% 수치)
- `eval_dates_only_finite_frac` (F0a~F4 eval 구간만)
- `eval_dates_and_tradable_finite_frac` ← 진짜 metric

ML-1.7 panel에 대해서도 ML-2.0 작업 시작 시 한 번 재측정해서 보고서에 부록으로 추가한다 (§9 참고).

### 2.6 `BLEND_ens_v_rc_v2`는 archive candidate (review 반영)

이 blend 신호는 CAGR로 ens_v 단독을 못 이기지만 (`+22.4%` vs `+26.9%`), **CV 0.327 (전체 1위), worst-fold +14.2%, hardgate clean ALL pass**라는 stability profile을 갖는다. 단독 승격 후보는 아니지만, 향후 live shadow에서 변동성 문제 발생 시 "고-CV GA를 안정화하는 control experiment" 카드로 재사용 가능하다. 산출물 (`phase3/ml/artifacts/ml17/blends/frozen_signal_ML_BLEND_ens_v_rc_v2_*.npz`)은 **삭제하지 않고 archive 유지**.

---

## 3. ML-2.0 가설과 mission

### 3.1 핵심 가설

> BULL/SIDE/DEFENSIVE 각 regime마다 **별도의 XGBRanker**를 학습하면, 단일 모델이 BULL에서 잘못 잡고 있는 부호를 양수로 뒤집을 수 있다. 그리고 SIDE/DEF에서 이미 좋은 IC는 유지/개선된다.

### 3.2 mission (이번 단계의 단일 질문)

**ML-2.0 BULL submodel의 BULL-only IC가 ML-1.6 대비 유의미하게 개선됐는가?**

(v1.0의 "양수로 만들 수 있는가"는 게이트가 너무 약했다 — n=51에서 +0.005는 우연 가능. composite gate는 §3.3 참고)

답이 YES면 — ML-2.1 ensemble (regime-routed ML × GA blend) 가능성을 본다.
답이 NO면 — *현재 feature × target × ranker family × walk-forward 조합*으로는 BULL을 못 잡는다는 강한 증거. §3.4의 fallback branch로 진행.

### 3.3 1차 composite gate (review 반영)

단일 IC threshold가 아닌 다음 4개 모두 만족할 때만 BULL submodel "성공" 판정:

| 항목 | 임계 | 측정 |
|---|---|---|
| BULL mean IC | > 0 | 일별 Spearman 평균 |
| ΔIC vs ML-1.6 BULL | ≥ +0.02 (즉 −0.042 → ≥ −0.022) | 동일 window, 동일 sample_step에서 비교 |
| BULL positive-rate | ≥ 55% | 일별 IC > 0인 날의 비율 |
| BULL t-stat | ≥ 1.0 | mean / (std / √n) |

추가 권장 (composite gate 보강):
- **`sample_step=1` 재확인** — F4에서 88 → ~440 dates로 확장해서 부호 안정성 검증 (~50초 추가)
- bootstrap 95% CI 하한 ≥ 0 근처 (선택, t-stat이 이미 보면 됨)

→ 통과 시 §4.5 본 평가 진행. 미통과 시 §3.4 fallback.

### 3.4 fallback branch (review 반영, ML-2.0 실패 시)

단순히 "ML 보류"가 아니라 다음 중 하나로 진행 결정:

1. **target redesign 우선**:
   - `fwd1 + fwd3 blended relevance` (장기 신호 가중)
   - `top-decile relevance label` (binary surge target)
   - `rank:ndcg` 유지하되 relevance binning 변경
2. **feature engineering**: macro feature, lagged returns, sector momentum
3. **model family 변경**: LightGBM `lambdarank`
4. **ML 트랙 보류 + GA P12/P13 ensemble 라인 집중** (가장 보수적)

위 4개 중 어느 branch로 갈지는 ML-2.0 실패 패턴을 보고 결정 (BULL은 못 잡았지만 SIDE는 더 좋아졌다 → target redesign이 유망 / SIDE도 같이 무너졌다 → 보류).

### 3.5 비-목표 (이번에는 안 함)

- ML-1.6보다 hardgate 통과를 보장하지 않는다 — *이건 ML-2.0의 책임이 아님*
- 새 feature 추가, target redesign — 이번 단계에선 안 함 (§3.4 fallback에서 다룸)
- LightGBM 비교 — 이번에 안 함
- shadow run, live promotion — 이번에 안 함 (research-only)

---

## 4. ML-2.0 구현 사양 (시작점, 조정 가능)

### 4.1 데이터/feature/target — 변경 없음

- pack: 동일 (`precompute_qresearch_v4_12_2011-01-03_*.npz`)
- target: `cross_sectional_rank_targets(fwd1)` (ML-1.6 그대로)
- feature: 36 indicator + ~~regime one-hot 3개~~ → **regime one-hot 제거** (각 모델이 단일 regime 데이터만 학습하므로 불필요)
  - 즉 feature 차원 = 36 (v15/v16의 39에서 3 줄어듦)
- walk-forward: `FOLDS_DEFAULT` 동일 (F0a~F4)
- embargo: 21 days, min_train_dis: 200 — ML-1.6과 동일

### 4.2 학습 절차 (regime별 분리)

각 fold마다, train_dis 안에서:

1. `regime_by_di` 맵으로 train_dis를 BULL/SIDE/DEFENSIVE 셋으로 분할
2. 각 regime 셋에 대해 별도 `XGBRanker` 학습
   - 동일 하이퍼파라미터 (V16_PARAMS_OVERRIDE: depth 7, n_est 1200, lr 0.04, min_child_weight 25, ndcg_exp_gain=False, bins=200)
   - 그룹 정의: 같은 di의 row들이 한 그룹 (ranker는 group-wise 학습)
3. eval_dis에서 각 di의 regime label로 모델 라우팅 → 그 모델의 prediction을 사용
4. per-date min-max 정규화 (ML-1.6과 동일)
5. `scores_panel[di, ni, regime_idx]` = 그 regime 모델의 예측. **slot 분리 의미 있음** — 다른 regime의 예측은 NaN

### 4.3 DEFENSIVE 데이터 부족 처리

- DEFENSIVE 비중은 train 기간 전체에서 매우 낮음 (몇 %). 학습 row가 5000 미만이면 SIDE 데이터에 merge하여 학습 (ML-1.6의 fallback과 동일한 양식: panel "n_train < 5000" 체크 그대로)
- inference 시 DEFENSIVE 라벨 di → SIDE 모델의 예측 사용
- 이 fallback이 발동된 경우 model_meta에 명시 기록

### 4.4 출력 schema — 호환성 유지

- 파일명: `frozen_signal_ML_v20_<timestamp>.npz`, `phase3/ml/artifacts/`
- `signal_type = 'ml_external_scores'` 유지 — live는 그대로 차단됨
- `scores_panel` shape `(D, N, 3)` 유지 — 단 이번엔 슬롯이 의미 있게 다름
- `model_meta`: 추가 필드
  - `regime_specific_submodels`: True
  - `per_regime_meta`: BULL/SIDE/DEFENSIVE 별 fold 학습 통계 (n_train_rows, best_iter, val_metric)
  - `defensive_fallback_used`: bool

### 4.5 평가 절차 (review 반영)

**A. 빠른 진단 (먼저, ~10분)**

1. `phase3/ml/run_ml_v17_ic.py`에 ml_v20 추가 → IC mean 측정 (overall + regime breakdown)
2. **§3.3 composite gate** 평가 (BULL mean IC > 0, ΔIC ≥ +0.02, pos-rate ≥ 55%, t-stat ≥ 1.0)
3. **`sample_step=1`로 재측정** — sample_step=5에서 통과해도 step=1에서 부호 안정성 확인

→ NO 통과 시: §3.4 fallback branch 결정 후 ML-2.0 보고서로 종료.

**B. 통과 시 본 평가 (~30분, default-only가 아님)**

4. `step_d_walk_forward.py` SIGNALS list에 등록 → **default + rolling + regime 3개 fold-set** 모두 평가 (review 반영, default-only는 부족하다고 codex가 지적)
5. ML-1.7 진단 (overlap/correlation) 갱신: ml_v20 vs p2_oos / ens_v
   - sample_step=1로 재측정 (default eval window 안에서)
   - ml_v20 vs ens_v top-10 A-only %가 80% 이상으로 유지되면 다양성 보존, 50% 이하로 떨어지면 ML이 GA를 흉내 내는 쪽으로 수렴 (위험 신호)
6. (선택) ML-2.0 panel과 ML-1.7 panel 모두에 대해 §2.5의 3가지 coverage 분리 측정해서 보고서 부록에 첨부

---

## 5. 절대 금지

- 라이브 경로 변경 금지 (`run_daily.py`, `daily_runner.py`의 ML guard 제거 금지)
- 기존 frozen signal (`v1`/`v15`/`v16`) 덮어쓰기 금지
- 기존 GA baseline / shadow signal (`P11_FUNDB_ANCHOR`) 변경 금지
- `signal_type='ml_external_scores'` flag 제거 금지 — research-only 격리 유지
- ML-1.7 산출물 (`phase3/ml/artifacts/ml17/*`) 덮어쓰기 금지
- ML-2.1 ensemble 또는 blend 변형으로 바로 점프 금지 — ML-2.0의 1차 게이트 통과 후
- `step_d_walk_forward.py` SIGNALS list의 기존 entries 변경 금지 (추가만 가능)

---

## 6. acceptance criteria (review 반영)

ML-2.0 작업 완료 보고는 아래에 답해야 한다.

### 6.1 1차 composite gate (§3.3)

1. **BULL submodel IC mean** — 이전 −0.042 → 현재 ?
2. **ΔIC vs ML-1.6 BULL** — ≥ +0.02 인가?
3. **BULL positive-rate** — ≥ 55% 인가?
4. **BULL t-stat** — ≥ 1.0 인가?
5. **sample_step=1 재측정** — 위 4개 결과가 sample_step=1에서도 유지되는가? (sign flip 안정성)

위 1~4 모두 만족 + 5 안정성 확인 시에만 1차 게이트 통과.

### 6.2 2차 본 평가 (1차 통과 시)

6. **SIDE submodel IC mean** — 이전 +0.048 유지 또는 개선?
7. **DEFENSIVE submodel IC mean** — fallback (SIDE merge) 사용했는가? 사용 안 했다면 IC?
8. **`scores_panel` 3 슬롯이 의미 있게 다른가** — `verify_ml_panel_regime_uniform()` 결과 `regime_uniform=False` 가 나오는가?
9. **default + rolling + regime 3개 fold-set step_d 결과** — mean CAGR / CV / hardgate
10. **ml_v20 vs ens_v overlap (sample_step=1)** — top-10 A-only %가 80%대 유지인가, 50% 이하로 떨어졌는가?

### 6.3 Coverage 분리 측정 (§2.5)

11. ML-1.7 panel (`ml_v16`): raw vs eval-only vs eval×tradable coverage 3종 보고
12. ML-2.0 panel (`ml_v20`): 동일 3종 coverage 보고

### 6.4 다음 우선순위

13. **ML-2.1 ensemble 진행** (1차 + 2차 게이트 둘 다 통과, BULL/SIDE 모두 양수, hardgate 일부 통과)
14. **target redesign 진행** (1차 게이트 실패하지만 SIDE는 개선 — feature는 있되 target이 안 맞다는 신호)
15. **feature engineering 진행** (1차/2차 모두 약하지만 ML-1.6 대비 일부 개선)
16. **ML 트랙 보류** (모든 게이트 실패 — GA P12/P13 라인 집중)

---

## 7. 결론 형식

작업 끝 보고는 아래 순서로:

1. 무엇을 만들었는지 (파일 목록 + 산출물 경로)
2. 어떤 실험을 돌렸는지 (커맨드 포함)
3. 핵심 표 3개
   - per-regime IC mean (overall + by_regime)
   - default 6-fold CAGR 표
   - vs ML-1.6 / vs ens_v / vs p2_oos 비교 표
4. 한 줄 결론 (BULL IC 부호 뒤집힘 여부)
5. 다음 우선순위 추천 1개

---

## 8. 중요한 해석 가이드 (review 반영)

- **단일 IC threshold 통과만으로는 "성공" 아님** — composite gate (§3.3)을 모두 만족해야 진짜 sign flip 증거. n=51에서 mean +0.005는 우연일 수 있음.
- **BULL IC가 음수로 남아도 결론을 좁히지 말 것** — "feature에 BULL 정보가 없다"는 너무 강한 결론. 정확하게는 "현재 (feature × target × ranker × walk-forward) 조합으로는 못 잡았다"는 증거. §3.4의 fallback branch에서 어느 axis를 바꿀지 결정.
- **DEFENSIVE n=3 정도라 어떤 결과도 신뢰 못한다** — fallback 여부와 SIDE와의 합치성 정도가 더 중요
- **CAGR이 ens_v 단독을 못 이겨도 hardgate ALL pass면 의미 있다** — 우리가 진짜 보는 건 BULL 부호 뒤집기 + hardgate 통과로 "ML이 GA를 보완하는 첫 번째 통계적 증거" 확보 여부
- **만약 ml_v20 vs ens_v overlap이 50% 이상으로 올라가면** ML이 GA를 흉내 내는 쪽으로 학습된 것 — 다양성 손실. 이 경우 ML-2.1 ensemble 효과 의문스러우니 신중
- **default 통과만으론 충분치 않다** — rolling/regime fold-set 재현성까지 봐야 ML-2.0 결과의 의사결정 무게가 생김 (codex review §4)

---

## 9. 추천 산출물

```
phase3/ml/run_ml_v20.py
phase3/ml/artifacts/frozen_signal_ML_v20_<timestamp>.npz
phase3/ml/artifacts/frozen_signal_ML_v20_<timestamp>_meta.json
phase3/docs/ml_v20_regime_submodel_report_<YYYYMMDD>.md
phase3/docs/t5_walk_forward_results_<timestamp>.{md,json}  (step_d 자동 생성)
```

선택 (Phase A 갱신용):
```
phase3/ml/artifacts/ml20/
    daily_overlap_metrics_v20.csv
    ic_results_v20.csv
```

---

## 10. 파일 읽기 순서 (codex 또는 다음 사람을 위한)

이번 ML-2.0 작업 시작 전 반드시 읽기:

1. `phase3/docs/ml_v17_complementarity_report_20260510.md` — Phase A/B/IC 종합 (가장 최근)
2. `phase3/docs/ml_handoff_20260510.md` — ML-1.0~1.6 이력
3. `phase3/ml/run_ml_v16.py` — 베이스로 쓸 코드
4. `phase3/ml/walk_forward.py` — fold 정의
5. `phase3/ml/data_panel.py` — feature_matrix_for_dates의 `regime_idx` 필드 사용법

참고:
6. `phase3/ml/run_ml_v17_ic.py` — IC 측정 패턴 (regime breakdown 코드 그대로 ml_v20에 재사용)
7. `phase3/ml/build_blend_signals.py` — score_panel 형식과 simulator 호환 schema
8. `phase3/tests/step_d_walk_forward.py` — SIGNALS list 등록 패턴 (162-180 line)

---

## 11. 작업 후 반드시 남길 것

- 추가/수정한 파일 목록
- 실행한 커맨드 (재현 가능하게)
- 생성된 산출물 경로
- 1차 게이트 통과/실패 명시 (BULL IC ≥ +0.005)
- 아직 남은 리스크 (특히 DEFENSIVE fallback 영향)
- 다음 사람이 바로 이어갈 수 있는 추천 next step 1개

---

## 12. Notes For Human Reader

- **이 handoff의 본질**: "regime을 input으로 주는 것" vs "regime마다 별도 모델을 학습하는 것"의 *차이가 ML 입장에서 진짜 있는가?*를 한 번에 정량 검증한다. ML-1.6은 전자, ML-2.0은 후자.
- **만약 BULL IC가 여전히 음수라면**: 우리 feature 36개로는 BULL 신호를 학습할 수 없다는 강한 증거가 된다 — 이 경우 LightGBM/CatBoost 같은 model family 변경이나 새 feature가 아니면 어렵다. 그 때는 ML 트랙을 잠시 보류하고 GA P12/P13 ensemble 라인에 집중하는 것이 합리적이다.
- **반대로 BULL IC가 +0.01 이상으로 강하게 올라간다면**: 매우 큰 발견이다. 이 경우 ML-2.1 (regime-routed ML × GA blend)을 즉시 진행할 가치가 있다.
- **shadow run 영향**: 이번 단계 결과와 무관하게 현재 shadow signal `P11_FUNDB_ANCHOR`는 그대로 진행한다. ML-2.0의 결과가 좋아도 shadow 후보가 되는 것은 한참 뒤다.

---

## 13. Appendix — 짧은 paste-ready prompt (codex 빠른 공유용, v1.1)

```
ML-2.0: regime-specific XGBRanker submodels.

Context: ML-1.6 단일 모델은 BULL에서 IC -0.042 (잘못된 부호), SIDE에서 +0.048,
DEF에서 +0.45 (n=3). blend(α-sweep)는 어떤 α로도 ens_v 단독을 CAGR로 못 이김.

Mission: BULL/SIDE/DEFENSIVE 각각에 별도 XGBRanker를 학습해서 BULL submodel의
IC를 ML-1.6 대비 유의미하게 개선할 수 있는지 검증.

읽을 파일: phase3/docs/CURSOR_HANDOFF_ML_20.md (this file v1.1),
          phase3/docs/ml_v17_complementarity_report_20260510.md,
          phase3/docs/ml_handoff_20260510.md
시작점: phase3/ml/run_ml_v16.py 복사 → run_ml_v20.py
가설 변경: regime one-hot 제거(36 features), regime별 train/eval 분리, DEFENSIVE는
n_train < 5000이면 SIDE에 merge.
출력 schema: signal_type='ml_external_scores' 유지, scores_panel 슬롯이 의미 있게
다르도록 (기존 ML-1.6과 달리 동일 복제 X).

1차 composite gate (4개 모두):
  - BULL mean IC > 0
  - ΔIC vs v16_BULL ≥ +0.02
  - BULL positive-rate ≥ 55%
  - BULL t-stat ≥ 1.0
  - sample_step=1 재측정으로 부호 안정성 확인

2차 본 평가 (1차 통과 시):
  - step_d default + rolling + regime 3개 fold-set
  - ML-1.7 진단 sample_step=1 재측정
  - coverage 3종 분리 측정 (raw / eval-only / eval×tradable)

1차 실패 시: §3.4 fallback branch (target redesign / feature eng / model family / ML 보류) 결정.

절대 금지: live 변경, run_daily ML guard 제거, 기존 v1/v15/v16/blends 덮어쓰기.
```
