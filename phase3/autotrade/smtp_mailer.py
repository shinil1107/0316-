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

Credential source — two paths
-----------------------------

R11B accepts credentials from EITHER of two sources, in this order:

1. **T7 config** (the existing recommendation-mail block already
   wired into ``phase3/mailer.py``): ``phase3/config_real.yaml`` (or
   ``phase3/config.yaml``) section ``email:`` with the password
   resolved from ``$GMAIL_APP_PASSWORD`` or
   ``phase3/config.local.yaml``.  Operators who already receive the
   T7 daily-recommendation mail get R11B run summaries to the same
   inbox automatically — no new env vars to set.

2. **Direct SMTP env vars** (fallback for non-Gmail setups, or when
   T7 config has ``email.enabled: false``):

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
   SMTP_USE_SSL        no                 false
   SMTP_DRY_RUN        no                 false
   SMTP_DISABLED       no                 false
   ==================  =================  ==============================

When ``SMTP_DISABLED=true`` (or T7's ``email.enabled=false``),
``send_run_summary_mail`` returns ok=False immediately — useful for
staging environments where the daily run runs unattended but no
mailbox is provisioned yet.
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


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent  # /Users/shin-il/PyCharmMiscProject/0316-
_DEFAULT_DOTENV = _REPO_ROOT / ".env"


def _merge_env_with_dotenv(
    env: Optional[Mapping[str, str]],
    dotenv_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Merge explicit ``env`` (or ``os.environ``) with values from
    ``.env`` on disk. Explicit env always wins so a process-level
    override beats the dotenv file — this matches the precedence
    ``kis_broker_adapter.load_env_config`` uses for KIS creds, so
    operators can keep all secrets in one ``.env`` and the SMTP
    section will Just Work without re-exporting in the shell.

    Missing / unreadable .env is silently ignored — we never want
    a misformatted dotenv to brick the daily run; ``is_complete``
    will already report which SMTP_* are missing."""
    explicit = dict(env if env is not None else os.environ)
    dotenv: Dict[str, str] = {}
    path = dotenv_path or _DEFAULT_DOTENV
    try:
        if path.exists():
            # Minimal KEY=VALUE parser — we deliberately do NOT pull in
            # ``kis_broker_adapter._read_dotenv`` to keep this module
            # standalone (no cycle risk).
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                # Strip matched surrounding quotes.
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                dotenv[k] = v
    except OSError:
        dotenv = {}
    # Explicit (process env) wins over dotenv.
    merged: Dict[str, str] = dict(dotenv)
    for k, v in explicit.items():
        if v is not None:
            merged[k] = v
    return merged


def _env_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_STRINGS


_PHASE3_ROOT = _HERE.parent  # /Users/.../0316-/phase3
_DEFAULT_T7_CONFIG_CANDIDATES: Tuple[Path, ...] = (
    _PHASE3_ROOT / "config_real.yaml",
    _PHASE3_ROOT / "config.yaml",
)
_DEFAULT_T7_CONFIG_LOCAL = _PHASE3_ROOT / "config.local.yaml"


def _resolve_t7_password(
    *,
    raw: str,
    env: Mapping[str, str],
    config_local_path: Optional[Path] = None,
) -> str:
    """Reimplementation of ``phase3.mailer._resolve_password`` that
    does not import ``phase3.mailer`` (which would pull pandas /
    yaml only for password resolution). Pure-Python parsing of the
    tiny YAML subset we need (``email.gmail_app_password: ...``).

    Order:

    1. ``raw`` literal (skip if it is a ``${VAR}`` placeholder)
    2. ``env['GMAIL_APP_PASSWORD']``
    3. value at ``email.gmail_app_password`` in
       ``config.local.yaml`` if it exists
    4. ``""``
    """
    raw = raw.strip() if isinstance(raw, str) else ""
    if raw and not raw.startswith("${"):
        return raw
    env_val = env.get("GMAIL_APP_PASSWORD", "")
    if env_val:
        return env_val
    local_path = config_local_path or _DEFAULT_T7_CONFIG_LOCAL
    try:
        if local_path.exists():
            try:
                import yaml  # type: ignore
                with open(local_path, "r", encoding="utf-8") as f:
                    local = yaml.safe_load(f) or {}
                pwd = (local.get("email") or {}).get(
                    "gmail_app_password", "")
                if isinstance(pwd, str):
                    return pwd
            except ImportError:
                # Minimal hand-parser fallback so a missing pyyaml
                # never blocks daily run mail. We only look for
                # the one ``email.gmail_app_password: <value>`` line.
                in_email = False
                with open(local_path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.rstrip("\n")
                        if stripped.startswith("email:"):
                            in_email = True
                            continue
                        if in_email and not stripped.startswith((" ", "\t")):
                            in_email = False
                        if in_email and "gmail_app_password" in stripped:
                            _, _, v = stripped.partition(":")
                            v = v.strip()
                            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                                v = v[1:-1]
                            return v
    except OSError:
        pass
    return ""


def _load_t7_config(
    path: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    """Load the T7 yaml config. Returns ``(config_dict, path_used)``.
    If no config is found / parseable, returns ``(None, None)`` and
    the caller should fall back to the SMTP_* env path.

    Auto-discovery order when ``path is None``:
    ``config_real.yaml`` then ``config.yaml`` at the phase3 root.
    """
    candidates = (path,) if path is not None else _DEFAULT_T7_CONFIG_CANDIDATES
    for p in candidates:
        if p is None or not p.exists():
            continue
        try:
            import yaml  # type: ignore
            with open(p, "r", encoding="utf-8") as f:
                conf = yaml.safe_load(f) or {}
            if isinstance(conf, dict):
                return conf, p
        except ImportError:
            return None, None
        except (OSError, Exception):  # noqa: BLE001 — defensive
            continue
    return None, None


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
    use_ssl: bool = False  # smtplib.SMTP_SSL (Gmail 465). Mutually exclusive with use_tls in practice.
    source: str = "env"    # diagnostic: "env" | "t7_config" | "manual"

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        dotenv_path: Optional[Path] = None,
        read_dotenv: Optional[bool] = None,
    ) -> "SmtpConfig":
        """Build SmtpConfig from process env + (optionally) the
        repo-root ``.env``.

        Default behaviour for ``read_dotenv``:

        * ``env is None``  (production: read os.environ)  → reads .env
        * ``env is given`` (test / explicit override)     → ignores .env

        That keeps the production call (``SmtpConfig.from_env()``)
        merging with the same ``.env`` file the KIS adapter already
        uses, while letting unit tests pass an exact env dict and
        be sure no developer-machine ``.env`` leaks in. Pass
        ``read_dotenv=True`` explicitly to force the merge in either
        mode (rarely needed).
        """
        if read_dotenv is None:
            read_dotenv = (env is None)
        if read_dotenv:
            e = _merge_env_with_dotenv(env, dotenv_path)
        else:
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
        use_ssl = _env_bool(e, "SMTP_USE_SSL", False)
        dry_run = _env_bool(e, "SMTP_DRY_RUN", False)
        disabled = _env_bool(e, "SMTP_DISABLED", False)
        return cls(
            host=host, port=port,
            username=username, password=password,
            from_addr=from_addr, to_addrs=to_addrs,
            use_tls=use_tls, dry_run=dry_run, disabled=disabled,
            use_ssl=use_ssl, source="env",
        )

    @classmethod
    def from_t7_config(
        cls,
        config: Mapping[str, Any],
        *,
        env: Optional[Mapping[str, str]] = None,
        config_local_path: Optional[Path] = None,
    ) -> "SmtpConfig":
        """Build SmtpConfig from the T7 ``email:`` block already used
        by ``phase3/mailer.py`` for the daily recommendation mail.

        Schema this honours (subset of T7's block):

            email:
              enabled: true
              gmail_address: bot@gmail.com
              gmail_app_password: ${GMAIL_APP_PASSWORD}     # or literal
              recipient: ops@gmail.com
              # optional R11B-only knobs (graceful defaults if absent)
              dry_run: false
              additional_recipients: [team@example.com]

        Password resolution mirrors ``phase3.mailer._resolve_password``:

        1. literal value if it does not start with ``${``
        2. ``$GMAIL_APP_PASSWORD`` env var
        3. ``phase3/config.local.yaml`` (if present)
        4. empty (``is_complete()`` will then flag missing creds)

        Host/port are hard-wired to Gmail SMTPS (``smtp.gmail.com:465``)
        because that is what the existing T7 mailer uses; we replicate
        the behaviour rather than introduce drift between the two
        mail paths. If you need a different provider, use the
        direct-env path (``SMTP_HOST=`` etc.).
        """
        e = env if env is not None else os.environ
        email_conf = dict(config.get("email") or {})
        enabled = bool(email_conf.get("enabled", False))
        gmail_addr = str(email_conf.get("gmail_address") or "").strip()
        raw_pwd = str(email_conf.get("gmail_app_password") or "")
        recipient = str(email_conf.get("recipient") or gmail_addr).strip()
        extras = email_conf.get("additional_recipients") or []
        to_list: List[str] = []
        if recipient:
            to_list.append(recipient)
        for extra in extras:
            extra = str(extra).strip()
            if extra and extra not in to_list:
                to_list.append(extra)

        password = _resolve_t7_password(
            raw=raw_pwd, env=e, config_local_path=config_local_path)

        dry_run = bool(email_conf.get("dry_run", False))
        # Mirror SMTP_DRY_RUN/SMTP_DISABLED env overrides so the
        # operator can flip those without editing yaml.
        if _env_bool(e, "SMTP_DRY_RUN", False):
            dry_run = True
        disabled_env = _env_bool(e, "SMTP_DISABLED", False)
        # T7 ``enabled=false`` is the canonical disabled state.
        disabled = (not enabled) or disabled_env

        return cls(
            host="smtp.gmail.com",
            port=465,
            username=gmail_addr,
            password=password,
            from_addr=gmail_addr,
            to_addrs=tuple(to_list),
            use_tls=False,
            use_ssl=True,
            dry_run=dry_run,
            disabled=disabled,
            source="t7_config",
        )

    def __repr__(self) -> str:  # pragma: no cover — convenience
        return (
            f"SmtpConfig(host={self.host!r}, port={self.port}, "
            f"username={self.username!r}, "
            f"password=<{len(self.password)} chars hidden>, "
            f"from_addr={self.from_addr!r}, "
            f"to_addrs={self.to_addrs}, use_tls={self.use_tls}, "
            f"use_ssl={self.use_ssl}, "
            f"dry_run={self.dry_run}, disabled={self.disabled}, "
            f"source={self.source!r})"
        )

    def is_complete(self) -> Tuple[bool, str]:
        """Return ``(True, '')`` if the config has the minimum fields
        needed to actually attempt delivery. ``dry_run=True`` relaxes
        the host/credential requirements — handy for offline tests."""
        if self.disabled:
            reason = (
                "email.enabled=false in T7 config"
                if self.source == "t7_config"
                else "SMTP_DISABLED=true"
            )
            return False, reason
        if not self.to_addrs:
            return False, (
                "email.recipient is empty in T7 config"
                if self.source == "t7_config"
                else "SMTP_TO is empty"
            )
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

    if smtp_factory is not None:
        factory = smtp_factory
    elif cfg.use_ssl:
        factory = smtplib.SMTP_SSL
    else:
        factory = smtplib.SMTP
    try:
        with factory(cfg.host, cfg.port) as smtp:
            # SMTP_SSL already negotiates TLS at connect; never call
            # starttls() on an SSL socket (smtplib raises).
            if cfg.use_tls and not cfg.use_ssl:
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
def resolve_smtp_config(
    *,
    env: Optional[Mapping[str, str]] = None,
    t7_config_path: Optional[Path] = None,
    config_local_path: Optional[Path] = None,
    dotenv_path: Optional[Path] = None,
    read_dotenv: Optional[bool] = None,
    prefer: str = "t7",
) -> SmtpConfig:
    """Pick the credential source for R11B mail.

    Resolution order (when ``prefer='t7'``, the default):

    1. T7 ``email:`` block in ``config_real.yaml`` (then ``config.yaml``)
       if it is reachable AND has ``enabled=true`` AND the password
       resolves to a non-empty string. This is the common path —
       operators already configured T7 for the daily recommendation
       mail get R11B for free.
    2. SMTP_* env vars (``.env`` + os.environ) as a fallback for
       non-Gmail setups or when the operator wants to send R11B
       to a different inbox than the T7 recommendations.

    Pass ``prefer='env'`` to invert the order (rarely useful — exists
    so an operator can keep T7 mail going while staging a totally
    separate R11B inbox).
    """
    def _from_t7() -> Optional[SmtpConfig]:
        conf, _ = _load_t7_config(t7_config_path)
        if conf is None:
            return None
        cfg = SmtpConfig.from_t7_config(
            conf, env=env, config_local_path=config_local_path)
        ok, _why = cfg.is_complete()
        if ok or cfg.dry_run:
            return cfg
        return None

    def _from_env() -> SmtpConfig:
        return SmtpConfig.from_env(
            env, dotenv_path=dotenv_path, read_dotenv=read_dotenv)

    if prefer == "env":
        cfg = _from_env()
        ok, _ = cfg.is_complete()
        if ok or cfg.dry_run:
            return cfg
        t7 = _from_t7()
        return t7 if t7 is not None else cfg

    # default: prefer T7
    t7 = _from_t7()
    if t7 is not None:
        return t7
    return _from_env()


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
    dotenv_path: Optional[Path] = None,
    read_dotenv: Optional[bool] = None,
    t7_config_path: Optional[Path] = None,
    config_local_path: Optional[Path] = None,
    prefer: str = "t7",
) -> MailDispatchResult:
    """Convenience wrapper used by the R11A coordinator (and
    the panel's manual "send report" button if we add one): build the
    payload, pick the best credential source, deliver.

    Credential resolution defaults to **T7 first** — see
    :func:`resolve_smtp_config` for details. That means operators who
    already get the T7 daily recommendation mail receive R11B run
    summaries to the same inbox with zero new env vars.

    Always returns a result — never raises. The caller is expected to
    log the result and otherwise ignore failures (the daily run is
    already finished).
    """
    cfg = resolve_smtp_config(
        env=env,
        t7_config_path=t7_config_path,
        config_local_path=config_local_path,
        dotenv_path=dotenv_path,
        read_dotenv=read_dotenv,
        prefer=prefer,
    )
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
