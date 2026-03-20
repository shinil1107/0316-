from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

THR_COV_RAW_PASS = 0.85
THR_COV_RAW_WARN = 0.75
THR_COV_500_PASS = 0.80
THR_COV_500_WARN = 0.65
THR_REPAIR_PASS = 0.05
THR_REPAIR_WARN = 0.15
THR_LOW_FLAG_PASS = 0.10
THR_LOW_FLAG_WARN = 0.25
THR_TAIL_ZERO_PASS = 0.00
THR_TAIL_ZERO_WARN = 0.20


def _safe_mean(v: pd.Series) -> float:
    s = pd.to_numeric(v, errors="coerce")
    return float(s.mean()) if len(s) else np.nan


def build_daily_trust_kpi(pack: Dict[str, Any], cfg: Any) -> pd.DataFrame:
    dates = list(pack.get("dates", []))
    if not dates:
        return pd.DataFrame()

    raw = np.asarray(pack.get("raw_universe_member_mask", pack.get("universe_member_mask", np.zeros((0, 0), dtype=bool))), dtype=bool)
    final = np.asarray(pack.get("universe_member_mask", np.zeros_like(raw, dtype=bool)), dtype=bool)

    D = len(dates)
    if raw.ndim != 2 or final.ndim != 2 or raw.shape[0] != D or final.shape[0] != D:
        return pd.DataFrame()

    raw_cnt = np.sum(raw, axis=1).astype(np.int64)
    final_cnt = np.sum(final, axis=1).astype(np.int64)

    cov_vs_raw = np.where(raw_cnt > 0, final_cnt / np.maximum(raw_cnt, 1), np.nan).astype(np.float64)
    cov_vs_500 = (final_cnt / 500.0).astype(np.float64)

    repair_mode = str(getattr(cfg, "historical_universe_repair_mode", "OFF")).upper().strip()
    if repair_mode == "OFF":
        repair_dep = np.zeros((D,), dtype=np.float64)
    else:
        # Approximation: share of final members not in raw membership.
        repair_only = final & (~raw)
        repair_dep = np.where(final_cnt > 0, np.sum(repair_only, axis=1) / np.maximum(final_cnt, 1), 0.0).astype(np.float64)

    # v6.1 fixed thresholds
    thr_raw = THR_COV_RAW_WARN
    thr_500 = THR_COV_500_WARN
    low_flag = ((cov_vs_raw < thr_raw) | (cov_vs_500 < thr_500)).astype(bool)

    return pd.DataFrame(
        {
            "Date": pd.to_datetime(pd.Index(dates), errors="coerce").strftime("%Y-%m-%d"),
            "raw_membership_count": raw_cnt,
            "final_universe_count": final_cnt,
            "coverage_ratio_vs_raw": cov_vs_raw,
            "coverage_ratio_vs_500": cov_vs_500,
            "repair_dependency_ratio": repair_dep,
            "low_coverage_flag": low_flag,
        }
    )


def _grade_higher_better(v: float, pass_thr: float, warn_thr: float) -> str:
    if not np.isfinite(v):
        return "WARN"
    if v >= pass_thr:
        return "PASS"
    if v >= warn_thr:
        return "WARN"
    return "FAIL"


def _grade_lower_better(v: float, pass_thr: float, warn_thr: float) -> str:
    if not np.isfinite(v):
        return "WARN"
    if v <= pass_thr:
        return "PASS"
    if v <= warn_thr:
        return "WARN"
    return "FAIL"


def _score_higher_better(v: float, lo: float, hi: float) -> float:
    if not np.isfinite(v):
        return 0.0
    if hi <= lo:
        return 0.0
    return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))


def _score_lower_better(v: float, lo: float, hi: float) -> float:
    # lo=best, hi=worst
    if not np.isfinite(v):
        return 0.0
    if hi <= lo:
        return 0.0
    return float(np.clip((hi - v) / (hi - lo), 0.0, 1.0))


