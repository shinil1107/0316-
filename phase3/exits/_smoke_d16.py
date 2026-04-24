"""D1.6 smoke test — HoldingsManager live schema extension.

Verifies:
    A. Legacy holdings_log.xlsx (pre-D1.6 CURRENT_COLS) auto-migrates
       with safe defaults + PeakPrice seeded from max(BuyPrice, CurrentPrice).
    B. ``apply_recommendations`` BUY_NEW writes EntryScore / EntryRegime /
       PeakPrice / LastScore; EntryRank stays -1 (D1.4 territory).
    C. ``apply_recommendations`` BUY_MORE preserves entry_* fields and
       only refreshes LastScore + PeakPrice.
    D. ``update_current_prices`` monotonically increases PeakPrice.
    E. ``HoldingsManager.holdings`` property returns the shape
       ``build_holding_snapshots`` expects.
    F. End-to-end: load_current + build_holding_snapshots works.

Run:
    cd 0316-/phase3
    python3 -m exits._smoke_d16
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

import pandas as pd  # noqa: E402

from holdings_manager import HoldingsManager, CURRENT_COLS, _CURRENT_D1_DEFAULTS  # noqa: E402
from exits.state import build_holding_snapshots  # noqa: E402


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


# ─────────────────────────────────────────────────────────────────────────────
# A. Legacy file migration
# ─────────────────────────────────────────────────────────────────────────────

def test_a_legacy_migration():
    """Simulate a pre-D1.6 holdings_log.xlsx with only the 9 original Current
    columns and verify HoldingsManager adds the 5 new ones on first open."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")

        # Write a legacy CURRENT sheet (9 cols, no D1.6 fields).
        legacy_current = pd.DataFrame([
            {"Ticker": "AAPL", "BuyDate": "2025-01-01", "BuyPrice": 150.0,
             "Shares": 10, "CurrentPrice": 160.0, "PnL_Pct": 6.67,
             "Weight": 50.0, "MarketValue": 1600.0, "Status": "ACTIVE"},
            {"Ticker": "MSFT", "BuyDate": "2025-01-05", "BuyPrice": 300.0,
             "Shares": 5, "CurrentPrice": 290.0, "PnL_Pct": -3.33,
             "Weight": 50.0, "MarketValue": 1450.0, "Status": "ACTIVE"},
        ])
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            legacy_current.to_excel(w, sheet_name="Current", index=False)
            pd.DataFrame(columns=["Date", "Ticker", "Action", "Price", "Shares",
                                  "Value", "Trigger", "Notes"]).to_excel(
                w, sheet_name="History", index=False)
            pd.DataFrame(columns=["Date", "TriggerFired"]).to_excel(
                w, sheet_name="DailyLog", index=False)
            pd.DataFrame(columns=["Date", "Ticker", "Action", "Score",
                                  "TargetPct", "ActualPct", "GapPct", "Price",
                                  "Shares", "Capital", "Regime", "GraceCount"]).to_excel(
                w, sheet_name="Recommendations", index=False)

        # Opening the file should migrate it in-place.
        hm = HoldingsManager(path)
        df = hm.load_current()

        for col in ("EntryScore", "EntryRank", "EntryRegime",
                    "PeakPrice", "LastScore"):
            _check(col in df.columns, f"A legacy file gains column {col}")

        row_aapl = df[df.Ticker == "AAPL"].iloc[0]
        # PeakPrice seeded from max(BuyPrice=150, CurrentPrice=160) = 160
        _check(float(row_aapl["PeakPrice"]) == 160.0,
               "A PeakPrice seeded from max(BuyPrice, CurrentPrice)",
               extra=f"got={row_aapl['PeakPrice']}")
        # Other D1 fields default to zero/-1/""
        _check(float(row_aapl["EntryScore"]) == 0.0,
               "A EntryScore defaults to 0.0 on migration")
        _check(int(row_aapl["EntryRank"]) == -1,
               "A EntryRank defaults to -1 on migration")
        _check(str(row_aapl["EntryRegime"]) == "",
               f"A EntryRegime defaults to empty  got='{row_aapl['EntryRegime']}'")


# ─────────────────────────────────────────────────────────────────────────────
# B. BUY_NEW populates entry_* fields
# ─────────────────────────────────────────────────────────────────────────────

