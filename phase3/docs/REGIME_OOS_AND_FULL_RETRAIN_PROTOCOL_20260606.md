# Regime OOS and Full-Retrain Protocol

Date: 2026-06-06

Purpose: keep the signal-promotion rule explicit before future V2-breakthrough work.

## Core Rule

For research signals, validate the recipe on OOS windows first. If the recipe passes, generate the production candidate by retraining the same recipe on the full available range.

Do not treat the full-range production signal itself as OOS-clean. It is the deployable artifact after recipe validation.

## Validation Layers

1. Chronological OOS
   - Train only on data before the evaluation window.
   - This answers: would the recipe have worked at that time without seeing the future?

2. Regime OOS
   - Validate the same recipe on multiple regime-shaped windows.
   - Current important windows:
     - Side-heavy OOS candidate: `2021-01-01 -> 2022-12-31` (`F2`)
     - Stronger side/stress holdout candidate: `2020-07-01 -> 2023-03-31`
     - Recent bull/latest OOS candidate: `2024-06-01 -> latest` (`F4`)
   - A candidate that only works on `F4` is not enough for promotion.

3. Purged Regime Holdout
   - Optional robustness test.
   - Example: train on `2011-2020 + 2023-latest`, hold out `2021-2022`.
   - This is not chronological OOS, but it tests whether the recipe generalizes when a side-heavy block is hidden.

4. Full-Range Retrain
   - After recipe validation, retrain the same recipe through the latest valid label date.
   - This creates the production candidate.
   - Then run live-like portfolio simulation and stateful ledger checks.

## Promotion Implication

The preferred promotion path is:

1. OOS recipe validation
2. Regime OOS sanity: bull/latest plus side-heavy
3. Axis-shift and residual-alpha checks versus current live/shadow
4. Full-range retrain with the same recipe
5. Full-range portfolio simulation and recent stateful ledger comparison
6. Shadow/live promotion decision

## Rank Velocity Research Note

Rank-velocity alpha should be tested as a small socket on top of a strong anchor first, not as a replacement core.

The highest-ROI path is:

1. Build rank-velocity labels/features from frozen baseline score panels.
2. Train an ML detector for useful fast-risers and false-riser avoidance.
3. Apply it as conditional boost/sleeve to V2/P2/Shadow-style anchors.
4. Validate on side-heavy and bull/latest OOS windows before any full-range production retrain.
