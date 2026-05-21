"""Round 4 (P2-B persistence) — durable order-state store.

Codex R4 §5.2 prescribed:
- append-only JSONL event log at `daily_runs/<RUN_ID>/autotrade_orders.jsonl`
- summary load helper for orchestrator + report builders
- restart-safe duplicate-submit guard via deterministic `client_order_id`

Design goals
------------
1. **One event = one transition.** A state never mutates an existing
   line; new state means a new line. Crash mid-write at worst loses the
   newest line; what already landed remains valid.
2. **Deterministic client_order_id.** Codex R4 §9 recipe:
     co-<RUN_DATE_YYYYMMDD>-<REC_ROW_ID>-<B|S>-<QTY>-<sha6(run_id|row|side|qty)>
   Same intent yields the same id forever, so a re-invocation of the
   orchestrator after a crash sees the prior `SUBMITTED`/`FILLED` line
   and short-circuits before talking to the broker.
3. **No KIS coupling.** This module never imports the broker adapter;
   it consumes the values orchestrator extracts and writes JSONL.
4. **JSON-safe.** Every event is dict-serializable via stdlib json.

Schema (`autotrade_order_event/v1`)
-----------------------------------
```json
{
  "schema_version": "autotrade_order_event/v1",
  "event_id":        "ev-2026-05-12T13-30-00-000Z-a3f1b2",
  "event_ts":        "2026-05-12T13:30:00.000+00:00",
  "event_kind":      "transition" | "run_started" | "run_ended",
  "autotrade_run_id":"at-20260512T133000Z-89af",
  "mode":            "dry_run" | "paper_submit",

  "run_id":          "20260512_210645_daily",
  "rec_row_id":      76,
  "ticker":          "APA",
  "market":          "NASD",
  "side":            "BUY",

  "qty_intended":    6,
  "qty_filled":      6,
  "qty_remaining":   0,
  "limit_price":     36.97,

  "client_order_id": "co-20260512-76-B-6-a3f1b2",
  "broker_order_id": "0000049652",

  "state":           "filled",                   # OrderState.value
  "status_source":   "position_delta",           # StatusSource.value
  "raw_broker_row":  { ... } | null,
  "echo":            { "source": "nccs"|"ccnl"|null, "matched": bool } | null,
  "fill_price":      37.1883,
  "fill_price_source":"paper_cash_delta",
  "error":           null
}
```

The `run_started` / `run_ended` events carry a small subset (mode,
gates, counts, durations) for the summary builder.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Optional

from phase3.autotrade.order_state import OrderState, StatusSource


SCHEMA_VERSION = "autotrade_order_event/v1"


# ──────────────────────────────────────────────────────────────────────
# Deterministic ids
# ──────────────────────────────────────────────────────────────────────
_RUN_DATE_RE = re.compile(r"^(\d{8})_")


def _run_date(run_id: str) -> str:
    """Extract the YYYYMMDD prefix from a run_id like '20260512_210645_daily'.
    Falls back to a placeholder so the resulting client_order_id is still
    deterministic on the rest of its inputs even if the run_id is custom.
    """
    m = _RUN_DATE_RE.match(run_id or "")
    return m.group(1) if m else "00000000"


def build_client_order_id(
    *,
    run_id: str,
    rec_row_id: int,
    side: str,
    qty: int,
) -> str:
    """Return the deterministic client_order_id Codex R4 §9 prescribed.

    Same (run_id, rec_row_id, side, qty) → same id. Used as the
    duplicate-submit key in `OrderStore.find_latest_by_client_id`.
    """
    side_c = (side or "?")[0].upper()
    digest = hashlib.sha256(
        f"{run_id}|{rec_row_id}|{side.upper()}|{int(qty)}".encode("utf-8")
    ).hexdigest()[:6]
    return f"co-{_run_date(run_id)}-{int(rec_row_id)}-{side_c}-{int(qty)}-{digest}"


def new_autotrade_run_id() -> str:
    """Unique id per orchestrator invocation. Different from
    `client_order_id` because it covers the whole batch, not one row."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"at-{ts}-{uuid.uuid4().hex[:4]}"


