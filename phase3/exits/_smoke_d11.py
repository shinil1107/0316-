"""D1.1 smoke test — run directly:  python -m phase3.exits._smoke_d11

Verifies:
  * Package imports cleanly with no circulars.
  * ``triggers/`` auto-registers the noop.
  * build_triggers() handles all 5 config shapes we expect:
      1. empty strategy                   → 0 triggers
      2. legacy stop_loss only            → 1 (stop_loss @ prio 100)   [D1.2 needed]
      3. legacy grace only                → 1 (sell_grace @ prio 10)   [D1.3 needed]
      4. legacy both                      → 2, ordered SL first         [D1.2/D1.3]
      5. explicit exit_triggers w/ noop   → 1 (noop)
  * HoldingSnapshot / MarketSnapshot / ExitVerdict construct + round-trip.
  * HistoryView works in engine-less test mode (returns empty arrays).
  * extend_holding_fields is idempotent.

In D1.1 scope, cases 2–4 are **expected to raise KeyError** because
``stop_loss`` and ``sell_grace`` triggers don't exist yet — we catch and
report that as EXPECTED.  Cases 1 and 5 must pass.
"""

from __future__ import annotations

import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from phase3.exits import (  # noqa: E402
    HoldingSnapshot, MarketSnapshot, ExitVerdict, VerdictAction,
    build_triggers, TRIGGER_REGISTRY, HistoryView,
    build_holding_snapshots, extend_holding_fields,
)
from phase3.exits.base import BaseTrigger  # noqa: E402

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402


PASS = "[ OK ]"
FAIL = "[FAIL]"
EXPECTED_FAIL = "[EXPECTED-FAIL]"

results: list[tuple[str, str, str]] = []


