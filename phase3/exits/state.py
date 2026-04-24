"""Per-holding snapshot construction + SimPortfolio field extension helpers.

Two responsibilities:

1. ``extend_holding_fields(holdings, price_map, date, score_map, ...)`` —
   keep the SimPortfolio / HoldingsManager per-ticker dict in sync with
   the fields triggers depend on (``entry_date``, ``entry_score``,
   ``entry_rank``, ``peak_price``, ``entry_regime``).  Called once per
   simulation step (or per live run) — backfills missing fields with
   sensible defaults so legacy holdings files remain readable.

2. ``build_holding_snapshots(current_df, holdings_store, score_map,
   rank_map, today, prev_grace)`` — produce one ``HoldingSnapshot`` per
   held ticker.  Pulls from the ``current`` DataFrame (public interface
   of HoldingsManager.load_current) plus the underlying ``holdings``
   dict when available for entry-attribution fields.

Both helpers are simulator/live parity safe: they read whatever is
present and fill defaults for anything missing.  No KeyError from old
data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from .base import HoldingSnapshot


# ─────────────────────────────────────────────────────────────────────────────
# Field extension
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_DEFAULTS = {
    "entry_date": "",
    "entry_price": 0.0,
    "entry_score": 0.0,
    "entry_rank": -1,
    "entry_regime": "",
    "peak_price": 0.0,
    "last_score": 0.0,
    # D4.3 — profit-target stateful fields.
    # ``profit_targets_hit`` is a set of tier_pct (float) values already
    # executed for this ticker; triggers skip tiers present in this set.
    # Serialised as a sorted list when persisted (HoldingsManager JSON).
    "profit_targets_hit": None,
}


def extend_holding_fields(
    holdings: Dict[str, dict],
    price_map: Mapping[str, float],
    date: str,
    score_map: Optional[Mapping[str, float]] = None,
    rank_map: Optional[Mapping[str, int]] = None,
    regime: str = "",
) -> None:
    """Mutate ``holdings`` in place: add/maintain snapshot-support fields.

    Rules:
      * Missing fields are back-filled with defaults (legacy holdings rows).
      * ``peak_price`` is monotonically increased by today's current_price.
      * ``entry_*`` fields are set the first time a ticker appears with
        ``entry_date == ""`` (i.e. initial BUY_NEW path didn't write them).
      * ``last_score`` is updated every call (used by velocity triggers).

    This function is idempotent and safe to call every simulation day.
    """
    score_map = score_map or {}
    rank_map = rank_map or {}

    for ticker, h in holdings.items():
        # Back-fill defaults.
        for key, default in _REQUIRED_DEFAULTS.items():
            if key not in h:
                # ``profit_targets_hit`` needs a fresh set per ticker
                # (can't share a single default mutable).
                h[key] = set() if key == "profit_targets_hit" else default
        # Legacy rows may have stored profit_targets_hit as a list (JSON).
        if isinstance(h.get("profit_targets_hit"), (list, tuple)):
            h["profit_targets_hit"] = set(float(x) for x in h["profit_targets_hit"])
        elif h.get("profit_targets_hit") is None:
            h["profit_targets_hit"] = set()

        cur_price = float(price_map.get(ticker, h.get("current_price", h.get("avg_cost", 0.0))))

        # Peak price update.
        prev_peak = float(h.get("peak_price", 0.0) or 0.0)
        if cur_price > prev_peak:
            h["peak_price"] = cur_price
        elif prev_peak <= 0.0:
            # First observation — seed with current (or avg_cost if no price yet).
            h["peak_price"] = max(cur_price, float(h.get("avg_cost", 0.0)))

        # Entry attribution on first see.
        if not h.get("entry_date"):
            h["entry_date"] = date
            h["entry_price"] = float(h.get("avg_cost", cur_price) or cur_price)
            h["entry_score"] = float(score_map.get(ticker, h.get("entry_score", 0.0)))
            h["entry_rank"] = int(rank_map.get(ticker, h.get("entry_rank", -1)))
            h["entry_regime"] = regime or h.get("entry_regime", "")

        # Always-fresh score snapshot.
        if ticker in score_map:
            h["last_score"] = float(score_map[ticker])


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot builder
# ─────────────────────────────────────────────────────────────────────────────

def _days_between(a: str, b: str) -> int:
    """Naive day diff ``b - a``, 0 on parse failure."""
    try:
        da = datetime.strptime(a[:10], "%Y-%m-%d")
        db = datetime.strptime(b[:10], "%Y-%m-%d")
        return max(0, (db - da).days)
    except Exception:
        return 0


def build_holding_snapshots(
    current: pd.DataFrame,
    holdings_store: Optional[Dict[str, dict]],
    score_map: Mapping[str, float],
    rank_map: Mapping[str, int],
    today: str,
    prev_grace: Optional[Mapping[str, int]] = None,
    entry_regime_map: Optional[Mapping[str, str]] = None,
) -> List[HoldingSnapshot]:
    """Build one ``HoldingSnapshot`` per held ticker.

    Parameters
    ----------
    current : pd.DataFrame
        Output of ``HoldingsManager.load_current()`` — must have Ticker,
        Shares, BuyPrice, CurrentPrice, PnL_Pct columns.  Other columns
        ignored.
    holdings_store : dict or None
        Underlying ``holdings`` dict (SimPortfolio.holdings or equivalent).
        Used for entry_* / peak_price fields.  If None, those fields fall
        back to CSV-derived values and -1 / 0.0 defaults.
    score_map, rank_map : Mapping
        Today's score and 1-based rank by ticker.  Missing → 0.0 / -1.
    today : str
        YYYY-MM-DD evaluation date.
    prev_grace : Mapping[str, int] or None
        Ticker → grace_count from previous run (already computed by
        generate_recommendations from prev_recos).
    entry_regime_map : Mapping[str, str] or None
        Optional override when holdings_store lacks entry_regime.
    """
    prev_grace = prev_grace or {}
    entry_regime_map = entry_regime_map or {}
    out: List[HoldingSnapshot] = []

    if current is None or current.empty:
        return out

    for _, row in current.iterrows():
        ticker = str(row["Ticker"])
        h = holdings_store.get(ticker, {}) if holdings_store else {}

        entry_date = h.get("entry_date") or ""
        entry_price = float(h.get("entry_price", row.get("BuyPrice", 0.0)) or row.get("BuyPrice", 0.0))
        entry_score = float(h.get("entry_score", 0.0) or 0.0)
        entry_rank = int(h.get("entry_rank", -1) or -1)
        entry_regime = h.get("entry_regime", "") or entry_regime_map.get(ticker, "")
        peak_price = float(h.get("peak_price", 0.0) or 0.0)

        current_price = float(row.get("CurrentPrice", row.get("BuyPrice", 0.0)) or 0.0)
        if peak_price <= 0.0:
            peak_price = current_price

        pnl_pct = float(row.get("PnL_Pct", 0.0) or 0.0)
        peak_dd_pct = (current_price / peak_price - 1.0) * 100.0 if peak_price > 0 else 0.0

        days_held = _days_between(entry_date, today) if entry_date else 0

        # D4.3 — load profit-target memory; accept set / list / None.
        _pt = h.get("profit_targets_hit")
        if isinstance(_pt, (set, frozenset)):
            pt_hit = frozenset(float(x) for x in _pt)
        elif isinstance(_pt, (list, tuple)):
            pt_hit = frozenset(float(x) for x in _pt)
        else:
            pt_hit = frozenset()

        out.append(HoldingSnapshot(
            ticker=ticker,
            shares=int(row.get("Shares", 0) or 0),
            entry_date=entry_date,
            entry_price=entry_price,
            entry_score=entry_score,
            entry_rank=entry_rank,
            entry_regime=entry_regime,
            current_price=current_price,
            current_score=float(score_map.get(ticker, 0.0) or 0.0),
            current_rank=int(rank_map.get(ticker, -1)),
            peak_price=peak_price,
            pnl_pct=pnl_pct,
            peak_drawdown_pct=peak_dd_pct,
            days_held=days_held,
            grace_count=int(prev_grace.get(ticker, 0) or 0),
            profit_targets_hit=pt_hit,
        ))

    return out
