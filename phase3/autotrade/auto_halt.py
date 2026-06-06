"""V2-B (G3) — automated lockout ("the system presses STOP for you").

What a "lockout" is
-------------------
The continuous (standing-arm) trader fires every trading day with no human
in the loop. ``auto_halt`` is its self-protection: after each trade fire it
records the outcome and evaluates a few abnormal-condition rules. If any
trips, it writes ``global_halt`` (the exact same flag the panel STOP button
sets). From then on EVERY subsequent fire sees the halt and cleanly skips
(rc=0, no trading) until the **operator** investigates and clears the halt
in the panel. So a lockout = an automatic, *latching* STOP that the operator
must release by hand. It never corrupts the failing run — it only protects
the *next* one, so the failure mode is safe.

Triggers (operator-confirmed thresholds)
-----------------------------------------
1. Consecutive trade-fire failures ``>= N`` (default 3). A "trade fire" is a
   fire that passed the calendar/halt/arm gates and actually attempted to
   trade; calendar/halt/arm *skips* are not failures and don't count.
2. Cumulative portfolio drawdown ``>= P%`` (default 20%) measured from the
   account's starting equity (the earliest recorded equity, which a paper
   reset truncates — so it re-anchors to the new seed).
3. Data staleness: the scoring close is more than ``stale_max_extra`` trading
   days behind the session (default 1) — i.e. the OHLCV cache failed to
   refresh and we'd be trading on stale signals.

This module is pure + import-light (only stdlib + ``trading_calendar`` +
``global_halt``); ``evaluate_lockout`` is a side-effect-free function so it
is trivially unit-testable, and ``apply_lockout`` is the only writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from phase3.autotrade import global_halt
from phase3.autotrade import trading_calendar as tc


_HERE = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_DIR = _HERE / "runtime"
HISTORY_FILENAME = "v1_fire_history.jsonl"

# Operator-confirmed defaults (2026-06-02).
DEFAULT_CONSECUTIVE_FAIL_THRESHOLD = 3
DEFAULT_DRAWDOWN_PCT = 20.0
DEFAULT_STALE_MAX_EXTRA_TRADING_DAYS = 1


# ──────────────────────────────────────────────────────────────────────
# Fire history (append-only JSONL)
# ──────────────────────────────────────────────────────────────────────
def history_path(*, runtime_dir: Optional[Path] = None) -> Path:
    base = Path(runtime_dir) if runtime_dir else _DEFAULT_RUNTIME_DIR
    return base / HISTORY_FILENAME


def record_fire(
    *,
    run_id: str,
    session_date_kst: str,
    kind: str,
    rc: int,
    total_after: Optional[float] = None,
    scoring_date: Optional[str] = None,
    note: str = "",
    runtime_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Append one fire-outcome record. ``kind`` is "trade" (a real trade
    attempt) or "skip" (gate skip). Best-effort: never raises so a logging
    hiccup can't break the pipeline."""
    n = now if now is not None else datetime.now(tz=timezone.utc)
    p = history_path(runtime_dir=runtime_dir)
    rec = {
        "ts": n.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "session_date_kst": session_date_kst,
        "kind": kind,
        "rc": int(rc),
        "total_after": total_after,
        "scoring_date": scoring_date,
        "note": note,
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return p


def read_history(
    *, runtime_dir: Optional[Path] = None, tail: Optional[int] = None,
) -> List[dict]:
    """Return fire records oldest→newest. Malformed lines are skipped."""
    p = history_path(runtime_dir=runtime_dir)
    if not p.exists():
        return []
    out: List[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    if tail is not None and tail >= 0:
        out = out[-tail:]
    return out


# ──────────────────────────────────────────────────────────────────────
# Evaluation (pure)
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LockoutDecision:
    tripped: bool
    reasons: List[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def message(self) -> str:
        return "; ".join(self.reasons)


def _consecutive_trade_failures(history: List[dict]) -> int:
    """Count the trailing run of consecutive failed TRADE fires (rc != 0),
    ignoring skips. Stops at the first successful trade fire."""
    n = 0
    for rec in reversed(history):
        if rec.get("kind") != "trade":
            continue
        if int(rec.get("rc", 0)) != 0:
            n += 1
        else:
            break
    return n


def _latest_equity(history: List[dict]) -> Optional[float]:
    for rec in reversed(history):
        v = rec.get("total_after")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _baseline_equity(history: List[dict]) -> Optional[float]:
    """Starting equity = earliest recorded equity. A paper reset truncates
    history, so this re-anchors to the new seed automatically."""
    for rec in history:
        v = rec.get("total_after")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _latest_scoring_date(history: List[dict]) -> Optional[str]:
    for rec in reversed(history):
        s = rec.get("scoring_date")
        if isinstance(s, str) and s:
            return s
    return None


def evaluate_lockout(
    history: List[dict],
    *,
    session_date: date,
    consecutive_fail_threshold: int = DEFAULT_CONSECUTIVE_FAIL_THRESHOLD,
    drawdown_pct: float = DEFAULT_DRAWDOWN_PCT,
    baseline_equity: Optional[float] = None,
    stale_max_extra_trading_days: int = DEFAULT_STALE_MAX_EXTRA_TRADING_DAYS,
) -> LockoutDecision:
    """Decide whether the abnormal-condition rules trip a lockout.

    Pure function — does not touch disk. ``baseline_equity`` defaults to the
    earliest equity in ``history`` when not supplied."""
    reasons: List[str] = []
    detail: dict = {}

    # 1) consecutive trade-fire failures
    fails = _consecutive_trade_failures(history)
    detail["consecutive_trade_failures"] = fails
    if consecutive_fail_threshold > 0 and fails >= consecutive_fail_threshold:
        reasons.append(
            f"{fails} consecutive trade-fire failures "
            f"(>= {consecutive_fail_threshold})")

    # 2) cumulative drawdown from starting equity
    base = baseline_equity if baseline_equity is not None \
        else _baseline_equity(history)
    cur = _latest_equity(history)
    detail["baseline_equity"] = base
    detail["latest_equity"] = cur
    if base is not None and base > 0 and cur is not None \
            and drawdown_pct > 0:
        dd = (cur - base) / base * 100.0
        detail["drawdown_pct"] = round(dd, 2)
        if dd <= -abs(drawdown_pct):
            reasons.append(
                f"portfolio down {dd:.1f}% from start "
                f"(<= -{abs(drawdown_pct):.0f}%)")

    # 3) data staleness
    sd = _latest_scoring_date(history)
    if sd:
        try:
            sd_date = date.fromisoformat(sd)
            lag = tc.trading_days_between(sd_date, session_date)
            detail["scoring_date"] = sd
            detail["stale_trading_days"] = lag
            if lag > stale_max_extra_trading_days:
                reasons.append(
                    f"scoring close {sd} is {lag} trading days behind "
                    f"session {session_date.isoformat()} "
                    f"(> {stale_max_extra_trading_days})")
        except ValueError:
            pass

    return LockoutDecision(tripped=bool(reasons), reasons=reasons,
                           detail=detail)


# ──────────────────────────────────────────────────────────────────────
# Apply (the only writer of global_halt for auto-lockout)
# ──────────────────────────────────────────────────────────────────────
def apply_lockout(
    decision: LockoutDecision,
    *,
    halt_path: Optional[Path] = None,
) -> Optional[Path]:
    """If ``decision`` tripped AND we're not already halted, latch
    ``global_halt`` and return its path. Otherwise return None.

    Idempotent: a second tripped evaluation while already halted is a
    no-op, so we don't keep overwriting the original lockout reason."""
    if not decision.tripped:
        return None
    if global_halt.is_halted(halt_path):
        return None
    return global_halt.write_halt(
        halt=True,
        reason="AUTO_LOCKOUT: " + decision.message,
        operator="auto_halt",
        path=halt_path,
    )
