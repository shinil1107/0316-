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
from run_artifact import create_run_context, write_daily_run_artifact

# Phase 3 dynamic exit architecture (Track D, D1 refactor).
# ``build_triggers`` consumes either legacy keys (enable_stop_loss,
# sell_grace_days, ...) or the new ``strategy.exit_triggers`` list;
# ``evaluate_exits`` runs the priority-ordered stack and returns a
# per-ticker verdict dict.  In legacy-mode with stock config, this
# pipeline produces byte-identical output to the pre-D1 hardcoded
# stop-loss + grace branches below.
from exits import build_triggers, evaluate_exits, RecosAction, RiskOffAssessor


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

    # ── Phase ML — external-scores signal pass-through ──────────────
    # Only honoured by the simulator (offline). The live path
    # (run_daily) loads conf['paths']['frozen_signal'] which is
    # validated to point at a GA-Linear signal, never an ML one.
    if "signal_type" in fs:
        sig_type = fs["signal_type"]
        if isinstance(sig_type, np.ndarray):
            sig_type = sig_type.item() if sig_type.shape == () else str(sig_type)
        sig_type = str(sig_type)
        if sig_type == "ml_external_scores":
            result["signal_type"] = sig_type
            result["scores_panel"] = np.asarray(fs["scores_panel"], dtype=np.float32)
            if "dates" in fs:
                result["dates"] = np.asarray(fs["dates"]).astype(str)
            if "tickers" in fs:
                result["tickers"] = np.asarray(fs["tickers"]).astype(str)
            if "regime_labels" in fs:
                result["regime_labels"] = np.asarray(fs["regime_labels"]).astype(str)
            for k in ("fold_meta", "model_meta", "feature_importance"):
                if k in fs:
                    val = fs[k]
                    if isinstance(val, np.ndarray) and val.dtype == object and val.shape == ():
                        val = val.item()
                    result[k] = val
    return result


# ─────────────────────────────────────────────
# Path C — Regime-composed signal loader
#
# Live path (run_daily) does NOT call this; it continues to use
# load_frozen_signal on conf["paths"]["frozen_signal"]. Backtests
# and Phase 3 Lab call load_composed_signal for opt-in regime routing.
# ─────────────────────────────────────────────

def load_composed_signal(conf: dict) -> dict:
    """Return either a plain single-signal dict (backward compatible with
    load_frozen_signal) or a compose-mode dict.

    Compose-mode schema:
      {
        "mode":            "compose",
        "mask":            union of member masks (for logging/diagnostics),
        "wb" / "ws" / "wd":default signal's weights (for logging only),
        "signal_summary":  default signal's summary,
        "regime_signals":  {"BULL": {...}, "SIDE": {...}, "DEFENSIVE": {...}},
        "default_path":    str,
        "regime_paths":    {"BULL": str|None, "SIDE": str|None, "DEFENSIVE": str|None},
      }

    Rules:
      - regime_compose.enabled=false → return single-signal dict (legacy).
      - enabled=true but every regime_signal_paths is null/missing → single
        mode (no-op composition).
      - If a regime's path is set but the file is missing, that regime
        falls back to default and a warning is printed.
    """
    default_path = conf["paths"]["frozen_signal"]
    default_sig = load_frozen_signal(default_path)

    rc = conf.get("regime_compose", {}) or {}
    if not bool(rc.get("enabled", False)):
        return default_sig

    regime_paths_raw = (conf.get("paths", {}) or {}).get("regime_signal_paths", {}) or {}
    regime_paths = {
        rg: (regime_paths_raw.get(rg) if regime_paths_raw.get(rg) else None)
        for rg in ("BULL", "SIDE", "DEFENSIVE")
    }

    any_override = any(v for v in regime_paths.values())
    if not any_override:
        print("  [compose] regime_compose.enabled=true but no regime_signal_paths "
              "configured — falling back to single signal.")
        return default_sig

    regime_signals = {}
    K = len(default_sig["mask"])
    for rg in ("BULL", "SIDE", "DEFENSIVE"):
        p = regime_paths[rg]
        if p and os.path.exists(p):
            try:
                s = load_frozen_signal(p)
                if len(s["mask"]) != K:
                    print(f"  [compose][warn] {rg} signal has mismatched K "
                          f"({len(s['mask'])} vs default {K}) — fallback to default.")
                    regime_signals[rg] = default_sig
                else:
                    regime_signals[rg] = s
            except Exception as e:
                print(f"  [compose][warn] failed to load {rg} signal: {e} — "
                      f"fallback to default.")
                regime_signals[rg] = default_sig
        else:
            if p:
                print(f"  [compose][warn] {rg} signal not found: {p} — "
                      f"fallback to default.")
            regime_signals[rg] = default_sig

    union_mask = default_sig["mask"].copy()
    for s in regime_signals.values():
        union_mask = union_mask | np.asarray(s["mask"], dtype=bool)

    print(f"  [compose] regime_compose.enabled=true")
    for rg in ("BULL", "SIDE", "DEFENSIVE"):
        s = regime_signals[rg]
        k = int(np.asarray(s["mask"], dtype=bool).sum())
        src = regime_paths[rg] or default_path
        tag = "custom" if regime_paths[rg] and s is not default_sig else "default"
        print(f"    {rg:10s}  k={k:2d}  [{tag}]  {os.path.basename(src)}")

    return {
        "mode": "compose",
        "mask": union_mask,
        "wb": default_sig["wb"],
        "ws": default_sig["ws"],
        "wd": default_sig["wd"],
        "signal_summary": default_sig.get("signal_summary", {}),
        "regime_signals": regime_signals,
        "default_path": default_path,
        "regime_paths": regime_paths,
    }


def pick_signal_for_regime(signal: dict, regime: str) -> dict:
    """Route a regime label to the concrete single-signal dict.

    CRASH and unknown regimes fall back to DEFENSIVE's signal.
    For non-compose signals, returns the signal unchanged.
    """
    if signal.get("mode") != "compose":
        return signal
    key = "DEFENSIVE" if regime in ("CRASH", "BEAR") else regime
    rs = signal.get("regime_signals") or {}
    return rs.get(key) or rs.get("SIDE") or signal


def describe_signal(signal: dict) -> str:
    """Human-readable one-line description of a (possibly composed) signal."""
    if signal.get("mode") == "compose":
        parts = []
        rs = signal.get("regime_signals") or {}
        for rg in ("BULL", "SIDE", "DEFENSIVE"):
            s = rs.get(rg)
            if s is None:
                continue
            k = int(np.asarray(s["mask"], dtype=bool).sum())
            parts.append(f"{rg}:k={k}")
        return "compose[" + " ".join(parts) + "]"
    mask = signal.get("mask")
    if mask is None:
        return "<no mask>"
    return f"single[k={int(np.asarray(mask, dtype=bool).sum())}]"


# ─────────────────────────────────────────────
# VIX & Regime
# ─────────────────────────────────────────────

