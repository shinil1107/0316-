"""V1-E.3 — Validation tests for the launchd plist template + install
script.

We can't load a real launchd job from a unit test, so instead we
lock the on-disk *shape* of the plist + helper:

* The plist parses as XML and produces a sensible plist dict
  (``plistlib`` is the canonical loader macOS itself uses).
* Every safety-critical env var declared in V1-E.3 (
  ``KIS_ENV=paper``, the three gate vars, suppress flag) is
  present with the exact value the headless runner enforces.
* The schedule fires at 22:25 (the documented KST trigger).
* The plist references the v1_runner module via ``ProgramArguments``,
  not a brittle absolute script path.
* The install script is shellcheck-clean enough to be ``bash -n``-
  parseable and has the four documented subcommands.

Catching drift here is cheap; catching it on the day the agent
silently misfires is not.
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent

PLIST_TEMPLATE = (_PHASE3 / "launchd" /
                  "com.autotrade.v1.daily.plist.template")
INSTALL_SH = _PHASE3 / "launchd" / "install_v1.sh"


# ──────────────────────────────────────────────────────────────────────
# plist template
# ──────────────────────────────────────────────────────────────────────
class TestPlistTemplate(unittest.TestCase):

    def setUp(self):
        self.assertTrue(PLIST_TEMPLATE.exists(),
                        f"missing template: {PLIST_TEMPLATE}")
        # Render with sentinels so plistlib can parse; we leave the
        # original template file untouched.
        rendered = (PLIST_TEMPLATE.read_text(encoding="utf-8")
                    .replace("__PYTHON__", "/usr/bin/python3")
                    .replace("__REPO_ROOT__", "/tmp/repo"))
        self.plist = plistlib.loads(rendered.encode("utf-8"))

    def test_label_matches_documented_string(self):
        self.assertEqual(self.plist["Label"],
                         "com.autotrade.v1.daily")

    def test_program_arguments_invoke_v1_runner_trade(self):
        """V1-F: the 22:35 fire calls the ``trade`` subcommand, which
        auto-discovers the morning T7 prefetch run instead of
        re-running T7 inline. (The legacy ``run`` subcommand is kept
        on the v1_runner CLI for manual ad-hoc operator use.)"""
        argv = self.plist["ProgramArguments"]
        self.assertEqual(argv[1], "-m")
        self.assertEqual(argv[2], "phase3.autotrade.v1_runner")
        self.assertEqual(argv[3], "trade")
        self.assertEqual(argv[0], "/usr/bin/python3")

    def test_schedule_fires_at_22_35(self):
        """22:35 KST = 5 min after US open (22:30 KST)."""
        cal = self.plist["StartCalendarInterval"]
        self.assertEqual(cal.get("Hour"), 22)
        self.assertEqual(cal.get("Minute"), 35)

    def test_safety_env_vars_present_with_exact_values(self):
        env = self.plist["EnvironmentVariables"]
        # KIS_ENV is paper-only — anything else would let a live key
        # leak in.
        self.assertEqual(env.get("KIS_ENV"), "paper")
        # Gates exactly as ``check_env_gates`` expects them.
        self.assertEqual(env.get("KIS_PAPER_SUBMIT_OK"), "true")
        self.assertEqual(env.get("KIS_PAPER_CANCEL_OK"), "true")
        # ``AUTOTRADE_T10_APPLY_OK`` (NOT ``KIS_T10_APPLY_OK``) —
        # the historical V1-E typo halted the live test-fire at the
        # ``--apply-t10`` gate after T7 + generate_intents had
        # already burned ~1 min. The plist + LAUNCHD_ENV constant
        # MUST track ``daily_runner.APPLY_ENV_GATE``.
        self.assertEqual(env.get("AUTOTRADE_T10_APPLY_OK"), "true")
        self.assertNotIn(
            "KIS_T10_APPLY_OK", env,
            "obsolete env-var name from V1-E v0; daily_runner "
            "ignores it and the run would halt at --apply-t10")
        # V1: suppress the T7 own-email so R11B owns the digest.
        self.assertEqual(
            env.get("AUTOTRADE_V1_SUPPRESS_T7_MAIL"), "true")
        # Hygiene: line-buffered stdout so logs flush per-line.
        self.assertEqual(env.get("PYTHONUNBUFFERED"), "1")
        # PYTHONPATH must point at the repo root, not a literal
        # placeholder that survived rendering.
        self.assertEqual(env.get("PYTHONPATH"), "/tmp/repo")

    def test_t7_config_matches_paper_profile_config(self):
        """V1 is paper-only. The panel + v1_runner subprocess
        ``daily_runner --profile paper`` (= ``config.yaml``), so
        T7 MUST also write its run_id under the SAME yaml's
        output_dir. Otherwise T7 writes to ``output_real/`` while
        paper_submit hunts in ``output/`` — exactly the
        ``artifact run_dir does not exist`` halt observed live at
        2026-05-27 13:14:57 KST.

        We resolve both paths and assert byte-equality."""
        from phase3.autotrade import t7_runner
        from phase3.autotrade.reconcile import _PROFILE_CONFIG
        paper_cfg = Path(_PROFILE_CONFIG["paper"]).resolve()
        t7_cfg = Path(t7_runner._DEFAULT_T7_CONFIG).resolve()
        self.assertEqual(
            t7_cfg, paper_cfg,
            f"T7 default config {t7_cfg} ≠ paper-profile config "
            f"{paper_cfg}; T7 artifacts would land where "
            f"paper_submit cannot find them")

    def test_panel_gate_names_match_daily_runner(self):
        """Same parity but for ``control_panel`` — the panel reads
        os.environ via ``SUBMIT_GATE`` / ``CANCEL_GATE`` /
        ``APPLY_GATE`` to light its UI rows. If THOSE drift from
        daily_runner the panel would say "all gates green" while
        an actual submit halts."""
        from phase3.autotrade import (
            control_panel as cp, daily_runner as dr)
        self.assertEqual(cp.SUBMIT_GATE, dr.SUBMIT_ENV_GATE)
        self.assertEqual(cp.CANCEL_GATE, dr.CANCEL_ENV_GATE)
        self.assertEqual(cp.APPLY_GATE, dr.APPLY_ENV_GATE)

    def test_env_gate_names_match_daily_runner(self):
        """V1-E reproduces three env-var names that
        ``phase3.autotrade.daily_runner`` enforces. If the daily
        runner renames a gate but we forget to update V1-E (or vice
        versa), the panel + the launchd fire would BOTH agree that
        everything is armed while ``daily_runner`` halts at rc=2.
        This is the bug the live test-fire on 2026-05-27 surfaced
        (we'd typed ``KIS_T10_APPLY_OK`` instead of the canonical
        ``AUTOTRADE_T10_APPLY_OK``). Lock the parity here so it
        cannot recur."""
        from phase3.autotrade import (
            daily_runner as dr, v1_runner)
        self.assertEqual(v1_runner.SUBMIT_ENV_GATE,
                         dr.SUBMIT_ENV_GATE)
        self.assertEqual(v1_runner.CANCEL_ENV_GATE,
                         dr.CANCEL_ENV_GATE)
        self.assertEqual(v1_runner.APPLY_ENV_GATE,
                         dr.APPLY_ENV_GATE)

    def test_plist_env_matches_launchd_env_constant(self):
        """The plist (hand-edited XML) MUST agree with
        ``v1_runner.LAUNCHD_ENV`` (the dict the panel test-fire
        button injects). If they drift, the panel will report
        rc=0 on a path the real launchd fire would halt on (or
        vice versa) — exactly the silent-divergence trap V1-E
        is meant to prevent."""
        from phase3.autotrade import v1_runner
        plist_env = self.plist["EnvironmentVariables"]
        for key, expected in v1_runner.LAUNCHD_ENV.items():
            self.assertEqual(
                plist_env.get(key), expected,
                f"plist EnvironmentVariables[{key!r}]"
                f"={plist_env.get(key)!r} but "
                f"v1_runner.LAUNCHD_ENV[{key!r}]={expected!r}")

    def test_v1h_reprice_resilience_env_present(self):
        """V1-H/V1-I — the unattended fire ships reprice chase +
        per-ticker resilience knobs. V1-I re-tuned the V1-H values: with
        live-ask pricing (use_quote) the FIRST limit already sits at the
        market, so the slippage ceiling shrank 1200->300 bps and
        attempts 12->6 (the wide ceiling only existed to climb from the
        stale close; the gap is now handled by the gap filter upstream).
        CONTINUE_ON_UNFILLED stays on. Lock the values so a future edit
        can't silently revert."""
        env = self.plist["EnvironmentVariables"]
        self.assertEqual(env.get("AUTOTRADE_MAX_REPRICE_ATTEMPTS"), "6")
        self.assertEqual(env.get("AUTOTRADE_MAX_SLIPPAGE_BPS"), "300")
        self.assertEqual(env.get("AUTOTRADE_CONTINUE_ON_UNFILLED"), "true")

    def test_v1i_live_quote_gap_filter_env_present(self):
        """V1-I — the unattended BUY must price off the live ask
        (use_quote/quote_only), start slightly below the ask
        (buy_quote_pad -0.2), and drop names that gapped up past the cap
        (gap_filter 15). These are the knobs that replace the V1-H
        "chase a stale close up a 12% ladder" behaviour."""
        env = self.plist["EnvironmentVariables"]
        self.assertEqual(env.get("AUTOTRADE_USE_QUOTE"), "true")
        self.assertEqual(env.get("AUTOTRADE_QUOTE_ONLY"), "true")
        self.assertEqual(env.get("AUTOTRADE_BUY_QUOTE_PAD_PCT"), "-0.2")
        self.assertEqual(env.get("AUTOTRADE_GAP_FILTER_MAX_PCT"), "15")

    def test_keep_alive_disabled(self):
        """Non-zero exits should NOT be retried — V1 deliberately
        exits rc=0 on safe-skip, so any non-zero is a real failure
        that wants operator triage, not a launchd retry loop."""
        ka = self.plist.get("KeepAlive", False)
        self.assertFalse(bool(ka))

    def test_log_paths_routed_to_runtime_dir(self):
        for k in ("StandardOutPath", "StandardErrorPath"):
            v = self.plist.get(k, "")
            self.assertIn("runtime", v,
                f"{k} must route under phase3/autotrade/runtime/")

    def test_no_secrets_baked_into_template(self):
        """Anything that looks like a key MUST NOT appear in the
        template — the plist is checked into git."""
        text = PLIST_TEMPLATE.read_text(encoding="utf-8")
        for forbidden in (
            "FMP_API_KEY", "GMAIL_APP_PASSWORD",
            "KIS_PAPER_APP_KEY", "KIS_PAPER_APP_SECRET",
        ):
            self.assertNotIn(forbidden + "=", text,
                f"{forbidden} value must not be embedded in plist")


