# T1 + T5 Integrated Design — Deployment-Aware Objective + Walk-Forward Validation

**상태**: APPROVED — 착수 대기
**선행 문서**: `phase12_signal_health_report.md` (P5 진단 결과)
**작성일**: 2026-03-28
**개정일**: 2026-03-28 (v2: sector penalty 보류, scope 축소)

---

## 1. 설계 목표

`phase12_signal_health_report.md` 가 밝혀낸 두 구조적 결함을 **GA fitness 레벨에서** 해결한다:

1. **P5 (in-sample overfit / temporal drift)**: GA 가 full-history 를 통 채로 보면서 "과거 regime 에 overfit 된 signal" 을 우수한 걸로 잘못 평가하는 문제
2. **P1 / P4 (turnover & rank churn)**: Fitness 가 turnover 를 전혀 인식하지 못해 live 에서 연 5%+ cost drag 발생

> **Scope 결정 (2026-03-28)**: **Sector concentration penalty 는 본 과제에서 제외**. 후속 과제 (T6 로 별도 tracking) 로 연기. 이유: GICS 데이터 infra 구축 공수가 크고, T1/T5 의 핵심인 P5/P1 해결에 직접 기여하지 않음. 또한 T5 의 temporal CV 가 성공적으로 작동하면 sector 집중 현상 자체가 어느 정도 자연 완화될 가능성도 있어 먼저 baseline 확인이 합리적.

### 성공 기준 (Validation Gate)

T1+T5 로 재훈련한 새 champion signal 이 **OOS holdout 구간** (2024-06 ~ 2026-04, ≈22개월) 에서:
- MeanIC > 0 이고 V1/V2 의 **같은 holdout 구간** MeanIC 보다 우위
- Spread10% > 0 이고 V1/V2 대비 우위
- AnnTO < 2500% (연 cost < 4%)

모두 만족 시 PASS → live 로 promote. 하나라도 실패 시 재설계.

**Sector HHI 는 모니터링 only** (PASS 기준 제외). Holdout 결과 리포트에 숫자는 기록하되 당락 영향 없음.

---

## 2. 아키텍처 개요

### 2.1 기존 fitness (요약)

```
fit = w_ic1 × MeanIC_1M_full
    + w_ic3 × MeanIC_3M_full
    + w_spread × SpreadMix_full
    + entropy_bonus
    − conc_pen − k_pen − corr_pen − bull_floor_pen
    + (bull_bonuses) + (side_biases)
    − risk_pen − neg_spread_pen
```

전체 기간 평균, turnover / sector 무인식, holdout 없음.

### 2.2 T1+T5 확장 fitness

```
# T5: 시간 fold 분할
for fold in folds:
    fit_fold[fold] = compute_base_fit(fold_dates)   # 기존 로직을 fold 에만 적용

fold_mean = mean(fit_fold)
fold_std  = std(fit_fold)

# T5 temporal robustness term
base = fold_mean − w_fold_std × fold_std

# T1 deployment penalties (sector HHI 제외 — T6 로 연기)
dep_pen = w_turnover       × turnover_rate(full_train_schedule)
        + w_cost           × estimated_cost_drag

fit = base − dep_pen − (existing other penalties)
```

### 2.3 OOS Holdout 분리

- 훈련 schedule: `date < holdout_cutoff`
- Holdout schedule: `date >= holdout_cutoff`
- GA 는 훈련 schedule 만 사용하여 모든 fitness / selection 수행
- 최종 champion 에 대해서만 holdout schedule 로 1회 evaluate (보고용)
- Holdout 결과가 PASS 기준 미달이면 champion 자격 박탈

---

## 3. Config 스키마 확장

Cell 0 의 `Config` dataclass 에 추가 (모두 default = OFF 로 backward-compat):

```python
# ── T1 deployment penalties ────────────────────────────────
enable_deployment_penalty: bool = False
deployment_top_n: int = 30            # phase3 portfolio cap 과 동일
deployment_cost_bps: float = 15.0     # commission + slippage 가정

w_turnover: float = 0.5               # GA sweep 으로 튜닝 예정
w_cost: float = 0.3                   # turnover 와 일부 중복이므로 낮게

# Sector HHI 는 T6 로 연기 (scope 제외)
# w_sector_hhi: float = ...
# sector_hhi_threshold: float = ...

# ── T5 walk-forward temporal validation ────────────────────
enable_temporal_cv: bool = False
cv_folds: int = 3                     # 훈련 기간을 3등분
cv_fold_mode: str = "equal"           # equal | expanding | sliding
w_fold_std: float = 1.0               # std across folds penalty weight
cv_min_samples_per_fold: int = 50     # fold 당 최소 rebalance 수

# ── OOS Holdout ────────────────────────────────────────────
oos_holdout_cutoff: str = ""          # "2024-06-01" 지정 시 활성
oos_report_only: bool = True          # True = selection 에 영향 없음, 보고 only
```

