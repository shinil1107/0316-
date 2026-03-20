from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
import pandas as pd


@dataclass
class ReliabilityCheckResult:
    name: str
    status: str
    value: float
    threshold: float
    message: str


def _safe_mean(series: Any) -> float:
    s = pd.to_numeric(series, errors="coerce")
    return float(s.mean()) if len(s) else float("nan")


def evaluate_reliability_layer(pack: Dict[str, Any], portfolio_ts: pd.DataFrame, cfg: Any) -> Dict[str, Any]:
    """
    Baseline reliability layer scaffold.
    Detailed thresholds/policies can be tightened after user requirements are finalized.
    """
    tradable = np.asarray(pack.get("tradable", np.zeros((0, 0), dtype=bool)), dtype=bool)
    dates = list(pack.get("dates", []))
    cov = np.asarray(pack.get("coverage_ratio_vs_raw", np.full((len(dates),), np.nan)), dtype=np.float64)

    daily_tradable = np.sum(tradable, axis=1) if tradable.ndim == 2 else np.array([], dtype=np.int64)
    tail_window = min(10, len(daily_tradable))
    tail_zero_ratio = float(np.mean(daily_tradable[-tail_window:] <= 0)) if tail_window > 0 else np.nan
    avg_cov = float(np.nanmean(cov)) if cov.size > 0 else np.nan
    avg_turnover = _safe_mean(portfolio_ts.get("Turnover", np.nan)) if portfolio_ts is not None and not portfolio_ts.empty else np.nan

    checks = [
        ReliabilityCheckResult(
            name="tail_zero_tradable_ratio",
            status="PASS" if (np.isfinite(tail_zero_ratio) and tail_zero_ratio <= 0.10) else "WARN",
            value=float(tail_zero_ratio) if np.isfinite(tail_zero_ratio) else np.nan,
            threshold=0.10,
            message="Recent dates should not have prolonged zero-tradable periods.",
        ),
        ReliabilityCheckResult(
            name="avg_coverage_ratio_vs_raw",
            status="PASS" if (np.isfinite(avg_cov) and avg_cov >= 0.85) else "WARN",
            value=float(avg_cov) if np.isfinite(avg_cov) else np.nan,
            threshold=0.85,
            message="Coverage should remain high vs raw historical membership anchor.",
        ),
        ReliabilityCheckResult(
            name="avg_portfolio_turnover",
            status="PASS" if (np.isfinite(avg_turnover) and avg_turnover <= float(getattr(cfg, "portfolio_target_avg_turnover", 0.35))) else "WARN",
            value=float(avg_turnover) if np.isfinite(avg_turnover) else np.nan,
            threshold=float(getattr(cfg, "portfolio_target_avg_turnover", 0.35)),
            message="Turnover should stay near strategy target.",
        ),
    ]

    return {
        "summary_status": "PASS" if all(c.status == "PASS" for c in checks) else "WARN",
        "checks": [c.__dict__ for c in checks],
    }

