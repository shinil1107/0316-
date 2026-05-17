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

from exits import RecosAction  # noqa: E402  (action-dispatch constants)


# ─────────────────────────────────────────────
# In-memory portfolio tracker
# ─────────────────────────────────────────────

class SimPortfolio:
    """In-memory portfolio for fast backtesting.

    Implements the subset of HoldingsManager interface that
    generate_recommendations() requires: load_current(), load_recommendations(),
    save_recommendations().

    D1.4 — holdings entries now mirror ``HoldingsManager.holdings`` exactly so
    ``exits.build_holding_snapshots`` produces identical snapshots in sim and
    live runs.  Per-ticker dict schema::

        {
          "shares":        int,
          "avg_cost":      float,
          "current_price": float,
          "entry_date":    str  (YYYY-MM-DD, set at first BUY_NEW),
          "entry_price":   float,
          "entry_score":   float,
          "entry_rank":    int (-1 if unknown — recos missing Rank col),
          "entry_regime":  str,
          "peak_price":    float (monotonic high-water mark since entry),
          "last_score":    float (most recent score seen, for D2 decay triggers),
        }
    """

    def __init__(self, initial_cash: float):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.holdings: Dict[str, dict] = {}
        self._last_recos = pd.DataFrame()
        self.trade_log: List[dict] = []
        self.total_commission = 0.0

    def load_current(self) -> pd.DataFrame:
        """Materialise holdings dict into a HoldingsManager-shaped DataFrame.

        Keeps parity with the live ``HoldingsManager.load_current`` schema
        post-D1.6 (BuyDate, EntryScore, EntryRank, EntryRegime, PeakPrice,
        LastScore) so ``build_holding_snapshots`` sees identical columns
        regardless of where the portfolio state lives.
        """
        if not self.holdings:
            return pd.DataFrame(columns=[
                "Ticker", "BuyDate", "BuyPrice", "Shares", "CurrentPrice",
                "MarketValue", "PnL_Pct", "Weight",
                "EntryScore", "EntryRank", "EntryRegime",
                "PeakPrice", "LastScore",
            ])
        rows = []
        for t, h in self.holdings.items():
            cp = h.get("current_price", h["avg_cost"])
            mv = h["shares"] * cp
            pnl = ((cp / h["avg_cost"]) - 1) * 100 if h["avg_cost"] > 0 else 0
            rows.append({
                "Ticker": t,
                "BuyDate": h.get("entry_date", ""),
                "BuyPrice": h["avg_cost"],
                "Shares": h["shares"],
                "CurrentPrice": cp,
                "MarketValue": mv,
                "PnL_Pct": pnl,
                "Weight": 0.0,
                "EntryScore": float(h.get("entry_score", 0.0) or 0.0),
                "EntryRank": int(h.get("entry_rank", -1) or -1),
                "EntryRegime": str(h.get("entry_regime", "") or ""),
                "PeakPrice": float(h.get("peak_price", 0.0) or 0.0),
                "LastScore": float(h.get("last_score", 0.0) or 0.0),
            })
        return pd.DataFrame(rows)

    def load_recommendations(self) -> pd.DataFrame:
        return self._last_recos

    def save_recommendations(self, recos: pd.DataFrame):
        self._last_recos = recos.copy()

    def update_prices(self, price_map: dict):
        """Update current_price and bump the PeakPrice high-water mark."""
        for t in list(self.holdings.keys()):
            if t in price_map and price_map[t] > 0:
                p = float(price_map[t])
                self.holdings[t]["current_price"] = p
                prev_peak = float(self.holdings[t].get("peak_price", 0.0) or 0.0)
                if p > prev_peak:
                    self.holdings[t]["peak_price"] = p

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
        """Apply recommendation actions to portfolio with transaction costs.

        D1.4 additions:
          * ``BUY_NEW`` seeds ``entry_*`` / ``peak_price`` / ``last_score``
            from the reco row (Score, Regime, Rank columns).  These fields
            power Track D dynamic exit triggers (peak_drawdown,
            score_decay, regime_switch, etc.).
          * ``BUY_MORE`` keeps entry_* frozen, refreshes ``last_score`` and
            bumps ``peak_price`` if the buy price is a new high.
          * New-avg-cost still computed from the transacted ``gross``
            (i.e. pre-commission value), preserving legacy backtest P&L.
        """
        if recos.empty:
            return

        cost_rate = (commission_bps + slippage_bps) / 10000.0

        for _, r in recos.iterrows():
            action = r["Action"]
            ticker = r["Ticker"]
            price = price_map.get(ticker, r.get("Price", 0))
            if price <= 0:
                continue

            # D2-aware dispatch: ``RecosAction.FULL_CLOSE`` / ``PARTIAL_CLOSE``
            # enumerate all SELL_* / TRIM_* variants so new triggers (D2.1-D2.6)
            # route to the correct code path without touching this block.
            if RecosAction.is_full_close(action):
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

            elif RecosAction.is_partial_close(action):
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
                            "Date": date, "Action": action, "Ticker": ticker,
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

                # D1.4 — extract entry attribution off the reco row.  All
                # three are optional; defaults keep behaviour identical
                # to legacy SimPortfolio when Rank/Score/Regime absent.
                reco_score = float(r.get("Score", 0.0) or 0.0)
                reco_rank = int(r.get("Rank", -1) or -1)
                reco_regime = str(r.get("Regime", "") or "")

                if ticker in self.holdings:
                    # BUY_MORE — entry_* FROZEN, live fields refreshed.
                    old = self.holdings[ticker]
                    new_shares = old["shares"] + shares
                    new_avg = (old["shares"] * old["avg_cost"] + gross) / new_shares
                    old["shares"] = new_shares
                    old["avg_cost"] = new_avg
                    old["current_price"] = price
                    old["last_score"] = reco_score
                    prev_peak = float(old.get("peak_price", 0.0) or 0.0)
                    old["peak_price"] = max(prev_peak, float(price))
                else:
                    # BUY_NEW — seed entry_* exactly once.
                    self.holdings[ticker] = {
                        "shares": shares,
                        "avg_cost": price,
                        "current_price": price,
                        "entry_date": date,
                        "entry_price": price,
                        "entry_score": reco_score,
                        "entry_rank": reco_rank,
                        "entry_regime": reco_regime,
                        "peak_price": price,
                        "last_score": reco_score,
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
    blend_conf: Optional[dict] = None,
    vix_smooth_by_date: Optional[Dict[str, float]] = None,
    # D4 diagnostics — if not None, each fired upstream exit verdict is
    # appended as a dict.  Enabled per-run by run_lab / CLI dump flag.
    trade_log: Optional[List[Dict]] = None,
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

    cfg_sim = dataclasses.replace(cfg)
    cfg_sim.enable_completeness_history_filter = False

    # ── Phase ML — external-scores signal (XGBoost/LightGBM walk-forward).
    #   When signal['signal_type'] == 'ml_external_scores', the predicted
    #   per-(date, ticker, regime) scores are precomputed in scores_panel
    #   and looked up directly at each rebalance day, bypassing the GA
    #   linear scoring kernel (engine._score_vector_for_regime).
    #
    #   LIVE SAFETY: This branch is reachable ONLY when an ML signal npz is
    #   explicitly passed in via the simulator API. ``daily_runner.py`` and
    #   the production live path never load ML signals, so the live engine
    #   continues to use the GA Linear path 100% unchanged.
    _ml_mode = bool(signal.get("signal_type") == "ml_external_scores")
    _ml_scores_panel = None
    _ml_di_offset = 0
    if _ml_mode:
        _ml_scores_panel = np.asarray(signal["scores_panel"], dtype=np.float32)
        _ml_dates = np.asarray(signal.get("dates", dates)).astype(str)
        # Map ML panel di's onto pack di's by date string. We assume the
        # ML signal was generated against the same pack.
        _pack_dates_str = np.asarray(dates).astype(str)
        if (len(_ml_dates) != len(_pack_dates_str)
                or not np.array_equal(_ml_dates, _pack_dates_str)):
            # Build a lookup so simulator stays robust if ml_dates is a
            # subset/superset (rare; usually byte-identical).
            _ml_date_to_idx = {str(d): i for i, d in enumerate(_ml_dates)}
            _ml_di_remap = np.array(
                [_ml_date_to_idx.get(d, -1) for d in _pack_dates_str],
                dtype=np.int64,
            )
        else:
            _ml_di_remap = None  # identity mapping

    # ── Path C: compose-aware precomputation ──
    _compose_mode = (signal.get("mode") == "compose")
    if _compose_mode:
        _rs = signal.get("regime_signals") or {}
        regime_slots = {}  # regime → (sel, w_bull, w_side, w_def, source_path)
        for rg in ("BULL", "SIDE", "DEFENSIVE"):
            s = _rs.get(rg)
            if s is None:
                s = signal  # shouldn't happen, but be defensive
            _sel_rg = np.asarray(s["mask"], dtype=bool)
            _wb = engine.get_regime_active_weight_vector(
                cfg_sim, _sel_rg, _sel_rg, _sel_rg,
                s["wb"], s["ws"], s["wd"], "BULL")
            _ws = engine.get_regime_active_weight_vector(
                cfg_sim, _sel_rg, _sel_rg, _sel_rg,
                s["wb"], s["ws"], s["wd"], "SIDE")
            _wd = engine.get_regime_active_weight_vector(
                cfg_sim, _sel_rg, _sel_rg, _sel_rg,
                s["wb"], s["ws"], s["wd"], "DEFENSIVE")
            _src = (signal.get("regime_paths") or {}).get(rg) or signal.get("default_path", "")
            regime_slots[rg] = (_sel_rg, _wb, _ws, _wd, _src)
        sel = regime_slots["SIDE"][0]  # default mask used only for non-rebal logic paths
        active_w_bull = regime_slots["SIDE"][1]  # unused in compose scoring path
        active_w_side = regime_slots["SIDE"][2]
        active_w_def = regime_slots["SIDE"][3]
    elif _ml_mode:
        # ML signal: GA-linear weight vectors are unused; set to harmless
        # zero placeholders so downstream code that still references
        # ``sel/active_w_*`` (e.g. exit-trigger history fallback) does not
        # crash. The actual scoring is done via panel lookup in the
        # rebalance block below.
        F_dim = int(np.asarray(signal.get("mask", np.zeros(36))).size)
        sel = np.zeros(F_dim, dtype=bool)
        active_w_bull = np.zeros(F_dim, dtype=np.float64)
        active_w_side = np.zeros(F_dim, dtype=np.float64)
        active_w_def = np.zeros(F_dim, dtype=np.float64)
        regime_slots = None
    else:
        sel = np.asarray(signal["mask"], dtype=bool)
        active_w_bull = engine.get_regime_active_weight_vector(
            cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "BULL")
        active_w_side = engine.get_regime_active_weight_vector(
            cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "SIDE")
        active_w_def = engine.get_regime_active_weight_vector(
            cfg_sim, sel, sel, sel, signal["wb"], signal["ws"], signal["wd"], "DEFENSIVE")
        regime_slots = None

    strat_base = strategy_conf or {}
    trig = trigger_conf or {}
    portfolio = SimPortfolio(initial_capital)

    close_arr = np.asarray(pack.get("close", pack.get("raw_close")), dtype=np.float64)

    _bc = blend_conf or {}
    _blend_on = bool(_bc.get("regime_blend_enabled", False))
    if _compose_mode and _blend_on:
        print("  [compose][warn] regime_compose + regime_blend_enabled are "
              "mutually exclusive in C.1. Forcing blend OFF.")
        _blend_on = False
    if _blend_on:
        from regime_blend import apply_hysteresis, compute_blend_alphas, blend_weight_vectors
        _bp = dict(
            bull_threshold=float(getattr(cfg, "vix_bull_threshold", 18.0)),
            def_threshold=float(getattr(cfg, "vix_defensive_threshold", 30.0)),
            bull_side_blend_width=float(_bc.get("bull_side_blend_width", 2.0)),
            side_def_blend_width=float(_bc.get("side_def_blend_width", 3.0)),
            cb_threshold=float(getattr(cfg, "circuit_breaker_vix_threshold", 35.0)),
            cb_enabled=bool(getattr(cfg, "enable_circuit_breaker", True)),
        )

    last_rebal_di = -9999
    prev_regime = "SIDE"
    prev_weights = None
    close_at_rebal = None

    daily_rows = []
    prev_value = initial_capital
    total_sim = len(sim_dates)

    # D4.2 — rolling buffers for RiskOffAssessor inputs.
    # VIX series length 10 covers the assessor's default 7-day lookback + buffer.
    # Regime series length 10 covers the default 5-day transition window + buffer.
    from collections import deque
    _vix_hist = deque(maxlen=10)
    _regime_hist = deque(maxlen=10)
    _portfolio_peak = float(initial_capital)

    # T-buy-grace — Idea 2: rolling history of regime-correct top-N
    # ticker sets, one entry per rebalance day, used to filter NEW BUYS
    # (not BUY_MORE) so a ticker must have been in top-N for the last
    # ``buy_grace_days`` rebal days before we initiate a position.
    # Read at runtime from the regime-resolved strategy dict; if the knob
    # is absent or 0, this list is appended-to but never consulted, and
    # behaviour is byte-identical to legacy.  Default maxlen = 32 covers
    # any realistic grace window (typical sweep N ∈ {0,1,2,3,5}).
    _top_n_history: deque = deque(maxlen=32)
    _buy_grace_diag = {"filtered_buys_total": 0, "rebal_days_with_filter": 0}

    # A1 — α_realized vol targeting (Idea 3, 2026-04-25).
    # Maintain a rolling window of daily portfolio returns to compute the
    # 30-day realized volatility (annualized).  At each rebalance day, if
    # ``strat["vol_target"]`` is enabled, scale ``target_invest_pct`` by
    # ``min(max_scale, max(min_scale, annual_target / realized_vol))`` so
    # gross exposure auto-deleverages when the portfolio's realized vol
    # exceeds the target.  Default lookback = 30 trading days; min_scale
    # caps how aggressive the cut can get; max_scale ≤ 1.0 keeps the
    # overlay one-sided (no leverage).
    #
    # vol_target absent / disabled  ⇒  history is appended but never
    # consulted, behaviour is byte-identical to legacy.
    _vol_target_max_lookback = 252  # generous upper bound for any reasonable lookback
    _daily_returns_hist: deque = deque(maxlen=_vol_target_max_lookback + 1)
    _vol_target_diag = {
        "events": [],          # per-rebal-day samples
        "rebal_days_total": 0,
        "rebal_days_engaged": 0,  # days where scale < 1.0 was applied
        "rebal_days_warmed": 0,   # days where lookback was satisfied
    }

    # R2 — restricted_universe + sector_cap diagnostics (Idea 4, 2026-04-25).
    #
    #   strat["restricted_universe"] = ["AAPL", ...]   # whitelist; tickers
    #       not on the list (and not currently held) are dropped from
    #       scores_df BEFORE the buy-grace top-N snapshot is taken so the
    #       grace history reflects the restricted universe.  None / missing
    #       ⇒ filter is skipped (byte-identical).
    #
    #   strat["sector_cap"] = {
    #       "enabled":           bool,
    #       "max_pct":           float (e.g. 0.30 for 30 %),
    #       "sector_by_ticker":  {ticker → sector},
    #       "exempt_unknown":    bool, default True (unknown sector bypasses),
    #   }
    #       Applied AFTER buy-grace and BEFORE vol-target / recos.  When any
    #       sector's portfolio share ≥ max_pct, NEW buys (BUY_NEW) into that
    #       sector are blocked by removing such tickers from scores_df;
    #       already-held tickers stay so exit triggers can close them.
    #       Disabled / absent ⇒ byte-identical.
    _restricted_universe_diag = {
        "filtered_buys_total":    0,
        "rebal_days_with_filter": 0,
    }
    _sec_cap_diag = {
        "filtered_buys_total":    0,
        "rebal_days_with_filter": 0,
        "max_breach_pct":         0.0,    # peak observed sector share
        "breach_days":            0,      # rebal days where any sector > cap
        "events":                 [],
    }

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

        vix = vix_close_by_date.get(d, 20.0)
        # D4.2 — VIX & portfolio-peak buffers refreshed each sim day,
        # independent of rebalance cadence (assessor needs fresh deltas even
        # on non-rebal days to keep the 7-day delta meaningful).
        _vix_hist.append(float(vix))
        _portfolio_cur_value = portfolio.get_value(price_map) + max(portfolio.get_cash_balance(), 0.0)
        if _portfolio_cur_value > _portfolio_peak:
            _portfolio_peak = _portfolio_cur_value
        vix_for_blend = (vix_smooth_by_date or {}).get(d, vix) if _blend_on else vix
        if _blend_on:
            regime = apply_hysteresis(prev_regime, vix_for_blend, **_bp)
        else:
            regime = vix_regime_by_date.get(d, "SIDE")
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
                if _ml_mode:
                    # ML score panel lookup — direct (date, ticker, regime) → score.
                    # No GA linear computation; bypass _score_vector_for_regime.
                    rg_idx = {"BULL": 0, "SIDE": 1, "DEFENSIVE": 2}.get(
                        score_regime, 1)
                    if _ml_di_remap is not None:
                        ml_di = int(_ml_di_remap[di])
                    else:
                        ml_di = di
                    if ml_di < 0:
                        scores = np.full(N, np.nan, dtype=np.float64)
                    else:
                        scores = np.asarray(
                            _ml_scores_panel[ml_di, :, rg_idx], dtype=np.float64)
                elif _compose_mode:
                    _slot = regime_slots.get(score_regime) or regime_slots["SIDE"]
                    _sel_rg, _wb_rg, _ws_rg, _wd_rg, _ = _slot
                    scores = engine._score_vector_for_regime(
                        pack=pack, di=di, sel=_sel_rg,
                        active_w_bull=_wb_rg,
                        active_w_side=_ws_rg,
                        active_w_def=_wd_rg,
                        score_regime=score_regime,
                        cfg=cfg_sim,
                    )
                elif _blend_on:
                    _alphas = compute_blend_alphas(vix_for_blend, **_bp)
                    if max(_alphas) < 1.0:
                        w_blend = blend_weight_vectors(
                            active_w_bull, active_w_side, active_w_def, _alphas)
                        scores = engine._score_vector_for_regime(
                            pack=pack, di=di, sel=sel,
                            active_w_bull=w_blend,
                            active_w_side=w_blend,
                            active_w_def=w_blend,
                            score_regime="SIDE",
                            cfg=cfg_sim,
                        )
                    else:
                        scores = engine._score_vector_for_regime(
                            pack=pack, di=di, sel=sel,
                            active_w_bull=active_w_bull,
                            active_w_side=active_w_side,
                            active_w_def=active_w_def,
                            score_regime=score_regime,
                            cfg=cfg_sim,
                        )
                else:
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

                    # R2 — restricted_universe filter (universe whitelist).
                    # Applied BEFORE the buy-grace prefilter snapshot so that
                    # the grace history is taken over the restricted universe
                    # only.  Already-held tickers are kept so existing
                    # positions can still be exit-driven by triggers.
                    _ru_list = strat.get("restricted_universe")
                    if _ru_list:
                        _ru_set = set(_ru_list)
                        _held_now = set(portfolio.holdings.keys())
                        _before_ru = len(scores_df)
                        scores_df = scores_df[
                            scores_df["Ticker"].isin(_ru_set | _held_now)
                        ].reset_index(drop=True)
                        _filt_ru = _before_ru - len(scores_df)
                        if _filt_ru > 0:
                            _restricted_universe_diag["filtered_buys_total"] += _filt_ru
                            _restricted_universe_diag["rebal_days_with_filter"] += 1

                    # T-buy-grace — Idea 2 (strict variant a):
                    # require a candidate ticker to have appeared in the
                    # regime-correct top-N on each of the last
                    # ``buy_grace_days`` rebal days BEFORE we open a NEW
                    # position in it.  Already-held tickers are exempt
                    # (BUY_MORE bypasses the filter — we don't want to
                    # starve scaling-in of conviction names).
                    #
                    # Order of operations on each rebal day:
                    #   1. Build pre-filter top-N set from sorted scores_df.
                    #   2. If grace > 0 and history is warmed up: filter
                    #      scores_df by intersection of the last K snapshots,
                    #      keeping currently-held tickers regardless.
                    #   3. Append the *pre-filter* snapshot to history so
                    #      future days check against the natural ranking
                    #      rather than the self-filtered universe.
                    #
                    # buy_grace_days = 0  ⇒  filter is skipped entirely;
                    # snapshot is still appended but never consulted, so
                    # output is byte-identical to legacy behaviour.
                    _buy_grace = int(strat.get("buy_grace_days", 0) or 0)
                    if regime == "BULL":
                        _topn_today = int(getattr(cfg_sim, "regime_bull_top_n", 20))
                    elif regime in ("DEFENSIVE", "CRASH"):
                        _topn_today = int(getattr(cfg_sim, "regime_defensive_top_n", 10))
                    else:
                        _topn_today = int(getattr(cfg_sim, "regime_side_top_n", 15))
                    _prefilter_topn = set(
                        scores_df["Ticker"].head(_topn_today).tolist()
                    )
                    if _buy_grace > 0 and len(_top_n_history) >= _buy_grace:
                        _persistent: set = set.intersection(
                            *list(_top_n_history)[-_buy_grace:]
                        )
                        _held = set(portfolio.holdings.keys())
                        _allow = _persistent | _held
                        _before = len(scores_df)
                        scores_df = scores_df[
                            scores_df["Ticker"].isin(_allow)
                        ].reset_index(drop=True)
                        _filtered_now = _before - len(scores_df)
                        if _filtered_now > 0:
                            _buy_grace_diag["filtered_buys_total"] += _filtered_now
                            _buy_grace_diag["rebal_days_with_filter"] += 1
                    _top_n_history.append(_prefilter_topn)

                    # R2 — sector_cap filter (concentration limit).
                    # Always evaluates current sector exposure for diagnostic
                    # purposes (max_breach_pct / breach_days) regardless of
                    # whether enabled, so tracking matrix can quantify how
                    # often the cap *would* bind in baseline runs.
                    _sc_cfg = strat.get("sector_cap") or {}
                    _sc_enabled = bool(_sc_cfg.get("enabled", False))
                    _sec_map_local = _sc_cfg.get("sector_by_ticker", {}) or {}
                    if _sc_cfg and total_capital > 0 and _sec_map_local:
                        _max_pct = float(_sc_cfg.get("max_pct", 0.30))
                        _exempt_unknown = bool(_sc_cfg.get("exempt_unknown", True))
                        _sec_exposure: Dict[str, float] = {}
                        for _tk, _h in portfolio.holdings.items():
                            _sec = _sec_map_local.get(_tk)
                            if _sec is None:
                                if _exempt_unknown:
                                    continue
                                _sec = "_UNKNOWN"
                            _v = _h["shares"] * price_map.get(_tk, 0.0)
                            _sec_exposure[_sec] = _sec_exposure.get(_sec, 0.0) + _v
                        _sec_pct = {s: v / total_capital for s, v in _sec_exposure.items()}
                        if _sec_pct:
                            _max_obs = max(_sec_pct.values())
                            if _max_obs > _sec_cap_diag["max_breach_pct"]:
                                _sec_cap_diag["max_breach_pct"] = _max_obs
                            if any(p > _max_pct for p in _sec_pct.values()):
                                _sec_cap_diag["breach_days"] += 1
                        if _sc_enabled:
                            _saturated = {s for s, p in _sec_pct.items() if p >= _max_pct}
                            if _saturated:
                                _held_sc = set(portfolio.holdings.keys())
                                def _allow_row_sc(
                                    t,
                                    _held=_held_sc,
                                    _smap=_sec_map_local,
                                    _sat=_saturated,
                                    _eu=_exempt_unknown,
                                ):
                                    if t in _held:
                                        return True
                                    sec = _smap.get(t)
                                    if sec is None:
                                        return _eu
                                    return sec not in _sat
                                _before_sc = len(scores_df)
                                scores_df = scores_df[
                                    scores_df["Ticker"].apply(_allow_row_sc)
                                ].reset_index(drop=True)
                                _filt_sc = _before_sc - len(scores_df)
                                if _filt_sc > 0:
                                    _sec_cap_diag["filtered_buys_total"] += _filt_sc
                                    _sec_cap_diag["rebal_days_with_filter"] += 1
                                    if len(_sec_cap_diag["events"]) < 2000:
                                        _sec_cap_diag["events"].append({
                                            "date": str(d),
                                            "saturated": list(_saturated),
                                            "exposure_pct": {
                                                s: round(_sec_pct[s], 4)
                                                for s in _saturated
                                            },
                                            "filtered_n": int(_filt_sc),
                                        })

                    # A1 — α_realized vol targeting hook.
                    # Reads regime-resolved ``strat["vol_target"]`` so the
                    # knob can be either flat (top-level dict) or per-regime
                    # via regime_overrides.  Spec:
                    #   strat["vol_target"] = {
                    #       "enabled": True,
                    #       "annual_target": 0.20,   # 20% annualized vol
                    #       "lookback_days": 30,
                    #       "min_scale": 0.30,       # never scale below 30%
                    #       "max_scale": 1.00,       # never lever > 1
                    #       "min_warmup_days": None, # default = lookback
                    #   }
                    _vt_cfg = strat.get("vol_target") or {}
                    if _vt_cfg.get("enabled", False):
                        _vt_target = float(_vt_cfg.get("annual_target", 0.20))
                        _vt_lookback = int(_vt_cfg.get("lookback_days", 30))
                        _vt_min_scale = float(_vt_cfg.get("min_scale", 0.30))
                        _vt_max_scale = float(_vt_cfg.get("max_scale", 1.0))
                        _vt_warmup = int(_vt_cfg.get("min_warmup_days") or _vt_lookback)
                        _vol_target_diag["rebal_days_total"] += 1
                        _hist_n = len(_daily_returns_hist)
                        if _hist_n >= _vt_warmup and _vt_target > 0:
                            _vol_target_diag["rebal_days_warmed"] += 1
                            _rets = np.asarray(
                                list(_daily_returns_hist)[-_vt_lookback:],
                                dtype=float,
                            )
                            _rv = float(np.std(_rets, ddof=1)) * float(np.sqrt(252.0))
                            if _rv > 1e-9:
                                _scale = _vt_target / _rv
                                _scale = max(_vt_min_scale, min(_vt_max_scale, _scale))
                            else:
                                _scale = _vt_max_scale
                            _base_inv = float(strat.get("target_invest_pct", 0.97))
                            _eff_inv = max(0.0, min(1.0, _base_inv * _scale))
                            strat["target_invest_pct"] = _eff_inv
                            if _scale < _vt_max_scale - 1e-9:
                                _vol_target_diag["rebal_days_engaged"] += 1
                            _vol_target_diag["events"].append({
                                "date": d, "regime": regime, "vix": float(vix),
                                "realized_vol": round(_rv, 6),
                                "target_vol": _vt_target,
                                "scale": round(_scale, 6),
                                "base_invest_pct": round(_base_inv, 6),
                                "eff_invest_pct": round(_eff_inv, 6),
                            })

                    daily_limit = _compute_daily_limit(
                        cash, holdings_val, strat, daily_buy_limit)

                    # D1.4 — build a per-day HistoryView for exit triggers
                    # that need price / score / rank lookback (D2 territory;
                    # D1 triggers ignore it).  Same (sel, w_bull, w_side,
                    # w_def) slot as used for *today's* scoring above, so
                    # historical scores are computed under the same model.
                    if _ml_mode:
                        # ML signal has no per-day GA scoring kernel;
                        # exit-trigger history falls back to legacy
                        # (history_view=None ≡ D1 default behaviour).
                        history_view = None
                    else:
                        try:
                            if _compose_mode:
                                _slot = regime_slots.get(score_regime) or regime_slots["SIDE"]
                                _hv_sel, _hv_wb, _hv_ws, _hv_wd, _ = _slot
                            else:
                                _hv_sel = sel
                                _hv_wb = active_w_bull
                                _hv_ws = active_w_side
                                _hv_wd = active_w_def
                            from exits import build_history_view
                            history_view = build_history_view(
                                pack=pack, engine=engine, di=di,
                                tickers=tickers, cfg_sim=cfg_sim,
                                sel=_hv_sel,
                                active_w_bull=_hv_wb,
                                active_w_side=_hv_ws,
                                active_w_def=_hv_wd,
                                score_regime=score_regime,
                            )
                        except Exception:
                            # Defensive — never let history construction fail a
                            # rebal day; legacy behaviour with history=None is
                            # a full fallback for all D1 triggers.
                            history_view = None

                    # Regime history needs today's regime included as the last
                    # entry so RiskOffAssessor can detect BULL→stress transitions
                    # that just occurred.
                    _regime_hist_snapshot = list(_regime_hist) + [regime]

                    recos = generate_recommendations(
                        cfg_sim, scores_df, regime, vix,
                        portfolio, total_capital, daily_limit, strat,
                        sim_date=d,
                        history=history_view,
                        vix_series=list(_vix_hist),
                        recent_regimes=_regime_hist_snapshot,
                        portfolio_peak=_portfolio_peak,
                        trade_log=trade_log,
                    )

                    if not recos.empty:
                        portfolio.apply_actions(
                            recos, price_map, d,
                            commission_bps, slippage_bps,
                        )
                        portfolio.save_recommendations(recos)

                        # Diagnostic counters — broadened to include D2/D4 new
                        # action strings so per-day trace reflects actual closes.
                        _actions_col = recos["Action"]
                        n_sl = int((_actions_col == "STOP_LOSS").sum())
                        n_sells = int(_actions_col.map(RecosAction.is_full_close).sum() - n_sl)
                        n_buys = int(_actions_col.isin(["BUY_NEW", "BUY_MORE"]).sum())
                        n_trim = int(_actions_col.map(RecosAction.is_partial_close).sum())

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

        # A1 — append today's portfolio return for the vol-target rolling
        # window (always; cheap O(1) append, only consumed when enabled).
        _daily_returns_hist.append(float(daily_ret))

        prev_value = total_val
        prev_regime = regime
        _regime_hist.append(regime)

    daily_ts = pd.DataFrame(daily_rows)
    if not daily_ts.empty:
        daily_ts["CumReturn"] = (1 + daily_ts["DailyReturn"]).cumprod() - 1

    trades = pd.DataFrame(portfolio.trade_log)
    metrics = compute_metrics(daily_ts, initial_capital, portfolio.total_commission)

    # ── regime_breakdown: flat BULL/SIDE/DEF_* keys → nested subdict ──
    rb = {}
    for rg in ("BULL", "SIDE", "DEF"):
        rb[rg] = {
            "Days":       metrics.get(f"{rg}_Days", 0),
            "MaxStreak":  metrics.get(f"{rg}_MaxStreak", 0),
            "AnnRet":     metrics.get(f"{rg}_AnnRet", 0.0),
            "Sharpe":     metrics.get(f"{rg}_Sharpe", 0.0),
            "MDD":        metrics.get(f"{rg}_MDD", 0.0),
            "Calmar":     metrics.get(f"{rg}_Calmar", 0.0),
            "WinRate":    metrics.get(f"{rg}_WinRate", 0.0),
        }
    metrics["regime_breakdown"] = rb

    # T-buy-grace diag — only meaningful when buy_grace_days > 0
    metrics["buy_grace_filtered_total"] = int(
        _buy_grace_diag["filtered_buys_total"]
    )
    metrics["buy_grace_rebal_days_with_filter"] = int(
        _buy_grace_diag["rebal_days_with_filter"]
    )

    # A1 — vol-target activation summary. The full event list lives on
    # ``result["vol_target_events"]`` for downstream diagnostics; metrics
    # carries scalar summaries only.
    metrics["vol_target_rebal_days_total"] = int(_vol_target_diag["rebal_days_total"])
    metrics["vol_target_rebal_days_warmed"] = int(_vol_target_diag["rebal_days_warmed"])
    metrics["vol_target_rebal_days_engaged"] = int(_vol_target_diag["rebal_days_engaged"])
    if _vol_target_diag["events"]:
        _scales = [e["scale"] for e in _vol_target_diag["events"]]
        _rvs = [e["realized_vol"] for e in _vol_target_diag["events"]]
        metrics["vol_target_scale_mean"] = float(np.mean(_scales))
        metrics["vol_target_scale_min"] = float(np.min(_scales))
        metrics["vol_target_realized_vol_mean"] = float(np.mean(_rvs))
        metrics["vol_target_realized_vol_p50"] = float(np.percentile(_rvs, 50))
        metrics["vol_target_realized_vol_p95"] = float(np.percentile(_rvs, 95))
    else:
        metrics["vol_target_scale_mean"] = 1.0
        metrics["vol_target_scale_min"] = 1.0
        metrics["vol_target_realized_vol_mean"] = 0.0
        metrics["vol_target_realized_vol_p50"] = 0.0
        metrics["vol_target_realized_vol_p95"] = 0.0

    # R2 — restricted_universe & sector_cap diagnostics.
    metrics["restricted_universe_filtered_total"] = int(
        _restricted_universe_diag["filtered_buys_total"]
    )
    metrics["restricted_universe_rebal_days_with_filter"] = int(
        _restricted_universe_diag["rebal_days_with_filter"]
    )
    metrics["sector_cap_filtered_total"] = int(_sec_cap_diag["filtered_buys_total"])
    metrics["sector_cap_rebal_days_with_filter"] = int(
        _sec_cap_diag["rebal_days_with_filter"]
    )
    metrics["sector_cap_max_breach_pct"] = float(_sec_cap_diag["max_breach_pct"])
    metrics["sector_cap_breach_days"] = int(_sec_cap_diag["breach_days"])

    result = {
        "daily_ts": daily_ts,
        "trades": trades,
        "metrics": metrics,
        "portfolio": portfolio,
        "vol_target_events": list(_vol_target_diag["events"]),
        "sector_cap_events": list(_sec_cap_diag["events"]),
    }

    if _compose_mode:
        _paths = signal.get("regime_paths") or {}
        _default = signal.get("default_path", "")
        result["compose_meta"] = {
            "mode": "compose",
            "default_path": _default,
            "regime_paths": {rg: (_paths.get(rg) or _default) for rg in ("BULL", "SIDE", "DEFENSIVE")},
            "regime_k": {
                rg: int(regime_slots[rg][0].sum())
                for rg in ("BULL", "SIDE", "DEFENSIVE")
            },
        }

    return result


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
    vix_smooth_by_date = {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            vix_close_by_date[d_str] = float(row.get("close", row.get("vix_close", 20)))
            vix_regime_by_date[d_str] = str(row.get("regime", "SIDE"))
            if "vix_smooth" in row.index:
                vix_smooth_by_date[d_str] = float(row["vix_smooth"])

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
        blend_conf=conf.get("regime", {}),
        vix_smooth_by_date=vix_smooth_by_date,
    )

    if progress_fn:
        progress_fn(100, 100, "Done.")

    return result
