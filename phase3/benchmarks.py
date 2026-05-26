"""Benchmark ETF cache for performance comparison.

Why this lives outside the main OHLCV cache
-------------------------------------------

The training universe is resolved from the S&P 500 constituent list
(``engine/data_pipeline.py::_resolve_input_ticker_universe`` →
``load_sp500_tickers_ttl`` and ``_load_historical_sp500_constituent_events``)
which is an explicit, FMP-authoritative membership list. SPY and QQQ
are ETFs, not S&P 500 members, so they would never appear in that list
even if their parquet files lived next to the constituent files.

That said, we deliberately store benchmark data in a separate
directory tree (``{fmp_cache_root}/benchmarks/{TICKER}/...``) so:

* directory listings used by maintenance scripts
  (``phase3/backfill_legacy_financials.py``,
  ``phase3/cache_health.py``) cannot accidentally feed SPY/QQQ into a
  ticker-iteration job that someone refactors later;
* the on-disk layout makes the "not training data" intent obvious to
  any future operator (or to ``rg`` greps);
* benchmark refresh cadence and chunk policy can drift from the main
  OHLCV layout without coordination.

Layout
------

``{fmp_cache_root}/benchmarks/{TICKER}/{year_a}_{year_b}.parquet``

Where ``year_a`` is the inclusive start year of the shard and
``year_b`` is the exclusive end (matches the main OHLCV chunk naming
so operators see one consistent convention). Columns:
``date, open, high, low, close, volume`` — same as the main OHLCV
shards. ``date`` is normalised to ``pd.Timestamp`` on read.

Refresh policy
--------------

``fetch(ticker, start, end, ...)`` does NOT overwrite by default. Pass
``overwrite=True`` to redo every chunk in the range, or use
``--refresh-latest`` on the CLI to only re-pull the most recent
incomplete shard (the common path: keep historical untouched, just
roll the current year forward).

Usage
-----

CLI (most common — refresh SPY's tail and fetch QQQ from scratch):

    python -m phase3.benchmarks fetch SPY QQQ --from 2010-01-01 --refresh-latest

In code (for ``step_e_spy_benchmark`` and codex comparison runs)::

    from phase3 import benchmarks
    df = benchmarks.load_benchmark("SPY", start="2010-01-01")
    df = benchmarks.load_benchmark("QQQ")  # full range
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import (
    Any, Callable, Dict, Iterable, List, Optional, Tuple, Union,
)

import pandas as pd


_LOG = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent  # 0316-/
_DEFAULT_CONFIG_PATHS: Tuple[Path, ...] = (
    _HERE / "config_real.yaml",
    _HERE / "config.yaml",
)

_FMP_BASE = "https://financialmodelingprep.com"
_FMP_EOD_ENDPOINT = "/stable/historical-price-eod/full"
_FMP_API_KEY_ENV = "FMP_API_KEY"

_OHLCV_COLUMNS: Tuple[str, ...] = (
    "date", "open", "high", "low", "close", "volume",
)
_COL_RENAMES: Dict[str, str] = {
    "Date": "date", "Open": "open", "High": "high",
    "Low": "low", "Close": "close", "Volume": "volume",
    "DATE": "date",
}

# 5-year chunk policy mirrors the main OHLCV layout in
# ``0315 windows이사.ipynb::download_ohlcv_to_cache_chunked``.
_DEFAULT_CHUNK_YEARS = 5


# ──────────────────────────────────────────────────────────────────────
# Config plumbing
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BenchmarkPaths:
    """Resolved on-disk paths for a single benchmark ticker."""
    fmp_cache_root: Path
    benchmarks_root: Path        # {cache}/benchmarks
    ticker_dir: Path             # {cache}/benchmarks/{TICKER}

    def shard_path(self, year_a: int, year_b: int) -> Path:
        return self.ticker_dir / f"{year_a}_{year_b}.parquet"


def _read_fmp_cache_root_from_yaml(
    config_paths: Iterable[Path] = _DEFAULT_CONFIG_PATHS,
) -> Optional[Path]:
    """Read ``paths.fmp_cache_root`` from ``phase3/config.yaml`` (or
    ``config_real.yaml``). Returns ``None`` if no config is parseable —
    the caller should then accept an explicit override or raise."""
    for cfg in config_paths:
        if not cfg.exists():
            continue
        try:
            import yaml  # type: ignore
            with open(cfg, "r", encoding="utf-8") as f:
                conf = yaml.safe_load(f) or {}
            root = (conf.get("paths") or {}).get("fmp_cache_root")
            if isinstance(root, str) and root.strip():
                return Path(root.strip())
        except ImportError:
            # Minimal hand-parser for the one key we need. Avoid
            # making pyyaml a hard dependency just for this script.
            try:
                in_paths = False
                with open(cfg, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("paths:"):
                            in_paths = True
                            continue
                        if in_paths and not (line.startswith(" ")
                                             or line.startswith("\t")):
                            in_paths = False
                        if in_paths and "fmp_cache_root" in line:
                            _, _, v = line.partition(":")
                            v = v.strip().strip("\"'")
                            if v:
                                return Path(v)
            except OSError:
                continue
        except (OSError, Exception):  # noqa: BLE001
            continue
    return None


def resolve_paths(
    ticker: str,
    *,
    fmp_cache_root: Optional[Union[str, Path]] = None,
) -> BenchmarkPaths:
    """Resolve cache + benchmark + per-ticker directory paths.

    Precedence for ``fmp_cache_root``:

    1. explicit ``fmp_cache_root`` arg
    2. ``$FMP_CACHE_ROOT`` env var (so tests can isolate)
    3. ``paths.fmp_cache_root`` in ``phase3/config_real.yaml`` then
       ``phase3/config.yaml`` (mirrors the engine config)

    Raises ``RuntimeError`` if none resolve.
    """
    ticker = _normalise_ticker(ticker)
    root: Optional[Path] = None
    if fmp_cache_root is not None:
        root = Path(fmp_cache_root)
    elif os.environ.get("FMP_CACHE_ROOT", "").strip():
        root = Path(os.environ["FMP_CACHE_ROOT"].strip())
    else:
        root = _read_fmp_cache_root_from_yaml()
    if root is None:
        raise RuntimeError(
            "fmp_cache_root could not be resolved: set $FMP_CACHE_ROOT, "
            "pass --cache-root, or ensure paths.fmp_cache_root exists in "
            "phase3/config.yaml")
    benchmarks_root = root / "benchmarks"
    ticker_dir = benchmarks_root / ticker
    return BenchmarkPaths(
        fmp_cache_root=root,
        benchmarks_root=benchmarks_root,
        ticker_dir=ticker_dir,
    )


def _normalise_ticker(ticker: str) -> str:
    t = str(ticker or "").strip().upper()
    if not t or any(c in t for c in (os.sep, "..", " ")):
        raise ValueError(f"refusing unsafe ticker name: {ticker!r}")
    return t


def _resolve_api_key(env: Optional[Dict[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    key = (e.get(_FMP_API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"FMP API key not found in env '{_FMP_API_KEY_ENV}'. "
            "Export it (same key used by the main OHLCV downloader) and retry.")
    return key


# ──────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────
FetchFn = Callable[[str, Dict[str, Any], int], Any]
"""Signature: ``(url, params_dict, timeout_sec) -> parsed_json``."""


def _default_fetch_fn(url: str, params: Dict[str, Any], timeout: int) -> Any:
    """Lazy ``requests.get`` wrapper. Imported lazily so unit tests
    that inject ``fetch_fn`` don't require requests in their env."""
    import requests  # type: ignore
    r = requests.get(url, params=params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(
            f"FMP HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _fetch_chunk(
    *,
    ticker: str,
    from_dt: datetime,
    to_dt: datetime,
    api_key: str,
    fetch_fn: FetchFn,
    timeout: int = 60,
) -> pd.DataFrame:
    """Fetch a single date range from FMP and return a normalised
    DataFrame with the canonical ``_OHLCV_COLUMNS``. Empty range
    (e.g. weekend-only window) returns an empty DataFrame; this is
    not an error.
    """
    params = {
        "symbol": ticker,
        "from": from_dt.strftime("%Y-%m-%d"),
        "to": to_dt.strftime("%Y-%m-%d"),
        "apikey": api_key,
    }
    payload = fetch_fn(_FMP_BASE + _FMP_EOD_ENDPOINT, params, timeout)
    if isinstance(payload, dict):
        # FMP returns ``{"historical": [...]}`` on the legacy v3
        # endpoint and ``[...]`` on the /stable endpoint. We always
        # request /stable, but be defensive in case FMP changes.
        if isinstance(payload.get("historical"), list):
            payload = payload["historical"]
        else:
            err = (payload.get("Error")
                   or payload.get("error")
                   or payload.get("message")
                   or f"keys={list(payload.keys())[:6]}")
            raise RuntimeError(
                f"FMP returned non-list for {ticker} "
                f"{from_dt.date()}~{to_dt.date()}: {err}")
    if not isinstance(payload, list):
        return pd.DataFrame(columns=list(_OHLCV_COLUMNS))
    df = pd.DataFrame(payload)
    if df.empty:
        return pd.DataFrame(columns=list(_OHLCV_COLUMNS))
    df = df.rename(columns={k: v for k, v in _COL_RENAMES.items()
                            if k in df.columns})
    for col in _OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[list(_OHLCV_COLUMNS)].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df


# ──────────────────────────────────────────────────────────────────────
# Chunk planning
# ──────────────────────────────────────────────────────────────────────
@dataclass
class _ChunkPlan:
    year_a: int             # inclusive start year (filename prefix)
    year_b: int             # exclusive end year (filename suffix)
    fetch_from: datetime    # actual API ``from``
    fetch_to: datetime      # actual API ``to``
    shard_path: Path
    will_skip: bool         # True ⇒ already on disk, not overwriting
    reason: str             # diagnostic for the panel/log


def _plan_chunks(
    *,
    paths: BenchmarkPaths,
    start: datetime,
    end: datetime,
    chunk_years: int,
    overwrite: bool,
    refresh_latest: bool,
) -> List[_ChunkPlan]:
    """Build the chunk plan for ``[start, end]``.

    Each chunk covers ``[a, a+chunk_years)`` years. Behaviour:

    * ``overwrite=True``  → every chunk in range is re-fetched
    * ``refresh_latest=True`` → only the latest chunk (current year)
      is re-fetched; older chunks stay on disk
    * default → only missing chunks are fetched (idempotent backfill)
    """
    plans: List[_ChunkPlan] = []
    if end < start:
        raise ValueError(f"end {end.date()} precedes start {start.date()}")
    y0, y1 = start.year, end.year
    step = int(chunk_years)
    # The latest shard (containing the run's ``end`` date) is the only
    # one ``refresh_latest`` touches.
    last_shard_year_a: Optional[int] = None
    a = y0
    while a <= y1:
        b = a + step
        if a <= y1 < b:
            last_shard_year_a = a
        a += step
    a = y0
    while a <= y1:
        b = a + step
        fetch_from = max(datetime(a, 1, 1), start)
        fetch_to = min(datetime(b - 1, 12, 31), end)
        path = paths.shard_path(a, b)
        exists = path.exists()
        is_latest = (a == last_shard_year_a)
        if overwrite:
            will_skip, reason = False, "overwrite=True"
        elif refresh_latest and is_latest:
            will_skip, reason = False, "refresh-latest (current shard)"
        elif exists:
            will_skip, reason = True, "shard already on disk"
        else:
            will_skip, reason = False, "missing — initial backfill"
        plans.append(_ChunkPlan(
            year_a=a, year_b=b,
            fetch_from=fetch_from, fetch_to=fetch_to,
            shard_path=path, will_skip=will_skip, reason=reason,
        ))
        a += step
    return plans


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FetchResult:
    ticker: str
    paths: BenchmarkPaths
    chunks_fetched: int
    chunks_skipped: int
    rows_total: int
    date_min: Optional[pd.Timestamp]
    date_max: Optional[pd.Timestamp]
    files_written: Tuple[Path, ...] = field(default_factory=tuple)


def fetch_benchmark(
    ticker: str,
    *,
    start: Union[str, datetime] = "2010-01-01",
    end: Optional[Union[str, datetime]] = None,
    fmp_cache_root: Optional[Union[str, Path]] = None,
    chunk_years: int = _DEFAULT_CHUNK_YEARS,
    overwrite: bool = False,
    refresh_latest: bool = False,
    fetch_fn: Optional[FetchFn] = None,
    api_key: Optional[str] = None,
    sleep_between_chunks_sec: float = 0.25,
    on_progress: Optional[Callable[[str], None]] = None,
) -> FetchResult:
    """Download ``ticker`` (e.g. ``"SPY"``, ``"QQQ"``) from FMP into the
    ``benchmarks/`` cache.

    Parameters
    ----------
    start, end
        ISO date strings or datetimes. ``end`` defaults to today.
    overwrite
        If True, re-fetch every chunk in the range even if a shard
        file is already on disk. Use this when FMP corrects history.
    refresh_latest
        If True (and ``overwrite=False``), only re-fetch the shard
        containing the run's ``end`` date. Older shards are left
        untouched. This is the daily-roll path.
    fetch_fn
        Override the HTTP layer (used by tests). Default uses
        ``requests.get`` lazily.
    api_key
        Override the API key resolution (defaults to ``$FMP_API_KEY``).
    """
    ticker = _normalise_ticker(ticker)
    paths = resolve_paths(ticker, fmp_cache_root=fmp_cache_root)
    paths.ticker_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(start, str):
        start_dt = datetime.strptime(start, "%Y-%m-%d")
    else:
        start_dt = start
    if end is None:
        end_dt = datetime.now()
    elif isinstance(end, str):
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    else:
        end_dt = end

    plans = _plan_chunks(
        paths=paths, start=start_dt, end=end_dt,
        chunk_years=chunk_years, overwrite=overwrite,
        refresh_latest=refresh_latest,
    )

    if api_key is None:
        # Only resolve when we actually need to call the API — that
        # way a pure cache-load with overwrite=False, all-shards-on-disk
        # never requires an API key.
        actual_work = any(not p.will_skip for p in plans)
        api_key = _resolve_api_key() if actual_work else ""

    fetch_fn = fetch_fn or _default_fetch_fn
    log = on_progress or (lambda msg: _LOG.info(msg))

    fetched = 0
    skipped = 0
    files_written: List[Path] = []
    for plan in plans:
        if plan.will_skip:
            log(f"[{ticker}] skip {plan.year_a}_{plan.year_b}.parquet "
                f"({plan.reason})")
            skipped += 1
            continue
        log(f"[{ticker}] fetching {plan.fetch_from.date()} → "
            f"{plan.fetch_to.date()} → {plan.shard_path.name} "
            f"({plan.reason})")
        df = _fetch_chunk(
            ticker=ticker,
            from_dt=plan.fetch_from, to_dt=plan.fetch_to,
            api_key=api_key, fetch_fn=fetch_fn,
        )
        if df.empty:
            log(f"[{ticker}]   empty payload for "
                f"{plan.fetch_from.date()}~{plan.fetch_to.date()} "
                "(weekend-only / market closure?)")
            # Do NOT delete an existing shard on an empty response —
            # FMP can transiently return [] under partial outages.
            if not plan.shard_path.exists():
                # Write nothing; subsequent runs will retry.
                continue
            skipped += 1
            continue
        # Atomic write: tmp → rename, so a SIGINT mid-write never
        # leaves a torn parquet that load_benchmark would then refuse.
        tmp = plan.shard_path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, plan.shard_path)
        files_written.append(plan.shard_path)
        fetched += 1
        if sleep_between_chunks_sec > 0:
            time.sleep(sleep_between_chunks_sec)

    # Summarise on-disk state after fetch.
    full = load_benchmark(
        ticker, fmp_cache_root=fmp_cache_root,
    )
    return FetchResult(
        ticker=ticker, paths=paths,
        chunks_fetched=fetched, chunks_skipped=skipped,
        rows_total=int(len(full)),
        date_min=(full["date"].min() if not full.empty else None),
        date_max=(full["date"].max() if not full.empty else None),
        files_written=tuple(files_written),
    )


