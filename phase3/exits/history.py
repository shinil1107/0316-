"""Read-only history lookup for exit triggers.

``HistoryView`` wraps the simulator's ``pack`` + engine and exposes three
time-series accessors that triggers need:

* ``price_series(ticker, lookback)`` — adjusted closes (newest last).
* ``score_series(ticker, lookback)`` — daily scores with the currently-active
  weight vector (newest last).
* ``rank_series(ticker, lookback)`` — daily 1-based rank within the scored
  universe; NaN where ticker was unscored.

All accessors are scoped to a single evaluation day ``di`` and cached in
a dict keyed by ``(ticker, lookback)``.  The cache is dropped whenever
``set_day(di)`` is called, keeping memory flat.

The design stays deliberately minimal for D1: callers pass pre-computed
weight vectors + score regime at construction time, so the view can
recompute past scores without needing to know about regime blending,
compose mode, etc.  D2 may extend this if regime-aware lookback is
needed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np


def build_history_view(
    *,
    pack: dict,
    engine: Any,
    di: int,
    tickers: List[str],
    cfg_sim: Any,
    sel: Optional[np.ndarray] = None,
    active_w_bull: Optional[np.ndarray] = None,
    active_w_side: Optional[np.ndarray] = None,
    active_w_def: Optional[np.ndarray] = None,
    score_regime: str = "SIDE",
) -> "HistoryView":
    """Convenience factory so callers don't need to remember the kwargs
    order / which fields are optional.

    All args are thin pass-throughs to the HistoryView constructor — this
    exists purely as a single named entry point both simulator.py and
    daily_runner.py can import without pulling in engine internals.
    """
    return HistoryView(
        pack=pack,
        engine=engine,
        di=int(di),
        tickers=list(tickers),
        cfg_sim=cfg_sim,
        sel=sel,
        active_w_bull=active_w_bull,
        active_w_side=active_w_side,
        active_w_def=active_w_def,
        score_regime=score_regime,
    )


class HistoryView:
    """Per-day, read-only view over past scores / ranks / prices.

    Constructed fresh every simulation step (cheap — just holds references),
    so the ``@property di`` value uniquely scopes all cached series.
    """

    def __init__(
        self,
        pack: dict,
        engine,
        di: int,
        tickers: List[str],
        cfg_sim,
        sel=None,
        active_w_bull=None,
        active_w_side=None,
        active_w_def=None,
        score_regime: str = "SIDE",
    ):
        self.pack = pack
        self.engine = engine
        self.di = int(di)
        self.tickers = tickers
        self._ticker_idx = {t: i for i, t in enumerate(tickers)}
        self.cfg_sim = cfg_sim
        self.sel = sel
        self.active_w_bull = active_w_bull
        self.active_w_side = active_w_side
        self.active_w_def = active_w_def
        self.score_regime = score_regime

        # Caches keyed by (ticker, lookback).  Rank cache keyed by lookback only
        # since it's computed universe-wide.
        self._price_cache: Dict[Tuple[str, int], np.ndarray] = {}
        self._score_cache: Dict[Tuple[str, int], np.ndarray] = {}
        self._universe_score_cache: Dict[int, np.ndarray] = {}  # lookback → (L, N)
        self._rank_cache: Dict[Tuple[str, int], np.ndarray] = {}
        # D4.1 — ATR support: high/low bars + per-ticker ATR memo.
        self._high_cache: Dict[Tuple[str, int], np.ndarray] = {}
        self._low_cache: Dict[Tuple[str, int], np.ndarray] = {}
        self._atr_cache: Dict[Tuple[str, int], float] = {}

        close = pack.get("close", pack.get("raw_close"))
        self._close_arr = np.asarray(close, dtype=np.float64) if close is not None else None
        # High / low may be absent in unit-test packs — ATR accessors guard on None.
        high = pack.get("high", pack.get("raw_high"))
        low = pack.get("low", pack.get("raw_low"))
        self._high_arr = np.asarray(high, dtype=np.float64) if high is not None else None
        self._low_arr = np.asarray(low, dtype=np.float64) if low is not None else None
        self._T = self._close_arr.shape[0] if self._close_arr is not None else 0
        self._N = len(tickers)

    # ── Basic accessors ──────────────────────────────────────────────────────

    def _slice_bounds(self, lookback: int) -> Tuple[int, int]:
        """Return (start, end) indices, inclusive of today at end-1."""
        lb = max(1, int(lookback))
        end = self.di + 1   # one past today so slice includes today
        start = max(0, end - lb)
        return start, end

    def _idx(self, ticker: str) -> int:
        return self._ticker_idx.get(ticker, -1)

    # ── Price ────────────────────────────────────────────────────────────────

    def price_series(self, ticker: str, lookback: int) -> np.ndarray:
        """Return last ``lookback`` adj closes for ``ticker`` (newest last).

        Returns empty array if ticker not in universe or close data missing.
        """
        if self._close_arr is None:
            return np.array([], dtype=np.float64)
        ni = self._idx(ticker)
        if ni < 0:
            return np.array([], dtype=np.float64)
        key = (ticker, int(lookback))
        cached = self._price_cache.get(key)
        if cached is not None:
            return cached
        start, end = self._slice_bounds(lookback)
        arr = self._close_arr[start:end, ni].astype(np.float64, copy=False)
        self._price_cache[key] = arr
        return arr

    # ── High / Low (for ATR / volatility-adaptive triggers) ──────────────────

    def _bar_series(self, arr: Optional[np.ndarray],
                    cache: Dict[Tuple[str, int], np.ndarray],
                    ticker: str, lookback: int) -> np.ndarray:
        """Shared slice + cache helper for high/low accessors."""
        if arr is None:
            return np.array([], dtype=np.float64)
        ni = self._idx(ticker)
        if ni < 0:
            return np.array([], dtype=np.float64)
        key = (ticker, int(lookback))
        cached = cache.get(key)
        if cached is not None:
            return cached
        start, end = self._slice_bounds(lookback)
        out = arr[start:end, ni].astype(np.float64, copy=False)
        cache[key] = out
        return out

    def high_series(self, ticker: str, lookback: int) -> np.ndarray:
        """Last ``lookback`` daily highs for ``ticker`` (newest last)."""
        return self._bar_series(self._high_arr, self._high_cache, ticker, lookback)

    def low_series(self, ticker: str, lookback: int) -> np.ndarray:
        """Last ``lookback`` daily lows for ``ticker`` (newest last)."""
        return self._bar_series(self._low_arr, self._low_cache, ticker, lookback)

    def atr(self, ticker: str, window: int = 20) -> float:
        """Average True Range over last ``window`` days — Wilder-style.

        Uses the standard True Range definition::

            TR_t = max(high_t - low_t,
                       |high_t - close_{t-1}|,
                       |low_t  - close_{t-1}|)

        and returns the simple mean over the last ``window`` TR values
        (including today).  Returns ``0.0`` when high/low data is missing
        or the ticker has fewer than ``window+1`` observations — callers
        treat 0.0 as "not enough data, skip".

        Simple mean (not Wilder's RMA) picked for D4.1 first cut: easier
        to reason about in sweeps, and window=20 smooths enough that the
        difference vs. Wilder is <5% in practice.  Can switch to RMA
        later without changing the interface.
        """
        if self._high_arr is None or self._low_arr is None or self._close_arr is None:
            return 0.0
        ni = self._idx(ticker)
        if ni < 0:
            return 0.0
        w = max(1, int(window))
        key = (ticker, w)
        cached = self._atr_cache.get(key)
        if cached is not None:
            return cached

        # Need one extra bar for prev_close; window+1 bars total.
        end = self.di + 1
        start = max(0, end - (w + 1))
        if end - start < 2:
            self._atr_cache[key] = 0.0
            return 0.0

        highs = self._high_arr[start:end, ni]
        lows = self._low_arr[start:end, ni]
        closes = self._close_arr[start:end, ni]
        # Skip rows with NaN in any of the three bars.
        ok = np.isfinite(highs) & np.isfinite(lows) & np.isfinite(closes)
        if ok.sum() < 2:
            self._atr_cache[key] = 0.0
            return 0.0

        prev_close = closes[:-1]
        tr = np.maximum.reduce([
            highs[1:] - lows[1:],
            np.abs(highs[1:] - prev_close),
            np.abs(lows[1:] - prev_close),
        ])
        # Align validity mask to tr (len = end-start-1).
        tr_ok = ok[1:] & ok[:-1]
        tr_valid = tr[tr_ok]
        if tr_valid.size == 0:
            self._atr_cache[key] = 0.0
            return 0.0

        # Use up to last ``window`` valid TR values.
        atr_val = float(np.mean(tr_valid[-w:]))
        self._atr_cache[key] = atr_val
        return atr_val

    # ── Score (universe-wide, lazy per lookback) ─────────────────────────────

    def _universe_scores(self, lookback: int) -> Optional[np.ndarray]:
        """Recompute past scores for every ticker over ``lookback`` days.

        Returns ``None`` if the engine / weight vectors are unavailable
        (e.g. unit-test mode).  Shape (L, N).
        """
        if self.engine is None or self.sel is None:
            return None
        if any(w is None for w in (
                self.active_w_bull, self.active_w_side, self.active_w_def)):
            return None
        lb = int(lookback)
        cached = self._universe_score_cache.get(lb)
        if cached is not None:
            return cached

        start, end = self._slice_bounds(lb)
        L = end - start
        mat = np.full((L, self._N), np.nan, dtype=np.float64)
        for row, di_past in enumerate(range(start, end)):
            try:
                s = self.engine._score_vector_for_regime(
                    pack=self.pack, di=di_past, sel=self.sel,
                    active_w_bull=self.active_w_bull,
                    active_w_side=self.active_w_side,
                    active_w_def=self.active_w_def,
                    score_regime=self.score_regime,
                    cfg=self.cfg_sim,
                )
                mat[row, :] = np.asarray(s, dtype=np.float64)
            except Exception:
                # Leave NaN; trigger can decide how to handle missing data.
                pass
        self._universe_score_cache[lb] = mat
        return mat

    def score_series(self, ticker: str, lookback: int) -> np.ndarray:
        """Return last ``lookback`` scores for ``ticker`` (newest last)."""
        ni = self._idx(ticker)
        if ni < 0:
            return np.array([], dtype=np.float64)
        key = (ticker, int(lookback))
        cached = self._score_cache.get(key)
        if cached is not None:
            return cached
        mat = self._universe_scores(lookback)
        if mat is None:
            return np.array([], dtype=np.float64)
        arr = mat[:, ni].astype(np.float64, copy=False)
        self._score_cache[key] = arr
        return arr

    # ── Rank (per day, all tickers) ──────────────────────────────────────────

    def rank_series(self, ticker: str, lookback: int) -> np.ndarray:
        """Return last ``lookback`` 1-based ranks for ``ticker``.

        NaN where ticker was unranked (score NaN or non-positive).  Rank is
        dense over the universe each day — lower = better score.
        """
        ni = self._idx(ticker)
        if ni < 0:
            return np.array([], dtype=np.float64)
        key = (ticker, int(lookback))
        cached = self._rank_cache.get(key)
        if cached is not None:
            return cached
        mat = self._universe_scores(lookback)
        if mat is None:
            return np.array([], dtype=np.float64)
        L = mat.shape[0]
        out = np.full(L, np.nan, dtype=np.float64)
        for row in range(L):
            day_scores = mat[row, :]
            # Only rank finite positives (engine convention).
            finite = np.isfinite(day_scores) & (day_scores > 0)
            if not finite.any():
                continue
            # Dense rank desc: highest score → 1.
            order = np.argsort(-day_scores, kind="stable")
            rank_of = np.full(self._N, np.nan, dtype=np.float64)
            r = 1
            for oi in order:
                if not finite[oi]:
                    continue
                rank_of[oi] = r
                r += 1
            out[row] = rank_of[ni]
        self._rank_cache[key] = out
        return out
