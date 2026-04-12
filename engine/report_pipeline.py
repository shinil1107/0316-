from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict

import numpy as np
import pandas as pd

from .data_trust_layer import build_daily_trust_kpi, build_trust_summary_reports


def _build_trust_bundle(ctx: Any, pack: dict, final_cfg: Any, best_ts_df: pd.DataFrame, portfolio_ts: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    daily_trust_df = build_daily_trust_kpi(pack, final_cfg)
    trust_summary_df, trust_dist_df, trust_yearly_df, trust_perf_split_df, trust_score_df = build_trust_summary_reports(
        daily_trust_df=daily_trust_df,
        best_ts_df=best_ts_df,
        portfolio_ts=portfolio_ts,
    )
    return {
        "daily_trust_df": daily_trust_df,
        "trust_summary_df": trust_summary_df,
        "trust_dist_df": trust_dist_df,
        "trust_yearly_df": trust_yearly_df,
        "trust_perf_split_df": trust_perf_split_df,
        "trust_score_df": trust_score_df,
    }


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
        # Live today top10 is currently the same snapshot logic as today top10.
        # Reuse the computed result when available to avoid duplicate scoring work.
        if today_top10 is not None and not today_top10.empty:
            live_today_top10 = today_top10.copy()
        else:
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
    backtest_last_portfolio = pd.DataFrame()
    live_reco_portfolio = pd.DataFrame()
    live_reco_diag_df = pd.DataFrame()

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
        backtest_last_portfolio = today_portfolio.copy() if today_portfolio is not None else pd.DataFrame()
        # Prefer the dedicated live portfolio path, but keep the top10 fallback
        # so the report still has a usable live snapshot when that path is empty.
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
            live_reco_portfolio = live_today_portfolio.copy()
        elif live_today_top10 is not None and not live_today_top10.empty:
            # Super-safe fallback: produce a usable live portfolio snapshot from live top10.
            tmp = live_today_top10.copy().reset_index(drop=True)
            n = len(tmp)
            if n > 0:
                tmp["RawScore100"] = pd.to_numeric(tmp.get("Score100", np.nan), errors="coerce")
                tmp["Weight"] = 1.0 / float(n)
                live_reco_portfolio = tmp[["Date", "Ticker", "Close", "Score100", "RawScore100", "Weight", "Regime", "ScoreRegime"]].copy()

        if live_reco_portfolio is not None and not live_reco_portfolio.empty:
            today_portfolio = live_reco_portfolio.copy()

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

    eval_last_date = ""
    if portfolio_ts is not None and not portfolio_ts.empty and "Date" in portfolio_ts.columns:
        eval_last_date = str(pd.to_datetime(portfolio_ts["Date"], errors="coerce").max().strftime("%Y-%m-%d"))
    live_last_date = ""
    if live_reco_portfolio is not None and not live_reco_portfolio.empty and "Date" in live_reco_portfolio.columns:
        live_last_date = str(pd.to_datetime(live_reco_portfolio["Date"], errors="coerce").max().strftime("%Y-%m-%d"))
    elif live_today_top10 is not None and not live_today_top10.empty and "Date" in live_today_top10.columns:
        live_last_date = str(pd.to_datetime(live_today_top10["Date"], errors="coerce").max().strftime("%Y-%m-%d"))

    gap_days = np.nan
    if eval_last_date and live_last_date:
        gap_days = float((pd.Timestamp(live_last_date) - pd.Timestamp(eval_last_date)).days)
    live_reco_diag_df = pd.DataFrame(
        [
            {"Metric": "EvalLastDate", "Value": eval_last_date},
            {"Metric": "LiveLastDate", "Value": live_last_date},
            {"Metric": "EvalLiveDateGapDays", "Value": gap_days},
            {"Metric": "LiveRecoCount", "Value": int(0 if live_reco_portfolio is None else len(live_reco_portfolio))},
            {"Metric": "LiveTop10Count", "Value": int(0 if live_today_top10 is None else len(live_today_top10))},
        ]
    )
    if not live_reco_diag_df.empty:
        print("\n[Live Recommendation Separation]")
        print(live_reco_diag_df.to_string(index=False))

    trust_bundle = _build_trust_bundle(ctx, pack, final_cfg, best_ts_df, portfolio_ts)
    daily_trust_df = trust_bundle["daily_trust_df"]
    trust_summary_df = trust_bundle["trust_summary_df"]
    trust_dist_df = trust_bundle["trust_dist_df"]
    trust_yearly_df = trust_bundle["trust_yearly_df"]
    trust_perf_split_df = trust_bundle["trust_perf_split_df"]
    trust_score_df = trust_bundle["trust_score_df"]
    if not daily_trust_df.empty:
        print("\n[Data Trust Daily KPI | Tail]")
        print(daily_trust_df.tail(12).to_string(index=False))
    if not trust_summary_df.empty:
        print("\n[Data Trust Summary]")
        print(trust_summary_df.to_string(index=False))
    if not trust_dist_df.empty:
        print("\n[Data Trust Distribution]")
        print(trust_dist_df.to_string(index=False))
    if not trust_yearly_df.empty:
        print("\n[Data Trust Yearly]")
        print(trust_yearly_df.to_string(index=False))
    if not trust_perf_split_df.empty:
        print("\n[Performance vs Coverage Split]")
        print(trust_perf_split_df.to_string(index=False))
    if not trust_score_df.empty:
        print("\n[Data Trust Score]")
        print(trust_score_df.to_string(index=False))

    if trust_summary_df is not None and not trust_summary_df.empty:
        add_rows = []
        for _, r in trust_summary_df.iterrows():
            add_rows.append(
                {
                    "Metric": f"Trust::{r.get('Metric', '')}",
                    "Current": r.get("Value", np.nan),
                    "Target": np.nan,
                    "AchievementPct": np.nan,
                    "Pass": "CHECK",
                }
            )
        if trust_score_df is not None and not trust_score_df.empty:
            ts_row = trust_score_df.loc[trust_score_df["Metric"] == "TrustScore"]
            if not ts_row.empty:
                add_rows.append(
                    {
                        "Metric": "Trust::TrustScore",
                        "Current": float(pd.to_numeric(ts_row["Value"], errors="coerce").iloc[0]),
                        "Target": 85.0,
                        "AchievementPct": np.nan,
                        "Pass": str(ts_row["Grade"].iloc[0]),
                    }
                )
        if add_rows:
            data_quality_summary_df = pd.concat([data_quality_summary_df, pd.DataFrame(add_rows)], ignore_index=True)

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
            "Version": "v6.4 QResearch (outer-loop hyperparameter search + inner-loop GA/stability + portfolio + piot logs)",
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

    # Append trust-specific sheets for v6.1 observability.
    try:
        with pd.ExcelWriter(save_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            (daily_trust_df if daily_trust_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_Daily", index=False)
            (trust_summary_df if trust_summary_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_Summary", index=False)
            (trust_dist_df if trust_dist_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_Dist", index=False)
            (trust_yearly_df if trust_yearly_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_Yearly", index=False)
            (trust_perf_split_df if trust_perf_split_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_PerfSplit", index=False)
            (trust_score_df if trust_score_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Data_Trust_Score", index=False)
            (live_reco_diag_df if live_reco_diag_df is not None else pd.DataFrame()).to_excel(w, sheet_name="Live_Reco_Diag", index=False)
            (backtest_last_portfolio if backtest_last_portfolio is not None else pd.DataFrame()).to_excel(w, sheet_name="Backtest_Last_Portfolio", index=False)
            (live_reco_portfolio if live_reco_portfolio is not None else pd.DataFrame()).to_excel(w, sheet_name="Live_Recommendation_Portfolio", index=False)
    except Exception as e:
        print(f"[DataTrust][WARN] failed to append trust sheets: {type(e).__name__}: {e}")

    signal_lab_result = search_bundle.get("signal_lab_result")
    if signal_lab_result is not None:
        sl_tables = signal_lab_result.get("signal_lab_tables", {})
        if sl_tables:
            try:
                with pd.ExcelWriter(save_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
                    for sheet_name, df in sl_tables.items():
                        if df is not None and not df.empty:
                            df.to_excel(w, sheet_name=sheet_name, index=False)
                print(f"[SignalLab] appended {len(sl_tables)} report sheets to Excel")
            except Exception as e:
                print(f"[SignalLab][WARN] failed to append signal lab sheets: {type(e).__name__}: {e}")

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
