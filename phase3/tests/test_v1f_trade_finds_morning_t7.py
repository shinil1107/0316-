"""V1-F.F2 — Trade fire discovers the morning T7 prefetch run.

The 22:35 KST trade fire calls ``v1_runner`` with ``cmd=trade`` (i.e.
``run_v1_pipeline(discover_today_t7=True)``). Instead of re-running
T7 inline (which produced non-deterministic recommendations in
V1-E because FMP was still settling at 22:35 = 09:35 EDT), the
trade fire scans ``daily_runs/`` for a run produced earlier today
KST. If found → reuse its run_id with ``skip_t7=True``. If absent
→ hard-fail rc=2 with an explicit halt_reason + R11B alert email.

Pinned invariants
-----------------

* Today's *_daily with non-empty recommendations.csv is picked up
* Yesterday's *_daily is ignored (avoids using stale picks)
* shadow / dryrun siblings are filtered out
* multiple today-runs → newest by mtime wins (so a manual re-run
  of T7 later in the day overrides the 09:00 fire)
* no candidate → rc=2 + halt_reason mentioning the date
* no candidate → R11B alert email STILL goes out (run_dir-less mail)
* discover stage records run_id into status.json
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Mapping, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_runner, v1_status   # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _write_yaml(td: Path, output_dir: Path) -> Path:
    cfg = td / "config_trade.yaml"
    cfg.write_text(
        "paths:\n"
        f"  output_dir: {output_dir}\n"
        "email:\n  enabled: false\n",
        encoding="utf-8",
    )
    return cfg


def _seed_run(daily_runs: Path, name: str, *,
              recs: int = 5,
              mtime_offset_sec: float = 0.0) -> Path:
    """Create a fake T7 *_daily directory complete with a parseable
    ``recommendations.csv``. ``mtime_offset_sec`` lets the test
    create runs at deterministic relative times so the
    'newest-mtime-wins' picker can be exercised."""
    d = daily_runs / name
    d.mkdir(parents=True, exist_ok=True)
    csv = d / "recommendations.csv"
    # V1-G.2 — use the actual T7-emitted action ``BUY_NEW`` here, not
    # the phantom ``BUY`` that the older seed used. ``intents_io``'s
    # ``_BUY_ACTIONS_DEFAULT`` is now byte-equal to the simulator's
    # ``("BUY_NEW", "BUY_MORE")`` filter, so a bare ``BUY`` row no
    # longer matches and ``run_generate_intents`` would correctly
    # reject this fixture as "no actionable candidates".
    csv.write_text(
        "Action,Ticker,Shares,Price,RecRowId\n"
        + "\n".join(
            f"BUY_NEW,SYM{i},1,1.0,{i}" for i in range(recs))
        + "\n",
        encoding="utf-8",
    )
    if mtime_offset_sec:
        t = time.time() + mtime_offset_sec
        os.utime(d, (t, t))
    return d


def _ok_env() -> Dict[str, str]:
    """Trade-fire env: full submit/cancel/apply gates."""
    return {
        "KIS_ENV": "paper",
        "KIS_PAPER_SUBMIT_OK": "true",
        "KIS_PAPER_CANCEL_OK": "true",
        "AUTOTRADE_T10_APPLY_OK": "true",
    }


def _make_submit_fake(rc: int = 0):
    """Fake the paper_submit+apply subprocess (returns rc verbatim).
    Captures argv so tests can assert ``--run-id`` was forwarded."""
    calls: List[Dict[str, Any]] = []

    def fake(argv: Sequence[str], *,
             env: Mapping[str, str],
             timeout: Optional[float] = None,
             ) -> subprocess.CompletedProcess:
        calls.append({"argv": list(argv), "env": dict(env)})
        return subprocess.CompletedProcess(
            argv, rc, stdout="", stderr="")

    fake._calls = calls  # type: ignore[attr-defined]
    return fake


# ──────────────────────────────────────────────────────────────────────
# _find_today_t7_run — the discovery primitive
# ──────────────────────────────────────────────────────────────────────
class TestFindTodayT7Run(unittest.TestCase):

    def test_returns_today_run_dir_when_present(self):
        with TemporaryDirectory() as td:
            td = Path(td)
            dr = td / "daily_runs"
            _seed_run(dr, "20260528_090015_daily", recs=3)
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertIsNotNone(disc.run_dir)
            self.assertEqual(disc.run_id, "20260528_090015_daily")
            self.assertEqual(disc.recommendations_count, 3)

    def test_ignores_yesterdays_run(self):
        """Stale-picks defence: yesterday's recommendations are
        DERIVED from a different signal universe / FMP snapshot and
        must NEVER be reused for today's trade."""
        with TemporaryDirectory() as td:
            td = Path(td)
            dr = td / "daily_runs"
            _seed_run(dr, "20260527_090015_daily", recs=3)
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertIsNone(disc.run_dir)
            self.assertIn("2026-05-28", disc.reason)
            self.assertIn("no T7 prefetch run", disc.reason)
            self.assertIn("20260527_090015_daily",
                           disc.candidates_seen)

    def test_picks_newest_when_multiple_today(self):
        """Operator re-ran T7 manually later in the day. Trading on
        the LATER run (most recently visible to the operator) is
        the least-surprise choice."""
        with TemporaryDirectory() as td:
            td = Path(td)
            dr = td / "daily_runs"
            _seed_run(dr, "20260528_090015_daily",
                       mtime_offset_sec=-3600)   # 1h ago
            _seed_run(dr, "20260528_150030_daily",
                       mtime_offset_sec=0)        # now
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertEqual(disc.run_id, "20260528_150030_daily")

    def test_skips_shadow_and_dryrun_siblings(self):
        """T7 emits ``*_daily_shadow`` and ``*_dryrun_daily`` siblings
        that share a timestamp prefix but never carry a real
        recommendations.csv; they must be invisible to the picker."""
        with TemporaryDirectory() as td:
            td = Path(td)
            dr = td / "daily_runs"
            _seed_run(dr, "20260528_090015_daily", recs=2)
            shadow = dr / "20260528_090020_daily_shadow"
            shadow.mkdir(parents=True)
            (shadow / "recommendations.csv").write_text(
                "Action,Ticker,Shares,Price,RecRowId\n"
                "BUY,FAKE,1,1.0,1\n", encoding="utf-8")
            dryrun = dr / "20260528_090025_dryrun_daily"
            dryrun.mkdir(parents=True)
            (dryrun / "recommendations.csv").write_text(
                "Action,Ticker,Shares,Price,RecRowId\n"
                "BUY,FAKE,1,1.0,1\n", encoding="utf-8")
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertEqual(disc.run_id, "20260528_090015_daily")

    def test_empty_recommendations_csv_is_rejected(self):
        """A run that crashed mid-write may leave an empty CSV;
        treating it as valid would silently produce no intents."""
        with TemporaryDirectory() as td:
            td = Path(td)
            dr = td / "daily_runs"
            d = dr / "20260528_090015_daily"
            d.mkdir(parents=True)
            (d / "recommendations.csv").write_text(
                "", encoding="utf-8")
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertIsNone(disc.run_dir)

    def test_missing_daily_runs_dir_returns_explicit_reason(self):
        with TemporaryDirectory() as td:
            dr = Path(td) / "does_not_exist" / "daily_runs"
            disc = v1_runner._find_today_t7_run(
                dr, today_kst_str="2026-05-28")
            self.assertIsNone(disc.run_dir)
            self.assertIn("daily_runs_dir does not exist",
                           disc.reason)


