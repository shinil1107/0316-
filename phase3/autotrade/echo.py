"""Round 3 (P1.B) — post-submit order echo visibility.

Background
----------
After Steps 4 / Round-2, `inquire-ccnl` failed to echo five out of five
newly-submitted paper ODNOs within a 12-second polling budget. Position
and cash deltas confirmed each fill independently, so it's a visibility
gap, not a submit/fill failure. Codex Round-3 §3 P1 prescribed:

    echo_poll -> inquire-nccs first   (open / pending visibility)
              -> inquire-ccnl fallback (executed / history)
              -> success if either matches the ODNO

This module owns that two-source echo logic so both `paper_buy.py` and
`paper_execute_intent.py` go through one well-audited helper. It is
network-light (one `nccs` call + at most one `ccnl` call per attempt)
and does not mutate any state.

Result shape (per Codex R3 §3 P1.B)
-----------------------------------
```python
{
    "matched": bool,                     # True if either source surfaced ODNO
    "source": "nccs" | "ccnl" | None,    # which endpoint produced the match
    "broker_order_id": str,              # echoed for caller convenience
    "matched_row": Dict[str, Any] | None,# the row exactly as returned by KIS,
                                         # plus an injected "_normalized" dict
                                         # when matched via nccs (so callers
                                         # can read symbol/qty/etc. without
                                         # parsing korean field names).
    "attempts": List[Dict[str, Any]],    # per-attempt diagnostics
}
```

`attempts[i]` looks like:

```python
{
    "i": int,                  # 1-indexed
    "ts": str,                 # ISO-8601 UTC
    "nccs_rows": int,          # rows seen on the nccs call (-1 on call failure)
    "ccnl_rows": int,          # rows seen on the ccnl call (-1 on call failure,
                               #  None if not consulted because nccs matched)
    "matched_source": "nccs" | "ccnl" | None,
}
```
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from phase3.autotrade.kis_broker_adapter import KisBrokerAdapter, OpenOrder
from phase3.autotrade.order_ids import normalize_odno

# R6 alias kept for backward compatibility (tests / external callers).
# R8-A consolidated the canonical implementation in `order_ids.normalize_odno`.
_norm_odno = normalize_odno


# ──────────────────────────────────────────────────────────────────────
# Single-source helpers (one network call each, no retry)
# ──────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _try_nccs(
    adapter: KisBrokerAdapter,
    odno: str,
    *,
    market: str,
) -> Dict[str, Any]:
    """Return {'rows': int_or_-1, 'matched': OpenOrder | None, 'error': str | None}."""
    try:
        rows: List[OpenOrder] = adapter.get_open_orders(market=market)
    except Exception as e:  # noqa: BLE001
        return {"rows": -1, "matched": None, "error": f"{type(e).__name__}: {e}"}
    target = normalize_odno(odno)
    for oo in rows:
        if normalize_odno(oo.broker_order_id) == target:
            return {"rows": len(rows), "matched": oo, "error": None}
    return {"rows": len(rows), "matched": None, "error": None}


def _try_ccnl(adapter: KisBrokerAdapter, odno: str) -> Dict[str, Any]:
    """Return {'rows': int_or_-1, 'matched': dict | None, 'error': str | None}.

    `inquire-ccnl` returns raw KIS rows; we match on `odno`. ODNO is
    normalized via `_norm_odno` because place_order returns a zero-padded
    form while ccnl rows do not (see Round 6 finding above).
    """
    try:
        rows = adapter.get_order_history()
    except Exception as e:  # noqa: BLE001
        return {"rows": -1, "matched": None, "error": f"{type(e).__name__}: {e}"}
    target = normalize_odno(odno)
    for r in rows:
        if normalize_odno(r.get("odno", "")) == target:
            return {"rows": len(rows), "matched": r, "error": None}
    return {"rows": len(rows), "matched": None, "error": None}


# ──────────────────────────────────────────────────────────────────────
# Public: echo_poll
# ──────────────────────────────────────────────────────────────────────
def echo_poll(
    adapter: KisBrokerAdapter,
    broker_order_id: str,
    *,
    market: str = "NASD",
    max_polls: int = 4,
    interval_sec: float = 3.0,
    on_attempt: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Poll `inquire-nccs` then `inquire-ccnl` looking for `broker_order_id`.

    Stops at the first match. Empty list from either endpoint is not an
    error — paper may simply not have indexed the ODNO yet. Each attempt
    is recorded for the JSON dump.

    Args:
        adapter: live `KisBrokerAdapter`.
        broker_order_id: ODNO returned by `place_order`.
        market: passed to `inquire-nccs`; 'NASD' returns US aggregate.
        max_polls: how many full nccs+ccnl rounds before giving up.
        interval_sec: seconds to sleep between rounds.
        on_attempt: optional callback to stream attempt diagnostics to
            stdout. Receives the same dict that ends up in `attempts[i]`.

    Returns:
        dict with keys: matched, source, broker_order_id, matched_row, attempts.
    """
    attempts: List[Dict[str, Any]] = []
    matched_row: Optional[Dict[str, Any]] = None
    source: Optional[str] = None

    for i in range(1, max_polls + 1):
        attempt: Dict[str, Any] = {
            "i": i, "ts": _now(),
            "nccs_rows": None, "nccs_error": None,
            "ccnl_rows": None, "ccnl_error": None,
            "matched_source": None,
        }

        nccs = _try_nccs(adapter, broker_order_id, market=market)
        attempt["nccs_rows"] = nccs["rows"]
        attempt["nccs_error"] = nccs["error"]
        if nccs["matched"] is not None:
            oo: OpenOrder = nccs["matched"]
            matched_row = dict(oo.raw)
            matched_row["_normalized"] = {
                "broker_order_id": oo.broker_order_id,
                "symbol":          oo.symbol,
                "market":          oo.market,
                "side":            oo.side,
                "qty_order":       oo.qty_order,
                "qty_filled":      oo.qty_filled,
                "qty_remaining":   oo.qty_remaining,
                "limit_price":     oo.limit_price,
                "status_text":     oo.status_text,
                "ord_dt":          oo.ord_dt,
                "ord_tmd":         oo.ord_tmd,
            }
            source = "nccs"
            attempt["matched_source"] = "nccs"
            attempts.append(attempt)
            if on_attempt:
                on_attempt(attempt)
            break

        # nccs didn't see it → fall back to ccnl.
        ccnl = _try_ccnl(adapter, broker_order_id)
        attempt["ccnl_rows"] = ccnl["rows"]
        attempt["ccnl_error"] = ccnl["error"]
        if ccnl["matched"] is not None:
            matched_row = ccnl["matched"]
            source = "ccnl"
            attempt["matched_source"] = "ccnl"
            attempts.append(attempt)
            if on_attempt:
                on_attempt(attempt)
            break

        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if i < max_polls:
            time.sleep(interval_sec)

    return {
        "matched":         matched_row is not None,
        "source":          source,
        "broker_order_id": str(broker_order_id),
        "matched_row":     matched_row,
        "attempts":        attempts,
    }


def attempt_stdout_line(attempt: Dict[str, Any], *, max_polls: int) -> str:
    """Format a single attempt for verbose stdout. Caller passes this
    into `on_attempt` to render progress lines without leaking module
    print() calls into the caller's UX."""
    i = attempt["i"]
    nccs_r = attempt["nccs_rows"]
    ccnl_r = attempt["ccnl_rows"]
    nccs_part = (f"nccs=ERR({attempt['nccs_error']})" if nccs_r == -1
                 else f"nccs={nccs_r if nccs_r is not None else '-'}rows")
    if attempt["matched_source"] == "nccs":
        nccs_part += " MATCH"
    if ccnl_r is None:
        ccnl_part = "ccnl=skip"
    elif ccnl_r == -1:
        ccnl_part = f"ccnl=ERR({attempt['ccnl_error']})"
    else:
        ccnl_part = f"ccnl={ccnl_r}rows"
        if attempt["matched_source"] == "ccnl":
            ccnl_part += " MATCH"
    return f"  [echo {i}/{max_polls}] {nccs_part}   {ccnl_part}"