def get_current_vix(
    cfg,
    prev_regime: str = "SIDE",
    blend_conf: Optional[dict] = None,
) -> Tuple[float, str, Tuple[float, float, float]]:
    """Get latest VIX close, regime (with hysteresis), and blend alphas.

    Returns (vix_close, regime, (alpha_bull, alpha_side, alpha_def)).
    When blend_conf is None or blend is disabled, alphas correspond to
    the discrete regime (one of them is 1.0, the rest 0.0).

    Only uses *settled* (post-market-close) daily bars.  Running during
    US market hours no longer picks up intraday VIX and therefore cannot
    flip the regime mid-session.
    """
    vix_sym = getattr(cfg, "vix_symbol", "^VIX")
    now = datetime.now()
    df = engine.load_ohlcv_from_cache(cfg, vix_sym, now - timedelta(days=60), now)
    if df.empty:
        return 20.0, "SIDE", (0.0, 1.0, 0.0)

    df["date"] = pd.to_datetime(df["date"])
    settled = pd.Timestamp(_last_available_trading_date())
    df = df[df["date"].dt.normalize() <= settled]
    if df.empty:
        return 20.0, "SIDE", (0.0, 1.0, 0.0)
    df = df.sort_values("date")
    vix_close = float(df["close"].iloc[-1])

    sw = max(1, int(getattr(cfg, "vix_smoothing_window", 5)))
    vix_smooth = float(
        pd.to_numeric(df["close"], errors="coerce")
        .dropna()
        .ewm(span=sw, min_periods=max(1, sw // 2))
        .mean()
        .iloc[-1]
    )

    blend_enabled = bool((blend_conf or {}).get("regime_blend_enabled", False))

    if blend_enabled:
        from regime_blend import apply_hysteresis, compute_blend_alphas
        bp = _build_blend_params(cfg, blend_conf)
        regime = apply_hysteresis(prev_regime, vix_smooth, **bp)
        alphas = compute_blend_alphas(vix_smooth, **bp)
    else:
        if vix_close < cfg.vix_bull_threshold:
            regime = "BULL"
        elif vix_close >= cfg.vix_defensive_threshold:
            regime = "DEFENSIVE"
        else:
            regime = "SIDE"
        if vix_close >= cfg.circuit_breaker_vix_threshold:
            regime = "CRASH"
        alphas = {"BULL": (1., 0., 0.), "SIDE": (0., 1., 0.),
                  "DEFENSIVE": (0., 0., 1.), "CRASH": (0., 0., 1.)}.get(regime, (0., 1., 0.))

    return vix_close, regime, alphas


def _build_blend_params(cfg, blend_conf: Optional[dict] = None) -> dict:
    """Construct blend parameter dict from cfg + blend_conf overlay."""
    return dict(
        bull_threshold=float(getattr(cfg, "vix_bull_threshold", 18.0)),
        def_threshold=float(getattr(cfg, "vix_defensive_threshold", 30.0)),
        bull_side_blend_width=float((blend_conf or {}).get("bull_side_blend_width", 2.0)),
        side_def_blend_width=float((blend_conf or {}).get("side_def_blend_width", 3.0)),
        cb_threshold=float(getattr(cfg, "circuit_breaker_vix_threshold", 35.0)),
        cb_enabled=bool(getattr(cfg, "enable_circuit_breaker", True)),
    )


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
    cfg, pack: dict, signal: dict, regime: str, return_meta: bool = False,
    blend_alphas: Optional[Tuple[float, float, float]] = None,
) -> pd.DataFrame | Tuple[pd.DataFrame, Dict[str, Any]]:
    """Compute stock scores for the latest valid date in pack."""
    import dataclasses
    mask = signal["mask"]
    wb = signal["wb"]
    ws = signal["ws"]
    wd = signal["wd"]

    dates = pack["dates"]
    tickers = pack["tickers"]
    N = len(tickers)

    sel = np.asarray(mask, dtype=bool)
    feat_valid = pack["feat_valid"]
    di = len(dates) - 1
    min_valid_tickers = max(100, N // 3)
    for candidate_di in range(len(dates) - 1, -1, -1):
        valid_count = int(np.all(feat_valid[sel, candidate_di, :], axis=0).sum())
        if valid_count >= min_valid_tickers:
            di = candidate_di
            break
    scoring_date = str(dates[di])[:10]
    print(f"  Using scoring date: {scoring_date} (di={di}, valid tickers at this date)")

    sel = np.asarray(mask, dtype=bool)
    print(f"  Signal mask: {sel.shape}, {int(sel.sum())} selected features out of {len(sel)}")

    cfg_live = dataclasses.replace(cfg)
    cfg_live.enable_completeness_history_filter = False

    active_w_bull = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "BULL")
    active_w_side = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "SIDE")
    active_w_def = engine.get_regime_active_weight_vector(cfg_live, sel, sel, sel, wb, ws, wd, "DEFENSIVE")

    score_regime = regime if regime != "CRASH" else "DEFENSIVE"

    use_blend = blend_alphas is not None and max(blend_alphas) < 1.0
    if use_blend:
        from regime_blend import blend_weight_vectors
        ab, as_, ad = blend_alphas
        w_blend = blend_weight_vectors(active_w_bull, active_w_side, active_w_def, blend_alphas)
        w_sum = float(np.sum(w_blend * sel.astype(np.float64)))
        print(f"  Blended score: α_bull={ab:.2f} α_side={as_:.2f} α_def={ad:.2f} | wsum={w_sum:.6f}")
        scores = engine._score_vector_for_regime(
            pack=pack, di=di, sel=sel,
            active_w_bull=w_blend,
            active_w_side=w_blend,
            active_w_def=w_blend,
            score_regime="SIDE",
            cfg=cfg_live,
        )
    else:
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
        empty_df = pd.DataFrame(columns=["Ticker", "Score", "Price"])
        scoring_meta = {
            "scoring_date": scoring_date,
            "scoring_index": int(di),
            "valid_ticker_count": int(np.all(feat_valid[sel, di, :], axis=0).sum()),
            "selected_factor_count": int(sel.sum()),
            "score_regime": score_regime,
            "ticker_count": int(N),
        }
        return (empty_df, scoring_meta) if return_meta else empty_df

    df = pd.DataFrame(rows).sort_values("Score", ascending=False).reset_index(drop=True)
    scoring_meta = {
        "scoring_date": scoring_date,
        "scoring_index": int(di),
        "valid_ticker_count": int(np.all(feat_valid[sel, di, :], axis=0).sum()),
        "selected_factor_count": int(sel.sum()),
        "score_regime": score_regime,
        "ticker_count": int(N),
    }
    return (df, scoring_meta) if return_meta else df


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
    # D1.4: per-row rank within today's scored universe (1-based).  Used
    # by ``apply_actions`` / ``apply_recommendations`` to seed
    # ``entry_rank`` on BUY_NEW.  ``-1`` = unranked (held ticker that
    # dropped out of top-N, TRIM, DEFERRED, etc.).
    "Rank",
    # v2.1 — profit_target tier carried to ``apply_partial_execution``
    # for ProfitTargetsHit persistence.  NaN for all other actions.
    "ProfitTier",
]