def build_trust_summary_reports(
    daily_trust_df: pd.DataFrame,
    best_ts_df: pd.DataFrame,
    portfolio_ts: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if daily_trust_df is None or daily_trust_df.empty:
        z = pd.DataFrame()
        return z, z, z, z, z

    d = daily_trust_df.copy()
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d["Year"] = d["Date"].dt.year

    low_flag_ratio = float(pd.to_numeric(d["low_coverage_flag"], errors="coerce").fillna(0).mean())
    summary = pd.DataFrame(
        [
            {"Metric": "avg_coverage_ratio_vs_raw", "Value": _safe_mean(d["coverage_ratio_vs_raw"])},
            {"Metric": "avg_coverage_ratio_vs_500", "Value": _safe_mean(d["coverage_ratio_vs_500"])},
            {"Metric": "avg_repair_dependency_ratio", "Value": _safe_mean(d["repair_dependency_ratio"])},
            {"Metric": "low_coverage_day_ratio", "Value": low_flag_ratio},
        ]
    )

    dist = pd.DataFrame(
        [
            {"Metric": "coverage_vs_raw_p10", "Value": float(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce").quantile(0.10))},
            {"Metric": "coverage_vs_raw_p25", "Value": float(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce").quantile(0.25))},
            {"Metric": "coverage_vs_raw_p50", "Value": float(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce").quantile(0.50))},
            {"Metric": "coverage_vs_raw_p75", "Value": float(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce").quantile(0.75))},
            {"Metric": "coverage_vs_raw_p90", "Value": float(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce").quantile(0.90))},
            {"Metric": "coverage_vs_500_p50", "Value": float(pd.to_numeric(d["coverage_ratio_vs_500"], errors="coerce").quantile(0.50))},
        ]
    )

    yearly = (
        d.groupby("Year", dropna=True)
        .agg(
            avg_coverage_ratio_vs_raw=("coverage_ratio_vs_raw", "mean"),
            avg_coverage_ratio_vs_500=("coverage_ratio_vs_500", "mean"),
            avg_repair_dependency_ratio=("repair_dependency_ratio", "mean"),
            low_coverage_day_ratio=("low_coverage_flag", "mean"),
            avg_final_universe_count=("final_universe_count", "mean"),
        )
        .reset_index()
    )

    # Coverage histogram-like buckets
    bins = [-np.inf, 0.65, 0.75, 0.85, 0.95, np.inf]
    labels = ["<0.65", "0.65-0.75", "0.75-0.85", "0.85-0.95", ">=0.95"]
    bucket = pd.cut(pd.to_numeric(d["coverage_ratio_vs_raw"], errors="coerce"), bins=bins, labels=labels, include_lowest=True)
    hist = bucket.value_counts(dropna=False).reindex(labels, fill_value=0).reset_index()
    hist.columns = ["Bucket", "Count"]
    hist["Ratio"] = hist["Count"] / max(len(d), 1)
    dist = pd.concat([dist, pd.DataFrame([{"Metric": "----hist_coverage_vs_raw----", "Value": np.nan}])], ignore_index=True)
    for _, r in hist.iterrows():
        dist = pd.concat([dist, pd.DataFrame([{"Metric": f"hist_{r['Bucket']}", "Value": float(r['Ratio'])}])], ignore_index=True)

    # Performance vs coverage split
    perf = pd.DataFrame()
    ts = best_ts_df.copy() if best_ts_df is not None else pd.DataFrame()
    if not ts.empty and "Date" in ts.columns:
        ts["Date"] = pd.to_datetime(ts["Date"], errors="coerce")
        merged = ts.merge(d[["Date", "low_coverage_flag"]], on="Date", how="left")
        merged["coverage_bucket"] = np.where(merged["low_coverage_flag"].fillna(False), "LOW", "HIGH")
        perf = (
            merged.groupby("coverage_bucket", dropna=False)
            .agg(
                Mean_IC_1M=("IC_1M", "mean"),
                Mean_IC_3M=("IC_3M", "mean"),
                Mean_Spread_Mix=("Spread_Mix", "mean"),
                EvalCount=("Date", "count"),
            )
            .reset_index()
        )
        if portfolio_ts is not None and not portfolio_ts.empty and "Date" in portfolio_ts.columns:
            p = portfolio_ts.copy()
            p["Date"] = pd.to_datetime(p["Date"], errors="coerce")
            p2 = p.merge(d[["Date", "low_coverage_flag"]], on="Date", how="left")
            p2["coverage_bucket"] = np.where(p2["low_coverage_flag"].fillna(False), "LOW", "HIGH")
            p_agg = (
                p2.groupby("coverage_bucket", dropna=False)
                .agg(
                    Portfolio_Fwd1M=("Portfolio_Fwd1M", "mean"),
                    Portfolio_Fwd3M=("Portfolio_Fwd3M", "mean"),
                    Portfolio_Turnover=("Turnover", "mean"),
                )
                .reset_index()
            )
            perf = perf.merge(p_agg, on="coverage_bucket", how="left")

    # TrustScore (0~100): Coverage(40) + Continuity(25) + Repair(15) + Stability split(20)
    avg_cov_raw = float(_safe_mean(d["coverage_ratio_vs_raw"]))
    avg_cov_500 = float(_safe_mean(d["coverage_ratio_vs_500"]))
    avg_repair = float(_safe_mean(d["repair_dependency_ratio"]))
    tail_window = min(10, len(d))
    tail_zero_ratio = float((d["final_universe_count"].tail(tail_window) <= 0).mean()) if tail_window > 0 else np.nan

    cov_sub = 0.5 * _score_higher_better(avg_cov_raw, THR_COV_RAW_WARN, THR_COV_RAW_PASS) + 0.5 * _score_higher_better(
        avg_cov_500, THR_COV_500_WARN, THR_COV_500_PASS
    )
    cont_sub = 0.5 * _score_lower_better(low_flag_ratio, THR_LOW_FLAG_PASS, THR_LOW_FLAG_WARN) + 0.5 * _score_lower_better(
        tail_zero_ratio, THR_TAIL_ZERO_PASS, THR_TAIL_ZERO_WARN
    )
    repair_sub = _score_lower_better(avg_repair, THR_REPAIR_PASS, THR_REPAIR_WARN)

    split_sub = 1.0
    if perf is not None and not perf.empty and "coverage_bucket" in perf.columns:
        hi = perf.loc[perf["coverage_bucket"] == "HIGH"]
        lo = perf.loc[perf["coverage_bucket"] == "LOW"]
        if not hi.empty and not lo.empty:
            hi_ic = float(pd.to_numeric(hi["Mean_IC_1M"], errors="coerce").iloc[0])
            lo_ic = float(pd.to_numeric(lo["Mean_IC_1M"], errors="coerce").iloc[0])
            hi_sp = float(pd.to_numeric(hi["Mean_Spread_Mix"], errors="coerce").iloc[0])
            lo_sp = float(pd.to_numeric(lo["Mean_Spread_Mix"], errors="coerce").iloc[0])
            ic_drop = (hi_ic - lo_ic) / max(abs(hi_ic), 1e-9) if np.isfinite(hi_ic) and np.isfinite(lo_ic) else np.nan
            sp_drop = (hi_sp - lo_sp) / max(abs(hi_sp), 1e-9) if np.isfinite(hi_sp) and np.isfinite(lo_sp) else np.nan
            drop = np.nanmax([ic_drop, sp_drop]) if (np.isfinite(ic_drop) or np.isfinite(sp_drop)) else np.nan
            split_sub = _score_lower_better(drop, 0.40, 0.60)

    trust_score = 100.0 * (0.40 * cov_sub + 0.25 * cont_sub + 0.15 * repair_sub + 0.20 * split_sub)
    trust_grade = "A" if trust_score >= 85 else ("B" if trust_score >= 70 else ("C" if trust_score >= 55 else "D"))

    trust_score_df = pd.DataFrame(
        [
            {
                "Metric": "TrustScore",
                "Value": float(trust_score),
                "Grade": trust_grade,
                "CoverageSubScore": float(100.0 * cov_sub),
                "ContinuitySubScore": float(100.0 * cont_sub),
                "RepairSubScore": float(100.0 * repair_sub),
                "StabilitySplitSubScore": float(100.0 * split_sub),
            },
            {
                "Metric": "ThresholdStatus",
                "Value": np.nan,
                "Grade": "",
                "CoverageSubScore": np.nan,
                "ContinuitySubScore": np.nan,
                "RepairSubScore": np.nan,
                "StabilitySplitSubScore": np.nan,
            },
            {"Metric": "avg_coverage_ratio_vs_raw", "Value": avg_cov_raw, "Grade": _grade_higher_better(avg_cov_raw, THR_COV_RAW_PASS, THR_COV_RAW_WARN), "CoverageSubScore": np.nan, "ContinuitySubScore": np.nan, "RepairSubScore": np.nan, "StabilitySplitSubScore": np.nan},
            {"Metric": "avg_coverage_ratio_vs_500", "Value": avg_cov_500, "Grade": _grade_higher_better(avg_cov_500, THR_COV_500_PASS, THR_COV_500_WARN), "CoverageSubScore": np.nan, "ContinuitySubScore": np.nan, "RepairSubScore": np.nan, "StabilitySplitSubScore": np.nan},
            {"Metric": "avg_repair_dependency_ratio", "Value": avg_repair, "Grade": _grade_lower_better(avg_repair, THR_REPAIR_PASS, THR_REPAIR_WARN), "CoverageSubScore": np.nan, "ContinuitySubScore": np.nan, "RepairSubScore": np.nan, "StabilitySplitSubScore": np.nan},
            {"Metric": "low_coverage_day_ratio", "Value": low_flag_ratio, "Grade": _grade_lower_better(low_flag_ratio, THR_LOW_FLAG_PASS, THR_LOW_FLAG_WARN), "CoverageSubScore": np.nan, "ContinuitySubScore": np.nan, "RepairSubScore": np.nan, "StabilitySplitSubScore": np.nan},
            {"Metric": "tail_zero_tradable_ratio", "Value": tail_zero_ratio, "Grade": _grade_lower_better(tail_zero_ratio, THR_TAIL_ZERO_PASS, THR_TAIL_ZERO_WARN), "CoverageSubScore": np.nan, "ContinuitySubScore": np.nan, "RepairSubScore": np.nan, "StabilitySplitSubScore": np.nan},
        ]
    )

    return summary, dist, yearly, perf, trust_score_df

