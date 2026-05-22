"""R11B — post-trade SMTP mailer.

Background
----------

By the time the R11A one-click coordinator returns, every artifact
the operator needs is already on disk:

* ``autotrade_daily_report.md`` — pre-reconcile + per-order outcomes
* ``autotrade_t10_apply_report.md`` — what hit holdings_log
* ``autotrade_oneclick_marker.json`` — stage timing / halt reason

The operator's missing link is: they're not sitting in front of the
panel for the whole 30-second-to-3-minute paper-run window, so when
something halts at stage 2 they don't see the popup until the next
time they switch back to the panel. R11B closes that loop with a
plain-old SMTP post: "run X finished, status Y, here is the report".

Design
------

This module is intentionally:

* **Self-contained** — no MIME library beyond ``email.message``,
  no third-party SMTP libraries. Standard ``smtplib`` only.
* **Credential-safe** — secrets live in environment variables; the
  ``SmtpConfig`` dataclass never exposes them to ``__repr__`` /
  ``__str__`` / logs. ``EnvConfig``-style isolation.
* **Failure-tolerant** — ``send_run_summary_mail`` returns a
  ``MailDispatchResult`` carrying ok/error info, never raises into
  the coordinator. R11A's overall_rc is independent of mail outcome.
* **Dry-run capable** — ``SMTP_DRY_RUN=true`` short-circuits the
  ``smtplib.SMTP`` connect/login/send, writes the would-be payload
  to a log file beside the run, and returns ok=True. Useful for
  testing real config without spamming inboxes.

Environment contract
--------------------

==================  =================  ==============================
Variable            Required?          Default
==================  =================  ==============================
SMTP_HOST           yes (unless dry)   —
SMTP_PORT           no                 587
SMTP_USERNAME       yes (unless dry)   —
SMTP_PASSWORD       yes (unless dry)   —
SMTP_FROM           yes (unless dry)   = SMTP_USERNAME
SMTP_TO             yes (unless dry)   —
SMTP_USE_TLS        no                 true
SMTP_DRY_RUN        no                 false
SMTP_DISABLED       no                 false
==================  =================  ==============================

When ``SMTP_DISABLED=true``, ``send_run_summary_mail`` returns ok=False
with reason=``disabled_by_env`` immediately — useful for staging
environments where the daily run runs unattended but no mailbox is
provisioned yet.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


_LOG = logging.getLogger(__name__)

_TRUE_STRINGS = frozenset({"true", "1", "yes", "y", "on"})


def _env_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_STRINGS


# ──────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SmtpConfig:
    """SMTP credentials + delivery preferences.

    ``__repr__`` is overridden to keep passwords out of any accidental
    log line / panel error popup. Field access is explicit and the
    only thing that ever reads ``password`` is :func:`send_mail`.
    """
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    to_addrs: Tuple[str, ...]
    use_tls: bool
    dry_run: bool
    disabled: bool

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SmtpConfig":
        e = dict(env if env is not None else os.environ)
        host = e.get("SMTP_HOST", "").strip()
        port_raw = e.get("SMTP_PORT", "").strip()
        try:
            port = int(port_raw) if port_raw else 587
        except (TypeError, ValueError):
            port = 587
        username = e.get("SMTP_USERNAME", "").strip()
        password = e.get("SMTP_PASSWORD", "")  # do NOT strip — some hashes have trailing chars
        from_addr = e.get("SMTP_FROM", "").strip() or username
        to_raw = e.get("SMTP_TO", "").strip()
        # SMTP_TO is comma-separated.
        to_addrs = tuple(
            x.strip() for x in to_raw.split(",") if x.strip()
        )
        use_tls = _env_bool(e, "SMTP_USE_TLS", True)
        dry_run = _env_bool(e, "SMTP_DRY_RUN", False)
        disabled = _env_bool(e, "SMTP_DISABLED", False)
        return cls(
            host=host, port=port,
            username=username, password=password,
            from_addr=from_addr, to_addrs=to_addrs,
            use_tls=use_tls, dry_run=dry_run, disabled=disabled,
        )

    def __repr__(self) -> str:  # pragma: no cover — convenience
        return (
            f"SmtpConfig(host={self.host!r}, port={self.port}, "
            f"username={self.username!r}, "
            f"password=<{len(self.password)} chars hidden>, "
            f"from_addr={self.from_addr!r}, "
            f"to_addrs={self.to_addrs}, use_tls={self.use_tls}, "
            f"dry_run={self.dry_run}, disabled={self.disabled})"
        )

    def is_complete(self) -> Tuple[bool, str]:
        """Return ``(True, '')`` if the config has the minimum fields
        needed to actually attempt delivery. ``dry_run=True`` relaxes
        the host/credential requirements — handy for offline tests."""
        if self.disabled:
            return False, "SMTP_DISABLED=true"
        if not self.to_addrs:
            return False, "SMTP_TO is empty"
        if self.dry_run:
            return True, ""
        missing: List[str] = []
        if not self.host:
            missing.append("SMTP_HOST")
        if not self.username:
            missing.append("SMTP_USERNAME")
        if not self.password:
            missing.append("SMTP_PASSWORD")
        if not self.from_addr:
            missing.append("SMTP_FROM")
        if missing:
            return False, f"missing: {', '.join(missing)}"
        return True, ""


# ──────────────────────────────────────────────────────────────────────
# Payload
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content: bytes
    mime_subtype: str = "plain"  # 'plain' for .md/.txt, 'json' for .json


@dataclass(frozen=True)
class MailPayload:
    subject: str
    body_text: str
    attachments: Tuple[MailAttachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MailDispatchResult:
    ok: bool
    reason: str               # "" on success, free-form diagnostic on failure
    dry_run: bool
    bytes_sent: int           # rough size estimate; 0 on failure / dry-run


# ──────────────────────────────────────────────────────────────────────
# Compose
# ──────────────────────────────────────────────────────────────────────
def compose_run_summary_mail(
    *,
    run_dir: Path,
    run_id: str,
    profile: str,
    overall_rc: int,
    halt_reason: Optional[str],
    duration_sec: float,
    stage_outcomes: List[Dict[str, Any]],
) -> MailPayload:
    """Build the mail payload from artifacts the R11A coordinator
    already wrote.

    Body shape:

        Subject:  [Autotrade <profile>] <run_id> — <status>
        Body:
          one-line headline (rc + halt_reason + duration)
          stage table
          <blank>
          <verbatim autotrade_daily_report.md if present>
        Attachments:
          autotrade_daily_report.md          (always, if it exists)
          autotrade_t10_apply_report.md      (if it exists)
          autotrade_oneclick_marker.json     (if it exists)

    All file reads are best-effort: a missing artifact just turns into
    "(no daily report on disk)" in the body so the operator can still
    triage from the headline and the marker JSON.
    """
    rd = Path(run_dir)
    status = "ok" if (overall_rc == 0 and not halt_reason) else "halted"
    subject = f"[Autotrade {profile}] {run_id} — {status}"

    lines: List[str] = []
    lines.append(f"Autotrade run {run_id}")
    lines.append(f"  profile      : {profile}")
    lines.append(f"  status       : {status}")
    lines.append(f"  overall_rc   : {overall_rc}")
    lines.append(f"  halt_reason  : {halt_reason or '(none)'}")
    lines.append(f"  duration_sec : {duration_sec:.1f}")
    lines.append(f"  generated_at : "
                  f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("Stages:")
    for so in stage_outcomes:
        rc = so.get("rc", "?")
        dur = so.get("duration_sec", 0.0)
        halted = so.get("halt_reason")
        skipped = so.get("skipped", False)
        if skipped:
            lines.append(f"  - {so.get('key','?'):<18} SKIPPED")
        else:
            tail = f"  halt={halted}" if halted else ""
            lines.append(
                f"  - {so.get('key','?'):<18} rc={rc}  "
                f"{dur:>6.1f}s{tail}")
    lines.append("")

    daily_md_path = rd / "autotrade_daily_report.md"
    if daily_md_path.exists():
        try:
            lines.append("── autotrade_daily_report.md ─────────────────")
            lines.append("")
            lines.append(daily_md_path.read_text(encoding="utf-8"))
        except OSError:
            lines.append("(failed to read autotrade_daily_report.md)")
    else:
        lines.append("(no autotrade_daily_report.md on disk)")

    body_text = "\n".join(lines) + "\n"

    attachments: List[MailAttachment] = []
    for fname, subtype in (
        ("autotrade_daily_report.md",       "plain"),
        ("autotrade_t10_apply_report.md",   "plain"),
        ("autotrade_oneclick_marker.json",  "json"),
    ):
        p = rd / fname
        if not p.exists():
            continue
        try:
            attachments.append(MailAttachment(
                filename=fname,
                content=p.read_bytes(),
                mime_subtype=subtype,
            ))
        except OSError:
            continue

    return MailPayload(
        subject=subject,
        body_text=body_text,
        attachments=tuple(attachments),
    )


# ──────────────────────────────────────────────────────────────────────
# Send
# ──────────────────────────────────────────────────────────────────────
def _build_message(payload: MailPayload, cfg: SmtpConfig) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = payload.subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.set_content(payload.body_text)
    for att in payload.attachments:
        maintype = "text" if att.mime_subtype in ("plain",) else "application"
        msg.add_attachment(
            att.content,
            maintype=maintype,
            subtype=att.mime_subtype,
            filename=att.filename,
        )
    return msg


def send_mail(
    payload: MailPayload,
    cfg: SmtpConfig,
    *,
    smtp_factory: Optional[Any] = None,
) -> MailDispatchResult:
    """Deliver ``payload`` via SMTP.

    ``smtp_factory`` lets tests inject a fake ``smtplib.SMTP``-like
    class (must support ``starttls / login / send_message / quit``).
    Production callers leave it ``None``; we use the real
    ``smtplib.SMTP``.

    On exception we capture and return a MailDispatchResult with
    ``ok=False`` and ``reason=<class>:<msg>``. We never propagate the
    exception because the daily run is already complete by the time
    we get here.
    """
    ok, why = cfg.is_complete()
    if not ok:
        return MailDispatchResult(
            ok=False, reason=why, dry_run=cfg.dry_run, bytes_sent=0)

    msg = _build_message(payload, cfg)
    msg_bytes = bytes(msg)
    if cfg.dry_run:
        return MailDispatchResult(
            ok=True, reason="dry_run",
            dry_run=True, bytes_sent=len(msg_bytes))

    factory = smtp_factory if smtp_factory is not None else smtplib.SMTP
    try:
        with factory(cfg.host, cfg.port) as smtp:
            if cfg.use_tls:
                smtp.starttls()
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)
    except Exception as e:  # noqa: BLE001
        return MailDispatchResult(
            ok=False,
            reason=f"{type(e).__name__}: {e}",
            dry_run=False, bytes_sent=0)
    return MailDispatchResult(
        ok=True, reason="", dry_run=False, bytes_sent=len(msg_bytes))


# ──────────────────────────────────────────────────────────────────────
# One-shot helper for the R11A hook
# ──────────────────────────────────────────────────────────────────────
def send_run_summary_mail(
    *,
    run_dir: Path,
    run_id: str,
    profile: str,
    overall_rc: int,
    halt_reason: Optional[str],
    duration_sec: float,
    stage_outcomes: List[Dict[str, Any]],
    env: Optional[Mapping[str, str]] = None,
    smtp_factory: Optional[Any] = None,
) -> MailDispatchResult:
    """Convenience wrapper used by the R11A coordinator (and
    the panel's manual "send report" button if we add one): build the
    payload, build the config from env, deliver.

    Always returns a result — never raises. The caller is expected to
    log the result and otherwise ignore failures (the daily run is
    already finished).
    """
    cfg = SmtpConfig.from_env(env)
    payload = compose_run_summary_mail(
        run_dir=Path(run_dir),
        run_id=run_id,
        profile=profile,
        overall_rc=overall_rc,
        halt_reason=halt_reason,
        duration_sec=duration_sec,
        stage_outcomes=stage_outcomes,
    )
    return send_mail(payload, cfg, smtp_factory=smtp_factory)