def generate_recommendations(
    cfg, scores_df: pd.DataFrame, regime: str,
    vix_close: float, holdings_mgr: HoldingsManager,
    total_capital: float, daily_buy_limit: float = 0.0,
    strategy_conf: Optional[Dict] = None,
    sim_date: Optional[str] = None,
    history: Optional[Any] = None,
    # D4.2 — risk-off gate inputs.  All optional for backward compat;
    # triggers that don't consult ``market.risk_off`` are indifferent.
    vix_series: Optional[List[float]] = None,
    recent_regimes: Optional[List[str]] = None,
    portfolio_peak: Optional[float] = None,
    # D4 diagnostics — if a list is supplied, each fired exit verdict
    # (excluding sell_grace/STOP_LOSS which are legacy-tracked) is
    # appended as a dict for offline analysis.  Does not affect portfolio
    # behaviour.  See simulator / run_lab for CSV dump wiring.
    trade_log: Optional[List[Dict]] = None,
    # Buy-grace blocked tickers — new-position buys are suppressed for
    # these tickers.  BUY_MORE on already-held names is unaffected.
    # Populated by run_daily's buy-grace logic; simulator handles its
    # own grace filtering internally.
    buy_grace_blocked: Optional[set] = None,
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
    grace_step1_days = strat.get("grace_step1_days", 0)
    grace_step1_sell_pct = strat.get("grace_step1_sell_pct", 0.5)
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

    # D1.4: rank over today's full scored universe (1-based, stable by
    # sort order of scores_df).  Stamped onto every recos row so
    # ``apply_actions`` / ``apply_recommendations`` can seed
    # ``entry_rank`` on BUY_NEW without re-deriving ranks from scores_df.
    rank_map: Dict[str, int] = {}
    if scores_df is not None and not scores_df.empty and "Ticker" in scores_df.columns:
        for idx, ticker in enumerate(scores_df["Ticker"].tolist(), start=1):
            rank_map[str(ticker)] = idx

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

    # --- 3b. Exit-trigger pipeline (Track D, D1 refactor) --------------------
    # Replaces the legacy hardcoded stop-loss + sell-grace branches.  The
    # pipeline is legacy-parity in stock config (build_triggers synthesises
    # StopLossTrigger / SellGraceTrigger from enable_stop_loss /
    # sell_grace_days / grace_step1_*) but also accepts the new
    # ``strategy.exit_triggers`` explicit list for future D2 dynamic
    # triggers.  Verdicts are consumed below in three places:
    #   (1) STOP_LOSS  → emitted immediately (block directly below),
    #   (2) SELL / SELL_GRACE / TRIM_GRACE → inside the classification
    #       loop's "not in_target and is_held" branch,
    #   (3) TRIM_GRACE partial_pct → in the TRIM_GRACE emission block.
    today = sim_date if sim_date else datetime.now().strftime("%Y-%m-%d")

    exit_triggers = build_triggers(strat)
    exit_verdicts: Dict[str, Any] = {}
    prev_grace: Dict[str, int] = {}
    same_day_rerun = False
    if exit_triggers:
        # Only pay for prev_recos I/O when a grace-state-consuming trigger
        # is present — matches legacy's ``if sell_grace_days > 0`` gating.
        needs_prev_recos = any(
            getattr(t, "name", None) == "sell_grace" for t in exit_triggers
        )
        prev_recos_for_triggers = (
            holdings_mgr.load_recommendations() if needs_prev_recos else None
        )
        holdings_store = getattr(holdings_mgr, "holdings", None)
        # D4.2 — resolve RiskOffAssessor from strategy_conf.risk_off (dict)
        # so arm configs can override thresholds without touching Python.
        ro_conf = strat.get("risk_off") if isinstance(strat, dict) else None
        ro_assessor = RiskOffAssessor.from_config(ro_conf) if ro_conf is not None else None

        exit_verdicts, prev_grace, same_day_rerun = evaluate_exits(
            triggers=exit_triggers,
            current=current,
            holdings_store=holdings_store,
            scores_df=scores_df,
            score_map=score_map,
            regime=regime,
            today=today,
            vix=float(vix_close),
            top_n=top_n,
            total_capital=total_capital,
            prev_recos=prev_recos_for_triggers,
            history=history,
            vix_series=vix_series,
            recent_regimes=recent_regimes,
            portfolio_peak=portfolio_peak,
            risk_off_assessor=ro_assessor,
        )

    # --- 4. Upstream terminal-verdict emission ------------------------
    # Every terminal verdict except ``sell_grace`` emits its recos row
    # *here*, before the target/held classification loop.  Rationale:
    # D2 triggers (peak_drawdown, score_decay, trend_break, rank_velocity,
    # relative_rebar, regime_switch) fire on held tickers **regardless of
    # top-N membership** — if routed through the classification loop they
    # would be dropped in the ``in_target`` branch.  ``sell_grace`` is the
    # single exception: its semantics are strictly rank-based (fires only
    # when out-of-target), so it keeps legacy classification-loop routing
    # and D1.5 byte-parity is preserved.
    #
    # STOP_LOSS is preserved bit-for-bit (GapPct=0, legacy row shape);
    # other triggers use modern SELL/TRIM shapes with computed GapPct.
    target_tickers = set(target_map.keys())
    held_tickers = set(actual_value.keys())

    recos = []
    upstream_handled: set = set()
    _UPSTREAM_EXCLUDED_TRIGGERS = {"sell_grace"}

    # STOP_LOSS first — preserve legacy row shape verbatim for D1.5 parity.
    # Must emit before any other D2 trigger so STOP_LOSS takes precedence
    # when two triggers both recommend SELL for the same ticker (the
    # pipeline's priority sort already picks one, but this ordering keeps
    # recos-row deterministic w.r.t. legacy).
    stop_loss_tickers = {
        t for t, v in exit_verdicts.items()
        if v.recos_action == RecosAction.STOP_LOSS and t in held_tickers
    }
    for ticker in stop_loss_tickers:
        recos.append({
            "Date": today, "Ticker": ticker, "Action": RecosAction.STOP_LOSS,
            "Score": score_map.get(ticker, 0),
            "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
            "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
            "GapPct": 0,
            "Price": held_price_map.get(ticker, 0),
            "Shares": held_shares_map.get(ticker, 0),
            "Capital": 0, "Regime": regime, "GraceCount": 0,
        })
        upstream_handled.add(ticker)

    # D2 terminal verdicts — any trigger that isn't stop_loss / sell_grace.
    # Side-effect for D4.3: persist fired profit-target tiers into
    # holdings_store so the trigger's tier-memory survives across days.
    _holdings_store = getattr(holdings_mgr, "holdings", None)
    for ticker, v in exit_verdicts.items():
        if ticker not in held_tickers or ticker in upstream_handled:
            continue
        if getattr(v, "trigger_name", "") in _UPSTREAM_EXCLUDED_TRIGGERS:
            continue
        if not v.is_terminal():
            continue
        if v.recos_action == RecosAction.STOP_LOSS:
            continue  # already handled above

        price = held_price_map.get(ticker, 0)
        shares = held_shares_map.get(ticker, 0)
        act_v = v.action  # "SELL" / "TRIM" / "WARN"

        # D4.3 — tier-memory persistence. Only writes on TRIM_PROFIT (SELL_PROFIT
        # fully closes the position, so the set is discarded anyway when the
        # ticker is removed from holdings_store).
        if (v.recos_action == RecosAction.TRIM_PROFIT
                and _holdings_store is not None
                and ticker in _holdings_store):
            tier = v.meta.get("profit_target_pct") if isinstance(v.meta, dict) else None
            if tier is not None:
                hit = _holdings_store[ticker].setdefault("profit_targets_hit", set())
                if isinstance(hit, (list, tuple)):
                    hit = set(float(x) for x in hit)
                    _holdings_store[ticker]["profit_targets_hit"] = hit
                hit.add(float(tier))

        # D4 trade-log diagnostics — capture verdict context for every
        # upstream-emitted non-STOP_LOSS terminal verdict.  Caller decides
        # whether to pass a list (None = disabled, no overhead).
        if trade_log is not None:
            _hold_entry = (
                _holdings_store.get(ticker)
                if _holdings_store is not None and ticker in _holdings_store
                else {}
            ) or {}
            entry_price = float(_hold_entry.get("entry_price", 0.0) or 0.0)
            days_held = int(_hold_entry.get("days_held", 0) or 0)
            pnl_pct = (
                (price / entry_price - 1.0) * 100.0
                if entry_price > 0 and price > 0 else 0.0
            )
            trade_log.append({
                "Date": today,
                "Ticker": ticker,
                "Regime": regime,
                "Trigger": getattr(v, "trigger_name", "") or "",
                "Action": v.recos_action or act_v,
                "Score": round(float(score_map.get(ticker, 0) or 0.0), 4),
                "EntryPrice": round(entry_price, 4),
                "Price": round(float(price), 4),
                "Shares": int(shares),
                "DaysHeld": days_held,
                "PnLPct": round(pnl_pct, 2),
                "PartialPct": round(float(v.partial_pct or 0.0), 4),
                "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
                "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
                "Meta": dict(v.meta) if isinstance(v.meta, dict) else {},
                "Reason": str(v.reason or "")[:400],
            })

        if act_v == "SELL":
            _tier = None
            if isinstance(getattr(v, "meta", None), dict):
                _tier = v.meta.get("profit_target_pct")
            recos.append({
                "Date": today, "Ticker": ticker,
                "Action": v.recos_action or RecosAction.SELL,
                "Score": score_map.get(ticker, 0),
                "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
                "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
                "GapPct": round(-actual_map.get(ticker, 0) * 100, 2),
                "Price": price, "Shares": shares,
                "Capital": 0, "Regime": regime,
                "GraceCount": int(v.grace_count or 0),
                "ProfitTier": float(_tier) if _tier is not None else np.nan,
            })
            upstream_handled.add(ticker)
        elif act_v == "TRIM":
            pct = max(0.0, min(1.0, float(v.partial_pct)))
            sell_shares = max(1, int(np.floor(shares * pct)))
            # v2.1 — surface profit_target tier so the Excel persistence
            # layer can record it against ProfitTargetsHit on execution.
            _tier = None
            if isinstance(getattr(v, "meta", None), dict):
                _tier = v.meta.get("profit_target_pct")
            recos.append({
                "Date": today, "Ticker": ticker,
                "Action": v.recos_action or RecosAction.TRIM,
                "Score": score_map.get(ticker, 0),
                "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
                "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
                "GapPct": round(-actual_map.get(ticker, 0) * pct * 100, 2),
                "Price": price, "Shares": sell_shares,
                "Capital": round(sell_shares * price, 2),
                "Regime": regime,
                "GraceCount": int(v.grace_count or 0),
                "ProfitTier": float(_tier) if _tier is not None else np.nan,
            })
            upstream_handled.add(ticker)
        elif act_v == "WARN":
            # WARN = record-only, no portfolio change.  Used by D2 WARN
            # verdicts (e.g. rank_velocity tier-1 warning).  sell_grace's
            # SELL_GRACE is NOT handled here (sell_grace routes through
            # classification loop by the excluded-triggers list above).
            recos.append({
                "Date": today, "Ticker": ticker,
                "Action": v.recos_action or "WARN",
                "Score": score_map.get(ticker, 0),
                "TargetPct": round(target_map.get(ticker, 0) * 100, 2),
                "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
                "GapPct": 0,
                "Price": price, "Shares": shares,
                "Capital": 0, "Regime": regime,
                "GraceCount": int(v.grace_count or 0),
            })
            upstream_handled.add(ticker)

    # --- 5. Classify remaining tickers ---
    all_tickers = target_tickers | held_tickers
    buy_items = []
    sell_items = []
    grace_items = []
    trim_grace_items = []
    trim_items = []
    hold_items = []
    grace_blocked_items = []  # tickers in top-N but buy-grace blocked (new-only)

    for ticker in all_tickers:
        if ticker in upstream_handled:
            continue

        target_w = target_map.get(ticker, 0.0)
        actual_w = actual_map.get(ticker, 0.0)
        gap_w = target_w - actual_w
        gap_dollar = gap_w * investable_capital

        in_target = ticker in target_tickers
        is_held = ticker in held_tickers

        if not in_target and is_held:
            # Decision comes from the trigger stack via ``exit_verdicts``
            # computed in step 3b above.  Legacy fallback: when no
            # grace-family trigger is configured (sell_grace_days = 0),
            # no verdict is produced → sell immediately, matching the
            # pre-D1 ``else: sell_items.append(ticker)`` branch.
            v = exit_verdicts.get(ticker)
            if v is not None:
                a = v.recos_action
                if a == "SELL":
                    sell_items.append(ticker)
                elif a == "TRIM_GRACE":
                    trim_grace_items.append((ticker, int(v.grace_count),
                                             float(v.partial_pct)))
                elif a == "SELL_GRACE":
                    grace_items.append((ticker, int(v.grace_count)))
                # Any other action here (STOP_LOSS handled upstream,
                # TRIM/HOLD not expected from grace-family triggers) is
                # ignored intentionally; ticker stays unclassified.
            else:
                sell_items.append(ticker)
        elif in_target and gap_w > gap_threshold:
            if (ticker not in held_tickers
                    and buy_grace_blocked
                    and ticker in buy_grace_blocked):
                grace_blocked_items.append((ticker, gap_dollar))
            else:
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

    # --- 6c. TRIM_GRACE: two-step grace partial sell ---
    # ``partial_pct`` now flows in from the verdict so that D2 triggers can
    # customise per-ticker trim fractions without touching this block.
    # In legacy-mode this equals ``strat["grace_step1_sell_pct"]`` for every
    # row, preserving pre-D1 behaviour bit-for-bit.
    for ticker, grace_count, partial_pct in trim_grace_items:
        total_shares = held_shares_map.get(ticker, 0)
        sell_shares = max(1, int(np.floor(total_shares * partial_pct)))
        price = held_price_map.get(ticker, 0)
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "TRIM_GRACE",
            "Score": score_map.get(ticker, 0),
            "TargetPct": 0, "ActualPct": round(actual_map.get(ticker, 0) * 100, 2),
            "GapPct": round(-actual_map.get(ticker, 0) * partial_pct * 100, 2),
            "Price": price,
            "Shares": sell_shares,
            "Capital": round(sell_shares * price, 2),
            "Regime": regime,
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

    # --- 8b. Buy-grace-blocked tickers → DEFERRED ---
    for ticker, gap_d in grace_blocked_items:
        target_w = target_map.get(ticker, 0)
        actual_w = actual_map.get(ticker, 0)
        recos.append({
            "Date": today, "Ticker": ticker, "Action": "DEFERRED",
            "Score": score_map.get(ticker, 0),
            "TargetPct": round(target_w * 100, 2),
            "ActualPct": round(actual_w * 100, 2),
            "GapPct": round((target_w - actual_w) * 100, 2),
            "Price": price_map.get(ticker, 0), "Shares": 0,
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
    # D1.4: stamp Rank column on every row from the once-computed
    # ``rank_map``.  Post-hoc rather than per-literal so the 8 row-
    # construction sites stay legacy-identical and any future row types
    # inherit the stamping for free.  Tickers not in today's scored
    # universe (e.g. held legacy ticker outside top-N) get Rank=-1.
    df["Rank"] = df["Ticker"].map(lambda t: rank_map.get(str(t), -1)).astype(int)
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
# Universe delta
# ─────────────────────────────────────────────

def _print_universe_delta(scores_df: pd.DataFrame, hm, cfg, regime: str) -> str:
    """Print and return top-N universe changes vs previous recommendations."""
    if regime == "BULL":
        top_n = cfg.regime_bull_top_n
    elif regime in ("DEFENSIVE", "CRASH"):
        top_n = cfg.regime_defensive_top_n
    else:
        top_n = cfg.regime_side_top_n

    today_ranked = scores_df.dropna(subset=["Price"]).reset_index(drop=True)
    today_top = set(today_ranked.head(top_n)["Ticker"].tolist())
    today_rank = {row["Ticker"]: i + 1 for i, row in today_ranked.iterrows()}

    today_str = datetime.now().strftime("%Y-%m-%d")
    prev_recos = hm.load_prev_day_recos(today_str=today_str)
    if prev_recos.empty or "Action" not in prev_recos.columns:
        # Fallback: if archive missing, try current Recommendations if it's from before today
        cur = hm.load_recommendations()
        if not cur.empty and "Date" in cur.columns:
            cur_date = str(cur["Date"].iloc[0])[:10]
            if cur_date < today_str:
                prev_recos = cur
    if prev_recos.empty or "Action" not in prev_recos.columns:
        msg = f"\n  [Universe] top-{top_n} ({regime}): {len(today_top)} tickers (no previous day data)"
        print(msg)
        return msg
    prev_date_str = str(prev_recos["Date"].iloc[0])[:10] if "Date" in prev_recos.columns else "?"
    if prev_date_str == today_str:
        msg = f"\n  [Universe] top-{top_n} ({regime}): {len(today_top)} tickers (no previous day data — first run today)"
        print(msg)
        return msg

    prev_regime = "?"
    if "Regime" in prev_recos.columns and not prev_recos["Regime"].dropna().empty:
        prev_regime = str(prev_recos["Regime"].dropna().iloc[0])

    in_top_actions = {"BUY_NEW", "BUY_MORE", "HOLD", "DEFERRED", "SELL_GRACE", "TRIM"}
    prev_in_top = prev_recos[prev_recos["Action"].isin(in_top_actions)]
    if "Score" in prev_in_top.columns:
        prev_sorted = prev_in_top.sort_values("Score", ascending=False).reset_index(drop=True)
        prev_rank = {row["Ticker"]: i + 1 for i, (_, row) in enumerate(prev_sorted.iterrows())}
    else:
        prev_rank = {}
    prev_top = set(prev_in_top["Ticker"].tolist()) if not prev_in_top.empty else set()

    get_in = today_top - prev_top
    remain = today_top & prev_top
    get_out = prev_top - today_top

    lines = []
    lines.append(f"[Universe Delta] top-{top_n} ({regime})  prev={prev_date_str} ({prev_regime})")
    if prev_regime not in ("?", "", regime):
        lines.append(
            f"[WARN] Regime changed ({prev_regime} → {regime}): prev-rank uses "
            f"{prev_regime} scoring weights while today uses {regime} — rank deltas "
            f"reflect regime switch, not genuine signal moves."
        )
    lines.append(f"GET_IN={len(get_in)}  REMAIN={len(remain)}  GET_OUT={len(get_out)}")

    if remain:
        lines.append(f"\n{'Ticker':>8s}  {'Prev':>5s}  {'Now':>5s}  {'Delta':>6s}  {'Score':>6s}")
        rows = []
        for t in remain:
            p_r = prev_rank.get(t, 0)
            t_r = today_rank.get(t, 0)
            delta = p_r - t_r if (p_r > 0 and t_r > 0) else 0
            score = float(today_ranked.loc[today_ranked["Ticker"] == t, "Score"].iloc[0]) \
                if not today_ranked[today_ranked["Ticker"] == t].empty else 0
            rows.append((t, p_r, t_r, delta, score))
        for t, p_r, t_r, delta, score in sorted(rows, key=lambda x: x[2]):
            d_str = f"+{delta}" if delta > 0 else str(delta)
            marker = " ▲" if delta > 0 else (" ▼" if delta < 0 else "  ")
            lines.append(f"{t:>8s}  {p_r:5d}  {t_r:5d}  {d_str:>5s}{marker}  {score:6.1f}")

    if get_in:
        lines.append(f"\nGET_IN (new):")
        for t in sorted(get_in, key=lambda x: today_rank.get(x, 999)):
            r = today_rank.get(t, 0)
            score = float(today_ranked.loc[today_ranked["Ticker"] == t, "Score"].iloc[0]) \
                if not today_ranked[today_ranked["Ticker"] == t].empty else 0
            lines.append(f"  {t:>8s}  rank={r:2d}  score={score:.1f}")

    if get_out:
        lines.append(f"\nGET_OUT (dropped):")
        for t in sorted(get_out, key=lambda x: prev_rank.get(x, 999)):
            p_r = prev_rank.get(t, 0)
            t_r = today_rank.get(t, 0)
            label = f"now #{t_r}" if t_r > 0 else "unranked"
            lines.append(f"  {t:>8s}  was #{p_r:2d} → {label}")

    text = "\n".join(lines)
    for line in lines:
        print(f"  {line}")
    return text


# ─────────────────────────────────────────────
# Shadow-run pass
# ─────────────────────────────────────────────

def _run_shadow_pass(
    *,
    conf: dict,
    shadow_conf: dict,
    cfg,
    pack: dict,
    regime: str,
    vix_close: float,
    blend_on: bool,
    blend_alphas,
    hm: HoldingsManager,
    live_scores: pd.DataFrame,
    live_recos: pd.DataFrame,
    total_capital: float,
    daily_limit: float,
    strat_conf: dict,
    run_timestamp: datetime,
) -> str:
    """Run shadow signal scoring + diff. Returns email text (empty on failure/skip)."""
    from datetime import date as _date
    try:
        shadow_path = shadow_conf.get("frozen_signal", "")
        label = shadow_conf.get("label", "shadow")
        start_date_str = shadow_conf.get("start_date", "")
        duration_days = int(shadow_conf.get("duration_days", 30))

        if not shadow_path or not os.path.exists(shadow_path):
            print(f"\n[Shadow] Signal file not found: {shadow_path} — skipped.")
            return ""

        today = _date.today()
        start_date = _date.fromisoformat(start_date_str) if start_date_str else today
        day_number = max(1, (today - start_date).days + 1)
        expired = day_number > duration_days

        if expired:
            print(f"\n[Shadow] Expired (day {day_number}/{duration_days}). "
                  f"Generating final report and disabling.")
            _shadow_auto_expire(conf, shadow_conf)
            return ""

        print(f"\n[Shadow] Pass — {label} (Day {day_number}/{duration_days})")
        print(f"  Signal: {os.path.basename(shadow_path)}")

        shadow_signal = load_frozen_signal(shadow_path)
        shadow_scores, _ = compute_today_scores(
            cfg, pack, shadow_signal, regime, return_meta=True,
            blend_alphas=blend_alphas if blend_on else None,
        )
        print(f"  Shadow scored {len(shadow_scores)} stocks. Top 5:")
        print(f"  {shadow_scores.head().to_string(index=False)}")

        shadow_recos = generate_recommendations(
            cfg, shadow_scores, regime, vix_close, hm, total_capital,
            daily_buy_limit=daily_limit,
            strategy_conf=strat_conf,
        )
        print(f"  Shadow recos: {len(shadow_recos)} rows")

        from shadow_diff import compare_recommendations, format_email_section, save_diff_artifact
        if regime == "BULL":
            shadow_top_n = getattr(cfg, "regime_bull_top_n", 20)
        elif regime in ("DEFENSIVE", "CRASH"):
            shadow_top_n = getattr(cfg, "regime_defensive_top_n", 10)
        else:
            shadow_top_n = getattr(cfg, "regime_side_top_n", 15)
        diff = compare_recommendations(
            live_scores, shadow_scores,
            live_recos, shadow_recos,
            label=label,
            day_number=day_number,
            duration_days=duration_days,
            top_n=shadow_top_n,
        )
        print(f"  Top-N overlap: {diff['topn_overlap_count']}/{diff['topn_union_count']} "
              f"({diff['topn_overlap_rate']:.0%}) | "
              f"Rank corr: {diff.get('rank_correlation', 'N/A')}")

        shadow_run_id = run_timestamp.strftime("%Y%m%d_%H%M%S") + "_shadow"
        shadow_run_dir = (
            Path(conf["paths"]["output_dir"]).expanduser()
            / "daily_runs" / shadow_run_id
        )

        from run_artifact import write_daily_run_artifact
        write_daily_run_artifact(
            run_dir=shadow_run_dir,
            run_id=shadow_run_id,
            run_timestamp=run_timestamp,
            dry_run=True,
            rebalance_mode="shadow",
            status="shadow",
            trigger_actionable=False,
            triggers=["SHADOW"],
            trigger_str=f"SHADOW:{label}",
            regime=regime,
            vix_close=vix_close,
            frozen_signal_path=shadow_path,
            signal_summary=shadow_signal.get("signal_summary"),
            config=conf,
            strategy_base=conf.get("strategy", {}),
            strategy_resolved=strat_conf,
            scores_df=shadow_scores,
            recos_df=shadow_recos,
            portfolio_before_df=pd.DataFrame(),
            portfolio_after_refresh_df=pd.DataFrame(),
            scoring_meta={},
            daily_buy_limit=daily_limit,
            holdings_value=0.0,
            cash_balance=0.0,
            total_capital=total_capital,
        )
        save_diff_artifact(shadow_run_dir, diff)
        print(f"  Shadow artifact saved: {shadow_run_dir}")

        email_text = ""
        if shadow_conf.get("include_in_email", True):
            email_text = format_email_section(diff)
        return email_text

    except Exception as e:
        print(f"\n[Shadow] ERROR (non-fatal): {type(e).__name__}: {e}")
        traceback.print_exc()
        return ""


def _run_shadow_ledger_update(
    *,
    conf: dict,
    shadow_conf: dict,
    config_path: str = None,
    expected_shadow_run_dir: Path = None,
) -> str:
    """Update stateful shadow ledger from daily/shadow artifacts.

    This is intentionally non-blocking: a ledger replay/reporting failure should
    not invalidate the daily recommendation artifact that was already produced.
    """
    if not shadow_conf.get("ledger_enabled", True):
        print("\n[Shadow Ledger] Skipped (shadow.ledger_enabled=false).")
        return ""

    if expected_shadow_run_dir is not None and not expected_shadow_run_dir.exists():
        print("\n[Shadow Ledger] Skipped (fresh shadow artifact not found).")
        return ""

    script_path = _THIS_DIR / "shadow_ledger.py"
    if not script_path.exists():
        print(f"\n[Shadow Ledger] Skipped (missing {script_path}).")
        return ""

    output_dir = Path(conf["paths"]["output_dir"]).expanduser()
    daily_runs_dir = output_dir / "daily_runs"
    output_root = output_dir / "shadow_ledgers"
    cfg_path = Path(config_path).expanduser() if config_path else (_THIS_DIR / "config.yaml")

    label = str(shadow_conf.get("label", "") or "")
    start_date = str(shadow_conf.get("start_date", "") or "")
    min_runs = str(int(shadow_conf.get("ledger_min_runs", 1) or 1))
    commission_bps = str(float(shadow_conf.get("ledger_commission_bps", 10.0)))
    slippage_bps = str(float(shadow_conf.get("ledger_slippage_bps", 5.0)))
    timeout_sec = int(shadow_conf.get("ledger_timeout_sec", 600) or 600)

    cmd = [
        sys.executable,
        str(script_path),
        "update-latest",
        "--config",
        str(cfg_path),
        "--daily-runs-dir",
        str(daily_runs_dir),
        "--output-root",
        str(output_root),
        "--min-runs",
        min_runs,
        "--commission-bps",
        commission_bps,
        "--slippage-bps",
        slippage_bps,
    ]
    if label:
        cmd.extend(["--shadow-label", label])
    if start_date:
        cmd.extend(["--start", start_date])

    print("\n[Shadow Ledger] Updating stateful baseline vs shadow ledger...")
    import subprocess
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_THIS_DIR.parent),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                print(f"  {line}")
        if proc.stderr.strip():
            print("  [Shadow Ledger stderr]")
            for line in proc.stderr.strip().splitlines():
                print(f"  {line}")
        if proc.returncode != 0:
            print(f"  [Shadow Ledger][WARN] update-latest exited rc={proc.returncode} (non-fatal).")
            return ""
        else:
            pointer_path = output_root / label / "latest_pointer.json"
            print(f"  [Shadow Ledger] Latest pointer: {pointer_path}")
            return _format_shadow_ledger_email(pointer_path)
    except subprocess.TimeoutExpired:
        print(f"  [Shadow Ledger][WARN] update-latest timed out after {timeout_sec}s (non-fatal).")
    except Exception as e:
        print(f"  [Shadow Ledger][WARN] update-latest failed: {type(e).__name__}: {e}")
    return ""


def _format_shadow_ledger_email(pointer_path: Path) -> str:
    """Build a compact email section from shadow ledger latest_pointer.json."""
    try:
        pointer = json.loads(Path(pointer_path).read_text(encoding="utf-8"))
        summary_path = pointer.get("latest_summary_json")
        summary = {}
        if summary_path and Path(summary_path).exists():
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))

        baseline = pointer.get("baseline", {}) or {}
        shadow = pointer.get("shadow", {}) or {}
        comp = pointer.get("comparison", {}) or {}
        label = pointer.get("shadow_label", "shadow")
        start = summary.get("start", "?")
        end = pointer.get("latest_date") or summary.get("end", "?")
        run_count = summary.get("run_count", "?")
        replay_dir = pointer.get("latest_replay_dir", "")

        return "\n".join([
            "",
            "[Stateful Shadow Ledger]",
            f"  Label   : {label}",
            f"  Window  : {start} -> {end} ({run_count} runs)",
            f"  Baseline: ${float(baseline.get('final_nav', 0.0)):,.2f} "
            f"({float(baseline.get('total_return_pct', 0.0)):+.4f}%)",
            f"  Shadow  : ${float(shadow.get('final_nav', 0.0)):,.2f} "
            f"({float(shadow.get('total_return_pct', 0.0)):+.4f}%)",
            f"  Delta   : ${float(comp.get('shadow_minus_baseline_final_nav', 0.0)):,.2f} | "
            f"{float(comp.get('shadow_minus_baseline_return_pp', 0.0)):+.4f} pp return | "
            f"{float(comp.get('shadow_minus_baseline_mdd_pp', 0.0)):+.4f} pp MDD",
            f"  Replay  : {replay_dir}",
        ])
    except Exception as e:
        return "\n".join([
            "",
            "[Stateful Shadow Ledger]",
            f"  Summary unavailable: {type(e).__name__}: {e}",
            f"  Pointer: {pointer_path}",
        ])


