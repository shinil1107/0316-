"""Shadow-run diff: compare live vs shadow signal recommendations."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def compare_recommendations(
    live_scores: pd.DataFrame,
    shadow_scores: pd.DataFrame,
    live_recos: pd.DataFrame,
    shadow_recos: pd.DataFrame,
    *,
    label: str = "shadow",
    day_number: int = 0,
    duration_days: int = 30,
    top_n: int = 0,
) -> Dict[str, Any]:
    """Compare live and shadow signal outputs and return a diff summary.

    Parameters
    ----------
    live_scores / shadow_scores : DataFrame with columns [Ticker, Score, ...]
    live_recos / shadow_recos   : DataFrame with columns [Ticker, Action, Score, ...]
    label        : shadow signal label for display
    day_number   : current day within shadow run (1-based)
    duration_days: total planned shadow duration
    top_n        : if >0, overlap is computed on the top-N ranked tickers
                   instead of the full scored universe.

    Returns a dict with keys usable for artifact JSON, email text, and
    the final summary report aggregation.
    """
    _BUY_ACTIONS = {"BUY", "BUY_NEW", "BUY_MORE"}
    _SELL_ACTIONS = {"SELL", "SELL_ALL", "SELL_PARTIAL", "TRIM"}

    live_buys = set()
    shadow_buys = set()
    if not live_recos.empty and "Action" in live_recos.columns:
        live_buys = set(live_recos.loc[live_recos["Action"].isin(_BUY_ACTIONS), "Ticker"])
    if not shadow_recos.empty and "Action" in shadow_recos.columns:
        shadow_buys = set(shadow_recos.loc[shadow_recos["Action"].isin(_BUY_ACTIONS), "Ticker"])

    def _ticker_score_list(df: pd.DataFrame, mask=None) -> List[Dict[str, Any]]:
        if df.empty or "Ticker" not in df.columns:
            return []
        sub = df if mask is None else df.loc[mask]
        out = []
        for _, row in sub.iterrows():
            score = float(row["Score"]) if "Score" in row.index and pd.notna(row.get("Score")) else 0.0
            entry = {"ticker": str(row["Ticker"]), "score": score}
            if "Action" in row.index:
                entry["action"] = str(row["Action"])
            out.append(entry)
        return out

    shadow_topn_picks: List[Dict[str, Any]] = []
    if not shadow_scores.empty and top_n > 0:
        shadow_topn_picks = _ticker_score_list(shadow_scores.head(top_n))

    shadow_buys_all: List[Dict[str, Any]] = []
    shadow_sells_all: List[Dict[str, Any]] = []
    if not shadow_recos.empty and "Action" in shadow_recos.columns:
        buy_mask = shadow_recos["Action"].isin(_BUY_ACTIONS)
        sell_mask = shadow_recos["Action"].isin(_SELL_ACTIONS)
        shadow_buys_all = _ticker_score_list(shadow_recos.loc[buy_mask])
        shadow_sells_all = _ticker_score_list(shadow_recos.loc[sell_mask])

    if top_n > 0:
        live_top = set(live_scores.head(top_n)["Ticker"].tolist()) if not live_scores.empty else set()
        shadow_top = set(shadow_scores.head(top_n)["Ticker"].tolist()) if not shadow_scores.empty else set()
    else:
        live_top = set(live_scores["Ticker"].tolist()) if not live_scores.empty else set()
        shadow_top = set(shadow_scores["Ticker"].tolist()) if not shadow_scores.empty else set()

    overlap = live_top & shadow_top
    overlap_n = len(overlap)
    union_n = len(live_top | shadow_top)
    overlap_rate = overlap_n / max(union_n, 1)

    shadow_only_buy = sorted(shadow_buys - live_buys)
    live_only_buy = sorted(live_buys - shadow_buys)

    rank_corr = _rank_correlation(live_scores, shadow_scores)

    shadow_buy_details = []
    for t in shadow_only_buy:
        row = shadow_recos.loc[shadow_recos["Ticker"] == t]
        score = float(row["Score"].iloc[0]) if not row.empty and "Score" in row.columns else 0.0
        shadow_buy_details.append({"ticker": t, "score": score})

    live_buy_details = []
    for t in live_only_buy:
        row = live_recos.loc[live_recos["Ticker"] == t]
        score = float(row["Score"].iloc[0]) if not row.empty and "Score" in row.columns else 0.0
        live_buy_details.append({"ticker": t, "score": score})

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "label": label,
        "day_number": day_number,
        "duration_days": duration_days,
        "top_n": top_n,
        "live_scored_count": len(live_scores),
        "shadow_scored_count": len(shadow_scores),
        "topn_overlap_count": overlap_n,
        "topn_union_count": union_n,
        "topn_overlap_rate": round(overlap_rate, 4),
        "live_buy_count": len(live_buys),
        "shadow_buy_count": len(shadow_buys),
        "shadow_only_buys": shadow_buy_details,
        "live_only_buys": live_buy_details,
        "shadow_topn_picks": shadow_topn_picks,
        "shadow_buys_all": shadow_buys_all,
        "shadow_sells_all": shadow_sells_all,
        "rank_correlation": rank_corr,
    }


def _rank_correlation(df_a: pd.DataFrame, df_b: pd.DataFrame) -> Optional[float]:
    """Spearman rank correlation of scores on common tickers."""
    if df_a.empty or df_b.empty:
        return None
    if "Ticker" not in df_a.columns or "Ticker" not in df_b.columns:
        return None
    if "Score" not in df_a.columns or "Score" not in df_b.columns:
        return None

    merged = pd.merge(
        df_a[["Ticker", "Score"]].rename(columns={"Score": "ScoreA"}),
        df_b[["Ticker", "Score"]].rename(columns={"Score": "ScoreB"}),
        on="Ticker",
        how="inner",
    )
    if len(merged) < 3:
        return None

    rank_a = merged["ScoreA"].rank(ascending=False)
    rank_b = merged["ScoreB"].rank(ascending=False)
    corr = rank_a.corr(rank_b, method="spearman")
    return round(float(corr), 4) if pd.notna(corr) else None


def format_email_section(diff: Dict[str, Any]) -> str:
    """Format a compact text block for email insertion."""
    lines = []
    sep = "=" * 55
    thin = "-" * 55

    day = diff.get("day_number", "?")
    dur = diff.get("duration_days", "?")
    label = diff.get("label", "shadow")

    lines.append("")
    lines.append(sep)
    lines.append(f"SHADOW SIGNAL: {label} (Day {day}/{dur})")
    lines.append(thin)

    olap_n = diff.get("topn_overlap_count", 0)
    union_n = diff.get("topn_union_count", 0)
    olap_rate = diff.get("topn_overlap_rate", 0)
    lines.append(f"  Top-N Overlap: {olap_n}/{union_n} ({olap_rate:.0%})")

    shadow_only = diff.get("shadow_only_buys", [])
    live_only = diff.get("live_only_buys", [])

    if shadow_only:
        parts = [f"{b['ticker']} ({b['score']:+.1f})" for b in shadow_only[:5]]
        extra = f" +{len(shadow_only)-5} more" if len(shadow_only) > 5 else ""
        lines.append(f"  Shadow-only BUY: {', '.join(parts)}{extra}")
    else:
        lines.append(f"  Shadow-only BUY: (none)")

    if live_only:
        parts = [f"{b['ticker']} ({b['score']:+.1f})" for b in live_only[:5]]
        extra = f" +{len(live_only)-5} more" if len(live_only) > 5 else ""
        lines.append(f"  Live-only BUY:   {', '.join(parts)}{extra}")
    else:
        lines.append(f"  Live-only BUY:   (none)")

    rc = diff.get("rank_correlation")
    rc_str = f"{rc:.2f}" if rc is not None else "N/A"
    lines.append(f"  Rank correlation: {rc_str}")

    # Shadow signal's own top-N picks (rank list with scores)
    shadow_picks = diff.get("shadow_topn_picks", [])
    top_n_used = diff.get("top_n", 0)
    if shadow_picks:
        lines.append(thin)
        lines.append(f"  Shadow Top-{top_n_used or len(shadow_picks)} picks (ranked):")
        for i, p in enumerate(shadow_picks, 1):
            lines.append(f"    {i:>2}. {p['ticker']:<6} {p['score']:+6.1f}")

    # Shadow signal's full BUY/SELL recommendations
    shadow_buys_all = diff.get("shadow_buys_all", [])
    shadow_sells_all = diff.get("shadow_sells_all", [])
    if shadow_buys_all or shadow_sells_all:
        lines.append(thin)
    if shadow_buys_all:
        n_b = len(shadow_buys_all)
        parts = [f"{b['ticker']}({b.get('action','BUY')[0]} {b['score']:+.1f})" for b in shadow_buys_all[:10]]
        extra = f" +{n_b-10} more" if n_b > 10 else ""
        lines.append(f"  Shadow BUY ({n_b}): {', '.join(parts)}{extra}")
    if shadow_sells_all:
        n_s = len(shadow_sells_all)
        parts = [f"{s['ticker']}({s.get('action','SELL')[0]} {s['score']:+.1f})" for s in shadow_sells_all[:10]]
        extra = f" +{n_s-10} more" if n_s > 10 else ""
        lines.append(f"  Shadow SELL ({n_s}): {', '.join(parts)}{extra}")

    lines.append(thin)

    return "\n".join(lines)


def save_diff_artifact(run_dir: Path, diff: Dict[str, Any]) -> None:
    """Save diff summary JSON and CSV into the shadow artifact folder."""
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "shadow_diff_summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(diff, f, ensure_ascii=False, indent=2, default=str)

    rows = []
    for entry in diff.get("shadow_only_buys", []):
        rows.append({"side": "shadow_only", **entry})
    for entry in diff.get("live_only_buys", []):
        rows.append({"side": "live_only", **entry})

    if rows:
        csv_path = run_dir / "shadow_diff.csv"
        pd.DataFrame(rows).to_csv(csv_path, index=False)


def generate_expiry_report(
    output_dir: str,
    label: str,
    start_date: str,
    duration_days: int,
) -> str:
    """Aggregate daily shadow_diff_summary.json files into a markdown report.

    Returns the path to the saved report file.
    """
    root = Path(output_dir).expanduser() / "daily_runs"
    summaries: List[Dict[str, Any]] = []

    if root.exists():
        for run_dir in sorted(root.iterdir()):
            if not run_dir.is_dir() or "_shadow" not in run_dir.name:
                continue
            json_path = run_dir / "shadow_diff_summary.json"
            if json_path.exists():
                try:
                    with json_path.open("r", encoding="utf-8") as f:
                        summaries.append(json.load(f))
                except Exception:
                    pass

    n_days = len(summaries)
    if n_days == 0:
        avg_overlap = 0.0
        avg_rank_corr = None
    else:
        avg_overlap = np.mean([s.get("topn_overlap_rate", 0) for s in summaries])
        corrs = [s["rank_correlation"] for s in summaries if s.get("rank_correlation") is not None]
        avg_rank_corr = float(np.mean(corrs)) if corrs else None

    today_str = datetime.now().strftime("%Y%m%d")
    report_name = f"shadow_run_report_{label}_{today_str}.md"
    docs_dir = Path(__file__).resolve().parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    report_path = docs_dir / report_name

    lines = [
        f"# Shadow-Run Report: {label}",
        f"",
        f"- **Start date**: {start_date}",
        f"- **Duration**: {duration_days} days (planned)",
        f"- **Actual days collected**: {n_days}",
        f"- **Report date**: {datetime.now().strftime('%Y-%m-%d')}",
        f"",
        f"## Summary Statistics",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Mean Top-N Overlap Rate | {avg_overlap:.1%} |",
        f"| Mean Rank Correlation | {avg_rank_corr:.3f if avg_rank_corr is not None else 'N/A'} |",
        f"| Days with data | {n_days} |",
        f"",
        f"## Daily Detail",
        f"",
        f"| Day | Date | Overlap | Rank Corr | Shadow-only BUY | Live-only BUY |",
        f"|-----|------|---------|-----------|-----------------|---------------|",
    ]

    for s in summaries:
        d = s.get("date", "?")
        day_n = s.get("day_number", "?")
        olap = s.get("topn_overlap_rate", 0)
        rc = s.get("rank_correlation")
        rc_str = f"{rc:.3f}" if rc is not None else "N/A"
        sb = len(s.get("shadow_only_buys", []))
        lb = len(s.get("live_only_buys", []))
        lines.append(f"| {day_n} | {d} | {olap:.1%} | {rc_str} | {sb} | {lb} |")

    lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(report_path)
