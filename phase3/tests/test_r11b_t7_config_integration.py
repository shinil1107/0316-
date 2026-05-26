"""R11B ↔ T7 config integration tests.

These tests pin the credential-source resolution that lets R11B send
post-trade run summaries through the SAME Gmail mailbox the T7
``phase3/mailer`` already uses for the daily recommendation mail. No
new SMTP_* env vars required — operators who configured T7 once get
R11B for free.

Schema we honour (subset of ``phase3/config_real.yaml``):

    email:
      enabled: true
      gmail_address: bot@gmail.com
      gmail_app_password: ${GMAIL_APP_PASSWORD}     # or literal
      recipient: ops@gmail.com
      # R11B-only optional knobs:
      dry_run: false                                 # forces dry-run
      additional_recipients: [team@example.com]      # adds to To:

Password resolution mirrors ``phase3.mailer._resolve_password``:
1. ``raw`` literal (if it does not start with ``${``)
2. ``$GMAIL_APP_PASSWORD`` env
3. ``email.gmail_app_password`` in ``config.local.yaml``
4. empty (is_complete reports missing)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase3.autotrade import smtp_mailer as sm


# ──────────────────────────────────────────────────────────────────────
# SmtpConfig.from_t7_config
# ──────────────────────────────────────────────────────────────────────
class TestSmtpConfigFromT7Config(unittest.TestCase):

    def test_literal_password_used_directly(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "abcd-efgh-ijkl-mnop",
                "recipient": "ops@gmail.com",
            }
        }, env={})
        self.assertEqual(cfg.host, "smtp.gmail.com")
        self.assertEqual(cfg.port, 465)
        self.assertTrue(cfg.use_ssl)
        self.assertFalse(cfg.use_tls)
        self.assertEqual(cfg.username, "bot@gmail.com")
        self.assertEqual(cfg.from_addr, "bot@gmail.com")
        self.assertEqual(cfg.password, "abcd-efgh-ijkl-mnop")
        self.assertEqual(cfg.to_addrs, ("ops@gmail.com",))
        self.assertEqual(cfg.source, "t7_config")
        self.assertFalse(cfg.disabled)
        ok, why = cfg.is_complete()
        self.assertTrue(ok, f"expected complete, got {why!r}")

    def test_placeholder_resolves_from_env(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "${GMAIL_APP_PASSWORD}",
                "recipient": "ops@gmail.com",
            }
        }, env={"GMAIL_APP_PASSWORD": "from-env-shell"})
        self.assertEqual(cfg.password, "from-env-shell")
        self.assertTrue(cfg.is_complete()[0])

    def test_placeholder_falls_back_to_config_local(self):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "config.local.yaml"
            local.write_text(
                "email:\n"
                "  gmail_app_password: 'from-local-yaml'\n",
                encoding="utf-8",
            )
            cfg = sm.SmtpConfig.from_t7_config(
                {
                    "email": {
                        "enabled": True,
                        "gmail_address": "bot@gmail.com",
                        "gmail_app_password": "${GMAIL_APP_PASSWORD}",
                        "recipient": "ops@gmail.com",
                    }
                },
                env={},  # no GMAIL_APP_PASSWORD in env
                config_local_path=local,
            )
            self.assertEqual(cfg.password, "from-local-yaml")

    def test_env_beats_config_local(self):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "config.local.yaml"
            local.write_text(
                "email:\n  gmail_app_password: 'local'\n",
                encoding="utf-8",
            )
            cfg = sm.SmtpConfig.from_t7_config(
                {"email": {
                    "enabled": True,
                    "gmail_address": "bot@gmail.com",
                    "gmail_app_password": "${GMAIL_APP_PASSWORD}",
                    "recipient": "ops@gmail.com",
                }},
                env={"GMAIL_APP_PASSWORD": "env-wins"},
                config_local_path=local,
            )
            self.assertEqual(cfg.password, "env-wins")

    def test_disabled_when_email_enabled_false(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": False,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
            }
        }, env={})
        self.assertTrue(cfg.disabled)
        ok, why = cfg.is_complete()
        self.assertFalse(ok)
        # The diagnostic must point at the T7 yaml — operators must
        # not chase a non-existent SMTP_DISABLED env var when their
        # actual issue is yaml-side.
        self.assertEqual(why, "email.enabled=false in T7 config")

    def test_disabled_when_smtp_disabled_env_set(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
            }
        }, env={"SMTP_DISABLED": "true"})
        self.assertTrue(cfg.disabled)

    def test_recipient_falls_back_to_gmail_address(self):
        """T7 sometimes omits ``recipient`` (the operator self-sends).
        We must mirror that — use ``gmail_address`` as both From and To."""
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "solo@gmail.com",
                "gmail_app_password": "x",
            }
        }, env={})
        self.assertEqual(cfg.to_addrs, ("solo@gmail.com",))

    def test_additional_recipients_appended(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
                "additional_recipients": [
                    "team@example.com",
                    "audit@example.com",
                ],
            }
        }, env={})
        self.assertEqual(
            cfg.to_addrs,
            ("ops@gmail.com", "team@example.com", "audit@example.com"),
        )

    def test_additional_recipients_dedup(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
                "additional_recipients": ["ops@gmail.com", "team@example.com"],
            }
        }, env={})
        # ops@gmail.com must appear only once.
        self.assertEqual(
            cfg.to_addrs, ("ops@gmail.com", "team@example.com"))

    def test_dry_run_from_yaml(self):
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
                "dry_run": True,
            }
        }, env={})
        self.assertTrue(cfg.dry_run)

    def test_dry_run_env_overrides_yaml(self):
        """Allow flipping to dry-run for a single shell session
        without editing yaml."""
        cfg = sm.SmtpConfig.from_t7_config({
            "email": {
                "enabled": True,
                "gmail_address": "bot@gmail.com",
                "gmail_app_password": "x",
                "recipient": "ops@gmail.com",
            }
        }, env={"SMTP_DRY_RUN": "true"})
        self.assertTrue(cfg.dry_run)

    def test_missing_email_block_is_disabled(self):
        cfg = sm.SmtpConfig.from_t7_config({}, env={})
        self.assertTrue(cfg.disabled)
        self.assertFalse(cfg.is_complete()[0])


# ──────────────────────────────────────────────────────────────────────
# _resolve_t7_password — order matters
# ──────────────────────────────────────────────────────────────────────
class TestResolveT7Password(unittest.TestCase):

    def test_literal_short_circuits(self):
        pwd = sm._resolve_t7_password(
            raw="literal-pwd",
            env={"GMAIL_APP_PASSWORD": "should-be-ignored"},
        )
        self.assertEqual(pwd, "literal-pwd")

    def test_placeholder_consults_env(self):
        pwd = sm._resolve_t7_password(
            raw="${GMAIL_APP_PASSWORD}",
            env={"GMAIL_APP_PASSWORD": "env-pwd"},
        )
        self.assertEqual(pwd, "env-pwd")

    def test_empty_raw_consults_env(self):
        pwd = sm._resolve_t7_password(
            raw="",
            env={"GMAIL_APP_PASSWORD": "env-pwd"},
        )
        self.assertEqual(pwd, "env-pwd")

    def test_no_env_no_local_returns_empty(self):
        pwd = sm._resolve_t7_password(
            raw="${GMAIL_APP_PASSWORD}",
            env={},
            config_local_path=Path("/no/such/file.yaml"),
        )
        self.assertEqual(pwd, "")


# ──────────────────────────────────────────────────────────────────────
# resolve_smtp_config — T7 vs env priority
# ──────────────────────────────────────────────────────────────────────
class TestResolveSmtpConfigPriority(unittest.TestCase):

    def _write_t7(self, td: Path, enabled: bool = True,
                  password: str = "literal-pwd") -> Path:
        p = td / "config_real.yaml"
        p.write_text(
            "email:\n"
            f"  enabled: {'true' if enabled else 'false'}\n"
            "  gmail_address: bot@gmail.com\n"
            f"  gmail_app_password: '{password}'\n"
            "  recipient: ops@gmail.com\n",
            encoding="utf-8",
        )
        return p

    def test_t7_preferred_when_complete(self):
        with tempfile.TemporaryDirectory() as td:
            t7 = self._write_t7(Path(td))
            cfg = sm.resolve_smtp_config(
                env={
                    # Direct env path is ALSO complete to make sure
                    # T7 actually wins on priority.
                    "SMTP_HOST": "smtp.somewhere.com",
                    "SMTP_USERNAME": "u",
                    "SMTP_PASSWORD": "p",
                    "SMTP_FROM": "f@example.com",
                    "SMTP_TO": "to@example.com",
                },
                t7_config_path=t7,
                read_dotenv=False,
            )
            self.assertEqual(cfg.source, "t7_config")
            self.assertEqual(cfg.host, "smtp.gmail.com")
            self.assertEqual(cfg.username, "bot@gmail.com")

    def test_falls_through_to_env_when_t7_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            t7 = self._write_t7(Path(td), enabled=False)
            cfg = sm.resolve_smtp_config(
                env={
                    "SMTP_HOST": "smtp.somewhere.com",
                    "SMTP_USERNAME": "u",
                    "SMTP_PASSWORD": "p",
                    "SMTP_FROM": "f@example.com",
                    "SMTP_TO": "to@example.com",
                },
                t7_config_path=t7,
                read_dotenv=False,
            )
            self.assertEqual(cfg.source, "env")
            self.assertEqual(cfg.host, "smtp.somewhere.com")

    def test_falls_through_to_env_when_t7_missing_password(self):
        """A T7 config with enabled=true but no resolvable password is
        treated as 'not configured' so we fall back to SMTP_* env."""
        with tempfile.TemporaryDirectory() as td:
            t7 = Path(td) / "config_real.yaml"
            t7.write_text(
                "email:\n"
                "  enabled: true\n"
                "  gmail_address: bot@gmail.com\n"
                "  gmail_app_password: '${GMAIL_APP_PASSWORD}'\n"
                "  recipient: ops@gmail.com\n",
                encoding="utf-8",
            )
            cfg = sm.resolve_smtp_config(
                env={
                    # No GMAIL_APP_PASSWORD here on purpose — T7 will
                    # have empty password and is_complete() returns False.
                    "SMTP_HOST": "smtp.somewhere.com",
                    "SMTP_USERNAME": "u",
                    "SMTP_PASSWORD": "p",
                    "SMTP_FROM": "f@example.com",
                    "SMTP_TO": "to@example.com",
                },
                t7_config_path=t7,
                config_local_path=Path("/no/such/file.yaml"),
                read_dotenv=False,
            )
            self.assertEqual(cfg.source, "env")

    def test_prefer_env_inverts_priority(self):
        with tempfile.TemporaryDirectory() as td:
            t7 = self._write_t7(Path(td))
            cfg = sm.resolve_smtp_config(
                env={
                    "SMTP_HOST": "smtp.somewhere.com",
                    "SMTP_USERNAME": "u",
                    "SMTP_PASSWORD": "p",
                    "SMTP_FROM": "f@example.com",
                    "SMTP_TO": "to@example.com",
                },
                t7_config_path=t7,
                prefer="env",
                read_dotenv=False,
            )
            self.assertEqual(cfg.source, "env")

    def test_dry_run_t7_is_still_picked(self):
        """A T7 config with dry_run=true is 'complete enough' to win
        over env. This is how an operator stages T7 mail without
        actually delivering."""
        with tempfile.TemporaryDirectory() as td:
            t7 = Path(td) / "config_real.yaml"
            t7.write_text(
                "email:\n"
                "  enabled: true\n"
                "  gmail_address: bot@gmail.com\n"
                "  gmail_app_password: 'doesnt-matter-in-dryrun'\n"
                "  recipient: ops@gmail.com\n"
                "  dry_run: true\n",
                encoding="utf-8",
            )
            cfg = sm.resolve_smtp_config(
                env={}, t7_config_path=t7, read_dotenv=False)
            self.assertEqual(cfg.source, "t7_config")
            self.assertTrue(cfg.dry_run)


# ──────────────────────────────────────────────────────────────────────
# send_mail wiring — SMTP_SSL must NOT call starttls()
# ──────────────────────────────────────────────────────────────────────
class _RecordingSmtp:
    """Fake SMTP context manager that records what was called and
    asserts starttls is never invoked when caller wants SSL."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.calls: list = []

    def __enter__(self):
        self.calls.append(("connect", self.host, self.port))
        return self

    def __exit__(self, *exc):
        self.calls.append(("close",))
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, pwd):
        self.calls.append(("login", user, "<pwd>"))

    def send_message(self, msg):
        self.calls.append(("send_message", msg["Subject"]))


