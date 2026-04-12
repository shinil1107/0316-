from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict

from .data_pipeline import prepare_inputs
from .report_pipeline import render_reports
from .search_pipeline import run_search_from_pack
from .signal_lab import run_signal_lab


def _build_standalone_search_bundle(
    ctx: Any,
    signal_lab_result: Dict[str, Any],
    cfg: Any,
    pack: dict,
    regime_by_date: Any,
) -> Dict[str, Any]:
    """Build a minimal search_bundle from the Signal Lab winner (no GA).

    The winner's mask/weights are evaluated once with evaluate_individual_qresearch
    to produce Phase 1 tables (ts_df, invest_tbl, regime tables).
    The Phase 1 gate report will then reflect the winner's signal quality.
    """
    winner = signal_lab_result.get("winner", {})
    if not winner.get("selected"):
        raise RuntimeError("[SignalLab STANDALONE] no winner selected — cannot build search bundle")

    mask_bull = np.asarray(winner["mask_bull"], dtype=bool)
    mask_side = np.asarray(winner["mask_side"], dtype=bool)
    mask_def  = np.asarray(winner["mask_def"],  dtype=bool)
    w_bull    = np.asarray(winner["w_bull"],     dtype=np.float64)
    w_side    = np.asarray(winner["w_side"],     dtype=np.float64)
    w_def     = np.asarray(winner["w_def"],      dtype=np.float64)
    alpha     = float(winner["alpha"])

    print(f"[SignalLab STANDALONE] Evaluating winner '{winner['name']}' for Phase 1 report...")
    fit, meta, ts_df, regime_tbl, scoring_regime_tbl, invest_tbl = ctx.evaluate_individual_qresearch(
        pack=pack, cfg=cfg,
        mask_bull=mask_bull, mask_side=mask_side, mask_def=mask_def,
        w_bull=w_bull, w_side=w_side, w_def=w_def,
        alpha=alpha,
        regime_by_date=regime_by_date,
        lightweight=False,
    )

    best_cfg_df = pd.DataFrame([
        {"Key": "mode",               "Value": "SIGNAL_LAB_STANDALONE"},
        {"Key": "winner_name",        "Value": str(winner.get("name", ""))},
        {"Key": "best_fitness",       "Value": str(round(float(fit), 6))},
        {"Key": "best_mean_ic_1m",    "Value": str(round(float(meta.get("mean_ic_1m",    float("nan"))), 6))},
        {"Key": "best_mean_ic_3m",    "Value": str(round(float(meta.get("mean_ic_3m",    float("nan"))), 6))},
        {"Key": "best_mean_spread_mix","Value": str(round(float(meta.get("mean_spread_mix", float("nan"))), 6))},
        {"Key": "best_positive_ic_ratio","Value": str(round(float(meta.get("positive_ic_ratio", float("nan"))), 4))},
    ])

    best_meta_cfg = {
        "meta_id": "SL_STANDALONE",
        "source": "SIGNAL_LAB_STANDALONE",
        "template_name": f"SL_{winner.get('name', '')}",
        "objective_profile": "OBJ_A",
        "regime_profile": "REG_A",
        "side_soft_profile": "SIDE_MID",
        "bull_soft_profile": "BULL_MID",
        "alpha_floor": 0.22,
        "top_quantile": float(getattr(cfg, "top_quantile", 0.20)),
        "factor_corr_penalty_lambda": float(getattr(cfg, "factor_corr_penalty_lambda", 0.08)),
        "entropy_bonus": float(getattr(cfg, "entropy_bonus", 0.08)),
        "conc_penalty": float(getattr(cfg, "conc_penalty", 0.12)),
    }

    return {
        "meta_summary_df":        pd.DataFrame(),
        "meta_top_df":            pd.DataFrame(),
        "meta_stability_df":      pd.DataFrame(),
        "best_meta_cfg":          best_meta_cfg,
        "best_meta_row":          {"MetaID": "SL_STANDALONE", "Source": "SIGNAL_LAB_STANDALONE", "MetaScore": float("nan")},
        "meta_results":           [],
        "meta_sec":               0.0,
        "final_cfg":              cfg,
        "run_seed":               0,
        "best_rule_table":        pd.DataFrame(),
        "ga_summary":             pd.DataFrame(),
        "best_ts_df":             ts_df if ts_df is not None else pd.DataFrame(),
        "best_cfg_df":            best_cfg_df,
        "best_regime_tbl":        regime_tbl        if regime_tbl        is not None else pd.DataFrame(),
        "best_scoring_regime_tbl": scoring_regime_tbl if scoring_regime_tbl is not None else pd.DataFrame(),
        "best_invest_tbl":        invest_tbl        if invest_tbl        is not None else pd.DataFrame(),
        "stability_tbl":          pd.DataFrame(),
        "stability_seed_summary": pd.DataFrame(),
        "best_mask":              mask_bull | mask_side | mask_def,
        "best_wb":                w_bull,
        "best_ws":                w_side,
        "best_wd":                w_def,
        "best_alpha":             alpha,
        "ga_sec":                 0.0,
    }


def run_engine(ctx: Any, cfg: Any) -> str:
    prepared_inputs = prepare_inputs(ctx, cfg)

    signal_lab_result = None
    if bool(getattr(cfg, "enable_signal_lab", False)):
        signal_lab_result = run_signal_lab(
            ctx=ctx,
            pack=prepared_inputs["pack"],
            regime_by_date=prepared_inputs["regime_by_date"],
            cfg=cfg,
        )

    # STANDALONE: skip GA entirely, use Signal Lab winner as the signal
    sl_mode = str(getattr(cfg, "signal_lab_mode", "DIAGNOSTIC")).upper()
    if signal_lab_result is not None and sl_mode == "STANDALONE":
        print("[SignalLab STANDALONE] Skipping GA — using Signal Lab winner as final signal.")
        search_bundle = _build_standalone_search_bundle(
            ctx=ctx,
            signal_lab_result=signal_lab_result,
            cfg=cfg,
            pack=prepared_inputs["pack"],
            regime_by_date=prepared_inputs["regime_by_date"],
        )
    else:
        search_bundle = run_search_from_pack(
            ctx=ctx,
            pack=prepared_inputs["pack"],
            regime_by_date=prepared_inputs["regime_by_date"],
            cfg=cfg,
        )

    if signal_lab_result is not None:
        search_bundle["signal_lab_result"] = signal_lab_result

    return render_reports(ctx, prepared_inputs=prepared_inputs, search_bundle=search_bundle)
