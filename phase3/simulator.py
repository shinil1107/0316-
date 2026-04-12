"""
Phase 3 Backtest Simulator — daily trading simulation with Phase 3 rules.

Simulates realistic daily trading from start_date using:
  - Phase 1/2 frozen signal for scoring
  - Phase 3 strategy: STOP_LOSS, SELL_GRACE, TRIM, gap-proportional BUY
  - Daily buy limit, cash management, commission/slippage
"""

import sys, os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Tuple
import dataclasses

sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────
# In-memory portfolio tracker
# ─────────────────────────────────────────────

class SimPortfolio:
    """In-memory portfolio for fast backtesting.

    Implements the subset of HoldingsManager interface that
    generate_recommendations() requires: load_current(), load_recommendations(),
    save_recommendations().
    """

    def __init__(self, initial_cash: float):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.holdings: Dict[str, dict] = {}
        self._last_recos = pd.DataFrame()
        self.trade_log: List[dict] = []
        self.total_commission = 0.0

    def load_current(self) -> pd.DataFrame:
        if not self.holdings:
            return pd.DataFrame(columns=[
                "Ticker", "Shares", "BuyPrice", "CurrentPrice",
                "MarketValue", "PnL_Pct", "Weight",
            ])
        rows = []
        for t, h in self.holdings.items():
            cp = h.get("current_price", h["avg_cost"])
            mv = h["shares"] * cp
            pnl = ((cp / h["avg_cost"]) - 1) * 100 if h["avg_cost"] > 0 else 0
            rows.append({
                "Ticker": t, "Shares": h["shares"], "BuyPrice": h["avg_cost"],
                "CurrentPrice": cp, "MarketValue": mv, "PnL_Pct": pnl, "Weight": 0.0,
            })
        return pd.DataFrame(rows)

    def load_recommendations(self) -> pd.DataFrame:
        return self._last_recos

    def save_recommendations(self, recos: pd.DataFrame):
        self._last_recos = recos.copy()

    def update_prices(self, price_map: dict):
        for t in list(self.holdings.keys()):
            if t in price_map and price_map[t] > 0:
                self.holdings[t]["current_price"] = price_map[t]

    def get_value(self, price_map: dict = None) -> float:
        total = 0.0
        for t, h in self.holdings.items():
            p = h.get("current_price", h["avg_cost"])
            if price_map and t in price_map:
                p = price_map[t]
            total += h["shares"] * p
        return total

    def get_portfolio_value(self) -> float:
        return self.get_value()

    def get_cash_balance(self) -> float:
        return self.cash

    def apply_actions(self, recos: pd.DataFrame, price_map: dict,
                      date: str, commission_bps: float = 10.0,
                      slippage_bps: float = 5.0):
        """Apply recommendation actions to portfolio with transaction costs."""
        if recos.empty:
            return

        cost_rate = (commission_bps + slippage_bps) / 10000.0

        for _, r in recos.iterrows():
            action = r["Action"]
            ticker = r["Ticker"]
            price = price_map.get(ticker, r.get("Price", 0))
            if price <= 0:
                continue

            if action in ("STOP_LOSS", "SELL"):
                if ticker in self.holdings:
                    h = self.holdings[ticker]
                    gross = h["shares"] * price
                    cost = gross * cost_rate
                    self.total_commission += cost
                    proceeds = gross - cost
                    self.trade_log.append({
                        "Date": date, "Action": action, "Ticker": ticker,
                        "Shares": h["shares"], "Price": price,
                        "Value": proceeds, "Commission": cost,
                    })
                    self.cash += proceeds
                    del self.holdings[ticker]

            elif action == "TRIM":
                shares = int(r.get("Shares", 0))
                if ticker in self.holdings and shares > 0:
                    h = self.holdings[ticker]
                    actual_trim = min(shares, h["shares"] - 1)
                    if actual_trim > 0:
                        gross = actual_trim * price
                        cost = gross * cost_rate
                        self.total_commission += cost
                        proceeds = gross - cost
                        self.trade_log.append({
                            "Date": date, "Action": "TRIM", "Ticker": ticker,
                            "Shares": actual_trim, "Price": price,
                            "Value": proceeds, "Commission": cost,
                        })
                        self.cash += proceeds
                        h["shares"] -= actual_trim

            elif action in ("BUY_NEW", "BUY_MORE"):
                shares = int(r.get("Shares", 0))
                if shares <= 0:
                    continue
                gross = shares * price
                cost_fee = gross * cost_rate
                total_cost = gross + cost_fee
                if total_cost > self.cash:
                    shares = int(np.floor(self.cash / (price * (1 + cost_rate))))
                    if shares <= 0:
                        continue
                    gross = shares * price
                    cost_fee = gross * cost_rate
                    total_cost = gross + cost_fee

                self.total_commission += cost_fee
                self.trade_log.append({
                    "Date": date, "Action": action, "Ticker": ticker,
                    "Shares": shares, "Price": price,
                    "Value": -total_cost, "Commission": cost_fee,
                })
                self.cash -= total_cost

                if ticker in self.holdings:
                    old = self.holdings[ticker]
                    new_shares = old["shares"] + shares
                    new_avg = (old["shares"] * old["avg_cost"] + gross) / new_shares
                    self.holdings[ticker] = {
                        "shares": new_shares, "avg_cost": new_avg,
                        "current_price": price,
                    }
                else:
                    self.holdings[ticker] = {
                        "shares": shares, "avg_cost": price,
                        "current_price": price,
                    }