---

## 4. 구현 단계 및 의존성

### Phase 0 — (삭제됨, T6 로 연기)

Sector data infrastructure 는 본 과제 scope 에서 제외. 후속 과제 T6 에서 별도 진행.

### Phase 1 — T1 deployment penalty hooks (2-3일)

`evaluate_individual_qresearch` 내부에 helper 삽입:

```python
def _compute_deployment_penalties(
    pack, cfg, eval_dates, scores_per_date, sel_union,
):
    """Returns (turnover_pen, cost_pen, sector_pen, diag_dict)."""
    if not cfg.enable_deployment_penalty:
        return 0.0, 0.0, 0.0, {}

    top_n = cfg.deployment_top_n

    prev_top = None
    turnover_list = []
    for d in eval_dates:
        di = date_to_idx[d]
        valid = pack["tradable"][di] & np.isfinite(scores_per_date[di])
        if valid.sum() < top_n:
            continue
        top_idx = _top_n_indices(scores_per_date[di], valid, top_n)
        if prev_top is not None:
            turnover_list.append(1 - len(set(top_idx) & set(prev_top)) / top_n)
        prev_top = top_idx

    turnover = float(np.mean(turnover_list)) if turnover_list else 0.0
    cost_drag = turnover * cfg.deployment_cost_bps / 10000.0 \
                * (252 / _schedule_step_days(cfg.eval_freq))

    return (cfg.w_turnover * turnover,
            cfg.w_cost * cost_drag,
            {"turnover": turnover, "cost_drag": cost_drag})
```

`evaluate_individual_qresearch` 호출부에서:
```python
to_pen, cost_pen, dep_diag = _compute_deployment_penalties(...)
fit = base - to_pen - cost_pen - (existing penalties)
```

**산출물**: Config flag 로 on/off 가능한 deployment penalty. Diagnostics 에 `turnover`, `cost_drag` 추가.

### Phase 2 — T5 temporal CV (3-4일)

새 helper:
```python
def _build_cv_folds(dates, cfg, holdout_cutoff):
    """Split eval_dates into cfg.cv_folds chronological folds,
    excluding dates >= holdout_cutoff."""
    train_dates = [d for d in dates if d < holdout_cutoff] if holdout_cutoff else list(dates)
    if cfg.cv_fold_mode == "equal":
        return np.array_split(train_dates, cfg.cv_folds)
    elif cfg.cv_fold_mode == "expanding":
        ...
    elif cfg.cv_fold_mode == "sliding":
        ...
```

`evaluate_individual_qresearch` 의 핵심 루프 refactor:
```python
if cfg.enable_temporal_cv:
    folds = _build_cv_folds(eval_dates, cfg, cfg.oos_holdout_cutoff)
    fold_fits, fold_diags = [], []
    for fold_dates in folds:
        fit_f, diag_f = _compute_fit_for_schedule(fold_dates, ...)
        if len(fold_dates) < cfg.cv_min_samples_per_fold:
            continue
        fold_fits.append(fit_f)
        fold_diags.append(diag_f)

    if len(fold_fits) < 2:
        base = -1e9   # Can't validate temporal stability
    else:
        base = np.mean(fold_fits) - cfg.w_fold_std * np.std(fold_fits)
else:
    base = _compute_fit_for_schedule(eval_dates, ...)
```

**핵심 아이디어**: 기존 full-history fit 로직을 `_compute_fit_for_schedule(dates_subset)` 로 함수화 → CV on/off 모두 같은 코드 사용.

**Risk**: Folds=3 이면 GA 평가 시간 ~3× 증가. Profile 후 `lightweight=True` path 최적화 필요할 수 있음.

**산출물**: `enable_temporal_cv: true` 로 GA 가 temporal robustness 를 강제.

### Phase 3 — OOS Holdout enforcement (1-2일)

