from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


def _unique_failed_tickers(df_timing: Any) -> List[str]:
    if df_timing is None or getattr(df_timing, "empty", True):
        return []
    if "Ticker" not in df_timing.columns:
        return []
    failed = df_timing.loc[df_timing.get("Status", "") != "OK", "Ticker"].dropna().astype(str)
    out = sorted(set(s.strip().upper() for s in failed.tolist() if s and s.strip()))
    return out


def try_cache_fallback_for_failed_tickers(
    ctx: Any,
    cfg: Any,
    df_timing: Any,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """
    Attempt OHLCV cache recovery for tickers that failed during panel build.
    This keeps runtime lightweight: one retry pass only.
    """
    enabled = bool(getattr(cfg, "enable_panel_cache_fallback_download", True))
    if not enabled:
        return {"enabled": False, "requested": 0, "success": 0, "failed": 0, "tickers": []}

    symbols = _unique_failed_tickers(df_timing)
    if not symbols:
        return {"enabled": True, "requested": 0, "success": 0, "failed": 0, "tickers": []}

    success = 0
    failed = 0
    probe = bool(getattr(cfg, "cache_download_probe", False))
    for sym in symbols:
        try:
            if hasattr(ctx, "ensure_symbol_cached"):
                ctx.ensure_symbol_cached(cfg, sym, start=start, end=end, probe=probe)
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return {
        "enabled": True,
        "requested": len(symbols),
        "success": int(success),
        "failed": int(failed),
        "tickers": symbols,
    }

