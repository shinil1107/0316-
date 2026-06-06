"""V2-A (G1) — standing (continuous) arm tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_arm  # noqa: E402


class TestStandingArm(unittest.TestCase):

    def test_write_read_clear_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            self.assertIsNone(v1_arm.read_standing_arm(runtime_dir=rt))
            p = v1_arm.write_standing_arm(runtime_dir=rt, note="go live")
            self.assertTrue(p.exists())
            payload = v1_arm.read_standing_arm(runtime_dir=rt)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["note"], "go live")
            self.assertIn("armed_at", payload)
            self.assertTrue(v1_arm.clear_standing_arm(runtime_dir=rt))
            self.assertFalse(p.exists())
            # second clear is a no-op
            self.assertFalse(v1_arm.clear_standing_arm(runtime_dir=rt))

    def test_rearm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt, note="first")
            v1_arm.write_standing_arm(runtime_dir=rt, note="second")
            payload = v1_arm.read_standing_arm(runtime_dir=rt)
            self.assertEqual(payload["note"], "second")

    def test_malformed_standing_token_reads_none(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            p = v1_arm.standing_token_path(runtime_dir=rt)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(v1_arm.read_standing_arm(runtime_dir=rt))


class TestGatePrecedence(unittest.TestCase):

    def _now(self):
        return datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)

    def test_standing_arm_satisfies_gate(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            res = v1_arm.require_armed_for_today(runtime_dir=rt,
                                                 now=self._now())
            self.assertTrue(res.ok)
            self.assertEqual(res.mode, "standing")

    def test_no_token_not_armed(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            res = v1_arm.require_armed_for_today(runtime_dir=rt,
                                                 now=self._now())
            self.assertFalse(res.ok)
            self.assertEqual(res.mode, "")

    def test_daily_token_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            d = v1_arm.today_kst(now=self._now())
            v1_arm.write_arm_token(date_kst=d, runtime_dir=rt)
            res = v1_arm.require_armed_for_today(runtime_dir=rt,
                                                 now=self._now())
            self.assertTrue(res.ok)
            self.assertEqual(res.mode, "daily")

    def test_standing_takes_precedence_over_missing_daily(self):
        # Standing arm present, no daily token for today -> still armed.
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td)
            v1_arm.write_standing_arm(runtime_dir=rt)
            res = v1_arm.require_armed_for_today(runtime_dir=rt,
                                                 now=self._now())
            self.assertTrue(res.ok)
            self.assertEqual(res.mode, "standing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