# ──────────────────────────────────────────────────────────────────────
# install_v1.sh
# ──────────────────────────────────────────────────────────────────────
class TestInstallScript(unittest.TestCase):

    def setUp(self):
        self.assertTrue(INSTALL_SH.exists(),
                        f"missing install script: {INSTALL_SH}")

    def test_is_executable(self):
        st = INSTALL_SH.stat()
        self.assertTrue(
            st.st_mode & stat.S_IXUSR,
            "install_v1.sh must be executable (chmod +x)")

    def test_bash_parse_clean(self):
        """``bash -n`` does a syntax check without running anything.
        We bail out gracefully if no bash on this CI box; macOS
        always has /bin/bash."""
        if not os.path.exists("/bin/bash"):
            self.skipTest("no /bin/bash on this system")
        cp = subprocess.run(
            ["/bin/bash", "-n", str(INSTALL_SH)],
            capture_output=True, text=True, check=False)
        self.assertEqual(
            cp.returncode, 0,
            f"bash -n failed:\n{cp.stderr or cp.stdout}")

    def test_subcommands_present(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        for cmd in ("install", "uninstall", "status", "test-fire"):
            self.assertIn(f"cmd_{cmd.replace('-', '_')}", text,
                f"missing handler for subcommand {cmd!r}")
            self.assertIn(cmd, text,
                f"missing dispatch case for {cmd!r}")

    def test_strict_mode_enabled(self):
        """``set -euo pipefail`` is the contract that lets the
        install fail loudly on a bad sed substitution."""
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)

    def test_template_substitution_validates_placeholders(self):
        """If a placeholder survives the sed step we MUST abort —
        otherwise the rendered plist would silently be invalid."""
        text = INSTALL_SH.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"grep -q .*__PYTHON__.*__REPO_ROOT__.*\.tmp",
            "install script must check for unresolved placeholders")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
