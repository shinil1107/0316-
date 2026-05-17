"""
Backfill financials + reconstructed marketcap for legacy (OHLCV-only) tickers.

Usage:
    python3 backfill_legacy_financials.py [--dry-run] [--limit N]

Requires: FMP_API_KEY environment variable
"""
import argparse
import os
import sys
import time
import functools
from pathlib import Path

import pandas as pd
import numpy as np
import requests

print = functools.partial(print, flush=True)

_THIS_DIR = Path(__file__).resolve().parent
_CACHE_ROOT = None  # set in main()

FMP_BASE = "https://financialmodelingprep.com"
RATE_SLEEP = 0.12
FIN_LIMIT = 100
MCAP_LIMIT = 5000

FIN_ENDPOINTS = {
    "income_quarter":   ("/stable/income-statement",       "quarter"),
    "income_annual":    ("/stable/income-statement",       "annual"),
    "balance_quarter":  ("/stable/balance-sheet-statement", "quarter"),
    "balance_annual":   ("/stable/balance-sheet-statement", "annual"),
    "cashflow_quarter": ("/stable/cash-flow-statement",     "quarter"),
    "cashflow_annual":  ("/stable/cash-flow-statement",     "annual"),
}


def _get_api_key() -> str:
    k = os.environ.get("FMP_API_KEY", "").strip()
    if not k:
        raise RuntimeError("FMP_API_KEY environment variable not set")
    return k


def _safe_fn(t: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(t))


def _fmp_get(url: str, params: dict, timeout: int = 30):
    params = {**params, "apikey": _get_api_key()}
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


# ─── Phase 1: Discover legacy tickers ───

def find_legacy_tickers(cache_root: str) -> list:
    ohlcv_dir = os.path.join(cache_root, "ohlcv")
    fin_dir = os.path.join(cache_root, "financials")

    ohlcv_tickers = set(os.listdir(ohlcv_dir)) if os.path.isdir(ohlcv_dir) else set()
    fin_tickers = set(os.listdir(fin_dir)) if os.path.isdir(fin_dir) else set()

    legacy = sorted(ohlcv_tickers - fin_tickers)
    legacy = [t for t in legacy if not t.startswith(".")]
    return legacy


# ─── Phase 2: Download financials ───

def _has_financials(ticker: str, cache_root: str) -> bool:
    tdir = os.path.join(cache_root, "financials", _safe_fn(ticker))
    if not os.path.isdir(tdir):
        return False
    return len([f for f in os.listdir(tdir) if f.endswith(".parquet")]) >= 6


def download_financials(ticker: str, cache_root: str) -> dict:
    tdir = os.path.join(cache_root, "financials", _safe_fn(ticker))
    os.makedirs(tdir, exist_ok=True)

    results = {}
    for key, (endpoint, period) in FIN_ENDPOINTS.items():
        path = os.path.join(tdir, f"{key}.parquet")
        try:
            j = _fmp_get(
                f"{FMP_BASE}{endpoint}",
                {"symbol": ticker, "period": period, "limit": FIN_LIMIT},
            )
            df = pd.DataFrame(j if isinstance(j, list) else [])
            if not df.empty:
                if "date" not in df.columns:
                    for c in ["Date", "DATE", "filingDate", "period"]:
                        if c in df.columns:
                            df = df.rename(columns={c: "date"})
                            break
                df["date"] = pd.to_datetime(
                    df.get("date", pd.Series()), errors="coerce"
                ).dt.tz_localize(None)
                df.to_parquet(path, index=False)
                results[key] = len(df)
            else:
                results[key] = 0
            time.sleep(RATE_SLEEP)
        except Exception as e:
            results[key] = f"ERR:{e}"
            time.sleep(RATE_SLEEP)
    return results


# ─── Phase 3: Build reconstructed marketcap ───