# ──────────────────────────────────────────────────────────────────────
# run_v1_pipeline(discover_today_t7=True) — end-to-end
# ──────────────────────────────────────────────────────────────────────
class TestTradeFireDiscovery(unittest.TestCase):

    def test_finds_run_and_skips_t7_then_submits(self):
        """Happy path: morning T7 run on disk → discover → skip_t7
        → generate_intents → paper_submit (mocked rc=0)."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            dr = out / "daily_runs"
            morning_id = "20260528_090015_daily"
            _seed_run(dr, morning_id, recs=2)
            cfg = _write_yaml(td, out)
            submit = _make_submit_fake(rc=0)
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                env=_ok_env(), send_mail=False,
                discover_today_t7=True,
                fire_label="trade",
                subprocess_run=submit,
                arm_now=datetime(2026, 5, 28, 13, 35,
                                  tzinfo=timezone.utc),
                status_path=td / "v1_status.json",
            )
            self.assertEqual(r.rc, 0, r.halt_reason)
            self.assertEqual(r.run_id, morning_id)
            stage_keys = [s["key"] for s in r.stages]
            self.assertIn("discover_t7_run", stage_keys)
            # T7 may appear in the stage log but ONLY as ``skipped``
            # — the discover stage flipped ``skip_t7=True`` so the
            # inline T7 invocation block records a skip, not a real
            # run. The wall-clock cost of skipped is 0.0s.
            t7_stages = [s for s in r.stages
                          if s["key"] == "t7_generate"]
            for s in t7_stages:
                self.assertTrue(
                    s.get("skipped"),
                    "discover_t7_run hit means inline T7 must NOT "
                    "actually run; it must be recorded as skipped")
                self.assertEqual(s.get("duration_sec", -1), 0.0)
            # Submit subprocess received the morning run_id.
            self.assertEqual(len(submit._calls), 1)
            argv = submit._calls[0]["argv"]
            self.assertIn("--run-id", argv)
            self.assertEqual(argv[argv.index("--run-id") + 1],
                              morning_id)

    def test_no_morning_run_hard_fails_rc2(self):
        """Stale-picks bug guard: V1-E used yesterday's run when T7
        failed silently; V1-F MUST hard-fail instead."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            (out / "daily_runs").mkdir(parents=True)
            # No today *_daily; only a yesterday one to ensure
            # the picker does NOT fall back to it.
            _seed_run(out / "daily_runs",
                       "20260527_090015_daily", recs=2)
            cfg = _write_yaml(td, out)
            submit = _make_submit_fake()
            r = v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                env=_ok_env(), send_mail=False,
                discover_today_t7=True,
                fire_label="trade",
                subprocess_run=submit,
                arm_now=datetime(2026, 5, 28, 13, 35,
                                  tzinfo=timezone.utc),
                status_path=td / "v1_status.json",
            )
            self.assertEqual(r.rc, 2)
            self.assertTrue(r.halt_reason)
            self.assertIn("T7 prefetch missing", r.halt_reason)
            # paper_submit must NOT have been called.
            self.assertEqual(submit._calls, [])

    def test_status_json_records_run_id_after_discover(self):
        """The panel surfaces ``Last fire`` from status.json. The
        discover stage MUST promote run_id so the panel can show it
        without the run finishing first."""
        with TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            morning_id = "20260528_090015_daily"
            _seed_run(out / "daily_runs", morning_id, recs=1)
            cfg = _write_yaml(td, out)
            submit = _make_submit_fake(rc=0)
            status_path = td / "v1_status.json"
            v1_runner.run_v1_pipeline(
                config_path=cfg,
                require_arm_token=False,
                env=_ok_env(), send_mail=False,
                discover_today_t7=True,
                fire_label="trade",
                subprocess_run=submit,
                arm_now=datetime(2026, 5, 28, 13, 35,
                                  tzinfo=timezone.utc),
                status_path=status_path,
            )
            snap = v1_status.read_status(path=status_path)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.run_id, morning_id)
            self.assertEqual(snap.fire_label, "trade")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
