from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def run_search_from_pack(ctx: Any, pack: dict, regime_by_date: Optional[Dict[str, str]], cfg: Any) -> Dict[str, Any]:
    meta_t0 = time.perf_counter()
    meta_summary_df = pd.DataFrame()
    meta_top_df = pd.DataFrame()
    meta_stability_df = pd.DataFrame()
    best_meta_cfg: Optional[Dict[str, Any]] = None
    best_meta_row: Optional[Dict[str, Any]] = None
    meta_results: List[Dict[str, Any]] = []

    if bool(cfg.enable_meta_search):
        (
            meta_summary_df,
            meta_top_df,
            meta_stability_df,
            best_meta_cfg,
            best_meta_row,
            meta_results,
        ) = ctx.run_meta_search_outer_loop(pack=pack, cfg=cfg, regime_by_date=regime_by_date)
        ctx.print_meta_search_console_summary(meta_summary_df, meta_top_df)
    else:
        single_template = str(getattr(cfg, "meta_disabled_template_name", "TPL_BALANCED") or "TPL_BALANCED")
        print(f"[Meta Search] disabled -> using template={single_template}")
        best_meta_cfg = ctx.build_meta_config_from_template(cfg, single_template, meta_id="META_SINGLE", source="NO_META_SEARCH")
        best_meta_cfg["alpha_floor"] = float(getattr(cfg, "ga_alpha_floor", best_meta_cfg.get("alpha_floor", 0.0)))
        best_meta_cfg["top_quantile"] = float(cfg.top_quantile)
        best_meta_cfg["factor_corr_penalty_lambda"] = float(cfg.factor_corr_penalty_lambda)
        best_meta_cfg["entropy_bonus"] = float(cfg.entropy_bonus)
        best_meta_cfg["conc_penalty"] = float(cfg.conc_penalty)
        best_meta_cfg = ctx._normalize_meta_config(best_meta_cfg)
        best_meta_row = {
            "MetaID": "META_SINGLE",
            "Source": "NO_META_SEARCH",
            "TemplateName": single_template,
            "MetaScore": np.nan,
        }

    meta_sec = float(time.perf_counter() - meta_t0)

    ga_t0 = time.perf_counter()
    final_cfg = ctx.apply_meta_config_to_cfg(cfg, best_meta_cfg)
    if final_cfg.use_random_seed:
        run_seed = int(time.time_ns() % (2**32 - 1))
    else:
        run_seed = int(final_cfg.ga_seed)

    (
        best_rule_table,
        ga_summary,
        best_ts_df,
        best_cfg_df,
        best_regime_tbl,
        best_scoring_regime_tbl,
        best_invest_tbl,
        stability_tbl,
        stability_seed_summary,
        (best_mask, best_wb, best_ws, best_wd, best_alpha),
    ) = ctx.run_ga_qresearch(
        pack=pack,
        cfg=final_cfg,
        run_seed=run_seed,
        regime_by_date=regime_by_date,
    )
    ga_sec = float(time.perf_counter() - ga_t0)

    if final_cfg.enable_stability_layer and stability_tbl is not None and not stability_tbl.empty:
        stable_cnt = int(pd.to_numeric(stability_tbl["StableFlag"], errors="coerce").fillna(False).astype(bool).sum())
        print(f"[Stability] stable_factor_count={stable_cnt}")

    return {
        "meta_summary_df": meta_summary_df,
        "meta_top_df": meta_top_df,
        "meta_stability_df": meta_stability_df,
        "best_meta_cfg": best_meta_cfg,
        "best_meta_row": best_meta_row,
        "meta_results": meta_results,
        "meta_sec": meta_sec,
        "final_cfg": final_cfg,
        "run_seed": run_seed,
        "best_rule_table": best_rule_table,
        "ga_summary": ga_summary,
        "best_ts_df": best_ts_df,
        "best_cfg_df": best_cfg_df,
        "best_regime_tbl": best_regime_tbl,
        "best_scoring_regime_tbl": best_scoring_regime_tbl,
        "best_invest_tbl": best_invest_tbl,
        "stability_tbl": stability_tbl,
        "stability_seed_summary": stability_seed_summary,
        "best_mask": best_mask,
        "best_wb": best_wb,
        "best_ws": best_ws,
        "best_wd": best_wd,
        "best_alpha": best_alpha,
        "ga_sec": ga_sec,
    }
