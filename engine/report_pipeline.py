from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict

import pandas as pd


def render_reports(ctx: Any, prepared_inputs: Dict[str, Any], search_bundle: Dict[str, Any]) -> str:
    pack = prepared_inputs["pack"]
    regime_ts = prepared_inputs["regime_ts"]
    regime_by_date = prepared_inputs["regime_by_date"]
    source = prepared_inputs["source"]
    df_timing = prepared_inputs["df_timing"]
    ok = prepared_inputs["ok"]
    fail = prepared_inputs["fail"]
    npz_path = prepared_inputs["npz_path"]
    loaded_from_npz = prepared_inputs["loaded_from_npz"]
    pre_sec = float(prepared_inputs["pre_sec"])
    data_quality_summary_df = prepared_inputs.get("data_quality_summary_df", pd.DataFrame())
    factor_coverage_df = prepared_inputs.get("factor_coverage_df", pd.DataFrame())
    data_quality_sample_df = prepared_inputs.get("data_quality_sample_df", pd.DataFrame())
    historical_universe_debug_summary_df = prepared_inputs.get("historical_universe_debug_summary_df", pd.DataFrame())
    historical_universe_yearly_df = prepared_inputs.get("historical_universe_yearly_df", pd.DataFrame())

    final_cfg = search_bundle["final_cfg"]
    best_mask = search_bundle["best_mask"]
    best_wb = search_bundle["best_wb"]
    best_ws = search_bundle["best_ws"]
    best_wd = search_bundle["best_wd"]
    best_alpha = search_bundle["best_alpha"]
    best_rule_table = search_bundle["best_rule_table"]
    ga_summary = search_bundle["ga_summary"]
    best_ts_df = search_bundle["best_ts_df"]
    best_cfg_df = search_bundle["best_cfg_df"]
    best_regime_tbl = search_bundle["best_regime_tbl"]
    best_scoring_regime_tbl = search_bundle["best_scoring_regime_tbl"]
    best_invest_tbl = search_bundle["best_invest_tbl"]
    stability_tbl = search_bundle["stability_tbl"]
    stability_seed_summary = search_bundle["stability_seed_summary"]
    meta_summary_df = search_bundle["meta_summary_df"]
    meta_top_df = search_bundle["meta_top_df"]
    meta_stability_df = search_bundle["meta_stability_df"]
    best_meta_cfg = search_bundle["best_meta_cfg"]
    best_meta_row = search_bundle["best_meta_row"]
    meta_results = search_bundle["meta_results"]
    meta_sec = float(search_bundle["meta_sec"])
    run_seed = int(search_bundle["run_seed"])
    ga_sec = float(search_bundle["ga_sec"])

    score_1y_long = pd.DataFrame()
    today_top10 = pd.DataFrame()
    live_today_top10 = pd.DataFrame()

    if final_cfg.write_score_1y_long:
        score_1y_long = ctx.build_score_last_year_long(
            pack=pack,
            mask=best_mask,
            w_bull=best_wb,
            w_side=best_ws,
            w_def=best_wd,
            cfg=final_cfg,
            regime_by_date=regime_by_date,
            lookback_calendar_days=365,
        )

    if final_cfg.write_today_top10:
        today_top10 = ctx.build_today_top10(
            pack=pack,
            mask=best_mask,
            w_bull=best_wb,
            w_side=best_ws,
            w_def=best_wd,
            cfg=final_cfg,
            regime_by_date=regime_by_date,
        )

    if bool(final_cfg.write_live_today_top10):
        live_today_top10 = ctx.build_live_today_top10(
            pack=pack,
            mask=best_mask,
            w_bull=best_wb,
            w_side=best_ws,
            w_def=best_wd,
            cfg=final_cfg,
            regime_by_date=regime_by_date,
        )
        ctx.print_live_today_top10_console(live_today_top10)

    portfolio_ts = pd.DataFrame()
    portfolio_report = pd.DataFrame()
    today_portfolio = pd.DataFrame()

    if final_cfg.enable_portfolio_construction:
        portfolio_ts, today_portfolio = ctx.build_portfolio_timeseries(
            pack=pack,
            cfg=final_cfg,
            mask=best_mask,
            w_bull=best_wb,
            w_side=best_ws,
            w_def=best_wd,
            regime_by_date=regime_by_date,
        )
        live_today_portfolio = ctx.build_live_today_portfolio(
            pack=pack,
            cfg=final_cfg,
            mask=best_mask,
            w_bull=best_wb,
            w_side=best_ws,
            w_def=best_wd,
            regime_by_date=regime_by_date,
        )
        if live_today_portfolio is not None and not live_today_portfolio.empty:
            today_portfolio = live_today_portfolio.copy()
        portfolio_report = ctx.build_portfolio_report(final_cfg, portfolio_ts)

        if final_cfg.log_marketcap_source_summary:
            ctx.print_marketcap_source_summary(portfolio_ts)

        if final_cfg.log_portfolio_filter_summary:
            ctx.print_portfolio_raw_coverage(portfolio_ts)
            ctx.print_portfolio_filter_detail(portfolio_ts)
            ctx.print_portfolio_filter_summary(portfolio_ts)
            ctx.print_marketcap_filter_diagnostic(portfolio_ts, final_cfg)

        if final_cfg.log_portfolio_report:
            ctx.print_portfolio_report_console(portfolio_report)

        if final_cfg.write_today_portfolio:
            ctx.print_today_portfolio_console(today_portfolio, final_cfg)

    regime_summary = pd.DataFrame()
    regime_ts_out = pd.DataFrame()
    if final_cfg.enable_regime_diag and regime_ts is not None and not regime_ts.empty:
        regime_ts_out = regime_ts.copy()
        regime_ts_out = regime_ts_out.reset_index().rename(columns={"index": "Date"})
        regime_ts_out = regime_ts_out.reset_index(drop=True)

        if "Date" not in regime_ts_out.columns:
            if "date" in regime_ts_out.columns:
                regime_ts_out = regime_ts_out.rename(columns={"date": "Date"})
            elif "index" in regime_ts_out.columns:
                regime_ts_out = regime_ts_out.rename(columns={"index": "Date"})

        if "Date" in regime_ts_out.columns:
            regime_ts_out["Date"] = pd.to_datetime(regime_ts_out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")

        regime_summary = ctx.regime_summary_from_ts(best_ts_df)
        ctx.print_regime_console_summary(regime_ts, regime_summary)
        ctx.plot_regime_timeseries(
            regime_ts,
            title=f"Regime (SPY) | {regime_ts.index.min().date()} ~ {regime_ts.index.max().date()}",
        )

    if final_cfg.write_investability_report and best_invest_tbl is not None and not best_invest_tbl.empty:
        ctx.print_investability_console(best_invest_tbl)

    if bool(getattr(final_cfg, "log_data_quality_report", False)):
        ctx.print_data_quality_console_summary(
            data_quality_summary_df,
            factor_coverage_df,
            data_quality_sample_df,
            historical_universe_debug_summary_df,
            historical_universe_yearly_df,
        )

    selected_factor_detail_df = ctx.build_selected_factor_detail_df(best_mask, best_wb, best_ws, best_wd)
    selected_completeness_tables = ctx.build_selected_factor_completeness_tables(pack, final_cfg, best_mask, regime_by_date=regime_by_date)
    selected_completeness_summary_df = selected_completeness_tables.get("summary_df", pd.DataFrame())
    selected_completeness_sample_df = selected_completeness_tables.get("sample_df", pd.DataFrame())
    if bool(getattr(final_cfg, "log_data_quality_report", False)):
        ctx.print_selected_completeness_console_summary(selected_completeness_summary_df, selected_completeness_sample_df)
    meta_export_tables = ctx.build_meta_export_tables_if_needed(
        meta_summary_df=meta_summary_df,
        meta_top_df=meta_top_df,
        meta_stability_df=meta_stability_df,
        best_meta_cfg=best_meta_cfg,
        best_meta_row=best_meta_row,
        meta_results=meta_results,
    )

    excel_t0 = time.perf_counter()
    save_path = ctx._unique_excel_path(final_cfg.save_dir, final_cfg.excel_prefix)
    meta = dict(asdict(final_cfg))
    meta.update(
        {
            "Version": "v4.12 QResearch (outer-loop hyperparameter search + inner-loop GA/stability + portfolio + piot logs)",
            "Ticker Source": source,
            "Run Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Panel OK": ok,
            "Panel FAIL": fail,
            "Panel Build Skipped": bool(loaded_from_npz),
            "Precompute NPZ": npz_path or "",
            "Precompute Loaded": bool(loaded_from_npz),
            "Precompute Sec": f"{pre_sec:.3f}",
            "Meta Search Sec": f"{meta_sec:.3f}",
            "GA Final Sec": f"{ga_sec:.3f}",
            "Run Seed": run_seed,
            "Best Alpha": best_alpha,
            "Best Meta Config Brief": ctx._meta_config_to_brief_name(best_meta_cfg) if best_meta_cfg is not None else "",
            "Best Meta ID": "" if best_meta_cfg is None else best_meta_cfg.get("meta_id", ""),
        }
    )
    meta_df = pd.DataFrame([{"Key": k, "Value": str(v)} for k, v in meta.items()])

    ctx.write_v412_excel(
        save_path=save_path,
        cfg=final_cfg,
        meta_df=meta_df,
        best_cfg_df=best_cfg_df,
        ga_summary=ga_summary,
        best_rule_table=best_rule_table,
        df_timing=df_timing,
        best_ts_df=best_ts_df,
        today_top10=today_top10,
        live_today_top10=live_today_top10,
        score_1y_long=score_1y_long,
        regime_ts_out=regime_ts_out,
        regime_summary=regime_summary,
        best_regime_tbl=best_regime_tbl,
        best_scoring_regime_tbl=best_scoring_regime_tbl,
        best_invest_tbl=best_invest_tbl,
        stability_tbl=stability_tbl,
        stability_seed_summary=stability_seed_summary,
        portfolio_report=portfolio_report,
        portfolio_ts=portfolio_ts,
        today_portfolio=today_portfolio,
        selected_factor_detail_df=selected_factor_detail_df,
        meta_export_tables=meta_export_tables,
        data_quality_summary_df=data_quality_summary_df,
        factor_coverage_df=factor_coverage_df,
        data_quality_sample_df=data_quality_sample_df,
        historical_universe_debug_summary_df=historical_universe_debug_summary_df,
        historical_universe_yearly_df=historical_universe_yearly_df,
        selected_completeness_summary_df=selected_completeness_summary_df,
        selected_completeness_sample_df=selected_completeness_sample_df,
    )
    excel_sec = float(time.perf_counter() - excel_t0)

    piot_meta_log_path = ""
    piot_result_log_path = ""
    if bool(final_cfg.write_piot_meta_log):
        meta_log_text = ctx.build_piot_meta_log_text(
            cfg=final_cfg,
            meta_summary_df=meta_summary_df,
            meta_top_df=meta_top_df,
            meta_stability_df=meta_stability_df,
            best_meta_cfg=best_meta_cfg,
            best_meta_row=best_meta_row,
        )
        piot_meta_log_path = ctx.write_text_log_file(final_cfg.save_dir, final_cfg.piot_meta_log_prefix, meta_log_text)

    if bool(final_cfg.write_piot_result_log):
        result_log_text = ctx.build_piot_result_log_text(
            cfg=final_cfg,
            best_meta_cfg=best_meta_cfg,
            best_meta_row=best_meta_row,
            best_cfg_df=best_cfg_df,
            best_invest_tbl=best_invest_tbl,
            best_regime_tbl=best_regime_tbl,
            best_scoring_regime_tbl=best_scoring_regime_tbl,
            stability_tbl=stability_tbl,
            stability_seed_summary=stability_seed_summary,
            portfolio_report=portfolio_report,
            portfolio_ts=portfolio_ts,
            today_top10=today_top10,
            live_today_top10=live_today_top10,
            today_portfolio=today_portfolio,
            data_quality_summary_df=data_quality_summary_df,
            data_quality_sample_df=data_quality_sample_df,
            factor_coverage_df=factor_coverage_df,
            historical_universe_debug_summary_df=historical_universe_debug_summary_df,
            historical_universe_yearly_df=historical_universe_yearly_df,
            selected_completeness_summary_df=selected_completeness_summary_df,
            selected_completeness_sample_df=selected_completeness_sample_df,
        )
        piot_result_log_path = ctx.write_text_log_file(final_cfg.save_dir, final_cfg.piot_result_log_prefix, result_log_text)

    if piot_meta_log_path:
        print(f"[PIOT META LOG] saved: {piot_meta_log_path}")
    if piot_result_log_path:
        print(f"[PIOT RESULT LOG] saved: {piot_result_log_path}")

    print(f"[DONE] saved: {save_path}")
    print(f"[Timing] precompute={pre_sec:.2f}s | meta={meta_sec:.2f}s | ga_final={ga_sec:.2f}s | excel={excel_sec:.2f}s")
    return save_path
