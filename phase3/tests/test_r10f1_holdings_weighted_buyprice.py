"""R10F-1 — HoldingsManager.apply_partial_execution must recompute
the share-weighted BuyPrice when a BUY_MORE lands on an existing
position.

Background
----------

Through R10E the live flow was:

* paper_submit fills BUY / BUY_MORE on the broker side
* t10_applicator calls HoldingsManager.apply_partial_execution(...)
* Current sheet's Shares column grows, **but BuyPrice is left at the
  original entry price**.

The 5/19 MRNA backfill that R10F-2 surfaced was the same class of
bug surfacing in reconcile output: PnL_Pct on the Current sheet
was drifting away from reality every time a BUY_MORE landed.

R10F-1 fix
----------

For BUY_MORE on an existing row, recompute BuyPrice as:

    new_buy = (old_shares * old_buy + new_shares * new_price)
              / (old_shares + new_shares)

Rounded to 4 dp to match the other prices on the sheet.

This file pins:

1. Happy path — BUY_MORE on an existing position updates BuyPrice
   to the share-weighted average and bumps Shares correctly.
2. Fractional / repeating decimal — the rounding stays at 4 dp.
3. Initial BUY (mask empty) — falls through the existing add-row
   path with the new fill price as BuyPrice; behaviour unchanged.
4. BUY_NEW on an existing ticker — also recomputed (a BUY_NEW that
   piles onto an existing position is rare but legal in the live
   flow; we treat it like BUY_MORE).
5. Sequence of BUY_MORE rows on the same ticker — the weighted
   average chains correctly.
6. Defensive: if the existing BuyPrice is 0 / NaN (legacy holdings,
   migration corner case), the new fill price becomes the cost
   basis (no division-by-zero, no NaN propagation).
7. History row is still emitted with the *fill* price, not the
   weighted average — History tracks each fill, Current tracks the
   running average. The two views must never converge.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.holdings_manager import HoldingsManager


def _new_manager(td: str) -> HoldingsManager:
    return HoldingsManager(str(Path(td) / "holdings_log.xlsx"))


def _seed_current(hm: HoldingsManager, *, ticker: str, shares: int,
                  buy_price: float, current_price: float = None) -> None:
    """Seed the Current sheet with a single live position. We round-
    trip through ``_write_sheets`` so subsequent loads see the same
    shape the production flow does."""
    cur_p = current_price if current_price is not None else buy_price
    row = {
        "Ticker": ticker,
        "BuyDate": "2026-04-01",
        "BuyPrice": buy_price,
        "Shares": shares,
        "CurrentPrice": cur_p,
        "PnL_Pct": 0.0,
        "Weight": 100.0,
        "MarketValue": round(cur_p * shares, 2),
        "Status": "ACTIVE",
        "EntryScore": 0.0,
        "EntryRank": -1,
        "EntryRegime": "",
        "PeakPrice": cur_p,
        "LastScore": 0.0,
        "ProfitTargetsHit": "",
    }
    hm._write_sheets({"Current": pd.DataFrame([row])})


def _exec_row(*, action: str, ticker: str, price: float,
              shares: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "Action": action, "Ticker": ticker,
        "Price": price, "Shares": shares,
        "Score": 0.0, "Regime": "", "Rank": -1,
    }])


# ──────────────────────────────────────────────────────────────────────
# 1. Happy path
# ──────────────────────────────────────────────────────────────────────
class TestBuyMoreWeightedAverage(unittest.TestCase):

    def test_buy_more_recomputes_buy_price_as_weighted_average(self):
        """The 5/19 MRNA case (already manually backfilled): 2 @ 50.0
        + 2 @ 40.0 → 4 shares with BuyPrice = (2*50+2*40)/4 = 45.0."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="MRNA", shares=2, buy_price=50.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="MRNA",
                          price=40.0, shares=2)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "MRNA"].iloc[0]
            self.assertEqual(int(row["Shares"]), 4)
            self.assertAlmostEqual(float(row["BuyPrice"]), 45.0, places=4)
            self.assertAlmostEqual(float(row["CurrentPrice"]), 40.0)
            # MarketValue tracks live, not cost basis
            self.assertAlmostEqual(float(row["MarketValue"]), 4 * 40.0)

    def test_buy_more_keeps_buy_date_unchanged(self):
        """Weighted-average accounting preserves the original entry
        date. A trader who wants 'last fill date' should query
        History; Current carries the cost basis story."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="MRNA", shares=2, buy_price=50.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="MRNA",
                          price=40.0, shares=2)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "MRNA"].iloc[0]
            self.assertEqual(str(row["BuyDate"])[:10], "2026-04-01")

    def test_rounding_to_4dp_for_repeating_decimal(self):
        """3 @ 100.0 + 2 @ 110.0 → (300+220)/5 = 104.0 (exact).
        3 @ 100.0 + 4 @ 110.0 → 740/7 = 105.714285…
        4-dp rounding mirrors how the rest of the sheet stores prices."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="VRT", shares=3, buy_price=100.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="VRT",
                          price=110.0, shares=4)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "VRT"].iloc[0]
            self.assertEqual(int(row["Shares"]), 7)
            self.assertAlmostEqual(float(row["BuyPrice"]), 105.7143, places=4)