def _shadow_auto_expire(conf: dict, shadow_conf: dict) -> None:
    """Disable shadow in config.yaml and generate the expiry report."""
    try:
        from shadow_diff import generate_expiry_report
        report_path = generate_expiry_report(
            output_dir=conf["paths"]["output_dir"],
            label=shadow_conf.get("label", "shadow"),
            start_date=shadow_conf.get("start_date", ""),
            duration_days=int(shadow_conf.get("duration_days", 30)),
        )
        print(f"  Shadow expiry report saved: {report_path}")
    except Exception as e:
        print(f"  [Shadow] WARN: expiry report failed: {e}")

    try:
        config_path = _THIS_DIR / "config.yaml"
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8")
            if "shadow:" in text:
                idx = text.index("shadow:")
                chunk = text[idx:idx + 200]
                if "enabled: true" in chunk:
                    offset = idx + chunk.index("enabled: true")
                    updated = text[:offset] + "enabled: false" + text[offset + len("enabled: true"):]
                    config_path.write_text(updated, encoding="utf-8")
                    print(f"  Shadow auto-disabled in config.yaml")
    except Exception as e:
        print(f"  [Shadow] WARN: failed to auto-disable: {e}")


# ─────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────

def run_daily(dry_run: bool = False, force: bool = False, config_path: str = None):
    """Main daily execution flow."""
    conf = load_config(config_path)
    cfg = build_engine_cfg(conf)
    rebalance_mode = conf.get("strategy", {}).get("rebalance_mode", "daily")
    run_timestamp = datetime.now().astimezone()
    run_id, run_dir = create_run_context(
        conf["paths"]["output_dir"], run_timestamp, rebalance_mode, dry_run,
    )
    now = datetime.now()
    et_now = _us_eastern_now()
    print(f"\n{'='*60}")
    print(f"  Phase 3 Daily Runner — {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"  US Eastern         — {et_now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  Artifact Run ID    — {run_id}")
    print(f"{'='*60}")

    # 1. Load frozen signal
    fs_path = conf["paths"]["frozen_signal"]
    if not os.path.exists(fs_path):
        print(f"  [ERROR] Frozen signal not found: {fs_path}")
        return
    signal = load_frozen_signal(fs_path)
    # ── LIVE-SAFETY GUARD ────────────────────────────────────────────
    # Phase ML signals (signal_type='ml_external_scores') are *strictly
    # offline* artefacts produced by phase3/ml/run_ml_v1.py for
    # walk-forward research. They MUST never drive real recommendations
    # until a full live qualification has been performed.  Refuse to
    # proceed if such a signal is mis-pointed by the live config.
    if signal.get("signal_type") == "ml_external_scores":
        raise RuntimeError(
            f"REFUSED: live frozen_signal points at an ML research "
            f"artefact ({fs_path}). ML signals are evaluation-only "
            f"and must not drive run_daily(). Update conf['paths']"
            f"['frozen_signal'] to a GA-Linear signal."
        )
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

    # 3.5 Post-refresh quality gate
    print("\n[Step 2.5] Cache quality gate...")
    from cache_health import check_vix_health, check_ohlcv_staleness
    qg_vix = check_vix_health(cfg)
    qg_sample = check_ohlcv_staleness(cfg, tickers[:30] + ["SPY"])
    qg_stale = [t for t, r in qg_sample.items() if r["status"] in ("STALE", "CRITICAL")]
    qg_missing = [t for t, r in qg_sample.items() if r["status"] == "MISSING"]
    stale_pct = len(qg_stale) / max(len(qg_sample), 1)

    if qg_vix["status"] == "CRITICAL":
        print(f"  [CRITICAL] VIX data critically stale (last={qg_vix.get('latest_date','?')}, gap={qg_vix.get('gap_days','?')}d)")
        print(f"  Regime detection will be unreliable. Aborting.")
        if not dry_run and conf.get("email", {}).get("send_on_cache_error", True):
            try:
                from mailer import send_daily_email
                send_daily_email(conf, ["CACHE_CRITICAL"], pd.DataFrame(), 0.0, "UNKNOWN",
                                 HoldingsManager(conf["paths"]["holdings_log"]),
                                 {"overall_status": "CRITICAL", "vix": qg_vix})
            except Exception:
                pass
        return
    elif qg_vix["status"] != "OK":
        print(f"  [WARN] VIX cache stale (last={qg_vix.get('latest_date','?')}, gap={qg_vix.get('gap_days','?')}d)")

    if qg_missing:
        print(f"  [WARN] {len(qg_missing)} tickers MISSING in cache: {qg_missing[:5]}")
    if qg_stale:
        print(f"  [WARN] {len(qg_stale)}/{len(qg_sample)} sampled tickers stale: {qg_stale[:5]}")
    if stale_pct > 0.5:
        print(f"  [CRITICAL] >50% of sampled tickers stale ({stale_pct:.0%}). Recommendations may be unreliable.")

    spy_status = qg_sample.get("SPY", {}).get("status", "?")
    if spy_status not in ("OK",):
        print(f"  [WARN] SPY cache: {spy_status} — benchmark tracking may be affected")

    if not qg_stale and not qg_missing and qg_vix["status"] == "OK":
        print(f"  Cache quality: OK ({len(qg_sample)} tickers checked)")
    health["post_refresh_vix"] = qg_vix
    health["post_refresh_stale_pct"] = stale_pct

    # 4. Get current VIX and regime (with optional hysteresis + blend)
    print("\n[Step 3] VIX & Regime check...")
    blend_conf = conf.get("regime", {})
    hm_prev = HoldingsManager(conf["paths"]["holdings_log"])
    prev_regime = "SIDE"
    try:
        dl = hm_prev.load_daily_log()
        if not dl.empty and "regime" in dl.columns:
            prev_regime = str(dl.iloc[-1]["regime"])
    except Exception:
        pass
    vix_close, regime, blend_alphas = get_current_vix(cfg, prev_regime=prev_regime, blend_conf=blend_conf)
    blend_on = bool(blend_conf.get("regime_blend_enabled", False))
    if blend_on:
        ab, as_, ad = blend_alphas
        print(f"  VIX={vix_close:.2f}  Regime={regime} (hysteresis, prev={prev_regime})")
        print(f"  Blend: α_bull={ab:.2f} α_side={as_:.2f} α_def={ad:.2f}")
    else:
        print(f"  VIX={vix_close:.2f}  Regime={regime}")

    # 5. Rebalance mode & trigger check
    hm = HoldingsManager(conf["paths"]["holdings_log"])

    if rebalance_mode == "daily":
        print(f"\n[Step 4] Rebalance mode: DAILY (all recommendations are actionable)")
        triggers = ["DAILY"]
        trigger_str = "DAILY"
    else:
        print("\n[Step 4] Trigger check (event-driven mode)...")
        triggers = check_triggers(cfg, vix_close, regime, hm, pack=None, signal=signal, force=force)
        trigger_str = ", ".join(triggers) if triggers else "NONE"
        print(f"  Triggers: {trigger_str}")

    # 6. Compute scores & generate recommendations
    recos = pd.DataFrame()
    scores_df = pd.DataFrame(columns=["Ticker", "Score", "Price"])
    scoring_meta: Dict[str, Any] = {}
    current_before = pd.DataFrame()
    current_after_refresh = pd.DataFrame()
    strat_base: Dict[str, Any] = conf.get("strategy", {})
    strat_conf: Dict[str, Any] = {}
    holdings_value = 0.0
    cash_balance = 0.0
    total_capital = 0.0
    artifact_error = ""
    trigger_actionable = bool(triggers)
    daily_limit = 0.0
    universe_delta_text = ""

    pack = None  # set by step 5; needed by shadow pass

    print(f"\n[Step 5] Computing scores (regime={regime})...")
    import dataclasses
    cfg_for_pack = dataclasses.replace(cfg)
    _settled_date = _last_available_trading_date()
    _settled_dt = datetime(_settled_date.year, _settled_date.month, _settled_date.day)
    cfg_for_pack.start_panel_date = _settled_dt - timedelta(days=365)
    cfg_for_pack.end_date = _settled_dt
    print(f"  Panel window: {cfg_for_pack.start_panel_date.date()} → {_settled_date} "
          f"(settled; ET now={_us_eastern_now().strftime('%H:%M %Z')})")
    cfg_for_pack.enable_historical_universe = True
    cfg_for_pack.historical_universe_expand_tickers = True
    cfg_for_pack.enable_coverage_based_universe = True
    try:
        result = engine.prepare_inputs(cfg_for_pack)
        pack = result["pack"]
        scores_df, scoring_meta = compute_today_scores(
            cfg, pack, signal, regime, return_meta=True,
            blend_alphas=blend_alphas if blend_on else None,
        )
        print(f"  Scored {len(scores_df)} stocks. Top 5:")
        print(scores_df.head().to_string(index=False))

        price_map = dict(zip(scores_df["Ticker"], scores_df["Price"]))
        current_before = hm.load_current()
        if not current_before.empty:
            hm.update_current_prices(price_map)
            current_after_refresh = hm.load_current()
            print(f"\n  [Price Update] {len(current_after_refresh)} holdings refreshed:")
            for _, h in current_after_refresh.iterrows():
                pnl_pct = h.get("PnL_Pct", 0)
                print(f"    {h['Ticker']:6s}  buy=${h['BuyPrice']:.2f}  "
                      f"now=${h['CurrentPrice']:.2f}  PnL={pnl_pct:+.2f}%")
        else:
            current_after_refresh = current_before.copy()

        print(f"\n[Step 6] Generating recommendations (mode={rebalance_mode})...")

        initial_cash = conf["portfolio"].get("initial_cash", conf["portfolio"]["total_capital"])
        hm.initialize_cash(initial_cash)

        holdings_value = hm.get_portfolio_value()
        cash_balance = hm.get_cash_balance()

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

        # T-buy-grace — live parity with simulator (2026-04-25 fix).
        # phase3/simulator.py applies buy_grace_days via an in-memory
        # ``_top_n_history`` deque that is rebuilt fresh on every
        # ``run_simulation`` call — fine for backtests but invisible to
        # daily_runner, which doesn't go through the simulator.
        #
        # Pre-2026-05-01 behaviour filtered scores_df in-place, which
        # coupled scoring universe to portfolio holdings and caused
        # Paper/Real profiles to diverge on rankings, SELL_GRACE counts,
        # and Universe-Delta even though they use the same signal.
        #
        # Fixed: scores_df is never mutated.  Instead a
        # ``_buy_grace_blocked`` set is computed and forwarded to
        # ``generate_recommendations``, which suppresses BUY_NEW only
        # (BUY_MORE on existing holdings is unaffected).  Target-weight
        # calculations, SELL_GRACE verdicts, and Universe Delta now use
        # the full, profile-independent scored universe.
        _buy_grace_blocked: Optional[set] = None
        try:
            _bg_days = int(strat_conf.get("buy_grace_days", 0) or 0)
        except (TypeError, ValueError):
            _bg_days = 0
        if _bg_days > 0:
            from buy_grace_history import BuyGraceHistory  # local import — avoid circulars
            _bgh_path = os.path.join(
                os.path.dirname(conf["paths"]["holdings_log"]),
                "top_n_history.jsonl",
            )
            _bgh = BuyGraceHistory(_bgh_path)
            if regime == "BULL":
                _topn_today = int(getattr(cfg, "regime_bull_top_n", 20))
            elif regime in ("DEFENSIVE", "CRASH"):
                _topn_today = int(getattr(cfg, "regime_defensive_top_n", 10))
            else:
                _topn_today = int(getattr(cfg, "regime_side_top_n", 15))
            _prefilter_topn = scores_df["Ticker"].head(_topn_today).tolist()

            _today_str = now.strftime("%Y-%m-%d")
            _recent_sets = _bgh.get_recent_topn_sets(_bg_days + 1)
            if _bgh.latest_date() == _today_str and _recent_sets:
                _recent_sets = _recent_sets[:-1]
            _recent_sets = _recent_sets[-_bg_days:]
            if len(_recent_sets) >= _bg_days:
                _persistent: set = set.intersection(*_recent_sets)
                _all_scored = set(scores_df["Ticker"].tolist())
                _buy_grace_blocked = _all_scored - _persistent
                _n_blocked = len(_buy_grace_blocked)
                print(f"  [Buy-Grace] grace={_bg_days}d, history={len(_recent_sets)}d warmed | "
                      f"persistent={len(_persistent)}, blocked={_n_blocked} new-buy candidates "
                      f"(scores_df unchanged, {len(scores_df)} tickers)")
            else:
                print(f"  [Buy-Grace] grace={_bg_days}d, history={len(_recent_sets)}d "
                      f"(WARMUP — need {_bg_days}d, filter skipped this run)")

            try:
                _bgh.append(_today_str, str(regime), _prefilter_topn, _topn_today)
                print(f"  [Buy-Grace] snapshot appended → {os.path.basename(_bgh_path)}")
            except Exception as _e:  # never fail the run on snapshot write
                print(f"  [Buy-Grace] WARN: snapshot append failed: {_e}")

        recos = generate_recommendations(
            cfg, scores_df, regime, vix_close, hm, total_capital,
            daily_buy_limit=daily_limit,
            strategy_conf=strat_conf,
            buy_grace_blocked=_buy_grace_blocked,
        )

        # ── Universe Delta: compare today's top-N vs previous ──
        universe_delta_text = _print_universe_delta(scores_df, hm, cfg, regime)

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
        trim_grace_count = len(recos[recos["Action"] == "TRIM_GRACE"]) if not recos.empty and "Action" in recos.columns else 0
        if len(trims): parts.append(f"TRIM={len(trims)}")
        if trim_grace_count: parts.append(f"TRIM_GRACE={trim_grace_count}")
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
        trim_grace = _ne("TRIM_GRACE")
        if not trim_grace.empty:
            print(f"\n  [TRIM_GRACE — two-step grace partial sell]")
            for _, r in trim_grace.iterrows():
                step1 = conf.get("strategy", {}).get("grace_step1_days", 0)
                grace_end = conf.get("strategy", {}).get("sell_grace_days", 60)
                sell_pct = conf.get("strategy", {}).get("grace_step1_sell_pct", 0.5)
                print(f"    {r['Ticker']:6s}  sell {sell_pct:.0%} ({int(r['Shares'])} shares) @ ${r['Price']:.2f}  "
                      f"grace {int(r['GraceCount'])}/{grace_end}  (step1@{step1}d)")
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
            _def_display = deferred[["Ticker", "Score", "TargetPct", "GapPct", "Price"]].copy()
            _def_display["1sh"] = deferred["Price"].apply(lambda p: f"${p:,.0f}")
            print(_def_display.to_string(index=False))

    except Exception as e:
        artifact_error = f"{type(e).__name__}: {e}"
        print(f"  [ERROR] Score computation failed: {e}")
        traceback.print_exc()

    artifact_status = "awaiting_execution" if (not dry_run and trigger_actionable and artifact_error == "") else "generated"
    if artifact_error:
        artifact_status = "error"

    try:
        write_daily_run_artifact(
            run_dir=run_dir,
            run_id=run_id,
            run_timestamp=run_timestamp,
            dry_run=dry_run,
            rebalance_mode=rebalance_mode,
            status=artifact_status,
            trigger_actionable=trigger_actionable,
            triggers=triggers,
            trigger_str=trigger_str,
            regime=regime,
            vix_close=vix_close,
            frozen_signal_path=fs_path,
            signal_summary=sig_summary,
            config=conf,
            strategy_base=strat_base,
            strategy_resolved=strat_conf,
            scores_df=scores_df,
            recos_df=recos,
            portfolio_before_df=current_before,
            portfolio_after_refresh_df=current_after_refresh,
            scoring_meta=scoring_meta,
            daily_buy_limit=daily_limit,
            holdings_value=holdings_value,
            cash_balance=cash_balance,
            total_capital=total_capital,
            health=health,
            error=artifact_error,
        )
        print(f"\n[Artifact] Run snapshot saved: {run_dir}")
    except Exception as artifact_exc:
        print(f"\n[Artifact][WARN] failed to save run snapshot: {type(artifact_exc).__name__}: {artifact_exc}")

    # 7. Save recommendations
    if dry_run:
        print(f"\n[DRY RUN] No state changes applied.")
    else:
        if not recos.empty:
            hm.save_recommendations(recos)
            print(f"\n[Step 7] Recommendations saved (ACTIONABLE)")
            print(f"  -> Open T10 'Report Execution' to apply after you trade.")

        portfolio_value = hm.get_portfolio_value()
        log_cash = hm.get_cash_balance()
        log_total = portfolio_value + max(log_cash, 0.0)
        cash_pct = (log_cash / log_total * 100) if log_total > 0 else 0.0
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
            cash_balance=log_cash,
            total_capital=log_total,
            top_holding=top_holding,
        )
        print(f"  Daily log recorded. Total: ${log_total:,.2f} (holdings ${portfolio_value:,.2f} + cash ${log_cash:,.2f})")

    # 7.5 Shadow pass — score candidate signal, compare, save artifacts.
    shadow_email_text = ""
    shadow_conf = conf.get("shadow", {})
    if shadow_conf.get("enabled", False) and pack is not None and artifact_error == "":
        shadow_email_text = _run_shadow_pass(
            conf=conf,
            shadow_conf=shadow_conf,
            cfg=cfg,
            pack=pack,
            regime=regime,
            vix_close=vix_close,
            blend_on=blend_on,
            blend_alphas=blend_alphas,
            hm=hm,
            live_scores=scores_df,
            live_recos=recos,
            total_capital=total_capital,
            daily_limit=daily_limit,
            strat_conf=strat_conf,
            run_timestamp=run_timestamp,
        )
        expected_shadow_run_dir = (
            Path(conf["paths"]["output_dir"]).expanduser()
            / "daily_runs"
            / f"{run_timestamp.strftime('%Y%m%d_%H%M%S')}_shadow"
        )
        shadow_ledger_text = _run_shadow_ledger_update(
            conf=conf,
            shadow_conf=shadow_conf,
            config_path=config_path,
            expected_shadow_run_dir=expected_shadow_run_dir,
        )
        if shadow_ledger_text:
            shadow_email_text = (shadow_email_text + "\n" + shadow_ledger_text).strip()

    # 8. Send email
    if not dry_run and conf.get("email", {}).get("enabled", False):
        try:
            from mailer import send_daily_email
            send_daily_email(conf, triggers, recos, vix_close, regime, hm, health,
                             computed_daily_limit=daily_limit,
                             universe_delta_text=universe_delta_text,
                             shadow_text=shadow_email_text)
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
    parser.add_argument("--force-rebalance", action="store_true", help="Force trigger (used in event-driven mode)")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()
    run_daily(dry_run=args.dry_run, force=args.force_rebalance, config_path=args.config)
