"""V1-E.1 — Unit tests for ``phase3.autotrade.v1_arm``.

Pinned invariants:

* ``today_kst`` is computed from UTC + offset, not from
  ``datetime.now()``; a Mac in Berkeley still reports the
  Korean trading-day date.
* ``write_arm_token`` is atomic (``.tmp`` + replace); a partial
  write must never leave a half-token that the gate would mistake
  for "armed".
* ``read_arm_token`` returns ``None`` (not an exception) on a
  malformed JSON file — the launchd gate must fail CLOSED.
* ``require_armed_for_today`` returns a structured outcome with the
  exact reason string the operator will see in the log.
* ``gc_old_tokens`` never deletes today's token (race-safety).
* Filename schema is locked: ``v1_armed_<YYYY-MM-DD>.json`` —
  changing it would silently break installs in the wild.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_arm


# ──────────────────────────────────────────────────────────────────────
# today_kst — boundary cases that bite Macs traveling between zones
# ──────────────────────────────────────────────────────────────────────
class TestTodayKst(unittest.TestCase):

    def test_kst_offset_is_9_hours(self):
        # 2026-05-26 14:59 UTC == 2026-05-26 23:59 KST
        n = datetime(2026, 5, 26, 14, 59, 0, tzinfo=timezone.utc)
        self.assertEqual(v1_arm.today_kst(now=n), "2026-05-26")

    def test_kst_rolls_over_at_15_00_utc(self):
        # 2026-05-26 14:59:59 UTC → still 2026-05-26 KST
        # 2026-05-26 15:00:00 UTC → 2026-05-27 KST
        before = datetime(2026, 5, 26, 14, 59, 59,
                          tzinfo=timezone.utc)
        after = datetime(2026, 5, 26, 15, 0, 0,
                         tzinfo=timezone.utc)
        self.assertEqual(v1_arm.today_kst(now=before), "2026-05-26")
        self.assertEqual(v1_arm.today_kst(now=after), "2026-05-27")

    def test_returns_iso_date_format(self):
        n = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
        self.assertRegex(v1_arm.today_kst(now=n),
                         r"^\d{4}-\d{2}-\d{2}$")


# ──────────────────────────────────────────────────────────────────────
# token_path — filename schema lock
# ──────────────────────────────────────────────────────────────────────
class TestTokenPath(unittest.TestCase):

    def test_filename_schema(self):
        with TemporaryDirectory() as td:
            p = v1_arm.token_path(date_kst="2026-05-26",
                                   runtime_dir=Path(td))
            self.assertEqual(p.name, "v1_armed_2026-05-26.json")
            self.assertEqual(p.parent, Path(td))

    def test_rejects_invalid_date_format(self):
        for bad in ("2026/05/26", "26-05-2026", "abcd-ef-gh",
                    "2026-5-26", "2026-05-26-extra"):
            with self.assertRaises(ValueError):
                v1_arm.token_path(date_kst=bad)


# ──────────────────────────────────────────────────────────────────────
# write_arm_token + read_arm_token — round-trip + atomicity
# ──────────────────────────────────────────────────────────────────────
class TestWriteAndRead(unittest.TestCase):

    def test_round_trip(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, 0,
                           tzinfo=timezone.utc)
            tok = v1_arm.write_arm_token(
                runtime_dir=Path(td), now=now,
                armed_by="alice", hostname="laptop",
                note="ok to trade",
            )
            self.assertEqual(tok.date_kst, "2026-05-26")
            self.assertEqual(tok.armed_by, "alice")
            self.assertEqual(tok.hostname, "laptop")
            self.assertEqual(tok.note, "ok to trade")

            tok2 = v1_arm.read_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td))
            self.assertIsNotNone(tok2)
            self.assertEqual(tok2, tok)

    def test_atomic_write_leaves_no_tmp(self):
        with TemporaryDirectory() as td:
            v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td))
            files = sorted(p.name for p in Path(td).iterdir())
            self.assertEqual(files, ["v1_armed_2026-05-26.json"])

    def test_no_overwrite_raises_when_disallowed(self):
        with TemporaryDirectory() as td:
            v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td))
            with self.assertRaises(FileExistsError):
                v1_arm.write_arm_token(
                    date_kst="2026-05-26", runtime_dir=Path(td),
                    overwrite=False,
                )

    def test_overwrite_is_default(self):
        with TemporaryDirectory() as td:
            t1 = v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td),
                note="first")
            t2 = v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td),
                note="second")
            self.assertEqual(t1.date_kst, t2.date_kst)
            self.assertEqual(t2.note, "second")

    def test_read_missing_returns_none(self):
        with TemporaryDirectory() as td:
            self.assertIsNone(v1_arm.read_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td)))

    def test_read_malformed_returns_none(self):
        with TemporaryDirectory() as td:
            p = v1_arm.token_path(date_kst="2026-05-26",
                                   runtime_dir=Path(td))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(v1_arm.read_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td)))

    def test_read_partial_schema_returns_none(self):
        """If the token file is JSON but missing required keys, treat
        it as 'not armed' (fail-closed)."""
        with TemporaryDirectory() as td:
            p = v1_arm.token_path(date_kst="2026-05-26",
                                   runtime_dir=Path(td))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"unrelated": "junk"}),
                         encoding="utf-8")
            self.assertIsNone(v1_arm.read_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td)))


# ──────────────────────────────────────────────────────────────────────
# require_armed_for_today — the gate
# ──────────────────────────────────────────────────────────────────────
class TestRequireArmedForToday(unittest.TestCase):

    def test_missing_token_reports_not_armed(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 13, 0, tzinfo=timezone.utc)
            r = v1_arm.require_armed_for_today(
                runtime_dir=Path(td), now=now)
            self.assertFalse(r.ok)
            self.assertEqual(r.date_kst, "2026-05-26")
            self.assertIsNone(r.token)
            self.assertIn("no arm token for 2026-05-26", r.reason)

    def test_present_token_reports_armed(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            v1_arm.write_arm_token(
                runtime_dir=Path(td), now=now, armed_by="alice")
            r = v1_arm.require_armed_for_today(
                runtime_dir=Path(td), now=now)
            self.assertTrue(r.ok)
            self.assertEqual(r.date_kst, "2026-05-26")
            self.assertIsNotNone(r.token)
            self.assertEqual(r.token.armed_by, "alice")

    def test_yesterday_token_does_not_count_for_today(self):
        """Hard constraint: one token = one day."""
        with TemporaryDirectory() as td:
            # Arm for 2026-05-25 (yesterday)
            v1_arm.write_arm_token(
                date_kst="2026-05-25", runtime_dir=Path(td))
            # Check on 2026-05-26 (today)
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            r = v1_arm.require_armed_for_today(
                runtime_dir=Path(td), now=now)
            self.assertFalse(r.ok)
            self.assertIsNone(r.token)
            self.assertIn("2026-05-26", r.reason,
                "reason must mention TODAY's date so the operator "
                "knows which file to create")

    def test_malformed_token_fails_closed_with_clear_reason(self):
        with TemporaryDirectory() as td:
            p = v1_arm.token_path(date_kst="2026-05-26",
                                   runtime_dir=Path(td))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("oops", encoding="utf-8")
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            r = v1_arm.require_armed_for_today(
                runtime_dir=Path(td), now=now)
            self.assertFalse(r.ok)
            self.assertIn("malformed", r.reason)


# ──────────────────────────────────────────────────────────────────────
# list_token_files + gc_old_tokens — bookkeeping
# ──────────────────────────────────────────────────────────────────────
class TestListAndGc(unittest.TestCase):

    def test_list_returns_newest_first(self):
        with TemporaryDirectory() as td:
            for d in ("2026-05-20", "2026-05-26", "2026-05-23"):
                v1_arm.write_arm_token(
                    date_kst=d, runtime_dir=Path(td))
            files = v1_arm.list_token_files(runtime_dir=Path(td))
            names = [p.name for p in files]
            self.assertEqual(names, [
                "v1_armed_2026-05-26.json",
                "v1_armed_2026-05-23.json",
                "v1_armed_2026-05-20.json",
            ])

    def test_list_ignores_unrelated_files(self):
        with TemporaryDirectory() as td:
            v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td))
            (Path(td) / "global_halt.json").write_text("{}",
                encoding="utf-8")
            (Path(td) / "v1_armed_NOPE.json").write_text("{}",
                encoding="utf-8")
            files = v1_arm.list_token_files(runtime_dir=Path(td))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name,
                             "v1_armed_2026-05-26.json")

    def test_gc_removes_old_tokens_only(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            for d in ("2026-04-01",   # 55 days old → delete
                      "2026-04-30",   # 26 days old → keep
                      "2026-05-26"):  # today → keep
                v1_arm.write_arm_token(
                    date_kst=d, runtime_dir=Path(td), now=now)
            removed = v1_arm.gc_old_tokens(
                keep_days=30, runtime_dir=Path(td), now=now)
            self.assertEqual(removed, 1)
            files = sorted(p.name for p in
                           v1_arm.list_token_files(
                                runtime_dir=Path(td)))
            self.assertEqual(files, [
                "v1_armed_2026-04-30.json",
                "v1_armed_2026-05-26.json",
            ])

    def test_gc_never_deletes_today(self):
        """Even with keep_days=0 the gate must not delete today's
        token — race-safety vs an arm-then-gc-then-launchd sequence."""
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            v1_arm.write_arm_token(
                date_kst="2026-05-26", runtime_dir=Path(td), now=now)
            v1_arm.write_arm_token(
                date_kst="2026-05-25", runtime_dir=Path(td), now=now)
            v1_arm.gc_old_tokens(
                keep_days=0, runtime_dir=Path(td), now=now)
            files = sorted(p.name for p in
                           v1_arm.list_token_files(
                                runtime_dir=Path(td)))
            self.assertIn("v1_armed_2026-05-26.json", files)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