# ─────────────────────────────────────────────
# Adaptive daily buy limit
# ─────────────────────────────────────────────

def _compute_daily_limit(
    cash: float, holdings_value: float,
    strategy_conf: dict, fixed_limit: float,
) -> float:
    """Compute today's buy budget.

    In 'adaptive' mode the limit scales with how much capital is still
    uninvested, naturally saturating as the portfolio fills up:
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
# Regime-aware strategy resolution
# ─────────────────────────────────────────────

_REGIME_KEY_MAP = {"BULL": "BULL", "SIDE": "SIDE",
                   "DEFENSIVE": "DEF", "CRASH": "DEF"}


def resolve_strategy(base_conf: dict, regime: str) -> dict:
    """Return effective strategy_conf for the current regime.

    If base_conf contains ``regime_overrides``, merge the matching
    regime's overrides on top of the base (excluding the overrides key
    itself).  This lets a single arm define different behaviour per
    regime while keeping the flat dict interface that
    ``generate_recommendations`` expects.
    """
    overrides_map = base_conf.get("regime_overrides")
    if not overrides_map:
        return base_conf

    effective = {k: v for k, v in base_conf.items() if k != "regime_overrides"}
    rg_key = _REGIME_KEY_MAP.get(regime, "SIDE")
    rg_overrides = overrides_map.get(rg_key, {})
    effective.update(rg_overrides)
    return effective


# ─────────────────────────────────────────────
# Event-driven trigger logic
# ─────────────────────────────────────────────

def _check_evt_triggers(
    di: int,
    last_rebal_di: int,
    vix: float,
    prev_vix: float,
    regime: str,
    prev_regime: str,
    trigger_conf: dict,
    portfolio: SimPortfolio,
    prev_weights: Optional[np.ndarray],
    close_today: np.ndarray,
    close_rebal: Optional[np.ndarray],
) -> List[str]:
    """Replicate engine's event-driven trigger logic."""
    min_days = trigger_conf.get("min_interval_days", 5)
    max_days = trigger_conf.get("max_interval_days", 14)
    vix_emg = trigger_conf.get("vix_emergency", 30.0)
    vix_rec = trigger_conf.get("vix_recovery", 25.0)
    drift_thr = trigger_conf.get("drift_threshold", 0.15)

    triggers = []
    days_since = di - last_rebal_di

    if vix >= vix_emg and prev_regime not in ("DEFENSIVE", "CRASH"):
        triggers.append("VIX_EMERGENCY")
    elif prev_regime in ("DEFENSIVE", "CRASH") and vix < vix_rec:
        triggers.append("VIX_RECOVERY")

    if days_since < min_days and not triggers:
        return []

    if days_since >= max_days:
        triggers.append("TIME_MAX")

    if (prev_weights is not None and close_rebal is not None
            and close_today is not None and len(prev_weights) > 0):
        ratio = np.where(
            np.isfinite(close_rebal) & (close_rebal > 0),
            close_today / close_rebal, 1.0,
        )
        drifted = prev_weights * ratio
        d_sum = np.nansum(drifted)
        if d_sum > 1e-8:
            drifted = drifted / d_sum * np.nansum(prev_weights)
        delta = float(np.nansum(np.abs(drifted - prev_weights)))
        if delta >= drift_thr:
            triggers.append("DRIFT")

    return triggers


