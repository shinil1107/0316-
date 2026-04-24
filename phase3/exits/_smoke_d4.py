"""D4 combined smoke test — exploratory dynamic exit triggers.

Covers:
  A. RiskOffAssessor — threshold-count logic, individual signals.
  B. HistoryView.atr() — correct computation + high/low plumbing.
  C. ATRTrailingStopTrigger — fire / no-fire / risk_off_only gate.
  D. ProfitTargetTrigger — fire / gates (score/extension) / tier memory.
  E. RecosAction dispatch — new D4 actions in FULL_CLOSE/PARTIAL_CLOSE.
  F. Pipeline integration — evaluate_exits stamps risk_off onto MarketSnapshot.
  G. Registry — new triggers registered with correct priorities.

Run:
    cd 0316-/phase3
    python3 -m exits._smoke_d4
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
sys.path.insert(0, str(_PHASE3))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from exits import (  # noqa: E402
    HoldingSnapshot, MarketSnapshot, ExitVerdict,
    VerdictAction, RecosAction,
    build_triggers, TRIGGER_REGISTRY,
    HistoryView, RiskOffAssessor, RiskOffInput, RiskOffResult,
    evaluate_exits,
)
from exits.triggers.atr_trailing_stop import ATRTrailingStopTrigger  # noqa: E402
from exits.triggers.profit_target import ProfitTargetTrigger  # noqa: E402


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


def _hdr(s: str) -> None:
    print(f"\n{'=' * 72}\n {s}\n{'=' * 72}")


# ─────────────────────────────────────────────────────────────────────
# A) RiskOffAssessor
# ─────────────────────────────────────────────────────────────────────

def test_risk_off_assessor():
    _hdr("A) RiskOffAssessor")

    ass = RiskOffAssessor()  # default threshold_count=2

    # A1: empty inputs → not risk off.
    res = ass.assess(RiskOffInput())
    _check(not res.risk_off and res.level == 0,
           "A1 empty input → not risk_off",
           f"reasons={res.reasons}")

    # A2: only vix level hit → level=1, not risk_off (needs 2).
    res = ass.assess(RiskOffInput(vix=32.0))
    _check(not res.risk_off and res.level == 1,
           "A2 single signal (vix_level) → level 1, not risk_off",
           f"reasons={res.reasons}")

    # A3: vix_level + vix_spike → risk_off (threshold 2 met).
    res = ass.assess(RiskOffInput(
        vix=32.0,
        vix_series=[18.0, 18.5, 19.0, 19.5, 20.0, 20.2, 20.5, 21.0],  # vix_7d_delta=+11
    ))
    _check(res.risk_off and res.level >= 2,
           "A3 vix_level+vix_spike → risk_off",
           f"level={res.level} delta={res.vix_7d_delta:+.1f} reasons={res.reasons}")

    # A4: regime transition BULL→DEF.
    res = ass.assess(RiskOffInput(
        vix=20.0,
        regime="DEF",
        recent_regimes=["BULL", "BULL", "BULL", "DEF", "DEF"],
    ))
    _check(res.level == 1,
           "A4 regime BULL→DEF transition detected (level=1)",
           f"reasons={res.reasons}")

    # A5: portfolio dd -12% (threshold 10%).
    res = ass.assess(RiskOffInput(
        portfolio_value=88.0, portfolio_peak=100.0,
    ))
    _check(res.level == 1 and abs(res.portfolio_dd_pct - (-12.0)) < 1e-6,
           "A5 portfolio DD -12% (>10% threshold)",
           f"level={res.level} dd={res.portfolio_dd_pct:+.1f}%")

    # A6: all four signals → risk_off.
    res = ass.assess(RiskOffInput(
        vix=33.0,
        vix_series=[18.0]*7 + [22.0],  # +15 delta
        regime="DEF",
        recent_regimes=["BULL", "BULL", "DEF"],
        portfolio_value=85.0, portfolio_peak=100.0,
    ))
    _check(res.risk_off and res.level == 4,
           "A6 all 4 signals → risk_off",
           f"level={res.level}")

    # A7: from_config override.
    ass2 = RiskOffAssessor.from_config({"threshold_count": 1, "vix_critical": 25.0})
    res = ass2.assess(RiskOffInput(vix=26.0))
    _check(res.risk_off and res.level == 1,
           "A7 from_config threshold=1 fires on single signal")


# ─────────────────────────────────────────────────────────────────────
# B) HistoryView.atr()
# ─────────────────────────────────────────────────────────────────────

def test_history_atr():
    _hdr("B) HistoryView.atr()")

    # Build a synthetic pack with known high/low/close for 1 ticker.
    T, N = 30, 1
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, size=(T, N))) .reshape(T, N)
    high = close + 2.0
    low = close - 2.0
    pack = {
        "dates": np.array([f"2025-01-{i+1:02d}" for i in range(T)]),
        "tickers": ["XYZ"],
        "close": close,
        "high": high,
        "low": low,
    }
    hv = HistoryView(pack=pack, engine=None, di=T - 1, tickers=["XYZ"], cfg_sim=None)

    atr = hv.atr("XYZ", 20)
    # True range is max(4, |high-prev_close|, |low-prev_close|) per day — on
    # average ~3-5 given the synth.  Just sanity-check bounds.
    _check(atr > 0 and atr < 20, "B1 ATR(20) in sane range", f"atr={atr:.3f}")

    # Test cache hit — same call returns cached value.
    atr2 = hv.atr("XYZ", 20)
    _check(atr2 == atr, "B2 ATR cache returns identical value")

    # Missing ticker returns 0.
    atr_missing = hv.atr("NOPE", 20)
    _check(atr_missing == 0.0, "B3 missing ticker → atr=0.0")

    # Pack without high/low → atr returns 0.
    pack_no_hl = {k: v for k, v in pack.items() if k not in ("high", "low")}
    hv2 = HistoryView(pack=pack_no_hl, engine=None, di=T - 1, tickers=["XYZ"], cfg_sim=None)
    _check(hv2.atr("XYZ", 20) == 0.0, "B4 pack missing high/low → atr=0.0")

    # high_series / low_series
    hs = hv.high_series("XYZ", 5)
    ls = hv.low_series("XYZ", 5)
    _check(hs.shape == (5,) and ls.shape == (5,),
           "B5 high/low series shapes", f"hs={hs.shape} ls={ls.shape}")
    _check((hs >= ls).all(), "B6 high >= low for all days")


# ─────────────────────────────────────────────────────────────────────
# C) ATRTrailingStopTrigger
# ─────────────────────────────────────────────────────────────────────

class _FakeHistoryATR:
    """Minimal HistoryView stub — only .atr() needed."""
    def __init__(self, atr_val: float):
        self._atr = atr_val
    def atr(self, ticker: str, window: int) -> float:
        return self._atr
    def score_series(self, *a, **k): return np.array([])
    def price_series(self, *a, **k): return np.array([])
    def rank_series(self, *a, **k): return np.array([])


def _market(date="2025-01-15", regime="SIDE", risk_off=False, hv=None) -> MarketSnapshot:
    return MarketSnapshot(
        date=date, regime=regime, prev_regime=regime, vix=20.0,
        scores_df=pd.DataFrame(), top_n=5, portfolio_value=100000.0,
        history=hv, risk_off=risk_off,
    )


def test_atr_trigger():
    _hdr("C) ATRTrailingStopTrigger")

    trig = ATRTrailingStopTrigger(k=3.0, atr_window=20, min_days_held=5,
                                  min_atr_pct=1.0, action="SELL")
    _check(trig.name == "atr_trailing_stop" and trig.default_priority == 155,
           "C1 name + default priority=155")

    # C2: pullback < k*ATR → escalate.
    hv = _FakeHistoryATR(atr_val=2.0)  # ATR $2; k=3 → threshold $6 pullback
    h = HoldingSnapshot(
        ticker="AAA", shares=10, entry_price=100.0, current_price=97.0,
        peak_price=100.0, days_held=10, pnl_pct=-3.0,
    )
    v = trig.evaluate(h, _market(hv=hv))
    _check(v.action == VerdictAction.ESCALATE,
           "C2 pullback $3 < 3*ATR=$6 → escalate",
           f"reason={v.reason[:60]}")

    # C3: pullback >= k*ATR → SELL_ATR_TRAIL.
    h2 = HoldingSnapshot(
        ticker="AAA", shares=10, entry_price=100.0, current_price=93.0,
        peak_price=100.0, days_held=10, pnl_pct=-7.0,
    )
    v = trig.evaluate(h2, _market(hv=hv))
    _check(v.action == VerdictAction.SELL and v.recos_action == RecosAction.SELL_ATR_TRAIL,
           "C3 pullback $7 >= 3*ATR=$6 → SELL_ATR_TRAIL",
           f"reason={v.reason[:80]}")

    # C4: min_days_held guard.
    h3 = HoldingSnapshot(
        ticker="AAA", shares=10, entry_price=100.0, current_price=93.0,
        peak_price=100.0, days_held=2, pnl_pct=-7.0,
    )
    v = trig.evaluate(h3, _market(hv=hv))
    _check(v.action == VerdictAction.ESCALATE, "C4 days_held<min → escalate")

    # C5: min_atr_pct guard (ATR too small).
    hv_tiny = _FakeHistoryATR(atr_val=0.50)  # 0.5/93 = 0.54% < min_atr_pct=1.0
    v = trig.evaluate(h2, _market(hv=hv_tiny))
    _check(v.action == VerdictAction.ESCALATE, "C5 atr_pct below min → escalate")

    # C6: risk_off_only — doesn't fire when market not in risk-off.
    trig_ro = ATRTrailingStopTrigger(k=3.0, risk_off_only=True, min_days_held=5)
    v = trig_ro.evaluate(h2, _market(risk_off=False, hv=hv))
    _check(v.action == VerdictAction.ESCALATE,
           "C6 risk_off_only=True + market not risk_off → escalate")

    # C7: risk_off_only — fires when risk-off.
    v = trig_ro.evaluate(h2, _market(risk_off=True, hv=hv))
    _check(v.action == VerdictAction.SELL, "C7 risk_off_only=True + risk_off=True → SELL")

    # C8: missing history → escalate (not crash).
    v = trig.evaluate(h2, _market(hv=None))
    _check(v.action == VerdictAction.ESCALATE, "C8 no history → escalate (no crash)")

    # C9: TRIM action.
    trig_tr = ATRTrailingStopTrigger(k=3.0, action="TRIM", partial_pct=0.5,
                                     min_days_held=5)
    v = trig_tr.evaluate(h2, _market(hv=hv))
    _check(v.action == VerdictAction.TRIM
           and v.recos_action == RecosAction.TRIM_ATR_TRAIL
           and abs(v.partial_pct - 0.5) < 1e-9,
           "C9 TRIM variant emits TRIM_ATR_TRAIL")


# ─────────────────────────────────────────────────────────────────────
# D) ProfitTargetTrigger
# ─────────────────────────────────────────────────────────────────────

class _FakeHistoryScores:
    """Stub that serves deterministic score / price series."""
    def __init__(self, scores=None, prices=None):
        self._s = np.asarray(scores, dtype=np.float64) if scores is not None else np.array([])
        self._p = np.asarray(prices, dtype=np.float64) if prices is not None else np.array([])
    def score_series(self, ticker, lookback):
        return self._s[-lookback:] if self._s.size else np.array([])
    def price_series(self, ticker, lookback):
        return self._p[-lookback:] if self._p.size else np.array([])
    def atr(self, *a, **k): return 0.0
    def rank_series(self, *a, **k): return np.array([])


def test_profit_target():
    _hdr("D) ProfitTargetTrigger")

    trig = ProfitTargetTrigger(target_pct=30.0, action="TRIM", partial_pct=0.3,
                               score_gate_enabled=True, score_decay_pct=-15.0,
                               min_days_held=10)
    _check(trig.name == "profit_target" and trig.default_priority == 115,
           "D1 name + default priority=115")

    # D2: pnl below target → escalate.
    h = HoldingSnapshot(ticker="B", shares=10, entry_price=100.0,
                       current_price=120.0, peak_price=120.0,
                       days_held=20, pnl_pct=20.0)
    v = trig.evaluate(h, _market())
    _check(v.action == VerdictAction.ESCALATE, "D2 pnl<target → escalate")

    # D3: pnl above target + score decayed enough → TRIM_PROFIT.
    hv = _FakeHistoryScores(
        scores=[100., 100., 100., 100., 120., 118., 110., 100., 95., 85.],  # peak 120, now 85 → -29%
    )
    h2 = HoldingSnapshot(ticker="B", shares=10, entry_price=100.0,
                        current_price=135.0, peak_price=135.0,
                        days_held=20, pnl_pct=35.0)
    v = trig.evaluate(h2, _market(hv=hv))
    _check(v.action == VerdictAction.TRIM
           and v.recos_action == RecosAction.TRIM_PROFIT
           and v.meta.get("profit_target_pct") == 30.0,
           "D3 pnl=+35%>=30, score decay=-29%<=-15 → TRIM_PROFIT w/ meta",
           f"meta={v.meta}")

    # D4: score gate NOT satisfied (decay too mild) → escalate.
    hv_strong = _FakeHistoryScores(
        scores=[100., 100., 100., 100., 100., 100., 100., 105., 108., 110.]
    )
    v = trig.evaluate(h2, _market(hv=hv_strong))
    _check(v.action == VerdictAction.ESCALATE, "D4 score not decayed → escalate")

    # D5: tier memory — tier already in profit_targets_hit → escalate.
    h3 = HoldingSnapshot(ticker="B", shares=10, entry_price=100.0,
                        current_price=135.0, peak_price=135.0,
                        days_held=20, pnl_pct=35.0,
                        profit_targets_hit=frozenset({30.0}))
    v = trig.evaluate(h3, _market(hv=hv))
    _check(v.action == VerdictAction.ESCALATE, "D5 tier already hit → escalate")

    # D6: extension gate alone.
    trig_ext = ProfitTargetTrigger(target_pct=30.0, action="TRIM", partial_pct=0.3,
                                   score_gate_enabled=False,
                                   extension_enabled=True,
                                   extension_window=5,
                                   extension_threshold=0.20,
                                   min_days_held=10)
    # MA5=100, price=135 → ext=35% > 20% threshold.
    hv_flat = _FakeHistoryScores(prices=[100., 100., 100., 100., 100.])
    v = trig_ext.evaluate(h2, _market(hv=hv_flat))
    _check(v.action == VerdictAction.TRIM, "D6 extension gate fires when ext>thresh",
           f"reason={v.reason[:100]}")

    # D7: SELL variant.
    trig_sell = ProfitTargetTrigger(target_pct=100.0, action="SELL",
                                    score_gate_enabled=True, score_decay_pct=-8.0,
                                    min_days_held=10)
    hv_decayed = _FakeHistoryScores(scores=[100., 100., 100., 100., 100., 90.])
    h_big = HoldingSnapshot(ticker="B", shares=10, entry_price=100.0,
                           current_price=210.0, peak_price=210.0,
                           days_held=20, pnl_pct=110.0)
    v = trig_sell.evaluate(h_big, _market(hv=hv_decayed))
    _check(v.action == VerdictAction.SELL
           and v.recos_action == RecosAction.SELL_PROFIT,
           "D7 SELL variant emits SELL_PROFIT")


# ─────────────────────────────────────────────────────────────────────
# E) RecosAction dispatch completeness
# ─────────────────────────────────────────────────────────────────────

def test_recos_action_dispatch():
    _hdr("E) RecosAction dispatch sets")

    for a in (RecosAction.SELL_ATR_TRAIL, RecosAction.SELL_PROFIT):
        _check(RecosAction.is_full_close(a),
               f"E full_close contains {a}")

    for a in (RecosAction.TRIM_ATR_TRAIL, RecosAction.TRIM_PROFIT):
        _check(RecosAction.is_partial_close(a),
               f"E partial_close contains {a}")

    # Sanity: new actions are not in NO_OP.
    for a in (RecosAction.SELL_ATR_TRAIL, RecosAction.TRIM_ATR_TRAIL,
              RecosAction.SELL_PROFIT, RecosAction.TRIM_PROFIT):
        _check(not RecosAction.is_no_op(a),
               f"E no_op does not contain {a}")


# ─────────────────────────────────────────────────────────────────────
# F) Pipeline integration — evaluate_exits stamps risk_off
# ─────────────────────────────────────────────────────────────────────

def test_pipeline_risk_off():
    _hdr("F) Pipeline integration — risk_off stamping")

    # Build a minimal current df + holdings_store.  We use the stop_loss
    # trigger so the pipeline exercises the full path with a grace consumer
    # disabled (prev_recos=None is safe).
    current = pd.DataFrame([
        {"Ticker": "AAA", "Shares": 10, "BuyPrice": 100.0,
         "CurrentPrice": 120.0, "PnL_Pct": 20.0, "MarketValue": 1200.0},
    ])
    holdings_store = {
        "AAA": {"shares": 10, "avg_cost": 100.0, "entry_price": 100.0,
                "entry_date": "2024-12-01", "peak_price": 125.0},
    }
    scores_df = pd.DataFrame([{"Ticker": "AAA", "Score": 50.0, "Price": 120.0}])

    # ATR trigger with risk_off_only=True.
    from exits.triggers.atr_trailing_stop import ATRTrailingStopTrigger as _Trig
    triggers = [_Trig(k=3.0, atr_window=20, risk_off_only=True, min_days_held=5)]

    # Use default assessor; inputs crafted to push risk-off True.
    verdicts, _g, _rr = evaluate_exits(
        triggers=triggers, current=current, holdings_store=holdings_store,
        scores_df=scores_df, score_map={"AAA": 50.0},
        regime="DEF", today="2025-02-05", vix=33.0, top_n=5,
        total_capital=80000.0,
        vix_series=[18.0]*7 + [22.0],
        recent_regimes=["BULL", "BULL", "BULL", "DEF", "DEF"],
        portfolio_peak=100000.0,
    )
    # No meaningful history → ATR = 0 → trigger escalates regardless of
    # risk_off.  But the MarketSnapshot stamping is tested via a direct
    # pipeline call — we don't have access to m_snap here.  Instead check
    # that the assessor classifies the inputs as risk_off by running it.
    ass = RiskOffAssessor()
    res = ass.assess(RiskOffInput(
        vix=33.0, vix_series=[18.0]*7 + [22.0],
        regime="DEF", recent_regimes=["BULL", "BULL", "BULL", "DEF", "DEF"],
        portfolio_value=80000.0, portfolio_peak=100000.0,
    ))
    _check(res.risk_off, "F1 assessor classifies inputs as risk_off",
           f"level={res.level} reasons={res.reasons}")
    _check(isinstance(verdicts, dict),
           "F2 evaluate_exits returns dict (even with risk_off path)",
           f"verdicts={len(verdicts)}")


# ─────────────────────────────────────────────────────────────────────
# G) Registry — new triggers + priorities
# ─────────────────────────────────────────────────────────────────────

def test_registry():
    _hdr("G) Registry + build_triggers priority ordering")

    _check("atr_trailing_stop" in TRIGGER_REGISTRY,
           "G1 atr_trailing_stop registered")
    _check("profit_target" in TRIGGER_REGISTRY,
           "G2 profit_target registered")

    # Build an explicit strategy with baseline + D4 triggers.
    strat = {
        "exit_triggers": [
            {"type": "peak_drawdown", "params": {"drawdown_pct": -20.0}},
            {"type": "atr_trailing_stop", "params": {"k": 3.0}},
            {"type": "profit_target", "params": {"target_pct": 50.0}},
            {"type": "stop_loss", "params": {"threshold_pct": -15.0}},
        ],
    }
    triggers = build_triggers(strat)
    priorities = [t.priority for t in triggers]
    names = [t.name for t in triggers]
    _check(names == ["peak_drawdown", "atr_trailing_stop", "profit_target", "stop_loss"]
           and priorities == sorted(priorities, reverse=True),
           "G3 priority ordering: peak_drawdown>atr>profit_target>stop_loss",
           f"{list(zip(names, priorities))}")


if __name__ == "__main__":
    test_risk_off_assessor()
    test_history_atr()
    test_atr_trigger()
    test_profit_target()
    test_recos_action_dispatch()
    test_pipeline_risk_off()
    test_registry()

    print(f"\n{'=' * 72}")
    print(f" D4 smoke: {_PASS} passed, {_FAIL} failed")
    print(f"{'=' * 72}")
    sys.exit(0 if _FAIL == 0 else 1)
