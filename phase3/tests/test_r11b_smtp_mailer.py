"""R11B — post-trade SMTP mailer.

Surfaces under test:

1. ``SmtpConfig.from_env`` parses env vars correctly, defaults match
   the documented contract, ``__repr__`` hides the password.
2. ``SmtpConfig.is_complete`` enforces the documented matrix
   (required fields, dry-run relaxation, SMTP_DISABLED short-circuit).
3. ``compose_run_summary_mail`` builds a payload with:
   * the correct subject (status=ok vs halted)
   * a deterministic body opener
   * attachments for every artifact that exists on disk
   * graceful degradation when artifacts are missing
4. ``send_mail`` against a fake SMTP factory:
   * connect / starttls / login / send_message called in order
   * dry-run path skips the SMTP factory entirely and returns ok=True
   * exceptions inside the factory are caught and returned as
     ok=False reason=<ClassName>:<msg>, NEVER raised
5. ``send_run_summary_mail`` end-to-end against a real-ish run_dir
   fixture and a fake SMTP factory.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import smtp_mailer as sm


# ──────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────
class _FakeSmtp:
    """Test double for ``smtplib.SMTP``. Recording-only; never opens
    a real socket. ``raise_on`` lets a test pin a failure point."""

    def __init__(self, host: str, port: int,
                 *, raise_on: Optional[str] = None):
        self.host = host
        self.port = port
        self.raise_on = raise_on
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.sent_messages: List[EmailMessage] = []
        self.quit_called = False
        if raise_on == "connect":
            raise ConnectionError("simulated dns failure")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.quit_called = True
        return False

    def starttls(self):
        self.calls.append(("starttls", ()))
        if self.raise_on == "starttls":
            raise RuntimeError("simulated tls handshake failure")

    def login(self, username: str, password: str):
        self.calls.append(("login", (username, password)))
        if self.raise_on == "login":
            raise PermissionError("simulated auth failure")

    def send_message(self, msg: EmailMessage):
        self.calls.append(("send_message", ()))
        if self.raise_on == "send":
            raise IOError("simulated send failure")
        self.sent_messages.append(msg)


def _factory(raise_on: Optional[str] = None):
    """Return a callable that yields a _FakeSmtp recorder. We capture
    the instance so the test can inspect it after send_mail returns."""
    captured: Dict[str, Any] = {"instance": None}

    def _make(host: str, port: int) -> _FakeSmtp:
        inst = _FakeSmtp(host, port, raise_on=raise_on)
        captured["instance"] = inst
        return inst
    _make.captured = captured  # type: ignore[attr-defined]
    return _make


# ──────────────────────────────────────────────────────────────────────
# 1. SmtpConfig.from_env
# ──────────────────────────────────────────────────────────────────────
class TestSmtpConfigFromEnv(unittest.TestCase):

    def test_defaults_when_env_empty(self):
        cfg = sm.SmtpConfig.from_env({})
        self.assertEqual(cfg.host, "")
        self.assertEqual(cfg.port, 587)
        self.assertEqual(cfg.to_addrs, ())
        self.assertTrue(cfg.use_tls)
        self.assertFalse(cfg.dry_run)
        self.assertFalse(cfg.disabled)

    def test_parses_full_env(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "2525",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "hunter2",
            "SMTP_FROM": "alerts@example.com",
            "SMTP_TO": "ops@example.com, dev@example.com ",
            "SMTP_USE_TLS": "false",
            "SMTP_DRY_RUN": "true",
        })
        self.assertEqual(cfg.host, "smtp.example.com")
        self.assertEqual(cfg.port, 2525)
        self.assertEqual(cfg.username, "bot@example.com")
        self.assertEqual(cfg.password, "hunter2")
        self.assertEqual(cfg.from_addr, "alerts@example.com")
        self.assertEqual(cfg.to_addrs, ("ops@example.com", "dev@example.com"))
        self.assertFalse(cfg.use_tls)
        self.assertTrue(cfg.dry_run)

    def test_from_defaults_to_username_when_unset(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_USERNAME": "bot@example.com",
        })
        self.assertEqual(cfg.from_addr, "bot@example.com")

    def test_invalid_port_falls_back_to_587(self):
        cfg = sm.SmtpConfig.from_env({"SMTP_PORT": "not-a-number"})
        self.assertEqual(cfg.port, 587)

    def test_repr_hides_password(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_PASSWORD": "supersecret",
        })
        rep = repr(cfg)
        self.assertNotIn("supersecret", rep)
        self.assertIn("chars hidden", rep)


# ──────────────────────────────────────────────────────────────────────
# 2. SmtpConfig.is_complete
# ──────────────────────────────────────────────────────────────────────
class TestSmtpConfigIsComplete(unittest.TestCase):

    def test_disabled_short_circuits(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_DISABLED": "true",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "u",
            "SMTP_PASSWORD": "p",
            "SMTP_TO": "ops@example.com",
        })
        ok, why = cfg.is_complete()
        self.assertFalse(ok)
        self.assertEqual(why, "SMTP_DISABLED=true")

    def test_empty_to_is_incomplete(self):
        cfg = sm.SmtpConfig.from_env({"SMTP_HOST": "x"})
        ok, why = cfg.is_complete()
        self.assertFalse(ok)
        self.assertIn("SMTP_TO", why)

    def test_dry_run_only_needs_to_addrs(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_DRY_RUN": "true",
            "SMTP_TO": "ops@example.com",
        })
        ok, why = cfg.is_complete()
        self.assertTrue(ok, f"expected ok, got reason={why!r}")

    def test_live_mode_requires_full_set(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_TO": "ops@example.com",
        })
        ok, why = cfg.is_complete()
        self.assertFalse(ok)
        for token in ("SMTP_HOST", "SMTP_USERNAME",
                      "SMTP_PASSWORD", "SMTP_FROM"):
            self.assertIn(token, why)

    def test_live_mode_complete(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "hunter2",
            "SMTP_TO": "ops@example.com",
        })
        ok, _ = cfg.is_complete()
        self.assertTrue(ok)


# ──────────────────────────────────────────────────────────────────────
# 3. compose_run_summary_mail
# ──────────────────────────────────────────────────────────────────────
def _seed_run_dir(td: str, *, with_t10: bool = True,
                  with_marker: bool = True,
                  with_daily: bool = True) -> Path:
    rd = Path(td) / "run_dir"
    rd.mkdir(parents=True, exist_ok=True)
    if with_daily:
        (rd / "autotrade_daily_report.md").write_text(
            "# fake daily report\nbody-line\n", encoding="utf-8")
    if with_t10:
        (rd / "autotrade_t10_apply_report.md").write_text(
            "# fake t10 report\n", encoding="utf-8")
    if with_marker:
        (rd / "autotrade_oneclick_marker.json").write_text(
            '{"schema_version":"autotrade_oneclick_marker/v1"}',
            encoding="utf-8")
    return rd


class TestComposeRunSummaryMail(unittest.TestCase):

    def test_ok_subject_uses_ok_status(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="20260522_test",
                profile="paper",
                overall_rc=0, halt_reason=None,
                duration_sec=34.0,
                stage_outcomes=[],
            )
            self.assertEqual(
                payload.subject,
                "[Autotrade paper] 20260522_test — ok")
            self.assertIn("overall_rc   : 0", payload.body_text)
            self.assertIn("halt_reason  : (none)", payload.body_text)

    def test_halt_subject_uses_halted_status(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="20260522_test",
                profile="paper",
                overall_rc=2, halt_reason="non_zero_rc: rc=2",
                duration_sec=12.5,
                stage_outcomes=[],
            )
            self.assertTrue(payload.subject.endswith("— halted"))
            self.assertIn("non_zero_rc", payload.body_text)

    def test_attachments_present_for_existing_files(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="20260522_test",
                profile="paper",
                overall_rc=0, halt_reason=None,
                duration_sec=1.0,
                stage_outcomes=[],
            )
            names = {a.filename for a in payload.attachments}
            self.assertIn("autotrade_daily_report.md", names)
            self.assertIn("autotrade_t10_apply_report.md", names)
            self.assertIn("autotrade_oneclick_marker.json", names)

    def test_missing_t10_report_gracefully_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td, with_t10=False)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="X", profile="paper",
                overall_rc=0, halt_reason=None,
                duration_sec=0.5, stage_outcomes=[],
            )
            names = {a.filename for a in payload.attachments}
            self.assertNotIn("autotrade_t10_apply_report.md", names)

    def test_missing_daily_report_body_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td, with_daily=False)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="X", profile="paper",
                overall_rc=0, halt_reason=None,
                duration_sec=0.5, stage_outcomes=[],
            )
            self.assertIn("no autotrade_daily_report.md",
                          payload.body_text)

    def test_stage_outcomes_render_with_rc_and_duration(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td)
            payload = sm.compose_run_summary_mail(
                run_dir=rd, run_id="X", profile="paper",
                overall_rc=2, halt_reason="non_zero_rc: rc=2",
                duration_sec=10.0,
                stage_outcomes=[
                    {"key": "generate_intents", "rc": 0,
                     "duration_sec": 2.1, "halt_reason": None,
                     "skipped": False},
                    {"key": "dry_run", "rc": 0,
                     "duration_sec": 6.1, "halt_reason": None,
                     "skipped": False},
                    {"key": "paper_submit", "rc": 2,
                     "duration_sec": 1.8,
                     "halt_reason": "non_zero_rc: rc=2",
                     "skipped": False},
                    {"key": "t10_apply", "rc": -1,
                     "duration_sec": 0.0, "halt_reason": "skipped",
                     "skipped": True},
                ],
            )
            self.assertIn("generate_intents", payload.body_text)
            self.assertIn("rc=2", payload.body_text)
            self.assertIn("SKIPPED", payload.body_text)


# ──────────────────────────────────────────────────────────────────────
# 4. send_mail with a fake SMTP factory
# ──────────────────────────────────────────────────────────────────────
def _ok_cfg() -> sm.SmtpConfig:
    return sm.SmtpConfig.from_env({
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "bot@example.com",
        "SMTP_PASSWORD": "hunter2",
        "SMTP_TO": "ops@example.com",
    })


class TestSendMailHappyPath(unittest.TestCase):

    def test_connects_logins_sends_in_order(self):
        cfg = _ok_cfg()
        factory = _factory()
        payload = sm.MailPayload(subject="s", body_text="b")
        result = sm.send_mail(payload, cfg, smtp_factory=factory)
        self.assertTrue(result.ok, f"reason={result.reason}")
        inst: _FakeSmtp = factory.captured["instance"]
        call_names = [c[0] for c in inst.calls]
        self.assertEqual(
            call_names, ["starttls", "login", "send_message"])
        login_args = inst.calls[1][1]
        self.assertEqual(login_args, ("bot@example.com", "hunter2"))
        self.assertEqual(len(inst.sent_messages), 1)
        sent = inst.sent_messages[0]
        self.assertEqual(sent["Subject"], "s")
        self.assertEqual(sent["From"], "bot@example.com")
        self.assertEqual(sent["To"], "ops@example.com")

    def test_use_tls_false_skips_starttls(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "hunter2",
            "SMTP_TO": "ops@example.com",
            "SMTP_USE_TLS": "false",
        })
        factory = _factory()
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertTrue(result.ok)
        inst: _FakeSmtp = factory.captured["instance"]
        call_names = [c[0] for c in inst.calls]
        self.assertNotIn("starttls", call_names)
        self.assertEqual(call_names, ["login", "send_message"])


class TestSendMailDryRun(unittest.TestCase):

    def test_dry_run_does_not_use_factory(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_TO": "ops@example.com",
            "SMTP_DRY_RUN": "true",
        })
        factory = _factory()
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.reason, "dry_run")
        self.assertIsNone(factory.captured["instance"])
        self.assertGreater(result.bytes_sent, 0)


class TestSendMailFailureModes(unittest.TestCase):

    def test_disabled_returns_not_ok_without_factory(self):
        cfg = sm.SmtpConfig.from_env({
            "SMTP_DISABLED": "true",
            "SMTP_TO": "ops@example.com",
        })
        factory = _factory()
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "SMTP_DISABLED=true")
        self.assertIsNone(factory.captured["instance"])

    def test_incomplete_returns_not_ok_without_factory(self):
        cfg = sm.SmtpConfig.from_env({"SMTP_TO": "ops@example.com"})
        factory = _factory()
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertIn("missing", result.reason)
        self.assertIsNone(factory.captured["instance"])

    def test_connect_exception_caught(self):
        cfg = _ok_cfg()
        factory = _factory(raise_on="connect")
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.reason)
        self.assertIn("dns", result.reason)

    def test_starttls_exception_caught(self):
        cfg = _ok_cfg()
        factory = _factory(raise_on="starttls")
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertIn("tls", result.reason.lower())

    def test_login_exception_caught(self):
        cfg = _ok_cfg()
        factory = _factory(raise_on="login")
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertIn("PermissionError", result.reason)

    def test_send_exception_caught(self):
        cfg = _ok_cfg()
        factory = _factory(raise_on="send")
        result = sm.send_mail(
            sm.MailPayload(subject="s", body_text="b"),
            cfg, smtp_factory=factory)
        self.assertFalse(result.ok)
        self.assertIn("simulated send", result.reason)


# ──────────────────────────────────────────────────────────────────────
# 5. send_run_summary_mail end-to-end
# ──────────────────────────────────────────────────────────────────────
class TestSendRunSummaryMailEndToEnd(unittest.TestCase):

    def test_end_to_end_with_fake_factory(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _seed_run_dir(td)
            factory = _factory()
            env = {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_USERNAME": "bot@example.com",
                "SMTP_PASSWORD": "hunter2",
                "SMTP_TO": "ops@example.com, dev@example.com",
            }
            result = sm.send_run_summary_mail(
                run_dir=rd, run_id="20260522_x",
                profile="paper",
                overall_rc=0, halt_reason=None,
                duration_sec=34.0,
                stage_outcomes=[
                    {"key": "generate_intents", "rc": 0,
                     "duration_sec": 2.1, "halt_reason": None,
                     "skipped": False},
                ],
                env=env, smtp_factory=factory,
            )
            self.assertTrue(result.ok, f"reason={result.reason}")
            inst: _FakeSmtp = factory.captured["instance"]
            sent = inst.sent_messages[0]
            self.assertEqual(
                sent["Subject"],
                "[Autotrade paper] 20260522_x — ok")
            # Attachments survived round-trip.
            parts = list(sent.iter_attachments())
            names = {p.get_filename() for p in parts}
            self.assertIn("autotrade_daily_report.md", names)
            self.assertIn("autotrade_oneclick_marker.json", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