# ─────────────────────────────────────────────
# Main simulation
# ─────────────────────────────────────────────

def run_simulation(
    engine,
    cfg,
    pack: dict,
    signal: dict,
    vix_close_by_date: Dict[str, float],
    vix_regime_by_date: Dict[str, str],
    initial_capital: float = 100000.0,
    daily_buy_limit: float = 1000.0,
    strategy_conf: Optional[dict] = None,
    trigger_conf: Optional[dict] = None,
    rebalance_mode: str = "event_driven",
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    progress_fn: Optional[Callable] = None,
) -> Dict:
    """
    Run Phase 3 daily trading simulation.

    Parameters
    ----------
    engine : module
        Loaded quant engine from engine_loader.
    cfg : Config
        Quant engine config.
    pack : dict
        Precomputed data pack from prepare_inputs().
    signal : dict
        Frozen signal with mask, wb, ws, wd.
    vix_close_by_date : dict
        Date str → VIX close.
    vix_regime_by_date : dict
        Date str → regime ("BULL", "SIDE", "DEFENSIVE", "CRASH").
    initial_capital, daily_buy_limit : float
        Trading constraints.
    strategy_conf : dict
        Phase 3 strategy params (from config.yaml's strategy section).
    trigger_conf : dict
        Event-driven trigger params (from config.yaml's triggers section).
    rebalance_mode : str
        "daily" = score + trade every day;
        "event_driven" = trade only on trigger days.
    commission_bps, slippage_bps : float
        Transaction cost in basis points.
    start_date, end_date : str
        Filter date range (YYYY-MM-DD). Defaults to pack's full range.
    progress_fn : callable
        Called with (current_idx, total, message) for progress reporting.

    Returns
    -------
    dict with:
        daily_ts  : pd.DataFrame — daily portfolio snapshots
        trades    : pd.DataFrame — all individual trades
        metrics   : dict — CAGR, Sharpe, MDD, Calmar, etc.
    """
    from daily_runner import generate_recommendations

    dates = list(pack["dates"])
    tickers = list(pack["tickers"])
    N = len(tickers)

    sim_dates = dates[:]
    if start_date:
        sim_dates = [d for d in sim_dates if d >= start_date]
    if end_date:
        sim_dates = [d for d in sim_dates if d <= end_date]

    date_to_di = {d: i for i, d in enumerate(dates)}

    sel = np.asarray(signal["mask"], dtype=bool)
    cfg_sim = dataclasses.replace(cfg)
    cfg_sim.enable_completeness_history_filter = False

    active_w_bull = engine.get_regime_active_weight_vector(
        cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "BULL")
    active_w_side = engine.get_regime_active_weight_vector(
        cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "SIDE")
    active_w_def = engine.get_regime_active_weight_vector(
        cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "DEFENSIVE")

    strat_base = strategy_conf or {}
    trig = trigger_conf or {}
    portfolio = SimPortfolio(initial_capital)

    close_arr = np.asarray(pack.get("close", pack.get("raw_close")), dtype=np.float64)

    last_rebal_di = -9999
    prev_regime = "SIDE"
    prev_weights = None
    close_at_rebal = None

    daily_rows = []
    prev_value = initial_capital
    total_sim = len(sim_dates)

    for day_idx, d in enumerate(sim_dates):
        di = date_to_di.get(d)
        if di is None:
            continue

        if progress_fn and day_idx % 50 == 0:
            pct = day_idx / max(total_sim, 1) * 100
            progress_fn(day_idx, total_sim,
                        f"\r  [{pct:5.1f}%] Day {day_idx}/{total_sim}: {d}")

        price_map = {}
        for ni in range(N):
            p = close_arr[di, ni]
            if np.isfinite(p) and p > 0:
                price_map[tickers[ni]] = float(p)

        portfolio.update_prices(price_map)

        regime = vix_regime_by_date.get(d, "SIDE")
        vix = vix_close_by_date.get(d, 20.0)
        score_regime = "DEFENSIVE" if regime in ("CRASH", "BEAR") else regime

        should_rebalance = False
        trigger_type = ""

        if rebalance_mode == "daily":
            should_rebalance = True
            trigger_type = "DAILY"
        elif rebalance_mode == "event_driven":
            if last_rebal_di < -999 + 10:
                should_rebalance = True
                trigger_type = "INITIAL"
            else:
                trigs = _check_evt_triggers(
                    di, last_rebal_di, vix,
                    vix_close_by_date.get(
                        dates[max(0, di - 1)], 20.0) if di > 0 else 20.0,
                    regime, prev_regime, trig, portfolio,
                    prev_weights,
                    close_arr[di] if di < len(close_arr) else None,
                    close_at_rebal,
                )
                if trigs:
                    should_rebalance = True
                    trigger_type = ",".join(trigs)

        actions_str = ""
        n_buys = n_sells = n_trim = n_sl = 0

        if should_rebalance:
            try:
                scores = engine._score_vector_for_regime(
                    pack=pack, di=di, sel=sel,
                    active_w_bull=active_w_bull,
                    active_w_side=active_w_side,
                    active_w_def=active_w_def,
                    score_regime=score_regime,
                    cfg=cfg_sim,
                )

                score100 = 100.0 * np.clip(scores, 0.0, 1.0)
                rows = []
                for ni in range(N):
                    s = score100[ni]
                    if np.isfinite(s) and s > 0 and tickers[ni] in price_map:
                        rows.append({
                            "Ticker": tickers[ni],
                            "Score": round(float(s), 2),
                            "Price": price_map[tickers[ni]],
                        })

                if rows:
                    scores_df = pd.DataFrame(rows).sort_values(
                        "Score", ascending=False).reset_index(drop=True)

                    holdings_val = portfolio.get_value(price_map)
                    cash = portfolio.get_cash_balance()
                    total_capital = holdings_val + max(cash, 0)

                    strat = resolve_strategy(strat_base, regime)

                    daily_limit = _compute_daily_limit(
                        cash, holdings_val, strat, daily_buy_limit)

                    recos = generate_recommendations(
                        cfg_sim, scores_df, regime, vix,
                        portfolio, total_capital, daily_limit, strat,
                    )

                    if not recos.empty:
                        portfolio.apply_actions(
                            recos, price_map, d,
                            commission_bps, slippage_bps,
                        )
                        portfolio.save_recommendations(recos)

                        n_sl = len(recos[recos["Action"] == "STOP_LOSS"])
                        n_sells = len(recos[recos["Action"] == "SELL"])
                        n_buys = len(recos[recos["Action"].isin(
                            ["BUY_NEW", "BUY_MORE"])])
                        n_trim = len(recos[recos["Action"] == "TRIM"])

                    last_rebal_di = di
                    close_at_rebal = close_arr[di].copy()

                    w = np.zeros(N, dtype=np.float64)
                    total_v = portfolio.get_value(price_map) + portfolio.cash
                    if total_v > 0:
                        for ni, t in enumerate(tickers):
                            if t in portfolio.holdings:
                                h = portfolio.holdings[t]
                                w[ni] = (h["shares"] * price_map.get(t, 0)) / total_v
                    prev_weights = w

            except Exception as e:
                actions_str = f"ERR:{str(e)[:40]}"

        if n_buys + n_sells + n_sl + n_trim > 0:
            parts = []
            if n_sl: parts.append(f"SL={n_sl}")
            if n_sells: parts.append(f"S={n_sells}")
            if n_buys: parts.append(f"B={n_buys}")
            if n_trim: parts.append(f"T={n_trim}")
            actions_str = " ".join(parts)

        holdings_val = portfolio.get_value(price_map)
        total_val = holdings_val + portfolio.cash
        daily_ret = (total_val / prev_value - 1) if prev_value > 0 else 0

        daily_rows.append({
            "Date": d,
            "PortfolioValue": round(total_val, 2),
            "Cash": round(portfolio.cash, 2),
            "HoldingsValue": round(holdings_val, 2),
            "DailyReturn": round(daily_ret, 6),
            "Regime": regime,
            "VIX": round(vix, 2),
            "NumHoldings": len(portfolio.holdings),
            "Trigger": trigger_type if should_rebalance else "",
            "Actions": actions_str,
        })

        prev_value = total_val
        prev_regime = regime

    daily_ts = pd.DataFrame(daily_rows)
    if not daily_ts.empty:
        daily_ts["CumReturn"] = (1 + daily_ts["DailyReturn"]).cumprod() - 1

    trades = pd.DataFrame(portfolio.trade_log)
    metrics = compute_metrics(daily_ts, initial_capital, portfolio.total_commission)

    return {
        "daily_ts": daily_ts,
        "trades": trades,
        "metrics": metrics,
        "portfolio": portfolio,
    }