def _t(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    results.append((tag, label, detail))
    print(f"{tag}  {label}  {detail}")


# ──────────────────────────────────────────────────────────────────────
# 1. Registry population
# ──────────────────────────────────────────────────────────────────────

print("\n--- Registry ---")
_t("triggers package imported and registered noop",
   "noop" in TRIGGER_REGISTRY,
   f"registered={sorted(TRIGGER_REGISTRY.keys())}")


# ──────────────────────────────────────────────────────────────────────
# 2. Config shapes
# ──────────────────────────────────────────────────────────────────────

print("\n--- build_triggers() modes ---")

# Case 1: empty strategy
try:
    ts = build_triggers({})
    _t("Case 1: empty strategy → 0 triggers",
       ts == [], f"len={len(ts)}")
except Exception as e:
    _t("Case 1: empty strategy", False, f"raised: {e}")

# Case 2: legacy stop_loss only (D1.2 — now active)
try:
    ts = build_triggers({
        "enable_stop_loss": True, "stop_loss_pct": -15.0,
    })
    _t("Case 2: legacy stop_loss only → 1 trigger (D1.2)",
       len(ts) == 1 and ts[0].name == "stop_loss"
       and ts[0].priority == 100
       and getattr(ts[0], "threshold_pct", None) == -15.0,
       f"got {[(t.name, t.priority, getattr(t, 'threshold_pct', None)) for t in ts]}")
except Exception as e:
    _t("Case 2: legacy stop_loss only", False, f"raised: {e}")

# Case 2b: legacy stop_loss OFF → NOT added
try:
    ts = build_triggers({
        "enable_stop_loss": False, "stop_loss_pct": -15.0,
    })
    _t("Case 2b: enable_stop_loss=False → trigger absent",
       ts == [], f"got {[t.name for t in ts]}")
except Exception as e:
    _t("Case 2b: enable_stop_loss=False", False, f"raised: {e}")

# Case 2c: stop_loss_pct uses default (-15.0) when omitted
try:
    ts = build_triggers({"enable_stop_loss": True})
    _t("Case 2c: default threshold = -15.0",
       len(ts) == 1 and ts[0].threshold_pct == -15.0,
       f"got threshold={getattr(ts[0], 'threshold_pct', None)}")
except Exception as e:
    _t("Case 2c: default threshold", False, f"raised: {e}")

# Case 3: legacy grace only (D1.3 — now active)
try:
    ts = build_triggers({"sell_grace_days": 60})
    _t("Case 3: legacy grace only → 1 trigger (D1.3)",
       len(ts) == 1 and ts[0].name == "sell_grace"
       and ts[0].priority == 10
       and getattr(ts[0], "days", None) == 60
       and getattr(ts[0], "step1_days", None) == 0,
       f"got {[(t.name, t.priority, t.days, t.step1_days) for t in ts]}")
except Exception as e:
    _t("Case 3: legacy grace only", False, f"raised: {e}")

# Case 3b: legacy grace with 2-step milestone
try:
    ts = build_triggers({
        "sell_grace_days": 60,
        "grace_step1_days": 15,
        "grace_step1_sell_pct": 0.5,
    })
    t0 = ts[0]
    _t("Case 3b: legacy grace + 2-step params propagate",
       t0.days == 60 and t0.step1_days == 15 and t0.step1_sell_pct == 0.5)
except Exception as e:
    _t("Case 3b: legacy grace 2-step", False, f"raised: {e}")

# Case 3c: sell_grace_days=0 → trigger NOT added (legacy default = no grace)
try:
    ts = build_triggers({"sell_grace_days": 0})
    _t("Case 3c: sell_grace_days=0 → trigger absent", ts == [])
except Exception as e:
    _t("Case 3c: grace=0", False, f"raised: {e}")

# Case 4: legacy both (D1.3 — now fully active)
try:
    ts = build_triggers({
        "enable_stop_loss": True, "stop_loss_pct": -15.0,
        "sell_grace_days": 60,
    })
    _t("Case 4: legacy both → 2 triggers, SL first",
       len(ts) == 2 and ts[0].name == "stop_loss" and ts[1].name == "sell_grace"
       and ts[0].priority > ts[1].priority,
       f"got {[(t.name, t.priority) for t in ts]}")
except Exception as e:
    _t("Case 4: legacy both", False, f"raised: {e}")

# Case 5: explicit list with noop
try:
    ts = build_triggers({
        "exit_triggers": [
            {"type": "noop", "priority": 50, "regimes": ["BULL", "SIDE"]},
        ],
    })
    ok = (len(ts) == 1 and ts[0].name == "noop"
          and ts[0].priority == 50
          and ts[0].enabled_regimes == {"BULL", "SIDE"})
    _t("Case 5: explicit [noop] → active only in BULL/SIDE",
       ok, repr(ts))
except Exception as e:
    _t("Case 5: explicit [noop]", False, f"raised: {e}")

# Case 5b: is_active regime gating
try:
    ts = build_triggers({
        "exit_triggers": [{"type": "noop", "regimes": ["BULL"]}],
    })
    t0 = ts[0]
    ok = (t0.is_active("BULL") and not t0.is_active("SIDE")
          and not t0.is_active("CRASH") and not t0.is_active("DEFENSIVE"))
    _t("Case 5b: is_active regime gating (BULL only)", ok,
       f"BULL={t0.is_active('BULL')} SIDE={t0.is_active('SIDE')} "
       f"DEFENSIVE={t0.is_active('DEFENSIVE')} CRASH={t0.is_active('CRASH')}")
except Exception as e:
    _t("Case 5b: is_active gating", False, f"raised: {e}")

# Case 5c: DEF alias folds DEFENSIVE/CRASH/BEAR
try:
    ts = build_triggers({
        "exit_triggers": [{"type": "noop", "regimes": ["DEF"]}],
    })
    t0 = ts[0]
    ok = (t0.is_active("DEFENSIVE") and t0.is_active("CRASH")
          and t0.is_active("BEAR") and not t0.is_active("BULL"))
    _t("Case 5c: DEF alias → DEFENSIVE/CRASH/BEAR all match", ok)
except Exception as e:
    _t("Case 5c: DEF alias", False, f"raised: {e}")

# Case 6: unknown type raises clearly
try:
    build_triggers({"exit_triggers": [{"type": "does_not_exist"}]})
    _t("Case 6: unknown type raises", False, "no exception!")
except KeyError:
    _t("Case 6: unknown type raises KeyError", True)
except Exception as e:
    _t("Case 6: unknown type raises", False, f"wrong exception: {type(e).__name__}")


# ──────────────────────────────────────────────────────────────────────
# 3. Snapshot + verdict round-trip
# ──────────────────────────────────────────────────────────────────────

print("\n--- Snapshot + Verdict ---")

h = HoldingSnapshot(ticker="AAPL", shares=10, current_price=150.0,
                    entry_price=100.0, peak_price=160.0, pnl_pct=50.0,
                    peak_drawdown_pct=-6.25)
m = MarketSnapshot(date="2026-03-28", regime="BULL", prev_regime="SIDE",
                   vix=15.0, scores_df=pd.DataFrame(), top_n=20,
                   portfolio_value=100000.0)

_t("HoldingSnapshot constructs", h.ticker == "AAPL" and h.shares == 10)
_t("MarketSnapshot constructs", m.regime == "BULL" and m.prev_regime == "SIDE")

v = ExitVerdict.hold()
_t("ExitVerdict.hold() not terminal", not v.is_terminal())
v = ExitVerdict.sell(reason="test")
_t("ExitVerdict.sell() terminal + reason", v.is_terminal() and v.reason == "test")
v = ExitVerdict.trim(pct=0.5, reason="half-out")
_t("ExitVerdict.trim() carries partial_pct",
   v.action == VerdictAction.TRIM and v.partial_pct == 0.5)


# ──────────────────────────────────────────────────────────────────────
# 4. Noop trigger evaluate
# ──────────────────────────────────────────────────────────────────────

print("\n--- Noop evaluate ---")

from phase3.exits.triggers._noop import NoopTrigger  # noqa: E402
noop = NoopTrigger()
v = noop.evaluate(h, m)
_t("NoopTrigger.evaluate → ESCALATE",
   v.action == VerdictAction.ESCALATE)


# ──────────────────────────────────────────────────────────────────────
# 4b. StopLossTrigger evaluate
# ──────────────────────────────────────────────────────────────────────

print("\n--- StopLossTrigger evaluate ---")

from phase3.exits.triggers.stop_loss import StopLossTrigger  # noqa: E402

sl = StopLossTrigger(threshold_pct=-15.0)

def _h(pnl, shares=10):
    return HoldingSnapshot(ticker="X", shares=shares, current_price=100.0,
                           entry_price=100.0, pnl_pct=pnl)

# Clear SELL
v = sl.evaluate(_h(-20.0), m)
_t("SL: pnl=-20% vs -15% threshold → SELL",
   v.action == VerdictAction.SELL, v.reason)

# Boundary equals threshold → SELL (legacy uses <=)
v = sl.evaluate(_h(-15.0), m)
_t("SL: pnl=-15% == threshold → SELL (<= semantics)",
   v.action == VerdictAction.SELL, v.reason)

# Small loss → ESCALATE
v = sl.evaluate(_h(-10.0), m)
_t("SL: pnl=-10% > -15% → ESCALATE", v.action == VerdictAction.ESCALATE)

# Profit → ESCALATE
v = sl.evaluate(_h(+5.0), m)
_t("SL: profit +5% → ESCALATE", v.action == VerdictAction.ESCALATE)

# Zero shares → ESCALATE (should never be reached in practice, defensive)
v = sl.evaluate(_h(-99.0, shares=0), m)
_t("SL: shares=0 → ESCALATE (no position)",
   v.action == VerdictAction.ESCALATE)

# Custom threshold
sl_tight = StopLossTrigger(threshold_pct=-10.0)
v = sl_tight.evaluate(_h(-12.0), m)
_t("SL: threshold=-10%, pnl=-12% → SELL", v.action == VerdictAction.SELL)
v = sl_tight.evaluate(_h(-9.0), m)
_t("SL: threshold=-10%, pnl=-9% → ESCALATE", v.action == VerdictAction.ESCALATE)

# Regime gate (legacy pathway auto-handles via build_triggers, but direct
# is_active check for explicit-mode users)
sl_bull_only = StopLossTrigger(threshold_pct=-15.0, enabled_regimes={"BULL"})
_t("SL: enabled_regimes={BULL} → active in BULL",
   sl_bull_only.is_active("BULL"))
_t("SL: enabled_regimes={BULL} → inactive in SIDE",
   not sl_bull_only.is_active("SIDE"))
_t("SL: enabled_regimes={BULL} → inactive in CRASH",
   not sl_bull_only.is_active("CRASH"))


# ──────────────────────────────────────────────────────────────────────
# 4c. SellGraceTrigger evaluate (3-branch state machine)
# ──────────────────────────────────────────────────────────────────────

print("\n--- SellGraceTrigger evaluate ---")

from phase3.exits.triggers.sell_grace import SellGraceTrigger  # noqa: E402

sg = SellGraceTrigger(days=60, step1_days=0)

def _h_grace(rank, grace_count=0, shares=10):
    return HoldingSnapshot(
        ticker="X", shares=shares, current_price=100.0,
        entry_price=100.0, pnl_pct=0.0, current_rank=rank,
        grace_count=grace_count,
    )

def _m_grace(top_n=20, same_day=False):
    return MarketSnapshot(
        date="2026-03-28", regime="BULL", prev_regime="BULL",
        vix=15.0, scores_df=pd.DataFrame(), top_n=top_n,
        portfolio_value=100000.0, same_day_rerun=same_day,
    )

# In target → ESCALATE (no grace work)
v = sg.evaluate(_h_grace(rank=5), _m_grace(top_n=20))
_t("SG: rank=5 in top_n=20 → ESCALATE (in target)",
   v.action == VerdictAction.ESCALATE)

# Boundary: rank == top_n → still in target
v = sg.evaluate(_h_grace(rank=20), _m_grace(top_n=20))
_t("SG: rank=20 == top_n=20 → ESCALATE (boundary in target)",
   v.action == VerdictAction.ESCALATE)

# Just-dropped-out, first day → WARN (SELL_GRACE) with new_count=1
v = sg.evaluate(_h_grace(rank=25, grace_count=0), _m_grace(top_n=20))
_t("SG: rank=25, grace=0 → WARN (day 1/60)",
   v.action == VerdictAction.WARN
   and v.recos_action == "SELL_GRACE"
   and v.grace_count == 1,
   f"got action={v.action} recos={v.recos_action} gc={v.grace_count}")

# Continuing to count down
v = sg.evaluate(_h_grace(rank=99, grace_count=30), _m_grace(top_n=20))
_t("SG: grace=30 → WARN day 31/60",
   v.action == VerdictAction.WARN and v.grace_count == 31)

# Boundary: new_count == days (60) → still WARN (not yet > threshold)
v = sg.evaluate(_h_grace(rank=99, grace_count=59), _m_grace(top_n=20))
_t("SG: grace=59 → WARN day 60/60 (== threshold, NOT expired)",
   v.action == VerdictAction.WARN and v.grace_count == 60)

# new_count > days → SELL
v = sg.evaluate(_h_grace(rank=99, grace_count=60), _m_grace(top_n=20))
_t("SG: grace=60 → SELL day 61 > 60 (expired)",
   v.action == VerdictAction.SELL and v.recos_action == "SELL")

# Unranked ticker (rank = -1) → treated as out-of-target
v = sg.evaluate(_h_grace(rank=-1, grace_count=0), _m_grace(top_n=20))
_t("SG: rank=-1 (unranked) → WARN (out of target)",
   v.action == VerdictAction.WARN and v.grace_count == 1)

# same_day_rerun → grace count NOT advanced
v = sg.evaluate(_h_grace(rank=99, grace_count=30),
                _m_grace(top_n=20, same_day=True))
_t("SG: same_day_rerun → count stays at 30 (no advance)",
   v.action == VerdictAction.WARN and v.grace_count == 30,
   f"got gc={v.grace_count}")

# same_day_rerun at boundary
v = sg.evaluate(_h_grace(rank=99, grace_count=60),
                _m_grace(top_n=20, same_day=True))
_t("SG: same_day_rerun at grace=60 → WARN, not SELL",
   v.action == VerdictAction.WARN and v.grace_count == 60,
   f"got action={v.action} gc={v.grace_count}")

# shares=0 → ESCALATE
v = sg.evaluate(_h_grace(rank=99, grace_count=30, shares=0), _m_grace())
_t("SG: shares=0 → ESCALATE", v.action == VerdictAction.ESCALATE)

# ── 2-step grace (step1) ──────────────────────────────────────────────

sg2 = SellGraceTrigger(days=30, step1_days=15, step1_sell_pct=0.5)

# Day 1..14 → WARN
v = sg2.evaluate(_h_grace(rank=99, grace_count=5), _m_grace(top_n=20))
_t("SG2: day 6 < step1 → WARN",
   v.action == VerdictAction.WARN and v.grace_count == 6)

# Day 15 (== step1) → TRIM 50%
v = sg2.evaluate(_h_grace(rank=99, grace_count=14), _m_grace(top_n=20))
_t("SG2: grace=14 → TRIM step1 (day 15 == step1_days, 50%)",
   v.action == VerdictAction.TRIM
   and v.partial_pct == 0.5
   and v.recos_action == "TRIM_GRACE"
   and v.grace_count == 15,
   f"got action={v.action} pct={v.partial_pct} recos={v.recos_action} gc={v.grace_count}")

# Day 16..30 → WARN (step1 already past, waiting for day 31)
v = sg2.evaluate(_h_grace(rank=99, grace_count=15), _m_grace(top_n=20))
_t("SG2: grace=15 → WARN day 16 (past step1, counting to full sell)",
   v.action == VerdictAction.WARN and v.grace_count == 16)

# Day 30 (== days) → WARN (not yet > days)
v = sg2.evaluate(_h_grace(rank=99, grace_count=29), _m_grace(top_n=20))
_t("SG2: grace=29 → WARN day 30 == days",
   v.action == VerdictAction.WARN and v.grace_count == 30)

# Day 31 (> days) → SELL
v = sg2.evaluate(_h_grace(rank=99, grace_count=30), _m_grace(top_n=20))
_t("SG2: grace=30 → SELL day 31 > days",
   v.action == VerdictAction.SELL and v.recos_action == "SELL")

# same_day_rerun at step1 threshold — count stays, so it stays == step1_days
# and re-emits TRIM (legacy same-day re-run on the step1 day re-emits TRIM_GRACE)
v = sg2.evaluate(_h_grace(rank=99, grace_count=15),
                 _m_grace(top_n=20, same_day=True))
_t("SG2: same_day_rerun at step1 → TRIM stays",
   v.action == VerdictAction.TRIM and v.grace_count == 15,
   f"got action={v.action} gc={v.grace_count}")

# ── Terminal semantics ────────────────────────────────────────────────

_t("VerdictAction.WARN is terminal",
   VerdictAction.is_terminal(VerdictAction.WARN))
_t("VerdictAction.ESCALATE is NOT terminal",
   not VerdictAction.is_terminal(VerdictAction.ESCALATE))
_t("VerdictAction.HOLD is NOT terminal",
   not VerdictAction.is_terminal(VerdictAction.HOLD))

# ── Interaction: stop_loss wins over sell_grace by priority ───────────

print("\n--- Priority resolution (SL > grace) ---")

ts = build_triggers({
    "enable_stop_loss": True, "stop_loss_pct": -15.0,
    "sell_grace_days": 60,
})
ordered = [t.name for t in ts]
_t("build_triggers returns SL before grace by priority",
   ordered == ["stop_loss", "sell_grace"],
   f"got {ordered}")

# A ticker that's BOTH out-of-target AND stopped-out → SL fires first, grace skipped.
h_both = HoldingSnapshot(
    ticker="X", shares=10, current_price=80.0, entry_price=100.0,
    pnl_pct=-20.0, current_rank=99, grace_count=5,
)
m_both = MarketSnapshot(date="2026-03-28", regime="BULL",
                        prev_regime="BULL", vix=15.0,
                        scores_df=pd.DataFrame(), top_n=20,
                        portfolio_value=100000.0)

# Simulate pipeline: iterate triggers in priority order, first terminal wins.
final_v = None
for t in ts:
    vv = t.evaluate(h_both, m_both)
    if vv.is_terminal():
        final_v = vv
        final_v.trigger_name = t.name
        break
_t("Combined: SL-hit + out-of-target → STOP_LOSS, not SELL_GRACE",
   final_v is not None
   and final_v.trigger_name == "stop_loss"
   and final_v.recos_action == "STOP_LOSS",
   f"got trigger={getattr(final_v, 'trigger_name', None)} "
   f"recos_action={getattr(final_v, 'recos_action', None)}")


# ──────────────────────────────────────────────────────────────────────
# 5. HistoryView in stub mode (no engine)
# ──────────────────────────────────────────────────────────────────────

print("\n--- HistoryView (stub mode) ---")

pack = {
    "tickers": ["AAPL", "MSFT", "GOOG"],
    "close": np.array([
        [100, 200, 300],
        [101, 201, 301],
        [102, 199, 305],
        [105, 205, 310],
        [103, 210, 315],
    ], dtype=np.float64),
}
hv = HistoryView(pack=pack, engine=None, di=4,
                 tickers=list(pack["tickers"]), cfg_sim=None)

ps = hv.price_series("AAPL", lookback=3)
_t("HistoryView.price_series returns last 3 closes",
   np.allclose(ps, [102.0, 105.0, 103.0]), f"got {ps.tolist()}")

ss = hv.score_series("AAPL", lookback=3)
_t("HistoryView.score_series empty when engine is None",
   ss.size == 0, f"got {ss.tolist()}")

rs = hv.rank_series("AAPL", lookback=3)
_t("HistoryView.rank_series empty when engine is None",
   rs.size == 0, f"got {rs.tolist()}")

ps2 = hv.price_series("UNKNOWN", lookback=3)
_t("HistoryView returns empty for unknown ticker", ps2.size == 0)


# ──────────────────────────────────────────────────────────────────────
# 6. extend_holding_fields / build_holding_snapshots
# ──────────────────────────────────────────────────────────────────────

print("\n--- State helpers ---")

holdings = {
    "AAPL": {"shares": 10, "avg_cost": 100.0, "current_price": 150.0},
}

extend_holding_fields(
    holdings, price_map={"AAPL": 150.0}, date="2026-01-01",
    score_map={"AAPL": 77.5}, rank_map={"AAPL": 3}, regime="BULL",
)
aapl = holdings["AAPL"]
_t("extend_holding_fields seeds entry_* on first call",
   aapl["entry_date"] == "2026-01-01"
   and aapl["entry_price"] == 100.0
   and aapl["entry_score"] == 77.5
   and aapl["entry_rank"] == 3
   and aapl["entry_regime"] == "BULL"
   and aapl["peak_price"] == 150.0
   and aapl["last_score"] == 77.5)

# Second call with higher price → peak updates, entry_* stable.
extend_holding_fields(
    holdings, price_map={"AAPL": 170.0}, date="2026-01-05",
    score_map={"AAPL": 80.0}, rank_map={"AAPL": 2}, regime="BULL",
)
_t("extend_holding_fields updates peak_price and last_score only",
   aapl["entry_date"] == "2026-01-01"
   and aapl["entry_score"] == 77.5
   and aapl["peak_price"] == 170.0
   and aapl["last_score"] == 80.0)

# Third call with lower price → peak unchanged.
extend_holding_fields(
    holdings, price_map={"AAPL": 160.0}, date="2026-01-06",
    score_map={"AAPL": 78.0}, rank_map={"AAPL": 4}, regime="BULL",
)
_t("extend_holding_fields leaves peak_price on drop",
   aapl["peak_price"] == 170.0 and aapl["last_score"] == 78.0)

# build_holding_snapshots
current = pd.DataFrame([{
    "Ticker": "AAPL", "Shares": 10, "BuyPrice": 100.0,
    "CurrentPrice": 160.0, "PnL_Pct": 60.0,
}])
snaps = build_holding_snapshots(
    current, holdings,
    score_map={"AAPL": 78.0}, rank_map={"AAPL": 4},
    today="2026-01-06", prev_grace={"AAPL": 0},
)
_t("build_holding_snapshots len == 1", len(snaps) == 1)
s = snaps[0]
_t("snapshot carries entry_* from holdings_store",
   s.entry_date == "2026-01-01" and s.entry_price == 100.0
   and s.entry_score == 77.5 and s.entry_rank == 3
   and s.peak_price == 170.0)
_t("snapshot derives days_held correctly",
   s.days_held == 5, f"days_held={s.days_held}")
_t("snapshot derives peak_drawdown_pct",
   abs(s.peak_drawdown_pct - (160.0 / 170.0 - 1.0) * 100.0) < 1e-9,
   f"dd_pct={s.peak_drawdown_pct:.4f}")


# ──────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────

n_pass = sum(1 for r, *_ in results if r == PASS)
n_fail = sum(1 for r, *_ in results if r == FAIL)

print(f"\n===== D1.1 Smoke: {n_pass} pass, {n_fail} fail =====")
if n_fail:
    for tag, label, detail in results:
        if tag == FAIL:
            print(f"  FAIL: {label}  {detail}")
sys.exit(0 if n_fail == 0 else 1)
