#!/usr/bin/env python3
"""
Phase 3 Daily Runner — execute once per trading day.

Usage:
    python daily_runner.py                    # normal run
    python daily_runner.py --dry-run          # check only, no state changes
    python daily_runner.py --force-rebalance  # ignore cooldown, force trigger
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from engine_loader import engine
from holdings_manager import HoldingsManager
from cache_health import run_full_health_check, load_config


# ─────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────

def build_engine_cfg(conf: dict) -> Any:
    """Build an engine Config from config.yaml values."""
    cfg = engine.Config()
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    cfg.enable_portfolio_construction = True
    cfg.enable_order_book = True
    cfg.order_book_total_capital = conf["portfolio"]["total_capital"]
    cfg.portfolio_top_n = conf["portfolio"]["top_n"]
    cfg.portfolio_max_weight_cap = conf["portfolio"]["max_weight_cap"]
    cfg.portfolio_hold_buffer_n = conf["portfolio"].get("hold_buffer_n", 30)
    cfg.portfolio_weight_mode = conf["portfolio"]["weight_mode"]
    cfg.portfolio_entry_score_margin = conf["portfolio"].get("entry_score_margin", 8.0)

    rg = conf.get("regime", {})
    cfg.enable_vix_fast_regime = True
    cfg.regime_adaptive_portfolio = True
    cfg.enable_circuit_breaker = True
    cfg.vix_bull_threshold = rg.get("vix_bull_threshold", 18.0)
    cfg.vix_defensive_threshold = rg.get("vix_defensive_threshold", 30.0)
    cfg.circuit_breaker_vix_threshold = rg.get("circuit_breaker_vix_threshold", 35.0)
    cfg.circuit_breaker_cash_pct = rg.get("circuit_breaker_cash_pct", 0.50)
    cfg.regime_bull_top_n = rg.get("regime_bull_top_n", 20)
    cfg.regime_bull_max_weight_cap = rg.get("regime_bull_max_weight_cap", 0.12)
    cfg.regime_side_top_n = rg.get("regime_side_top_n", 15)
    cfg.regime_side_max_weight_cap = rg.get("regime_side_max_weight_cap", 0.12)
    cfg.regime_side_cash_pct = rg.get("regime_side_cash_pct", 0.20)
    cfg.regime_defensive_top_n = rg.get("regime_defensive_top_n", 10)
    cfg.regime_defensive_max_weight_cap = rg.get("regime_defensive_max_weight_cap", 0.15)
    cfg.regime_defensive_cash_pct = rg.get("regime_defensive_cash_pct", 0.35)

    cfg.enable_event_driven_rebal = True
    trig = conf.get("triggers", {})
    cfg.event_min_interval_days = trig.get("min_interval_days", 7)
    cfg.event_max_interval_days = trig.get("max_interval_days", 14)
    cfg.event_vix_emergency_threshold = trig.get("vix_emergency", 30.0)
    cfg.event_vix_recovery_threshold = trig.get("vix_recovery", 25.0)
    cfg.event_drift_threshold = trig.get("drift_threshold", 0.15)
    cfg.event_score_change_threshold = trig.get("score_change_threshold", 0.20)

    return cfg


# ─────────────────────────────────────────────
# Frozen signal loader
# ─────────────────────────────────────────────

def load_frozen_signal(path: str) -> dict:
    fs = np.load(path, allow_pickle=True)
    result = {
        "mask": np.asarray(fs["mask"], dtype=bool),
        "wb": np.asarray(fs["wb"], dtype=np.float64),
        "ws": np.asarray(fs["ws"], dtype=np.float64),
        "wd": np.asarray(fs["wd"], dtype=np.float64),
    }
    if "signal_summary" in fs:
        result["signal_summary"] = json.loads(str(fs["signal_summary"]))
    return result


# ─────────────────────────────────────────────
# VIX & Regime
# ─────────────────────────────────────────────

def get_current_vix(cfg) -> Tuple[float, str]:
    """Get latest VIX close and current regime."""
    vix_sym = getattr(cfg, "vix_symbol", "^VIX")
    now = datetime.now()
    df = engine.load_ohlcv_from_cache(cfg, vix_sym, now - timedelta(days=60), now)
    if df.empty:
        return 20.0, "SIDE"

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    vix_close = float(df["close"].iloc[-1])

    if vix_close < cfg.vix_bull_threshold:
        regime = "BULL"
    elif vix_close >= cfg.vix_defensive_threshold:
        regime = "DEFENSIVE"
    else:
        regime = "SIDE"

    if vix_close >= cfg.circuit_breaker_vix_threshold:
        regime = "CRASH"

    return vix_close, regime


# ─────────────────────────────────────────────
# Trigger check (EVT_CONS logic)
# ─────────────────────────────────────────────

def check_triggers(
    cfg, vix_close: float, regime: str,
    holdings_mgr: HoldingsManager,
    pack: dict, signal: dict,
    force: bool = False,
) -> List[str]:
    """Check event-driven triggers. Returns list of trigger names."""
    triggers = []

    if force:
        triggers.append("FORCED")
        return triggers

    last_rebal = holdings_mgr.get_last_rebalance_date()
    now = datetime.now()
    days_since = (now - last_rebal).days if last_rebal else 999

    # Track emergency state from daily log
    log = holdings_mgr.load_daily_log()
    in_emergency = False
    if not log.empty and "TriggerType" in log.columns:
        last_types = str(log["TriggerType"].iloc[-1]) if len(log) > 0 else ""
        in_emergency = "VIX_EMERGENCY" in last_types and "VIX_RECOVERY" not in last_types

    # T1: VIX Emergency (bypasses cooldown)
    if vix_close >= cfg.event_vix_emergency_threshold and not in_emergency:
        triggers.append("VIX_EMERGENCY")
    elif in_emergency and vix_close < cfg.event_vix_recovery_threshold:
        triggers.append("VIX_RECOVERY")

    # Cooldown check (VIX emergency bypasses)
    if days_since < cfg.event_min_interval_days and not triggers:
        return []

    # T2: Max interval
    if days_since >= cfg.event_max_interval_days:
        triggers.append("TIME_MAX")

    # T3: Drift — price movement shifted portfolio weights
    current_weights = holdings_mgr.get_weight_vector()
    if current_weights:
        current = holdings_mgr.load_current()
        if not current.empty and "BuyPrice" in current.columns:
            buy_weights = {}
            total_cost = (current["BuyPrice"] * current["Shares"]).sum()
            if total_cost > 0:
                for _, r in current.iterrows():
                    buy_weights[r["Ticker"]] = r["BuyPrice"] * r["Shares"] / total_cost
            all_tickers = set(list(current_weights.keys()) + list(buy_weights.keys()))
            drift = sum(abs(current_weights.get(t, 0) - buy_weights.get(t, 0)) for t in all_tickers)
            if drift >= cfg.event_drift_threshold:
                triggers.append("DRIFT")

    # T4: Score change — check if top holder scores shifted
    # (Simplified: compare against threshold; full implementation uses pack)
    if days_since >= cfg.event_min_interval_days and not triggers:
        pass  # score change requires previous scores, checked during rebalance

    return triggers


# ─────────────────────────────────────────────
# Score computation for a single date
# ─────────────────────────────────────────────

def compute_today_scores(
    cfg, pack: dict, signal: dict, regime: str,
) -> pd.DataFrame:
    """Compute stock scores for the latest valid date in pack."""
    import dataclasses
    mask = signal["mask"]
    wb = signal["wb"]
    ws = signal["ws"]
    wd = signal["wd"]

    dates = pack["dates"]
    tickers = pack["tickers"]
    N = len(tickers)

    # Find the most recent date with sufficient feature coverage
    sel = np.asarray(mask, dtype=bool)
    feat_valid = pack["feat_valid"]
    di = len(dates) - 1
    min_valid_tickers = max(100, N // 3)
    for candidate_di in range(len(dates) - 1, -1, -1):
        valid_count = int(np.all(feat_valid[sel, candidate_di, :], axis=0).sum())
        if valid_count >= min_valid_tickers:
            di = candidate_di
            break
    print(f"  Using scoring date: {dates[di]} (di={di}, valid tickers at this date)")

    sel = np.asarray(mask, dtype=bool)
    print(f"  Signal mask: {sel.shape}, {int(sel.sum())} selected features out of {len(sel)}")

    # Disable the chronic completeness history filter for live scoring.
    # That filter requires 95% of a 252-day lookback to have all features valid,
    # which fails when the cache has gaps in historical data.
    # For live scoring we only need today's features to be valid.
    cfg_live = dataclasses.replace(cfg)
    cfg_live.enable_completeness_history_filter = False

    active_w_bull = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "BULL")
    active_w_side = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "SIDE")
    active_w_def = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "DEFENSIVE")

    score_regime = regime if regime != "CRASH" else "DEFENSIVE"
    regime_w = {"BULL": active_w_bull, "SIDE": active_w_side, "DEFENSIVE": active_w_def}.get(
        score_regime, active_w_side
    )
    w_sum = float(np.sum(regime_w * sel.astype(np.float64)))
    print(f"  Score regime: {score_regime} | Active weight sum (sel-masked): {w_sum:.6f}")

    scores = engine._score_vector_for_regime(
        pack=pack, di=di, sel=sel,
        active_w_bull=active_w_bull,
        active_w_side=active_w_side,
        active_w_def=active_w_def,
        score_regime=score_regime,
        cfg=cfg_live,
    )

    finite_mask = np.isfinite(scores)
    n_finite = int(finite_mask.sum())
    n_positive = int((scores[finite_mask] > 0).sum()) if n_finite > 0 else 0
    print(f"  Raw scores: {N} total, {n_finite} finite, {n_positive} positive")
    if n_finite > 0:
        print(f"  Score range: [{np.nanmin(scores):.6f}, {np.nanmax(scores):.6f}]")

    score100 = 100.0 * np.clip(scores, 0.0, 1.0)

    close_prices = {}
    close_key = "close" if "close" in pack else "raw_close"
    if close_key in pack:
        close_arr = pack[close_key]
        for ni in range(N):
            p = float(close_arr[di, ni])
            if np.isfinite(p) and p > 0:
                close_prices[tickers[ni]] = p

    rows = []
    for ni in range(N):
        s = float(score100[ni])
        if np.isfinite(s) and s > 0:
            rows.append({
                "Ticker": tickers[ni],
                "Score": round(s, 2),
                "Price": close_prices.get(tickers[ni], np.nan),
            })

    if not rows:
        print(f"  [WARN] No stocks scored > 0. Returning empty DataFrame.")
        return pd.DataFrame(columns=["Ticker", "Score", "Price"])

    df = pd.DataFrame(rows).sort_values("Score", ascending=False).reset_index(drop=True)
    return df


# ─────────────────────────────────────────────
# Adaptive daily buy limit
# ─────────────────────────────────────────────

def _compute_daily_limit(
    cash: float, holdings_value: float,
    strategy_conf: dict, fixed_limit: float,
) -> float:
    """Compute today's buy budget.

    'adaptive' mode scales with uninvested capital, saturating as portfolio fills:
        limit = max(uninvested * deploy_rate, min_limit)
    """
    mode = strategy_conf.get("buy_limit_mode", "fixed")
    if mode != "adaptive":
        return min(fixed_limit, max(cash, 0.0))

    total = holdings_value + max(cash, 0.0)
    target_invest = strategy_conf.get("target_invest_pct", 0.95)
    deploy_rate = strategy_conf.get("adaptive_deploy_rate", 0.05)
    min_limit = strategy_conf.get("adaptive_min_limit", 500.0)

    target_cash_reserve = total * (1.0 - target_invest)
    uninvested = max(cash - target_cash_reserve, 0.0)
    limit = max(uninvested * deploy_rate, min_limit)
    return min(limit, max(cash, 0.0))


# ─────────────────────────────────────────────
# Buy/Sell recommendation generation
# ─────────────────────────────────────────────

_RECO_COLS = [
    "Date", "Ticker", "Action", "Score", "TargetPct", "ActualPct",
    "GapPct", "Price", "Shares", "Capital", "Regime", "GraceCount",
]


def generate_recommendations(
    cfg, scores_df: pd.DataFrame, regime: str,
    vix_close: float, holdings_mgr: HoldingsManager,
    total_capital: float, daily_buy_limit: float = 0.0,
    strategy_conf: Optional[Dict] = None,
) -> pd.DataFrame:
    """Generate gap-based incremental buy/sell recommendations.

    Features:
      - Gap-proportional buy allocation (BUY_NEW / BUY_MORE)
      - STOP_LOSS: forced sell when PnL <= threshold, regardless of rank
      - Grace Period: held tickers dropping out of top_n get SELL_GRACE
        for N days before actual SELL
      - TRIM: partial sell for overweight positions
    """

    strat = strategy_conf or {}
    gap_threshold = strat.get("rebalance_gap_threshold", 0.02)
    enable_trim = strat.get("enable_trim", False)
    trim_threshold = strat.get("trim_threshold", 0.03)
    min_buy_shares = strat.get("min_buy_shares", 1)
    sell_grace_days = strat.get("sell_grace_days", 0)
    enable_stop_loss = strat.get("enable_stop_loss", False)
    stop_loss_pct = strat.get("stop_loss_pct", -15.0)

    # --- 1. Regime parameters (unchanged) ---
    if regime == "BULL":
        top_n = cfg.regime_bull_top_n
        max_cap = cfg.regime_bull_max_weight_cap
        cash_pct = getattr(cfg, "regime_bull_cash_pct", 0.0)
    elif regime == "DEFENSIVE" or regime == "CRASH":
        top_n = cfg.regime_defensive_top_n
        max_cap = cfg.regime_defensive_max_weight_cap
        cash_pct = cfg.regime_defensive_cash_pct
    else:
        top_n = cfg.regime_side_top_n
        max_cap = cfg.regime_side_max_weight_cap
        cash_pct = cfg.regime_side_cash_pct

    if vix_close >= cfg.circuit_breaker_vix_threshold:
        cash_pct = max(cash_pct, cfg.circuit_breaker_cash_pct)

    investable_capital = total_capital * (1.0 - cash_pct)

    # --- 2. Target weights (score-weighted, capped) ---
    candidates = scores_df.dropna(subset=["Price"]).head(top_n).copy()
    if candidates.empty:
        return pd.DataFrame(columns=_RECO_COLS)

    total_score = candidates["Score"].sum()
    if total_score > 0:
        candidates["Weight"] = (candidates["Score"] / total_score).clip(upper=max_cap)
        w_sum = candidates["Weight"].sum()
        if w_sum > 0:
            candidates["Weight"] = candidates["Weight"] / w_sum
    else:
        candidates["Weight"] = 1.0 / len(candidates)

    target_map = dict(zip(candidates["Ticker"], candidates["Weight"]))
    score_map = dict(zip(candidates["Ticker"], candidates["Score"]))
    price_map = dict(zip(candidates["Ticker"], candidates["Price"]))

    # --- 3. Actual weights + PnL from current holdings ---
    current = holdings_mgr.load_current()
    actual_value = {}
    pnl_map = {}
    held_price_map = {}
    held_shares_map = {}
    if not current.empty:
        for _, row in current.iterrows():
            t = row["Ticker"]
            mv = float(row.get("MarketValue", 0))
            if mv > 0:
                actual_value[t] = mv
            pnl_map[t] = float(row.get("PnL_Pct", 0))
            held_price_map[t] = float(row.get("CurrentPrice", row.get("BuyPrice", 0)))
            held_shares_map[t] = int(row.get("Shares", 0))

    actual_map = {}
    if investable_capital > 0:
        for t, v in actual_value.items():
            actual_map[t] = v / investable_capital

    # --- 3b. Load previous recommendations for grace period tracking ---
    prev_grace = {}  # ticker → grace count from previous run
    if sell_grace_days > 0:
        prev_recos = holdings_mgr.load_recommendations()
        if not prev_recos.empty and "Action" in prev_recos.columns:
            grace_rows = prev_recos[prev_recos["Action"] == "SELL_GRACE"]
            if not grace_rows.empty and "GraceCount" in grace_rows.columns:
                for _, gr in grace_rows.iterrows():
                    prev_grace[gr["Ticker"]] = int(gr.get("GraceCount", 1))

    # --- 4. STOP_LOSS check (highest priority — before gap classification) ---
    today = datetime.now().strftime("%Y-%m-%d")
    target_tickers = set(target_map.keys())
    held_tickers = set(actual_value.keys())

    recos = []
    stop_loss_tickers = set()

    if enable_stop_loss and not current.empty:
        for ticker in held_tickers:
            pnl = pnl_map.get(ticker, 0)
            if pnl <= stop_loss_pct:
                stop_loss_tickers.add(ticker)
                recos.append({
                    "Date": today, "Ticker": ticker, "Action": "STOP_LOSS",
                    "Score": score_map.get(ticker, 0),
                    "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
                    "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
                    "GapPct": 0,
                    "Price": held_price_map.get(ticker, 0),
                    "Shares": held_shares_map.get(ticker, 0),
                    "Capital": 0, "Regime": regime, "GraceCount": 0,
                })

    # --- 5. Classify remaining tickers ---
    all_tickers = target_tickers | held_tickers
    buy_items = []
    sell_items = []
    grace_items = []
    trim_items = []
    hold_items = []

    for ticker in all_tickers:
        if ticker in stop_loss_tickers:
            continue

        target_w = target_map.get(ticker, 0.0)
        actual_w = actual_map.get(ticker, 0.0)
        gap_w = target_w - actual_w
        gap_dollar = gap_w * investable_capital

        in_target = ticker in target_tickers
        is_held = ticker in held_tickers

        if not in_target and is_held:
            if sell_grace_days > 0:
                prev_count = prev_grace.get(ticker, 0)
                new_count = prev_count + 1
                if new_count > sell_grace_days:
                    sell_items.append(ticker)
                else:
                    grace_items.append((ticker, new_count))
            else:
                sell_items.append(ticker)
        elif in_target and gap_w > gap_threshold:
            buy_items.append((ticker, gap_dollar))
        elif in_target and enable_trim and gap_w < -trim_threshold:
            trim_items.append((ticker, gap_dollar))
        else:
            if in_target:
                hold_items.append(ticker)

    # --- 6. SELL recommendations ---
    for ticker in sell_items:
        c_row = current[current["Ticker"] == ticker].iloc[0]
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "SELL",
            "Score": score_map.get(ticker, 0),
            "TargetPct": 0, "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
            "GapPct": round(-actual_map.get(ticker, 0) * 100, 2),
            "Price": float(c_row.get("CurrentPrice", c_row.get("BuyPrice", 0))),
            "Shares": int(c_row["Shares"]), "Capital": 0, "Regime": regime,
            "GraceCount": 0,
        })

    # --- 6b. SELL_GRACE recommendations (holding with warning) ---
    for ticker, grace_count in grace_items:
        remaining = sell_grace_days - grace_count
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "SELL_GRACE",
            "Score": score_map.get(ticker, 0),
            "TargetPct": 0, "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
            "GapPct": round(-actual_map.get(ticker, 0) * 100, 2),
            "Price": held_price_map.get(ticker, 0),
            "Shares": held_shares_map.get(ticker, 0),
            "Capital": 0, "Regime": regime,
            "GraceCount": grace_count,
        })

    # --- 7. TRIM recommendations ---
    for ticker, gap_dollar in trim_items:
        target_w = target_map[ticker]
        actual_w = actual_map[ticker]
        excess_dollar = abs(gap_dollar)
        price = price_map[ticker]
        trim_shares = int(np.floor(excess_dollar / price))
        if trim_shares < 1:
            hold_items.append(ticker)
            continue
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "TRIM",
            "Score": score_map.get(ticker, 0),
            "TargetPct": round(target_w * 100, 2),
            "ActualPct": round(actual_w * 100, 2),
            "GapPct": round((target_w - actual_w) * 100, 2),
            "Price": price, "Shares": trim_shares,
            "Capital": round(trim_shares * price, 2), "Regime": regime,
            "GraceCount": 0,
        })

    # --- 7. Gap-proportional BUY allocation ---
    buy_items.sort(key=lambda x: x[1], reverse=True)
    total_gap_dollar = sum(g for _, g in buy_items)

    budget = daily_buy_limit if daily_buy_limit > 0 else float("inf")

    allocated = []
    if total_gap_dollar > 0 and buy_items:
        for ticker, gap_d in buy_items:
            share_of_budget = (gap_d / total_gap_dollar) * min(budget, daily_buy_limit) \
                if daily_buy_limit > 0 else gap_d
            allocated.append((ticker, gap_d, share_of_budget))

        # Round 1: allocate proportionally
        remaining_budget = budget
        buy_recos = []
        leftover_tickers = []

        for ticker, gap_d, alloc in allocated:
            price = price_map[ticker]
            max_affordable = int(np.floor(alloc / price))
            if max_affordable < min_buy_shares:
                leftover_tickers.append((ticker, gap_d))
                continue
            cost = max_affordable * price
            if cost > remaining_budget:
                max_affordable = int(np.floor(remaining_budget / price))
                if max_affordable < min_buy_shares:
                    leftover_tickers.append((ticker, gap_d))
                    continue
                cost = max_affordable * price
            remaining_budget -= cost
            is_new = ticker not in held_tickers
            buy_recos.append((ticker, max_affordable, cost, is_new))

        # Round 2: sweep remaining budget to largest-gap tickers
        for ticker, gap_d in leftover_tickers + [(t, g) for t, g in buy_items
                                                  if t not in [b[0] for b in buy_recos]]:
            if remaining_budget < 1.0:
                break
            price = price_map.get(ticker, 0)
            if price <= 0:
                continue
            shares_fit = int(np.floor(remaining_budget / price))
            if shares_fit < min_buy_shares:
                continue
            cost = shares_fit * price
            remaining_budget -= cost
            is_new = ticker not in held_tickers
            existing = [b for b in buy_recos if b[0] == ticker]
            if existing:
                idx = buy_recos.index(existing[0])
                old_shares, old_cost, old_new = existing[0][1], existing[0][2], existing[0][3]
                buy_recos[idx] = (ticker, old_shares + shares_fit, old_cost + cost, old_new)
            else:
                buy_recos.append((ticker, shares_fit, cost, is_new))

        for ticker, shares, cost, is_new in buy_recos:
            action = "BUY_NEW" if is_new else "BUY_MORE"
            target_w = target_map.get(ticker, 0)
            actual_w = actual_map.get(ticker, 0)
            recos.append({
                "Date": today, "Ticker": ticker, "Action": action,
                "Score": score_map.get(ticker, 0),
                "TargetPct": round(target_w * 100, 2),
                "ActualPct": round(actual_w * 100, 2),
                "GapPct": round((target_w - actual_w) * 100, 2),
                "Price": price_map[ticker], "Shares": shares,
                "Capital": round(cost, 2), "Regime": regime,
                "GraceCount": 0,
            })

        # Tickers with buy gap but couldn't allocate anything → DEFERRED
        bought_tickers = {b[0] for b in buy_recos}
        for ticker, gap_d in buy_items:
            if ticker not in bought_tickers:
                target_w = target_map.get(ticker, 0)
                actual_w = actual_map.get(ticker, 0)
                recos.append({
                    "Date": today, "Ticker": ticker, "Action": "DEFERRED",
                    "Score": score_map.get(ticker, 0),
                    "TargetPct": round(target_w * 100, 2),
                    "ActualPct": round(actual_w * 100, 2),
                    "GapPct": round((target_w - actual_w) * 100, 2),
                    "Price": price_map[ticker], "Shares": 0,
                    "Capital": 0, "Regime": regime, "GraceCount": 0,
                })

    # --- 9. HOLD ---
    for ticker in hold_items:
        target_w = target_map.get(ticker, 0)
        actual_w = actual_map.get(ticker, 0)
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "HOLD",
            "Score": score_map.get(ticker, 0),
            "TargetPct": round(target_w * 100, 2),
            "ActualPct": round(actual_w * 100, 2),
            "GapPct": round((target_w - actual_w) * 100, 2),
            "Price": price_map.get(ticker, 0), "Shares": 0,
            "Capital": 0, "Regime": regime, "GraceCount": 0,
        })

    df = pd.DataFrame(recos)
    if df.empty:
        return pd.DataFrame(columns=_RECO_COLS)
    return df


# ─────────────────────────────────────────────
# Cache update
# ─────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")
_DATA_READY_HOUR_ET = 18  # 6PM ET — 2h buffer after 4PM close


def _us_eastern_now() -> datetime:
    """Current time in US Eastern (handles EDT/EST automatically)."""
    return datetime.now(_ET)


def _last_available_trading_date() -> datetime:
    """Return the most recent US trading date whose *finalized daily OHLCV*
    should be available from the data provider.

    Rules:
      - Uses US Eastern time so that the caller's local timezone is irrelevant.
      - Before 6 PM ET: today's candle isn't finalized → expect previous trading day.
      - After  6 PM ET: today's candle should be ready (if today was a trading day).
      - Weekends are skipped backwards to Friday.
    """
    et_now = _us_eastern_now()
    if et_now.hour < _DATA_READY_HOUR_ET:
        d = et_now.date() - timedelta(days=1)
    else:
        d = et_now.date()
    while d.weekday() >= 5:  # Sat=5, Sun=6 → back to Friday
        d -= timedelta(days=1)
    return d


def update_cache(cfg, tickers: List[str], max_stale_trading_days: int = 0,
                 refresh_days: int = 7):
    """Detect stale tickers and re-download recent OHLCV data.

    Args:
        max_stale_trading_days: 0 means the latest cached date must be >=
            the last available trading date (no gap allowed).
    """
    now = datetime.now()
    et_now = _us_eastern_now()
    vix_sym = getattr(cfg, "vix_symbol", "^VIX")
    refresh_start = now - timedelta(days=max(refresh_days, 30))
    expected_date = _last_available_trading_date()
    cutoff = expected_date - timedelta(days=max_stale_trading_days)

    print(f"  US Eastern now : {et_now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  Expected latest: {expected_date}  (cutoff: {cutoff})")

    print(f"  Refreshing VIX cache...")
    try:
        engine.download_ohlcv_to_cache_chunked(
            cfg, vix_sym, refresh_start, now, overwrite=True,
        )
    except Exception as e:
        print(f"  [WARN] VIX refresh failed: {e}")

    print(f"  Scanning {len(tickers)} tickers (expected latest >= {cutoff})...")
    stale = []
    for t in tickers:
        df = engine.load_ohlcv_from_cache(cfg, t, now - timedelta(days=14), now)
        if df.empty:
            stale.append(t)
        else:
            latest = pd.to_datetime(df["date"]).max().date()
            if latest < cutoff:
                stale.append(t)

    if not stale:
        print(f"  All {len(tickers)} tickers up to date (latest >= {cutoff}).")
        return

    print(f"  Found {len(stale)} stale tickers. Downloading last {refresh_days} days...")
    ok, fail = 0, 0
    for i, t in enumerate(stale):
        try:
            engine.download_ohlcv_to_cache_chunked(
                cfg, t, now - timedelta(days=refresh_days), now, overwrite=True,
            )
            ok += 1
        except Exception as e:
            fail += 1
        if (i + 1) % 50 == 0 or i == len(stale) - 1:
            print(f"  Progress: {i + 1}/{len(stale)} (ok={ok} fail={fail})")

    print(f"  Cache refresh done: {ok} updated, {fail} failed out of {len(stale)} stale")


def force_overwrite_recent_cache(cfg, tickers: List[str], days: int = 7):
    """Force re-download the last N days of OHLCV for ALL tickers unconditionally.
    Use this to fix potentially corrupted cache from partial (intraday) data."""
    now = datetime.now()
    et_now = _us_eastern_now()
    start = now - timedelta(days=days)
    vix_sym = getattr(cfg, "vix_symbol", "^VIX")
    expected = _last_available_trading_date()

    print(f"  US Eastern now : {et_now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  Expected latest: {expected}")
    print(f"  Force overwrite: last {days} days for {len(tickers)} tickers + VIX\n")

    print(f"  [1/2] VIX...")
    try:
        engine.download_ohlcv_to_cache_chunked(
            cfg, vix_sym, start, now, overwrite=True)
        print(f"  VIX OK")
    except Exception as e:
        print(f"  [WARN] VIX failed: {e}")

    print(f"  [2/2] Tickers...")
    ok, fail = 0, 0
    for i, t in enumerate(tickers):
        try:
            engine.download_ohlcv_to_cache_chunked(
                cfg, t, start, now, overwrite=True)
            ok += 1
        except Exception:
            fail += 1
        if (i + 1) % 50 == 0 or i == len(tickers) - 1:
            pct = (i + 1) / len(tickers) * 100
            print(f"\r  Progress: {i + 1}/{len(tickers)} ({pct:.0f}%) ok={ok} fail={fail}", end="")
    print()
    print(f"\n  Force overwrite done: {ok} updated, {fail} failed out of {len(tickers)}")


def backfill_cache(cfg, tickers: List[str], scan_days: int = 180, max_gap_days: int = 5):
    """Scan cache for interior gaps and backfill missing data for all tickers.

    Args:
        cfg: Engine Config
        tickers: List of ticker symbols to scan
        scan_days: How far back to scan (default 180 = 6 months)
        max_gap_days: Calendar days gap threshold to trigger backfill
    """
    now = datetime.now()
    scan_start = now - timedelta(days=scan_days)

    print(f"  VIX backfill ({scan_days}d)...")
    vix_sym = getattr(cfg, "vix_symbol", "^VIX")
    try:
        engine.download_ohlcv_to_cache_chunked(
            cfg, vix_sym, scan_start, now, overwrite=True,
        )
        print(f"  VIX OK")
    except Exception as e:
        print(f"  [WARN] VIX failed: {e}")

    print(f"  Scanning {len(tickers)} tickers for gaps (>{max_gap_days}d) over last {scan_days} days...")
    needs_backfill = []
    for t in tickers:
        df = engine.load_ohlcv_from_cache(cfg, t, scan_start, now)
        if df.empty:
            needs_backfill.append((t, "EMPTY"))
            continue
        dates = pd.to_datetime(df["date"]).sort_values().reset_index(drop=True)
        if (now - dates.max()).days > max_gap_days:
            needs_backfill.append((t, f"STALE(last={dates.max().date()})"))
            continue
        if (dates.min() - scan_start).days > max_gap_days + 5:
            needs_backfill.append((t, f"LATE_START({dates.min().date()})"))
            continue
        diffs = dates.diff().dt.days
        big_gaps = diffs[diffs > max_gap_days]
        if not big_gaps.empty:
            worst = int(diffs.max())
            needs_backfill.append((t, f"GAP({worst}d)"))

    print(f"  {len(needs_backfill)}/{len(tickers)} tickers need backfill")
    if not needs_backfill:
        print(f"  All tickers healthy.")
        return

    sample = needs_backfill[:10]
    for t, reason in sample:
        print(f"    {t}: {reason}")
    if len(needs_backfill) > 10:
        print(f"    ... and {len(needs_backfill) - 10} more")

    print(f"\n  Downloading {scan_days} days of data for {len(needs_backfill)} tickers...")
    ok, fail = 0, 0
    for i, (t, reason) in enumerate(needs_backfill):
        try:
            engine.download_ohlcv_to_cache_chunked(
                cfg, t, scan_start, now, overwrite=True,
            )
            ok += 1
        except Exception:
            fail += 1
        if (i + 1) % 25 == 0 or i == len(needs_backfill) - 1:
            pct = (i + 1) / len(needs_backfill) * 100
            print(f"\r  Progress: {i + 1}/{len(needs_backfill)} ({pct:.0f}%) ok={ok} fail={fail}", end="")
    print()
    print(f"  Backfill complete: {ok} updated, {fail} failed out of {len(needs_backfill)}")


# ─────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────

def run_daily(dry_run: bool = False, force: bool = False, config_path: str = None):
    """Main daily execution flow."""
    conf = load_config(config_path)
    cfg = build_engine_cfg(conf)
    now = datetime.now()
    et_now = _us_eastern_now()
    print(f"\n{'='*60}")
    print(f"  Phase 3 Daily Runner — {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"  US Eastern         — {et_now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*60}")

    # 1. Load frozen signal
    fs_path = conf["paths"]["frozen_signal"]
    if not os.path.exists(fs_path):
        print(f"  [ERROR] Frozen signal not found: {fs_path}")
        return
    signal = load_frozen_signal(fs_path)
    sig_summary = signal.get("signal_summary", {})
    print(f"  Signal: MeanIC={sig_summary.get('Invest_MeanIC', '?')}, "
          f"Spread={sig_summary.get('Invest_Spread', '?')}")

    # 1.5 Duplicate run guard (skip for dry_run)
    if not dry_run:
        hm_check = HoldingsManager(conf["paths"]["holdings_log"])
        today_str = now.strftime("%Y-%m-%d")
        daily_log = hm_check.load_daily_log()
        if not daily_log.empty and today_str in daily_log["Date"].astype(str).values:
            print(f"\n  [WARN] Daily run already executed today ({today_str}).")
            if not force:
                print(f"  Use force=True (or T7 confirmation) to override. Aborting.")
                return
            print(f"  force=True — proceeding with re-run.")

    # 2. Cache health check
    print("\n[Step 1] Cache health check...")
    health = run_full_health_check(config_path)
    print(f"  Overall: {health['overall_status']} | VIX: {health['vix'].get('latest_close', '?')} ({health['vix']['status']})")

    # 3. Update stale cache (always scan full universe)
    print("\n[Step 2] Cache refresh...")
    tickers, _ = engine.load_sp500_tickers_ttl(cfg, ttl_days=30)
    update_cache(cfg, tickers, max_stale_trading_days=0, refresh_days=30)

    # 4. Get current VIX and regime
    print("\n[Step 3] VIX & Regime check...")
    vix_close, regime = get_current_vix(cfg)
    print(f"  VIX={vix_close:.2f}  Regime={regime}")

    # 5. Check triggers
    print("\n[Step 4] Trigger check...")
    hm = HoldingsManager(conf["paths"]["holdings_log"])
    triggers = check_triggers(cfg, vix_close, regime, hm, pack=None, signal=signal, force=force)
    trigger_str = ", ".join(triggers) if triggers else "NONE"
    print(f"  Triggers: {trigger_str}")

    # 6. Always compute scores & generate recommendations
    #    (trigger only controls whether recommendations are *applied*)
    recos = pd.DataFrame()
    trigger_actionable = bool(triggers)
    daily_limit = 0.0

    print(f"\n[Step 5] Computing scores (regime={regime})...")
    import dataclasses
    cfg_for_pack = dataclasses.replace(cfg)
    cfg_for_pack.start_panel_date = now - timedelta(days=365)
    cfg_for_pack.end_date = now
    cfg_for_pack.enable_historical_universe = True
    cfg_for_pack.historical_universe_expand_tickers = True
    cfg_for_pack.enable_coverage_based_universe = True
    try:
        result = engine.prepare_inputs(cfg_for_pack)
        pack = result["pack"]
        scores_df = compute_today_scores(cfg, pack, signal, regime)
        print(f"  Scored {len(scores_df)} stocks. Top 5:")
        print(scores_df.head().to_string(index=False))

        price_map = dict(zip(scores_df["Ticker"], scores_df["Price"]))
        current_before = hm.load_current()
        if not current_before.empty:
            hm.update_current_prices(price_map)
            updated = hm.load_current()
            print(f"\n  [Price Update] {len(updated)} holdings refreshed:")
            for _, h in updated.iterrows():
                pnl_pct = h.get("PnL_Pct", 0)
                print(f"    {h['Ticker']:6s}  buy=${h['BuyPrice']:.2f}  "
                      f"now=${h['CurrentPrice']:.2f}  PnL={pnl_pct:+.2f}%")

        print(f"\n[Step 6] Generating recommendations...")
        if not trigger_actionable:
            print(f"  (Trigger=NONE → recommendations are PREVIEW only, not applied)")

        initial_cash = conf["portfolio"].get("initial_cash", conf["portfolio"]["total_capital"])
        hm.initialize_cash(initial_cash)

        holdings_value = hm.get_portfolio_value()
        cash_balance = hm.get_cash_balance()
        strat_base = conf.get("strategy", {})

        from simulator import resolve_strategy
        strat_conf = resolve_strategy(strat_base, regime)
        regime_tag = ""
        if strat_base.get("regime_overrides"):
            rg_key = {"BULL": "BULL", "SIDE": "SIDE"}.get(
                regime, "DEF" if regime in ("DEFENSIVE", "CRASH") else "SIDE")
            rg_ov = strat_base["regime_overrides"].get(rg_key, {})
            if rg_ov:
                changed = {k: strat_conf[k] for k in rg_ov}
                regime_tag = f" | regime→{regime}: {changed}"

        fixed_limit = conf["portfolio"].get("daily_buy_limit", 1000.0)
        daily_limit = _compute_daily_limit(
            cash_balance, holdings_value, strat_conf, fixed_limit)
        print(f"  Cash balance: ${cash_balance:,.2f} | Today's buy budget: ${daily_limit:,.2f}"
              f"  (mode={strat_conf.get('buy_limit_mode', 'fixed')}{regime_tag})")

        total_capital = holdings_value + max(cash_balance, 0.0)
        if total_capital < 1.0:
            total_capital = conf["portfolio"]["total_capital"]
        print(f"  Total capital: ${total_capital:,.2f} (holdings ${holdings_value:,.2f} + cash ${max(cash_balance,0):,.2f})")
        recos = generate_recommendations(
            cfg, scores_df, regime, vix_close, hm, total_capital,
            daily_buy_limit=daily_limit,
            strategy_conf=strat_conf,
        )

        _ne = lambda col: recos[recos["Action"] == col] if not recos.empty and "Action" in recos.columns else pd.DataFrame()
        stop_losses = _ne("STOP_LOSS")
        sells = _ne("SELL")
        sell_grace = _ne("SELL_GRACE")
        trims = _ne("TRIM")
        buy_new = _ne("BUY_NEW")
        buy_more = _ne("BUY_MORE")
        all_buys = pd.concat([buy_new, buy_more]) if not (buy_new.empty and buy_more.empty) else pd.DataFrame()
        holds = _ne("HOLD")
        deferred = _ne("DEFERRED")

        buy_total = all_buys["Capital"].sum() if not all_buys.empty else 0
        parts = []
        if len(stop_losses): parts.append(f"STOP_LOSS={len(stop_losses)}")
        if len(sells): parts.append(f"SELL={len(sells)}")
        if len(sell_grace): parts.append(f"SELL_GRACE={len(sell_grace)}")
        if len(trims): parts.append(f"TRIM={len(trims)}")
        parts.append(f"BUY_NEW={len(buy_new)} BUY_MORE={len(buy_more)} (${buy_total:,.0f})")
        parts.append(f"HOLD={len(holds)} DEFERRED={len(deferred)}")
        print(f"  {' | '.join(parts)}")
        if daily_limit > 0:
            print(f"  Daily buy limit: ${daily_limit:,.0f} | Remaining: ${daily_limit - buy_total:,.0f}")

        if not stop_losses.empty:
            print("\n  [STOP_LOSS — PnL breached threshold]")
            cur = hm.load_current()
            for _, r in stop_losses.iterrows():
                pnl_val = 0.0
                if not cur.empty:
                    mask = cur["Ticker"] == r["Ticker"]
                    if mask.any():
                        pnl_val = float(cur.loc[mask, "PnL_Pct"].iloc[0])
                print(f"    {r['Ticker']:6s}  {int(r['Shares']):3d} shares @ ${r['Price']:.2f}  PnL={pnl_val:+.1f}%")
        if not sells.empty:
            print("\n  [SELL — dropped from top_n]")
            print(sells[["Ticker", "ActualPct", "Price", "Shares"]].to_string(index=False))
        if not sell_grace.empty:
            print(f"\n  [SELL_GRACE — holding {conf.get('strategy',{}).get('sell_grace_days',60)}d grace period]")
            for _, r in sell_grace.iterrows():
                remaining = conf.get("strategy", {}).get("sell_grace_days", 60) - int(r.get("GraceCount", 0))
                print(f"    {r['Ticker']:6s}  grace {int(r['GraceCount'])}/{conf.get('strategy',{}).get('sell_grace_days',60)}  "
                      f"({remaining}d left)  weight={r['ActualPct']:.1f}%")
        if not trims.empty:
            print("\n  [TRIM — overweight reduction]")
            print(trims[["Ticker", "TargetPct", "ActualPct", "GapPct", "Price", "Shares"]].to_string(index=False))
        if not all_buys.empty:
            print("\n  [BUY — gap-proportional allocation]")
            cols = ["Ticker", "Action", "Score", "TargetPct", "ActualPct", "GapPct", "Price", "Shares", "Capital"]
            print(all_buys[cols].to_string(index=False))
        if not holds.empty:
            print("\n  [HOLD — within target]")
            print(holds[["Ticker", "Score", "TargetPct", "ActualPct"]].to_string(index=False))
        if not deferred.empty:
            print("\n  [DEFERRED — budget exhausted]")
            print(deferred[["Ticker", "Score", "TargetPct", "GapPct", "Price"]].to_string(index=False))

    except Exception as e:
            print(f"  [ERROR] Score computation failed: {e}")
            traceback.print_exc()

    # 7. Save recommendations — applied only when trigger fires
    if dry_run:
        print(f"\n[DRY RUN] No state changes applied.")
    else:
        if not recos.empty:
            hm.save_recommendations(recos)
            if trigger_actionable:
                print(f"\n[Step 7] Recommendations saved (ACTIONABLE)")
                print(f"  -> Open T10 'Report Execution' to apply after you trade.")
            else:
                print(f"\n[Step 7] Recommendations saved (PREVIEW — no trigger)")
                print(f"  -> Next trigger in ~{max(0, conf.get('triggers',{}).get('min_interval_days',7) - ((now - (hm.get_last_rebalance_date() or now)).days))}d")

        portfolio_value = hm.get_portfolio_value()
        cash_pct = 0.0
        top_holding = ""
        current = hm.load_current()
        if not current.empty:
            top_holding = current.sort_values("MarketValue", ascending=False).iloc[0]["Ticker"]

        hm.log_daily(
            trigger_fired=bool(triggers),
            trigger_type=trigger_str,
            vix=vix_close, regime=regime,
            cash_pct=cash_pct,
            portfolio_value=portfolio_value,
            top_holding=top_holding,
        )
        print(f"  Daily log recorded. Portfolio value: ${portfolio_value:,.2f}")

    # 8. Send email
    if not dry_run and conf.get("email", {}).get("enabled", False):
        try:
            from mailer import send_daily_email
            send_daily_email(conf, triggers, recos, vix_close, regime, hm, health,
                             computed_daily_limit=daily_limit)
            print("  Email sent.")
        except Exception as e:
            print(f"  [WARN] Email failed: {e}")

    # Summary
    print(f"\n{'='*60}")
    pnl = hm.get_pnl_summary()
    print(f"  Portfolio: ${pnl['total_value']:,.2f} (PnL: {pnl['pnl_pct']:+.2f}%)")
    print(f"  Holdings: {pnl['holdings_count']} stocks")
    print(f"  Regime: {regime} | VIX: {vix_close:.2f} | Triggers: {trigger_str}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 Daily Runner")
    parser.add_argument("--dry-run", action="store_true", help="Check only, no state changes")
    parser.add_argument("--force-rebalance", action="store_true", help="Force trigger regardless of cooldown")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()
    run_daily(dry_run=args.dry_run, force=args.force_rebalance, config_path=args.config)
