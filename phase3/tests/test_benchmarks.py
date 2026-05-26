"""Unit tests for ``phase3.benchmarks`` — the SPY/QQQ benchmark
cache used by Step E and codex comparison runs.

Tests injects a fake ``fetch_fn`` so no real FMP call is ever made.
Every test runs against a temp cache root so we cannot mutate the
operator's real ``cache_fmp_c2_1`` tree.

Key invariants pinned here:

* benchmark data is written ONLY to
  ``{root}/benchmarks/{TICKER}/`` — never to ``{root}/ohlcv/`` (which
  would risk training contamination)
* default fetch is idempotent: existing shards stay, missing ones
  are pulled
* ``refresh_latest`` re-pulls ONLY the current shard
* ``overwrite`` re-pulls every shard in range
* empty payload does not delete an existing shard (FMP transient
  outages must not destroy history)
* atomic write: a crash mid-write must not leave a torn parquet
* load_benchmark returns a canonical column order + ascending dates
* ticker normalisation rejects path traversal
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from phase3 import benchmarks as bm


def _fake_payload(start_iso: str, days: int,
                  base_price: float = 100.0) -> list:
    """Build a fake FMP /historical-price-eod/full payload. FMP returns
    newest-first, OHLCV per business day."""
    rows = []
    d = pd.Timestamp(start_iso)
    biz = pd.bdate_range(start=d, periods=days)
    for i, b in enumerate(biz):
        rows.append({
            "Date": b.strftime("%Y-%m-%d"),
            "Open": base_price + i * 0.1,
            "High": base_price + i * 0.1 + 0.5,
            "Low": base_price + i * 0.1 - 0.5,
            "Close": base_price + i * 0.1 + 0.25,
            "Volume": 1_000_000 + i,
        })
    rows.reverse()  # FMP returns newest-first
    return rows


def _make_fake_fetch(payload_factory):
    """Build a fake fetch_fn that records calls and returns the
    payload from ``payload_factory(params)``."""
    calls = []

    def fake_fetch(url, params, timeout):
        calls.append({
            "url": url, "params": dict(params), "timeout": timeout,
        })
        return payload_factory(params)
    return fake_fetch, calls


# ──────────────────────────────────────────────────────────────────────
# Path resolution
# ──────────────────────────────────────────────────────────────────────
class TestResolvePaths(unittest.TestCase):

    def test_explicit_root_wins(self):
        with TemporaryDirectory() as td:
            p = bm.resolve_paths("SPY", fmp_cache_root=td)
            self.assertEqual(p.fmp_cache_root, Path(td))
            self.assertEqual(p.benchmarks_root, Path(td) / "benchmarks")
            self.assertEqual(p.ticker_dir, Path(td) / "benchmarks" / "SPY")

    def test_env_var_used_when_no_explicit(self):
        with TemporaryDirectory() as td:
            with patch.dict(os.environ,
                            {"FMP_CACHE_ROOT": td}, clear=False):
                p = bm.resolve_paths("QQQ")
                self.assertEqual(p.fmp_cache_root, Path(td))

    def test_separate_from_main_ohlcv(self):
        """The benchmark dir must NOT collide with the main ohlcv
        tree. Future maintenance scripts that list
        ``{root}/ohlcv/*`` must not see SPY/QQQ."""
        with TemporaryDirectory() as td:
            p = bm.resolve_paths("SPY", fmp_cache_root=td)
            self.assertNotIn("ohlcv", p.benchmarks_root.parts)
            self.assertIn("benchmarks", p.ticker_dir.parts)

    def test_ticker_uppercased(self):
        with TemporaryDirectory() as td:
            p = bm.resolve_paths("spy", fmp_cache_root=td)
            self.assertEqual(p.ticker_dir.name, "SPY")

    def test_unsafe_ticker_rejected(self):
        for bad in ("../etc", "SP/Y", "SPY ", " SPY", "..", ""):
            with self.subTest(ticker=bad):
                with TemporaryDirectory() as td:
                    if bad.strip() and not any(c in bad for c in (os.sep, "..")):
                        # ``" SPY"`` / ``"SPY "`` strip cleanly — skip
                        # those (they're not actually unsafe).
                        continue
                    with self.assertRaises((ValueError, RuntimeError)):
                        bm.resolve_paths(bad, fmp_cache_root=td)


# ──────────────────────────────────────────────────────────────────────
# API-key resolution
# ──────────────────────────────────────────────────────────────────────
class TestApiKeyResolution(unittest.TestCase):

    def test_missing_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                bm._resolve_api_key(env={})
            self.assertIn("FMP_API_KEY", str(ctx.exception))

    def test_explicit_env_used(self):
        self.assertEqual(
            bm._resolve_api_key(env={"FMP_API_KEY": "abc123"}),
            "abc123")

    def test_cache_load_does_not_require_key(self):
        """If every shard is already on disk and overwrite=False, we
        should never touch FMP. The API key resolution must therefore
        be deferred — the call must succeed even with no key set."""
        with TemporaryDirectory() as td:
            # Pre-seed a shard so the plan would say "skip".
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(_fake_payload("2020-01-01", 5))
            df.to_parquet(paths.shard_path(2020, 2025), index=False)

            with patch.dict(os.environ, {}, clear=True):
                # No FMP_API_KEY — must not raise.
                r = bm.fetch_benchmark(
                    "SPY", start="2020-01-01", end="2024-12-31",
                    fmp_cache_root=td, refresh_latest=False,
                    fetch_fn=lambda *a, **kw: self.fail(
                        "fetch must not be called"))
                self.assertEqual(r.chunks_fetched, 0)
                self.assertEqual(r.chunks_skipped, 1)


# ──────────────────────────────────────────────────────────────────────
# Chunk planning
# ──────────────────────────────────────────────────────────────────────
class TestChunkPlanning(unittest.TestCase):

    def _paths(self, td: str) -> bm.BenchmarkPaths:
        return bm.resolve_paths("SPY", fmp_cache_root=td)

    def test_initial_backfill_covers_full_range(self):
        with TemporaryDirectory() as td:
            paths = self._paths(td)
            plans = bm._plan_chunks(
                paths=paths,
                start=datetime(2010, 1, 1),
                end=datetime(2024, 6, 1),
                chunk_years=5, overwrite=False, refresh_latest=False,
            )
            chunk_names = [p.shard_path.name for p in plans]
            self.assertEqual(chunk_names, [
                "2010_2015.parquet",
                "2015_2020.parquet",
                "2020_2025.parquet",
            ])
            for p in plans:
                self.assertFalse(p.will_skip,
                                 f"missing shard must be fetched: {p}")

    def test_existing_shards_skipped(self):
        with TemporaryDirectory() as td:
            paths = self._paths(td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            # Pre-create the 2010_2015 shard.
            pd.DataFrame({"date": [], "open": [], "high": [], "low": [],
                          "close": [], "volume": []}
                         ).to_parquet(paths.shard_path(2010, 2015),
                                      index=False)
            plans = bm._plan_chunks(
                paths=paths,
                start=datetime(2010, 1, 1),
                end=datetime(2024, 1, 1),
                chunk_years=5, overwrite=False, refresh_latest=False,
            )
            skipped = {p.shard_path.name: p.will_skip for p in plans}
            self.assertTrue(skipped["2010_2015.parquet"])
            self.assertFalse(skipped["2015_2020.parquet"])
            self.assertFalse(skipped["2020_2025.parquet"])

    def test_overwrite_forces_all(self):
        with TemporaryDirectory() as td:
            paths = self._paths(td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"date": []}).to_parquet(
                paths.shard_path(2010, 2015), index=False)
            plans = bm._plan_chunks(
                paths=paths,
                start=datetime(2010, 1, 1),
                end=datetime(2024, 1, 1),
                chunk_years=5, overwrite=True, refresh_latest=False,
            )
            for p in plans:
                self.assertFalse(p.will_skip, p)

    def test_refresh_latest_only_touches_current(self):
        with TemporaryDirectory() as td:
            paths = self._paths(td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            for a, b in ((2010, 2015), (2015, 2020), (2020, 2025)):
                pd.DataFrame({"date": []}).to_parquet(
                    paths.shard_path(a, b), index=False)
            plans = bm._plan_chunks(
                paths=paths,
                start=datetime(2010, 1, 1),
                end=datetime(2022, 6, 1),
                chunk_years=5, overwrite=False, refresh_latest=True,
            )
            by_name = {p.shard_path.name: p for p in plans}
            self.assertTrue(by_name["2010_2015.parquet"].will_skip)
            self.assertTrue(by_name["2015_2020.parquet"].will_skip)
            self.assertFalse(by_name["2020_2025.parquet"].will_skip,
                             "current shard must be re-fetched")

    def test_end_before_start_raises(self):
        with TemporaryDirectory() as td:
            paths = self._paths(td)
            with self.assertRaises(ValueError):
                bm._plan_chunks(
                    paths=paths,
                    start=datetime(2020, 1, 1),
                    end=datetime(2019, 1, 1),
                    chunk_years=5, overwrite=False, refresh_latest=False,
                )


# ──────────────────────────────────────────────────────────────────────
# fetch_benchmark — end-to-end with fake fetch_fn
# ──────────────────────────────────────────────────────────────────────
class TestFetchBenchmark(unittest.TestCase):

    def test_full_backfill_writes_to_benchmarks_dir(self):
        with TemporaryDirectory() as td:
            fake, calls = _make_fake_fetch(lambda p: _fake_payload(
                p["from"], 252))
            r = bm.fetch_benchmark(
                "SPY", start="2020-01-01", end="2024-06-30",
                fmp_cache_root=td, chunk_years=5,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            self.assertEqual(r.chunks_fetched, 1)  # 2020-2025 only
            self.assertEqual(r.chunks_skipped, 0)
            written = r.paths.ticker_dir.glob("*.parquet")
            paths = sorted(str(p) for p in written)
            self.assertEqual(len(paths), 1)
            self.assertIn("benchmarks/SPY/2020_2025.parquet",
                          paths[0].replace(os.sep, "/"))
            # Must NOT have written to {root}/ohlcv at all.
            ohlcv = Path(td) / "ohlcv"
            self.assertFalse(ohlcv.exists())

    def test_refresh_latest_rewrites_only_current_shard(self):
        """When ALL historical shards exist, refresh_latest must only
        re-pull the current shard. Missing shards in the middle WILL
        still be pulled (that's the correct backfill semantics) — the
        ``test_refresh_latest_still_backfills_missing`` test below
        pins that behaviour separately."""
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            # Seed ALL historical shards (2010-2025) so the only
            # missing/forced shard is the current one (2020-2025).
            for a, b, close_sentinel in (
                (2010, 2015, 99.25),
                (2015, 2020, 199.25),
            ):
                pd.DataFrame({
                    "date": pd.to_datetime([f"{a}-06-01"]),
                    "open": [close_sentinel - 1],
                    "high": [close_sentinel + 1],
                    "low": [close_sentinel - 2],
                    "close": [close_sentinel],
                    "volume": [1.0],
                }).to_parquet(paths.shard_path(a, b), index=False)

            fake, calls = _make_fake_fetch(lambda p: _fake_payload(
                p["from"], 200, base_price=300.0))
            r = bm.fetch_benchmark(
                "SPY", start="2010-01-01", end="2024-06-30",
                fmp_cache_root=td, chunk_years=5,
                refresh_latest=True,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            # Only 2020-2025 should have been written.
            self.assertEqual(r.chunks_fetched, 1)
            self.assertEqual(r.chunks_skipped, 2)
            # Sentinel still intact:
            old = pd.read_parquet(paths.shard_path(2010, 2015))
            self.assertEqual(len(old), 1)
            self.assertAlmostEqual(float(old.iloc[0]["close"]), 99.25)
            # FMP was called for exactly the latest shard's year range:
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["params"]["symbol"], "SPY")
            self.assertTrue(calls[0]["params"]["from"].startswith("2020"))

    def test_refresh_latest_still_backfills_missing(self):
        """``refresh_latest=True`` must NOT silently leave gaps. If
        intermediate shards are missing, they are still pulled (so a
        partial cache always converges to a complete one)."""
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            # Only seed the very oldest shard.
            pd.DataFrame({
                "date": pd.to_datetime(["2010-06-01"]),
                "open": [1], "high": [1], "low": [1],
                "close": [1], "volume": [1],
            }).to_parquet(paths.shard_path(2010, 2015), index=False)
            fake, calls = _make_fake_fetch(lambda p: _fake_payload(
                p["from"], 200))
            r = bm.fetch_benchmark(
                "SPY", start="2010-01-01", end="2024-06-30",
                fmp_cache_root=td, chunk_years=5,
                refresh_latest=True,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            # 2015-2020 (missing) + 2020-2025 (latest) must be pulled.
            self.assertEqual(r.chunks_fetched, 2)
            self.assertEqual(r.chunks_skipped, 1)
            fetched_starts = sorted(
                c["params"]["from"][:4] for c in calls)
            self.assertEqual(fetched_starts, ["2015", "2020"])

    def test_overwrite_refetches_everything(self):
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            for a, b in ((2015, 2020), (2020, 2025)):
                pd.DataFrame({
                    "date": pd.to_datetime(["2015-06-01"]),
                    "open": [0], "high": [0], "low": [0],
                    "close": [0], "volume": [0],
                }).to_parquet(paths.shard_path(a, b), index=False)
            fake, calls = _make_fake_fetch(lambda p: _fake_payload(
                p["from"], 100))
            r = bm.fetch_benchmark(
                "SPY", start="2015-01-01", end="2024-06-30",
                fmp_cache_root=td, chunk_years=5,
                overwrite=True,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            self.assertEqual(r.chunks_fetched, 2)
            self.assertEqual(r.chunks_skipped, 0)
            # Every recorded call ended up overwriting a shard.
            self.assertEqual(len(calls), 2)

    def test_empty_payload_does_not_delete_existing_shard(self):
        """FMP transient outages return []; we must not corrupt cache."""
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            sentinel = pd.DataFrame({
                "date": pd.to_datetime(["2020-03-15"]),
                "open": [50.0], "high": [50.5], "low": [49.5],
                "close": [50.25], "volume": [1.0],
            })
            sentinel.to_parquet(paths.shard_path(2020, 2025),
                                index=False)
            fake, _ = _make_fake_fetch(lambda p: [])  # ← empty
            r = bm.fetch_benchmark(
                "SPY", start="2020-01-01", end="2024-06-30",
                fmp_cache_root=td, chunk_years=5,
                overwrite=True,  # would normally re-fetch & overwrite
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            survivor = pd.read_parquet(paths.shard_path(2020, 2025))
            self.assertEqual(len(survivor), 1)
            self.assertAlmostEqual(
                float(survivor.iloc[0]["close"]), 50.25)
            # And chunks_fetched stayed 0 because nothing was written.
            self.assertEqual(r.chunks_fetched, 0)

    def test_atomic_write_no_tmp_remains(self):
        with TemporaryDirectory() as td:
            fake, _ = _make_fake_fetch(lambda p: _fake_payload(
                p["from"], 50))
            r = bm.fetch_benchmark(
                "QQQ", start="2020-01-01", end="2022-06-30",
                fmp_cache_root=td, chunk_years=5,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            tmps = list(r.paths.ticker_dir.glob("*.tmp"))
            self.assertEqual(tmps, [],
                             f"tmp file leaked: {tmps}")

    def test_fmp_dict_with_error_raises(self):
        with TemporaryDirectory() as td:
            fake, _ = _make_fake_fetch(
                lambda p: {"Error": "rate limit"})
            with self.assertRaises(RuntimeError) as ctx:
                bm.fetch_benchmark(
                    "SPY", start="2020-01-01", end="2022-06-30",
                    fmp_cache_root=td, chunk_years=5,
                    fetch_fn=fake, api_key="dummy",
                    sleep_between_chunks_sec=0,
                )
            self.assertIn("rate limit", str(ctx.exception))

    def test_fmp_legacy_dict_wrapper_unwrapped(self):
        """FMP v3 wrapped payloads in {"historical": [...]}. /stable
        returns a bare list, but if FMP ever flips back we still cope."""
        with TemporaryDirectory() as td:
            payload = _fake_payload("2020-01-01", 50)
            fake, _ = _make_fake_fetch(
                lambda p: {"historical": payload})
            r = bm.fetch_benchmark(
                "SPY", start="2020-01-01", end="2022-06-30",
                fmp_cache_root=td, chunk_years=5,
                fetch_fn=fake, api_key="dummy",
                sleep_between_chunks_sec=0,
            )
            self.assertEqual(r.chunks_fetched, 1)
            self.assertGreater(r.rows_total, 0)


# ──────────────────────────────────────────────────────────────────────
# load_benchmark — read-side behaviour
# ──────────────────────────────────────────────────────────────────────
class TestLoadBenchmark(unittest.TestCase):

    def test_empty_when_dir_absent(self):
        with TemporaryDirectory() as td:
            df = bm.load_benchmark("SPY", fmp_cache_root=td)
            self.assertTrue(df.empty)
            self.assertEqual(list(df.columns), list(bm._OHLCV_COLUMNS))

    def test_concat_and_sort_ascending(self):
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            df_a = pd.DataFrame({
                "date": pd.to_datetime(["2020-01-02", "2020-01-03"]),
                "open": [1, 2], "high": [1, 2], "low": [1, 2],
                "close": [1, 2], "volume": [1, 2],
            })
            df_b = pd.DataFrame({
                "date": pd.to_datetime(["2010-01-04", "2010-01-05"]),
                "open": [10, 11], "high": [10, 11], "low": [10, 11],
                "close": [10, 11], "volume": [10, 11],
            })
            df_a.to_parquet(paths.shard_path(2020, 2025), index=False)
            df_b.to_parquet(paths.shard_path(2010, 2015), index=False)
            df = bm.load_benchmark("SPY", fmp_cache_root=td)
            self.assertEqual(len(df), 4)
            self.assertEqual(
                list(df["date"].dt.strftime("%Y-%m-%d")),
                ["2010-01-04", "2010-01-05",
                 "2020-01-02", "2020-01-03"])

    def test_dedup_on_overlap(self):
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame({
                "date": pd.to_datetime(["2020-01-02", "2020-01-02"]),
                "open": [1, 99], "high": [1, 99], "low": [1, 99],
                "close": [1, 99], "volume": [1, 99],
            })
            df.to_parquet(paths.shard_path(2020, 2025), index=False)
            out = bm.load_benchmark("SPY", fmp_cache_root=td)
            self.assertEqual(len(out), 1)
            # ``keep='last'`` means later row wins.
            self.assertAlmostEqual(float(out.iloc[0]["close"]), 99.0)

    def test_date_range_filter(self):
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            dates = pd.to_datetime([
                "2020-01-02", "2020-01-03", "2020-01-06"])
            df = pd.DataFrame({
                "date": dates,
                "open": [1, 2, 3], "high": [1, 2, 3],
                "low": [1, 2, 3], "close": [1, 2, 3],
                "volume": [1, 2, 3],
            })
            df.to_parquet(paths.shard_path(2020, 2025), index=False)
            out = bm.load_benchmark(
                "SPY", fmp_cache_root=td,
                start="2020-01-03", end="2020-01-05")
            self.assertEqual(len(out), 1)
            self.assertEqual(
                out.iloc[0]["date"].strftime("%Y-%m-%d"),
                "2020-01-03")


# ──────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────
class TestBenchmarkSummary(unittest.TestCase):

    def test_summary_empty_cache(self):
        with TemporaryDirectory() as td:
            s = bm.benchmark_summary("QQQ", fmp_cache_root=td)
            self.assertEqual(s["ticker"], "QQQ")
            self.assertEqual(s["rows"], 0)
            self.assertIsNone(s["date_min"])
            self.assertEqual(s["shards"], [])

    def test_summary_populated(self):
        with TemporaryDirectory() as td:
            paths = bm.resolve_paths("SPY", fmp_cache_root=td)
            paths.ticker_dir.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame({
                "date": pd.to_datetime(["2010-06-01", "2024-12-31"]),
                "open": [1, 2], "high": [1, 2], "low": [1, 2],
                "close": [1, 2], "volume": [1, 2],
            })
            df.to_parquet(paths.shard_path(2010, 2015), index=False)
            s = bm.benchmark_summary("SPY", fmp_cache_root=td)
            self.assertEqual(s["rows"], 2)
            self.assertEqual(s["date_min"], "2010-06-01")
            self.assertEqual(s["date_max"], "2024-12-31")
            self.assertEqual(s["shards"], ["2010_2015.parquet"])


# ──────────────────────────────────────────────────────────────────────
# CLI smoke
# ──────────────────────────────────────────────────────────────────────
class TestCli(unittest.TestCase):

    def test_show_subcommand_runs(self):
        with TemporaryDirectory() as td:
            parser = bm._build_parser()
            args = parser.parse_args([
                "show", "SPY", "--cache-root", td])
            self.assertEqual(args.func(args), 0)

    def test_fetch_requires_tickers(self):
        parser = bm._build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["fetch"])

    def test_fetch_parses_flags(self):
        parser = bm._build_parser()
        args = parser.parse_args([
            "fetch", "SPY", "QQQ",
            "--from", "2010-01-01",
            "--refresh-latest",
            "--chunk-years", "5",
            "--cache-root", "/tmp/whatever",
        ])
        self.assertEqual(args.tickers, ["SPY", "QQQ"])
        self.assertEqual(args.from_date, "2010-01-01")
        self.assertTrue(args.refresh_latest)
        self.assertEqual(args.chunk_years, 5)
        self.assertEqual(args.cache_root, "/tmp/whatever")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
