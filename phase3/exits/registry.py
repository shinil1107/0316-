"""Registry + factory for exit triggers.

Two jobs:

1. ``register_trigger(cls)`` — decorator concrete trigger classes use to
   self-register by their ``name``.  Triggers in ``phase3.exits.triggers.*``
   import this decorator at module load time, so merely importing the
   ``triggers`` package populates ``TRIGGER_REGISTRY``.

2. ``build_triggers(strategy_conf)`` — turns a strategy dict into an
   ordered list of live trigger instances.  Accepts both modes:

   * **Explicit mode**: ``strategy_conf["exit_triggers"]`` is a list of
     ``{type, priority?, regimes?, params}`` entries → dispatched via the
     registry.
   * **Legacy mode**: ``exit_triggers`` absent → synthesize the equivalent
     ``stop_loss`` + ``sell_grace`` trigger configs from the flat keys
     (``enable_stop_loss``, ``stop_loss_pct``, ``sell_grace_days``,
     ``grace_step1_days``, ``grace_step1_sell_pct``).

The D1 refactor invariant is: legacy mode must produce output byte-identical
to the pre-refactor generate_recommendations.  That contract lives here — if
it ever breaks, this module is the first suspect.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type
import copy

from .base import BaseTrigger, ExitTrigger


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

TRIGGER_REGISTRY: Dict[str, Type[BaseTrigger]] = {}


def register_trigger(cls: Type[BaseTrigger]) -> Type[BaseTrigger]:
    """Class decorator: register a trigger under its ``cls.name``."""
    name = getattr(cls, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError(
            f"register_trigger: {cls.__name__} must define class attribute `name: str`"
        )
    if name in TRIGGER_REGISTRY and TRIGGER_REGISTRY[name] is not cls:
        raise ValueError(
            f"register_trigger: duplicate name {name!r} "
            f"(existing={TRIGGER_REGISTRY[name].__name__}, new={cls.__name__})"
        )
    TRIGGER_REGISTRY[name] = cls
    return cls


def _ensure_triggers_imported() -> None:
    """Lazy-import the ``triggers`` package so decorators run.

    Called from ``build_triggers`` — keeps ``registry.py`` importable on its
    own (useful in unit tests that stub the registry).
    """
    # noqa: local import to avoid a circular import at module load time.
    try:
        from . import triggers  # noqa: F401
    except ImportError:
        # Triggers package not yet present (e.g. very early D1.1 scaffolding).
        # Registry stays empty; build_triggers will raise with a clear error.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

# Priorities for the legacy-mode auto-constructed triggers.  Chosen to
# preserve the hardcoded precedence in pre-refactor generate_recommendations:
# STOP_LOSS evaluated before SELL_GRACE.
_LEGACY_PRIORITY_STOP_LOSS = 100
_LEGACY_PRIORITY_SELL_GRACE = 10


def _synthesize_legacy_configs(strat: dict) -> List[dict]:
    """Build the equivalent ``exit_triggers`` list from legacy flat keys.

    Returns an empty list if neither legacy feature is enabled (user has
    disabled all exits via config — rare but legal).
    """
    out: List[dict] = []

    enable_sl = bool(strat.get("enable_stop_loss", False))
    if enable_sl:
        out.append({
            "type": "stop_loss",
            "priority": _LEGACY_PRIORITY_STOP_LOSS,
            # Legacy mode: SL respects regime_overrides.* (already resolved into
            # strat by simulator.resolve_strategy before we see it), so we route
            # to all three regimes and let the evaluate() short-circuit on
            # threshold=0 if the regime-specific override disabled SL.
            "regimes": ["BULL", "SIDE", "DEF"],
            "params": {
                "threshold_pct": float(strat.get("stop_loss_pct", -15.0)),
            },
        })

    grace_days = int(strat.get("sell_grace_days", 0) or 0)
    if grace_days > 0:
        out.append({
            "type": "sell_grace",
            "priority": _LEGACY_PRIORITY_SELL_GRACE,
            "regimes": ["BULL", "SIDE", "DEF"],
            "params": {
                "days": grace_days,
                "step1_days": int(strat.get("grace_step1_days", 0) or 0),
                "step1_sell_pct": float(strat.get("grace_step1_sell_pct", 0.5)),
            },
        })

    return out


def _build_one(entry: dict) -> BaseTrigger:
    """Instantiate a single trigger from its config dict."""
    if not isinstance(entry, dict):
        raise TypeError(f"exit_triggers entry must be a dict, got {type(entry).__name__}")

    t_name = entry.get("type")
    if not t_name:
        raise ValueError(f"exit_triggers entry missing 'type': {entry}")

    cls = TRIGGER_REGISTRY.get(t_name)
    if cls is None:
        known = sorted(TRIGGER_REGISTRY.keys()) or ["<none registered>"]
        raise KeyError(
            f"Unknown exit trigger type {t_name!r}. Registered: {known}"
        )

    kwargs = {
        "priority": entry.get("priority"),
        "enabled_regimes": entry.get("regimes"),
    }
    kwargs.update(copy.deepcopy(entry.get("params", {}) or {}))
    return cls(**kwargs)


def build_triggers(strategy_conf: Optional[dict]) -> List[BaseTrigger]:
    """Resolve a strategy dict to an ordered list of trigger instances.

    Returns the list sorted by ``priority`` descending (highest first), so
    callers can iterate directly.
    """
    _ensure_triggers_imported()

    strat = dict(strategy_conf or {})
    explicit = strat.get("exit_triggers")

    if explicit is not None:
        if not isinstance(explicit, list):
            raise TypeError(
                f"strategy.exit_triggers must be a list, got {type(explicit).__name__}"
            )
        entries = explicit
    else:
        entries = _synthesize_legacy_configs(strat)

    triggers = [_build_one(e) for e in entries]
    triggers.sort(key=lambda t: -int(t.priority))
    return triggers