1. Pack schedule 생성 시 holdout 분리:
   ```python
   eval_dates_all = _build_schedule(dates, cfg.eval_freq)
   if cfg.oos_holdout_cutoff:
       train_dates = [d for d in eval_dates_all if d < cfg.oos_holdout_cutoff]
       holdout_dates = [d for d in eval_dates_all if d >= cfg.oos_holdout_cutoff]
   else:
       train_dates, holdout_dates = eval_dates_all, []
   ```
2. GA 는 `train_dates` 만 사용.
3. `run_ga_qresearch_stability` 끝단에서 champion 한 번만 `holdout_dates` 로 evaluate → 별도 리포트 column `Holdout_MeanIC`, `Holdout_Spread`, `Holdout_Turnover`, `Holdout_SectorHHI`.
4. (권장) Cell 3 / Signal Lab 저장 시 `frozen_signal_*.npz` 의 `signal_summary` 에 holdout 결과 포함.

**산출물**: 훈련/OOS 엄격 분리. OOS 숫자가 champion 자격 기준.

### Phase 4 — New objective profiles (1일)

`get_objective_profile_dict()` 확장:

```python
{
    "OBJ_A": (0.40, 0.40, 0.20),   # classic IC-heavy
    ...
    "OBJ_E": (0.20, 0.15, 0.65),   # spread-heavy
    "OBJ_F": ...,
    # ── T1/T5 objectives ────────────────────────────────
    "OBJ_G": (0.30, 0.30, 0.40),   # deployment-aware (T1 on, T5 off)
    "OBJ_H": (0.30, 0.30, 0.40),   # full T1+T5 (both on)
}
```

Meta-search config 에서 `OBJ_H` 선택 시 자동으로 `enable_deployment_penalty=True`, `enable_temporal_cv=True` 적용하는 preset mapping 추가.

### Phase 5 — Retrain experiment (1주)

1. Pack 재빌드 (sector 필드 포함)
2. GA run with `OBJ_H`, `oos_holdout_cutoff = "2024-06-01"` (≈18개월 holdout)
3. Champion 선정 후 holdout 검증
4. V1 / V2 / new champion 을 **동일 holdout 기간에서 비교 리포트** 생성 → `docs/t1_t5_retrain_experiment_log.md`
5. PASS/FAIL 판정 → live promote 또는 iterate

### 전체 추정 공수

| 단계 | 예상 일수 |
|---|---|
| ~~Phase 0 (sector data)~~ | ~~2-3일~~ (T6 로 연기) |
| Phase 1 (T1 penalties: turnover + cost 만) | 2-3일 |
| Phase 2 (T5 CV) | 3-4일 |
| Phase 3 (OOS holdout) | 1-2일 |
| Phase 4 (OBJ profiles) | 1일 |
| Phase 5 (retrain) | 5-7일 |
| **총계** | **12-17 일 (≈2.5주)** |

**품질 우선 원칙 (2026-03-28 합의)**: 공수 추정보다 **각 단계의 검증 체크리스트 통과**를 우선. 예상보다 오래 걸려도 quality gate 건너뛰지 않음.

---

## 5. 구조적 결정 사항

### 5.1 Notebook vs .py 모듈

현재 `evaluate_individual_qresearch` 는 notebook Cell 0 에 위치. 변경 시:
- **선택 A**: Notebook 직접 수정 (기존 개발 방식, 최소 마찰)
- **선택 B**: `engine/fitness.py` 로 발췌 + Cell 0 에서 import
- **추천**: **A** — Cell 0 전체를 module 화하는 건 본 과제 scope 초과. 단일 함수만 옮기면 Cell 0 다른 함수와 순환 의존 가능성.

### 5.2 기존 live V2 ENS_L3 운영 처리

T1/T5 개발 기간 (~3주) 동안 live 운영 방식:
- **선택 A**: V2 유지 (현재 상태 그대로)
- **선택 B**: V1 rollback (BATCH11) — deployment 지표 거의 동일하나 SIDE 소폭 우수
- **선택 C**: Observation-only (자동 trading 잠정 중단)
- **추천**: **A** — 방어막 (`SIDE_DEF_p12`, stop loss, grace) 이 signal 약점을 덮고 있어 긴급 교체 사유 아님. 재훈련 champion 나오면 한 번에 cutover.

### 5.3 Fold 경계 선택

