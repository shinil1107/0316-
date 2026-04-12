"""
FMP Cache Daily Updater
=======================
Incrementally updates the local FMP cache with latest OHLCV + universe data.
Designed for both free-plan (250 calls/day, 2-day rotation) and premium use.

Usage:
    # Free plan - day 1 (tickers 0-249)
    python fmp_cache_updater.py --day 1

    # Free plan - day 2 (tickers 250+)
    python fmp_cache_updater.py --day 2

    # Premium plan - all tickers at once
    python fmp_cache_updater.py --all

    # Premium plan - full backfill for missing tickers
    python fmp_cache_updater.py --backfill

    # Dry-run (no API calls, just show what would be updated)
    python fmp_cache_updater.py --all --dry-run

    # Custom cache root
    python fmp_cache_updater.py --all --cache-root "/path/to/cache"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ── defaults ────────────────────────────────────────────────────────────────

DEFAULT_CACHE_ROOT = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1"
FMP_BASE = "https://financialmodelingprep.com"
OHLCV_ENDPOINT = "/stable/historical-price-eod/full"
SP500_LIST_ENDPOINT = "/stable/sp500-constituent"
SP500_HIST_ENDPOINT = "/api/v4/historical/sp500_constituent"

CHUNK_YEARS = 5
OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]

FREE_PLAN_DAILY_LIMIT = 250
RATE_LIMIT_SLEEP = 0.25  # seconds between API calls (free plan safe)
PREMIUM_RATE_SLEEP = 0.05


# ── helpers ─────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        print("[ERROR] FMP_API_KEY not set in environment.")
        sys.exit(1)
    return key


def safe_ticker_filename(t: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(t))


def fmp_get_json(api_key: str, url: str, params: dict, timeout: int = 30) -> Any:
    params = {**params, "apikey": api_key}
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def write_ohlcv_parquet(df: pd.DataFrame, path: str) -> None:
    df = df[[c for c in OHLCV_COLS if c in df.columns]].copy()
    dates = pa.array(pd.to_datetime(df["date"]).astype("datetime64[ns]"))
    arrays = [dates]
    names = ["date"]
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            arrays.append(pa.array(pd.to_numeric(df[c], errors="coerce").fillna(0).astype("float64")))
            names.append(c)
    table = pa.table(dict(zip(names, arrays)))
    pq.write_table(table, path)


def normalize_ohlcv_df(j: list) -> pd.DataFrame:
    """Convert FMP JSON response to standard OHLCV DataFrame."""
    df = pd.DataFrame(j)
    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLS)

    renames = {"Date": "date", "Open": "open", "High": "high",
               "Low": "low", "Close": "close", "Volume": "volume"}
    for old, new in renames.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    if "date" not in df.columns:
        for c in ["Date", "DATE"]:
            if c in df.columns:
                df = df.rename(columns={c: "date"})
                break

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")

    for c in OHLCV_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[OHLCV_COLS].copy()


# ── universe ────────────────────────────────────────────────────────────────

def load_sp500_tickers(cache_root: str) -> List[str]:
    path = os.path.join(cache_root, "universe", "sp500_tickers.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("tickers", [])


def update_sp500_tickers(api_key: str, cache_root: str, force: bool = False) -> List[str]:
    """Refresh S&P500 ticker list if stale (>1 day) or forced."""
    uni_dir = os.path.join(cache_root, "universe")
    os.makedirs(uni_dir, exist_ok=True)
    path = os.path.join(uni_dir, "sp500_tickers.json")

    if not force and os.path.exists(path):
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        if (datetime.now() - mtime) < timedelta(days=1):
            tickers = load_sp500_tickers(cache_root)
            print(f"[Universe] cached ({len(tickers)} tickers, updated {mtime:%Y-%m-%d %H:%M})")
            return tickers

    url = f"{FMP_BASE}{SP500_LIST_ENDPOINT}"
    j = fmp_get_json(api_key, url, {})
    if not isinstance(j, list) or len(j) < 100:
        print(f"[Universe] WARNING: unexpected response (len={len(j) if isinstance(j, list) else 'dict'}), using cache")
        return load_sp500_tickers(cache_root)

    tickers = sorted(set(row.get("symbol", "") for row in j if row.get("symbol")))
    data = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ttl_days": 7,
        "tickers": tickers,
        "source": url,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[Universe] refreshed: {len(tickers)} tickers")
    return tickers


# ── OHLCV delta update ──────────────────────────────────────────────────────

def get_ohlcv_last_date(cache_root: str, ticker: str) -> Optional[datetime]:
    """Find the last cached OHLCV date for a ticker."""
    tdir = os.path.join(cache_root, "ohlcv", safe_ticker_filename(ticker))
    if not os.path.isdir(tdir):
        return None
    chunks = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
    if not chunks:
        return None
    try:
        df = pd.read_parquet(os.path.join(tdir, chunks[-1]))
        return pd.Timestamp(df["date"].max()).to_pydatetime()
    except Exception:
        return None


def update_ohlcv_ticker(
    api_key: str,
    cache_root: str,
    ticker: str,
    target_date: datetime,
    full_backfill: bool = False,
) -> Dict[str, Any]:
    """
    Incrementally update OHLCV cache for one ticker.
    Returns dict with status info.
    """
    tdir = os.path.join(cache_root, "ohlcv", safe_ticker_filename(ticker))
    os.makedirs(tdir, exist_ok=True)

    last_date = get_ohlcv_last_date(cache_root, ticker)

    if full_backfill or last_date is None:
        start = datetime(2016, 1, 1)
        mode = "backfill"
    else:
        start = last_date + timedelta(days=1)
        mode = "delta"

    if start.date() > target_date.date():
        return {"ticker": ticker, "mode": "skip", "reason": "already current", "rows": 0, "api_calls": 0}

    url = f"{FMP_BASE}{OHLCV_ENDPOINT}"
    total_rows = 0
    api_calls = 0

    if full_backfill:
        y0, y1 = start.year, target_date.year
        for a in range(y0, y1 + 1, CHUNK_YEARS):
            b = min(a + CHUNK_YEARS, y1 + 1)
            fn = f"{a}_{b}.parquet"
            path = os.path.join(tdir, fn)

            from_dt = max(datetime(a, 1, 1), start)
            to_dt = min(datetime(b - 1, 12, 31), target_date)

            params = {
                "symbol": ticker,
                "from": from_dt.strftime("%Y-%m-%d"),
                "to": to_dt.strftime("%Y-%m-%d"),
            }
            j = fmp_get_json(api_key, url, params, timeout=60)
            api_calls += 1

            if isinstance(j, dict):
                return {"ticker": ticker, "mode": mode, "error": str(j)[:100], "api_calls": api_calls}

            df = normalize_ohlcv_df(j if isinstance(j, list) else [])
            if df.empty:
                continue

            write_ohlcv_parquet(df, path)
            total_rows += len(df)
    else:
        # Delta mode: find the latest existing chunk and append to it
        existing_chunks = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
        if not existing_chunks:
            return {"ticker": ticker, "mode": "error", "error": "no existing chunks for delta", "api_calls": 0}

        latest_chunk = existing_chunks[-1]
        chunk_path = os.path.join(tdir, latest_chunk)

        params = {
            "symbol": ticker,
            "from": start.strftime("%Y-%m-%d"),
            "to": target_date.strftime("%Y-%m-%d"),
        }
        j = fmp_get_json(api_key, url, params, timeout=60)
        api_calls += 1

        if isinstance(j, dict):
            return {"ticker": ticker, "mode": mode, "error": str(j)[:100], "api_calls": api_calls}

        new_df = normalize_ohlcv_df(j if isinstance(j, list) else [])
        if new_df.empty:
            return {"ticker": ticker, "mode": "skip", "reason": "no new data from API", "rows": 0, "api_calls": api_calls}

        try:
            existing_df = pd.read_parquet(chunk_path)
        except Exception:
            existing_df = pd.DataFrame(columns=OHLCV_COLS)

        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        combined = combined.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
        combined = combined[OHLCV_COLS].copy()

        write_ohlcv_parquet(combined, chunk_path)
        total_rows = len(combined) - len(existing_df)

    return {"ticker": ticker, "mode": mode, "rows": total_rows, "api_calls": api_calls}


# ── diagnostics ─────────────────────────────────────────────────────────────

def scan_cache_status(cache_root: str, tickers: List[str], target_date: datetime) -> Dict[str, Any]:
    """Scan cache and report status for given tickers."""
    missing_ohlcv = []
    stale_ohlcv = []
    current_ohlcv = []
    missing_fin = []
    missing_mcap = []

    cutoff = (target_date - timedelta(days=5)).strftime("%Y-%m-%d")

    for t in tickers:
        last = get_ohlcv_last_date(cache_root, t)
        if last is None:
            missing_ohlcv.append(t)
        elif last.strftime("%Y-%m-%d") < cutoff:
            stale_ohlcv.append((t, last.strftime("%Y-%m-%d")))
        else:
            current_ohlcv.append(t)

        fin_dir = os.path.join(cache_root, "financials", safe_ticker_filename(t))
        if not os.path.isdir(fin_dir) or len(os.listdir(fin_dir)) < 6:
            missing_fin.append(t)

        mcap_path = os.path.join(cache_root, "marketcap", f"{safe_ticker_filename(t)}.parquet")
        if not os.path.exists(mcap_path):
            missing_mcap.append(t)

    return {
        "total": len(tickers),
        "ohlcv_current": len(current_ohlcv),
        "ohlcv_stale": stale_ohlcv,
        "ohlcv_missing": missing_ohlcv,
        "fin_missing": missing_fin,
        "mcap_missing": missing_mcap,
    }


def print_status(status: Dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"Cache Status Report")
    print(f"{'='*60}")
    print(f"Total tickers:        {status['total']}")
    print(f"OHLCV current:        {status['ohlcv_current']}")
    print(f"OHLCV stale:          {len(status['ohlcv_stale'])}")
    print(f"OHLCV missing:        {len(status['ohlcv_missing'])}")
    print(f"Financials missing:   {len(status['fin_missing'])}")
    print(f"Marketcap missing:    {len(status['mcap_missing'])}")

    if status["ohlcv_missing"]:
        print(f"\n  Missing OHLCV: {status['ohlcv_missing']}")
    if status["ohlcv_stale"]:
        print(f"\n  Stale OHLCV:")
        for t, d in status["ohlcv_stale"][:20]:
            print(f"    {t}: last={d}")
    if status["fin_missing"]:
        print(f"\n  Missing financials: {status['fin_missing']}")
    if status["mcap_missing"]:
        print(f"\n  Missing marketcap: {status['mcap_missing']}")

    needs_premium = []
    if status["ohlcv_missing"]:
        needs_premium.append(f"OHLCV backfill for {status['ohlcv_missing']}")
    if status["fin_missing"]:
        needs_premium.append(f"Financials for {status['fin_missing']}")
    if status["mcap_missing"]:
        needs_premium.append(f"Marketcap for {status['mcap_missing']}")

    if needs_premium:
        print(f"\n{'─'*60}")
        print(f"⚠  Premium plan backfill needed:")
        for item in needs_premium:
            print(f"  • {item}")
        print(f"  Run: python fmp_cache_updater.py --backfill")
    print(f"{'='*60}\n")


# ── main runner ─────────────────────────────────────────────────────────────

def run_update(
    cache_root: str,
    tickers: List[str],
    target_date: datetime,
    api_key: str,
    dry_run: bool = False,
    full_backfill: bool = False,
    rate_sleep: float = RATE_LIMIT_SLEEP,
) -> Dict[str, Any]:
    """Run OHLCV update for the given ticker list."""
    results = {"updated": 0, "skipped": 0, "errors": 0, "api_calls": 0, "details": []}

    for i, ticker in enumerate(tickers):
        last = get_ohlcv_last_date(cache_root, ticker)
        needs_backfill = last is None

        if dry_run:
            if needs_backfill:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: would BACKFILL (no data)")
            elif last.date() >= target_date.date():
                print(f"  [{i+1}/{len(tickers)}] {ticker}: CURRENT ({last.date()})")
            else:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: would UPDATE ({last.date()} → {target_date.date()})")
            continue

        try:
            result = update_ohlcv_ticker(
                api_key, cache_root, ticker, target_date,
                full_backfill=full_backfill or needs_backfill,
            )
            results["api_calls"] += result.get("api_calls", 0)

            if result.get("error"):
                results["errors"] += 1
                print(f"  [{i+1}/{len(tickers)}] {ticker}: ERROR - {result['error']}")
            elif result["mode"] == "skip":
                results["skipped"] += 1
                if (i + 1) % 50 == 0:
                    print(f"  [{i+1}/{len(tickers)}] {ticker}: skip (current)")
            else:
                results["updated"] += 1
                print(f"  [{i+1}/{len(tickers)}] {ticker}: {result['mode']} +{result['rows']} rows")

            results["details"].append(result)
            time.sleep(rate_sleep)

        except Exception as e:
            results["errors"] += 1
            print(f"  [{i+1}/{len(tickers)}] {ticker}: EXCEPTION - {e}")
            results["details"].append({"ticker": ticker, "error": str(e)})

    return results


def main():
    parser = argparse.ArgumentParser(description="FMP Cache Daily Updater")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--day", type=int, choices=[1, 2],
                       help="Free plan: day 1 (tickers 0-249) or day 2 (250+)")
    group.add_argument("--all", action="store_true",
                       help="Premium plan: update all tickers at once")
    group.add_argument("--backfill", action="store_true",
                       help="Premium plan: full backfill for missing tickers only")
    group.add_argument("--status", action="store_true",
                       help="Show cache status report (no API calls)")

    parser.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT,
                        help=f"Cache root directory (default: {DEFAULT_CACHE_ROOT})")
    parser.add_argument("--target-date", default=None,
                        help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making API calls")
    parser.add_argument("--rate-sleep", type=float, default=None,
                        help="Seconds between API calls (default: 0.25 free, 0.05 premium)")

    args = parser.parse_args()
    cache_root = args.cache_root
    target_date = datetime.strptime(args.target_date, "%Y-%m-%d") if args.target_date else datetime.now()

    print(f"FMP Cache Updater")
    print(f"  Cache root:  {cache_root}")
    print(f"  Target date: {target_date.date()}")

    api_key = get_api_key()

    tickers = update_sp500_tickers(api_key, cache_root)
    if not tickers:
        print("[ERROR] No tickers loaded.")
        sys.exit(1)

    # Always include index/volatility symbols needed by the engine
    EXTRA_SYMBOLS = ["SPY", "^VIX"]
    for sym in EXTRA_SYMBOLS:
        if sym not in tickers:
            tickers.append(sym)

    if args.status:
        status = scan_cache_status(cache_root, tickers, target_date)
        print_status(status)
        return

    if args.backfill:
        status = scan_cache_status(cache_root, tickers, target_date)
        backfill_tickers = status["ohlcv_missing"]
        if not backfill_tickers:
            print("\n[Backfill] No missing tickers. Cache is complete.")
            return

        print(f"\n[Backfill] {len(backfill_tickers)} tickers need full OHLCV backfill: {backfill_tickers}")
        rate = args.rate_sleep if args.rate_sleep else PREMIUM_RATE_SLEEP

        results = run_update(
            cache_root, backfill_tickers, target_date, api_key,
            dry_run=args.dry_run, full_backfill=True, rate_sleep=rate,
        )

        print(f"\n[Backfill Complete] updated={results['updated']} errors={results['errors']} api_calls={results['api_calls']}")
        return

    if args.day:
        day = args.day
        midpoint = min(FREE_PLAN_DAILY_LIMIT, len(tickers))
        if day == 1:
            batch = tickers[:midpoint]
        else:
            batch = tickers[midpoint:]
        print(f"\n[Free Plan Day {day}] {len(batch)} tickers (indices {midpoint*(day-1)}-{midpoint*(day-1)+len(batch)-1})")
        rate = args.rate_sleep if args.rate_sleep else RATE_LIMIT_SLEEP
        # Reserve 1 call for universe refresh already used above
        max_calls = FREE_PLAN_DAILY_LIMIT - 1
    else:
        batch = tickers
        print(f"\n[Premium] All {len(batch)} tickers")
        rate = args.rate_sleep if args.rate_sleep else PREMIUM_RATE_SLEEP
        max_calls = None

    if max_calls and len(batch) > max_calls:
        print(f"  Trimming batch to {max_calls} (daily call limit minus universe refresh)")
        batch = batch[:max_calls]

    results = run_update(
        cache_root, batch, target_date, api_key,
        dry_run=args.dry_run, rate_sleep=rate,
    )

    print(f"\n{'='*60}")
    print(f"Update Complete")
    print(f"  Updated:    {results['updated']}")
    print(f"  Skipped:    {results['skipped']}")
    print(f"  Errors:     {results['errors']}")
    print(f"  API calls:  {results['api_calls']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
