"""V2-A (G2) — NYSE trading-day calendar.

Why
---
The V1 fires (07:20 T7 prefetch + 22:35 trade) are scheduled by launchd
on *every* calendar day. V1 relied on the daily arm token to gate them;
V2 replaces that with a *standing* arm, so the fires would otherwise run
on weekends and US market holidays too — generating recommendations off a
stale cache and submitting orders into a closed market. This module is
the calendar gate that lets both fires cleanly skip a non-trading day.

Date convention
---------------
The 22:35 KST trade fire opens the US session that is dated the SAME
calendar day in ET (22:30 KST == 09:30 ET, same date). The 07:20 KST
prefetch preps that same session. So both fires gate on **today's KST
date interpreted as the ET session date** — see ``v1_arm.today_kst``.

Scope
-----
* Regular full-day closures + weekends. Early-close ("half") days are
  intentionally treated as NORMAL trading days: the market still OPENS at
  the regular time, and we only trade in the first minutes after open, so
  an early *close* never touches our fill window.
* No reliance on an external package — holidays are computed from rules
  (incl. Good Friday via the Computus algorithm) so the calendar is
  correct for any year without a hard-coded table going stale.

Import-light on purpose (no pandas / no network) so the launchd gate adds
no measurable load.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Set


# ──────────────────────────────────────────────────────────────────────
# Holiday computation
# ──────────────────────────────────────────────────────────────────────
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0..Sun=6) of ``month`` in ``year``.

    e.g. ``_nth_weekday(2026, 1, 0, 3)`` = 3rd Monday of January 2026."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The LAST ``weekday`` of ``month`` in ``year`` (e.g. last Monday)."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian / Computus algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date, *, new_year: bool = False) -> date:
    """Apply NYSE weekend-observance shift.

    Saturday holiday -> observed the preceding Friday.
    Sunday holiday   -> observed the following Monday.
    Exception: a New Year's Day that falls on Saturday is NOT observed
    (NYSE gives no Friday holiday for it), so ``new_year=True`` suppresses
    the Saturday->Friday shift.
    """
    if d.weekday() == 5:  # Saturday
        return d if new_year else d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


_holiday_cache: dict[int, Set[date]] = {}


def nyse_holidays(year: int) -> Set[date]:
    """Return the set of full-day NYSE closures observed in ``year``.

    Juneteenth is included from 2022 onward (first observed 2022)."""
    cached = _holiday_cache.get(year)
    if cached is not None:
        return cached

    hols: Set[date] = set()

    # New Year's Day (Sat -> not observed; Sun -> Mon).
    ny = _observed(date(year, 1, 1), new_year=True)
    if ny.weekday() < 5:
        hols.add(ny)

    # MLK Jr. Day — 3rd Monday of January.
    hols.add(_nth_weekday(year, 1, 0, 3))
    # Washington's Birthday / Presidents' Day — 3rd Monday of February.
    hols.add(_nth_weekday(year, 2, 0, 3))
    # Good Friday — Friday before Easter Sunday.
    hols.add(_easter_sunday(year) - timedelta(days=2))
    # Memorial Day — last Monday of May.
    hols.add(_last_weekday(year, 5, 0))
    # Juneteenth — Jun 19 (observed), federal market holiday since 2022.
    if year >= 2022:
        je = _observed(date(year, 6, 19))
        if je.weekday() < 5:
            hols.add(je)
    # Independence Day — Jul 4 (observed).
    ind = _observed(date(year, 7, 4))
    if ind.weekday() < 5:
        hols.add(ind)
    # Labor Day — 1st Monday of September.
    hols.add(_nth_weekday(year, 9, 0, 1))
    # Thanksgiving — 4th Thursday of November.
    hols.add(_nth_weekday(year, 11, 3, 4))
    # Christmas — Dec 25 (observed).
    xmas = _observed(date(year, 12, 25))
    if xmas.weekday() < 5:
        hols.add(xmas)

    _holiday_cache[year] = hols
    return hols


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────
def is_trading_day(d: date) -> bool:
    """True iff ``d`` (an ET session date) is a regular NYSE trading day:
    a weekday that is not a full-day closure."""
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d not in nyse_holidays(d.year)


def previous_trading_day(d: date) -> date:
    """The most recent trading day strictly BEFORE ``d``."""
    cur = d - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def next_trading_day(d: date) -> date:
    """The next trading day strictly AFTER ``d``."""
    cur = d + timedelta(days=1)
    while not is_trading_day(cur):
        cur += timedelta(days=1)
    return cur


def trading_days_between(start: date, end: date) -> int:
    """Count trading days ``d`` with ``start < d < end`` (both exclusive).

    Used by the staleness check: for a healthy run the scoring close is
    the immediately-prior trading day, so this returns 0; each fully
    skipped trading day between the cache date and the session adds 1."""
    if end <= start:
        return 0
    n = 0
    cur = start + timedelta(days=1)
    while cur < end:
        if is_trading_day(cur):
            n += 1
        cur += timedelta(days=1)
    return n
