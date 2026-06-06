"""V1-A.1 — Locks the ``AUTOTRADE_V1_SUPPRESS_T7_MAIL`` patch in
``phase3/daily_runner.py``.

We do not exercise ``run_daily`` end-to-end here (engine init is too
heavy and the function expects a real config + cache). Instead we
lock the BEHAVIOURAL CONTRACT at the source-text level:

1. The exact env-var name owned by ``phase3.autotrade.t7_runner.
   SUPPRESS_T7_MAIL_ENV`` is read by ``daily_runner``.
2. The truthy parsing covers the same five spellings the rest of
   the codebase agrees on (``1`` / ``true`` / ``yes`` / ``y`` /
   ``on``, case-insensitive).
3. The suppress branch is wired into the same conditional that
   gates ``send_daily_email`` so unsetting the env restores v0
   behaviour exactly.

A drift in any of these would silently let T7 still emit its own
mail under V1 — operators would get TWO emails per daily run and
the V1 "single EOD digest" contract breaks.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import t7_runner


DAILY_RUNNER_PATH = _PHASE3 / "daily_runner.py"


class TestSuppressPatchPresent(unittest.TestCase):

    def setUp(self):
        self.src = DAILY_RUNNER_PATH.read_text(encoding="utf-8")

    def test_env_var_name_matches_t7_runner_constant(self):
        """The daily_runner patch and the t7_runner contract MUST
        reference the same env-var key. If you rename the constant,
        update both — this test makes drift loud."""
        self.assertEqual(
            t7_runner.SUPPRESS_T7_MAIL_ENV,
            "AUTOTRADE_V1_SUPPRESS_T7_MAIL")
        self.assertIn(
            "AUTOTRADE_V1_SUPPRESS_T7_MAIL", self.src,
            "daily_runner.py must read AUTOTRADE_V1_SUPPRESS_T7_MAIL")

    def test_truthy_spellings_match_rest_of_codebase(self):
        """Same vocabulary the autotrade env-var helpers use
        (``("1","true","yes","y","on")``)."""
        # Find the suppression assignment block — span until we hit
        # the closing tuple ``)``-after-``in (``.
        m = re.search(
            r"_v1_suppress_t7_mail\s*=\s*.+?in\s*\([^)]+\)",
            self.src, flags=re.DOTALL)
        self.assertIsNotNone(
            m, "expected a `_v1_suppress_t7_mail = ... in (...)` "
                "block in daily_runner.py")
        snippet = m.group(0)
        for token in ('"1"', '"true"', '"yes"', '"y"', '"on"'):
            self.assertIn(token, snippet,
                f"truthy token {token!r} missing from suppress parse "
                f"(span={snippet!r})")

    def test_send_daily_email_guarded_by_suppress_flag(self):
        """The patch must short-circuit the email-dispatch block;
        otherwise the suppress flag would not actually skip mail
        dispatch.

        V2 update: the raw ``and not dry_run`` term was refactored into
        a ``_mail_allowed_for_mode`` helper so a non-trading-day dry-run
        *preview* can still mail (gated by ``AUTOTRADE_DRYRUN_SEND_MAIL``).
        The contract this test pins is unchanged: (a) the suppress flag
        still gates dispatch, and (b) ``dry_run`` is still part of that
        decision."""
        # (a) suppress flag is still ANDed into the dispatch guard.
        self.assertRegex(
            self.src,
            r"if\s+not\s+_v1_suppress_t7_mail\s+and\s+_mail_allowed_for_mode",
            "suppress flag is not wired into the email-dispatch guard")
        # (b) the mode helper still derives from ``not dry_run`` so unsetting
        # the preview override restores v0 behaviour exactly.
        self.assertRegex(
            self.src,
            r"_mail_allowed_for_mode\s*=\s*\(not dry_run\)\s*or\s*_dryrun_send_mail",
            "dry_run must still gate the email dispatch decision")

    def test_logging_line_emitted_when_suppressed(self):
        """Operators triaging "did the mail go?" need a visible
        log line, not silence."""
        self.assertIn("T7 email suppressed", self.src)
        self.assertIn("R11B EOD digest will deliver", self.src)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