class TestSendMailSslWiring(unittest.TestCase):

    def _payload(self) -> sm.MailPayload:
        return sm.MailPayload(
            subject="hello", body_text="body", attachments=tuple())

    def test_ssl_path_skips_starttls(self):
        recorded: list = []

        def factory(host, port):
            obj = _RecordingSmtp(host, port)
            recorded.append(obj)
            return obj

        cfg = sm.SmtpConfig(
            host="smtp.gmail.com", port=465,
            username="bot@gmail.com", password="pw",
            from_addr="bot@gmail.com",
            to_addrs=("ops@gmail.com",),
            use_tls=False, use_ssl=True,
            dry_run=False, disabled=False,
            source="t7_config",
        )
        result = sm.send_mail(self._payload(), cfg,
                              smtp_factory=factory)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(len(recorded), 1)
        calls = [c[0] for c in recorded[0].calls]
        self.assertNotIn("starttls", calls,
                         f"SMTP_SSL path must not call starttls; got {calls}")
        self.assertIn("login", calls)
        self.assertIn("send_message", calls)

    def test_starttls_path_calls_starttls(self):
        recorded: list = []

        def factory(host, port):
            obj = _RecordingSmtp(host, port)
            recorded.append(obj)
            return obj

        cfg = sm.SmtpConfig(
            host="smtp.example.com", port=587,
            username="u", password="p",
            from_addr="f@example.com",
            to_addrs=("to@example.com",),
            use_tls=True, use_ssl=False,
            dry_run=False, disabled=False,
            source="env",
        )
        result = sm.send_mail(self._payload(), cfg,
                              smtp_factory=factory)
        self.assertTrue(result.ok, result.reason)
        calls = [c[0] for c in recorded[0].calls]
        self.assertIn("starttls", calls)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