def _event_id() -> str:
    """Sortable, collision-resistant event id."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    return f"ev-{ts}-{uuid.uuid4().hex[:6]}"


def _now_iso() -> str:
    # microseconds so unit tests writing two events in the same ms can
    # still order them deterministically. Standard ISO-8601 sort still
    # works against the older millisecond-precision rows we already have
    # on disk.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ──────────────────────────────────────────────────────────────────────
# Event records (in-memory mirror of one JSONL line)
# ──────────────────────────────────────────────────────────────────────
@dataclass
class StoredEvent:
    """One row from the JSONL, parsed back into a tagged dict-ish shape.

    Most callers just read `.raw` (the full dict) — this class exists so
    typed accessors on the common fields don't have to scatter
    `.get(...) or default` everywhere.
    """
    raw: Dict[str, Any]

    @property
    def event_kind(self) -> str:
        return str(self.raw.get("event_kind", "transition"))

    @property
    def state(self) -> Optional[OrderState]:
        v = self.raw.get("state")
        if v is None:
            return None
        try:
            return OrderState(v)
        except ValueError:
            return None

    @property
    def client_order_id(self) -> Optional[str]:
        v = self.raw.get("client_order_id")
        return str(v) if v else None

    @property
    def broker_order_id(self) -> Optional[str]:
        v = self.raw.get("broker_order_id")
        return str(v) if v else None

    @property
    def event_ts(self) -> str:
        return str(self.raw.get("event_ts", ""))


# ──────────────────────────────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────────────────────────────
class OrderStore:
    """Append-only JSONL store for one orchestrator invocation, keyed
    by the artifact run.

    Multiple orchestrator runs against the same artifact share the same
    JSONL file (events are tagged with `autotrade_run_id` so the summary
    builder can separate them). This is how a crashed run gets resumed:
    the next invocation reads the same file, finds prior
    `SUBMITTED`/`FILLED` rows for matching client_order_ids, and skips.
    """

    def __init__(self, jsonl_path: Path):
        self.path = Path(jsonl_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Don't create the file on open — first write opens it. Keeps
        # `--dry-run` from leaving empty files behind on failure.

    # -- writes ------------------------------------------------------
    def append_event(self, event: Dict[str, Any]) -> StoredEvent:
        """Write one event line atomically (a single write() on a JSONL
        line is atomic on most POSIX filesystems for small payloads)."""
        payload = dict(event)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("event_id", _event_id())
        payload.setdefault("event_ts", _now_iso())
        line = json.dumps(payload, ensure_ascii=False, sort_keys=False) + "\n"
        # Use line-buffered append so concurrent reads see whole lines.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return StoredEvent(raw=payload)

    def log_run_started(
        self,
        *,
        autotrade_run_id: str,
        mode: str,
        run_id: str,
        gates: Dict[str, Any],
        artifact_dir: str,
        intents_count: int,
        cli_args: Optional[Dict[str, Any]] = None,
    ) -> StoredEvent:
        return self.append_event({
            "event_kind": "run_started",
            "autotrade_run_id": autotrade_run_id,
            "mode": mode,
            "run_id": run_id,
            "artifact_dir": artifact_dir,
            "gates": gates,
            "intents_count": intents_count,
            "cli_args": cli_args or {},
        })

    def log_run_ended(
        self,
        *,
        autotrade_run_id: str,
        run_id: str,
        counts: Dict[str, int],
        cash_delta_usd: float,
        position_delta_by_ticker: Dict[str, int],
        duration_sec: float,
    ) -> StoredEvent:
        return self.append_event({
            "event_kind": "run_ended",
            "autotrade_run_id": autotrade_run_id,
            "run_id": run_id,
            "counts": counts,
            "cash_delta_usd": cash_delta_usd,
            "position_delta_by_ticker": position_delta_by_ticker,
            "duration_sec": duration_sec,
        })

    def log_operator_cleared(
        self,
        *,
        autotrade_run_id: str,
        run_id: str,
        client_order_id: str,
        broker_state_at_clear: str,
        operator_note: str = "",
        broker_probe: Optional[Dict[str, Any]] = None,
    ) -> StoredEvent:
        """Record an explicit operator-driven clearing of a stuck
        client_order_id (R10F-S).

        R5B-P1.1's ``is_already_active`` treats any ACTIVE state ever
        seen for a cid as blocking — including the long tail of
        cancelled-then-reprice-then-cancelled chains that pile up on a
        retried CIEN/LRCX intent. The only safe out used to be moving
        the JSONL aside; that worked for one operator but lost audit
        history and was easy to miss.

        Now the operator can append a single ``operator_cleared`` event
        AFTER they have:

          1. Independently verified the broker has no live order for
             this cid (status one of ``cancelled``, ``rejected``,
             ``absent``, ``no_broker_contact``);
          2. Optionally captured the probe summary so a future audit
             can see what the broker actually said at the moment of
             the clear.

        ``is_already_active`` will then treat any ACTIVE evidence that
        predates this cleared event as resolved. A future ACTIVE
        transition for the same cid (e.g. a fresh paper-submit that
        reuses the deterministic cid) re-arms the guard, because the
        cleared event is no longer the latest entry for the cid.

        ``broker_state_at_clear`` is free-form but the UI restricts it
        to the four canonical values above. ``broker_probe`` is the
        raw structured payload the UI captured (e.g. the ccnl row for
        the original ODNO), kept for forensic value only.
        """
        return self.append_event({
            "event_kind": "operator_cleared",
            "autotrade_run_id": autotrade_run_id,
            "run_id": run_id,
            "client_order_id": client_order_id,
            "broker_state_at_clear": broker_state_at_clear,
            "operator_note": operator_note,
            "broker_probe": broker_probe or {},
        })

    def log_transition(
        self,
        *,
        autotrade_run_id: str,
        mode: str,
        run_id: str,
        rec_row_id: int,
        ticker: str,
        market: str,
        side: str,
        qty_intended: int,
        limit_price: float,
        client_order_id: str,
        state: OrderState,
        status_source: StatusSource,
        broker_order_id: Optional[str] = None,
        qty_filled: float = 0.0,
        qty_remaining: float = 0.0,
        raw_broker_row: Optional[Dict[str, Any]] = None,
        echo: Optional[Dict[str, Any]] = None,
        fill_price: Optional[float] = None,
        fill_price_source: Optional[str] = None,
        error: Optional[str] = None,
        note: Optional[str] = None,
    ) -> StoredEvent:
        return self.append_event({
            "event_kind": "transition",
            "autotrade_run_id": autotrade_run_id,
            "mode": mode,
            "run_id": run_id,
            "rec_row_id": int(rec_row_id),
            "ticker": ticker.upper(),
            "market": market,
            "side": side,
            "qty_intended": int(qty_intended),
            "qty_filled": float(qty_filled),
            "qty_remaining": float(qty_remaining),
            "limit_price": float(limit_price),
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "state": state.value,
            "status_source": status_source.value,
            "raw_broker_row": raw_broker_row,
            "echo": echo,
            "fill_price": fill_price,
            "fill_price_source": fill_price_source,
            "error": error,
            "note": note,
        })

    # -- reads -------------------------------------------------------
    def read_events(self) -> Iterator[StoredEvent]:
        """Yield every event in file order. Tolerates a partial last line
        (skips it with a warning marker rather than raising)."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    yield StoredEvent(raw=json.loads(line))
                except json.JSONDecodeError:
                    # Partial last line after a crash; ignore.
                    continue

    def find_latest_by_client_id(self, client_order_id: str) -> Optional[StoredEvent]:
        """Return the most recent transition event for the given
        client_order_id, or None if no event matches.

        Insertion order (last write wins) is the source of truth here.
        `event_ts` is microsecond-precision ISO-8601, but two events
        written in the same microsecond on a fast machine would tie on
        ts comparison; the JSONL is append-only so the last matching
        row IS the most recent."""
        latest: Optional[StoredEvent] = None
        for ev in self.read_events():
            if ev.event_kind != "transition":
                continue
            if ev.client_order_id != client_order_id:
                continue
            latest = ev
        return latest

    # Codex R5B-P1.1: states that, if seen ANY time in the event history
    # for a given client_order_id, mean the broker may already hold an
    # order with this id and we must not re-submit.
    _ACTIVE_STATES: FrozenSet[OrderState] = frozenset({
        OrderState.SUBMITTED,
        OrderState.OPEN_OR_PENDING,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_REQUESTED,
        OrderState.REPLACE_REQUESTED,
        OrderState.REPLACED,
    })

    @staticmethod
    def _unknown_is_blocking(raw: Dict[str, Any]) -> bool:
        """Codex R5B-P1.1: a transition logged with state=UNKNOWN can
        still mean the broker may hold the order. Treat it as blocking
        when any evidence of broker contact / suspect provenance is
        present:

        - `broker_order_id` is non-empty (ODNO was returned by KIS)
        - note text indicates the duplicate guard fired (we wrote it
          because we *believe* a prior active row exists)
        - note text indicates a post-submit-path exception (post-
          place_order failure)
        - note text contains the canonical "verify before retry" hint
        - an `error` field is populated (some failure happened that
          may have left state at broker indeterminate)

        Anything else (e.g. UNKNOWN from a missing-quote pre-trade
        check, or an early aborted run that never reached the broker)
        is not blocked. That matches the user-facing behavior: dry-run
        and reject paths do not produce these markers.
        """
        if raw.get("broker_order_id"):
            return True
        note = (raw.get("note") or "").lower()
        if "duplicate guard" in note:
            return True
        if "exception during paper_submit" in note:
            return True
        if "verify before retry" in note:
            return True
        if "outside _process_intent guard" in note:
            return True
        if raw.get("error"):
            return True
        return False

    def is_already_active(self, client_order_id: str) -> bool:
        """True iff the event history contains any transition for
        `client_order_id` whose state (or evidence of broker contact in
        an UNKNOWN row) means we must not re-submit.

        Codex R5B-P1.1 fix:
        the previous implementation looked only at the *latest* event,
        so a newer UNKNOWN row (e.g. one we wrote ourselves for the
        duplicate guard, or for a post-submit exception) could shadow
        an older SUBMITTED/FILLED row and let the next retry call
        place_order again. Now we scan all events and use a strict
        union of the active states plus blocking-UNKNOWN conditions.

        R10F-S extension:
        an explicit ``operator_cleared`` event (see
        ``log_operator_cleared``) invalidates ACTIVE evidence that
        predates it. We walk the cid's events in append order and
        flip an ``active`` flag on/off as we see ACTIVE or cleared
        rows; the final flag is the answer. A future ACTIVE row that
        lands AFTER an ``operator_cleared`` re-arms the guard, which
        is the desired behaviour: a fresh paper-submit reusing the
        same deterministic cid must still be blocked once it touches
        the broker again.
        """
        active = False
        for ev in self.read_events():
            if ev.client_order_id != client_order_id:
                continue
            kind = ev.event_kind
            if kind == "operator_cleared":
                active = False
                continue
            if kind != "transition":
                continue
            state = ev.state
            if state in self._ACTIVE_STATES:
                active = True
            elif state == OrderState.UNKNOWN and self._unknown_is_blocking(ev.raw):
                active = True
        return active

    def find_stuck_client_order_ids(self) -> List[str]:
        """R10F-S: list every client_order_id whose JSONL history is
        currently blocking (i.e. ``is_already_active`` would return
        True). Used by the panel's Operator Recovery UI to surface
        candidates without making the operator type the cid by hand.

        Order is first-appearance in the JSONL — stable across calls.
        Returns ``[]`` for an empty/missing store."""
        seen: List[str] = []
        for ev in self.read_events():
            cid = ev.client_order_id
            if not cid or cid in seen:
                continue
            seen.append(cid)
        return [cid for cid in seen if self.is_already_active(cid)]

    def find_latest_blocking_by_client_id(
        self, client_order_id: str,
    ) -> Optional[StoredEvent]:
        """Companion to `is_already_active`: return the most recent
        blocking event. Returns None if the client_id is not currently
        blocked. Insertion order (last write wins) is the tie-breaker.

        R10F-S: respects ``operator_cleared`` resolution events. An
        ACTIVE transition that predates a ``cleared`` row is no longer
        the latest blocking row — the cid is unblocked unless a later
        ACTIVE transition lands.
        """
        latest: Optional[StoredEvent] = None
        for ev in self.read_events():
            if ev.client_order_id != client_order_id:
                continue
            kind = ev.event_kind
            if kind == "operator_cleared":
                latest = None
                continue
            if kind != "transition":
                continue
            state = ev.state
            blocking = state in self._ACTIVE_STATES or (
                state == OrderState.UNKNOWN and self._unknown_is_blocking(ev.raw)
            )
            if not blocking:
                continue
            latest = ev
        return latest

    # -- summary -----------------------------------------------------
    def build_summary(
        self,
        *,
        autotrade_run_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """Build a flat summary dict over events tagged with the given
        `autotrade_run_id`. Last-state-wins per client_order_id."""
        started: Optional[Dict[str, Any]] = None
        ended: Optional[Dict[str, Any]] = None
        latest_per_co: Dict[str, StoredEvent] = {}

        for ev in self.read_events():
            if ev.raw.get("autotrade_run_id") != autotrade_run_id:
                continue
            kind = ev.event_kind
            if kind == "run_started":
                started = dict(ev.raw)
            elif kind == "run_ended":
                ended = dict(ev.raw)
            elif kind == "transition":
                co = ev.client_order_id or ""
                if not co:
                    continue
                # Insertion order: last write wins.
                latest_per_co[co] = ev

        state_counts: Counter[str] = Counter()
        rows: List[Dict[str, Any]] = []
        for co, ev in sorted(latest_per_co.items(), key=lambda kv: kv[1].event_ts):
            r = ev.raw
            rows.append({
                "client_order_id":  co,
                "broker_order_id":  r.get("broker_order_id"),
                "run_id":           r.get("run_id"),
                "rec_row_id":       r.get("rec_row_id"),
                "ticker":           r.get("ticker"),
                "side":             r.get("side"),
                "qty_intended":     r.get("qty_intended"),
                "qty_filled":       r.get("qty_filled"),
                "qty_remaining":    r.get("qty_remaining"),
                "limit_price":      r.get("limit_price"),
                "fill_price":       r.get("fill_price"),
                "fill_price_source":r.get("fill_price_source"),
                "state":            r.get("state"),
                "status_source":    r.get("status_source"),
                "echo":             r.get("echo"),
                "error":            r.get("error"),
                "note":             r.get("note"),
                "event_ts":         r.get("event_ts"),
            })
            if r.get("state"):
                state_counts[str(r["state"])] += 1

        summary = {
            "schema_version":     "autotrade_summary/v1",
            "autotrade_run_id":   autotrade_run_id,
            "run_id":             run_id,
            "started_at":         started.get("event_ts") if started else None,
            "ended_at":           ended.get("event_ts") if ended else None,
            "mode":               (started or {}).get("mode"),
            "gates":              (started or {}).get("gates"),
            "intents_count":      (started or {}).get("intents_count"),
            "counts_by_state":    dict(state_counts),
            "counts_total":       (ended or {}).get("counts") or {},
            "cash_delta_usd":     (ended or {}).get("cash_delta_usd"),
            "position_delta_by_ticker": (ended or {}).get("position_delta_by_ticker") or {},
            "duration_sec":       (ended or {}).get("duration_sec"),
            "orders":             rows,
        }
        return summary

    def write_summary_json(self, summary: Dict[str, Any], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
