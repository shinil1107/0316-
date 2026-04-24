"""Core contracts for the Phase 3 exit-trigger system.

Three dataclasses + one Protocol define the whole surface area that
triggers and callers must agree on:

* ``HoldingSnapshot``  — per-ticker state at decision time.
* ``MarketSnapshot``   — environment (regime, scores, history view).
* ``ExitVerdict``      — what a trigger decided for one holding.
* ``ExitTrigger``      — the Protocol each trigger implements.

Triggers MUST be pure functions of the two snapshots plus their own
immutable config.  No mutation of snapshots, no I/O, no global state.
History lookup goes through ``MarketSnapshot.history`` which owns its
own day-scoped cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Optional, Set, Tuple, TYPE_CHECKING
import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from .history import HistoryView


# ─────────────────────────────────────────────────────────────────────────────
# Verdict enum (string-typed for YAML friendliness + legacy Action compatibility)
# ─────────────────────────────────────────────────────────────────────────────

class VerdictAction:
    """Allowed values for ``ExitVerdict.action``.

    Not a true Enum — kept as string constants so YAML/JSON round-trip and
    existing ``recos["Action"]`` code paths stay unchanged.

    High-level semantics (pipeline dispatches to recos Action string via
    ``ExitVerdict.recos_action``):

    * ``SELL``     — close the position fully.
    * ``TRIM``     — partial close, fraction in ``partial_pct``.
    * ``WARN``     — no position change, emit a record row (e.g. SELL_GRACE
                     warning that a ticker is counting down to auto-sell).
    * ``HOLD``     — no action needed, do not emit any row.
    * ``ESCALATE`` — this trigger declines, let the next lower-priority
                     trigger decide.  Distinct from HOLD: HOLD is a final
                     decision; ESCALATE is 'I abstain'.
    """
    SELL = "SELL"
    TRIM = "TRIM"
    WARN = "WARN"
    HOLD = "HOLD"
    ESCALATE = "ESCALATE"

    @classmethod
    def is_terminal(cls, action: str) -> bool:
        """Terminal = decision is final, stop evaluating lower-priority triggers."""
        return action in (cls.SELL, cls.TRIM, cls.WARN)


class RecosAction:
    """Canonical strings that appear in the ``recos["Action"]`` column.

    Unlike ``VerdictAction`` (which governs trigger-pipeline control flow),
    these are the **emit-layer** labels that ultimately drive portfolio
    dispatch (``SimPortfolio.apply_actions`` / ``HoldingsManager.apply_*``)
    and user-facing reports.

    Naming convention: ``<SELL|TRIM>_<TRIGGER_SUFFIX>``.  The suffix encodes
    which trigger fired so reports / sweeps can attribute P&L contribution
    without parsing reason strings.

    Backward-compat:
      * Legacy strings ``STOP_LOSS``, ``SELL``, ``SELL_GRACE``, ``TRIM``,
        ``TRIM_GRACE`` are preserved byte-for-byte.
      * New D2 triggers use the ``*_<suffix>`` variants.  Any call site
        that dispatches on Action should consult ``FULL_CLOSE`` /
        ``PARTIAL_CLOSE`` / ``NO_OP`` rather than hard-coding strings.
    """
    # ── Full-close (SELL family) ───────────────────────────────────────
    STOP_LOSS = "STOP_LOSS"
    SELL = "SELL"
    SELL_PEAK_DD = "SELL_PEAK_DD"        # D2.1
    SELL_SCORE_DECAY = "SELL_SCORE_DECAY"  # D2.2
    SELL_TREND_BREAK = "SELL_TREND_BREAK"  # D2.3
    SELL_RANK_VEL = "SELL_RANK_VEL"      # D2.4
    SELL_REL_REBAR = "SELL_REL_REBAR"    # D2.5
    SELL_REGIME = "SELL_REGIME"          # D2.6
    SELL_ATR_TRAIL = "SELL_ATR_TRAIL"    # D4.1 volatility-adaptive trailing stop
    SELL_PROFIT = "SELL_PROFIT"          # D4.3 profit-target exit (full close)

    # ── Partial-close (TRIM family) ────────────────────────────────────
    TRIM = "TRIM"
    TRIM_GRACE = "TRIM_GRACE"
    TRIM_PEAK_DD = "TRIM_PEAK_DD"
    TRIM_SCORE_DECAY = "TRIM_SCORE_DECAY"
    TRIM_TREND_BREAK = "TRIM_TREND_BREAK"
    TRIM_RANK_VEL = "TRIM_RANK_VEL"
    TRIM_REL_REBAR = "TRIM_REL_REBAR"
    TRIM_REGIME = "TRIM_REGIME"
    TRIM_ATR_TRAIL = "TRIM_ATR_TRAIL"    # D4.1 (partial variant)
    TRIM_PROFIT = "TRIM_PROFIT"          # D4.3 (tiered partial profit-take)

    # ── Record-only (WARN family, no position change) ──────────────────
    SELL_GRACE = "SELL_GRACE"

    # ── Dispatch sets ──────────────────────────────────────────────────
    FULL_CLOSE = frozenset({
        STOP_LOSS, SELL,
        SELL_PEAK_DD, SELL_SCORE_DECAY, SELL_TREND_BREAK,
        SELL_RANK_VEL, SELL_REL_REBAR, SELL_REGIME,
        SELL_ATR_TRAIL, SELL_PROFIT,
    })
    PARTIAL_CLOSE = frozenset({
        TRIM, TRIM_GRACE,
        TRIM_PEAK_DD, TRIM_SCORE_DECAY, TRIM_TREND_BREAK,
        TRIM_RANK_VEL, TRIM_REL_REBAR, TRIM_REGIME,
        TRIM_ATR_TRAIL, TRIM_PROFIT,
    })
    NO_OP = frozenset({SELL_GRACE})

    @classmethod
    def is_full_close(cls, action: str) -> bool:
        return action in cls.FULL_CLOSE

    @classmethod
    def is_partial_close(cls, action: str) -> bool:
        return action in cls.PARTIAL_CLOSE

    @classmethod
    def is_no_op(cls, action: str) -> bool:
        return action in cls.NO_OP


# ─────────────────────────────────────────────────────────────────────────────
# Snapshots
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HoldingSnapshot:
    """Per-ticker state passed to each trigger's ``evaluate()``.

    All fields are computed once per (date, ticker) by
    ``state.build_holding_snapshots`` and are treated as read-only by triggers.
    """
    ticker: str
    shares: int

    # Entry attribution (set at first BUY_NEW, carried forward).
    entry_date: str = ""
    entry_price: float = 0.0
    entry_score: float = 0.0
    entry_rank: int = -1            # 1-based rank in scores_df at entry; -1 if unknown

    # Current market values.
    current_price: float = 0.0
    current_score: float = 0.0
    current_rank: int = -1          # 1-based rank today; -1 if not ranked today

    # Running extremes (maintained by SimPortfolio / HoldingsManager).
    peak_price: float = 0.0

    # Derived (computed in snapshot builder).
    pnl_pct: float = 0.0            # (current_price / entry_price - 1) * 100
    peak_drawdown_pct: float = 0.0  # (current_price / peak_price - 1) * 100
    days_held: int = 0

    # Grace state (from previous day's recos — parity with legacy path).
    grace_count: int = 0            # consecutive days out of top_n so far

    # Regime at entry (optional; used by some D2 triggers).
    entry_regime: str = ""

    # D4.3 — profit-target stateful memory.  Set of tier_pct values that
    # have already been executed for this ticker (so each tier fires once).
    # Populated by ``build_holding_snapshots`` from ``holdings_store``.
    profit_targets_hit: frozenset = frozenset()


@dataclass
class MarketSnapshot:
    """Shared environment state for all triggers on a given evaluation day."""
    date: str
    regime: str                    # today's regime (BULL/SIDE/DEFENSIVE/CRASH/BEAR)
    prev_regime: str               # yesterday's regime; "" if first day
    vix: float
    scores_df: pd.DataFrame        # full ranked (Ticker, Score, Price) for today
    top_n: int                     # regime-resolved top_n
    portfolio_value: float         # holdings + cash
    history: Optional["HistoryView"] = None  # None only in unit tests

    # Set by generate_recommendations: True when today's prev_recos date == today,
    # meaning the simulator/live daily runner was re-executed on the same day
    # and grace counters should NOT advance.  Matches legacy
    # ``same_day_rerun`` semantic in daily_runner.py.
    same_day_rerun: bool = False

    # ── D4.2 — Market-context global gate (risk-off flag) ───────────────────
    # Computed once per day by ``pipeline.evaluate_exits`` via
    # ``RiskOffAssessor``.  Triggers that opt-in via their
    # ``risk_off_only`` param must check ``market.risk_off`` before firing.
    risk_off: bool = False           # True when market is in heightened-stress mode
    risk_off_level: int = 0          # number of conditions that evaluated True (0..N)
    risk_off_reasons: Tuple[str, ...] = ()  # short labels for logging / diagnostics
    vix_7d_delta: float = 0.0        # VIX today - VIX(today-7): used by RiskOffAssessor
    portfolio_dd_pct: float = 0.0    # portfolio drawdown from rolling peak (<=0 by convention)


@dataclass
class ExitVerdict:
    """Decision returned by a trigger for a single holding.

    The pipeline uses ``action`` to control flow and ``recos_action`` to
    construct the actual ``recos`` DataFrame row.  This two-level scheme
    lets multiple triggers share a high-level semantic (e.g. "close out
    fully = SELL") while emitting distinct legacy ``Action`` strings
    (e.g. ``"STOP_LOSS"`` vs ``"SELL"``).
    """
    action: str = VerdictAction.HOLD
    partial_pct: float = 0.0        # TRIM only, in [0, 1]
    reason: str = ""                 # short human-readable, ends up in logs
    trigger_name: str = ""           # set by pipeline, not by trigger itself

    # Legacy-parity row-emission fields (triggers populate these):
    recos_action: str = ""           # e.g. "STOP_LOSS", "SELL", "TRIM_GRACE", "SELL_GRACE"
    grace_count: int = 0             # GraceCount column for grace-family rows

    # D4.3+ — small free-form metadata bag for trigger-specific state the
    # caller needs to act on (e.g. profit_target tier_pct for state
    # persistence).  Keys & types are owned by individual triggers.
    meta: dict = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return VerdictAction.is_terminal(self.action)

    @classmethod
    def hold(cls) -> "ExitVerdict":
        return cls(action=VerdictAction.HOLD)

    @classmethod
    def escalate(cls, reason: str = "") -> "ExitVerdict":
        return cls(action=VerdictAction.ESCALATE, reason=reason)

    @classmethod
    def sell(
        cls, reason: str = "", recos_action: str = "SELL",
        meta: Optional[dict] = None,
    ) -> "ExitVerdict":
        return cls(action=VerdictAction.SELL, reason=reason,
                   recos_action=recos_action,
                   meta=dict(meta) if meta else {})

    @classmethod
    def trim(
        cls, pct: float, reason: str = "",
        recos_action: str = "TRIM", grace_count: int = 0,
        meta: Optional[dict] = None,
    ) -> "ExitVerdict":
        return cls(action=VerdictAction.TRIM, partial_pct=float(pct),
                   reason=reason, recos_action=recos_action,
                   grace_count=int(grace_count),
                   meta=dict(meta) if meta else {})

    @classmethod
    def warn(
        cls, reason: str = "",
        recos_action: str = "SELL_GRACE", grace_count: int = 0,
    ) -> "ExitVerdict":
        return cls(action=VerdictAction.WARN, reason=reason,
                   recos_action=recos_action, grace_count=int(grace_count))


# ─────────────────────────────────────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────────────────────────────────────

class ExitTrigger(Protocol):
    """Contract every concrete trigger implements.

    Implementations should inherit from ``BaseTrigger`` (below) rather than
    this Protocol directly — the base class handles ``is_active`` boilerplate
    and provides sane defaults.
    """
    name: str                       # e.g. "stop_loss"  (unique)
    priority: int                   # higher evaluated first
    enabled_regimes: Set[str]       # regime names where this trigger fires

    def is_active(self, regime: str) -> bool: ...
    def evaluate(self, h: HoldingSnapshot, m: MarketSnapshot) -> ExitVerdict: ...


# Convenience base class — triggers inherit, fill in .name/.priority + evaluate().
# Kept separate from the Protocol so duck-typed implementations still pass.

class BaseTrigger:
    """Default implementation of non-logic plumbing.

    Concrete triggers override ``evaluate()`` and set class-level ``name``
    and ``default_priority``; ``enabled_regimes`` defaults to all three
    scoring regimes (BULL/SIDE/DEF).  DEFENSIVE/CRASH/BEAR are mapped to
    the DEF bucket by ``is_active``.
    """
    name: str = "base"
    default_priority: int = 0

    # DEFENSIVE / CRASH / BEAR all count as "DEF" for trigger routing —
    # keeps config short (users write one key, not three).
    _REGIME_ALIASES = {
        "BULL": "BULL",
        "SIDE": "SIDE",
        "DEF": "DEF",
        "DEFENSIVE": "DEF",
        "CRASH": "DEF",
        "BEAR": "DEF",
    }

    def __init__(
        self,
        priority: Optional[int] = None,
        enabled_regimes: Optional[Set[str]] = None,
        **params,
    ):
        self.priority = int(priority) if priority is not None else self.default_priority
        self.enabled_regimes = (
            {r.upper() for r in enabled_regimes}
            if enabled_regimes is not None
            else {"BULL", "SIDE", "DEF"}
        )
        self.params = dict(params)

    def is_active(self, regime: str) -> bool:
        key = self._REGIME_ALIASES.get(str(regime).upper(), str(regime).upper())
        return key in self.enabled_regimes

    # Default: do nothing, let lower-priority triggers decide.
    def evaluate(self, h: HoldingSnapshot, m: MarketSnapshot) -> ExitVerdict:
        return ExitVerdict.escalate(reason="base trigger (no logic)")

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} name={self.name!r} prio={self.priority} "
            f"regimes={sorted(self.enabled_regimes)} params={self.params}>"
        )
