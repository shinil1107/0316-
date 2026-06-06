"""V1-E UI — purely-functional helpers in ``control_panel``.

The Tk widgets are not tested directly (we don't bring up an X
session in CI), but every render decision the panel makes is driven
by ``compute_v1e_status``, ``V1EStatus.status_lines``, and
``_parse_launchctl_print`` — all pure. We lock those.

Pinned invariants:

* Today's KST date is always populated regardless of arm state.
* Armed → ``armed_by`` / ``armed_at_utc`` / ``armed_note`` are
  populated from the on-disk token; ``armed_reason`` is empty.
* Unarmed → ``armed=False`` + ``armed_reason`` carries the launchd-
  visible diagnostic ("no arm token for YYYY-MM-DD …").
* ``_parse_launchctl_print`` distinguishes:
    rc=0                   → "loaded" + parsed last-exit-code (if any)
    rc!=0, stdout empty    → "not loaded"
    rc!=0, stdout non-empty → "unknown: <first line>"
* ``status_lines`` produces four lines in the documented order, each
  prefixed with the row label that matches the Tk widget's column 0.
* Log-size formatter shows ``(missing)`` for ``-1`` and a clean
  ``…B``/``…KB`` for present files.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import control_panel as cp
from phase3.autotrade import v1_arm


# ──────────────────────────────────────────────────────────────────────
# _parse_launchctl_print
# ──────────────────────────────────────────────────────────────────────
class TestParseLaunchctlPrint(unittest.TestCase):

    def test_loaded_with_last_exit(self):
        stdout = (
            "{\n"
            "    state = running\n"
            "    last exit code = 0\n"
            "    program = /usr/bin/python3\n"
            "}\n"
        )
        loaded, label, rc = cp._parse_launchctl_print(0, stdout)
        self.assertTrue(loaded)
        self.assertEqual(label, "loaded")
        self.assertEqual(rc, 0)

    def test_loaded_with_nonzero_last_exit(self):
        stdout = "last exit code = 2\n"
        loaded, label, rc = cp._parse_launchctl_print(0, stdout)
        self.assertTrue(loaded)
        self.assertEqual(rc, 2)

    def test_loaded_without_last_exit_line(self):
        loaded, label, rc = cp._parse_launchctl_print(
            0, "state = running\n")
        self.assertTrue(loaded)
        self.assertIsNone(rc)

    def test_not_loaded_empty_stdout(self):
        loaded, label, rc = cp._parse_launchctl_print(113, "")
        self.assertFalse(loaded)
        self.assertEqual(label, "not loaded")
        self.assertIsNone(rc)

    def test_unknown_with_stderr_blurb(self):
        loaded, label, _rc = cp._parse_launchctl_print(
            1, "permission denied: blah\nignored")
        self.assertFalse(loaded)
        self.assertIn("unknown", label)
        self.assertIn("permission denied", label)

    def test_garbage_last_exit_returns_none(self):
        stdout = "last exit code = oops\n"
        _loaded, _label, rc = cp._parse_launchctl_print(0, stdout)
        self.assertIsNone(rc)


# ──────────────────────────────────────────────────────────────────────
# compute_v1e_status — arm-state branches
# ──────────────────────────────────────────────────────────────────────
def _fake_launchctl_loaded(label: str):
    return 0, "last exit code = 0\nstate = waiting\n"


def _fake_launchctl_not_loaded(label: str):
    return 113, ""


class TestComputeV1EStatus(unittest.TestCase):

    def test_today_kst_always_populated(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
            st = cp.compute_v1e_status(
                runtime_dir=Path(td), now=now,
                launchctl_query=_fake_launchctl_not_loaded,
            )
            self.assertEqual(st.today_kst, "2026-05-26")

    def test_unarmed_state(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            st = cp.compute_v1e_status(
                runtime_dir=Path(td), now=now,
                launchctl_query=_fake_launchctl_not_loaded,
            )
            self.assertFalse(st.armed)
            self.assertEqual(st.armed_by, "")
            self.assertIn("no arm token", st.armed_reason)
            self.assertFalse(st.launchd_loaded)
            self.assertEqual(st.launchd_state, "not loaded")

    def test_armed_state_surfaces_token_fields(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            v1_arm.write_arm_token(
                runtime_dir=Path(td), now=now,
                armed_by="alice", hostname="laptop",
                note="ok to trade",
            )
            st = cp.compute_v1e_status(
                runtime_dir=Path(td), now=now,
                launchctl_query=_fake_launchctl_loaded,
            )
            self.assertTrue(st.armed)
            self.assertEqual(st.armed_by, "alice")
            self.assertEqual(st.armed_note, "ok to trade")
            self.assertEqual(st.armed_reason, "")
            # launchd
            self.assertTrue(st.launchd_loaded)
            self.assertEqual(st.launchd_state, "loaded")
            self.assertEqual(st.launchd_last_exit_code, 0)

    def test_log_sizes_when_missing(self):
        with TemporaryDirectory() as td:
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            st = cp.compute_v1e_status(
                runtime_dir=Path(td), now=now,
                launchctl_query=_fake_launchctl_not_loaded,
                log_out_path=Path(td) / "nope1.log",
                log_err_path=Path(td) / "nope2.log",
            )
            self.assertEqual(st.log_out_size, -1)
            self.assertEqual(st.log_err_size, -1)

    def test_log_sizes_when_present(self):
        with TemporaryDirectory() as td:
            out = Path(td) / "out.log"
            err = Path(td) / "err.log"
            out.write_text("a" * 100, encoding="utf-8")
            err.write_text("b" * 2048, encoding="utf-8")
            now = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)
            st = cp.compute_v1e_status(
                runtime_dir=Path(td), now=now,
                launchctl_query=_fake_launchctl_not_loaded,
                log_out_path=out, log_err_path=err,
            )
            self.assertEqual(st.log_out_size, 100)
            self.assertEqual(st.log_err_size, 2048)


# ──────────────────────────────────────────────────────────────────────
# V1EStatus.status_lines — render contract
# ──────────────────────────────────────────────────────────────────────
class TestStatusLines(unittest.TestCase):

    def _build(self, **overrides):
        defaults = dict(
            today_kst="2026-05-26",
            armed=False, armed_reason="no arm token for 2026-05-26",
            armed_by="", armed_at_utc="", armed_note="",
            launchd_loaded=False, launchd_state="not loaded",
            launchd_last_exit_code=None,
            log_out_path=Path("/tmp/out.log"),
            log_err_path=Path("/tmp/err.log"),
            log_out_size=-1, log_err_size=-1,
            missing_secrets=[],
            next_fire_label="Today 22:35",
            next_fire_countdown="in 1h 00m",
        )
        defaults.update(overrides)
        return cp.V1EStatus(**defaults)

    def test_five_lines_in_documented_order_with_readiness_first(self):
        """Readiness is the operator's primary signal — it MUST be
        the first line so the GO/NO-GO answer is visible without
        scanning. We pin both count and order."""
        st = self._build()
        lines = st.status_lines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("Readiness  :"))
        self.assertTrue(lines[1].startswith("Today (KST):"))
        self.assertTrue(lines[2].startswith("Arm token  :"))
        self.assertTrue(lines[3].startswith("launchd    :"))
        self.assertTrue(lines[4].startswith("Logs       :"))

    def test_unarmed_renders_reason(self):
        st = self._build()
        self.assertIn("NOT ARMED", st.status_lines()[2])
        self.assertIn("no arm token for 2026-05-26",
                      st.status_lines()[2])

    def test_armed_renders_who_and_when(self):
        st = self._build(
            armed=True, armed_by="alice",
            armed_at_utc="2026-05-26T07:00:00+00:00",
            armed_note="ok to trade",
        )
        line = st.status_lines()[2]
        self.assertIn("ARMED", line)
        self.assertIn("alice", line)
        self.assertIn("2026-05-26T07:00:00", line)
        self.assertIn("ok to trade", line)

    def test_launchd_line_shows_last_exit_when_loaded(self):
        st = self._build(
            launchd_loaded=True, launchd_state="loaded",
            launchd_last_exit_code=0,
        )
        line = st.status_lines()[3]
        self.assertIn("loaded", line)
        self.assertIn("last_exit=0", line)

    def test_launchd_line_omits_last_exit_when_unknown(self):
        st = self._build(
            launchd_loaded=True, launchd_state="loaded",
            launchd_last_exit_code=None,
        )
        line = st.status_lines()[3]
        self.assertIn("loaded", line)
        self.assertNotIn("last_exit", line)

    def test_logs_line_human_friendly_sizes(self):
        st = self._build(log_out_size=512, log_err_size=2048)
        line = st.status_lines()[4]
        self.assertIn("512B", line)
        self.assertIn("2KB", line)

    def test_logs_line_missing_marker(self):
        line = self._build().status_lines()[4]
        self.assertIn("(missing)", line)


# ──────────────────────────────────────────────────────────────────────
# V1-E.5 Readiness — GO/NO-GO synthesizer
# ──────────────────────────────────────────────────────────────────────
class TestReadiness(unittest.TestCase):
    """``ready`` is True iff **all four** launchd gates pass. The
    panel paints the row green when True, red when False, and the
    operator-facing blockers list explains exactly which gate failed
    so 'why is this red?' is answerable without log spelunking."""

    def _all_green(self, **overrides):
        defaults = dict(
            today_kst="2026-05-27",
            armed=True, armed_reason="",
            armed_by="alice", armed_at_utc="2026-05-27T07:00:00+00:00",
            armed_note="",
            launchd_loaded=True, launchd_state="loaded",
            launchd_last_exit_code=0,
            log_out_path=Path("/tmp/out.log"),
            log_err_path=Path("/tmp/err.log"),
            log_out_size=100, log_err_size=0,
            missing_secrets=[],
            next_fire_label="Today 22:35",
            next_fire_countdown="in 8h 49m",
        )
        defaults.update(overrides)
        return cp.V1EStatus(**defaults)

    def test_all_green_is_ready(self):
        st = self._all_green()
        self.assertTrue(st.ready)
        self.assertEqual(st.readiness_blockers(), [])
        line = st.status_lines()[0]
        self.assertIn("READY", line)
        self.assertIn("Today 22:35", line)
        self.assertIn("in 8h 49m", line)

    def test_unarmed_blocks_readiness(self):
        st = self._all_green(armed=False, armed_by="",
                              armed_reason="no token")
        self.assertFalse(st.ready)
        line = st.status_lines()[0]
        self.assertIn("NOT READY", line)
        self.assertIn("not armed", line)

    def test_launchd_not_loaded_blocks_readiness(self):
        st = self._all_green(launchd_loaded=False,
                              launchd_state="not loaded",
                              launchd_last_exit_code=None)
        self.assertFalse(st.ready)
        line = st.status_lines()[0]
        self.assertIn("not loaded", line)

    def test_nonzero_last_exit_blocks_readiness(self):
        """A prior failed fire from TODAY is the 'go check err.log'
        state. We block READY until the operator either acknowledges
        and clears (re-fires successfully) or unloads the agent.
        V1-E.6: only TODAY's failures hard-block; see the stale
        case below."""
        st = self._all_green(
            launchd_last_exit_code=2,
            # same as today_kst → fresh failure
            last_fire_finished_kst_date="2026-05-27",
        )
        self.assertFalse(st.ready)
        line = st.status_lines()[0]
        self.assertIn("rc=2", line)
        self.assertIn("err.log", line)

    def test_stale_prior_day_failure_does_not_block(self):
        """V1-E.6 — Yesterday's failed t10_apply leaves launchd
        reporting rc=2, but today is a fresh KST day with a new
        arm token. The next scheduled fire is what the operator
        cares about, so the panel should show READY with an info
        note rather than NOT READY. Reproducer for the 26-05-29
        morning post-mortem."""
        st = self._all_green(
            launchd_last_exit_code=2,
            last_fire_finished_kst_date="2026-05-26",  # < today
            today_kst="2026-05-27",
        )
        self.assertTrue(st.stale_last_exit)
        self.assertTrue(st.ready)
        self.assertEqual(st.readiness_blockers(), [])
        notes = st.info_notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("rc=2", notes[0])
        self.assertIn("2026-05-26", notes[0])
        line = st.status_lines()[0]
        self.assertIn("READY", line)
        self.assertIn("note", line)
        self.assertIn("rc=2", line)

    def test_same_day_failure_still_blocks_even_with_known_date(self):
        """Pin the boundary: when last_fire_finished_kst_date ==
        today, we still treat the rc as fresh and block."""
        st = self._all_green(
            launchd_last_exit_code=2,
            last_fire_finished_kst_date="2026-05-27",
            today_kst="2026-05-27",
        )
        self.assertFalse(st.stale_last_exit)
        self.assertFalse(st.ready)
        self.assertEqual(st.info_notes(), [])

    def test_unknown_last_fire_date_keeps_conservative_block(self):
        """When we cannot prove staleness (status.json missing /
        empty), preserve pre-V1-E.6 behaviour: non-zero rc blocks."""
        st = self._all_green(
            launchd_last_exit_code=2,
            last_fire_finished_kst_date="",  # unknown
        )
        self.assertFalse(st.stale_last_exit)
        self.assertFalse(st.ready)

    def test_zero_last_exit_does_not_block(self):
        """Successful prior fire is the happy steady-state."""
        st = self._all_green(launchd_last_exit_code=0)
        self.assertTrue(st.ready)

    def test_none_last_exit_does_not_block_first_install(self):
        """Fresh install: launchd has never fired this label so
        ``last exit code`` is missing → parsed as None. This must
        NOT block readiness — the operator just installed."""
        st = self._all_green(launchd_last_exit_code=None)
        self.assertTrue(st.ready)

    def test_missing_secrets_block_readiness(self):
        st = self._all_green(missing_secrets=["FMP_API_KEY",
                                                "KIS_APP_KEY"])
        self.assertFalse(st.ready)
        line = st.status_lines()[0]
        self.assertIn("missing secrets", line)
        self.assertIn("FMP_API_KEY", line)
        self.assertIn("KIS_APP_KEY", line)


# ──────────────────────────────────────────────────────────────────────
# V1-E.5 — Secret check + next-fire countdown
# ──────────────────────────────────────────────────────────────────────
class TestCheckLaunchdSecrets(unittest.TestCase):

    def test_dotenv_alone_satisfies_check(self):
        """Operator workflow: keys live in .env, NOT in current
        shell. v1_runner hydrates at startup, so this must NOT
        report missing."""
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"
            dotenv.write_text(
                "FMP_API_KEY=x\nKIS_APP_KEY=x\nKIS_APP_SECRET=x\n"
                "KIS_ACCOUNT_NO=x\nKIS_ACCOUNT_PRODUCT_CODE=01\n",
                encoding="utf-8")
            missing = cp._check_launchd_secrets(
                dotenv_path=dotenv, environ={})
            self.assertEqual(missing, [])

    def test_environ_alone_satisfies_check(self):
        """Operator workflow: keys exported in zshrc, no .env. The
        panel still reports READY because exporting via shell is
        also a valid setup."""
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"  # does not exist
            environ = {
                "FMP_API_KEY": "x", "KIS_APP_KEY": "x",
                "KIS_APP_SECRET": "x", "KIS_ACCOUNT_NO": "x",
                "KIS_ACCOUNT_PRODUCT_CODE": "01",
            }
            missing = cp._check_launchd_secrets(
                dotenv_path=dotenv, environ=environ)
            self.assertEqual(missing, [])

    def test_partial_dotenv_reports_only_actually_missing(self):
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"
            dotenv.write_text(
                "FMP_API_KEY=x\nKIS_APP_KEY=x\n",
                encoding="utf-8")
            missing = cp._check_launchd_secrets(
                dotenv_path=dotenv, environ={})
            self.assertEqual(
                missing,
                ["KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                 "KIS_ACCOUNT_PRODUCT_CODE"])

    def test_no_dotenv_and_no_environ_returns_all(self):
        with TemporaryDirectory() as td:
            dotenv = Path(td) / ".env"  # absent
            missing = cp._check_launchd_secrets(
                dotenv_path=dotenv, environ={})
            self.assertEqual(
                missing, list(cp.LAUNCHD_REQUIRED_SECRETS))


class TestNextV1EFireKst(unittest.TestCase):

    def test_before_2235_today(self):
        now = datetime(2026, 5, 27, 13, 46)
        label, cd = cp._next_v1e_fire_kst(now=now)
        self.assertEqual(label, "Today 22:35")
        # 22:35 - 13:46 = 8h 49m
        self.assertEqual(cd, "in 8h 49m")

    def test_at_or_after_2235_rolls_to_tomorrow(self):
        now = datetime(2026, 5, 27, 23, 0)
        label, cd = cp._next_v1e_fire_kst(now=now)
        self.assertEqual(label, "Tomorrow 22:35")
        # 22:35 next day - 23:00 today = 23h 35m
        self.assertEqual(cd, "in 23h 35m")

    def test_exact_minute_rolls_forward(self):
        """22:35:00 exactly: the fire WILL happen at 22:35 — but
        we round 'in 0h 00m' to 'Tomorrow' to avoid the operator
        seeing a confusing 0-countdown line. Test the boundary."""
        now = datetime(2026, 5, 27, 22, 35, 0)
        label, _cd = cp._next_v1e_fire_kst(now=now)
        self.assertEqual(label, "Tomorrow 22:35")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
