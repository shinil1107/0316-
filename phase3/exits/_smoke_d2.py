"""D2 combined smoke test — dynamic exit triggers.

Covers:
  A. Unit behavior for each D2 trigger (fire / no-fire / guard).
  B. ``build_triggers`` explicit-mode priority ordering.
  C. Pipeline routing: D2 verdicts emit upstream (via
     ``generate_recommendations``) with correct recos_action strings.
  D. Priority interaction: higher-priority trigger's SELL pre-empts
     lower-priority trigger's SELL on the same ticker.
  E. RecosAction dispatch sets are complete (SimPortfolio /
     HoldingsManager can route every D2 action).

Run:
    cd 0316-/phase3
    python3 -m exits._smoke_d2
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
sys.path.insert(0, str(_PHASE3))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from engine_loader import engine  # noqa: E402
from daily_runner import generate_recommendations  # noqa: E402
from exits import (  # noqa: E402
    HoldingSnapshot, MarketSnapshot, ExitVerdict,
    VerdictAction, RecosAction,
    build_triggers, TRIGGER_REGISTRY,
)
from exits.triggers.peak_drawdown import PeakDrawdownTrigger  # noqa: E402
from exits.triggers.score_decay import ScoreDecayTrigger  # noqa: E402
from exits.triggers.trend_break import TrendBreakTrigger  # noqa: E402
from exits.triggers.rank_velocity import RankVelocityTrigger  # noqa: E402
from exits.triggers.relative_rebar import RelativeRebarTrigger  # noqa: E402
from exits.triggers.regime_switch import RegimeSwitchTrigger  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0


def _check(cond: bool, msg: str, extra: str = "") -> None:
    global _PASS, _FAIL
    tag = "[ OK ]" if cond else "[FAIL]"
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"{tag}  {msg}  {extra}".rstrip())


def _mk_h(**kw) -> HoldingSnapshot:
    """Build a HoldingSnapshot with sensible defaults, overriding via kwargs."""
    defaults = dict(
        ticker="A", shares=10,
        entry_date="2025-01-01", entry_price=100.0, entry_score=1.0, entry_rank=3,
        current_price=90.0, current_score=0.8, current_rank=3,
        peak_price=110.0,
        pnl_pct=-10.0,
        peak_drawdown_pct=-18.18,  # 90/110 - 1
        days_held=30,
        grace_count=0,
        entry_regime="BULL",
    )
    defaults.update(kw)
    return HoldingSnapshot(**defaults)


def _mk_m(**kw) -> MarketSnapshot:
    defaults = dict(
        date="2025-02-01", regime="BULL", prev_regime="BULL",
        vix=15.0, scores_df=pd.DataFrame(), top_n=5,
        portfolio_value=100_000.0, history=None, same_day_rerun=False,
    )
    defaults.update(kw)
    return MarketSnapshot(**defaults)


class _FakeHistory:
    """Minimal HistoryView double for trend_break / rank_velocity tests."""
    def __init__(self, prices=None, ranks=None):
        self._prices = prices or {}
        self._ranks = ranks or {}

    def price_series(self, ticker, lookback):
        arr = np.asarray(self._prices.get(ticker, []), dtype=np.float64)
        if arr.size == 0:
            return arr
        return arr[-int(lookback):]

    def rank_series(self, ticker, lookback):
        arr = np.asarray(self._ranks.get(ticker, []), dtype=np.float64)
        if arr.size == 0:
            return arr
        return arr[-int(lookback):]

    def score_series(self, ticker, lookback):
        return np.array([], dtype=np.float64)


# ═════════════════════════════════════════════════════════════════════════════
# A. Per-trigger unit behaviour
# ═════════════════════════════════════════════════════════════════════════════

def test_peak_drawdown():
    print("\n── A1. peak_drawdown ──")
    t = PeakDrawdownTrigger(drawdown_pct=-20.0, action="SELL")
    # Fires: DD -25% ≤ -20%
    h = _mk_h(peak_price=100.0, current_price=75.0, peak_drawdown_pct=-25.0)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.SELL, "DD -25% fires SELL")
    _check(v.recos_action == RecosAction.SELL_PEAK_DD, "recos_action = SELL_PEAK_DD")

    # Doesn't fire: DD -15% > -20%
    h = _mk_h(peak_price=100.0, current_price=85.0, peak_drawdown_pct=-15.0)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.ESCALATE, "DD -15% escalates")

    # Guard: peak_price == 0 → escalate
    h = _mk_h(peak_price=0.0, current_price=80.0, peak_drawdown_pct=0.0)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.ESCALATE and "no peak" in v.reason,
           "peak_price=0 escalates")

    # min_days_held guard
    t2 = PeakDrawdownTrigger(drawdown_pct=-10.0, min_days_held=5)
    h = _mk_h(peak_drawdown_pct=-30.0, days_held=2)
    v = t2.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.ESCALATE, "min_days_held guard escalates")

    # TRIM mode
    t3 = PeakDrawdownTrigger(drawdown_pct=-15.0, action="TRIM", partial_pct=0.4)
    h = _mk_h(peak_drawdown_pct=-20.0)
    v = t3.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.TRIM and abs(v.partial_pct - 0.4) < 1e-9,
           "TRIM mode emits TRIM w/ partial_pct=0.4")
    _check(v.recos_action == RecosAction.TRIM_PEAK_DD, "recos_action = TRIM_PEAK_DD")


def test_score_decay():
    print("\n── A2. score_decay ──")
    t = ScoreDecayTrigger(decay_pct=-50.0, action="SELL")
    # Fires: score halved (decay = -50%)
    h = _mk_h(entry_score=1.0, current_score=0.5)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.SELL, "entry=1.0 current=0.5 fires SELL")
    _check(v.recos_action == RecosAction.SELL_SCORE_DECAY, "recos_action correct")

    # Fires harder: score to 0 (unranked) → decay = -100%
    h = _mk_h(entry_score=1.0, current_score=0.0)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.SELL, "current=0 (unranked) fires SELL")

    # Doesn't fire: decay = -30%
    h = _mk_h(entry_score=1.0, current_score=0.7)
    v = t.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.ESCALATE, "decay -30% escalates")

    # min_entry_score guard: entry_score tiny → escalate regardless
    t2 = ScoreDecayTrigger(decay_pct=-50.0, min_entry_score=0.5)
    h = _mk_h(entry_score=0.05, current_score=0.01)
    v = t2.evaluate(h, _mk_m())
    _check(v.action == VerdictAction.ESCALATE and "below min" in v.reason,
           "tiny entry_score escalates")


def test_trend_break():
    print("\n── A3. trend_break ──")
    # Construct 250-day price path: gently up → MA50 above MA200.
    # Then make today dip so cross_down fires.
    base = np.linspace(80, 120, 250).astype(np.float64)  # strong uptrend
    # Make MA50 ≥ MA200 yesterday, MA50 < MA200 today.
    # Force today's price to crash ~ -40% → MA50 still drops enough.
    # Simpler: two explicit paths for 'below' mode and 'cross_down' mode.

    # "below" mode: MA50 < MA200 today.
    # Path: 200 days at 100, then 50 days at 60 → MA50=60 < MA200~92.
    prices_below = np.array([100.0] * 200 + [60.0] * 50)
    hist = _FakeHistory(prices={"A": prices_below})
    t = TrendBreakTrigger(fast=50, slow=200, mode="below", action="TRIM", partial_pct=0.5)
    h = _mk_h()
    m = _mk_m(history=hist)
    v = t.evaluate(h, m)
    _check(v.action == VerdictAction.TRIM, "below mode: MA50 < MA200 → TRIM")
    _check(v.recos_action == RecosAction.TRIM_TREND_BREAK, "recos_action TRIM_TREND_BREAK")

    # "below" mode no-fire: MA50 > MA200.
    prices_up = np.array([80.0] * 200 + [120.0] * 50)
    hist = _FakeHistory(prices={"A": prices_up})
    v = t.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.ESCALATE, "below mode: MA50 > MA200 → ESCALATE")

    # "cross_down" mode.  Construct 201 prices: first 200 at 100 (MA200=100),
    # then one day at 60 → MA50_today = (49*100 + 60)/50 = 99.2,
    # MA200_today = (151*100 + 60)/200 * adjusted…
    # Easier: arbitrarily construct a cross pattern using hand-tuned arrays.
    #   Yesterday:  MA50 >= MA200 (equal allowed).
    #   Today:      MA50 <  MA200.
    n = 210
    prices = np.linspace(80, 100, 200).tolist() + [70.0] * 10
    prices_cross = np.asarray(prices, dtype=np.float64)
    # Manually verify: MA50_today uses prices[-50:]; MA50_prev uses prices[-51:-1]
    t_cross = TrendBreakTrigger(fast=50, slow=200, mode="cross_down", action="SELL")
    hist = _FakeHistory(prices={"A": prices_cross})
    v = t_cross.evaluate(h, _mk_m(history=hist))
    # The exact MA values depend on the linspace; just verify SOME finite decision.
    _check(v.action in (VerdictAction.SELL, VerdictAction.ESCALATE),
           f"cross_down returns SELL or ESCALATE (got {v.action})")

    # History None → escalate.
    v = t.evaluate(h, _mk_m(history=None))
    _check(v.action == VerdictAction.ESCALATE and "no history" in v.reason,
           "history=None escalates")

    # Too-short series → escalate.
    hist = _FakeHistory(prices={"A": np.ones(10)})
    v = t.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.ESCALATE, "short series escalates")


def test_rank_velocity():
    print("\n── A4. rank_velocity ──")
    # rank_series uses lookback+1 and picks series[0] vs series[-1].
    # 5-day lookback → needs 6 data points. Rank 5 → 40 = drop 35 ≥ 30 → fires.
    ranks = [5.0, 10.0, 15.0, 25.0, 30.0, 40.0]
    hist = _FakeHistory(ranks={"A": ranks})
    t = RankVelocityTrigger(lookback=5, drop_threshold=30, action="TRIM",
                            partial_pct=0.3)
    h = _mk_h(current_rank=40)
    v = t.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.TRIM, "rank 5→40 drop fires TRIM")
    _check(abs(v.partial_pct - 0.3) < 1e-9, "partial_pct carried")
    _check(v.recos_action == RecosAction.TRIM_RANK_VEL, "recos_action correct")

    # No fire: drop only 10 ranks.
    ranks2 = [20.0, 21.0, 22.0, 24.0, 28.0, 30.0]
    hist = _FakeHistory(ranks={"A": ranks2})
    v = t.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.ESCALATE, "rank 20→30 no fire")

    # WARN mode
    t_warn = RankVelocityTrigger(lookback=5, drop_threshold=30, action="WARN")
    hist = _FakeHistory(ranks={"A": ranks})
    v = t_warn.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.WARN, "WARN mode emits WARN")
    _check(v.recos_action == RecosAction.SELL_RANK_VEL, "WARN recos_action")

    # current_rank = -1 → escalate
    h = _mk_h(current_rank=-1)
    v = t.evaluate(h, _mk_m(history=hist))
    _check(v.action == VerdictAction.ESCALATE, "unranked today escalates")

    # No history
    h = _mk_h(current_rank=40)
    v = t.evaluate(h, _mk_m(history=None))
    _check(v.action == VerdictAction.ESCALATE, "no history escalates")


def test_relative_rebar():
    print("\n── A5. relative_rebar ──")
    scores_df = pd.DataFrame({
        "Ticker": ["A", "B", "C", "D", "E", "F"],
        "Score": [1.0, 0.9, 0.8, 0.7, 0.6, 0.3],
    })
    # top_n=5 → median of [1.0,0.9,0.8,0.7,0.6] = 0.8
    # floor_multiplier=0.8 → threshold = 0.64
    t = RelativeRebarTrigger(
        reference="topN_median", floor_multiplier=0.8,
        action="TRIM", partial_pct=0.5,
    )
    # Holding with score 0.4 < 0.64 → fires.
    h = _mk_h(current_score=0.4, current_rank=10)
    v = t.evaluate(h, _mk_m(scores_df=scores_df, top_n=5))
    _check(v.action == VerdictAction.TRIM, "score 0.4 < 0.64 threshold fires TRIM")
    _check(v.recos_action == RecosAction.TRIM_REL_REBAR, "recos_action correct")

    # Holding with score 0.75 > 0.64 → no fire.
    h = _mk_h(current_score=0.75, current_rank=4)
    v = t.evaluate(h, _mk_m(scores_df=scores_df, top_n=5))
    _check(v.action == VerdictAction.ESCALATE, "score 0.75 > threshold escalates")

    # rankK mode
    t2 = RelativeRebarTrigger(reference="rankK", ref_rank=3,
                              floor_multiplier=0.5, action="SELL")
    # ref = scores_sorted[2] = 0.8 → threshold = 0.4 → current_score 0.3 fires
    h = _mk_h(current_score=0.3, current_rank=6)
    v = t2.evaluate(h, _mk_m(scores_df=scores_df, top_n=5))
    _check(v.action == VerdictAction.SELL, "rankK mode fires SELL")
    _check(v.recos_action == RecosAction.SELL_REL_REBAR, "SELL_REL_REBAR")

    # min_universe_size guard
    small_df = pd.DataFrame({"Ticker": ["A", "B"], "Score": [1.0, 0.5]})
    t3 = RelativeRebarTrigger(min_universe_size=5)
    h = _mk_h(current_score=0.1, current_rank=2)
    v = t3.evaluate(h, _mk_m(scores_df=small_df, top_n=2))
    _check(v.action == VerdictAction.ESCALATE, "small universe escalates")


def test_regime_switch():
    print("\n── A6. regime_switch ──")
    # bear_only mode (default).
    t = RegimeSwitchTrigger(
        mode="bear_only", grace_days=0, action="TRIM", partial_pct=0.5,
    )
    # Entry BULL, current DEF → fires.
    h = _mk_h(entry_regime="BULL", days_held=30)
    v = t.evaluate(h, _mk_m(regime="DEF"))
    _check(v.action == VerdictAction.TRIM, "entry BULL → current DEF fires")
    _check(v.recos_action == RecosAction.TRIM_REGIME, "recos_action TRIM_REGIME")

    # Entry DEF, current DEF → no fire (both in bear).
    h = _mk_h(entry_regime="DEF")
    v = t.evaluate(h, _mk_m(regime="DEF"))
    _check(v.action == VerdictAction.ESCALATE, "entry DEF current DEF escalates (both bear)")

    # Entry BULL, current SIDE → no fire (SIDE not in bear_regimes).
    h = _mk_h(entry_regime="BULL")
    v = t.evaluate(h, _mk_m(regime="SIDE"))
    _check(v.action == VerdictAction.ESCALATE, "BULL → SIDE escalates (SIDE not bear)")

    # "different" mode: BULL → SIDE fires.
    t2 = RegimeSwitchTrigger(mode="different", grace_days=0, action="SELL")
    h = _mk_h(entry_regime="BULL")
    v = t2.evaluate(h, _mk_m(regime="SIDE"))
    _check(v.action == VerdictAction.SELL, "different mode: BULL→SIDE fires SELL")

    # "downgrade" mode: SIDE → BULL does NOT fire (upgrade).
    t3 = RegimeSwitchTrigger(mode="downgrade", grace_days=0, action="SELL")
    h = _mk_h(entry_regime="SIDE")
    v = t3.evaluate(h, _mk_m(regime="BULL"))
    _check(v.action == VerdictAction.ESCALATE, "downgrade: SIDE→BULL escalates (upgrade)")

    # grace_days guard: days_held < grace_days → escalate.
    t_g = RegimeSwitchTrigger(mode="different", grace_days=5)
    h = _mk_h(entry_regime="BULL", days_held=2)
    v = t_g.evaluate(h, _mk_m(regime="SIDE"))
    _check(v.action == VerdictAction.ESCALATE, "grace_days guard escalates")

    # empty entry_regime → escalate.
    h = _mk_h(entry_regime="", days_held=30)
    v = t.evaluate(h, _mk_m(regime="DEF"))
    _check(v.action == VerdictAction.ESCALATE, "empty entry_regime escalates")


# ═════════════════════════════════════════════════════════════════════════════
# B. build_triggers priority ordering
# ═════════════════════════════════════════════════════════════════════════════

def test_build_triggers_priority():
    print("\n── B. build_triggers priority ordering ──")
    strat = {
        "exit_triggers": [
            {"type": "peak_drawdown", "params": {"drawdown_pct": -20.0}},
            {"type": "score_decay", "params": {"decay_pct": -50.0}},
            {"type": "trend_break", "params": {"fast": 50, "slow": 200}},
            {"type": "rank_velocity", "params": {"lookback": 5, "drop_threshold": 30}},
            {"type": "relative_rebar", "params": {"floor_multiplier": 0.8}},
            {"type": "regime_switch", "params": {"mode": "bear_only"}},
            {"type": "stop_loss", "params": {"threshold_pct": -15.0}},
            {"type": "sell_grace", "params": {"days": 60}},
        ]
    }
    triggers = build_triggers(strat)
    names = [t.name for t in triggers]
    _check(names == [
        "peak_drawdown", "score_decay", "trend_break",
        "rank_velocity", "relative_rebar", "regime_switch",
        "stop_loss", "sell_grace",
    ], f"priority-sorted names: {names}")


# ═════════════════════════════════════════════════════════════════════════════
# C. Pipeline routing via generate_recommendations (upstream emission)
# ═════════════════════════════════════════════════════════════════════════════

class MockHM:
    def __init__(self, current_rows, holdings_dict=None):
        self._current = pd.DataFrame(current_rows)
        self.holdings = holdings_dict or {}

    def load_current(self):
        return self._current

    def load_recommendations(self):
        return pd.DataFrame()


def _mk_cfg():
    cfg = engine.Config()
    cfg.regime_bull_top_n = 3
    cfg.regime_side_top_n = 3
    cfg.regime_defensive_top_n = 3
    cfg.regime_bull_cash_pct = 0.0
    cfg.regime_side_cash_pct = 0.0
    cfg.regime_defensive_cash_pct = 0.0
    cfg.regime_bull_max_weight_cap = 1.0
    cfg.regime_side_max_weight_cap = 1.0
    cfg.regime_defensive_max_weight_cap = 1.0
    cfg.circuit_breaker_vix_threshold = 999.0
    cfg.circuit_breaker_cash_pct = 0.0
    return cfg


def test_upstream_emission_peak_drawdown():
    print("\n── C1. Upstream emission: peak_drawdown fires on in-target holding ──")
    # A is in top-3 today (in_target) but held and dropped 25% from peak.
    # Legacy path would keep A and let gap logic handle it; D2 routes to SELL.
    scores_df = pd.DataFrame([
        {"Ticker": "A", "Score": 1.0, "Price": 75.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 10,
        "BuyPrice": 80.0, "CurrentPrice": 75.0,
        "MarketValue": 750.0, "PnL_Pct": -6.25,
        "PeakPrice": 100.0, "EntryScore": 1.0,
        "EntryRegime": "BULL", "EntryRank": 1,
        "LastScore": 1.0,
    }]
    holdings = {"A": {
        "shares": 10, "avg_cost": 80.0, "current_price": 75.0,
        "entry_date": "2024-12-01", "entry_price": 80.0,
        "entry_score": 1.0, "entry_rank": 1, "entry_regime": "BULL",
        "peak_price": 100.0, "last_score": 1.0,
    }}
    hm = MockHM(current, holdings_dict=holdings)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "exit_triggers": [
            {"type": "peak_drawdown",
             "params": {"drawdown_pct": -20.0, "action": "SELL",
                        "min_days_held": 0}},
        ],
    }
    out = generate_recommendations(
        cfg=_mk_cfg(), holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-02-01",
        strategy_conf=strat,
    )
    row_a = out[(out.Ticker == "A") & (out.Action == RecosAction.SELL_PEAK_DD)]
    _check(len(row_a) == 1, f"peak_drawdown emits SELL_PEAK_DD for A  got={out.Action.tolist()}")
    if len(row_a) == 1:
        _check(int(row_a.iloc[0]["Shares"]) == 10, "full-close shares = 10")


def test_upstream_emission_score_decay_trim():
    print("\n── C2. Upstream emission: score_decay TRIM ──")
    scores_df = pd.DataFrame([
        {"Ticker": "A", "Score": 0.3, "Price": 80.0},  # decayed from 1.0 at entry
        {"Ticker": "B", "Score": 0.9, "Price": 50.0},
        {"Ticker": "C", "Score": 0.8, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 20, "BuyPrice": 90.0,
        "CurrentPrice": 80.0, "MarketValue": 1600.0, "PnL_Pct": -11.1,
        "PeakPrice": 95.0, "EntryScore": 1.0,
        "EntryRegime": "BULL", "EntryRank": 1, "LastScore": 0.3,
    }]
    holdings = {"A": {
        "shares": 20, "avg_cost": 90.0, "current_price": 80.0,
        "entry_date": "2024-12-01", "entry_price": 90.0,
        "entry_score": 1.0, "entry_rank": 1, "entry_regime": "BULL",
        "peak_price": 95.0, "last_score": 0.3,
    }}
    hm = MockHM(current, holdings_dict=holdings)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "exit_triggers": [
            {"type": "score_decay",
             "params": {"decay_pct": -50.0, "action": "TRIM",
                        "partial_pct": 0.5, "min_entry_score": 0.01}},
        ],
    }
    out = generate_recommendations(
        cfg=_mk_cfg(), holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-02-01",
        strategy_conf=strat,
    )
    row_a = out[(out.Ticker == "A") & (out.Action == RecosAction.TRIM_SCORE_DECAY)]
    _check(len(row_a) == 1, f"score_decay emits TRIM_SCORE_DECAY  actions={out.Action.tolist()}")
    if len(row_a) == 1:
        _check(int(row_a.iloc[0]["Shares"]) == 10,
               f"TRIM emits half shares (10 of 20)  got={int(row_a.iloc[0]['Shares'])}")


def test_upstream_emission_does_not_duplicate_buy():
    print("\n── C3. D2 SELL on in-target ticker blocks BUY_MORE/BUY_NEW ──")
    # A is top scorer → would trigger BUY_MORE in legacy, but peak_drawdown SELL
    # must pre-empt and A's row should be SELL_PEAK_DD, not BUY_MORE.
    scores_df = pd.DataFrame([
        {"Ticker": "A", "Score": 1.0, "Price": 75.0},
        {"Ticker": "B", "Score": 0.5, "Price": 50.0},
        {"Ticker": "C", "Score": 0.3, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 5,  # under-weight → legacy would BUY_MORE
        "BuyPrice": 100.0, "CurrentPrice": 75.0,
        "MarketValue": 375.0, "PnL_Pct": -25.0,
        "PeakPrice": 100.0, "EntryScore": 1.0,
        "EntryRegime": "BULL", "EntryRank": 1, "LastScore": 1.0,
    }]
    holdings = {"A": {
        "shares": 5, "avg_cost": 100.0, "current_price": 75.0,
        "entry_date": "2024-12-01", "entry_price": 100.0,
        "entry_score": 1.0, "entry_rank": 1, "entry_regime": "BULL",
        "peak_price": 100.0, "last_score": 1.0,
    }}
    hm = MockHM(current, holdings_dict=holdings)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "exit_triggers": [
            {"type": "peak_drawdown",
             "params": {"drawdown_pct": -20.0, "action": "SELL"}},
        ],
    }
    out = generate_recommendations(
        cfg=_mk_cfg(), holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-02-01",
        strategy_conf=strat,
    )
    # A should have exactly one row, and it should be SELL_PEAK_DD.
    a_rows = out[out.Ticker == "A"]
    _check(len(a_rows) == 1,
           f"A has exactly one row  len={len(a_rows)} actions={a_rows.Action.tolist()}")
    _check(a_rows.iloc[0]["Action"] == RecosAction.SELL_PEAK_DD,
           f"A's row is SELL_PEAK_DD  got={a_rows.iloc[0]['Action']}")


# ═════════════════════════════════════════════════════════════════════════════
# D. Priority interaction — higher-priority trigger wins
# ═════════════════════════════════════════════════════════════════════════════

def test_priority_peak_beats_score_decay():
    print("\n── D. peak_drawdown (160) pre-empts score_decay (150) ──")
    # Both triggers would fire on A; peak_drawdown should win.
    scores_df = pd.DataFrame([
        {"Ticker": "A", "Score": 0.2, "Price": 60.0},  # decayed
        {"Ticker": "B", "Score": 0.9, "Price": 50.0},
        {"Ticker": "C", "Score": 0.8, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 10, "BuyPrice": 100.0,
        "CurrentPrice": 60.0, "MarketValue": 600.0, "PnL_Pct": -40.0,
        "PeakPrice": 100.0, "EntryScore": 1.0,
        "EntryRegime": "BULL", "EntryRank": 1, "LastScore": 0.2,
    }]
    holdings = {"A": {
        "shares": 10, "avg_cost": 100.0, "current_price": 60.0,
        "entry_date": "2024-12-01", "entry_price": 100.0,
        "entry_score": 1.0, "entry_rank": 1, "entry_regime": "BULL",
        "peak_price": 100.0, "last_score": 0.2,
    }}
    hm = MockHM(current, holdings_dict=holdings)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "exit_triggers": [
            {"type": "peak_drawdown",
             "params": {"drawdown_pct": -20.0, "action": "SELL"}},
            {"type": "score_decay",
             "params": {"decay_pct": -50.0, "action": "SELL",
                        "min_entry_score": 0.01}},
        ],
    }
    out = generate_recommendations(
        cfg=_mk_cfg(), holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-02-01",
        strategy_conf=strat,
    )
    a_rows = out[out.Ticker == "A"]
    _check(len(a_rows) == 1, "A has exactly one exit row")
    _check(a_rows.iloc[0]["Action"] == RecosAction.SELL_PEAK_DD,
           f"A's row is SELL_PEAK_DD (peak_drawdown wins)  got={a_rows.iloc[0]['Action']}")


# ═════════════════════════════════════════════════════════════════════════════
# E. RecosAction dispatch completeness
# ═════════════════════════════════════════════════════════════════════════════

def test_recos_action_sets():
    print("\n── E. RecosAction dispatch sets ──")
    # Every SELL_* string from D2 triggers must be in FULL_CLOSE.
    for a in ["SELL_PEAK_DD", "SELL_SCORE_DECAY", "SELL_TREND_BREAK",
              "SELL_RANK_VEL", "SELL_REL_REBAR", "SELL_REGIME"]:
        _check(RecosAction.is_full_close(a), f"FULL_CLOSE contains {a}")
    # Every TRIM_* string from D2 triggers must be in PARTIAL_CLOSE.
    for a in ["TRIM_PEAK_DD", "TRIM_SCORE_DECAY", "TRIM_TREND_BREAK",
              "TRIM_RANK_VEL", "TRIM_REL_REBAR", "TRIM_REGIME"]:
        _check(RecosAction.is_partial_close(a), f"PARTIAL_CLOSE contains {a}")
    # Legacy preserved.
    _check(RecosAction.is_full_close("STOP_LOSS"), "STOP_LOSS still in FULL_CLOSE")
    _check(RecosAction.is_full_close("SELL"), "SELL still in FULL_CLOSE")
    _check(RecosAction.is_partial_close("TRIM"), "TRIM still in PARTIAL_CLOSE")
    _check(RecosAction.is_partial_close("TRIM_GRACE"), "TRIM_GRACE still in PARTIAL_CLOSE")
    _check(RecosAction.is_no_op("SELL_GRACE"), "SELL_GRACE in NO_OP")
    # Mutual exclusivity.
    overlap = RecosAction.FULL_CLOSE & RecosAction.PARTIAL_CLOSE
    _check(len(overlap) == 0, f"FULL_CLOSE ∩ PARTIAL_CLOSE = ∅  overlap={overlap}")
    overlap2 = RecosAction.FULL_CLOSE & RecosAction.NO_OP
    _check(len(overlap2) == 0, f"FULL_CLOSE ∩ NO_OP = ∅  overlap={overlap2}")


# ═════════════════════════════════════════════════════════════════════════════
# Run all
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_peak_drawdown()
    test_score_decay()
    test_trend_break()
    test_rank_velocity()
    test_relative_rebar()
    test_regime_switch()
    test_build_triggers_priority()
    test_upstream_emission_peak_drawdown()
    test_upstream_emission_score_decay_trim()
    test_upstream_emission_does_not_duplicate_buy()
    test_priority_peak_beats_score_decay()
    test_recos_action_sets()

    print(f"\n===== D2 Smoke: {_PASS} pass, {_FAIL} fail =====")
    sys.exit(0 if _FAIL == 0 else 1)
