#!/usr/bin/env python3
"""Stateful shadow portfolio ledger replay.

Phase A goal
------------
Replay existing daily/shadow score artifacts through an independent virtual
portfolio ledger so a shadow signal can be compared against the live baseline
as if both accounts traded their own recommendations.

Important constraints:
  * This module does not mutate holdings_log.xlsx, autotrade ledgers, or
    daily_run artifacts.
  * Fills are synthetic: every actionable recommendation is assumed to fill
    100% at the artifact price plus configured slippage/commission.
  * Both baseline and shadow use the same recommendation engine and the same
    virtual starting portfolio.

Typical use:
    python3 phase3/shadow_ledger.py replay \
      --start 2026-05-08 --end 2026-06-07 \
      --shadow-label P11_FUNDB_ANCHOR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml


PHASE3_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PHASE3_DIR.parent
for _p in (str(PHASE3_DIR), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Keep matplotlib's font-cache writable in headless / sandboxed runs.
# (The Codex mirror's CODEX_MIRROR_ALLOW_RUN guard is intentionally NOT
# set here: production daily_runner.py has no such gate, and silently
# poking that env var would auto-bypass any future guard with the same
# name.)
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-codex")

from daily_runner import build_engine_cfg, generate_recommendations  # noqa: E402
from simulator import SimPortfolio, _compute_daily_limit, resolve_strategy  # noqa: E402


DEFAULT_PROD_DAILY_RUNS = Path(
    "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/daily_runs"
)


@dataclass(frozen=True)
class ArtifactPair:
    run_id: str
    run_date: str
    baseline_dir: Path
    shadow_dir: Path
    label: str
    regime: str
    vix_close: float
    signature: str


@dataclass
class LedgerResult:
    name: str
    daily: pd.DataFrame
    trades: pd.DataFrame
    final_holdings: pd.DataFrame
    buy_grace_filtered_total: int = 0
    buy_grace_filter_days: int = 0


@dataclass
class ReplayJob:
    summary: Dict[str, Any]
    output_dir: Path
    pairs: List[ArtifactPair]


class BuyGraceState:
    """In-memory top-N persistence tracker for one virtual ledger."""

    def __init__(self) -> None:
        self.history: List[set[str]] = []
        self.filtered_total = 0
        self.filter_days = 0

    def blocked_tickers(
        self,
        scores_df: pd.DataFrame,
        portfolio: SimPortfolio,
        cfg: Any,
        regime: str,
        strategy_conf: Dict[str, Any],
    ) -> set[str]:
        if scores_df.empty:
            return set()

        try:
            grace_days = int(strategy_conf.get("buy_grace_days", 0) or 0)
        except (TypeError, ValueError):
            grace_days = 0

        top_n = _regime_top_n(cfg, regime)
        prefilter_topn = set(scores_df["Ticker"].astype(str).head(top_n).tolist())
        blocked: set[str] = set()

        if grace_days > 0 and len(self.history) >= grace_days:
            persistent = set.intersection(*self.history[-grace_days:])
            all_scored = set(scores_df["Ticker"].astype(str).tolist())
            blocked = all_scored - persistent
            if blocked:
                self.filtered_total += int(len(blocked))
                self.filter_days += 1

        self.history.append(prefilter_topn)
        if len(self.history) > 64:
            self.history = self.history[-64:]
        return blocked


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if np.isfinite(out):
            return out
    except Exception:
        pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def _date_from_run_id(run_id: str) -> str:
    raw = str(run_id)[:8]
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def _sanitize_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label).strip())
    return cleaned.strip("_") or "shadow"


def _regime_top_n(cfg: Any, regime: str) -> int:
    if regime == "BULL":
        return int(getattr(cfg, "regime_bull_top_n", 20))
    if regime in ("DEFENSIVE", "CRASH", "BEAR", "DEF"):
        return int(getattr(cfg, "regime_defensive_top_n", 10))
    return int(getattr(cfg, "regime_side_top_n", 15))


def _load_scores(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["Ticker", "Score", "Price", "Regime"])
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=["Ticker", "Score", "Price", "Regime"])
    out = df.copy()
    out["Ticker"] = out["Ticker"].astype(str)
    out["Score"] = pd.to_numeric(out["Score"], errors="coerce")
    out["Price"] = pd.to_numeric(out["Price"], errors="coerce")
    out = out.dropna(subset=["Ticker", "Score", "Price"])
    out = out[out["Price"] > 0].sort_values("Score", ascending=False).reset_index(drop=True)
    return out


def _score_signature(scores_path: Path, rows: int = 64) -> str:
    df = _load_scores(scores_path).head(rows)
    if df.empty:
        return ""
    payload = "|".join(
        f"{r.Ticker}:{float(r.Score):.4f}:{float(r.Price):.4f}"
        for r in df.itertuples(index=False)
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _infer_artifact_meta(run_dir: Path, fallback_run_id: str) -> Tuple[str, float]:
    meta = _read_json(run_dir / "run_meta.json")
    snap = _read_json(run_dir / "market_snapshot.json")
    regime = str(meta.get("regime") or snap.get("regime") or "SIDE")
    vix = _safe_float(meta.get("vix_close", snap.get("vix_close", 20.0)), 20.0)
    if not regime:
        scores = _load_scores(run_dir / "scores.csv")
        if not scores.empty and "Regime" in scores.columns:
            regime = str(scores["Regime"].dropna().iloc[0])
    return regime or "SIDE", vix


def _discover_daily_runs_dir(conf: Dict[str, Any], override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    out_dir = Path(conf.get("paths", {}).get("output_dir", "")).expanduser()
    configured = (out_dir / "daily_runs").resolve()
    if list(configured.glob("*_shadow")):
        return configured
    if DEFAULT_PROD_DAILY_RUNS.exists() and list(DEFAULT_PROD_DAILY_RUNS.glob("*_shadow")):
        return DEFAULT_PROD_DAILY_RUNS
    return configured


def _discover_pairs(
    daily_runs_dir: Path,
    shadow_label: str,
    start: Optional[str],
    end: Optional[str],
    include_duplicate_scores: bool,
) -> List[ArtifactPair]:
    pairs: List[ArtifactPair] = []
    seen_signatures: set[str] = set()

    for shadow_dir in sorted(daily_runs_dir.glob("*_shadow")):
        run_id = shadow_dir.name
        base_stem = run_id[: -len("_shadow")]
        baseline_dir = daily_runs_dir / f"{base_stem}_daily"
        if not baseline_dir.exists():
            continue

        summary = _read_json(shadow_dir / "shadow_diff_summary.json")
        label = str(summary.get("label") or "")
        if shadow_label and label != shadow_label:
            continue

        run_date = str(summary.get("date") or _date_from_run_id(run_id))
        if start and run_date < start:
            continue
        if end and run_date > end:
            continue

        baseline_scores = baseline_dir / "scores.csv"
        shadow_scores = shadow_dir / "scores.csv"
        if not baseline_scores.exists() or not shadow_scores.exists():
            continue

        signature = _score_signature(shadow_scores)
        if signature and not include_duplicate_scores and signature in seen_signatures:
            continue
        if signature:
            seen_signatures.add(signature)

        regime, vix = _infer_artifact_meta(shadow_dir, run_id)
        pairs.append(ArtifactPair(
            run_id=run_id,
            run_date=run_date,
            baseline_dir=baseline_dir,
            shadow_dir=shadow_dir,
            label=label or shadow_label,
            regime=regime,
            vix_close=vix,
            signature=signature,
        ))

    return pairs


def _resolve_initial_path(
    initial_from: Optional[str],
    first_pair: ArtifactPair,
    daily_runs_dir: Path,
) -> Path:
    if initial_from:
        p = Path(initial_from).expanduser()
        if p.is_absolute():
            return p
        direct = (Path.cwd() / p).resolve()
        if direct.exists():
            return direct
        return (daily_runs_dir / p).resolve()
    return first_pair.baseline_dir / "portfolio_before.csv"


def _seed_portfolio_from_current(
    current_path: Path,
    total_capital: float,
    override_cash: Optional[float] = None,
) -> SimPortfolio:
    if not current_path.exists():
        raise FileNotFoundError(f"initial holdings file not found: {current_path}")

    df = pd.read_csv(current_path)
    if df.empty:
        return SimPortfolio(float(total_capital if override_cash is None else override_cash))

    rows = []
    holding_value = 0.0
    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        shares = _safe_int(row.get("Shares"), 0)
        if not ticker or shares <= 0:
            continue
        current_price = _safe_float(
            row.get("CurrentPrice", row.get("Price", row.get("BuyPrice"))),
            _safe_float(row.get("BuyPrice"), 0.0),
        )
        buy_price = _safe_float(row.get("BuyPrice"), current_price)
        market_value = _safe_float(row.get("MarketValue"), current_price * shares)
        holding_value += market_value
        rows.append((ticker, shares, buy_price, current_price, row))

    cash = float(override_cash) if override_cash is not None else max(float(total_capital) - holding_value, 0.0)
    portfolio = SimPortfolio(cash)
    for ticker, shares, buy_price, current_price, row in rows:
        profit_targets_hit = row.get("ProfitTargetsHit", "")
        if isinstance(profit_targets_hit, str) and profit_targets_hit.strip():
            try:
                parsed = json.loads(profit_targets_hit)
                profit_targets = set(float(x) for x in parsed)
            except Exception:
                profit_targets = set()
        else:
            profit_targets = set()

        portfolio.holdings[ticker] = {
            "shares": int(shares),
            "avg_cost": float(buy_price),
            "current_price": float(current_price),
            "entry_date": str(row.get("BuyDate", ""))[:10],
            "entry_price": float(buy_price),
            "entry_score": _safe_float(row.get("EntryScore"), 0.0),
            "entry_rank": _safe_int(row.get("EntryRank"), -1),
            "entry_regime": str(row.get("EntryRegime", "") or ""),
            "peak_price": max(
                _safe_float(row.get("PeakPrice"), 0.0),
                float(current_price),
                float(buy_price),
            ),
            "last_score": _safe_float(row.get("LastScore"), 0.0),
            "profit_targets_hit": profit_targets,
        }
    return portfolio


def _clone_portfolio(portfolio: SimPortfolio) -> SimPortfolio:
    cloned = SimPortfolio(float(portfolio.cash))
    cloned.initial_cash = float(portfolio.initial_cash)
    cloned.total_commission = float(portfolio.total_commission)
    cloned.holdings = {}
    for ticker, holding in portfolio.holdings.items():
        copied = dict(holding)
        if isinstance(copied.get("profit_targets_hit"), set):
            copied["profit_targets_hit"] = set(copied["profit_targets_hit"])
        cloned.holdings[ticker] = copied
    return cloned


def _mark_to_market(portfolio: SimPortfolio, scores_df: pd.DataFrame) -> Dict[str, float]:
    price_map = dict(zip(scores_df["Ticker"].astype(str), scores_df["Price"].astype(float)))
    portfolio.update_prices(price_map)
    return price_map


def _snapshot_nav(
    portfolio: SimPortfolio,
    date: str,
    name: str,
    run_id: str,
    regime: str,
    vix_close: float,
    actions: pd.DataFrame,
    trade_rows: Sequence[Dict[str, Any]],
    prev_nav: Optional[float],
) -> Dict[str, Any]:
    holdings_value = float(portfolio.get_value())
    cash = float(portfolio.cash)
    nav = holdings_value + cash
    daily_ret = 0.0 if not prev_nav or prev_nav <= 0 else nav / prev_nav - 1.0
    trade_value = float(sum(abs(float(t.get("Value", 0.0))) for t in trade_rows))
    buy_count = int(actions["Action"].isin(["BUY_NEW", "BUY_MORE"]).sum()) if not actions.empty else 0
    sell_count = 0
    trim_count = 0
    if not actions.empty:
        from exits import RecosAction

        sell_count = int(actions["Action"].map(RecosAction.is_full_close).sum())
        trim_count = int(actions["Action"].map(RecosAction.is_partial_close).sum())
    return {
        "Date": date,
        "Ledger": name,
        "RunId": run_id,
        "Regime": regime,
        "VIX": round(float(vix_close), 4),
        "Cash": round(cash, 2),
        "HoldingsValue": round(holdings_value, 2),
        "NAV": round(nav, 2),
        "DailyReturn": round(float(daily_ret), 8),
        "PositionCount": len(portfolio.holdings),
        "BuyCount": buy_count,
        "SellCount": sell_count,
        "TrimCount": trim_count,
        "RecoCount": int(len(actions)),
        "TradeCount": int(len(trade_rows)),
        "TurnoverPct": round(trade_value / nav * 100.0, 4) if nav > 0 else 0.0,
    }


def _run_one_ledger(
    name: str,
    pairs: Sequence[ArtifactPair],
    use_shadow_scores: bool,
    initial_portfolio: SimPortfolio,
    cfg: Any,
    conf: Dict[str, Any],
    commission_bps: float,
    slippage_bps: float,
    daily_buy_limit_override: Optional[float],
) -> LedgerResult:
    portfolio = _clone_portfolio(initial_portfolio)
    buy_grace = BuyGraceState()
    daily_rows: List[Dict[str, Any]] = []
    prev_nav: Optional[float] = None
    vix_series: List[float] = []
    regime_series: List[str] = []
    portfolio_peak = max(float(portfolio.get_value()) + float(portfolio.cash), 1.0)

    base_strategy = dict(conf.get("strategy", {}) or {})
    fixed_daily_limit = (
        float(daily_buy_limit_override)
        if daily_buy_limit_override is not None
        else float(conf.get("portfolio", {}).get("daily_buy_limit", 1000.0))
    )

    for pair in pairs:
        run_dir = pair.shadow_dir if use_shadow_scores else pair.baseline_dir
        scores_df = _load_scores(run_dir / "scores.csv")
        if scores_df.empty:
            continue
        regime = pair.regime
        if "Regime" in scores_df.columns and not scores_df["Regime"].dropna().empty:
            regime = str(scores_df["Regime"].dropna().iloc[0]) or regime

        price_map = _mark_to_market(portfolio, scores_df)
        holdings_value = float(portfolio.get_value(price_map))
        cash = float(portfolio.get_cash_balance())
        total_capital = holdings_value + max(cash, 0.0)
        if total_capital <= 0:
            total_capital = float(conf.get("portfolio", {}).get("total_capital", 100000.0))

        strategy = resolve_strategy(base_strategy, regime)
        buy_grace_blocked = buy_grace.blocked_tickers(scores_df, portfolio, cfg, regime, strategy)
        daily_limit = _compute_daily_limit(cash, holdings_value, strategy, fixed_daily_limit)
        vix_series.append(float(pair.vix_close))
        recent_regimes = regime_series + [regime]

        before_trade_n = len(portfolio.trade_log)
        recos = generate_recommendations(
            cfg,
            scores_df,
            regime,
            float(pair.vix_close),
            portfolio,
            total_capital,
            daily_buy_limit=daily_limit,
            strategy_conf=strategy,
            sim_date=pair.run_date,
            history=None,
            vix_series=vix_series[-10:],
            recent_regimes=recent_regimes[-10:],
            portfolio_peak=portfolio_peak,
            buy_grace_blocked=buy_grace_blocked,
        )
        if not recos.empty:
            portfolio.apply_actions(
                recos,
                price_map,
                pair.run_date,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
            )
            portfolio.save_recommendations(recos)

        new_trade_rows = portfolio.trade_log[before_trade_n:]
        nav_after = float(portfolio.get_value()) + float(portfolio.cash)
        portfolio_peak = max(portfolio_peak, nav_after)
        daily_rows.append(
            _snapshot_nav(
                portfolio,
                pair.run_date,
                name,
                run_dir.name,
                regime,
                pair.vix_close,
                recos,
                new_trade_rows,
                prev_nav,
            )
        )
        prev_nav = nav_after
        regime_series.append(regime)

    daily = pd.DataFrame(daily_rows)
    trades = pd.DataFrame(portfolio.trade_log)
    final_holdings = portfolio.load_current()
    if not final_holdings.empty:
        total_mv = pd.to_numeric(final_holdings["MarketValue"], errors="coerce").fillna(0.0).sum()
        if total_mv > 0:
            final_holdings["Weight"] = (
                pd.to_numeric(final_holdings["MarketValue"], errors="coerce").fillna(0.0)
                / total_mv
                * 100.0
            ).round(4)
        final_holdings = final_holdings.sort_values("MarketValue", ascending=False).reset_index(drop=True)
    return LedgerResult(
        name=name,
        daily=daily,
        trades=trades,
        final_holdings=final_holdings,
        buy_grace_filtered_total=buy_grace.filtered_total,
        buy_grace_filter_days=buy_grace.filter_days,
    )


def _metrics(daily: pd.DataFrame) -> Dict[str, Any]:
    if daily.empty:
        return {
            "start_nav": 0.0,
            "final_nav": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_daily_turnover_pct": 0.0,
            "trade_count": 0,
        }
    nav = daily["NAV"].astype(float)
    start_nav = float(nav.iloc[0])
    final_nav = float(nav.iloc[-1])
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return {
        "start_nav": round(start_nav, 2),
        "final_nav": round(final_nav, 2),
        "total_return_pct": round((final_nav / start_nav - 1.0) * 100.0, 4) if start_nav > 0 else 0.0,
        "max_drawdown_pct": round(float(dd.min()) * 100.0, 4),
        "avg_daily_turnover_pct": round(float(daily["TurnoverPct"].mean()), 4),
        "trade_count": int(daily["TradeCount"].sum()),
        "buy_count": int(daily["BuyCount"].sum()),
        "sell_count": int(daily["SellCount"].sum()),
        "trim_count": int(daily["TrimCount"].sum()),
        "final_position_count": int(daily["PositionCount"].iloc[-1]),
    }


def _write_outputs(
    output_dir: Path,
    baseline: LedgerResult,
    shadow: LedgerResult,
    pairs: Sequence[ArtifactPair],
    args: argparse.Namespace,
    initial_path: Path,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline.daily.to_csv(output_dir / "baseline_daily_nav.csv", index=False)
    shadow.daily.to_csv(output_dir / "shadow_daily_nav.csv", index=False)
    baseline.trades.to_csv(output_dir / "baseline_trades.csv", index=False)
    shadow.trades.to_csv(output_dir / "shadow_trades.csv", index=False)
    baseline.final_holdings.to_csv(output_dir / "baseline_holdings_final.csv", index=False)
    shadow.final_holdings.to_csv(output_dir / "shadow_holdings_final.csv", index=False)
    nav_compare = _build_nav_compare(baseline.daily, shadow.daily)
    holdings_compare = _build_holdings_compare(
        baseline.final_holdings,
        shadow.final_holdings,
    )
    nav_compare.to_csv(output_dir / "nav_compare.csv", index=False)
    holdings_compare.to_csv(output_dir / "holdings_compare_final.csv", index=False)
    if not holdings_compare.empty:
        holdings_compare[holdings_compare["BaselineShares"].fillna(0) <= 0].to_csv(
            output_dir / "shadow_only_holdings_final.csv",
            index=False,
        )
        holdings_compare[holdings_compare["ShadowShares"].fillna(0) <= 0].to_csv(
            output_dir / "baseline_only_holdings_final.csv",
            index=False,
        )

    b = _metrics(baseline.daily)
    s = _metrics(shadow.daily)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "scores_artifact_replay",
        "shadow_label": args.shadow_label,
        "start": pairs[0].run_date if pairs else None,
        "end": pairs[-1].run_date if pairs else None,
        "run_count": len(pairs),
        "run_ids": [p.run_id for p in pairs],
        "initial_from": str(initial_path),
        "fill_model": {
            "fill_price": "artifact_price",
            "fill_ratio": 1.0,
            "commission_bps": float(args.commission_bps),
            "slippage_bps": float(args.slippage_bps),
        },
        "baseline": {
            **b,
            "buy_grace_filtered_total": baseline.buy_grace_filtered_total,
            "buy_grace_filter_days": baseline.buy_grace_filter_days,
        },
        "shadow": {
            **s,
            "buy_grace_filtered_total": shadow.buy_grace_filtered_total,
            "buy_grace_filter_days": shadow.buy_grace_filter_days,
        },
        "comparison": {
            "shadow_minus_baseline_final_nav": round(s["final_nav"] - b["final_nav"], 2),
            "shadow_minus_baseline_return_pp": round(
                s["total_return_pct"] - b["total_return_pct"], 4
            ),
            "shadow_minus_baseline_mdd_pp": round(
                s["max_drawdown_pct"] - b["max_drawdown_pct"], 4
            ),
        },
        "files": {
            "baseline_daily_nav": str(output_dir / "baseline_daily_nav.csv"),
            "shadow_daily_nav": str(output_dir / "shadow_daily_nav.csv"),
            "nav_compare": str(output_dir / "nav_compare.csv"),
            "baseline_trades": str(output_dir / "baseline_trades.csv"),
            "shadow_trades": str(output_dir / "shadow_trades.csv"),
            "baseline_holdings_final": str(output_dir / "baseline_holdings_final.csv"),
            "shadow_holdings_final": str(output_dir / "shadow_holdings_final.csv"),
            "holdings_compare_final": str(output_dir / "holdings_compare_final.csv"),
        },
    }
    (output_dir / "compare_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_markdown_summary(output_dir / "compare_summary.md", summary)
    return summary


def _build_nav_compare(baseline_daily: pd.DataFrame, shadow_daily: pd.DataFrame) -> pd.DataFrame:
    if baseline_daily.empty or shadow_daily.empty:
        return pd.DataFrame(columns=[
            "Date", "BaselineNAV", "ShadowNAV", "NavDelta",
            "BaselineDailyReturn", "ShadowDailyReturn", "DailyReturnDelta",
            "BaselinePositions", "ShadowPositions", "PositionDelta",
        ])

    b = baseline_daily[[
        "Date", "NAV", "DailyReturn", "PositionCount", "TradeCount", "TurnoverPct"
    ]].rename(columns={
        "NAV": "BaselineNAV",
        "DailyReturn": "BaselineDailyReturn",
        "PositionCount": "BaselinePositions",
        "TradeCount": "BaselineTradeCount",
        "TurnoverPct": "BaselineTurnoverPct",
    })
    s = shadow_daily[[
        "Date", "NAV", "DailyReturn", "PositionCount", "TradeCount", "TurnoverPct"
    ]].rename(columns={
        "NAV": "ShadowNAV",
        "DailyReturn": "ShadowDailyReturn",
        "PositionCount": "ShadowPositions",
        "TradeCount": "ShadowTradeCount",
        "TurnoverPct": "ShadowTurnoverPct",
    })
    out = b.merge(s, on="Date", how="outer").sort_values("Date").reset_index(drop=True)
    out["NavDelta"] = (out["ShadowNAV"] - out["BaselineNAV"]).round(2)
    out["DailyReturnDelta"] = (out["ShadowDailyReturn"] - out["BaselineDailyReturn"]).round(8)
    out["PositionDelta"] = (
        out["ShadowPositions"].fillna(0).astype(int)
        - out["BaselinePositions"].fillna(0).astype(int)
    )
    out["TradeCountDelta"] = (
        out["ShadowTradeCount"].fillna(0).astype(int)
        - out["BaselineTradeCount"].fillna(0).astype(int)
    )
    out["TurnoverDeltaPct"] = (
        out["ShadowTurnoverPct"].fillna(0.0)
        - out["BaselineTurnoverPct"].fillna(0.0)
    ).round(4)
    cols = [
        "Date", "BaselineNAV", "ShadowNAV", "NavDelta",
        "BaselineDailyReturn", "ShadowDailyReturn", "DailyReturnDelta",
        "BaselinePositions", "ShadowPositions", "PositionDelta",
        "BaselineTradeCount", "ShadowTradeCount", "TradeCountDelta",
        "BaselineTurnoverPct", "ShadowTurnoverPct", "TurnoverDeltaPct",
    ]
    return out[cols]


def _build_holdings_compare(
    baseline_holdings: pd.DataFrame,
    shadow_holdings: pd.DataFrame,
) -> pd.DataFrame:
    cols = [
        "Ticker", "BaselineShares", "ShadowShares", "ShareDelta",
        "BaselineMarketValue", "ShadowMarketValue", "MarketValueDelta",
        "BaselineWeight", "ShadowWeight", "WeightDelta",
    ]
    if baseline_holdings.empty and shadow_holdings.empty:
        return pd.DataFrame(columns=cols)

    def _prep(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["Ticker", f"{prefix}Shares", f"{prefix}MarketValue", f"{prefix}Weight"])
        out = df.copy()
        out["Ticker"] = out["Ticker"].astype(str)
        return out[["Ticker", "Shares", "MarketValue", "Weight"]].rename(columns={
            "Shares": f"{prefix}Shares",
            "MarketValue": f"{prefix}MarketValue",
            "Weight": f"{prefix}Weight",
        })

    b = _prep(baseline_holdings, "Baseline")
    s = _prep(shadow_holdings, "Shadow")
    out = b.merge(s, on="Ticker", how="outer")
    for col in ("BaselineShares", "ShadowShares"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    for col in ("BaselineMarketValue", "ShadowMarketValue", "BaselineWeight", "ShadowWeight"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out["ShareDelta"] = out["ShadowShares"] - out["BaselineShares"]
    out["MarketValueDelta"] = (out["ShadowMarketValue"] - out["BaselineMarketValue"]).round(2)
    out["WeightDelta"] = (out["ShadowWeight"] - out["BaselineWeight"]).round(4)
    return out[cols].sort_values("MarketValueDelta", ascending=False).reset_index(drop=True)


def _write_markdown_summary(path: Path, summary: Dict[str, Any]) -> None:
    b = summary["baseline"]
    s = summary["shadow"]
    c = summary["comparison"]
    lines = [
        "# Stateful Shadow Ledger Replay",
        "",
        f"- Shadow label: `{summary['shadow_label']}`",
        f"- Window: `{summary['start']}` to `{summary['end']}` ({summary['run_count']} runs)",
        f"- Initial holdings: `{summary['initial_from']}`",
        f"- Fill model: artifact price, 100% fill, commission {summary['fill_model']['commission_bps']} bps, slippage {summary['fill_model']['slippage_bps']} bps",
        "",
        "| Ledger | Final NAV | Return | MDD | Trades | Final Positions |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Baseline | ${b['final_nav']:,.2f} | {b['total_return_pct']:+.4f}% | {b['max_drawdown_pct']:+.4f}% | {b['trade_count']} | {b['final_position_count']} |",
        f"| Shadow | ${s['final_nav']:,.2f} | {s['total_return_pct']:+.4f}% | {s['max_drawdown_pct']:+.4f}% | {s['trade_count']} | {s['final_position_count']} |",
        "",
        f"- Shadow final NAV delta: ${c['shadow_minus_baseline_final_nav']:,.2f}",
        f"- Shadow return delta: {c['shadow_minus_baseline_return_pp']:+.4f} pp",
        f"- Shadow MDD delta: {c['shadow_minus_baseline_mdd_pp']:+.4f} pp",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _resolve_shadow_label(conf: Dict[str, Any], label: Optional[str]) -> str:
    if label:
        return str(label)
    shadow_conf = conf.get("shadow", {}) or {}
    return str(shadow_conf.get("label") or "P11_FUNDB_ANCHOR")


def _resolve_shadow_start(conf: Dict[str, Any], start: Optional[str]) -> Optional[str]:
    if start:
        return start
    shadow_conf = conf.get("shadow", {}) or {}
    return shadow_conf.get("start_date")


def _run_replay_job(args: argparse.Namespace) -> ReplayJob:
    config_path = Path(args.config).expanduser().resolve()
    conf = _load_yaml(config_path)
    cfg = build_engine_cfg(conf)
    daily_runs_dir = _discover_daily_runs_dir(conf, args.daily_runs_dir)
    args.shadow_label = _resolve_shadow_label(conf, getattr(args, "shadow_label", None))
    args.start = _resolve_shadow_start(conf, getattr(args, "start", None))
    pairs = _discover_pairs(
        daily_runs_dir=daily_runs_dir,
        shadow_label=args.shadow_label,
        start=args.start,
        end=args.end,
        include_duplicate_scores=bool(args.include_duplicate_scores),
    )
    if not pairs:
        print(
            f"No artifact pairs found for label={args.shadow_label!r} in {daily_runs_dir}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    min_runs = int(getattr(args, "min_runs", 1) or 1)
    if len(pairs) < min_runs:
        print(
            f"Only {len(pairs)} paired runs found; min_runs={min_runs}.",
            file=sys.stderr,
        )
        raise SystemExit(3)

    initial_path = _resolve_initial_path(args.initial_from, pairs[0], daily_runs_dir)
    initial_meta = _read_json(pairs[0].baseline_dir / "run_meta.json")
    initial_capital = (
        float(args.initial_capital)
        if args.initial_capital is not None
        else _safe_float(
            initial_meta.get("total_capital"),
            float(conf.get("portfolio", {}).get("total_capital", 100000.0)),
        )
    )
    initial_cash = (
        float(args.initial_cash)
        if args.initial_cash is not None
        else (
            _safe_float(initial_meta.get("cash_balance"), np.nan)
            if "cash_balance" in initial_meta
            else None
        )
    )
    if isinstance(initial_cash, float) and not np.isfinite(initial_cash):
        initial_cash = None
    initial_portfolio = _seed_portfolio_from_current(
        initial_path,
        total_capital=initial_capital,
        override_cash=initial_cash,
    )

    baseline = _run_one_ledger(
        name=args.baseline_name,
        pairs=pairs,
        use_shadow_scores=False,
        initial_portfolio=initial_portfolio,
        cfg=cfg,
        conf=conf,
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
        daily_buy_limit_override=args.daily_buy_limit,
    )
    shadow = _run_one_ledger(
        name=args.shadow_name or _sanitize_label(args.shadow_label),
        pairs=pairs,
        use_shadow_scores=True,
        initial_portfolio=initial_portfolio,
        cfg=cfg,
        conf=conf,
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
        daily_buy_limit_override=args.daily_buy_limit,
    )

    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else Path(conf.get("paths", {}).get("output_dir", "output")).expanduser().resolve() / "shadow_ledgers"
    )
    run_label = f"replay_{pairs[0].run_date}_{pairs[-1].run_date}".replace("-", "")
    output_dir = output_root / _sanitize_label(args.shadow_label) / run_label
    summary = _write_outputs(output_dir, baseline, shadow, pairs, args, initial_path)
    return ReplayJob(summary=summary, output_dir=output_dir, pairs=list(pairs))


def replay(args: argparse.Namespace) -> int:
    job = _run_replay_job(args)

    print(f"Replay complete: {len(job.pairs)} paired runs")
    print(f"Output: {job.output_dir}")
    print(
        "Shadow vs baseline: "
        f"NAV delta ${job.summary['comparison']['shadow_minus_baseline_final_nav']:,.2f}, "
        f"return delta {job.summary['comparison']['shadow_minus_baseline_return_pp']:+.4f} pp"
    )
    return 0


def update_latest(args: argparse.Namespace) -> int:
    """Run a full replay through the latest paired artifact and publish pointers.

    This is the Phase B integration seam.  A daily runner or UI can invoke this
    command after daily/shadow artifacts exist, then read latest_pointer.json
    without needing to understand the replay internals.
    """
    job = _run_replay_job(args)
    label_dir = job.output_dir.parent
    label_dir.mkdir(parents=True, exist_ok=True)

    latest_pointer = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "shadow_label": job.summary["shadow_label"],
        "latest_date": job.summary["end"],
        "latest_run_id": job.summary["run_ids"][-1] if job.summary["run_ids"] else "",
        "latest_replay_dir": str(job.output_dir),
        "latest_summary_json": str(job.output_dir / "compare_summary.json"),
        "latest_summary_md": str(job.output_dir / "compare_summary.md"),
        "comparison": job.summary.get("comparison", {}),
        "baseline": job.summary.get("baseline", {}),
        "shadow": job.summary.get("shadow", {}),
    }
    pointer_path = label_dir / "latest_pointer.json"
    pointer_path.write_text(
        json.dumps(latest_pointer, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    latest_md = label_dir / "latest_compare_summary.md"
    src_md = job.output_dir / "compare_summary.md"
    if src_md.exists():
        shutil.copyfile(src_md, latest_md)

    if bool(getattr(args, "write_artifact_summary", False)):
        artifact_dir = job.pairs[-1].shadow_dir
        artifact_payload = {
            **latest_pointer,
            "note": "Generated by shadow_ledger.py update-latest.",
        }
        (artifact_dir / "shadow_ledger_summary.json").write_text(
            json.dumps(artifact_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if src_md.exists():
            shutil.copyfile(src_md, artifact_dir / "shadow_ledger_summary.md")

    print(f"Latest shadow ledger updated: {len(job.pairs)} paired runs")
    print(f"Replay output: {job.output_dir}")
    print(f"Latest pointer: {pointer_path}")
    print(
        "Shadow vs baseline: "
        f"NAV delta ${job.summary['comparison']['shadow_minus_baseline_final_nav']:,.2f}, "
        f"return delta {job.summary['comparison']['shadow_minus_baseline_return_pp']:+.4f} pp"
    )
    return 0


def _add_common_replay_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=str(PHASE3_DIR / "config.yaml"))
    p.add_argument("--daily-runs-dir", default=None)
    p.add_argument("--shadow-label", default=None)
    p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--initial-from", default=None, help="portfolio_before.csv path; defaults to first baseline artifact")
    p.add_argument("--initial-capital", type=float, default=None)
    p.add_argument("--initial-cash", type=float, default=None)
    p.add_argument("--daily-buy-limit", type=float, default=None)
    p.add_argument("--commission-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--baseline-name", default="baseline_daily")
    p.add_argument("--shadow-name", default=None)
    p.add_argument("--output-root", default=None)
    p.add_argument("--min-runs", type=int, default=1)
    p.add_argument(
        "--include-duplicate-scores",
        action="store_true",
        help="Do not skip repeated shadow score signatures, useful for debugging weekend reruns.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay baseline vs shadow score artifacts through independent virtual ledgers."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("replay", help="Run stateful baseline/shadow ledger replay")
    _add_common_replay_args(p)
    p.set_defaults(func=replay)

    p_latest = sub.add_parser(
        "update-latest",
        help="Phase B seam: replay through latest paired artifact and publish latest pointer files.",
    )
    _add_common_replay_args(p_latest)
    p_latest.add_argument(
        "--write-artifact-summary",
        action="store_true",
        help="Also write shadow_ledger_summary.json/md into the latest shadow artifact directory.",
    )
    p_latest.set_defaults(func=update_latest)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