def build_reconstructed_mcap(ticker: str, cache_root: str) -> int:
    fin_dir = os.path.join(cache_root, "financials", _safe_fn(ticker))
    inc_path = os.path.join(fin_dir, "income_quarter.parquet")

    if not os.path.exists(inc_path):
        return -1

    try:
        inc = pd.read_parquet(inc_path)
    except Exception:
        return -1

    if "date" not in inc.columns:
        for c in ["Date", "DATE"]:
            if c in inc.columns:
                inc = inc.rename(columns={c: "date"})
                break
    if "date" not in inc.columns:
        return -1

    shares_col = None
    for c in ["weightedAverageShsOutDil", "weightedAverageShsOut",
              "commonStockSharesOutstanding"]:
        if c in inc.columns:
            shares_col = c
            break
    if shares_col is None:
        bal_path = os.path.join(fin_dir, "balance_quarter.parquet")
        if os.path.exists(bal_path):
            try:
                bal = pd.read_parquet(bal_path)
                for c in ["commonStockSharesOutstanding", "totalStockholdersEquity"]:
                    if c in bal.columns:
                        if "date" not in bal.columns:
                            for dc in ["Date", "DATE"]:
                                if dc in bal.columns:
                                    bal = bal.rename(columns={dc: "date"})
                                    break
                        if "date" in bal.columns and c == "commonStockSharesOutstanding":
                            inc = bal
                            shares_col = c
                            break
            except Exception:
                pass
    if shares_col is None:
        return -2

    inc["date"] = pd.to_datetime(inc["date"], errors="coerce")
    inc = inc.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    inc[shares_col] = pd.to_numeric(inc[shares_col], errors="coerce")
    shares_ts = inc.set_index("date")[shares_col].dropna()

    if shares_ts.empty:
        return -3

    ohlcv_dir = os.path.join(cache_root, "ohlcv", _safe_fn(ticker))
    if not os.path.isdir(ohlcv_dir):
        return -4

    ohlcv_parts = []
    for fn in sorted(os.listdir(ohlcv_dir)):
        if fn.endswith(".parquet"):
            try:
                ohlcv_parts.append(pd.read_parquet(os.path.join(ohlcv_dir, fn)))
            except Exception:
                pass
    if not ohlcv_parts:
        return -4

    ohlcv = pd.concat(ohlcv_parts, ignore_index=True)
    ohlcv["date"] = pd.to_datetime(ohlcv["date"], errors="coerce")
    ohlcv = ohlcv.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    ohlcv["close"] = pd.to_numeric(ohlcv["close"], errors="coerce")

    dt_index = ohlcv.set_index("date").index
    shares_aligned = shares_ts.reindex(dt_index, method="ffill")

    mcap = ohlcv["close"].values * shares_aligned.values

    out = pd.DataFrame({
        "date": ohlcv["date"].values,
        "close": ohlcv["close"].values,
        "shares_outstanding_reconstructed": shares_aligned.values,
        "marketCap_reconstructed": mcap,
        "Ticker": ticker,
        "SharesSourcePrimary": "income_quarter",
        "PrimaryShareCol": shares_col,
    })
    out = out.dropna(subset=["marketCap_reconstructed"])

    if out.empty:
        return -5

    mcap_dir = os.path.join(cache_root, "marketcap_reconstructed")
    os.makedirs(mcap_dir, exist_ok=True)
    out.to_parquet(os.path.join(mcap_dir, f"{_safe_fn(ticker)}.parquet"), index=False)
    return len(out)


# ─── Verify integrity ───

EXPECTED_PARQUETS = sorted(FIN_ENDPOINTS.keys())  # 6 files per ticker
COVERAGE_CUTOFF = pd.Timestamp("2011-01-01")


