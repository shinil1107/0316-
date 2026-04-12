"""Cache data integrity checker for OHLCV and VIX data."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = str(Path(__file__).parent / "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_ohlcv_staleness(
    cfg, tickers: List[str], reference_date: datetime = None,
) -> Dict[str, dict]:
    """Check how fresh each ticker's OHLCV cache is."""
    from engine_loader import engine

    if reference_date is None:
        reference_date = datetime.now()

    results = {}
    for ticker in tickers:
        df = engine.load_ohlcv_from_cache(
            cfg, ticker,
            start=reference_date - timedelta(days=30),
            end=reference_date,
        )
        if df.empty:
            results[ticker] = {"status": "MISSING", "latest_date": None, "gap_days": None}
        else:
            latest = pd.to_datetime(df["date"]).max()
            gap = (reference_date - latest).days
            status = "OK" if gap <= 3 else ("STALE" if gap <= 7 else "CRITICAL")
            results[ticker] = {
                "status": status,
                "latest_date": latest.strftime("%Y-%m-%d"),
                "gap_days": gap,
                "row_count": len(df),
            }
    return results


def check_vix_health(cfg, reference_date: datetime = None) -> dict:
    """Check VIX cache freshness and continuity."""
    from engine_loader import engine

    if reference_date is None:
        reference_date = datetime.now()

    vix_symbol = getattr(cfg, "vix_symbol", "^VIX")
    df = engine.load_ohlcv_from_cache(
        cfg, vix_symbol,
        start=reference_date - timedelta(days=60),
        end=reference_date,
    )
    if df.empty:
        return {"status": "MISSING", "latest_date": None, "latest_close": None}

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    latest = df["date"].max()
    gap = (reference_date - latest).days

    date_diffs = df["date"].diff().dt.days.dropna()
    max_gap_days = int(date_diffs.max()) if len(date_diffs) > 0 else 0
    has_gaps = max_gap_days > 4

    latest_close = float(df["close"].iloc[-1]) if "close" in df.columns else None

    status = "OK" if gap <= 3 and not has_gaps else "STALE" if gap <= 7 else "CRITICAL"

    return {
        "status": status,
        "latest_date": latest.strftime("%Y-%m-%d"),
        "gap_days": gap,
        "latest_close": latest_close,
        "max_internal_gap": max_gap_days,
        "has_continuity_gaps": has_gaps,
        "row_count": len(df),
    }


def check_cache_file_integrity(cache_root: str) -> Dict[str, dict]:
    """Check for corrupted or empty parquet files."""
    ohlcv_dir = os.path.join(cache_root, "ohlcv")
    issues = {}
    if not os.path.isdir(ohlcv_dir):
        return {"_error": {"status": "MISSING_DIR", "path": ohlcv_dir}}

    for ticker_dir in sorted(os.listdir(ohlcv_dir)):
        ticker_path = os.path.join(ohlcv_dir, ticker_dir)
        if not os.path.isdir(ticker_path):
            continue
        for fn in os.listdir(ticker_path):
            if not fn.endswith(".parquet"):
                continue
            fpath = os.path.join(ticker_path, fn)
            fsize = os.path.getsize(fpath)
            if fsize == 0:
                issues[f"{ticker_dir}/{fn}"] = {"status": "EMPTY", "size": 0}
            elif fsize < 100:
                issues[f"{ticker_dir}/{fn}"] = {"status": "SUSPICIOUS", "size": fsize}
    return issues


def run_full_health_check(config_path: str = None) -> dict:
    """Run comprehensive cache health check. Returns summary dict."""
    from engine_loader import engine

    conf = load_config(config_path)
    cfg = engine.Config()
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]

    now = datetime.now()

    tickers, _ = engine.load_sp500_tickers_ttl(cfg, ttl_days=30)
    sample_tickers = tickers[:20] + (["SPY", "^VIX"] if "SPY" not in tickers[:20] else ["^VIX"])

    ohlcv = check_ohlcv_staleness(cfg, sample_tickers, now)
    vix = check_vix_health(cfg, now)
    integrity = check_cache_file_integrity(conf["paths"]["fmp_cache_root"])

    missing = [t for t, r in ohlcv.items() if r["status"] == "MISSING"]
    stale = [t for t, r in ohlcv.items() if r["status"] in ("STALE", "CRITICAL")]

    summary = {
        "check_time": now.strftime("%Y-%m-%d %H:%M"),
        "sp500_ticker_count": len(tickers),
        "ohlcv_sampled": len(sample_tickers),
        "ohlcv_missing": missing,
        "ohlcv_stale": stale,
        "vix": vix,
        "file_integrity_issues": len(integrity),
        "overall_status": "OK",
    }

    if vix["status"] != "OK" or len(missing) > 0 or len(stale) > 5:
        summary["overall_status"] = "WARNING"
    if vix["status"] == "CRITICAL" or len(missing) > 10:
        summary["overall_status"] = "CRITICAL"

    return summary


if __name__ == "__main__":
    result = run_full_health_check()
    print(f"\n{'='*50}")
    print(f"Cache Health Check — {result['check_time']}")
    print(f"{'='*50}")
    print(f"  SP500 tickers : {result['sp500_ticker_count']}")
    print(f"  OHLCV sampled : {result['ohlcv_sampled']}")
    print(f"  Missing       : {len(result['ohlcv_missing'])} {result['ohlcv_missing'][:5]}")
    print(f"  Stale         : {len(result['ohlcv_stale'])} {result['ohlcv_stale'][:5]}")
    print(f"  VIX status    : {result['vix']['status']} (close={result['vix'].get('latest_close')}, date={result['vix'].get('latest_date')})")
    print(f"  File issues   : {result['file_integrity_issues']}")
    print(f"  Overall       : {result['overall_status']}")
