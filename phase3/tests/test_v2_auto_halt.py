"""V2-B (G3) — auto-lockout tests (fire history + evaluate + apply)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import auto_halt  # noqa: E402
from phase3.autotrade import global_halt  # noqa: E402


def _trade(rc, total=None, scoring=None):
    return {"kind": "trade", "rc": rc, "total_after": total,
            "scoring_date": scoring}


def _skip(scoring=None):
    return {"kind": "skip", "rc": 0, "total_after": None,
            "scoring_date": scoring}


_SESSION = date(2026, 6, 2)


class TestHistoryIO(unittest.TestCase):

    def test_record_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            auto_halt.record_fire(run_id="r1", session_date_kst="2026-06-01",
                                  kind="trade", rc=0, total_after=100.0,
                                  scoring_date="2026-05-29", runtime_dir=rt)
            auto_halt.record_fire(run_id="r2", session_date_kst="2026-06-02",
                                  kind="skip", rc=0, runtime_dir=rt)
            hist = auto_halt.read_history(runtime_dir=rt)
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[0]["run_id"], "r1")
            self.assertEqual(hist[1]["kind"], "skip")

    def test_malformed_line_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            p = auto_halt.history_path(runtime_dir=rt)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"kind":"trade","rc":0}\nGARBAGE\n', encoding="utf-8")
            self.assertEqual(len(auto_halt.read_history(runtime_dir=rt)), 1)


class TestConsecutiveFailures(unittest.TestCase):

    def test_three_fails_trips(self):
        hist = [_trade(2), _trade(1), _trade(2)]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertTrue(d.tripped)
        self.assertIn("consecutive", d.message)

    def test_two_fails_does_not_trip(self):
        hist = [_trade(0), _trade(2), _trade(1)]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertFalse(d.tripped)

    def test_skips_do_not_count_as_failures(self):
        # Two fails, then skips interspersed, then a fail: still 3 fails.
        hist = [_trade(2), _skip(), _trade(1), _skip(), _trade(2)]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertTrue(d.tripped)

    def test_success_resets_streak(self):
        hist = [_trade(2), _trade(2), _trade(0), _trade(2)]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertFalse(d.tripped)


class TestDrawdown(unittest.TestCase):

    def test_drawdown_trips_at_threshold(self):
        # start 100k, now 79k -> -21% -> trips
        hist = [_trade(0, total=100000.0, scoring="2026-06-02"),
                _trade(0, total=79000.0, scoring="2026-06-02")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertTrue(d.tripped)
        self.assertIn("down", d.message)

    def test_mild_drawdown_does_not_trip(self):
        hist = [_trade(0, total=100000.0, scoring="2026-06-02"),
                _trade(0, total=90000.0, scoring="2026-06-02")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertFalse(d.tripped)

    def test_explicit_baseline_used(self):
        hist = [_trade(0, total=80000.0, scoring="2026-06-02")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION,
                                       baseline_equity=100000.0)
        self.assertTrue(d.tripped)


class TestStale(unittest.TestCase):

    def test_fresh_does_not_trip(self):
        # scoring 6/1, session 6/2: lag 0 -> fresh
        hist = [_trade(0, total=100000.0, scoring="2026-06-01")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertFalse(d.tripped)

    def test_one_skipped_day_tolerated(self):
        # scoring 5/29 (Fri), session 6/2 (Tue): 6/1 skipped -> lag 1, tol 1
        hist = [_trade(0, total=100000.0, scoring="2026-05-29")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertFalse(d.tripped)

    def test_two_skipped_days_trips(self):
        # scoring 5/28 (Thu), session 6/2: 5/29 + 6/1 skipped -> lag 2 > 1
        hist = [_trade(0, total=100000.0, scoring="2026-05-28")]
        d = auto_halt.evaluate_lockout(hist, session_date=_SESSION)
        self.assertTrue(d.tripped)
        self.assertIn("stale", d.message.lower() + "stale")  # reason present


class TestApply(unittest.TestCase):

    def test_apply_latches_halt_once(self):
        with tempfile.TemporaryDirectory() as td:
            hp = Path(td) / "global_halt.json"
            dec = auto_halt.LockoutDecision(tripped=True, reasons=["x"])
            p = auto_halt.apply_lockout(dec, halt_path=hp)
            self.assertIsNotNone(p)
            self.assertTrue(global_halt.is_halted(hp))
            # idempotent: already halted -> no-op
            self.assertIsNone(auto_halt.apply_lockout(dec, halt_path=hp))

    def test_apply_noop_when_not_tripped(self):
        with tempfile.TemporaryDirectory() as td:
            hp = Path(td) / "global_halt.json"
            dec = auto_halt.LockoutDecision(tripped=False)
            self.assertIsNone(auto_halt.apply_lockout(dec, halt_path=hp))
            self.assertFalse(global_halt.is_halted(hp))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