def verify_integrity(cache_root: str) -> dict:
    """Scan all financials dirs for corrupted/incomplete parquet and coverage gaps.

    Returns a summary dict with categorised ticker lists.
    """
    fin_dir = os.path.join(cache_root, "financials")
    ohlcv_dir = os.path.join(cache_root, "ohlcv")

    all_fin_tickers = sorted(
        t for t in os.listdir(fin_dir)
        if os.path.isdir(os.path.join(fin_dir, t)) and not t.startswith(".")
    )
    all_ohlcv_tickers = set(
        t for t in os.listdir(ohlcv_dir)
        if os.path.isdir(os.path.join(ohlcv_dir, t)) and not t.startswith(".")
    ) if os.path.isdir(ohlcv_dir) else set()

    corrupted: list[tuple[str, str, str]] = []    # (ticker, file, error)
    incomplete: list[tuple[str, int]] = []         # (ticker, n_files)
    empty_rows: list[tuple[str, str]] = []         # (ticker, file)
    all_empty: list[str] = []                      # ticker dirs where every file is 0-row or missing
    short_coverage: list[tuple[str, str]] = []     # (ticker, earliest_date_str)
    ok_tickers: list[str] = []
    no_ohlcv: list[str] = []                       # have financials but no OHLCV

    print(f"\n{'='*70}")
    print("  Data Integrity Verification")
    print(f"{'='*70}")
    print(f"  Financials dirs : {len(all_fin_tickers)}")
    print(f"  OHLCV dirs      : {len(all_ohlcv_tickers)}")
    print()

    for idx, ticker in enumerate(all_fin_tickers):
        tdir = os.path.join(fin_dir, ticker)
        parquets = [f for f in os.listdir(tdir) if f.endswith(".parquet")]
        parquet_keys = sorted(f.replace(".parquet", "") for f in parquets)

        ticker_corrupted = False
        ticker_has_data = False
        ticker_empty_files = []

        if len(parquets) < 6:
            missing = set(EXPECTED_PARQUETS) - set(parquet_keys)
            incomplete.append((ticker, len(parquets)))

        for pf in parquets:
            ppath = os.path.join(tdir, pf)
            try:
                df = pd.read_parquet(ppath)
                if len(df) == 0:
                    ticker_empty_files.append(pf)
                else:
                    ticker_has_data = True
            except Exception as exc:
                corrupted.append((ticker, pf, str(exc)[:120]))
                ticker_corrupted = True

        if ticker_empty_files:
            for ef in ticker_empty_files:
                empty_rows.append((ticker, ef))

        if not ticker_has_data and not ticker_corrupted and len(parquets) > 0:
            all_empty.append(ticker)
        elif not ticker_has_data and len(parquets) == 0:
            all_empty.append(ticker)

        if not ticker_corrupted and ticker_has_data:
            inc_path = os.path.join(tdir, "income_quarter.parquet")
            if os.path.exists(inc_path):
                try:
                    inc = pd.read_parquet(inc_path)
                    if "date" in inc.columns:
                        dates = pd.to_datetime(inc["date"], errors="coerce").dropna()
                        if len(dates) > 0:
                            earliest = dates.min()
                            if earliest > COVERAGE_CUTOFF:
                                short_coverage.append((ticker, str(earliest.date())))
                except Exception:
                    pass
            ok_tickers.append(ticker)

        if ticker not in all_ohlcv_tickers:
            no_ohlcv.append(ticker)

        if (idx + 1) % 200 == 0:
            print(f"  ... scanned {idx+1}/{len(all_fin_tickers)}")

    print(f"\n{'='*70}")
    print("  VERIFICATION RESULTS")
    print(f"{'='*70}")
    print(f"  Total financials dirs scanned : {len(all_fin_tickers)}")
    print(f"  OK (has data, not corrupt)    : {len(ok_tickers)}")
    print(f"  Corrupted parquet files       : {len(corrupted)}")
    print(f"  Incomplete (<6 files)         : {len(incomplete)}")
    print(f"  All-empty (0 rows everywhere) : {len(all_empty)}")
    print(f"  Empty individual files        : {len(empty_rows)}")
    print(f"  Short coverage (>2011-01-01)  : {len(short_coverage)}")
    print(f"  No OHLCV counterpart          : {len(no_ohlcv)}")

    if corrupted:
        print(f"\n  --- CORRUPTED FILES ({len(corrupted)}) ---")
        for t, f, e in corrupted[:30]:
            print(f"    {t:10s} / {f:30s}  {e}")
        if len(corrupted) > 30:
            print(f"    ... and {len(corrupted)-30} more")

    if incomplete:
        print(f"\n  --- INCOMPLETE DIRS ({len(incomplete)}) ---")
        for t, n in incomplete[:30]:
            print(f"    {t:10s}  {n}/6 files")
        if len(incomplete) > 30:
            print(f"    ... and {len(incomplete)-30} more")

    if all_empty:
        print(f"\n  --- ALL-EMPTY (rate-limit or genuine no-data) ({len(all_empty)}) ---")
        for t in all_empty[:30]:
            print(f"    {t}")
        if len(all_empty) > 30:
            print(f"    ... and {len(all_empty)-30} more")

    if short_coverage:
        print(f"\n  --- SHORT 15Y COVERAGE ({len(short_coverage)}) ---")
        for t, d in short_coverage[:30]:
            print(f"    {t:10s}  earliest={d}")
        if len(short_coverage) > 30:
            print(f"    ... and {len(short_coverage)-30} more")

    coverage_ok = len(all_fin_tickers) - len(corrupted) - len(all_empty)
    print(f"\n  VERDICT: {coverage_ok}/{len(all_fin_tickers)} tickers have usable data "
          f"({len(all_empty)} empty, {len(corrupted)} corrupt)")
    if not corrupted:
        print("  No corruption detected — boundary kill was clean.")
    print()

    return {
        "total": len(all_fin_tickers),
        "ok": len(ok_tickers),
        "corrupted": corrupted,
        "incomplete": incomplete,
        "all_empty": all_empty,
        "empty_rows": empty_rows,
        "short_coverage": short_coverage,
        "no_ohlcv": no_ohlcv,
    }


# ─── Main ───