def load_benchmark(
    ticker: str,
    *,
    start: Optional[Union[str, datetime]] = None,
    end: Optional[Union[str, datetime]] = None,
    fmp_cache_root: Optional[Union[str, Path]] = None,
) -> pd.DataFrame:
    """Read all shards for ``ticker`` from the benchmark cache and
    return a single DataFrame sorted ascending by date.

    Returns an empty DataFrame (with the canonical columns) if no
    shards are on disk. ``start`` / ``end`` are inclusive filters
    applied after concatenation."""
    ticker = _normalise_ticker(ticker)
    paths = resolve_paths(ticker, fmp_cache_root=fmp_cache_root)
    if not paths.ticker_dir.exists():
        return pd.DataFrame(columns=list(_OHLCV_COLUMNS))
    shards: List[pd.DataFrame] = []
    for p in sorted(paths.ticker_dir.iterdir()):
        if not p.name.endswith(".parquet"):
            continue
        try:
            shards.append(pd.read_parquet(p))
        except (OSError, Exception):  # noqa: BLE001
            _LOG.warning("[%s] failed to read %s — skipping", ticker, p)
            continue
    if not shards:
        return pd.DataFrame(columns=list(_OHLCV_COLUMNS))
    df = pd.concat(shards, ignore_index=True)
    df = df.rename(columns={k: v for k, v in _COL_RENAMES.items()
                            if k in df.columns})
    for col in _OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[list(_OHLCV_COLUMNS)].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if start is not None:
        s = pd.Timestamp(start)
        df = df[df["date"] >= s]
    if end is not None:
        e = pd.Timestamp(end)
        df = df[df["date"] <= e]
    return df.reset_index(drop=True)


