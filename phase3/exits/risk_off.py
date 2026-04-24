"""D4.2 — Market-context global gate (risk-off detector).

``RiskOffAssessor`` consolidates four independent stress signals into one
boolean ``risk_off`` flag that triggers can opt into via a
``risk_off_only: bool`` param.  The design target is "hard to
coincidentally satisfy during a healthy pullback, easy to satisfy during
a real regime break" — hence the default threshold-count logic requires
**at least 2 of 4** signals to fire simultaneously.

The four signals (all configurable):

1. ``vix_level``  — ``vix >= vix_critical``           (default 30.0)
2. ``vix_spike`` — ``vix_7d_delta >= vix_spike_delta`` (default 10.0)
3. ``regime_break`` — today's regime is BULL→{SIDE, DEF/CRASH/BEAR} transition
                      that happened within ``recent_transition_days`` (default 5)
4. ``port_dd`` — ``portfolio_dd_pct <= -portfolio_dd_threshold`` (default 10.0)

The assessor is pure: no I/O, no global state.  Caller
(``pipeline.evaluate_exits``) builds a ``RiskOffInput`` from current
market + VIX history + recent regimes + portfolio peak, runs
``assess()``, and stamps the result onto ``MarketSnapshot``.

Config surface (``exit_triggers[].type == 'risk_off_gate'`` is **not** a
trigger — risk-off is always-on gate layer; triggers simply consult
``market.risk_off``).  Instead, the top-level strategy config can
override assessor thresholds via a flat ``risk_off`` sub-dict:

    risk_off:
        vix_critical: 30.0
        vix_spike_delta: 10.0
        regime_transition_days: 5
        portfolio_dd_threshold: 10.0
        threshold_count: 2
        vix_lookback: 7

The assessor is idempotent across calls — same inputs → same outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# Regimes considered "defensive" for the BULL→DEF transition check.
_DEF_REGIMES = {"SIDE", "DEF", "DEFENSIVE", "CRASH", "BEAR"}


@dataclass
class RiskOffInput:
    """All inputs needed to evaluate risk-off state on a single day.

    Assembled by the caller (daily_runner / simulator) and passed to the
    assessor.  Any missing data (empty vix_series, no portfolio_peak,
    etc.) results in the corresponding signal defaulting to *False* —
    the assessor never raises on partial data.
    """
    vix: float = 0.0
    vix_series: List[float] = field(default_factory=list)  # last N VIX closes (newest last)
    regime: str = ""
    recent_regimes: List[str] = field(default_factory=list)  # last N regime labels (newest last incl. today)
    portfolio_value: float = 0.0
    portfolio_peak: float = 0.0


@dataclass
class RiskOffResult:
    risk_off: bool
    level: int                             # number of True signals
    reasons: Tuple[str, ...]               # ordered, short labels
    vix_7d_delta: float
    portfolio_dd_pct: float                # <= 0


class RiskOffAssessor:
    """Threshold-count risk-off detector.

    Typical use::

        ass = RiskOffAssessor(threshold_count=2)
        res = ass.assess(RiskOffInput(
            vix=vix, vix_series=vix_hist,
            regime=today_regime, recent_regimes=regimes,
            portfolio_value=pv, portfolio_peak=peak_pv,
        ))
        market.risk_off = res.risk_off
    """

    def __init__(
        self,
        *,
        vix_critical: float = 30.0,
        vix_spike_delta: float = 10.0,
        vix_lookback: int = 7,
        regime_transition_days: int = 5,
        portfolio_dd_threshold: float = 10.0,   # in absolute %, positive number
        threshold_count: int = 2,
        # Regime transitions counted as stress: from BULL to any non-BULL regime.
        # Keep configurable for experimentation.
        stress_regimes: Optional[set] = None,
    ) -> None:
        self.vix_critical = float(vix_critical)
        self.vix_spike_delta = float(vix_spike_delta)
        self.vix_lookback = max(1, int(vix_lookback))
        self.regime_transition_days = max(1, int(regime_transition_days))
        self.portfolio_dd_threshold = float(portfolio_dd_threshold)
        self.threshold_count = max(1, int(threshold_count))
        self.stress_regimes = set(stress_regimes) if stress_regimes is not None else set(_DEF_REGIMES)

    @staticmethod
    def _norm(regime: str) -> str:
        return str(regime or "").upper()

    # ── Individual signals ──────────────────────────────────────────────────

    def _vix_level_hit(self, vix: float) -> bool:
        return float(vix) >= self.vix_critical if vix else False

    def _vix_spike_hit(self, vix: float, vix_series: List[float]) -> Tuple[bool, float]:
        """Return (hit, vix_7d_delta).  delta = vix - vix[-lookback]."""
        if not vix_series or len(vix_series) < 2:
            return False, 0.0
        lb = min(self.vix_lookback, len(vix_series) - 1)
        past = float(vix_series[-(lb + 1)])
        delta = float(vix) - past
        return (delta >= self.vix_spike_delta), delta

    def _regime_break_hit(self, regime: str, recent_regimes: List[str]) -> bool:
        """True if any BULL→stress transition happened in the last
        ``regime_transition_days`` calendar slots of ``recent_regimes``
        (including today's regime as the final entry)."""
        cur = self._norm(regime)
        if cur not in self.stress_regimes:
            return False
        if not recent_regimes or len(recent_regimes) < 2:
            return False
        window = recent_regimes[-self.regime_transition_days - 1:]
        norm = [self._norm(r) for r in window]
        # Look for BULL followed by a stress-regime within the window.
        for i in range(len(norm) - 1):
            if norm[i] == "BULL" and norm[i + 1] in self.stress_regimes:
                return True
        return False

    def _portfolio_dd_hit(self, pv: float, peak: float) -> Tuple[bool, float]:
        """Return (hit, dd_pct).  dd_pct is negative (or 0) by convention."""
        if peak <= 0 or pv <= 0:
            return False, 0.0
        dd = (pv / peak - 1.0) * 100.0  # <= 0
        # Threshold stored as positive number; DD is negative, hence the sign flip.
        return (dd <= -self.portfolio_dd_threshold), dd

    # ── Main entry ──────────────────────────────────────────────────────────

    def assess(self, inp: RiskOffInput) -> RiskOffResult:
        reasons: List[str] = []

        if self._vix_level_hit(inp.vix):
            reasons.append(f"vix_level({inp.vix:.1f}>={self.vix_critical:.0f})")

        vix_hit, vix_delta = self._vix_spike_hit(inp.vix, inp.vix_series)
        if vix_hit:
            reasons.append(f"vix_spike({vix_delta:+.1f}>={self.vix_spike_delta:.0f})")

        if self._regime_break_hit(inp.regime, inp.recent_regimes):
            reasons.append(f"regime_break(BULL->{self._norm(inp.regime)})")

        port_hit, dd_pct = self._portfolio_dd_hit(
            inp.portfolio_value, inp.portfolio_peak)
        if port_hit:
            reasons.append(f"port_dd({dd_pct:+.1f}%)")

        level = len(reasons)
        risk_off = level >= self.threshold_count
        return RiskOffResult(
            risk_off=risk_off,
            level=level,
            reasons=tuple(reasons),
            vix_7d_delta=vix_delta,
            portfolio_dd_pct=dd_pct,
        )

    # Convenience: build from a strategy config dict.
    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "RiskOffAssessor":
        cfg = cfg or {}
        return cls(
            vix_critical=cfg.get("vix_critical", 30.0),
            vix_spike_delta=cfg.get("vix_spike_delta", 10.0),
            vix_lookback=cfg.get("vix_lookback", 7),
            regime_transition_days=cfg.get("regime_transition_days", 5),
            portfolio_dd_threshold=cfg.get("portfolio_dd_threshold", 10.0),
            threshold_count=cfg.get("threshold_count", 2),
            stress_regimes=(
                set(cfg["stress_regimes"])
                if isinstance(cfg.get("stress_regimes"), (list, tuple, set))
                else None
            ),
        )
