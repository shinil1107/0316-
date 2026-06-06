"""V1-F.F4 — Validation tests for the dual-plist launchd install.

V1-F splits the V1-E single plist into two:

* ``com.autotrade.v1.t7``    — 09:00 KST T7 prefetch (own email)
* ``com.autotrade.v1.daily`` — 22:35 KST trade fire (reuses T7 run)

Both must coexist on disk, both must be installed by a single
``./install_v1.sh install``, both must be removed by
``./install_v1.sh uninstall``, and their env blocks must NOT drift.

Pinned invariants
-----------------

* Both templates exist and parse as valid plist XML
* Trade plist uses the ``trade`` subcommand (NOT legacy ``run``)
* T7 plist uses the ``t7-prefetch`` subcommand
* Trade plist fires at 22:35; T7 plist fires at 09:00
* T7 plist has NO submit/cancel/apply gates (broker-free fire)
* T7 plist has KIS_ENV=paper
* Both plists have ProcessType=Background (one-time GateKeeper popup)
* install_v1.sh references BOTH labels (install loop, status loop,
  uninstall loop)
* T7 plist's env matches ``v1_runner.T7_LAUNCHD_ENV`` exactly
* Trade plist's env still matches ``v1_runner.LAUNCHD_ENV`` exactly
"""

from __future__ import annotations

import os
import plistlib
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import v1_runner   # noqa: E402

T7_TEMPLATE = _PHASE3 / "launchd" / "com.autotrade.v1.t7.plist.template"
TRADE_TEMPLATE = _PHASE3 / "launchd" / "com.autotrade.v1.daily.plist.template"
INSTALL_SH = _PHASE3 / "launchd" / "install_v1.sh"


def _render(template: Path) -> dict:
    """Substitute the documented sentinels so plistlib can parse."""
    rendered = (template.read_text(encoding="utf-8")
                 .replace("__PYTHON__", "/usr/bin/python3")
                 .replace("__REPO_ROOT__", "/tmp/repo"))
    return plistlib.loads(rendered.encode("utf-8"))


# ──────────────────────────────────────────────────────────────────────
# Both templates exist + parse
# ──────────────────────────────────────────────────────────────────────
class TestBothTemplatesPresent(unittest.TestCase):

    def test_t7_template_exists(self):
        self.assertTrue(T7_TEMPLATE.exists(),
                         f"missing template: {T7_TEMPLATE}")

    def test_trade_template_exists(self):
        self.assertTrue(TRADE_TEMPLATE.exists(),
                         f"missing template: {TRADE_TEMPLATE}")

    def test_both_parse_as_plist(self):
        t7 = _render(T7_TEMPLATE)
        tr = _render(TRADE_TEMPLATE)
        self.assertEqual(t7["Label"], "com.autotrade.v1.t7")
        self.assertEqual(tr["Label"], "com.autotrade.v1.daily")


# ──────────────────────────────────────────────────────────────────────
# Subcommand wiring
# ──────────────────────────────────────────────────────────────────────
class TestSubcommandWiring(unittest.TestCase):

    def test_t7_plist_invokes_t7_prefetch(self):
        argv = _render(T7_TEMPLATE)["ProgramArguments"]
        self.assertEqual(argv[1], "-m")
        self.assertEqual(argv[2], "phase3.autotrade.v1_runner")
        self.assertEqual(argv[3], "t7-prefetch")

    def test_trade_plist_invokes_trade(self):
        argv = _render(TRADE_TEMPLATE)["ProgramArguments"]
        self.assertEqual(argv[1], "-m")
        self.assertEqual(argv[2], "phase3.autotrade.v1_runner")
        self.assertEqual(argv[3], "trade",
                          "V1-F trade plist must use the new "
                          "``trade`` subcommand (auto-discovers "
                          "morning T7), not legacy ``run``")


# ──────────────────────────────────────────────────────────────────────
# Schedule semantics
# ──────────────────────────────────────────────────────────────────────
class TestSchedules(unittest.TestCase):

    def test_t7_fires_at_07_20_kst(self):
        """07:20 KST: FMP prior-day closes stable since 04:00 KST
        (US close), leaves a buffer before the operator's commute
        for the first-fire macOS permission popup."""
        cal = _render(T7_TEMPLATE)["StartCalendarInterval"]
        self.assertEqual(cal.get("Hour"), 7)
        self.assertEqual(cal.get("Minute"), 20)

    def test_trade_fires_at_22_35_kst(self):
        cal = _render(TRADE_TEMPLATE)["StartCalendarInterval"]
        self.assertEqual(cal.get("Hour"), 22)
        self.assertEqual(cal.get("Minute"), 35)


