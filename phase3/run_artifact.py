"""Helpers for writing Phase 3 daily run artifacts."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd


_REDACT_KEYS = ("password", "secret", "token", "api_key", "apikey")
_RECO_COMPARE_COLUMNS = (
    "Date",
    "Ticker",
    "Action",
    "Score",
    "TargetPct",
    "ActualPct",
    "GapPct",
    "Price",
    "Shares",
    "Capital",
    "Regime",
    "GraceCount",
)


def create_run_context(output_dir: str, run_timestamp: datetime, rebalance_mode: str, dry_run: bool) -> Tuple[str, Path]:
    base_name = run_timestamp.strftime("%Y%m%d_%H%M%S")
    suffix = "dryrun" if dry_run else str(rebalance_mode or "run").lower()
    run_id = f"{base_name}_{suffix}"

    root = Path(output_dir).expanduser() / "daily_runs"
    run_dir = root / run_id
    seq = 1
    while run_dir.exists():
        run_id = f"{base_name}_{suffix}_{seq:02d}"
        run_dir = root / run_id
        seq += 1

    return run_id, run_dir


def write_daily_run_artifact(
    run_dir: Path,
    run_id: str,
    run_timestamp: datetime,
    *,
    dry_run: bool,
    rebalance_mode: str,
    status: str,
    trigger_actionable: bool,
    triggers: Iterable[str],
    trigger_str: str,
    regime: str,
    vix_close: float,
    frozen_signal_path: str,
    signal_summary: Dict[str, Any] | None,
    config: Dict[str, Any],
    strategy_base: Dict[str, Any] | None,
    strategy_resolved: Dict[str, Any] | None,
    scores_df: pd.DataFrame | None,
    recos_df: pd.DataFrame | None,
    portfolio_before_df: pd.DataFrame | None,
    portfolio_after_refresh_df: pd.DataFrame | None,
    scoring_meta: Dict[str, Any] | None,
    daily_buy_limit: float,
    holdings_value: float,
    cash_balance: float,
    total_capital: float,
    health: Dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)

    scores_df = scores_df.copy() if scores_df is not None else pd.DataFrame(columns=["Ticker", "Score", "Price"])
    recos_df = recos_df.copy() if recos_df is not None else pd.DataFrame()
    portfolio_before_df = portfolio_before_df.copy() if portfolio_before_df is not None else pd.DataFrame()
    portfolio_after_refresh_df = portfolio_after_refresh_df.copy() if portfolio_after_refresh_df is not None else pd.DataFrame()
    scoring_meta = dict(scoring_meta or {})
    signal_summary = dict(signal_summary or {})
    strategy_base = dict(strategy_base or {})
    strategy_resolved = dict(strategy_resolved or {})
    health = dict(health or {})

    action_counts = _action_counts(recos_df)
    scoring_date = scoring_meta.get("scoring_date", "")

    scores_export = _prepend_columns(
        scores_df,
        {
            "RunId": run_id,
            "ScoringDate": scoring_date,
            "Regime": regime,
        },
    )

    recos_export = recos_df.copy()
    recos_export.insert(0, "RecRowId", np.arange(1, len(recos_export) + 1, dtype=np.int64))
    recos_export = _prepend_columns(
        recos_export,
        {
            "RunId": run_id,
            "ScoringDate": scoring_date,
            "Actionable": bool(trigger_actionable),
        },
    )

    execution_template = pd.DataFrame(
        {
            "RunId": recos_export.get("RunId", pd.Series(dtype=str)),
            "RecRowId": recos_export.get("RecRowId", pd.Series(dtype="Int64")),
            "Ticker": recos_export.get("Ticker", pd.Series(dtype=str)),
            "Action": recos_export.get("Action", pd.Series(dtype=str)),
            "RecommendedShares": recos_export.get("Shares", pd.Series(dtype="Int64")),
            "RecommendedPrice": recos_export.get("Price", pd.Series(dtype=float)),
            "RecommendedCapital": recos_export.get("Capital", pd.Series(dtype=float)),
            "ExecuteFlag": False,
            "ExecutedShares": pd.Series([pd.NA] * len(recos_export), dtype="Int64"),
            "ExecutedPrice": pd.Series([np.nan] * len(recos_export), dtype=float),
            "ExecutionNote": "",
        }
    )

    market_snapshot = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "scoring_date": scoring_date,
        "scoring_index": scoring_meta.get("scoring_index"),
        "score_regime": scoring_meta.get("score_regime"),
        "selected_factor_count": scoring_meta.get("selected_factor_count"),
        "valid_ticker_count": scoring_meta.get("valid_ticker_count"),
        "ticker_count": scoring_meta.get("ticker_count"),
        "scored_count": int(len(scores_df)),
        "top_score": _safe_stat(scores_df, "Score", "max"),
        "median_score": _safe_stat(scores_df, "Score", "median"),
        "min_positive_score": _safe_positive_min(scores_df, "Score"),
    }

    recommendation_summary = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "counts": action_counts,
        "buy_capital_total": _safe_sum(recos_df, "Capital", {"BUY", "BUY_NEW", "BUY_MORE"}),
        "sell_value_total": _safe_sum(recos_df, "Capital", {"SELL", "STOP_LOSS", "TRIM", "DECREASE"}),
        "net_capital_delta": _safe_net_capital_delta(recos_df),
    }

    run_meta = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "run_timestamp": run_timestamp.isoformat(),
        "phase": "phase3",
        "mode": "dry_run" if dry_run else "live",
        "rebalance_mode": rebalance_mode,
        "status": status,
        "trigger_actionable": bool(trigger_actionable),
        "trigger_list": list(triggers),
        "trigger_str": trigger_str,
        "regime": regime,
        "vix_close": _to_basic(vix_close),
        "scoring_date": scoring_date,
        "frozen_signal_path": frozen_signal_path,
        "daily_buy_limit": _to_basic(daily_buy_limit),
        "cash_balance": _to_basic(cash_balance),
        "holdings_value": _to_basic(holdings_value),
        "total_capital": _to_basic(total_capital),
        "recommendation_count": int(len(recos_df)),
        "action_counts": action_counts,
        "health_overall": health.get("overall_status"),
        "post_refresh_stale_pct": _to_basic(health.get("post_refresh_stale_pct")),
        "error": error or "",
    }

    signal_path = Path(frozen_signal_path).expanduser()
    signal_snapshot = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "signal_path": frozen_signal_path,
        "signal_file": signal_path.name,
        "signal_exists": signal_path.exists(),
        "signal_mtime": _file_mtime(signal_path),
        "signal_summary": _sanitize(signal_summary),
    }

    config_snapshot = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "paths": _sanitize(config.get("paths", {})),
        "portfolio": _sanitize(config.get("portfolio", {})),
        "regime": _sanitize(config.get("regime", {})),
        "triggers": _sanitize(config.get("triggers", {})),
        "strategy_base": _sanitize(strategy_base),
        "strategy_resolved": _sanitize(strategy_resolved),
    }

    _write_json(run_dir / "run_meta.json", run_meta)
    _write_json(run_dir / "config_snapshot.json", config_snapshot)
    _write_json(run_dir / "signal_snapshot.json", signal_snapshot)
    _write_json(run_dir / "market_snapshot.json", market_snapshot)
    _write_json(run_dir / "recommendation_summary.json", recommendation_summary)
    _write_csv(run_dir / "portfolio_before.csv", portfolio_before_df)
    _write_csv(run_dir / "portfolio_after_price_refresh.csv", portfolio_after_refresh_df)
    _write_csv(run_dir / "scores.csv", scores_export)
    _write_csv(run_dir / "recommendations.csv", recos_export)
    _write_csv(run_dir / "execution_template.csv", execution_template)


def find_matching_execution_run(output_dir: str, recos_df: pd.DataFrame) -> Tuple[Optional[Path], Optional[Dict[str, Any]], pd.DataFrame]:
    """Find the latest pending artifact run that matches current recommendations."""
    if recos_df is None or recos_df.empty:
        return None, None, pd.DataFrame()

    root = Path(output_dir).expanduser() / "daily_runs"
    if not root.exists():
        return None, None, pd.DataFrame()

    candidates = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    for run_dir in candidates:
        meta_path = run_dir / "run_meta.json"
        rec_path = run_dir / "recommendations.csv"
        if not meta_path.exists() or not rec_path.exists():
            continue

        meta = _read_json(meta_path)
        if str(meta.get("status", "")) not in {"awaiting_execution", "partially_executed", "executed"}:
            continue

        try:
            artifact_recos = pd.read_csv(rec_path)
        except Exception:
            continue

        if _recommendations_match(recos_df, artifact_recos):
            return run_dir, meta, artifact_recos

    return None, None, pd.DataFrame()


def load_last_execution_batch(run_dir: Path) -> pd.DataFrame:
    """Return the latest execution batch for a run based on ExecutionTimestamp."""
    applied_path = run_dir / "execution_applied.csv"
    if not applied_path.exists():
        return pd.DataFrame()

    applied = pd.read_csv(applied_path)
    if applied.empty or "ExecutionTimestamp" not in applied.columns:
        return pd.DataFrame()

    ts_values = applied["ExecutionTimestamp"].astype(str)
    last_ts = ts_values.iloc[-1]
    return applied.loc[ts_values == last_ts].copy().reset_index(drop=True)


def record_execution_artifact(
    run_dir: Path,
    executed_df: pd.DataFrame,
    *,
    source: str,
    total_checkable_count: int,
    portfolio_after_execution_df: pd.DataFrame,
    cash_balance: float,
    total_capital: float,
    operator_note: str = "",
) -> Dict[str, Any]:
    """Append execution results to an existing run artifact and update run status."""
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"run_meta.json not found: {meta_path}")

    run_meta = _read_json(meta_path)
    run_id = str(run_meta.get("run_id", run_dir.name))
    exec_timestamp = datetime.now().astimezone()

    executed_export = executed_df.copy() if executed_df is not None else pd.DataFrame()
    if executed_export.empty:
        raise ValueError("executed_df is empty")

    if "RunId" not in executed_export.columns:
        executed_export.insert(0, "RunId", run_id)
    if "RecRowId" not in executed_export.columns:
        executed_export.insert(1, "RecRowId", pd.Series([pd.NA] * len(executed_export), dtype="Int64"))

    executed_export = _prepend_columns(
        executed_export,
        {
            "ExecutionTimestamp": exec_timestamp.isoformat(),
            "Source": source,
        },
    )
    if "ExecutedValue" not in executed_export.columns:
        price = pd.to_numeric(executed_export.get("ExecutedPrice"), errors="coerce").fillna(0.0)
        shares = pd.to_numeric(executed_export.get("ExecutedShares"), errors="coerce").fillna(0.0)
        executed_export["ExecutedValue"] = price * shares

    applied_path = run_dir / "execution_applied.csv"
    if applied_path.exists():
        prev = pd.read_csv(applied_path)
        combined = pd.concat([prev, executed_export], ignore_index=True)
    else:
        combined = executed_export
    _write_csv(applied_path, combined)

    executed_ids = set()
    if "RecRowId" in combined.columns:
        executed_ids = {
            int(v) for v in pd.to_numeric(combined["RecRowId"], errors="coerce").dropna().astype(int).tolist()
        }
    executed_total = len(executed_ids) if executed_ids else int(len(combined))
    if total_checkable_count > 0 and executed_total >= total_checkable_count:
        exec_status = "executed"
    else:
        exec_status = "partially_executed"

    execution_meta = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "source": source,
        "execution_timestamp": exec_timestamp.isoformat(),
        "execution_status": exec_status,
        "executed_row_count_this_update": int(len(executed_export)),
        "executed_row_count_total": int(len(combined)),
        "executed_recommendation_count": int(executed_total),
        "total_checkable_count": int(total_checkable_count),
        "cash_balance": _to_basic(cash_balance),
        "total_capital": _to_basic(total_capital),
        "operator_note": operator_note,
    }
    _write_json(run_dir / "execution_meta.json", execution_meta)
    _write_csv(run_dir / "portfolio_after_execution.csv", portfolio_after_execution_df.copy())

    run_meta["status"] = exec_status
    run_meta["last_execution_timestamp"] = exec_timestamp.isoformat()
    run_meta["executed_row_count_total"] = int(len(combined))
    run_meta["executed_recommendation_count"] = int(executed_total)
    run_meta["total_checkable_count"] = int(total_checkable_count)
    _write_json(meta_path, run_meta)
    return execution_meta


def record_execution_reversal(
    run_dir: Path,
    reverted_batch_df: pd.DataFrame,
    reversal_applied_df: pd.DataFrame,
    *,
    source: str,
    total_checkable_count: int,
    portfolio_after_execution_df: pd.DataFrame,
    cash_balance: float,
    total_capital: float,
    operator_note: str = "",
) -> Dict[str, Any]:
    """Reverse the latest execution batch in artifact storage and update run status."""
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"run_meta.json not found: {meta_path}")

    reverted_batch_df = reverted_batch_df.copy()
    reversal_applied_df = reversal_applied_df.copy()
    if reverted_batch_df.empty:
        raise ValueError("reverted_batch_df is empty")

    applied_path = run_dir / "execution_applied.csv"
    if not applied_path.exists():
        raise FileNotFoundError(f"execution_applied.csv not found: {applied_path}")

    applied = pd.read_csv(applied_path)
    if applied.empty or "ExecutionTimestamp" not in reverted_batch_df.columns:
        raise ValueError("No execution batch available to reverse")

    batch_ts = str(reverted_batch_df["ExecutionTimestamp"].astype(str).iloc[0])
    remaining = applied.loc[applied["ExecutionTimestamp"].astype(str) != batch_ts].copy()
    _write_csv(applied_path, remaining)

    reversal_ts = datetime.now().astimezone().isoformat()
    reverted_export = reverted_batch_df.copy()
    reverted_export.insert(0, "ReversalTimestamp", reversal_ts)
    reverted_export.insert(1, "ReversalSource", source)
    reverted_export.insert(2, "BatchExecutionTimestamp", batch_ts)

    reversed_path = run_dir / "execution_reverted.csv"
    if reversed_path.exists():
        prev = pd.read_csv(reversed_path)
        reverted_export = pd.concat([prev, reverted_export], ignore_index=True)
    _write_csv(reversed_path, reverted_export)

    effective_ids = set()
    if "RecRowId" in remaining.columns:
        effective_ids = {
            int(v) for v in pd.to_numeric(remaining["RecRowId"], errors="coerce").dropna().astype(int).tolist()
        }
    effective_count = len(effective_ids) if effective_ids else int(len(remaining))
    if effective_count <= 0:
        exec_status = "awaiting_execution"
    elif total_checkable_count > 0 and effective_count >= total_checkable_count:
        exec_status = "executed"
    else:
        exec_status = "partially_executed"

    reversal_meta = {
        "schema_version": "artifact/v1",
        "run_id": str(_read_json(meta_path).get("run_id", run_dir.name)),
        "reversal_timestamp": reversal_ts,
        "source": source,
        "reversed_batch_execution_timestamp": batch_ts,
        "reversed_row_count": int(len(reverted_batch_df)),
        "remaining_effective_row_count": int(len(remaining)),
        "effective_executed_recommendation_count": int(effective_count),
        "total_checkable_count": int(total_checkable_count),
        "execution_status": exec_status,
        "cash_balance": _to_basic(cash_balance),
        "total_capital": _to_basic(total_capital),
        "operator_note": operator_note,
    }
    _write_json(run_dir / "reversal_meta.json", reversal_meta)
    _write_csv(run_dir / "portfolio_after_execution.csv", portfolio_after_execution_df.copy())

    run_meta = _read_json(meta_path)
    run_meta["status"] = exec_status
    run_meta["last_reversal_timestamp"] = reversal_ts
    run_meta["executed_row_count_total"] = int(len(remaining))
    run_meta["executed_recommendation_count"] = int(effective_count)
    run_meta["total_checkable_count"] = int(total_checkable_count)
    _write_json(meta_path, run_meta)

    execution_meta_path = run_dir / "execution_meta.json"
    if execution_meta_path.exists():
        exec_meta = _read_json(execution_meta_path)
        exec_meta["execution_status"] = exec_status
        exec_meta["last_reversal_timestamp"] = reversal_ts
        exec_meta["executed_row_count_total"] = int(len(remaining))
        exec_meta["executed_recommendation_count"] = int(effective_count)
        exec_meta["cash_balance"] = _to_basic(cash_balance)
        exec_meta["total_capital"] = _to_basic(total_capital)
        _write_json(execution_meta_path, exec_meta)

    return reversal_meta


def _prepend_columns(df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    for key, value in reversed(list(meta.items())):
        out.insert(0, key, value)
    return out


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(_sanitize(payload), f, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def _action_counts(df: pd.DataFrame) -> Dict[str, int]:
    if df is None or df.empty or "Action" not in df.columns:
        return {}
    vc = df["Action"].astype(str).value_counts()
    return {str(k): int(v) for k, v in vc.items()}


def _recommendations_match(recos_df: pd.DataFrame, artifact_recos_df: pd.DataFrame) -> bool:
    if recos_df is None or artifact_recos_df is None:
        return False
    if len(recos_df) != len(artifact_recos_df):
        return False

    cols = [c for c in _RECO_COMPARE_COLUMNS if c in recos_df.columns and c in artifact_recos_df.columns]
    if not cols:
        return False

    left = recos_df.reset_index(drop=True)
    right = artifact_recos_df.reset_index(drop=True)
    for col in cols:
        left_vals = [_compare_value(v) for v in left[col].tolist()]
        right_vals = [_compare_value(v) for v in right[col].tolist()]
        if left_vals != right_vals:
            return False
    return True


def _compare_value(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        return str(value)
    if isinstance(value, np.generic):
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return round(float(value), 8)
    return str(value)


def _safe_stat(df: pd.DataFrame, column: str, fn: str) -> float | None:
    if df is None or df.empty or column not in df.columns:
        return None
    series = pd.to_numeric(df[column], errors="coerce").dropna()
    if series.empty:
        return None
    if fn == "max":
        return float(series.max())
    if fn == "median":
        return float(series.median())
    return None


def _safe_positive_min(df: pd.DataFrame, column: str) -> float | None:
    if df is None or df.empty or column not in df.columns:
        return None
    series = pd.to_numeric(df[column], errors="coerce")
    series = series[series > 0].dropna()
    if series.empty:
        return None
    return float(series.min())


def _safe_sum(df: pd.DataFrame, value_column: str, actions: set[str]) -> float:
    if df is None or df.empty or value_column not in df.columns or "Action" not in df.columns:
        return 0.0
    mask = df["Action"].astype(str).isin(actions)
    vals = pd.to_numeric(df.loc[mask, value_column], errors="coerce").fillna(0.0)
    return float(vals.sum())


def _safe_net_capital_delta(df: pd.DataFrame) -> float:
    if df is None or df.empty or "Capital" not in df.columns or "Action" not in df.columns:
        return 0.0
    buys = _safe_sum(df, "Capital", {"BUY", "BUY_NEW", "BUY_MORE"})
    sells = _safe_sum(df, "Capital", {"SELL", "STOP_LOSS", "TRIM", "DECREASE"})
    return float(sells - buys)


def _file_mtime(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(tok in lower for tok in _REDACT_KEYS):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = _sanitize(item)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return _to_basic(value)


def _to_basic(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (str, bytes, dict, list, tuple, set)) else False:
        return None
    return value
