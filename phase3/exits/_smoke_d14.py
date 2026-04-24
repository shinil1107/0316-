"""D1.4 smoke test — SimPortfolio extension + Rank plumbing + HistoryView factory.

Verifies:
    A. SimPortfolio.apply_actions BUY_NEW seeds entry_* / peak_price /
       last_score from reco row (Score / Regime / Rank columns).
    B. SimPortfolio BUY_MORE keeps entry_* frozen, refreshes last_score,
       bumps peak_price only monotonically.
    C. SimPortfolio.update_prices maintains peak_price monotonic.
    D. SimPortfolio.load_current returns HoldingsManager-shaped DataFrame
       (parity with D1.6 live schema).
    E. build_holding_snapshots works against SimPortfolio.holdings.
    F. generate_recommendations stamps Rank column on every recos row.
    G. HoldingsManager.apply_recommendations now sources EntryRank from
       recos["Rank"] (no longer hardcoded -1).
    H. build_history_view factory constructs a usable HistoryView without
       exploding when engine internals are stubbed.

Run:
    cd 0316-/phase3
    python3 -m exits._smoke_d14
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
sys.path.insert(0, str(_PHASE3))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from simulator import SimPortfolio  # noqa: E402
from holdings_manager import HoldingsManager  # noqa: E402
from exits import build_holding_snapshots, HistoryView, build_history_view  # noqa: E402

_PASS = 0
_FAIL = 0
_LAST_SECTION = ""


def _section(name: str) -> None:
    global _LAST_SECTION
    _LAST_SECTION = name
    print(f"\n── {name} ──")


def _check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL  [{_LAST_SECTION}] {msg}")


def _make_reco(
    ticker: str, action: str, shares: int, price: float,
    score: float, regime: str, rank: int,
) -> dict:
    return {
        "Date": "2026-03-28", "Ticker": ticker, "Action": action,
        "Score": score, "TargetPct": 10.0, "ActualPct": 0.0, "GapPct": 10.0,
        "Price": price, "Shares": shares, "Capital": price * shares,
        "Regime": regime, "GraceCount": 0, "Rank": rank,
    }


# ── A. SimPortfolio BUY_NEW seeds entry_* ────────────────────────────
_section("A. SimPortfolio BUY_NEW seeds entry_*")

port = SimPortfolio(100_000.0)
recos = pd.DataFrame([
    _make_reco("AAPL", "BUY_NEW", 10, 180.0, 87.5, "BULL", 1),
    _make_reco("MSFT", "BUY_NEW", 5, 400.0, 74.2, "BULL", 2),
])
port.apply_actions(recos, {"AAPL": 180.0, "MSFT": 400.0},
                   "2026-03-28", commission_bps=10.0, slippage_bps=5.0)

h_aapl = port.holdings.get("AAPL", {})
_check(h_aapl.get("shares") == 10, "AAPL shares = 10")
_check(h_aapl.get("entry_date") == "2026-03-28", "AAPL entry_date set")
_check(abs(h_aapl.get("entry_price", 0) - 180.0) < 1e-9, "AAPL entry_price = 180")
_check(abs(h_aapl.get("entry_score", 0) - 87.5) < 1e-9, "AAPL entry_score = 87.5")
_check(h_aapl.get("entry_rank") == 1, "AAPL entry_rank = 1")
_check(h_aapl.get("entry_regime") == "BULL", "AAPL entry_regime = BULL")
_check(abs(h_aapl.get("peak_price", 0) - 180.0) < 1e-9, "AAPL peak_price = 180")
_check(abs(h_aapl.get("last_score", 0) - 87.5) < 1e-9, "AAPL last_score = 87.5")

h_msft = port.holdings.get("MSFT", {})
_check(h_msft.get("entry_rank") == 2, "MSFT entry_rank = 2")
_check(h_msft.get("entry_regime") == "BULL", "MSFT entry_regime = BULL")


# ── B. BUY_MORE keeps entry_* frozen, refreshes last_score/peak ─────
_section("B. SimPortfolio BUY_MORE freezes entry_*")

# Price moved up + regime changed + rank changed.
recos2 = pd.DataFrame([
    _make_reco("AAPL", "BUY_MORE", 5, 195.0, 91.0, "SIDE", 3),
])
port.apply_actions(recos2, {"AAPL": 195.0}, "2026-04-02",
                   commission_bps=10.0, slippage_bps=5.0)

h_aapl = port.holdings["AAPL"]
_check(h_aapl["shares"] == 15, "BUY_MORE increments shares to 15")
_check(h_aapl.get("entry_date") == "2026-03-28", "BUY_MORE preserves entry_date")
_check(abs(h_aapl.get("entry_price", 0) - 180.0) < 1e-9, "BUY_MORE preserves entry_price")
_check(abs(h_aapl.get("entry_score", 0) - 87.5) < 1e-9, "BUY_MORE preserves entry_score")
_check(h_aapl.get("entry_rank") == 1, "BUY_MORE preserves entry_rank")
_check(h_aapl.get("entry_regime") == "BULL", "BUY_MORE preserves entry_regime")
_check(abs(h_aapl.get("last_score", 0) - 91.0) < 1e-9, "BUY_MORE refreshes last_score")
_check(abs(h_aapl.get("peak_price", 0) - 195.0) < 1e-9, "BUY_MORE bumps peak to 195")


# ── C. update_prices keeps peak_price monotonic ─────────────────────
_section("C. update_prices monotonic peak_price")

port.update_prices({"AAPL": 210.0, "MSFT": 390.0})
_check(abs(port.holdings["AAPL"]["peak_price"] - 210.0) < 1e-9,
       "peak_price moves up with higher price")
_check(abs(port.holdings["MSFT"]["peak_price"] - 400.0) < 1e-9,
       "MSFT peak stays at 400 (price went down)")

port.update_prices({"AAPL": 205.0, "MSFT": 420.0})
_check(abs(port.holdings["AAPL"]["peak_price"] - 210.0) < 1e-9,
       "peak_price stays at 210 when price dips")
_check(abs(port.holdings["MSFT"]["peak_price"] - 420.0) < 1e-9,
       "MSFT peak now 420")


# ── D. load_current shape parity with live ──────────────────────────
_section("D. load_current D1.6-parity columns")

df = port.load_current()
expected_cols = {
    "Ticker", "BuyDate", "BuyPrice", "Shares", "CurrentPrice", "MarketValue",
    "PnL_Pct", "Weight", "EntryScore", "EntryRank", "EntryRegime",
    "PeakPrice", "LastScore",
}
missing = expected_cols - set(df.columns)
_check(not missing, f"all D1.6 cols present (missing={missing!r})")

aapl_row = df[df["Ticker"] == "AAPL"].iloc[0]
_check(abs(float(aapl_row["EntryScore"]) - 87.5) < 1e-9,
       "EntryScore materialised")
_check(int(aapl_row["EntryRank"]) == 1, "EntryRank materialised")
_check(str(aapl_row["EntryRegime"]) == "BULL", "EntryRegime materialised")
_check(abs(float(aapl_row["PeakPrice"]) - 210.0) < 1e-9,
       "PeakPrice materialised")
_check(abs(float(aapl_row["LastScore"]) - 91.0) < 1e-9,
       "LastScore materialised (last seen on BUY_MORE)")


# ── E. build_holding_snapshots parity ───────────────────────────────
_section("E. build_holding_snapshots works on SimPortfolio.holdings")

snapshots = build_holding_snapshots(
    current=df,
    holdings_store=port.holdings,
    score_map={"AAPL": 88.0, "MSFT": 77.0},
    rank_map={"AAPL": 5, "MSFT": 12},
    today="2026-04-03",
)
sn_by_tkr = {s.ticker: s for s in snapshots}
_check(len(snapshots) == 2, "2 snapshots from 2 holdings")
_check("AAPL" in sn_by_tkr and "MSFT" in sn_by_tkr,
       "AAPL + MSFT snapshots present")

sn_a = sn_by_tkr["AAPL"]
_check(sn_a.entry_date == "2026-03-28", "snapshot entry_date wired")
_check(abs((sn_a.entry_price or 0) - 180.0) < 1e-9, "snapshot entry_price wired")
_check(abs((sn_a.peak_price or 0) - 210.0) < 1e-9, "snapshot peak_price wired")
_check(sn_a.entry_rank == 1, "snapshot entry_rank wired")


# ── F. generate_recommendations stamps Rank on every row ────────────
_section("F. generate_recommendations emits Rank column")

# Make a controllable cfg + inputs.  We piggyback on the sim-mode path.
from types import SimpleNamespace
from engine_loader import engine  # noqa: E402

cfg = engine.Config()
cfg.regime_bull_top_n = 4
cfg.regime_side_top_n = 4
cfg.regime_defensive_top_n = 4
cfg.regime_bull_max_weight_cap = 1.0
cfg.regime_side_max_weight_cap = 1.0
cfg.regime_defensive_max_weight_cap = 1.0
cfg.regime_bull_cash_pct = 0.0
cfg.regime_side_cash_pct = 0.0
cfg.regime_defensive_cash_pct = 0.0
cfg.circuit_breaker_vix_threshold = 999.0
cfg.circuit_breaker_cash_pct = 0.0

# Empty holdings (via SimPortfolio) so we'll only see BUY_NEW + HOLD rows.
empty_port = SimPortfolio(50_000.0)
from daily_runner import generate_recommendations  # noqa: E402

scores_df = pd.DataFrame({
    "Ticker": ["AAA", "BBB", "CCC", "DDD"],
    "Score": [90.0, 80.0, 70.0, 60.0],
    "Price": [100.0, 50.0, 25.0, 10.0],
})
recos_out = generate_recommendations(
    cfg=cfg,
    scores_df=scores_df,
    regime="BULL",
    vix_close=18.0,
    holdings_mgr=empty_port,
    total_capital=50_000.0,
    daily_buy_limit=50_000.0,
    strategy_conf={},
    sim_date="2026-05-01",
)

_check("Rank" in recos_out.columns, "Rank column present in recos")
if "Rank" in recos_out.columns:
    # Top-4 universe → AAA=1, BBB=2, CCC=3, DDD=4. Check each BUY/HOLD row.
    rank_by_tkr = dict(zip(recos_out["Ticker"], recos_out["Rank"]))
    _check(rank_by_tkr.get("AAA") == 1, "AAA rank = 1")
    _check(rank_by_tkr.get("BBB") == 2, "BBB rank = 2")
    _check(rank_by_tkr.get("CCC") == 3, "CCC rank = 3")
    _check(rank_by_tkr.get("DDD") == 4, "DDD rank = 4")


# ── G. HoldingsManager.apply_recommendations sources EntryRank ──────
_section("G. HoldingsManager EntryRank from recos.Rank")

with tempfile.TemporaryDirectory() as td:
    xlsx_path = os.path.join(td, "holdings_test.xlsx")
    hm = HoldingsManager(xlsx_path)

    buy_recos = pd.DataFrame([{
        "Date": "2026-05-01", "Ticker": "XYZ", "Action": "BUY_NEW",
        "Score": 82.5, "TargetPct": 5.0, "ActualPct": 0.0, "GapPct": 5.0,
        "Price": 50.0, "Shares": 20, "Capital": 1000.0,
        "Regime": "SIDE", "GraceCount": 0, "Rank": 7,
    }])
    hm.apply_recommendations(buy_recos, trigger_type="TEST",
                              date=datetime(2026, 5, 1))

    cur = hm.load_current()
    row = cur[cur["Ticker"] == "XYZ"].iloc[0]
    _check(int(row["EntryRank"]) == 7, "EntryRank = 7 (sourced from Rank column)")
    _check(abs(float(row["EntryScore"]) - 82.5) < 1e-9, "EntryScore sourced")
    _check(str(row["EntryRegime"]) == "SIDE", "EntryRegime sourced")


# ── H. Legacy recos (no Rank col) → EntryRank = -1 ──────────────────
_section("H. Legacy recos (missing Rank) → EntryRank = -1")

with tempfile.TemporaryDirectory() as td:
    xlsx_path = os.path.join(td, "holdings_legacy.xlsx")
    hm = HoldingsManager(xlsx_path)

    legacy_recos = pd.DataFrame([{
        "Date": "2026-05-01", "Ticker": "LEG", "Action": "BUY_NEW",
        "Score": 60.0, "TargetPct": 5.0, "ActualPct": 0.0, "GapPct": 5.0,
        "Price": 40.0, "Shares": 10, "Capital": 400.0,
        "Regime": "BEAR", "GraceCount": 0,
    }])
    hm.apply_recommendations(legacy_recos, trigger_type="TEST",
                              date=datetime(2026, 5, 1))
    cur = hm.load_current()
    row = cur[cur["Ticker"] == "LEG"].iloc[0]
    _check(int(row["EntryRank"]) == -1,
           "EntryRank defaults to -1 when Rank column missing")


# ── I. build_history_view factory basics ────────────────────────────
_section("I. build_history_view factory")

# Minimal pack + stub engine: HistoryView must construct cleanly, and
# price_series must return something sane even without engine support
# (score/rank series will return [] via the None-guards in the class).
tickers_uv = ["AAA", "BBB", "CCC"]
close_mat = np.array([
    [100.0, 50.0, 25.0],
    [101.0, 51.0, 24.5],
    [102.5, 52.0, 24.0],
    [103.0, 53.0, 23.5],
    [104.0, 54.0, 23.0],
], dtype=np.float64)
pack = {"close": close_mat}

cfg_stub = SimpleNamespace()

hv = build_history_view(
    pack=pack, engine=None, di=4, tickers=tickers_uv,
    cfg_sim=cfg_stub, sel=None,
    active_w_bull=None, active_w_side=None, active_w_def=None,
    score_regime="SIDE",
)
_check(isinstance(hv, HistoryView), "factory returns HistoryView instance")

ps = hv.price_series("AAA", lookback=3)
_check(ps.shape == (3,), f"price_series lookback=3 → shape (3,), got {ps.shape}")
_check(abs(ps[-1] - 104.0) < 1e-9, "price_series latest = 104")
_check(abs(ps[0] - 102.5) < 1e-9, "price_series oldest = 102.5 (lookback=3)")

# Score / rank series should gracefully return empty when engine=None.
ss = hv.score_series("AAA", lookback=3)
_check(ss.shape == (0,), "score_series empty when engine=None")
rs = hv.rank_series("AAA", lookback=3)
_check(rs.shape == (0,), "rank_series empty when engine=None")

# Unknown ticker → empty array.
ps_bad = hv.price_series("ZZZ", lookback=3)
_check(ps_bad.shape == (0,), "price_series empty for unknown ticker")


# ── Summary ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"D1.4 SMOKE: {_PASS} passed, {_FAIL} failed")
print("=" * 60)
sys.exit(0 if _FAIL == 0 else 1)