# ──────────────────────────────────────────────────────────────────────
# 2. Initial BUY (no existing row) still works
# ──────────────────────────────────────────────────────────────────────
class TestInitialBuyUnchanged(unittest.TestCase):

    def test_first_buy_uses_fill_price_as_cost_basis(self):
        """Behaviour must not change for the 'no prior row' path —
        only the BUY_MORE / existing-row branch was buggy."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            # No seed.
            hm.apply_partial_execution(
                _exec_row(action="BUY", ticker="MRNA",
                          price=47.5, shares=2)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "MRNA"].iloc[0]
            self.assertEqual(int(row["Shares"]), 2)
            self.assertAlmostEqual(float(row["BuyPrice"]), 47.5)


# ──────────────────────────────────────────────────────────────────────
# 3. BUY_NEW piling onto an existing ticker
# ──────────────────────────────────────────────────────────────────────
class TestBuyNewOnExistingTicker(unittest.TestCase):

    def test_buy_new_also_recomputes_weighted_average(self):
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="VRT", shares=5, buy_price=300.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_NEW", ticker="VRT",
                          price=320.0, shares=5)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "VRT"].iloc[0]
            self.assertEqual(int(row["Shares"]), 10)
            self.assertAlmostEqual(float(row["BuyPrice"]), 310.0, places=4)


# ──────────────────────────────────────────────────────────────────────
# 4. Sequence of BUY_MORE chains
# ──────────────────────────────────────────────────────────────────────
class TestBuyMoreChain(unittest.TestCase):

    def test_two_consecutive_buy_more_chains_correctly(self):
        """Sequence: seed 1 @ 100, then BUY_MORE 1 @ 200, then BUY_MORE
        2 @ 50. After both:
          shares = 4
          buy_price = (1*100 + 1*200 + 2*50) / 4 = 400/4 = 100.0
        """
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="APA", shares=1, buy_price=100.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="APA",
                          price=200.0, shares=1)
            )
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="APA",
                          price=50.0, shares=2)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "APA"].iloc[0]
            self.assertEqual(int(row["Shares"]), 4)
            self.assertAlmostEqual(float(row["BuyPrice"]), 100.0, places=4)


# ──────────────────────────────────────────────────────────────────────
# 5. Defensive: legacy row with BuyPrice=0
# ──────────────────────────────────────────────────────────────────────
class TestDefensiveZeroBuyPrice(unittest.TestCase):

    def test_zero_buy_price_falls_back_to_fill_price(self):
        """A migrated / hand-edited Current row may have BuyPrice=0
        (column added by migration but never populated). The fix
        must seed BuyPrice from the new fill rather than dividing
        zero into the weighted average."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="ZZZ", shares=3, buy_price=0.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="ZZZ",
                          price=42.0, shares=2)
            )
            cur = hm.load_current()
            row = cur[cur["Ticker"] == "ZZZ"].iloc[0]
            self.assertEqual(int(row["Shares"]), 5)
            self.assertAlmostEqual(float(row["BuyPrice"]), 42.0, places=4)


# ──────────────────────────────────────────────────────────────────────
# 6. History sheet still records the *fill* price
# ──────────────────────────────────────────────────────────────────────
class TestHistorySheetRecordsFillPrice(unittest.TestCase):

    def test_history_row_uses_fill_price_not_weighted_average(self):
        """History is the per-fill audit trail. Even after R10F-1
        bumps Current's BuyPrice to the weighted average, the
        History row for the BUY_MORE must still carry the actual
        broker fill price."""
        with tempfile.TemporaryDirectory() as td:
            hm = _new_manager(td)
            _seed_current(hm, ticker="MRNA", shares=2, buy_price=50.0)
            hm.apply_partial_execution(
                _exec_row(action="BUY_MORE", ticker="MRNA",
                          price=40.0, shares=2)
            )
            hist = hm.load_history()
            row = hist[(hist["Ticker"] == "MRNA")
                        & (hist["Action"] == "BUY_MORE")].iloc[0]
            self.assertAlmostEqual(float(row["Price"]), 40.0)
            self.assertEqual(int(row["Shares"]), 2)
            self.assertAlmostEqual(float(row["Value"]), 80.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