# ─────────────────────────────────────────────
# Performance metrics
# ─────────────────────────────────────────────

def compute_metrics(daily_ts: pd.DataFrame, initial_capital: float,
                    total_commission: float = 0.0) -> dict:
    if daily_ts.empty or len(daily_ts) < 2:
        return {}

    rets = daily_ts["DailyReturn"].values
    values = daily_ts["PortfolioValue"].values

    n_days = len(rets)
    n_years = n_days / 252.0

    final_val = values[-1]
    cagr = (final_val / initial_capital) ** (1.0 / n_years) - 1 if n_years > 0 else 0

    mean_ret = np.mean(rets)
    std_ret = np.std(rets, ddof=1) if len(rets) > 1 else 1e-10
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 1e-12 else 0

    cummax = np.maximum.accumulate(values)
    drawdowns = (values - cummax) / np.where(cummax > 0, cummax, 1.0)
    max_dd = abs(np.min(drawdowns)) if len(drawdowns) > 0 else 0

    calmar = cagr / max_dd if max_dd > 1e-8 else 0

    win_rate = float(np.mean(rets > 0)) if n_days > 0 else 0

    total_return = final_val / initial_capital - 1

    monthly_ts = daily_ts.copy()
    monthly_ts["Date"] = pd.to_datetime(monthly_ts["Date"])
    monthly_ts = monthly_ts.set_index("Date").resample("ME")["PortfolioValue"].last().dropna()
    if len(monthly_ts) >= 2:
        m_rets = monthly_ts.pct_change().dropna()
        monthly_win = float((m_rets > 0).mean())
    else:
        monthly_win = 0

    rebal_days = int((daily_ts["Trigger"] != "").sum())

    result = {
        "CAGR": round(cagr, 6),
        "Net_Sharpe": round(sharpe, 4),
        "Max_Drawdown": round(max_dd, 6),
        "Calmar_Ratio": round(calmar, 4),
        "Total_Return": round(total_return, 4),
        "Daily_Win_Rate": round(win_rate, 4),
        "Monthly_Win_Rate": round(monthly_win, 4),
        "Start_Date": str(daily_ts["Date"].iloc[0]),
        "End_Date": str(daily_ts["Date"].iloc[-1]),
        "Trading_Days": n_days,
        "Years": round(n_years, 2),
        "Final_Value": round(final_val, 2),
        "Initial_Capital": initial_capital,
        "Max_Holdings": int(daily_ts["NumHoldings"].max()),
        "Rebalance_Days": rebal_days,
        "Total_Commission": round(total_commission, 2),
        "Commission_Pct": round(total_commission / initial_capital * 100, 2),
    }

    if "Regime" in daily_ts.columns:
        regime_map = {"BULL": "BULL", "SIDE": "SIDE",
                      "DEFENSIVE": "DEF", "CRASH": "DEF"}
        daily_ts = daily_ts.copy()
        daily_ts["_rg"] = daily_ts["Regime"].map(
            lambda r: regime_map.get(r, "SIDE"))

        rg_series = daily_ts["_rg"].values
        for rg in ["BULL", "SIDE", "DEF"]:
            rg_mask = daily_ts["_rg"] == rg
            rg_rets = daily_ts.loc[rg_mask, "DailyReturn"].values
            rg_vals = daily_ts.loc[rg_mask, "PortfolioValue"].values
            n_rg = len(rg_rets)

            max_streak = 0
            cur_streak = 0
            for r in rg_series:
                if r == rg:
                    cur_streak += 1
                    if cur_streak > max_streak:
                        max_streak = cur_streak
                else:
                    cur_streak = 0

            if n_rg < 2:
                result[f"{rg}_Days"] = n_rg
                result[f"{rg}_MaxStreak"] = max_streak
                continue

            rg_ann = (1 + np.mean(rg_rets)) ** 252 - 1
            rg_std = np.std(rg_rets, ddof=1)
            rg_sharpe = (np.mean(rg_rets) / rg_std * np.sqrt(252)
                         if rg_std > 1e-12 else 0)
            rg_cummax = np.maximum.accumulate(rg_vals)
            rg_dd = (rg_vals - rg_cummax) / np.where(rg_cummax > 0, rg_cummax, 1)
            rg_mdd = abs(np.min(rg_dd))
            rg_calmar = rg_ann / rg_mdd if rg_mdd > 1e-8 else 0
            rg_win = float(np.mean(rg_rets > 0))

            result[f"{rg}_Days"] = n_rg
            result[f"{rg}_MaxStreak"] = max_streak
            result[f"{rg}_AnnRet"] = round(rg_ann, 4)
            result[f"{rg}_Sharpe"] = round(rg_sharpe, 4)
            result[f"{rg}_MDD"] = round(rg_mdd, 4)
            result[f"{rg}_Calmar"] = round(rg_calmar, 4)
            result[f"{rg}_WinRate"] = round(rg_win, 4)

    return result


