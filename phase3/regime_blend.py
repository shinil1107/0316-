"""Regime blending: hysteresis + soft interpolation between regimes.

Replaces hard VIX-threshold step-function regime switching with:
1. Hysteresis — regime label only changes when VIX clearly exits the
   transition zone, preventing chattering of discrete parameters.
2. Soft blend — scoring weight vectors are interpolated in the transition
   zone so IC/spread measurements have no discontinuity at boundaries.
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple


_DEFAULTS = dict(
    bull_threshold=18.0,
    def_threshold=30.0,
    bull_side_blend_width=2.0,
    side_def_blend_width=3.0,
    cb_threshold=35.0,
    cb_enabled=True,
)


def _params(cfg_or_dict=None):
    """Extract blend parameters from a config dict or object."""
    if cfg_or_dict is None:
        return dict(_DEFAULTS)
    if isinstance(cfg_or_dict, dict):
        d = cfg_or_dict
    else:
        d = {k: getattr(cfg_or_dict, k, v) for k, v in _DEFAULTS.items()}
    return {k: d.get(k, _DEFAULTS[k]) for k in _DEFAULTS}


def compute_blend_alphas(
    vix: float,
    bull_threshold: float = 18.0,
    def_threshold: float = 30.0,
    bull_side_blend_width: float = 2.0,
    side_def_blend_width: float = 3.0,
    **_kw,
) -> Tuple[float, float, float]:
    """Return (alpha_bull, alpha_side, alpha_def) that sum to 1.0.

    Transition zones:
      BULL ↔ SIDE : [bull_threshold - w, bull_threshold + w]
      SIDE ↔ DEF  : [def_threshold  - w, def_threshold  + w]
    """
    b_lo = bull_threshold - bull_side_blend_width
    b_hi = bull_threshold + bull_side_blend_width
    d_lo = def_threshold - side_def_blend_width
    d_hi = def_threshold + side_def_blend_width

    if vix <= b_lo:
        return (1.0, 0.0, 0.0)
    if b_hi > b_lo and vix < b_hi:
        t = (vix - b_lo) / (b_hi - b_lo)
        return (1.0 - t, t, 0.0)
    if vix <= d_lo:
        return (0.0, 1.0, 0.0)
    if d_hi > d_lo and vix < d_hi:
        t = (vix - d_lo) / (d_hi - d_lo)
        return (0.0, 1.0 - t, t)
    return (0.0, 0.0, 1.0)


def apply_hysteresis(
    prev_regime: str,
    vix: float,
    bull_threshold: float = 18.0,
    def_threshold: float = 30.0,
    bull_side_blend_width: float = 2.0,
    side_def_blend_width: float = 3.0,
    cb_threshold: float = 35.0,
    cb_enabled: bool = True,
    **_kw,
) -> str:
    """Return regime label with hysteresis.

    The label only changes when VIX exits the transition zone completely.
    This prevents chattering of discrete strategy parameters (top_n,
    enable_stop_loss, etc.) near regime boundaries.
    """
    if cb_enabled and vix >= cb_threshold:
        return "CRASH"

    b_lo = bull_threshold - bull_side_blend_width
    b_hi = bull_threshold + bull_side_blend_width
    d_lo = def_threshold - side_def_blend_width
    d_hi = def_threshold + side_def_blend_width

    rg = prev_regime.upper().strip()

    if rg == "BULL":
        if vix >= d_hi:
            return "DEFENSIVE"
        if vix >= b_hi:
            return "SIDE"
        return "BULL"
    elif rg in ("DEFENSIVE", "CRASH"):
        if vix <= b_lo:
            return "BULL"
        if vix <= d_lo:
            return "SIDE"
        return "DEFENSIVE"
    else:  # SIDE or unknown
        if vix <= b_lo:
            return "BULL"
        if vix >= d_hi:
            return "DEFENSIVE"
        return "SIDE"


def blend_weight_vectors(
    w_bull: np.ndarray,
    w_side: np.ndarray,
    w_def: np.ndarray,
    alphas: Tuple[float, float, float],
) -> np.ndarray:
    """Return alpha-weighted combination of three weight vectors."""
    ab, as_, ad = alphas
    return ab * w_bull + as_ * w_side + ad * w_def


def interpolate_param(
    val_bull, val_side, val_def,
    alphas: Tuple[float, float, float],
    as_int: bool = False,
):
    """Interpolate a numeric strategy parameter using blend alphas."""
    ab, as_, ad = alphas
    v = ab * val_bull + as_ * val_side + ad * val_def
    return int(round(v)) if as_int else v


def enrich_regime_timeseries(
    vix_df: pd.DataFrame,
    blend_conf: dict,
) -> pd.DataFrame:
    """Add hysteresis regime and blend alphas to a VIX regime DataFrame.

    Takes the output of engine.build_vix_regime_timeseries() and enriches
    it with ``regime_h`` (hysteresis label) and ``alpha_bull/side/def``
    columns.
    """
    df = vix_df.copy()
    p = _params(blend_conf)

    vix_col = "vix_smooth" if "vix_smooth" in df.columns else "close"

    regimes_h = []
    a_bull, a_side, a_def = [], [], []
    prev = "SIDE"

    for _, row in df.iterrows():
        v = float(row[vix_col])

        regime = apply_hysteresis(prev, v, **p)
        ab, as_, ad = compute_blend_alphas(v, **p)

        regimes_h.append(regime)
        a_bull.append(ab)
        a_side.append(as_)
        a_def.append(ad)

        prev = regime if regime != "CRASH" else "DEFENSIVE"

    df["regime_h"] = regimes_h
    df["alpha_bull"] = a_bull
    df["alpha_side"] = a_side
    df["alpha_def"] = a_def
    return df
