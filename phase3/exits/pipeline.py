"""Trigger-evaluation pipeline — one call per simulation / live daily step.

This module is the bridge between ``daily_runner.generate_recommendations``
and the trigger stack.  Its single public entry point
``evaluate_exits(...)`` does three things:

  1. Optionally restore grace-counter state from yesterday's recos (only
     when a trigger that cares about it — currently ``sell_grace`` — is
     in the stack).  Restoration logic replicates the legacy block at
     ``daily_runner.py:608-621`` verbatim, including the
     ``same_day_rerun`` semantics.
  2. Build ``HoldingSnapshot`` instances for every currently-held ticker
     and one ``MarketSnapshot`` for the day.
  3. Walk the priority-sorted trigger list for each holding, stopping at
     the first terminal verdict (SELL / TRIM / WARN).  Record the
     ``trigger_name`` on the verdict for downstream logging / row
     construction.

The caller is responsible for translating the verdicts into recos rows
(STOP_LOSS / SELL / SELL_GRACE / TRIM_GRACE) — this module stays in the
"decide" layer, not the "emit" layer, to keep row-schema coupling away
from the trigger authors.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

import pandas as pd

from .base import ExitVerdict, HoldingSnapshot, MarketSnapshot
from .state import build_holding_snapshots
from .risk_off import RiskOffAssessor, RiskOffInput


# Names of triggers whose correct operation requires prev_recos to be
# loaded (for grace-counter restoration / same_day_rerun detection).
# Listed here rather than as a class attribute so the pipeline can decide
# whether to incur the prev_recos I/O cost before instantiating / running.
_GRACE_STATE_CONSUMERS = {"sell_grace"}


def _needs_grace_state(triggers) -> bool:
    return any(getattr(t, "name", None) in _GRACE_STATE_CONSUMERS
               for t in triggers)


# ─────────────────────────────────────────────────────────────────────────────
# Grace state restoration (legacy-parity port)
# ─────────────────────────────────────────────────────────────────────────────

def load_grace_state_from_recos(
    prev_recos: Optional[pd.DataFrame],
    today: str,
) -> Tuple[Dict[str, int], bool]:
    """Restore ``(prev_grace, same_day_rerun)`` from yesterday's recos.

    Mirrors ``daily_runner.py:608-621`` behaviour exactly:

      * Rows whose ``Action`` is ``SELL_GRACE`` or ``TRIM_GRACE`` carry the
        grace counter forward; default 1 if the column is missing.
      * ``same_day_rerun`` is True when ``prev_recos["Date"].iloc[0][:10]
        == today[:10]`` — used to freeze the grace counter on a re-run.

    Returns:
        (prev_grace dict, same_day_rerun bool)
    """
    prev_grace: Dict[str, int] = {}
    same_day_rerun = False

    if prev_recos is None or prev_recos.empty:
        return prev_grace, same_day_rerun
    if "Action" not in prev_recos.columns:
        return prev_grace, same_day_rerun

    if "Date" in prev_recos.columns:
        prev_date = str(prev_recos["Date"].iloc[0])[:10]
        same_day_rerun = (prev_date == str(today)[:10])

    grace_rows = prev_recos[prev_recos["Action"].isin(["SELL_GRACE", "TRIM_GRACE"])]
    if not grace_rows.empty and "GraceCount" in grace_rows.columns:
        for _, gr in grace_rows.iterrows():
            # Match legacy int() semantics exactly; grace rows always emit
            # a positive GraceCount so there's no NaN/0 fallback to worry about.
            prev_grace[str(gr["Ticker"])] = int(gr.get("GraceCount", 1))

    return prev_grace, same_day_rerun


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_exits(
    triggers: List,
    current: Optional[pd.DataFrame],
    holdings_store: Optional[Dict[str, dict]],
    scores_df: Optional[pd.DataFrame],
    score_map: Mapping[str, float],
    regime: str,
    today: str,
    vix: float,
    top_n: int,
    total_capital: float,
    *,
    prev_recos: Optional[pd.DataFrame] = None,
    prev_regime: str = "",
    history=None,
    # D4.2 — risk-off gate inputs (all optional; caller may pass any subset).
    vix_series: Optional[List[float]] = None,
    recent_regimes: Optional[List[str]] = None,
    portfolio_peak: Optional[float] = None,
    risk_off_assessor: Optional[RiskOffAssessor] = None,
) -> Tuple[Dict[str, ExitVerdict], Dict[str, int], bool]:
    """Run trigger stack for every held ticker.

    Parameters
    ----------
    triggers : list of trigger instances (priority-sorted, output of
        ``build_triggers``).  Empty list → no-op, returns empty verdicts.
    current : HoldingsManager.load_current() output.
    holdings_store : SimPortfolio.holdings (or equivalent dict), used to
        enrich snapshots with entry_* / peak_price fields when available.
    scores_df : today's ranked (Ticker, Score, Price) DataFrame.
    score_map : ticker → today's score (convenience).
    regime, today, vix, top_n, total_capital : environment values.
    prev_recos : yesterday's recos DataFrame, used for grace-counter
        restoration.  Pass ``None`` to skip the restoration entirely.
    prev_regime : yesterday's regime, if known (D2 regime_switch trigger).
    history : HistoryView, if caller can provide one (D2 triggers).

    Returns
    -------
    (exit_verdicts, prev_grace, same_day_rerun)
      * ``exit_verdicts`` : ticker → ExitVerdict (terminal verdicts only).
        Tickers with no terminal verdict are absent from the dict.
      * ``prev_grace``    : ticker → int (grace count from prev_recos).
        Empty when no grace-state-consumer trigger was run.  Returned so
        the caller can honour the legacy fallback (sell_grace_days=0
        means immediate sell when out-of-target).
      * ``same_day_rerun`` : bool (see ``load_grace_state_from_recos``).
    """
    prev_grace: Dict[str, int] = {}
    same_day_rerun = False

    if not triggers:
        return {}, prev_grace, same_day_rerun

    if _needs_grace_state(triggers) and prev_recos is not None:
        prev_grace, same_day_rerun = load_grace_state_from_recos(prev_recos, today)

    if current is None or current.empty:
        return {}, prev_grace, same_day_rerun

    # Build 1-based rank map from today's scores_df (dense over ranked universe).
    rank_map: Dict[str, int] = {}
    if scores_df is not None and not scores_df.empty and "Ticker" in scores_df.columns:
        for idx, ticker in enumerate(scores_df["Ticker"].tolist(), start=1):
            rank_map[str(ticker)] = idx

    h_snaps = build_holding_snapshots(
        current=current,
        holdings_store=holdings_store,
        score_map=score_map,
        rank_map=rank_map,
        today=today,
        prev_grace=prev_grace,
    )

    m_snap = MarketSnapshot(
        date=today, regime=regime, prev_regime=prev_regime,
        vix=float(vix), scores_df=scores_df if scores_df is not None else pd.DataFrame(),
        top_n=int(top_n), portfolio_value=float(total_capital),
        history=history, same_day_rerun=same_day_rerun,
    )

    # D4.2 — Evaluate risk-off gate once per day and stamp onto the snapshot.
    # Using the caller-provided assessor keeps config ownership in the
    # strategy dict (see RiskOffAssessor.from_config).  If no assessor is
    # passed, a default (threshold_count=2) is used, which is still harmless
    # because no trigger consults ``market.risk_off`` unless configured with
    # ``risk_off_only=True``.
    if risk_off_assessor is None:
        risk_off_assessor = RiskOffAssessor()
    ro_input = RiskOffInput(
        vix=float(vix),
        vix_series=list(vix_series) if vix_series else [],
        regime=regime,
        recent_regimes=list(recent_regimes) if recent_regimes else [],
        portfolio_value=float(total_capital),
        portfolio_peak=float(portfolio_peak) if portfolio_peak else 0.0,
    )
    ro_res = risk_off_assessor.assess(ro_input)
    m_snap.risk_off = ro_res.risk_off
    m_snap.risk_off_level = ro_res.level
    m_snap.risk_off_reasons = ro_res.reasons
    m_snap.vix_7d_delta = ro_res.vix_7d_delta
    m_snap.portfolio_dd_pct = ro_res.portfolio_dd_pct

    exit_verdicts: Dict[str, ExitVerdict] = {}
    for h in h_snaps:
        for t in triggers:
            if not t.is_active(regime):
                continue
            v = t.evaluate(h, m_snap)
            if v.is_terminal():
                v.trigger_name = t.name
                exit_verdicts[h.ticker] = v
                break

    return exit_verdicts, prev_grace, same_day_rerun