# ─────────────────────────────────────────────
# Report formatting
# ─────────────────────────────────────────────

def format_report(result: dict) -> str:
    m = result["metrics"]
    if not m:
        return "No results."

    lines = [
        "=" * 60,
        " Phase 3 Backtest Simulation Report",
        "=" * 60,
        f"Period : {m.get('Start_Date','?')} ~ {m.get('End_Date','?')}",
        f"         {m.get('Years',0):.1f} years ({m.get('Trading_Days',0):,} trading days)",
        f"Capital: ${m.get('Initial_Capital',0):,.0f} → ${m.get('Final_Value',0):,.2f}",
        "",
        "[Performance]",
        f"  CAGR             : {m.get('CAGR',0)*100:+.2f}%",
        f"  Net Sharpe       : {m.get('Net_Sharpe',0):.4f}",
        f"  Max Drawdown     : {m.get('Max_Drawdown',0)*100:.2f}%",
        f"  Calmar Ratio     : {m.get('Calmar_Ratio',0):.4f}",
        f"  Total Return     : {m.get('Total_Return',0)*100:+.2f}%",
        f"  Daily Win Rate   : {m.get('Daily_Win_Rate',0)*100:.1f}%",
        f"  Monthly Win Rate : {m.get('Monthly_Win_Rate',0)*100:.1f}%",
        "",
        "[Portfolio]",
        f"  Max Holdings     : {m.get('Max_Holdings',0)}",
        f"  Rebalance Days   : {m.get('Rebalance_Days',0):,}",
        f"  Total Commission : ${m.get('Total_Commission',0):,.2f} "
        f"({m.get('Commission_Pct',0):.2f}% of capital)",
    ]

    trades = result.get("trades", pd.DataFrame())
    if not trades.empty:
        n_buys = len(trades[trades["Action"].isin(["BUY_NEW", "BUY_MORE"])])
        n_sells = len(trades[trades["Action"].isin(["SELL", "STOP_LOSS"])])
        n_trims = len(trades[trades["Action"] == "TRIM"])
        total_bought = abs(trades[trades["Value"] < 0]["Value"].sum())
        total_sold = trades[trades["Value"] > 0]["Value"].sum()
        lines.extend([
            "",
            "[Trades]",
            f"  Buy Trades  : {n_buys:,}  (${total_bought:,.0f} invested)",
            f"  Sell Trades : {n_sells:,}  (${total_sold:,.0f} recovered)",
            f"  Trim Trades : {n_trims:,}",
            f"  Stop Losses : {len(trades[trades['Action'] == 'STOP_LOSS']):,}",
        ])

    portfolio = result.get("portfolio")
    if portfolio and portfolio.holdings:
        lines.append("")
        lines.append(f"[Final Holdings — {len(portfolio.holdings)} stocks]")
        sorted_h = sorted(portfolio.holdings.items(),
                          key=lambda x: x[1]["shares"] * x[1].get("current_price", x[1]["avg_cost"]),
                          reverse=True)
        for t, h in sorted_h[:10]:
            cp = h.get("current_price", h["avg_cost"])
            mv = h["shares"] * cp
            pnl = ((cp / h["avg_cost"]) - 1) * 100 if h["avg_cost"] > 0 else 0
            lines.append(f"  {t:6s}  {h['shares']:4d} sh  ${mv:>10,.2f}  PnL={pnl:+.1f}%")
        if len(portfolio.holdings) > 10:
            lines.append(f"  ... and {len(portfolio.holdings) - 10} more")

    lines.append("=" * 60)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Convenience: build pack + VIX + run all
