"""
Phase 1 Signal Lab — pre-search signal candidate evaluation and selection.

Generates hand-designed signal candidates, evaluates each on Phase 1 metrics
(MeanIC, Spread, PosICRatio, regime splits, bucket decomposition), ranks them,
and optionally selects a winner frozen signal for the downstream pipeline.

Usage flow:
    prepare_inputs → run_signal_lab → run_search_from_pack → render_reports

When ``cfg.enable_signal_lab`` is False the lab is skipped entirely and the
existing pipeline behaviour is preserved unchanged.
"""
from __future__ import annotations

import gc
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════
# Factor semantic groups  (keys → factor names in INDICATOR_NAMES)
# ═══════════════════════════════════════════════════════════════════════════
_FACTOR_GROUPS: Dict[str, List[str]] = {
    "MOMENTUM":       ["MOM_3M", "MOM_6M", "MOM_12M_EX1M", "ROC"],
    "BREAKOUT":       ["BREAKOUT_252", "BREAKOUT_126", "HIGH_20_BREAK"],
    "TREND":          ["SMA_CROSS", "SMA50_SLOPE", "MACD", "ADX",
                       "DIST_FROM_SMA50", "RSI_TREND"],
    "MEAN_REVERSION": ["RSI", "BBP", "STOCH", "WILLR", "CCI"],
    "VOLUME":         ["VOL_SPIKE", "OBV_POS", "MFI", "VWAP_ABOVE"],
    "VOLATILITY":     ["ATR_LOW"],
    "VALUE":          ["VAL_EARN_YIELD", "VAL_BOOK2PRICE"],
    "QUALITY":        ["QUAL_ROE", "CF_FCF_YIELD"],
    "LEVERAGE":       ["LEV_DEBT_EQUITY"],
    "INTERACTION_MOM": [
        "QUAL_ROE_X_MOM_6M", "VAL_EARN_YIELD_X_MOM_6M",
        "CF_FCF_YIELD_X_MOM_6M", "VAL_BOOK2PRICE_X_MOM_6M",
        "MOM_12M_EX1M_X_QUAL_ROE", "LEV_DEBT_EQUITY_X_MOM_6M",
    ],
    "INTERACTION_BREAKOUT": [
        "QUAL_ROE_X_BREAKOUT_126", "BREAKOUT_252_X_CF_FCF_YIELD",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# Hand-designed candidate templates
#
# Design principle: base_weight=0.0 → sparse templates.
# Only factors explicitly listed in *_groups get non-zero weight.
# This mirrors how the GA succeeds: it selects ~8 focused factors,
# not all-19 equal-weighted.  Templates differ by which factor subsets
# they concentrate on.
# ═══════════════════════════════════════════════════════════════════════════
def _default_candidate_templates() -> List[Dict[str, Any]]:
    return [
        # ── 1. MOM_BREAK_QUALITY_CORE ─────────────────────────────────────
        # Mimics what the GA historically selects:
        # momentum/breakout in bull, quality×momentum interactions everywhere.
        {
            "name": "MOM_BREAK_QUALITY_CORE",
            "description": "GA-like: momentum+breakout bull, interaction terms heavy, quality anchors",
            "alpha": 0.55,
            "base_weight": 0.0,
            "bull_groups": {
                "MOMENTUM": 3.0, "BREAKOUT": 3.0,
                "TREND": 2.0,
                "INTERACTION_MOM": 2.5, "INTERACTION_BREAKOUT": 2.5,
                "QUALITY": 1.5,
            },
            "side_groups": {
                "MEAN_REVERSION": 3.0, "VALUE": 2.0,
                "INTERACTION_MOM": 2.0,
                "QUALITY": 1.5, "VOLUME": 1.5,
            },
            "def_groups": {
                "QUALITY": 3.0, "VALUE": 2.5,
                "INTERACTION_MOM": 2.0,
                "LEVERAGE": 1.5,
            },
        },
        # ── 2. INTERACTION_FOCUSED ────────────────────────────────────────
        # Pure interaction-term strategy.
        # QUAL_ROE_X_BREAKOUT_126, VAL_EARN_YIELD_X_MOM_6M, etc.
        # are known to be strongly predictive.
        {
            "name": "INTERACTION_FOCUSED",
            "description": "Heavy on quality×momentum interaction cross-terms",
            "alpha": 0.50,
            "base_weight": 0.0,
            "bull_groups": {
                "INTERACTION_MOM": 4.0, "INTERACTION_BREAKOUT": 4.0,
                "QUALITY": 2.0, "MOMENTUM": 1.5,
            },
            "side_groups": {
                "INTERACTION_MOM": 3.5, "INTERACTION_BREAKOUT": 3.0,
                "VALUE": 2.0, "MEAN_REVERSION": 1.5,
            },
            "def_groups": {
                "INTERACTION_MOM": 3.0, "INTERACTION_BREAKOUT": 2.5,
                "QUALITY": 2.5, "VALUE": 2.0,
            },
        },
        # ── 3. TREND_QUALITY_BLEND ────────────────────────────────────────
        # Focus on trend-following + quality anchors.
        # DIST_FROM_SMA50, RSI_TREND, MACD + QUAL_ROE, VAL_EARN_YIELD.
        {
            "name": "TREND_QUALITY_BLEND",
            "description": "Trend + quality blend: DIST_FROM_SMA50, RSI_TREND, QUAL_ROE, VAL_EARN_YIELD",
            "alpha": 0.55,
            "base_weight": 0.0,
            "bull_groups": {
                "TREND": 3.0, "QUALITY": 2.5, "VALUE": 2.5,
                "BREAKOUT": 2.0, "MOMENTUM": 1.5,
                "INTERACTION_MOM": 2.0,
            },
            "side_groups": {
                "VALUE": 3.0, "QUALITY": 2.5,
                "MEAN_REVERSION": 2.0, "TREND": 1.5,
            },
            "def_groups": {
                "QUALITY": 3.5, "VALUE": 3.0,
                "LEVERAGE": 2.0, "VOLATILITY": 1.5,
            },
        },
        # ── 4. MOMENTUM_PURE ──────────────────────────────────────────────
        # Aggressive momentum in bull, hard mean-reversion in side.
        # Zero quality/value factors in bull.
        {
            "name": "MOMENTUM_PURE",
            "description": "Aggressive momentum/breakout in bull; pure mean-rev in side",
            "alpha": 0.60,
            "base_weight": 0.0,
            "bull_groups": {
                "MOMENTUM": 4.0, "BREAKOUT": 4.0,
                "TREND": 2.5,
            },
            "side_groups": {
                "MEAN_REVERSION": 4.0, "VOLUME": 2.5,
                "VALUE": 1.5,
            },
            "def_groups": {
                "QUALITY": 3.0, "VALUE": 2.5,
                "VOLATILITY": 2.0, "LEVERAGE": 1.5,
            },
        },
        # ── 5. BALANCED_CORE_8 ────────────────────────────────────────────
        # Deliberately selects ~8 diverse factors (no all-19 dilution).
        # Equal-ish weighting but only on the most reliable factor classes.
        {
            "name": "BALANCED_CORE_8",
            "description": "8-factor balanced: 2 momentum, 2 breakout, 2 interaction, 2 quality",
            "alpha": 0.55,
            "base_weight": 0.0,
            "bull_groups": {
                "MOMENTUM": 2.0, "BREAKOUT": 2.0,
                "INTERACTION_MOM": 2.0, "INTERACTION_BREAKOUT": 2.0,
                "QUALITY": 1.5, "VALUE": 1.5,
                "TREND": 1.5,
            },
            "side_groups": {
                "MEAN_REVERSION": 2.5, "VALUE": 2.5,
                "QUALITY": 2.0, "VOLUME": 1.5,
                "INTERACTION_MOM": 1.5,
            },
            "def_groups": {
                "QUALITY": 3.0, "VALUE": 2.5,
                "INTERACTION_MOM": 2.0, "LEVERAGE": 1.5,
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Weight builder
# ═══════════════════════════════════════════════════════════════════════════
def _build_weight_from_groups(
    indicator_names: List[str],
    group_weights: Dict[str, float],
    base_weight: float = 1.0,
) -> np.ndarray:
    """Build a factor-weight vector using semantic group multipliers.

    Factors not covered by any specified group receive ``base_weight``.
    Set ``base_weight=0.0`` for sparse templates that only activate
    explicitly specified factors (mirrors GA-style factor selection).
    """
    w = np.full(len(indicator_names), base_weight, dtype=np.float64)
    name_to_idx = {n: i for i, n in enumerate(indicator_names)}
    for group, multiplier in group_weights.items():
        if group in _FACTOR_GROUPS:
            for fname in _FACTOR_GROUPS[group]:
                if fname in name_to_idx:
                    w[name_to_idx[fname]] = multiplier
        elif group in name_to_idx:
            w[name_to_idx[group]] = multiplier
    return w


def _normalize_weights(w: np.ndarray) -> np.ndarray:
    s = w.sum()
    if s > 1e-12:
        return w / s
    return w


# ═══════════════════════════════════════════════════════════════════════════
# 1. build_signal_candidates
# ═══════════════════════════════════════════════════════════════════════════
def build_signal_candidates(
    ctx: Any,
    pack: dict,
    regime_by_date: Optional[Dict[str, str]],
    cfg: Any,
) -> List[Dict[str, Any]]:
    """Generate signal candidates from hand-designed templates."""
    indicator_names: List[str] = list(ctx.INDICATOR_NAMES)
    n_fac = len(indicator_names)

    regime_masks = ctx.build_regime_allowed_masks(cfg)
    allowed_bull = np.asarray(regime_masks["BULL"], dtype=bool)
    allowed_side = np.asarray(regime_masks["SIDE"], dtype=bool)
    allowed_def = np.asarray(regime_masks["DEFENSIVE"], dtype=bool)

    templates = _default_candidate_templates()
    top_k = int(getattr(cfg, "signal_lab_top_k", 5))
    templates = templates[:top_k]

    candidates: List[Dict[str, Any]] = []
    for tpl in templates:
        bw = float(tpl.get("base_weight", 1.0))  # sparse templates use 0.0
        w_bull = _build_weight_from_groups(indicator_names, tpl.get("bull_groups", {}), base_weight=bw)
        w_side = _build_weight_from_groups(indicator_names, tpl.get("side_groups", {}), base_weight=bw)
        w_def = _build_weight_from_groups(indicator_names, tpl.get("def_groups", {}), base_weight=bw)

        mask_bull = (w_bull > 0) & allowed_bull
        mask_side = (w_side > 0) & allowed_side
        mask_def = (w_def > 0) & allowed_def

        w_bull = _normalize_weights(w_bull * mask_bull.astype(np.float64))
        w_side = _normalize_weights(w_side * mask_side.astype(np.float64))
        w_def = _normalize_weights(w_def * mask_def.astype(np.float64))

        alpha = float(tpl.get("alpha", 0.55))

        candidates.append({
            "name": tpl["name"],
            "description": tpl["description"],
            "mask_bull": mask_bull,
            "mask_side": mask_side,
            "mask_def": mask_def,
            "w_bull": w_bull,
            "w_side": w_side,
            "w_def": w_def,
            "alpha": alpha,
            "mask_union": mask_bull | mask_side | mask_def,
            "n_factors": {
                "bull": int(mask_bull.sum()),
                "side": int(mask_side.sum()),
                "def": int(mask_def.sum()),
                "union": int((mask_bull | mask_side | mask_def).sum()),
            },
        })

    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# 2. evaluate_signal_candidates
# ═══════════════════════════════════════════════════════════════════════════
def _evaluate_single(
    ctx: Any,
    pack: dict,
    regime_by_date: Optional[Dict[str, str]],
    cfg: Any,
    cand: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate one candidate via the existing evaluation function."""
    fit, meta, ts_df, regime_tbl, scoring_regime_tbl, invest_tbl = \
        ctx.evaluate_individual_qresearch(
            pack=pack, cfg=cfg,
            mask_bull=cand["mask_bull"],
            mask_side=cand["mask_side"],
            mask_def=cand["mask_def"],
            w_bull=cand["w_bull"],
            w_side=cand["w_side"],
            w_def=cand["w_def"],
            alpha=cand["alpha"],
            regime_by_date=regime_by_date,
            lightweight=False,
        )
    mic1 = float(meta.get("mean_ic_1m", np.nan))
    mic3 = float(meta.get("mean_ic_3m", np.nan))
    mean_ic = np.nanmean([mic1, mic3]) if np.isfinite(mic1) or np.isfinite(mic3) else np.nan
    spread = float(meta.get("mean_spread_mix", np.nan))
    pos_ic = float(meta.get("positive_ic_ratio", np.nan))

    regime_ic = {}
    regime_spread = {}
    if scoring_regime_tbl is not None and not scoring_regime_tbl.empty:
        for _, row in scoring_regime_tbl.iterrows():
            rg = str(row.get("ScoringRegime", ""))
            regime_ic[rg] = float(pd.to_numeric(row.get("MeanIC_1M", np.nan), errors="coerce"))
            regime_spread[rg] = float(pd.to_numeric(row.get("MeanSpreadMix", np.nan), errors="coerce"))

    return {
        "name": cand["name"],
        "fitness": fit,
        "MeanIC": mean_ic,
        "MeanIC_1M": mic1,
        "MeanIC_3M": mic3,
        "Spread": spread,
        "PosICRatio": pos_ic,
        "regime_ic": regime_ic,
        "regime_spread": regime_spread,
        "n_factors": cand["n_factors"],
        "alpha": cand["alpha"],
        "meta": meta,
        "ts_df": ts_df,
        "regime_tbl": regime_tbl,
        "scoring_regime_tbl": scoring_regime_tbl,
        "invest_tbl": invest_tbl,
    }


def _compute_bucket_stats(
    ctx: Any,
    pack: dict,
    regime_by_date: Optional[Dict[str, str]],
    cfg: Any,
    cand: Dict[str, Any],
    n_buckets: int = 5,
) -> pd.DataFrame:
    """Quintile/decile decomposition of forward returns by score bucket."""
    dates = list(pack["dates"])
    fwd1 = pack["fwd1"]
    tradable = pack["tradable"]
    D = len(dates)
    horizon_3m = int(getattr(cfg, "horizon_3m", 63))

    eval_dates_set = set(ctx._build_schedule(dates, cfg.eval_freq))

    active_w_bull = ctx.get_regime_active_weight_vector(
        cfg, cand["mask_bull"], cand["mask_side"], cand["mask_def"],
        cand["w_bull"], cand["w_side"], cand["w_def"], "BULL")
    active_w_side = ctx.get_regime_active_weight_vector(
        cfg, cand["mask_bull"], cand["mask_side"], cand["mask_def"],
        cand["w_bull"], cand["w_side"], cand["w_def"], "SIDE")
    active_w_def = ctx.get_regime_active_weight_vector(
        cfg, cand["mask_bull"], cand["mask_side"], cand["mask_def"],
        cand["w_bull"], cand["w_side"], cand["w_def"], "DEFENSIVE")

    bucket_accum: Dict[int, List[float]] = {b: [] for b in range(n_buckets)}

    for di, d in enumerate(dates):
        if d not in eval_dates_set:
            continue
        if di + horizon_3m >= D:
            continue

        diag_regime = str(regime_by_date.get(d, "SIDE")) if regime_by_date else "SIDE"
        score_regime = ctx._collapse_diag_regime_to_scoring(diag_regime)

        svec = ctx.score_vector_for_day(
            pack, di,
            cand["mask_bull"], cand["mask_side"], cand["mask_def"],
            active_w_bull, active_w_side, active_w_def,
            score_regime=score_regime, cfg=cfg,
        )
        valid = tradable[di] & np.isfinite(svec) & np.isfinite(fwd1[di])
        n_valid = int(valid.sum())
        if n_valid < n_buckets * 5:
            continue

        scores = svec[valid]
        fwd = fwd1[di][valid]
        order = np.argsort(-scores)
        bucket_size = n_valid / n_buckets
        for b in range(n_buckets):
            lo = int(b * bucket_size)
            hi = int((b + 1) * bucket_size) if b < n_buckets - 1 else n_valid
            idx = order[lo:hi]
            if len(idx) > 0:
                bucket_accum[b].append(float(np.mean(fwd[idx])))

    label_prefix = "Q" if n_buckets == 5 else "D"
    rows = []
    for b in range(n_buckets):
        rets = bucket_accum[b]
        rows.append({
            "Bucket": f"{label_prefix}{b + 1}",
            "MeanFwdReturn_1M": float(np.nanmean(rets)) if rets else np.nan,
            "StdFwdReturn_1M": float(np.nanstd(rets)) if rets else np.nan,
            "DateCount": len(rets),
        })
    df = pd.DataFrame(rows)
    if len(df) >= 2:
        df.loc[len(df)] = {
            "Bucket": "TopMinusBottom",
            "MeanFwdReturn_1M": float(df.iloc[0]["MeanFwdReturn_1M"] - df.iloc[-1]["MeanFwdReturn_1M"]),
            "StdFwdReturn_1M": np.nan,
            "DateCount": min(df["DateCount"].iloc[0], df["DateCount"].iloc[-1]),
        }
    return df


def evaluate_signal_candidates(
    ctx: Any,
    pack: dict,
    regime_by_date: Optional[Dict[str, str]],
    cfg: Any,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate all candidates on Phase 1 metrics + bucket analysis."""
    n_buckets = int(getattr(cfg, "signal_lab_bucket_n", 5))
    use_regime = bool(getattr(cfg, "signal_lab_use_regime_split", True))

    evals: List[Dict[str, Any]] = []
    bucket_tables: Dict[str, pd.DataFrame] = {}

    for i, cand in enumerate(candidates):
        t0 = time.perf_counter()
        print(f"  [SignalLab] evaluating {i + 1}/{len(candidates)}: {cand['name']}...", end="", flush=True)

        ev = _evaluate_single(ctx, pack, regime_by_date, cfg, cand)
        bkt = _compute_bucket_stats(ctx, pack, regime_by_date, cfg, cand, n_buckets)

        ev["bucket_df"] = bkt
        tail_spread = np.nan
        if len(bkt) > 1 and "TopMinusBottom" in bkt["Bucket"].values:
            tmb = bkt.loc[bkt["Bucket"] == "TopMinusBottom", "MeanFwdReturn_1M"]
            if not tmb.empty:
                tail_spread = float(tmb.iloc[0])
        ev["TailSpread"] = tail_spread

        evals.append(ev)
        bucket_tables[cand["name"]] = bkt
        dt = time.perf_counter() - t0
        print(f" MeanIC={ev['MeanIC']:.4f}  Spread={ev['Spread']:.4f}  PosIC={ev['PosICRatio']:.3f}"
              f"  TailSpread={tail_spread:.4f}  ({dt:.1f}s)")

        pack.pop("_score_vector_cache", None)
        gc.collect()

    return {"evals": evals, "bucket_tables": bucket_tables}


# ═══════════════════════════════════════════════════════════════════════════
# 3. rank_signal_candidates
# ═══════════════════════════════════════════════════════════════════════════
def rank_signal_candidates(
    eval_bundle: Dict[str, Any],
    cfg: Any,
) -> pd.DataFrame:
    """Rank evaluated candidates by a composite score."""
    primary = str(getattr(cfg, "signal_lab_primary_metric", "MeanIC"))
    use_tail_bonus = bool(getattr(cfg, "signal_lab_use_tail_separation_bonus", True))
    use_turnover_pen = bool(getattr(cfg, "signal_lab_use_turnover_proxy_penalty", False))

    rows = []
    for ev in eval_bundle["evals"]:
        mic = float(ev.get("MeanIC", np.nan))
        sp = float(ev.get("Spread", np.nan))
        pic = float(ev.get("PosICRatio", np.nan))
        ts = float(ev.get("TailSpread", np.nan))

        # Composite score: weighted blend
        composite = 0.0
        w_total = 0.0
        for metric, weight in [("MeanIC", 0.40), ("Spread", 0.30), ("PosICRatio", 0.15), ("TailSpread", 0.15)]:
            val = float(ev.get(metric, np.nan))
            if np.isfinite(val):
                composite += weight * val
                w_total += weight
        if w_total > 0:
            composite /= w_total
        else:
            composite = np.nan

        if use_tail_bonus and np.isfinite(ts) and ts > 0:
            composite += 0.05 * ts

        rows.append({
            "Name": ev["name"],
            "MeanIC": mic,
            "MeanIC_1M": ev.get("MeanIC_1M", np.nan),
            "MeanIC_3M": ev.get("MeanIC_3M", np.nan),
            "Spread": sp,
            "PosICRatio": pic,
            "TailSpread": ts,
            "Composite": composite,
            "Alpha": ev.get("alpha", np.nan),
            "N_Factors_Union": ev.get("n_factors", {}).get("union", 0),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Composite", ascending=False).reset_index(drop=True)
        df.insert(0, "Rank", range(1, len(df) + 1))
    return df


# ═══════════════════════════════════════════════════════════════════════════
# 4. select_frozen_signal
# ═══════════════════════════════════════════════════════════════════════════
def select_frozen_signal(
    ctx: Any,
    eval_bundle: Dict[str, Any],
    rank_df: pd.DataFrame,
    cfg: Any,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Select winner and optionally save as frozen signal .npz."""
    if rank_df.empty:
        return {"selected": False, "reason": "empty_rank"}

    winner_name = str(rank_df.iloc[0]["Name"])
    winner_ev = None
    winner_cand = None
    for ev in eval_bundle["evals"]:
        if ev["name"] == winner_name:
            winner_ev = ev
            break
    for c in candidates:
        if c["name"] == winner_name:
            winner_cand = c
            break

    if winner_ev is None or winner_cand is None:
        return {"selected": False, "reason": "winner_not_found"}

    min_ic = float(getattr(cfg, "signal_lab_min_mean_ic", 0.0))
    min_sp = float(getattr(cfg, "signal_lab_min_spread", 0.0))

    mic = float(winner_ev.get("MeanIC", np.nan))
    sp = float(winner_ev.get("Spread", np.nan))
    passes_min = (np.isfinite(mic) and mic >= min_ic) and (np.isfinite(sp) and sp >= min_sp)

    result = {
        "selected": True,
        "name": winner_name,
        "MeanIC": mic,
        "Spread": sp,
        "PosICRatio": float(winner_ev.get("PosICRatio", np.nan)),
        "passes_min": passes_min,
        "mask_union": winner_cand["mask_union"],
        "mask_bull": winner_cand["mask_bull"],
        "mask_side": winner_cand["mask_side"],
        "mask_def": winner_cand["mask_def"],
        "w_bull": winner_cand["w_bull"],
        "w_side": winner_cand["w_side"],
        "w_def": winner_cand["w_def"],
        "alpha": winner_cand["alpha"],
    }

    if bool(getattr(cfg, "signal_lab_save_tables", True)):
        save_dir = str(getattr(cfg, "save_dir", "."))
        ts = time.strftime("%Y%m%d_%H%M%S")
        npz_path = os.path.join(save_dir, f"frozen_signal_lab_{winner_name}_{ts}.npz")
        summary = {
            "SignalLab_Winner": winner_name,
            "Invest_MeanIC": mic,
            "Invest_Spread": sp,
            "Invest_PosICRatio": float(winner_ev.get("PosICRatio", np.nan)),
        }
        np.savez(
            npz_path,
            mask=winner_cand["mask_union"],
            wb=winner_cand["w_bull"],
            ws=winner_cand["w_side"],
            wd=winner_cand["w_def"],
            signal_summary=json.dumps(summary),
        )
        result["npz_path"] = npz_path
        print(f"  [SignalLab] frozen signal saved: {npz_path}")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 5. Report table builders
# ═══════════════════════════════════════════════════════════════════════════
def build_signal_lab_report_tables(
    eval_bundle: Dict[str, Any],
    rank_df: pd.DataFrame,
    winner_info: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """Build report DataFrames for Excel export."""
    tables: Dict[str, pd.DataFrame] = {}

    # --- SignalLab_Summary ---
    tables["SignalLab_Summary"] = rank_df.copy() if not rank_df.empty else pd.DataFrame()

    # --- SignalLab_Regime ---
    regime_rows = []
    for ev in eval_bundle["evals"]:
        for rg in ["BULL", "SIDE", "DEFENSIVE"]:
            regime_rows.append({
                "Candidate": ev["name"],
                "Regime": rg,
                "MeanIC_1M": ev.get("regime_ic", {}).get(rg, np.nan),
                "MeanSpreadMix": ev.get("regime_spread", {}).get(rg, np.nan),
            })
    tables["SignalLab_Regime"] = pd.DataFrame(regime_rows)

    # --- SignalLab_Bucket ---
    bucket_rows = []
    for ev in eval_bundle["evals"]:
        bkt = ev.get("bucket_df", pd.DataFrame())
        if bkt is not None and not bkt.empty:
            bkt_copy = bkt.copy()
            bkt_copy.insert(0, "Candidate", ev["name"])
            bucket_rows.append(bkt_copy)
    tables["SignalLab_Bucket"] = pd.concat(bucket_rows, ignore_index=True) if bucket_rows else pd.DataFrame()

    # --- SignalLab_Winner ---
    winner_rows = [
        {"Key": "Winner", "Value": str(winner_info.get("name", ""))},
        {"Key": "MeanIC", "Value": f"{winner_info.get('MeanIC', np.nan):.6f}"},
        {"Key": "Spread", "Value": f"{winner_info.get('Spread', np.nan):.6f}"},
        {"Key": "PosICRatio", "Value": f"{winner_info.get('PosICRatio', np.nan):.4f}"},
        {"Key": "PassesMinimum", "Value": str(winner_info.get("passes_min", False))},
        {"Key": "NPZ_Path", "Value": str(winner_info.get("npz_path", ""))},
    ]
    tables["SignalLab_Winner"] = pd.DataFrame(winner_rows)

    return tables


# ═══════════════════════════════════════════════════════════════════════════
# 6. Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════
def run_signal_lab(
    ctx: Any,
    pack: dict,
    regime_by_date: Optional[Dict[str, str]],
    cfg: Any,
) -> Dict[str, Any]:
    """Run the full Signal Lab pipeline.

    Returns a dict with:
      - ``"signal_lab_tables"``: report tables for Excel
      - ``"winner"``: winner info dict (or empty if no winner)
      - ``"rank_df"``: ranking DataFrame
      - ``"mode"``: the lab mode used
    """
    mode = str(getattr(cfg, "signal_lab_mode", "DIAGNOSTIC"))
    print(f"\n{'='*60}")
    print(f"[SignalLab] Phase 1 Signal Lab  mode={mode}")
    print(f"{'='*60}")

    t0 = time.perf_counter()

    candidates = build_signal_candidates(ctx, pack, regime_by_date, cfg)
    print(f"[SignalLab] {len(candidates)} candidates generated")
    for c in candidates:
        nf = c["n_factors"]
        print(f"  {c['name']}: bull={nf['bull']} side={nf['side']} def={nf['def']} union={nf['union']}")

    eval_bundle = evaluate_signal_candidates(ctx, pack, regime_by_date, cfg, candidates)

    rank_df = rank_signal_candidates(eval_bundle, cfg)
    print(f"\n[SignalLab] Ranking:")
    print(rank_df.to_string(index=False))

    winner_info = select_frozen_signal(ctx, eval_bundle, rank_df, cfg, candidates)

    tables = build_signal_lab_report_tables(eval_bundle, rank_df, winner_info)

    dt = time.perf_counter() - t0
    print(f"\n[SignalLab] completed in {dt:.1f}s  winner={winner_info.get('name', 'N/A')}")
    print(f"  MeanIC={winner_info.get('MeanIC', np.nan):.4f}  "
          f"Spread={winner_info.get('Spread', np.nan):.4f}  "
          f"PosIC={winner_info.get('PosICRatio', np.nan):.3f}")
    print(f"{'='*60}\n")

    pack.pop("_score_vector_cache", None)
    gc.collect()

    return {
        "signal_lab_tables": tables,
        "winner": winner_info,
        "rank_df": rank_df,
        "mode": mode,
        "elapsed_sec": dt,
    }
