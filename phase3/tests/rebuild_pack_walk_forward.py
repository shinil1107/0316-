"""Rebuild the walk-forward training pack (2011-01-03 → 2026-02-27).

Prerequisite for T5 Phase A. Extends the OOS pack from 7-year window
(2017-2024-05 train / 2024-06+ OOS) to **14-year full window** to enable
6-fold walk-forward evaluation including 2 pre-train OOS folds (F0a, F0b).

Data availability was verified on 2026-03-28:
  • OHLCV:  100% (1167/1167 historical S&P 500, chunks back to 2006)
  • Marketcap reconstructed: 503 tickers, data back to 1997-01-02
  • Financial annual:  ~508 tickers, back to 1985
  • Financial quarterly: ~508 tickers, back to 2001
  • Historical S&P 500 constituent events: 1957~, 1513 events

See `phase3/docs/t5_walk_forward_plan.md` v2 §3 for fold design.

Usage
-----
    python3 -u phase3/tests/rebuild_pack_walk_forward.py
    python3 -u phase3/tests/rebuild_pack_walk_forward.py --force

Output
------
- `{cfg.save_dir}/precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz`
- `phase3/docs/t5_pack_rebuild_log_<stamp>.json`
"""
from __future__ import annotations

# macOS: suppress Tk + fork safety popup when invoked from launcher UI
import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402


# ─── Fixed walk-forward pack window ──────────────────────────────────
PACK_START = datetime(2011, 1, 3)     # first trading day of 2011
PACK_END   = datetime(2026, 2, 27)    # last date covered by marketcap_reconstructed / financials

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")


def rebuild_pack(force: bool = False) -> Dict[str, Any]:
    """Build / load the 14-year walk-forward pack.

    Returns
    -------
    dict with keys:
      - pack_path      : absolute path to the .npz file
      - already_exists : bool (True if pack was already cached)
      - rebuilt        : bool (True if we actually ran prepare_inputs)
      - n_tickers, n_dates : int
      - elapsed_sec    : float
      - log_path       : path to rebuild log JSON
    """
    print("=" * 72)
    print("  T5 Walk-Forward — Pack Rebuild")
    print("=" * 72)
    print(f"  start_panel_date : {PACK_START.date()}")
    print(f"  end_date         : {PACK_END.date()}")
    print(f"  force rebuild    : {force}")
    print("=" * 72)

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)

    save_dir = conf["paths"]["output_dir"]
    fmp_root = conf["paths"]["fmp_cache_root"]

    cfg = engine.Config()
    cfg.start_panel_date = PACK_START
    cfg.end_date         = PACK_END
    cfg.fmp_cache_root   = fmp_root
    cfg.save_dir         = save_dir
    # Historical universe handling (identical to Step C / P5 retrain)
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True

    # Expected pack path (engine naming convention)
    target_name = (
        f"{cfg.precompute_npz_prefix}_"
        f"{PACK_START.strftime('%Y-%m-%d')}_"
        f"{PACK_END.strftime('%Y-%m-%d')}.npz"
    )
    target_path = os.path.join(save_dir, target_name)
    already_exists = os.path.exists(target_path)
    print(f"  target pack      : {target_name}")
    print(f"  exists?          : {already_exists}")

    if already_exists and force:
        os.remove(target_path)
        print(f"  [force] removed existing pack")
        already_exists = False

    # Call prepare_inputs — this builds + caches pack if not present
    t0 = time.time()
    prepared = engine.prepare_inputs(cfg)
    elapsed = time.time() - t0

    pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    n_tickers = int(len(pack["tickers"]))
    n_dates   = int(len(pack["dates"]))
    rebuilt   = (not already_exists) or force

    print()
    print(f"  pack ready in    : {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  tickers          : {n_tickers}")
    print(f"  dates            : {n_dates}")
    print(f"  first date       : {str(pack['dates'][0])[:10]}")
    print(f"  last date        : {str(pack['dates'][-1])[:10]}")
    print(f"  rebuilt?         : {rebuilt}")
    print(f"  saved to         : {target_path}")

    # Write audit log
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(DOCS_DIR, f"t5_pack_rebuild_log_{stamp}.json")
    log = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_path": target_path,
        "pack_start": PACK_START.strftime("%Y-%m-%d"),
        "pack_end":   PACK_END.strftime("%Y-%m-%d"),
        "already_exists_before": already_exists and not force,
        "force": force,
        "rebuilt": rebuilt,
        "elapsed_sec": round(elapsed, 1),
        "n_tickers": n_tickers,
        "n_dates":   n_dates,
        "first_date": str(pack["dates"][0])[:10],
        "last_date":  str(pack["dates"][-1])[:10],
        "notes": (
            "Rebuilt to support 6-fold walk-forward (F0a 2012-14, F0b 2015-16, "
            "F1-F4 2019-2026). See phase3/docs/t5_walk_forward_plan.md v2."
        ),
    }
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"  audit log        : {log_path}")

    return {
        "pack_path": target_path,
        "already_exists": already_exists and not force,
        "rebuilt": rebuilt,
        "n_tickers": n_tickers,
        "n_dates": n_dates,
        "first_date": str(pack["dates"][0])[:10],
        "last_date": str(pack["dates"][-1])[:10],
        "elapsed_sec": round(elapsed, 1),
        "log_path": log_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild 14-year walk-forward training pack"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Delete cached pack (if any) and rebuild from scratch.",
    )
    args = parser.parse_args()

    try:
        rebuild_pack(force=args.force)
        return 0
    except Exception as exc:
        print(f"\n[ERROR] pack rebuild failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