def main():
    parser = argparse.ArgumentParser(description="Backfill legacy ticker financials")
    parser.add_argument("--dry-run", action="store_true", help="List tickers only")
    parser.add_argument("--limit", type=int, default=0, help="Max tickers (0=all)")
    parser.add_argument("--skip-financials", action="store_true",
                        help="Skip financial download, only build mcap")
    parser.add_argument("--verify", action="store_true",
                        help="Scan all financials dirs for integrity issues and exit")
    args = parser.parse_args()

    sys.path.insert(0, str(_THIS_DIR))
    from cache_health import load_config
    conf = load_config()
    cache_root = conf["paths"]["fmp_cache_root"]

    if args.verify:
        verify_integrity(cache_root)
        return

    legacy = find_legacy_tickers(cache_root)
    print(f"\n{'='*70}")
    print(f"Legacy Financials Backfill")
    print(f"{'='*70}")
    print(f"  Cache root:     {cache_root}")
    print(f"  Legacy tickers: {len(legacy)}")
    if args.limit > 0:
        legacy = legacy[:args.limit]
        print(f"  Limited to:     {args.limit}")
    print(f"  API calls est:  {len(legacy) * 6} (financials) + {len(legacy)} (mcap check)")
    print(f"  Time est:       ~{len(legacy) * 6 * RATE_SLEEP / 60:.1f} min")
    print()

    if args.dry_run:
        print("  [DRY RUN] Tickers:")
        for i, t in enumerate(legacy):
            print(f"    {i+1:4d}. {t}")
        return

    _get_api_key()

    # Phase 1: Download financials
    fin_ok = fin_empty = fin_err = fin_skip = 0
    if not args.skip_financials:
        print(f"[Phase 1/3] Downloading financials for {len(legacy)} tickers...")
        for i, ticker in enumerate(legacy):
            if _has_financials(ticker, cache_root):
                fin_skip += 1
                continue

            results = download_financials(ticker, cache_root)
            has_data = any(isinstance(v, int) and v > 0 for v in results.values())
            has_err = any(isinstance(v, str) for v in results.values())

            if has_err:
                fin_err += 1
                status = "ERR"
            elif has_data:
                fin_ok += 1
                status = "OK "
            else:
                fin_empty += 1
                status = "---"

            inc_q = results.get("income_quarter", 0)
            bal_q = results.get("balance_quarter", 0)
            cf_q = results.get("cashflow_quarter", 0)
            detail = f"inc={inc_q} bal={bal_q} cf={cf_q}"
            if (i + 1) % 25 == 0 or i == 0 or (i + 1) == len(legacy):
                print(f"  [{i+1:4d}/{len(legacy)}] {ticker:8s} {status} {detail}")

        print(f"\n[Phase 1 Done] OK={fin_ok}  EMPTY={fin_empty}  ERR={fin_err}  SKIP={fin_skip}")
    else:
        print("[Phase 1] Skipped (--skip-financials)")

    # Phase 2: Build reconstructed marketcap
    print(f"\n[Phase 2/3] Building reconstructed marketcap...")
    mcap_ok = mcap_skip = mcap_fail = 0
    for i, ticker in enumerate(legacy):
        rows = build_reconstructed_mcap(ticker, cache_root)
        if rows > 0:
            mcap_ok += 1
            if (i + 1) % 50 == 0 or i == 0 or (i + 1) == len(legacy):
                print(f"  [{i+1:4d}/{len(legacy)}] {ticker:8s} OK  {rows} rows")
        elif rows == -1 or rows == -2 or rows == -3:
            mcap_skip += 1
        else:
            mcap_fail += 1

    print(f"\n[Phase 2 Done] OK={mcap_ok}  SKIP(no shares)={mcap_skip}  FAIL={mcap_fail}")

    # Phase 3: Summary
    print(f"\n{'='*70}")
    print(f"Final Cache Status")
    print(f"{'='*70}")
    ohlcv_count = len(os.listdir(os.path.join(cache_root, "ohlcv")))
    fin_count = len(os.listdir(os.path.join(cache_root, "financials")))
    mcap_count = len([f for f in os.listdir(os.path.join(cache_root, "marketcap"))
                      if f.endswith(".parquet")])
    mcap_r_count = len([f for f in os.listdir(
        os.path.join(cache_root, "marketcap_reconstructed")) if f.endswith(".parquet")])
    print(f"  OHLCV:                  {ohlcv_count}")
    print(f"  Financials:             {fin_count}")
    print(f"  MarketCap (snapshot):   {mcap_count}")
    print(f"  MarketCap (reconstruct):{mcap_r_count}")
    remaining_gap = ohlcv_count - fin_count
    print(f"  Remaining gap:          {remaining_gap}")
    print()


if __name__ == "__main__":
    main()
