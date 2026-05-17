# 급등 Ticker Score 상관성 분석 — 2026-05-06 03:29

- **Surge threshold**: forward return >= +20%
- **Horizons**: 5d, 10d, 21d
- **Pre-event lookbacks**: t-0, t-5, t-10

## 5-Day Horizon

| Signal | Surge Rate | Top-10% Precision | Lift | Top-10% Drop | Surge Events |
|--------|-----------|-------------------|------|-------------|-------------|
| Baseline_V2 | 0.14% | 0.25% | 1.72x | 8.07% | 396 |

### Pre-Event Score Percentile (surge tickers, 5d horizon)

| Signal | t-0 mean | t-0 median | t-0 %>80 | t-5 mean | t-5 median | t-5 %>80 | t-10 mean | t-10 median | t-10 %>80 |
|--------|----------|------------|---------|----------|------------|---------|----------|------------|---------|
| Baseline_V2 | 52.0 | 51.4 | 25.8% | 54.3 | 56.8 | 29.0% | 52.8 | 54.8 | 26.8% |

### Score Quintile → Surge Rate (5d)

**Baseline_V2**

| Quintile | Count | Surges | Surge% | Drop% |
|----------|-------|--------|--------|-------|
| Q1 (bottom) | 54,767 | 91 | 0.17% | 7.86% |
| Q2 | 54,767 | 70 | 0.13% | 6.91% |
| Q3 | 54,767 | 60 | 0.11% | 6.54% |
| Q4 | 54,767 | 70 | 0.13% | 6.42% |
| Q5 (top) | 56,213 | 105 | 0.19% | 7.32% |

## 10-Day Horizon

| Signal | Surge Rate | Top-10% Precision | Lift | Top-10% Drop | Surge Events |
|--------|-----------|-------------------|------|-------------|-------------|
| Baseline_V2 | 0.48% | 0.83% | 1.72x | 13.41% | 1,331 |

### Pre-Event Score Percentile (surge tickers, 10d horizon)

| Signal | t-0 mean | t-0 median | t-0 %>80 | t-5 mean | t-5 median | t-5 %>80 | t-10 mean | t-10 median | t-10 %>80 |
|--------|----------|------------|---------|----------|------------|---------|----------|------------|---------|
| Baseline_V2 | 52.5 | 52.4 | 26.8% | 52.4 | 52.1 | 27.6% | 49.5 | 48.1 | 24.7% |

### Score Quintile → Surge Rate (10d)

**Baseline_V2**

| Quintile | Count | Surges | Surge% | Drop% |
|----------|-------|--------|--------|-------|
| Q1 (bottom) | 54,767 | 282 | 0.51% | 13.51% |
| Q2 | 54,767 | 239 | 0.44% | 12.28% |
| Q3 | 54,767 | 211 | 0.39% | 11.60% |
| Q4 | 54,767 | 235 | 0.43% | 11.85% |
| Q5 (top) | 56,213 | 364 | 0.65% | 12.52% |

## 21-Day Horizon

| Signal | Surge Rate | Top-10% Precision | Lift | Top-10% Drop | Surge Events |
|--------|-----------|-------------------|------|-------------|-------------|
| Baseline_V2 | 1.80% | 2.73% | 1.52x | 19.55% | 4,945 |

### Pre-Event Score Percentile (surge tickers, 21d horizon)

| Signal | t-0 mean | t-0 median | t-0 %>80 | t-5 mean | t-5 median | t-5 %>80 | t-10 mean | t-10 median | t-10 %>80 |
|--------|----------|------------|---------|----------|------------|---------|----------|------------|---------|
| Baseline_V2 | 50.7 | 50.4 | 24.4% | 49.9 | 49.4 | 24.1% | 50.0 | 48.9 | 24.2% |

### Score Quintile → Surge Rate (21d)

**Baseline_V2**

| Quintile | Count | Surges | Surge% | Drop% |
|----------|-------|--------|--------|-------|
| Q1 (bottom) | 54,767 | 1,101 | 2.01% | 19.76% |
| Q2 | 54,767 | 898 | 1.64% | 18.60% |
| Q3 | 54,767 | 859 | 1.57% | 17.74% |
| Q4 | 54,767 | 861 | 1.57% | 17.84% |
| Q5 (top) | 56,213 | 1,226 | 2.18% | 18.59% |

## 해석 가이드

- **Lift > 1.0**: signal의 top-decile이 random보다 급등 포착을 잘 함
- **Lift ≈ 1.0**: score rank와 급등이 무관함 (alpha 부재)
- **Pre-event percentile 높을수록**: 급등 전에 이미 high-score → 예측력 있음
- **Top-decile drop rate 높으면**: false positive 많음 → 순수 momentum 의존?
- **Quintile monotonicity**: Q1→Q5로 surge rate가 단조증가하면 score 자체에 alpha
