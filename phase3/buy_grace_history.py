"""buy_grace_history — JSONL persistence for live buy_grace_days filter.

Mirrors the in-memory ``_top_n_history`` deque used by
``phase3/simulator.py`` so the live ``daily_runner.run_daily`` path can
apply the *same* buy-grace policy that backtests assume.  Without this
module the live path silently runs with grace=0 even when
``config.yaml`` declares ``buy_grace_days: 3``, because the simulator's
deque is in-process state and isn't persisted across daily invocations.

Schema (one JSON object per line)
---------------------------------
    {
        "date":            "2026-04-25",
        "regime":          "BULL"|"SIDE"|"DEFENSIVE"|"CRASH",
        "topn_size":       20,
        "prefilter_topn":  ["AAPL","MSFT", ...],   # top-N before grace
        "ts":              "2026-04-25T20:31:14+09:00",
        "schema":          1,
    }

Only the *prefilter* top-N is recorded — i.e. the natural ranking
**before** the grace filter is applied — so the next day's filter
checks against today's signal-driven set rather than against a self-
filtered universe.  This matches simulator.py's append-after-filter
behaviour at the line marked ``_top_n_history.append(_prefilter_topn)``.

Re-runs of the same date (force=True) overwrite the trailing entry
(no duplicates) so re-execution doesn't poison the lookback.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

SCHEMA_VERSION = 1


class BuyGraceHistory:
    """Lightweight JSONL append-only store for top-N snapshots."""

    def __init__(self, path: str) -> None:
        self.path = path

    # ── Read ────────────────────────────────────────────────────────
    def load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        rows: List[Dict[str, Any]] = []
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def get_recent_topn_sets(self, k: int) -> List[Set[str]]:
        """Return the last ``k`` prefilter top-N as Python sets, oldest
        first.  If fewer than ``k`` snapshots exist returns whatever is
        available (caller should check len before applying filter)."""
        rows = self.load()
        if k <= 0 or not rows:
            return []
        tail = rows[-k:]
        return [set(r.get("prefilter_topn", []) or []) for r in tail]

    def latest_date(self) -> Optional[str]:
        rows = self.load()
        if not rows:
            return None
        return rows[-1].get("date")

    # ── Write ───────────────────────────────────────────────────────
    def append(
        self,
        date: str,
        regime: str,
        prefilter_topn: List[str],
        topn_size: int,
    ) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        rows = self.load()
        # Idempotent re-run: if the last row's date matches, drop it
        # so the new snapshot replaces it.  We rewrite the whole file
        # in this case (cheap — file is tiny: ~1 row per trading day).
        if rows and str(rows[-1].get("date", "")) == str(date):
            rows = rows[:-1]
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)

        record = {
            "schema":         SCHEMA_VERSION,
            "date":           str(date),
            "regime":         str(regime),
            "topn_size":      int(topn_size),
            "prefilter_topn": [str(t) for t in (prefilter_topn or [])],
            "ts":             datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
