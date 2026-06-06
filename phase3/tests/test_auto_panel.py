"""Unit tests for the simple autotrade dashboard pure helpers
(``phase3.autotrade.auto_panel``).

Covers the log→outcome parsing (incl. the 07:20-KST-is-previous-UTC-day
quirk), the per-fire calendar status state machine, the natural-language
status sentences, and the day-over-day portfolio computation. No Tk.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
for _p in (_PHASE3, _PHASE3.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import auto_panel as ap

KST = ap.KST


class TestKstConversion(unittest.TestCase):
    def test_t7_morning_maps_to_next_calendar_day(self):
        # 07:20 KST fire is logged at 22:20 UTC the previous day.
        self.assertEqual(ap.to_kst_date("2026-06-04T22:20:00+00:00"),
                         date(2026, 6, 5))

    def test_trade_evening_same_day(self):
        self.assertEqual(ap.to_kst_date("2026-06-05T13:35:00+00:00"),
                         date(2026, 6, 5))

    def test_bad_timestamp(self):
        self.assertIsNone(ap.to_kst_date("not-a-time"))


class TestParseFireLog(unittest.TestCase):
    def test_finished_and_skip_and_last_wins(self):
        text = "\n".join([
            "[v1] 2026-06-04T22:20:00+00:00 T7 prefetch FINISHED — rc=0 duration=10s",
            "[v1] 2026-06-05T13:35:00+00:00 V1 pipeline FINISHED — rc=2 halt_reason='x'",
            "[v1] 2026-06-05T13:40:00+00:00 V1 pipeline FINISHED — rc=0 halt_reason=None",
            "[v1] 2026-06-06T13:35:00+00:00 halt gate: ... SKIP — exit rc=0",
            "noise line without prefix",
        ])
        out = ap.parse_fire_log(text)
        self.assertEqual(out[date(2026, 6, 5)].kind, "done")
        # last terminal event for 6/5 wins (rc=0, not the earlier rc=2)
        self.assertEqual(out[date(2026, 6, 5)].rc, 0)
        self.assertEqual(out[date(2026, 6, 5)].ts_kst.hour, 22)
        self.assertEqual(out[date(2026, 6, 6)].kind, "skip")
        self.assertIn(date(2026, 6, 5), out)

    def test_intermediate_t7_lines_ignored(self):
        text = "\n".join([
            "[v1] 2026-06-04T22:20:00+00:00 [t7] finished rc=0 in 5s",   # not terminal
            "[v1] 2026-06-04T22:22:00+00:00 T7 prefetch FINISHED — rc=0 d=1s",
        ])
        out = ap.parse_fire_log(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[date(2026, 6, 5)].rc, 0)


class TestFireStatus(unittest.TestCase):
    def setUp(self):
        # "now" = 2026-06-08 12:00 KST (after 07:20, before 22:35).
        self.now = datetime(2026, 6, 8, 12, 0, tzinfo=KST)

    def _st(self, **kw):
        base = dict(
            day=date(2026, 6, 8), fire="t7", outcome=None, now_kst=self.now,
            is_trading=True, installed=True, halted=False, standing_armed=True,
            running=False, history_start=date(2026, 5, 27),
        )
        base.update(kw)
        return ap.fire_status(**base)

    def test_done(self):
        oc = ap.FireOutcome(date(2026, 6, 8), "done", 0, self.now)
        self.assertEqual(self._st(outcome=oc), "done")

    def test_fail(self):
        oc = ap.FireOutcome(date(2026, 6, 8), "done", 2, self.now)
        self.assertEqual(self._st(outcome=oc), "fail")

    def test_skip(self):
        oc = ap.FireOutcome(date(2026, 6, 8), "skip", 0, self.now)
        self.assertEqual(self._st(outcome=oc), "skip")

    def test_running_overrides(self):
        self.assertEqual(self._st(running=True), "running")

    def test_t7_already_past_no_record_is_missing(self):
        # 07:20 already passed at 12:00 → missing (within recorded history).
        self.assertEqual(self._st(fire="t7"), "missing")

    def test_trade_future_scheduled(self):
        # 22:35 not yet reached → scheduled.
        self.assertEqual(self._st(fire="trade"), "scheduled")

    def test_trade_future_blocked_when_unarmed(self):
        self.assertEqual(self._st(fire="trade", standing_armed=False), "blocked")

    def test_blocked_when_halted(self):
        self.assertEqual(self._st(fire="trade", halted=True), "blocked")

    def test_blocked_when_not_installed(self):
        self.assertEqual(self._st(fire="trade", installed=False), "blocked")

    def test_closed_non_trading_trade(self):
        self.assertEqual(self._st(fire="trade", is_trading=False), "closed")

    def test_preview_non_trading_t7_done(self):
        oc = ap.FireOutcome(date(2026, 6, 8), "done", 0, self.now)
        self.assertEqual(self._st(fire="t7", is_trading=False, outcome=oc), "preview")

    def test_pre_history_is_none_not_missing(self):
        # A trading day before logging started should be blank, not red.
        self.assertEqual(
            self._st(fire="t7", day=date(2026, 5, 20),
                     history_start=date(2026, 5, 27)), "none")


class TestBuildCalendar(unittest.TestCase):
    def test_range_and_today_and_prehistory(self):
        today = date(2026, 6, 8)
        now = datetime(2026, 6, 8, 12, 0, tzinfo=KST)
        t7 = {date(2026, 6, 8): ap.FireOutcome(date(2026, 6, 8), "done", 0, now)}
        trade = {date(2026, 6, 5): ap.FireOutcome(date(2026, 6, 5), "done", 0, now)}
        # everything is a trading day to isolate history-start logic
        cells = ap.build_calendar(
            today=today, now_kst=now, t7_outcomes=t7, trade_outcomes=trade,
            t7_installed=True, trade_installed=True, halted=False,
            standing_armed=True, days_back=5, days_fwd=5,
            is_trading_day_fn=lambda d: True,
        )
        self.assertEqual(len(cells), 11)
        tcell = [c for c in cells if c.is_today][0]
        self.assertEqual(tcell.day, today)
        self.assertEqual(tcell.t7_status, "done")
        # Days before the earliest recorded trade outcome (6/5) → none, not missing
        early = [c for c in cells if c.day < date(2026, 6, 5)]
        self.assertTrue(all(c.trade_status == "none" for c in early))


class TestNaturalLanguage(unittest.TestCase):
    def test_running_sentence(self):
        today = date(2026, 6, 8)
        now = datetime(2026, 6, 8, 7, 25, tzinfo=KST)
        lines = ap.natural_language_status(
            today=today, now_kst=now, is_trading=True,
            t7_status="running", trade_status="scheduled",
            t7_outcome=None, trade_outcome=None,
            running_fire="t7", running_stage="t7_generate",
            running_elapsed_s=42,
        )
        joined = "\n".join(lines)
        self.assertIn("실행 중", joined)
        self.assertIn("t7_generate", joined)
        self.assertIn("예약 대기", joined)

    def test_done_with_recos(self):
        today = date(2026, 6, 8)
        now = datetime(2026, 6, 8, 23, 0, tzinfo=KST)
        oc7 = ap.FireOutcome(today, "done", 0, datetime(2026, 6, 8, 7, 24, tzinfo=KST))
        oct = ap.FireOutcome(today, "done", 0, datetime(2026, 6, 8, 22, 42, tzinfo=KST))
        lines = ap.natural_language_status(
            today=today, now_kst=now, is_trading=True,
            t7_status="done", trade_status="done",
            t7_outcome=oc7, trade_outcome=oct, t7_recos=21,
        )
        joined = "\n".join(lines)
        self.assertIn("추천 21종목", joined)
        self.assertIn("07:24", joined)
        self.assertIn("22:42", joined)


class _StubHM:
    def __init__(self, df):
        self._df = df

    def load_daily_log(self):
        return self._df


class TestDoD(unittest.TestCase):
    def test_prefers_total_capital(self):
        df = pd.DataFrame({
            "Date": ["2026-06-04", "2026-06-05"],
            "PortfolioValue": [10000.0, 40000.0],   # misleading (cash deployed)
            "TotalCapital": [100000.0, 100150.0],   # true equity
        })
        dod, last_d, prev_d = ap._dod_from_daily_log(_StubHM(df))
        self.assertAlmostEqual(dod, 0.15, places=2)
        self.assertEqual(last_d, "2026-06-05")
        self.assertEqual(prev_d, "2026-06-04")

    def test_falls_back_to_portfolio_value(self):
        df = pd.DataFrame({
            "Date": ["2026-06-04", "2026-06-05"],
            "PortfolioValue": [100.0, 110.0],
        })
        dod, _, _ = ap._dod_from_daily_log(_StubHM(df))
        self.assertAlmostEqual(dod, 10.0, places=2)

    def test_single_row_no_dod(self):
        df = pd.DataFrame({"Date": ["2026-06-05"], "TotalCapital": [100.0]})
        dod, last_d, prev_d = ap._dod_from_daily_log(_StubHM(df))
        self.assertIsNone(dod)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