# ──────────────────────────────────────────────────────────────────────
# Env-block semantics — T7 must NOT carry broker gates
# ──────────────────────────────────────────────────────────────────────
class TestEnvBlockSeparation(unittest.TestCase):

    def setUp(self) -> None:
        self.t7_env = _render(T7_TEMPLATE)["EnvironmentVariables"]
        self.tr_env = _render(TRADE_TEMPLATE)["EnvironmentVariables"]

    def test_t7_has_kis_env_paper(self):
        self.assertEqual(self.t7_env.get("KIS_ENV"), "paper")

    def test_t7_does_NOT_have_submit_or_cancel_or_apply(self):
        """T7 prefetch never calls the broker. Keeping these gates
        out of its plist is defence in depth (even if the codepath
        is broker-free) so a future regression that adds a stray
        broker call can't go un-noticed."""
        for gate in ("KIS_PAPER_SUBMIT_OK",
                     "KIS_PAPER_CANCEL_OK",
                     "AUTOTRADE_T10_APPLY_OK"):
            self.assertNotIn(
                gate, self.t7_env,
                f"T7 plist must NOT set {gate} — T7 fire is "
                "broker-free by design")

    def test_t7_does_NOT_have_suppress_t7_mail(self):
        """The whole point of V1-F's T7 prefetch is its own email."""
        self.assertNotIn(
            "AUTOTRADE_V1_SUPPRESS_T7_MAIL", self.t7_env,
            "T7 prefetch must NOT suppress its own mail")

    def test_trade_carries_full_broker_gates(self):
        self.assertEqual(self.tr_env.get("KIS_ENV"), "paper")
        self.assertEqual(self.tr_env.get("KIS_PAPER_SUBMIT_OK"),
                          "true")
        self.assertEqual(self.tr_env.get("KIS_PAPER_CANCEL_OK"),
                          "true")
        self.assertEqual(self.tr_env.get("AUTOTRADE_T10_APPLY_OK"),
                          "true")

    def test_t7_plist_env_matches_T7_LAUNCHD_ENV_constant(self):
        """Plist (hand-edited XML) and the Python-side constant the
        panel test-fire button injects MUST agree, exactly the same
        contract V1-E.3 enforced for the trade env. Parity test
        prevents the panel from green-lighting a configuration the
        actual launchd fire would halt on (or vice versa)."""
        for k, expected in v1_runner.T7_LAUNCHD_ENV.items():
            self.assertEqual(
                self.t7_env.get(k), expected,
                f"T7 plist EnvironmentVariables[{k!r}]="
                f"{self.t7_env.get(k)!r} but "
                f"v1_runner.T7_LAUNCHD_ENV[{k!r}]={expected!r}")

    def test_trade_plist_env_still_matches_LAUNCHD_ENV(self):
        for k, expected in v1_runner.LAUNCHD_ENV.items():
            self.assertEqual(
                self.tr_env.get(k), expected,
                f"trade plist EnvironmentVariables[{k!r}]="
                f"{self.tr_env.get(k)!r} but "
                f"v1_runner.LAUNCHD_ENV[{k!r}]={expected!r}")


# ──────────────────────────────────────────────────────────────────────
# GateKeeper / popup mitigation
# ──────────────────────────────────────────────────────────────────────
class TestProcessTypeBackground(unittest.TestCase):

    def test_t7_marked_background(self):
        self.assertEqual(_render(T7_TEMPLATE).get("ProcessType"),
                          "Background")

    def test_trade_marked_background(self):
        self.assertEqual(
            _render(TRADE_TEMPLATE).get("ProcessType"),
            "Background")


# ──────────────────────────────────────────────────────────────────────
# install_v1.sh handles BOTH labels
# ──────────────────────────────────────────────────────────────────────
class TestInstallScriptDualAgent(unittest.TestCase):

    def setUp(self) -> None:
        self.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_references_both_labels(self):
        """install/uninstall/status loops MUST process both agents
        — otherwise ``install_v1.sh uninstall`` would leave one
        agent orbiting and ``install`` would only register one."""
        self.assertIn("com.autotrade.v1.t7", self.text)
        self.assertIn("com.autotrade.v1.daily", self.text)

    def test_references_both_template_basenames(self):
        self.assertIn("com.autotrade.v1.t7.plist.template",
                       self.text)
        self.assertIn("com.autotrade.v1.daily.plist.template",
                       self.text)

    def test_loop_over_agents_exists(self):
        """The dual-install must be loop-driven, not a copy-paste
        block. Two copy-pasted code paths drift; a loop over an
        AGENTS list cannot."""
        self.assertRegex(self.text,
                          r"for .* in .*AGENTS\[@\].*")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
