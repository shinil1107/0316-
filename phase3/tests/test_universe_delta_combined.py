"""Combined Top-N Universe-Delta table (single table, ascending by rank,
with a Movement column) — readability refactor of ``_print_universe_delta``.

Pins the operator-facing contract:

1. One table with every ticker in today's top-N, ascending by rank (no more
   separate REMAIN / GET_IN sub-tables to bounce between).
2. Movement labels are attached per row.
3. GET_OUT (dropped) tickers are listed compactly below.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
for _p in (_PHASE3, _PHASE3.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import daily_runner as dr


class _Cfg:
    regime_bull_top_n = 3
    regime_defensive_top_n = 2
    regime_side_top_n = 2


class _StubHM:
    def __init__(self, prev_recos: pd.DataFrame):
        self._prev = prev_recos

    def load_prev_day_recos(self, today_str=None):
        return self._prev

    def load_recommendations(self):
        return pd.DataFrame()


def _scores():
    return pd.DataFrame({
        "Ticker": ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "Score": [90.0, 80.0, 70.0, 60.0, 50.0],
        "Price": [10.0, 11.0, 12.0, 13.0, 14.0],
    })


def _prev_recos():
    # Yesterday's top: BBB#1, AAA#2, DDD#3  → DDD drops out today.
    return pd.DataFrame({
        "Date": ["2026-06-04"] * 3,
        "Action": ["HOLD", "HOLD", "HOLD"],
        "Ticker": ["BBB", "AAA", "DDD"],
        "Score": [99.0, 98.0, 97.0],
    })


def _labels():
    return pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC"],
        "primary_label": ["RISING", "FALLING", "SIDEWAYS"],
        "tags": ["", "", "NEW_ENTRY"],
    })


class TestCombinedTable(unittest.TestCase):

    def setUp(self):
        self.txt = dr._print_universe_delta(
            _scores(), _StubHM(_prev_recos()), _Cfg(), "BULL",
            labels_df=_labels(), verbose=False,
        )

    def test_single_table_one_header(self):
        # Exactly one combined table header, not separate REMAIN/GET_IN ones.
        self.assertEqual(self.txt.count("Rank"), 1)
        self.assertNotIn("GET_IN (new)", self.txt)

    def test_rows_ascending_by_rank(self):
        lines = self.txt.splitlines()
        order = [ln.split()[1] for ln in lines
                 if ln.strip().split() and ln.strip().split()[0] in ("1", "2", "3")]
        self.assertEqual(order, ["AAA", "BBB", "CCC"])

    def test_new_entry_marked(self):
        ccc = [l for l in self.txt.splitlines() if " CCC " in l][0]
        self.assertIn("NEW", ccc)
        self.assertIn("NEW_ENTRY", ccc)

    def test_movement_and_direction_markers(self):
        aaa = [l for l in self.txt.splitlines() if " AAA " in l][0]
        bbb = [l for l in self.txt.splitlines() if " BBB " in l][0]
        self.assertIn("\u25b2", aaa)
        self.assertIn("RISING", aaa)
        self.assertIn("\u25bc", bbb)
        self.assertIn("FALLING", bbb)

    def test_get_out_listed_below(self):
        self.assertIn("GET_OUT", self.txt)
        self.assertIn("DDD", self.txt)
        # DDD must NOT appear in the main ranked rows (it left the top-N).
        ranked = [l for l in self.txt.splitlines()
                  if l[:4].strip().isdigit()]
        self.assertFalse(any(" DDD " in l for l in ranked))


class TestMovementCell(unittest.TestCase):

    def test_directional_primary(self):
        self.assertEqual(dr._movement_cell("RISING", ""), "RISING")
        self.assertEqual(dr._movement_cell("FALLING", ""), "FALLING")

    def test_tags_appended(self):
        self.assertEqual(
            dr._movement_cell("RISING", "FAST_RISER,NEW_ENTRY"),
            "RISING FAST_RISER NEW_ENTRY")

    def test_sideways_quiet(self):
        self.assertEqual(dr._movement_cell("SIDEWAYS", ""), "")

    def test_core_stable_neutral_shown(self):
        self.assertEqual(dr._movement_cell("SIDEWAYS", "CORE_STABLE"), "CORE_STABLE")

    def test_choppy_regime_noise_dropped(self):
        # CHOPPY / REGIME_SWITCHED are not surfaced in the compact cell.
        self.assertEqual(dr._movement_cell("SIDEWAYS", "CHOPPY,REGIME_SWITCHED"), "")
        self.assertEqual(
            dr._movement_cell("RISING", "CHOPPY,REGIME_SWITCHED"), "RISING")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
