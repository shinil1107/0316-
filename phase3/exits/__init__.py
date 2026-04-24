"""Phase 3 Dynamic Exit Architecture (Track D).

Pluggable exit-trigger system. Replaces the flat ``if enable_stop_loss`` /
``if sell_grace_days > 0`` branches in ``generate_recommendations`` with a
priority-ordered stack of ``ExitTrigger`` instances.

Design invariants (D1 refactor, pre-D2):
    * If ``strategy.exit_triggers`` is absent in config, ``build_triggers``
      consumes the legacy keys (``sell_grace_days``, ``enable_stop_loss``,
      ``stop_loss_pct``, ``grace_step1_days``, ``grace_step1_sell_pct``)
      and constructs the same two built-in triggers at the same priorities.
    * In that mode, backtest output is **byte-identical** to pre-refactor.
    * If ``strategy.exit_triggers`` is present, it is authoritative and the
      legacy keys are ignored for exit decisions.

Public entry points are re-exported here for convenience.
"""

from .base import (
    HoldingSnapshot,
    MarketSnapshot,
    ExitVerdict,
    ExitTrigger,
    VerdictAction,
    RecosAction,
)
from .registry import build_triggers, register_trigger, TRIGGER_REGISTRY
from .history import HistoryView, build_history_view
from .state import build_holding_snapshots, extend_holding_fields
from .pipeline import evaluate_exits, load_grace_state_from_recos
from .risk_off import RiskOffAssessor, RiskOffInput, RiskOffResult

# Eager-import trigger implementations so ``TRIGGER_REGISTRY`` is populated
# at module load time (no surprise empty-registry reads before the first
# ``build_triggers`` call).  Keep this at the bottom to avoid partial-init
# issues — all the names we re-export are already bound above.
from . import triggers as _triggers  # noqa: F401  (side-effect: @register_trigger fires)

__all__ = [
    "HoldingSnapshot",
    "MarketSnapshot",
    "ExitVerdict",
    "ExitTrigger",
    "VerdictAction",
    "RecosAction",
    "build_triggers",
    "register_trigger",
    "TRIGGER_REGISTRY",
    "HistoryView",
    "build_history_view",
    "build_holding_snapshots",
    "extend_holding_fields",
    "evaluate_exits",
    "load_grace_state_from_recos",
    "RiskOffAssessor",
    "RiskOffInput",
    "RiskOffResult",
]