# ─────────────────────────────────────────────

def prepare_and_run(
    config_yaml_path: str,
    start_date: str = "2017-01-03",
    end_date: Optional[str] = None,
    rebalance_mode: str = "event_driven",
    progress_fn: Optional[Callable] = None,
) -> Dict:
    """One-call entry point: load config, build pack, run simulation."""
    import yaml
    from engine_loader import engine
    from daily_runner import load_frozen_signal

    if progress_fn:
        progress_fn(0, 100, "Loading config...")

    with open(config_yaml_path) as f:
        conf = yaml.safe_load(f)

    signal_path = conf["paths"]["frozen_signal"]
    signal = load_frozen_signal(signal_path)

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    if progress_fn:
        progress_fn(5, 100, "Building pack (this may take 30+ minutes)...")

    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, type(getattr(cfg, k))(v))

    cfg.start_panel_date = datetime.strptime(start_date, "%Y-%m-%d")
    cfg.end_date = datetime.strptime(end_date, "%Y-%m-%d")
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]

    result = engine.prepare_inputs(cfg)
    pack = result if isinstance(result, dict) and "pack" in result else {"pack": result}
    if "pack" in pack:
        pack = pack["pack"]

    if progress_fn:
        progress_fn(50, 100, "Building VIX regime timeseries...")

    vix_df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=30),
        datetime.strptime(end_date, "%Y-%m-%d"),
    )

    vix_close_by_date = {}
    vix_regime_by_date = {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            vix_close_by_date[d_str] = float(row.get("close", row.get("vix_close", 20)))
            vix_regime_by_date[d_str] = str(row.get("regime", "SIDE"))

    if progress_fn:
        progress_fn(55, 100, "Running simulation...")

    result = run_simulation(
        engine=engine,
        cfg=cfg,
        pack=pack,
        signal=signal,
        vix_close_by_date=vix_close_by_date,
        vix_regime_by_date=vix_regime_by_date,
        initial_capital=conf["portfolio"]["initial_cash"],
        daily_buy_limit=conf["portfolio"]["daily_buy_limit"],
        strategy_conf=conf.get("strategy", {}),
        trigger_conf=conf.get("triggers", {}),
        rebalance_mode=rebalance_mode,
        commission_bps=10.0,
        slippage_bps=5.0,
        start_date=start_date,
        end_date=end_date,
        progress_fn=progress_fn,
    )

    if progress_fn:
        progress_fn(100, 100, "Done.")

    return result
