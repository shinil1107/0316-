"""D1.5 smoke test — ``generate_recommendations`` byte-parity after
the pipeline refactor.

Strategy
--------
We can't import the pre-D1 hardcoded branches side-by-side (they're gone),
so instead we pin the **expected** recos rows for a handful of carefully
chosen scenarios.  Each scenario was validated manually against the
legacy code before the refactor landed; any future regression will
surface as an assertion failure here.

Covered branches (each a separate scenario block):
    S1. Stop-loss fires, grace not configured         → STOP_LOSS row
    S2. Grace first day out of top-N                  → SELL_GRACE row (grace=1)
    S3. Grace step1 day (2-step trim)                 → TRIM_GRACE row (grace=30)
    S4. Grace expired (new_count > sell_grace_days)   → SELL row
    S5. same_day_rerun → grace count frozen, no new row
    S6. ``sell_grace_days = 0`` + held + not in target → SELL (legacy fallback)
    S7. Held and in top-N, PnL above SL               → no exit row (HOLD/TRIM/BUY path)

Run:
    cd 0316-/phase3
    python3 -m exits._smoke_d15
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
sys.path.insert(0, str(_PHASE3))

import pandas as pd  # noqa: E402

# Reuse the engine loader so the real Config is used (we only need the
# regime param fields that generate_recommendations touches).
from engine_loader import engine  # noqa: E402
from daily_runner import generate_recommendations  # noqa: E402

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


class MockHoldingsManager:
    """Minimal stand-in: only ``load_current`` / ``load_recommendations``
    / ``holdings`` are hit by generate_recommendations."""

    def __init__(self, current_rows, prev_recos_rows=None, holdings_dict=None):
        self._current = pd.DataFrame(current_rows)
        self._prev = pd.DataFrame(prev_recos_rows) if prev_recos_rows else pd.DataFrame()
        self.holdings = holdings_dict or {}

    def load_current(self):
        return self._current

    def load_recommendations(self):
        return self._prev


def _mk_cfg():
    """Default engine Config with permissive top-N so tests have headroom."""
    cfg = engine.Config()
    # Use small top_n so we can easily craft "out of target" scenarios.
    cfg.regime_bull_top_n = 3
    cfg.regime_side_top_n = 3
    cfg.regime_defensive_top_n = 3
    cfg.regime_bull_cash_pct = 0.0
    cfg.regime_side_cash_pct = 0.0
    cfg.regime_defensive_cash_pct = 0.0
    cfg.regime_bull_max_weight_cap = 1.0
    cfg.regime_side_max_weight_cap = 1.0
    cfg.regime_defensive_max_weight_cap = 1.0
    cfg.circuit_breaker_vix_threshold = 999.0  # disable
    cfg.circuit_breaker_cash_pct = 0.0
    return cfg


def _scores(rows):
    """Build a scores_df with columns ['Ticker','Score','Price']."""
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: Stop-loss fires — STOP_LOSS row, no grace involvement.
# ─────────────────────────────────────────────────────────────────────────────

def test_s1_stop_loss_only():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 10,
        "BuyPrice": 120.0, "CurrentPrice": 100.0,
        "MarketValue": 1000.0, "PnL_Pct": -16.67,
    }]
    hm = MockHoldingsManager(current)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "enable_stop_loss": True, "stop_loss_pct": -15.0,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_a = out[(out.Ticker == "A") & (out.Action == "STOP_LOSS")]
    _check(len(row_a) == 1, "S1 emits STOP_LOSS for A (pnl -16.67 ≤ -15)")
    _check(int(row_a.iloc[0]["GraceCount"]) == 0,
           "S1 STOP_LOSS row has GraceCount = 0")
    # A is not in any grace bucket
    _check(len(out[(out.Ticker == "A") & (out.Action == "SELL_GRACE")]) == 0,
           "S1 A does not appear in SELL_GRACE")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: First day out of top-N → SELL_GRACE (grace=1)
# ─────────────────────────────────────────────────────────────────────────────

def test_s2_grace_first_day():
    cfg = _mk_cfg()
    # D falls out of top-3 today; no prior grace row.
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 5, "BuyPrice": 50.0,
        "CurrentPrice": 55.0, "MarketValue": 275.0, "PnL_Pct": 10.0,
    }]
    hm = MockHoldingsManager(current, prev_recos_rows=None)
    strat = {
        "rebalance_gap_threshold": 0.02, "enable_stop_loss": False,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_d = out[(out.Ticker == "D") & (out.Action == "SELL_GRACE")]
    _check(len(row_d) == 1, "S2 emits SELL_GRACE for D (first day out of top-N)")
    _check(int(row_d.iloc[0]["GraceCount"]) == 1,
           "S2 SELL_GRACE GraceCount = 1 (prev_count 0 → +1)")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: Grace step1 day → TRIM_GRACE (grace=30, partial_pct=0.5)
# ─────────────────────────────────────────────────────────────────────────────

def test_s3_grace_step1_trim():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 10, "BuyPrice": 50.0,
        "CurrentPrice": 55.0, "MarketValue": 550.0, "PnL_Pct": 10.0,
    }]
    # D had grace=29 yesterday → today becomes 30 → step1 fires.
    prev_recos = [{
        "Date": "2025-01-14", "Ticker": "D", "Action": "SELL_GRACE",
        "GraceCount": 29,
    }]
    hm = MockHoldingsManager(current, prev_recos_rows=prev_recos)
    strat = {
        "rebalance_gap_threshold": 0.02, "enable_stop_loss": False,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_d = out[(out.Ticker == "D") & (out.Action == "TRIM_GRACE")]
    _check(len(row_d) == 1, "S3 emits TRIM_GRACE for D (day 30 step1)")
    _check(int(row_d.iloc[0]["GraceCount"]) == 30,
           "S3 TRIM_GRACE GraceCount = 30")
    _check(int(row_d.iloc[0]["Shares"]) == 5,
           f"S3 TRIM_GRACE sells 5 shares (50% of 10)  shares={row_d.iloc[0]['Shares']}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: Grace expired → full SELL row
# ─────────────────────────────────────────────────────────────────────────────

def test_s4_grace_expiry_sell():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 5, "BuyPrice": 50.0,
        "CurrentPrice": 55.0, "MarketValue": 275.0, "PnL_Pct": 10.0,
    }]
    # D had grace=60 yesterday → today becomes 61 → > 60 → full SELL.
    prev_recos = [{
        "Date": "2025-01-14", "Ticker": "D", "Action": "SELL_GRACE",
        "GraceCount": 60,
    }]
    hm = MockHoldingsManager(current, prev_recos_rows=prev_recos)
    strat = {
        "rebalance_gap_threshold": 0.02, "enable_stop_loss": False,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_d = out[(out.Ticker == "D") & (out.Action == "SELL")]
    _check(len(row_d) == 1, "S4 emits SELL for D (grace expired)")
    _check(int(row_d.iloc[0]["Shares"]) == 5,
           "S4 SELL row has full position shares (5)")
    # No SELL_GRACE / TRIM_GRACE for D
    _check(len(out[(out.Ticker == "D") & out.Action.isin(["SELL_GRACE", "TRIM_GRACE"])]) == 0,
           "S4 no grace-warn/trim row alongside SELL")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: same_day_rerun freezes grace count
# ─────────────────────────────────────────────────────────────────────────────

def test_s5_same_day_rerun():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 5, "BuyPrice": 50.0,
        "CurrentPrice": 55.0, "MarketValue": 275.0, "PnL_Pct": 10.0,
    }]
    # Prev row was written TODAY (same_day_rerun = True) with GraceCount=15.
    # Expect today's emit to keep 15, not advance to 16.
    prev_recos = [{
        "Date": "2025-01-15", "Ticker": "D", "Action": "SELL_GRACE",
        "GraceCount": 15,
    }]
    hm = MockHoldingsManager(current, prev_recos_rows=prev_recos)
    strat = {
        "rebalance_gap_threshold": 0.02, "enable_stop_loss": False,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_d = out[(out.Ticker == "D") & (out.Action == "SELL_GRACE")]
    _check(len(row_d) == 1, "S5 still emits SELL_GRACE for D on re-run")
    _check(int(row_d.iloc[0]["GraceCount"]) == 15,
           f"S5 GraceCount FROZEN at 15  got={int(row_d.iloc[0]['GraceCount'])}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 6: sell_grace_days=0 → held & not-in-target → immediate SELL.
# ─────────────────────────────────────────────────────────────────────────────

def test_s6_grace_disabled_fallback():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 5, "BuyPrice": 50.0,
        "CurrentPrice": 55.0, "MarketValue": 275.0, "PnL_Pct": 10.0,
    }]
    hm = MockHoldingsManager(current, prev_recos_rows=None)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "enable_stop_loss": False,
        # sell_grace_days = 0 → SellGraceTrigger NOT built
        "sell_grace_days": 0,
        "grace_step1_days": 0, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_d_sell = out[(out.Ticker == "D") & (out.Action == "SELL")]
    row_d_grace = out[(out.Ticker == "D") & out.Action.isin(["SELL_GRACE", "TRIM_GRACE"])]
    _check(len(row_d_sell) == 1, "S6 emits SELL for D (grace disabled fallback)")
    _check(len(row_d_grace) == 0, "S6 no grace row for D (grace disabled)")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 7: Held & in top-N & PnL above SL → no exit row.
# ─────────────────────────────────────────────────────────────────────────────

def test_s7_held_in_target_no_exit():
    cfg = _mk_cfg()
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "A", "Shares": 10, "BuyPrice": 95.0,
        "CurrentPrice": 100.0, "MarketValue": 1000.0, "PnL_Pct": 5.26,
    }]
    hm = MockHoldingsManager(current)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "enable_stop_loss": True, "stop_loss_pct": -15.0,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    bad = out[(out.Ticker == "A") & out.Action.isin(["STOP_LOSS", "SELL", "SELL_GRACE", "TRIM_GRACE"])]
    _check(len(bad) == 0, "S7 no exit row for A (held, in top-N, PnL +5%)",
           extra=f"got actions: {list(bad.Action) if len(bad) else '[]'}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 8: StopLoss + Grace both applicable on same ticker → StopLoss wins
#             (priority 100 > 10).  Important for regression guard.
# ─────────────────────────────────────────────────────────────────────────────

def test_s8_sl_over_grace_priority():
    cfg = _mk_cfg()
    # D out of top-N AND PnL -16% → both would fire; SL must win.
    scores_df = _scores([
        {"Ticker": "A", "Score": 1.0, "Price": 100.0},
        {"Ticker": "B", "Score": 0.8, "Price": 50.0},
        {"Ticker": "C", "Score": 0.6, "Price": 200.0},
    ])
    current = [{
        "Ticker": "D", "Shares": 5, "BuyPrice": 100.0,
        "CurrentPrice": 84.0, "MarketValue": 420.0, "PnL_Pct": -16.0,
    }]
    hm = MockHoldingsManager(current)
    strat = {
        "rebalance_gap_threshold": 0.02,
        "enable_stop_loss": True, "stop_loss_pct": -15.0,
        "sell_grace_days": 60, "grace_step1_days": 30, "grace_step1_sell_pct": 0.5,
    }
    out = generate_recommendations(
        cfg=cfg, holdings_mgr=hm, scores_df=scores_df,
        vix_close=15.0, regime="BULL", total_capital=10_000.0,
        daily_buy_limit=500.0, sim_date="2025-01-15",
        strategy_conf=strat,
    )
    row_sl = out[(out.Ticker == "D") & (out.Action == "STOP_LOSS")]
    row_grace = out[(out.Ticker == "D") & out.Action.isin(["SELL_GRACE", "TRIM_GRACE"])]
    _check(len(row_sl) == 1, "S8 D fires STOP_LOSS (priority > grace)")
    _check(len(row_grace) == 0, "S8 D has NO grace row (SL preempts)")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    test_s1_stop_loss_only()
    test_s2_grace_first_day()
    test_s3_grace_step1_trim()
    test_s4_grace_expiry_sell()
    test_s5_same_day_rerun()
    test_s6_grace_disabled_fallback()
    test_s7_held_in_target_no_exit()
    test_s8_sl_over_grace_priority()
    print(f"\n===== D1.5 Smoke: {_PASS} pass, {_FAIL} fail =====")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
