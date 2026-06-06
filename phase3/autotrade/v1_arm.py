"""V1-E.1 — Daily "arm token" for the launchd-driven V1 runner.

Why a token at all
------------------

V1-A → V1-D let the operator drive the full pipeline from a single
panel click. V1-E adds an *unattended* path: a ``launchd`` agent
fires the V1 CLI every weekday at 22:25 KST, ~5 minutes before US
market open. That convenience creates a new risk vector — every
calendar day, a Mac that happens to be on with the right env vars
exported would submit live paper orders, including:

  * days the operator is on vacation
  * days the operator decided overnight to skip (e.g. CPI release,
    FOMC, a known data outage in the FMP cache)
  * days a stale config / mis-set gate would otherwise go uncaught

The arm token is the daily "the operator looked at this run and
explicitly green-lit it" affirmation. To enable a given trading
day the operator runs::

    python -m phase3.autotrade.v1_runner arm-today

which writes ``runtime/v1_armed_<YYYY-MM-DD>.json``. The launchd
job at 22:25 calls ``require_armed_for_today()`` BEFORE doing
anything observable; missing or stale token → exit cleanly with
``rc=0`` and a log line. This means:

  * forgetting to arm = no-op (safe default)
  * arming = "yes, run tonight's V1"
  * one token = one day; can't carry over

Token semantics
---------------

* Filename is ``v1_armed_<YYYY-MM-DD>.json`` under
  ``phase3/autotrade/runtime/`` — same dir global_halt.json lives in.
* ``<YYYY-MM-DD>`` is computed in **KST** because that is the
  timezone the operator thinks of "tonight's trading session" in
  (US market open is 22:30 KST). A naive ``date.today()`` on the
  Mac running launchd would otherwise drift if the user travels.
* Token body records ``armed_at`` (RFC3339), ``armed_by`` (env user),
  ``hostname``, and an optional ``note`` for the operator's audit
  trail. None of these are required for the gate — the gate is
  purely "does the file exist for today's KST date?".
* No TTL beyond the date; the file naturally goes stale at midnight
  KST because the next-day arm-check looks for tomorrow's filename.

Hard requirement: this module MUST NOT import Tk or anything else
that would block headless invocation. Anything that would touch
the network or the heavy engine belongs in v1_runner.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_HERE = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_DIR = _HERE / "runtime"

# KST is UTC+9 with no DST. Hard-coding the offset avoids pulling
# ``zoneinfo`` (which is in the stdlib since 3.9 but still requires
# the tz database to be present) and the result is identical for
# the next several decades.
KST = timezone(timedelta(hours=9))

# Filename rule. Locked here so the launchd plist + the CLI + any
# future audit tooling all agree on the on-disk shape.
TOKEN_PREFIX = "v1_armed_"
TOKEN_SUFFIX = ".json"
_FILENAME_RE = re.compile(
    r"^v1_armed_(\d{4}-\d{2}-\d{2})\.json$"
)

# V2-A (G1) — standing ("armed until I stop") token. Unlike the daily
# token this has NO date in the name: its mere presence arms EVERY
# subsequent trading-day fire until the operator removes it. This is what
# turns the system into a hands-off continuous trader. The daily-token
# path is kept for backwards-compatibility and for an operator who wants
# to arm exactly one session.
STANDING_FILENAME = "v1_standing_arm.json"


# ──────────────────────────────────────────────────────────────────────
# Date helpers
# ──────────────────────────────────────────────────────────────────────
def today_kst(*, now: Optional[datetime] = None) -> str:
    """Return today's KST date as ``YYYY-MM-DD``.

    ``now`` is injectable for tests; production callers leave it None."""
    n = now if now is not None else datetime.now(tz=timezone.utc)
    return n.astimezone(KST).strftime("%Y-%m-%d")


def today_kst_date(*, now: Optional[datetime] = None):
    """Today's KST date as a ``datetime.date``.

    The KST calendar date equals the ET *session* date for our evening
    fires (22:30 KST == 09:30 ET, same day), so this is what the trading
    -calendar gate keys on. ``now`` is injectable for tests."""
    n = now if now is not None else datetime.now(tz=timezone.utc)
    return n.astimezone(KST).date()


def token_path(*, date_kst: str,
               runtime_dir: Optional[Path] = None) -> Path:
    if not _FILENAME_RE.match(f"{TOKEN_PREFIX}{date_kst}{TOKEN_SUFFIX}"):
        raise ValueError(
            f"date_kst must be YYYY-MM-DD, got {date_kst!r}")
    base = Path(runtime_dir) if runtime_dir else _DEFAULT_RUNTIME_DIR
    return base / f"{TOKEN_PREFIX}{date_kst}{TOKEN_SUFFIX}"


# ──────────────────────────────────────────────────────────────────────
# Token I/O
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ArmToken:
    date_kst: str
    armed_at: str        # RFC3339 UTC
    armed_by: str
    hostname: str
    note: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "schema_version": "v1_arm/v1",
            "date_kst": self.date_kst,
            "armed_at": self.armed_at,
            "armed_by": self.armed_by,
            "hostname": self.hostname,
            "note": self.note,
        }, indent=2, sort_keys=True)


def write_arm_token(
    *,
    date_kst: Optional[str] = None,
    runtime_dir: Optional[Path] = None,
    note: str = "",
    now: Optional[datetime] = None,
    armed_by: Optional[str] = None,
    hostname: Optional[str] = None,
    overwrite: bool = True,
) -> ArmToken:
    """Persist today's arm token. Default ``date_kst`` is today's KST
    date; pass an explicit value to arm a future date (e.g. for a
    known T+1 schedule).

    Atomic write via ``.tmp`` + ``Path.replace`` so a crashed Mac in
    the middle of an arm command cannot leave a half-written file
    that the launchd gate would mistake for armed.
    """
    n = now if now is not None else datetime.now(tz=timezone.utc)
    d = date_kst or today_kst(now=n)
    p = token_path(date_kst=d, runtime_dir=runtime_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise FileExistsError(p)
    tok = ArmToken(
        date_kst=d,
        armed_at=n.astimezone(timezone.utc)
                  .isoformat(timespec="seconds"),
        armed_by=armed_by if armed_by is not None
                  else (os.environ.get("USER")
                        or os.environ.get("LOGNAME")
                        or getpass.getuser()),
        hostname=hostname if hostname is not None
                  else platform.node(),
        note=note,
    )
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(tok.to_json(), encoding="utf-8")
    tmp.replace(p)
    return tok


def read_arm_token(
    *,
    date_kst: str,
    runtime_dir: Optional[Path] = None,
) -> Optional[ArmToken]:
    """Return the token for ``date_kst`` if present + well-formed.

    A malformed file is treated as "not armed" rather than raising
    so the launchd gate fails CLOSED (skip the run) on partial /
    corrupted files rather than blowing up the daemon process."""
    p = token_path(date_kst=date_kst, runtime_dir=runtime_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ArmToken(
            date_kst=str(data["date_kst"]),
            armed_at=str(data["armed_at"]),
            armed_by=str(data.get("armed_by", "")),
            hostname=str(data.get("hostname", "")),
            note=str(data.get("note", "")),
        )
    except (KeyError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────────
# Public gate API
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ArmCheckResult:
    """Outcome of ``require_armed_for_today``.

    ``ok=True``  → safe to proceed with the V1 run
    ``ok=False`` → caller MUST log ``reason`` and exit rc=0
                    (skipping is the safe default for unattended runs;
                    rc!=0 would make launchd retry)
    """
    ok: bool
    date_kst: str
    token_path: Path
    token: Optional[ArmToken]
    reason: str = ""
    # V2-A — which arm satisfied the gate: "daily" (dated token),
    # "standing" (continuous token), or "" when not armed.
    mode: str = ""


# ──────────────────────────────────────────────────────────────────────
# V2-A — standing ("armed until stopped") token
# ──────────────────────────────────────────────────────────────────────
def standing_token_path(*, runtime_dir: Optional[Path] = None) -> Path:
    base = Path(runtime_dir) if runtime_dir else _DEFAULT_RUNTIME_DIR
    return base / STANDING_FILENAME


def write_standing_arm(
    *,
    runtime_dir: Optional[Path] = None,
    note: str = "",
    now: Optional[datetime] = None,
    armed_by: Optional[str] = None,
    hostname: Optional[str] = None,
) -> Path:
    """Arm the continuous (V2) trader. Idempotent — re-arming just
    refreshes the metadata. Atomic write (.tmp + replace)."""
    n = now if now is not None else datetime.now(tz=timezone.utc)
    p = standing_token_path(runtime_dir=runtime_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "v1_arm/standing-v1",
        "armed_at": n.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "armed_by": (armed_by if armed_by is not None
                     else (os.environ.get("USER")
                           or os.environ.get("LOGNAME")
                           or getpass.getuser())),
        "hostname": hostname if hostname is not None else platform.node(),
        "note": note,
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def read_standing_arm(
    *, runtime_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Return the standing-arm payload if present + well-formed, else
    None. Malformed → None (gate fails closed, same as the daily token)."""
    p = standing_token_path(runtime_dir=runtime_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def clear_standing_arm(*, runtime_dir: Optional[Path] = None) -> bool:
    """Remove the standing-arm token (stop the continuous trader). Returns
    True if a token was present and removed, False if none existed."""
    p = standing_token_path(runtime_dir=runtime_dir)
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


def require_armed_for_today(
    *,
    runtime_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> ArmCheckResult:
    """Return a structured arm outcome for today's KST session.

    V2-A precedence:
      1. Standing arm present  → armed (mode="standing").
      2. Today's daily token   → armed (mode="daily", backwards-compat).
      3. Neither               → not armed (skip).

    NOTE: this function does NOT consider ``global_halt`` or the trading
    calendar — those are separate, higher-precedence gates evaluated by
    the pipeline BEFORE this one (see ``v1_runner.run_v1_pipeline``).
    """
    d = today_kst(now=now)
    # 1) standing arm — continuous mode.
    standing = read_standing_arm(runtime_dir=runtime_dir)
    if standing is not None:
        return ArmCheckResult(
            ok=True, date_kst=d,
            token_path=standing_token_path(runtime_dir=runtime_dir),
            token=None, mode="standing",
            reason=(f"standing arm active "
                    f"(armed_by={standing.get('armed_by', '?')})"),
        )
    # 2) daily token — single-session backwards-compatible mode.
    p = token_path(date_kst=d, runtime_dir=runtime_dir)
    tok = read_arm_token(date_kst=d, runtime_dir=runtime_dir)
    if tok is None:
        if not p.exists():
            why = (f"no arm token for {d} at {p} — run "
                   f"'python -m phase3.autotrade.v1_runner arm-today' "
                   f"(single day) or 'arm-standing' (continuous) "
                   f"before launchd fires tonight")
        else:
            why = (f"arm token for {d} at {p} is malformed; treating "
                   f"as not armed")
        return ArmCheckResult(
            ok=False, date_kst=d, token_path=p,
            token=None, reason=why,
        )
    return ArmCheckResult(ok=True, date_kst=d, token_path=p, token=tok,
                          mode="daily")


def list_token_files(
    *,
    runtime_dir: Optional[Path] = None,
) -> list[Path]:
    """List existing arm-token files (newest first by filename, which
    is also lexically newest by date). Useful for ``v1_runner status``
    diagnostics + GC of stale tokens."""
    base = Path(runtime_dir) if runtime_dir else _DEFAULT_RUNTIME_DIR
    if not base.exists():
        return []
    out: list[Path] = []
    for p in base.iterdir():
        if _FILENAME_RE.match(p.name):
            out.append(p)
    out.sort(key=lambda x: x.name, reverse=True)
    return out


def gc_old_tokens(
    *,
    keep_days: int = 30,
    runtime_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> int:
    """Delete tokens older than ``keep_days`` to bound disk growth.

    Returns the number of files removed. We never delete TODAY's
    token even if ``keep_days=0`` so a launchd race that GC's before
    the gate-check cannot accidentally cancel tonight's run.
    """
    cutoff = today_kst(now=now)
    base = Path(runtime_dir) if runtime_dir else _DEFAULT_RUNTIME_DIR
    if not base.exists():
        return 0
    today_d = datetime.strptime(cutoff, "%Y-%m-%d").date()
    removed = 0
    for p in base.iterdir():
        m = _FILENAME_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d == today_d:
            continue
        age = (today_d - d).days
        if age > keep_days:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
