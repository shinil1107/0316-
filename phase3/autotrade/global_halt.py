"""R9-C — global emergency halt flag.

What this is
------------
A single tiny JSON file (``phase3/autotrade/runtime/global_halt.json``)
that any operator-facing UI can write to instantly veto new broker
submissions. Anything that is about to call ``place_order`` MUST go
through :func:`assert_not_halted` first.

Contract
--------
File present and ``{"halt": true}``                  → submissions blocked.
File missing OR ``{"halt": false}`` (or unparseable) → submissions allowed.

Unparseable on purpose returns "not halted" — we don't want a broken
config file to silently freeze trading. Operator pressing STOP must
write a well-formed payload. That's why :func:`write_halt` exists and
is the only sanctioned writer.

Why a file
----------
A file works across processes / IDEs / launchd jobs without needing a
local daemon or message bus. The R8 daily_runner runs in-process from
the control panel today, but we want this flag to keep meaning
something once the runner is moved to its own subprocess / cron job.

This module is deliberately import-light so picking it up from
``order_manager`` and ``daily_runner`` adds no measurable load time.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_HERE = Path(__file__).resolve().parent
RUNTIME_DIR = _HERE / "runtime"
HALT_FILE = RUNTIME_DIR / "global_halt.json"


class GlobalHaltError(RuntimeError):
    """Raised by :func:`assert_not_halted` when the halt flag is set."""


@dataclass(frozen=True)
class HaltState:
    halted: bool
    reason: str = ""
    ts: str = ""
    raw_path: str = ""


def halt_path(override: Optional[Path] = None) -> Path:
    """Resolve the halt file path. The override exists for tests."""
    if override is not None:
        return Path(override)
    env_override = os.environ.get("AUTOTRADE_HALT_FILE", "").strip()
    if env_override:
        return Path(env_override).expanduser()
    return HALT_FILE


def read_halt(path: Optional[Path] = None) -> HaltState:
    """Return current halt state. Never raises on parse error — broken
    files report ``halted=False`` with a reason in ``raw_path``. The
    contract is: only a well-formed ``{"halt": true}`` blocks."""
    p = halt_path(path)
    if not p.exists():
        return HaltState(halted=False, raw_path=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HaltState(halted=False, raw_path=str(p),
                          reason="unparseable_halt_file")
    if not isinstance(data, dict):
        return HaltState(halted=False, raw_path=str(p),
                          reason="halt_file_not_object")
    return HaltState(
        halted=bool(data.get("halt", False)),
        reason=str(data.get("reason", "")),
        ts=str(data.get("ts", "")),
        raw_path=str(p),
    )


def is_halted(path: Optional[Path] = None) -> bool:
    return read_halt(path).halted


def assert_not_halted(*, where: str, path: Optional[Path] = None) -> None:
    """Raise :class:`GlobalHaltError` if the halt flag is set. Callers
    must catch this themselves; we do NOT print or log here so we don't
    leak the halt state into stdout from unrelated callers."""
    state = read_halt(path)
    if state.halted:
        raise GlobalHaltError(
            f"global_halt is set at {state.raw_path}: "
            f"reason={state.reason or '(none)'} "
            f"ts={state.ts or '(none)'} "
            f"(blocked at: {where})"
        )


def write_halt(*, halt: bool, reason: str,
                operator: str = "control_panel",
                path: Optional[Path] = None) -> Path:
    """Persist halt state. Used by the control panel STOP button and by
    tests. The runtime dir is created if missing."""
    p = halt_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "halt": bool(halt),
        "reason": str(reason),
        "operator": str(operator),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                  encoding="utf-8")
    return p


def clear_halt(*, operator: str = "control_panel",
                path: Optional[Path] = None) -> Path:
    """Convenience: write a fresh ``halt=false`` payload so the audit
    trail shows when the halt was lifted."""
    return write_halt(halt=False, reason="cleared_by_operator",
                       operator=operator, path=path)
