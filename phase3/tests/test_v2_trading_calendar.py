"""V2-A (G2) — NYSE trading-calendar tests.

Pins the 2026/2027 NYSE full-day closure set (incl. weekend-observance
and Good Friday) so the gate cannot silently skip a real trading day or
trade on a holiday.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import trading_calendar as tc  # noqa: E402


# Source: NYSE published holiday calendars.
_NYSE_2026 = {
    date(2026, 1, 1),    # New Year's Day (Thu)
    date(2026, 1, 19),   # MLK (3rd Mon Jan)
    date(2026, 2, 16),   # Presidents (3rd Mon Feb)
    date(2026, 4, 3),    # Good Friday (Easter Apr 5 2026)
    date(2026, 5, 25),   # Memorial (last Mon May)
    date(2026, 6, 19),   # Juneteenth (Fri)
    date(2026, 7, 3),    # Independence observed (Jul 4 2026 = Sat -> Fri)
    date(2026, 9, 7),    # Labor (1st Mon Sep)
    date(2026, 11, 26),  # Thanksgiving (4th Thu Nov)
    date(2026, 12, 25),  # Christmas (Fri)
}

_NYSE_2027 = {
    date(2027, 1, 1),    # New Year (Fri)
    date(2027, 1, 18),   # MLK
    date(2027, 2, 15),   # Presidents
    date(2027, 3, 26),   # Good Friday (Easter Mar 28 2027)
    date(2027, 5, 31),   # Memorial
    date(2027, 6, 18),   # Juneteenth observed (Jun 19 2027 = Sat -> Fri)
    date(2027, 7, 5),    # Independence observed (Jul 4 2027 = Sun -> Mon)
    date(2027, 9, 6),    # Labor
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas observed (Dec 25 2027 = Sat -> Fri)
}


class TestHolidaySet(unittest.TestCase):

    def test_2026_holiday_set_exact(self):
        self.assertEqual(tc.nyse_holidays(2026), _NYSE_2026)

    def test_2027_holiday_set_exact(self):
        self.assertEqual(tc.nyse_holidays(2027), _NYSE_2027)


class TestIsTradingDay(unittest.TestCase):

    def test_holidays_are_not_trading(self):
        for d in _NYSE_2026 | _NYSE_2027:
            self.assertFalse(tc.is_trading_day(d), f"{d} should be closed")

    def test_weekends_are_not_trading(self):
        self.assertFalse(tc.is_trading_day(date(2026, 5, 30)))  # Sat
        self.assertFalse(tc.is_trading_day(date(2026, 5, 31)))  # Sun

    def test_normal_weekdays_are_trading(self):
        self.assertTrue(tc.is_trading_day(date(2026, 6, 1)))   # Mon
        self.assertTrue(tc.is_trading_day(date(2026, 6, 2)))   # Tue (tonight)
        self.assertTrue(tc.is_trading_day(date(2026, 7, 2)))   # Thu before Jul3
        self.assertTrue(tc.is_trading_day(date(2026, 7, 6)))   # Mon after Jul4


class TestNeighbours(unittest.TestCase):

    def test_prev_skips_weekend(self):
        # Monday 6/1 -> previous trading day is Friday 5/29.
        self.assertEqual(
            tc.previous_trading_day(date(2026, 6, 1)), date(2026, 5, 29))

    def test_prev_skips_holiday(self):
        # Day after Christmas 2026 (Fri 12/25 closed): Mon 12/28 -> prev is
        # Thu 12/24.
        self.assertEqual(
            tc.previous_trading_day(date(2026, 12, 28)), date(2026, 12, 24))

    def test_next_skips_weekend_and_holiday(self):
        # Fri 7/3 2026 closed (Independence observed) -> next after Thu 7/2
        # is Mon 7/6.
        self.assertEqual(
            tc.next_trading_day(date(2026, 7, 2)), date(2026, 7, 6))


class TestTradingDaysBetween(unittest.TestCase):

    def test_healthy_lag_is_zero(self):
        # scoring 5/29 (Fri), session 6/1 (Mon): nothing skipped in between.
        self.assertEqual(
            tc.trading_days_between(date(2026, 5, 29), date(2026, 6, 1)), 0)

    def test_one_skipped_trading_day(self):
        # scoring 5/28 (Thu), session 6/1 (Mon): 5/29 was skipped -> 1.
        self.assertEqual(
            tc.trading_days_between(date(2026, 5, 28), date(2026, 6, 1)), 1)

    def test_same_or_reversed_is_zero(self):
        self.assertEqual(
            tc.trading_days_between(date(2026, 6, 1), date(2026, 6, 1)), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