def benchmark_summary(
    ticker: str,
    *,
    fmp_cache_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """One-line diagnostic dict for ``ticker``. Useful for the CLI's
    final report and for unit-test assertions."""
    paths = resolve_paths(ticker, fmp_cache_root=fmp_cache_root)
    df = load_benchmark(ticker, fmp_cache_root=fmp_cache_root)
    shards = (sorted(paths.ticker_dir.glob("*.parquet"))
              if paths.ticker_dir.exists() else [])
    return {
        "ticker": ticker,
        "path": str(paths.ticker_dir),
        "shards": [p.name for p in shards],
        "rows": int(len(df)),
        "date_min": (str(df["date"].min().date()) if not df.empty else None),
        "date_max": (str(df["date"].max().date()) if not df.empty else None),
    }


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def _cli_fetch(args: argparse.Namespace) -> int:
    tickers: List[str] = [_normalise_ticker(t) for t in args.tickers]
    results: List[FetchResult] = []
    for t in tickers:
        print(f"\n=== {t} ===")
        try:
            r = fetch_benchmark(
                t,
                start=args.from_date,
                end=args.end_date,
                fmp_cache_root=args.cache_root,
                chunk_years=args.chunk_years,
                overwrite=args.overwrite,
                refresh_latest=args.refresh_latest,
                sleep_between_chunks_sec=args.sleep,
                on_progress=lambda m: print(" ", m),
            )
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {type(e).__name__}: {e}")
            return 1
        results.append(r)
        print(f"  rows={r.rows_total} "
              f"first={r.date_min.date() if r.date_min is not None else '—'} "
              f"last={r.date_max.date() if r.date_max is not None else '—'} "
              f"fetched={r.chunks_fetched} skipped={r.chunks_skipped}")
    print("\nDone.")
    return 0


def _cli_show(args: argparse.Namespace) -> int:
    for t in args.tickers:
        summary = benchmark_summary(
            t, fmp_cache_root=args.cache_root)
        print(f"\n[{summary['ticker']}]")
        for k in ("path", "rows", "date_min", "date_max"):
            print(f"  {k:<10} = {summary[k]}")
        print(f"  shards     = {summary['shards']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m phase3.benchmarks",
        description=(
            "FMP benchmark cache for SPY/QQQ (and any other ETF). "
            "Data lives in {fmp_cache_root}/benchmarks/{TICKER}/ and is "
            "isolated from the S&P 500 training universe."),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fetch", help="Download / refresh benchmark data.")
    pf.add_argument("tickers", nargs="+",
                    help="Tickers, e.g. SPY QQQ")
    pf.add_argument("--from", dest="from_date", default="2010-01-01",
                    help="Start date (default: 2010-01-01).")
    pf.add_argument("--end", dest="end_date", default=None,
                    help="End date (default: today).")
    pf.add_argument("--cache-root", default=None,
                    help="Override fmp_cache_root (defaults to "
                         "phase3/config.yaml or $FMP_CACHE_ROOT).")
    pf.add_argument("--chunk-years", type=int,
                    default=_DEFAULT_CHUNK_YEARS,
                    help=f"Years per parquet shard (default: "
                         f"{_DEFAULT_CHUNK_YEARS}).")
    pf.add_argument("--overwrite", action="store_true",
                    help="Re-fetch every shard in range even if present.")
    pf.add_argument("--refresh-latest", action="store_true",
                    help="Only re-fetch the shard containing --end (the "
                         "common daily-roll path).")
    pf.add_argument("--sleep", type=float, default=0.25,
                    help="Sleep between chunk fetches (default: 0.25s).")
    pf.set_defaults(func=_cli_fetch)

    ps = sub.add_parser("show", help="Print on-disk summary.")
    ps.add_argument("tickers", nargs="+")
    ps.add_argument("--cache-root", default=None)
    ps.set_defaults(func=_cli_show)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