- **Equal split**: 단순. 그러나 2020 COVID 같은 특이 구간이 fold 중간을 가로지르면 왜곡.
- **Expanding window**: fold_1=[t0, t1], fold_2=[t0, t2], ... — CS 표준이나 뒤 fold 가 in-sample 포함이라 정보 누수
- **Sliding window**: fold_k=[tk-w, tk] — 각 fold 독립. 권장 기본값.
- **Regime-aware**: BULL/SIDE/DEF 비율이 각 fold 에 균등하도록 stratified split (향후 개선)

**추천**: 초기 구현은 `equal`, 검증 후 `sliding` 으로 전환.

---

## 6. 위험 요소

| 위험 | 완화 전략 |
|---|---|
| **T5 GA 속도 저하 3×** | Phase 2 직후 profiling. `lightweight=True` path 유지. Population/generation 튜닝. |
| **Penalty weight 수동 튜닝 어려움** | Meta-search 의 existing meta-search 레이어에 w_turnover, w_fold_std 등을 포함시켜 자동 튜닝 |
| **Fold boundary artifacts** | 초기 equal, 문제 발견 시 sliding 또는 regime-aware |
| **18개월 holdout 이 부족할 수 있음** | Pack 가 10년치이므로 2024-06 cutoff 면 ~18개월 확보. 부족 시 2024-01 로 조정 가능 |
| **재훈련 champion 이 OOS 에서 실패** | PASS 기준 미달 시 penalty weight 조정하거나 T2 (regime-specialized) 로 이어감 |

---

## 7. 검증 체크리스트

구현 완료 후 다음 각 항목이 만족되어야 함:

- [ ] Phase 1: `enable_deployment_penalty=False` 상태에서 기존 GA 결과 byte-identical 재현 (backward compat)
- [ ] Phase 1: `enable_deployment_penalty=True` + w_turnover=0 + w_cost=0 상태에서도 byte-identical 재현
- [ ] Phase 1: Diagnostic 에 `turnover`, `cost_drag` 정상 기록
- [ ] Phase 2: `enable_temporal_cv=True`, `cv_folds=1` 에서 full-history fit 과 수치 일치 (degenerate case)
- [ ] Phase 2: `cv_folds=3` 에서 GA 속도 profile 수행 후 수용 가능한 수준 (3× 이내)
- [ ] Phase 3: `oos_holdout_cutoff` 설정 시 `train_dates` 에 holdout 날짜 없음 (엄격 격리)
- [ ] Phase 3: Champion 의 holdout 지표가 `signal_summary` 에 저장됨
- [ ] Phase 4: `OBJ_H` 선택 시 deployment_penalty + temporal_cv 자동 활성
- [ ] Phase 5: 새 champion 의 holdout MeanIC > 0, Spread > 0, V1/V2 대비 우수 (PASS)

---

## 8. 향후 연결 작업

- **T2 (Regime-Specialized Sub-Signals)**: T1+T5 가 작동하면, 각 regime 별로 T5-validated 하위 signal 을 생성 → V2 같은 ensemble 의 합리적 기반 구축
- **Phase 3 launcher 확장**: `Signal Health Dashboard` 탭 추가하여 live 에서 rolling IC / Sector HHI / Turnover 실시간 모니터링
- **자동 signal 교체 프로토콜**: Holdout 지표가 live 지표보다 크게 악화되면 auto-pause

---

## 9. 사용자 결정 사항 (APPROVED)

본 설계안 관련 주요 결정은 2026-03-28 에 확정됨:

| 항목 | 결정 |
|---|---|
| Sector concentration penalty | **보류** (T6 로 별도 tracking) |
| OOS holdout cutoff | **2024-06-01** (≈22개월 holdout, ≈8.5년 훈련) |
| 공수 제약 | **없음**. 품질 우선 (각 Phase 검증 체크리스트 통과 필수) |
| 착수 순서 | **A: 순차적** (Phase 1 → 2 → 3 → 4 → 5) |
| 개발 중 live 운영 | V2 유지 (방어막 정상, 긴급 교체 불필요) |

---

## 변경 이력

| 일자 | 버전 | 변경 내용 |
|---|---|---|
| 2026-03-28 | v1 | 초안 작성 |
| 2026-03-28 | v2 | 사용자 결정 반영: sector penalty 보류 (T6 로 연기), OOS cutoff 2024-06-01 확정, 순차 Phase 진행 (1→5), 품질 우선 합의. Phase 0 (sector data) 삭제. |