def test_b_buy_new_writes_entry_fields():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")
        hm = HoldingsManager(path)
        hm.initialize_cash(10_000.0)

        recos = pd.DataFrame([
            {"Date": "2025-01-10", "Ticker": "NVDA", "Action": "BUY_NEW",
             "Score": 2.75, "TargetPct": 30.0, "ActualPct": 0.0,
             "GapPct": 30.0, "Price": 400.0, "Shares": 5, "Capital": 2000.0,
             "Regime": "BULL", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos, trigger_type="TEST",
                                 date=datetime(2025, 1, 10))

        df = hm.load_current()
        row = df[df.Ticker == "NVDA"].iloc[0]
        _check(float(row["EntryScore"]) == 2.75,
               "B EntryScore captured from reco row  got={}".format(row["EntryScore"]))
        _check(int(row["EntryRank"]) == -1,
               "B EntryRank is -1 (unknown, D1.4 will wire rank)")
        _check(str(row["EntryRegime"]) == "BULL",
               f"B EntryRegime captured  got='{row['EntryRegime']}'")
        _check(float(row["PeakPrice"]) == 400.0,
               f"B PeakPrice seeded from BUY price  got={row['PeakPrice']}")
        _check(float(row["LastScore"]) == 2.75,
               f"B LastScore == EntryScore on first buy  got={row['LastScore']}")


# ─────────────────────────────────────────────────────────────────────────────
# C. BUY_MORE preserves entry_*, refreshes LastScore + PeakPrice
# ─────────────────────────────────────────────────────────────────────────────

def test_c_buy_more_preserves_entry():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")
        hm = HoldingsManager(path)
        hm.initialize_cash(10_000.0)

        # Initial BUY_NEW
        recos1 = pd.DataFrame([
            {"Date": "2025-01-10", "Ticker": "NVDA", "Action": "BUY_NEW",
             "Score": 2.0, "TargetPct": 30.0, "ActualPct": 0.0,
             "GapPct": 30.0, "Price": 400.0, "Shares": 5, "Capital": 2000.0,
             "Regime": "BULL", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos1, trigger_type="TEST",
                                 date=datetime(2025, 1, 10))

        # Later BUY_MORE with different score, lower price
        recos2 = pd.DataFrame([
            {"Date": "2025-01-20", "Ticker": "NVDA", "Action": "BUY_MORE",
             "Score": 3.5, "TargetPct": 40.0, "ActualPct": 30.0,
             "GapPct": 10.0, "Price": 380.0, "Shares": 2, "Capital": 760.0,
             "Regime": "SIDE", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos2, trigger_type="TEST",
                                 date=datetime(2025, 1, 20))

        df = hm.load_current()
        row = df[df.Ticker == "NVDA"].iloc[0]
        # Entry fields MUST be frozen at first buy
        _check(str(row["BuyDate"])[:10] == "2025-01-10",
               f"C BuyDate (entry_date) frozen at first buy  got={row['BuyDate']}")
        _check(float(row["EntryScore"]) == 2.0,
               f"C EntryScore frozen at 2.0  got={row['EntryScore']}")
        _check(str(row["EntryRegime"]) == "BULL",
               f"C EntryRegime frozen as BULL  got='{row['EntryRegime']}'")
        # Live fields refreshed
        _check(float(row["LastScore"]) == 3.5,
               f"C LastScore refreshed to 3.5  got={row['LastScore']}")
        # PeakPrice max(400, 380) = 400 (no new high)
        _check(float(row["PeakPrice"]) == 400.0,
               f"C PeakPrice stays at 400 (BUY_MORE price lower)  got={row['PeakPrice']}")
        # Shares aggregated
        _check(int(row["Shares"]) == 7,
               f"C Shares aggregated  got={row['Shares']}")


# ─────────────────────────────────────────────────────────────────────────────
# D. update_current_prices bumps PeakPrice monotonically
# ─────────────────────────────────────────────────────────────────────────────

def test_d_update_prices_bumps_peak():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")
        hm = HoldingsManager(path)
        hm.initialize_cash(10_000.0)

        recos = pd.DataFrame([
            {"Date": "2025-01-10", "Ticker": "TSLA", "Action": "BUY_NEW",
             "Score": 1.5, "TargetPct": 20.0, "ActualPct": 0.0,
             "GapPct": 20.0, "Price": 200.0, "Shares": 5, "Capital": 1000.0,
             "Regime": "BULL", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos, trigger_type="TEST",
                                 date=datetime(2025, 1, 10))

        # Price rises to 250 → PeakPrice should update
        hm.update_current_prices({"TSLA": 250.0})
        df = hm.load_current()
        _check(float(df.iloc[0]["PeakPrice"]) == 250.0,
               f"D PeakPrice rises to 250 on up-move  got={df.iloc[0]['PeakPrice']}")

        # Price drops to 180 → PeakPrice stays at 250
        hm.update_current_prices({"TSLA": 180.0})
        df = hm.load_current()
        _check(float(df.iloc[0]["PeakPrice"]) == 250.0,
               f"D PeakPrice stays at 250 on down-move  got={df.iloc[0]['PeakPrice']}")

        # Price rises above prior peak to 270 → updates
        hm.update_current_prices({"TSLA": 270.0})
        df = hm.load_current()
        _check(float(df.iloc[0]["PeakPrice"]) == 270.0,
               f"D PeakPrice rises to new high 270  got={df.iloc[0]['PeakPrice']}")


# ─────────────────────────────────────────────────────────────────────────────
# E. .holdings property returns build_holding_snapshots-compatible dict
# ─────────────────────────────────────────────────────────────────────────────

def test_e_holdings_property_shape():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")
        hm = HoldingsManager(path)
        hm.initialize_cash(10_000.0)

        recos = pd.DataFrame([
            {"Date": "2025-01-10", "Ticker": "AMZN", "Action": "BUY_NEW",
             "Score": 1.25, "TargetPct": 25.0, "ActualPct": 0.0,
             "GapPct": 25.0, "Price": 180.0, "Shares": 3, "Capital": 540.0,
             "Regime": "DEFENSIVE", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos, trigger_type="TEST",
                                 date=datetime(2025, 1, 10))
        hm.update_current_prices({"AMZN": 195.0})

        holdings = hm.holdings
        _check(isinstance(holdings, dict) and "AMZN" in holdings,
               "E .holdings returns dict with ticker keys")

        h = holdings["AMZN"]
        expected_keys = {
            "shares", "avg_cost", "current_price",
            "entry_date", "entry_price", "entry_score", "entry_rank",
            "entry_regime", "peak_price", "last_score",
        }
        _check(expected_keys.issubset(h.keys()),
               f"E .holdings['AMZN'] has all D1 keys  missing={expected_keys - h.keys()}")
        _check(h["shares"] == 3, f"E shares=3  got={h['shares']}")
        _check(abs(h["avg_cost"] - 180.0) < 1e-9, f"E avg_cost=180  got={h['avg_cost']}")
        _check(abs(h["entry_score"] - 1.25) < 1e-9,
               f"E entry_score=1.25  got={h['entry_score']}")
        _check(h["entry_rank"] == -1,
               f"E entry_rank=-1  got={h['entry_rank']}")
        _check(h["entry_regime"] == "DEFENSIVE",
               f"E entry_regime=DEFENSIVE  got='{h['entry_regime']}'")
        _check(abs(h["peak_price"] - 195.0) < 1e-9,
               f"E peak_price=195 (after update)  got={h['peak_price']}")
        _check(abs(h["last_score"] - 1.25) < 1e-9,
               f"E last_score=1.25  got={h['last_score']}")


# ─────────────────────────────────────────────────────────────────────────────
# F. End-to-end: load_current + .holdings + build_holding_snapshots
# ─────────────────────────────────────────────────────────────────────────────

def test_f_end_to_end_snapshot():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "holdings_log.xlsx")
        hm = HoldingsManager(path)
        hm.initialize_cash(10_000.0)

        recos = pd.DataFrame([
            {"Date": "2025-01-10", "Ticker": "GOOG", "Action": "BUY_NEW",
             "Score": 0.9, "TargetPct": 20.0, "ActualPct": 0.0,
             "GapPct": 20.0, "Price": 140.0, "Shares": 10, "Capital": 1400.0,
             "Regime": "SIDE", "GraceCount": 0},
        ])
        hm.apply_recommendations(recos, trigger_type="TEST",
                                 date=datetime(2025, 1, 10))
        hm.update_current_prices({"GOOG": 155.0})

        current = hm.load_current()
        holdings_dict = hm.holdings
        snaps = build_holding_snapshots(
            current=current,
            holdings_store=holdings_dict,
            score_map={"GOOG": 1.1},
            rank_map={"GOOG": 3},
            today="2025-01-20",
        )
        _check(len(snaps) == 1, f"F snapshot produced  n={len(snaps)}")
        s = snaps[0]
        _check(s.ticker == "GOOG", "F snapshot ticker=GOOG")
        _check(s.entry_date == "2025-01-10",
               f"F snapshot entry_date from BuyDate  got='{s.entry_date}'")
        _check(abs(s.entry_score - 0.9) < 1e-9,
               f"F snapshot entry_score from holdings dict  got={s.entry_score}")
        _check(s.entry_regime == "SIDE",
               f"F snapshot entry_regime  got='{s.entry_regime}'")
        _check(abs(s.peak_price - 155.0) < 1e-9,
               f"F snapshot peak_price reflects update_current_prices  got={s.peak_price}")
        _check(abs(s.current_score - 1.1) < 1e-9,
               f"F snapshot current_score from score_map  got={s.current_score}")
        _check(s.current_rank == 3,
               f"F snapshot current_rank from rank_map  got={s.current_rank}")
        # Days held: 2025-01-10 → 2025-01-20 = 10 days
        _check(s.days_held == 10,
               f"F snapshot days_held = 10  got={s.days_held}")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    test_a_legacy_migration()
    test_b_buy_new_writes_entry_fields()
    test_c_buy_more_preserves_entry()
    test_d_update_prices_bumps_peak()
    test_e_holdings_property_shape()
    test_f_end_to_end_snapshot()
    print(f"\n===== D1.6 Smoke: {_PASS} pass, {_FAIL} fail =====")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
