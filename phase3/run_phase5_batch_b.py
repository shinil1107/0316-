"""Phase B — overnight batch orchestrator (Batches 1 / 2 / 3).

Runs sequential GA retrains that explore the deployment-penalty design
space, leaving the rest of the Phase-5 recipe (patched formula, BULL
biases, stability seeds, population sizes) identical to
``run_phase5_retrain.py``.

Design
------
Batch 1 — **scalar profile sweep** on the base window
          (2012-01-01 → 2024-05-31):

    consv  w_turnover=0.40  w_cost=0.25  (≈1.33× T1b; upper bound)
    prop   w_turnover=0.15  w_cost=0.10  (≈0.50× T1b; Option-A mimic)
    aggr   w_turnover=0.05  w_cost=0.03  (≈0.17× T1b; near baseline)

Batch 2 — **scalar window sweep** on the PROP profile:

    win_base  2012-01-01 → 2024-05-31   seed B  (seed-variance of #2)
    win_fwd   2013-01-01 → 2025-05-31   seed B  (+1y forward-shift)
    win_back  2011-01-01 → 2023-05-31   seed B  (−1y backward-shift)

Batch 3 — **Phase B2 regime-conditional penalties** (Option 3a engine
          modification, 2026-04-24). Same base window + seed; varies the
          per-regime (BULL / SIDE / DEFENSIVE) ``w_turnover`` and
          ``w_cost`` fields introduced in the engine Cell 0:

    A-axis (BULL concentration tier):
       mild         w_to = (0.10, 0.30, 0.40)   w_co = (0.05, 0.20, 0.25)
       balanced     w_to = (0.15, 0.25, 0.35)   w_co = (0.10, 0.15, 0.20)
       deep         w_to = (0.00, 0.40, 0.50)   w_co = (0.00, 0.25, 0.30)

    B-axis (protection direction):
       bull_free    w_to = (0.00, 0.30, 0.40)   w_co = (0.00, 0.20, 0.25)
       def_heavy    w_to = (0.05, 0.15, 0.60)   w_co = (0.02, 0.10, 0.35)
       side_heavy   w_to = (0.05, 0.50, 0.30)   w_co = (0.02, 0.30, 0.15)

Runtime
-------
Each GA run takes ~2.0–2.5 h on Apple Silicon. Batch 3 is designed to
run all 6 regime-conditional presets back-to-back in one ~12 h overnight
session. Batches 1 and 2 (scalar sweep) remain available for reference
and can still be re-run individually with ``--batch 1`` / ``--batch 2``.

Why Batch 3 (regime-conditional)?
---------------------------------
Batch 1 results (evaluated 2026-04-24) showed a monotonic BULL/SIDE
trade-off: lowering scalar ``w_turnover`` recovered BULL CAGR but
collapsed SIDE-dominant folds (F2 → +1-2%). Scalar sweeps cannot break
the trade-off because one weight governs all three regimes. Phase B2
splits the penalty tier-by-tier: BULL gets a near-zero drag (allowing
the momentum concentration Baseline_V2 exploits) while SIDE/DEFENSIVE
retain strong churn suppression.

Usage
-----
    # Full run (all batches back-to-back)
    python3 -u phase3/run_phase5_batch_b.py

    # Individual batches
    python3 -u phase3/run_phase5_batch_b.py --batch 1   # scalar profile sweep
    python3 -u phase3/run_phase5_batch_b.py --batch 2   # scalar window sweep
    python3 -u phase3/run_phase5_batch_b.py --batch 3   # regime-conditional (Phase B2)

    # Pick specific runs by id
    python3 -u phase3/run_phase5_batch_b.py --runs mild,deep
    python3 -u phase3/run_phase5_batch_b.py --runs bull_free,side_heavy

    # Enumerate configs and exit (useful from launcher UI)
    python3 -u phase3/run_phase5_batch_b.py --list

    # Tiny smoke (≈1-2 min per preset; 6 × 2 = 12 min total)
    python3 -u phase3/run_phase5_batch_b.py --dry-run

Progress log
------------
After every run, an aggregated status JSON is written to
``phase3/docs/phase_b_batch_progress_<stamp>.json`` so the user can check
overnight status without tailing stdout. On failure the orchestrator
records the traceback and continues with the next preset (fail-soft).

See ``phase3/docs/phase_b_batch_plan.md`` (Batches 1-2) and
``phase3/docs/phase_b2_regime_cond_plan.md`` (Batch 3) for the full
decision record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

from phase3.run_phase5_retrain import run_phase5_retrain  # noqa: E402


# ─── 6 preset configurations ────────────────────────────────────────
# NB: all presets keep Config defaults for BULL biases (all 6 ON) and the
#     patched GA formula (F1/F4/F11/S1). Only deployment-penalty scalars,
#     the training window, and the GA seed differ.

SEED_A = 20260428   # baseline seed (matches legacy P5_RETRAIN_T1b)
SEED_B = 20260429   # Batch-2 seed-variance run
SEED_C = 20260501   # Batch-3 Phase B2 regime-conditional


def _rc(w_to_bsd: tuple, w_co_bsd: tuple) -> Dict[str, Any]:
    """Build a Phase-B2 regime-conditional override dict.

    ``w_to_bsd`` and ``w_co_bsd`` are 3-tuples ``(BULL, SIDE, DEFENSIVE)``.
    The engine Config (notebook Cell 0) interprets any non-None value as a
    per-regime override; fields left unset inherit the scalar ``w_turnover``
    / ``w_cost``. We also set ``w_turnover`` / ``w_cost`` to the SIDE weights
    (mid-value) so any legacy diagnostic that reads the scalar still sees a
    plausible number.

    The ``enable_deployment_penalty`` flag is left to inherit True from
    PHASE5_OVERRIDES in run_phase5_retrain.py.
    """
    b, s, d = w_to_bsd
    cb, cs, cd = w_co_bsd
    return {
        # Scalar placeholder for legacy telemetry / diagnostics
        "w_turnover": float(s),
        "w_cost":     float(cs),
        # Phase B2 per-regime overrides
        "w_turnover_bull": float(b),
        "w_turnover_side": float(s),
        "w_turnover_def":  float(d),
        "w_cost_bull":     float(cb),
        "w_cost_side":     float(cs),
        "w_cost_def":      float(cd),
    }


BATCH_CONFIGS: List[Dict[str, Any]] = [
    # ──────────────── Batch 1 — profile sweep (base window) ───────────────
    {
        "id": "consv",
        "batch": 1,
        "tag": "P5B_CONSV",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_CONSV",
        "overrides": {"w_turnover": 0.40, "w_cost": 0.25},
        "intent": "Upper-bound stability test (1.33× T1b).",
    },
    {
        "id": "prop",
        "batch": 1,
        "tag": "P5B_PROP",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_PROP",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "Proposed balanced profile (0.5× T1b; Option-A mimic).",
    },
    {
        "id": "aggr",
        "batch": 1,
        "tag": "P5B_AGGR",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_A,
        "excel_prefix": "SP500_P5B_AGGR",
        "overrides": {"w_turnover": 0.05, "w_cost": 0.03},
        "intent": "Near-baseline loosening (0.17× T1b).",
    },
    # ──────────────── Batch 2 — window sweep (PROP profile) ───────────────
    {
        "id": "win_base",
        "batch": 2,
        "tag": "P5B_WIN_BASE",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_BASE",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "Seed-variance re-run of PROP on the base window.",
    },
    {
        "id": "win_fwd",
        "batch": 2,
        "tag": "P5B_WIN_FWD",
        "train_start": datetime(2013, 1, 3),
        "train_end":   datetime(2025, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_FWD",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "+1y forward-shift window (includes more 2025 BULL tape).",
    },
    {
        "id": "win_back",
        "batch": 2,
        "tag": "P5B_WIN_BACK",
        "train_start": datetime(2011, 1, 3),
        "train_end":   datetime(2023, 5, 31),
        "ga_seed": SEED_B,
        "excel_prefix": "SP500_P5B_WIN_BACK",
        "overrides": {"w_turnover": 0.15, "w_cost": 0.10},
        "intent": "−1y backward-shift window (more 2011-2013 recovery tape).",
    },
    # ────── Batch 3 — Phase B2 regime-conditional penalties (Option 3a) ──
    # A-axis: BULL-concentration tier (allowable BULL turnover descending).
    {
        "id": "mild",
        "batch": 3,
        "tag": "P5C_MILD",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_MILD",
        "overrides": _rc(w_to_bsd=(0.10, 0.30, 0.40), w_co_bsd=(0.05, 0.20, 0.25)),
        "intent": "Mild BULL relaxation; SIDE/DEF held at T1b baseline.",
    },
    {
        "id": "balanced",
        "batch": 3,
        "tag": "P5C_BALANCED",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_BALANCED",
        "overrides": _rc(w_to_bsd=(0.15, 0.25, 0.35), w_co_bsd=(0.10, 0.15, 0.20)),
        "intent": "Regime-tiered version of P5B_PROP (0.5× T1b gradient).",
    },
    {
        "id": "deep",
        "batch": 3,
        "tag": "P5C_DEEP",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_DEEP",
        "overrides": _rc(w_to_bsd=(0.00, 0.40, 0.50), w_co_bsd=(0.00, 0.25, 0.30)),
        "intent": "BULL fully free + aggressive SIDE/DEF churn suppression.",
    },
    # B-axis: protection-direction tier (where the penalty is concentrated).
    {
        "id": "bull_free",
        "batch": 3,
        "tag": "P5C_BULL_FREE",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_BULL_FREE",
        "overrides": _rc(w_to_bsd=(0.00, 0.30, 0.40), w_co_bsd=(0.00, 0.20, 0.25)),
        "intent": "BULL absolutely free × SIDE/DEF at T1b baseline (MILD w/ BULL=0).",
    },
    {
        "id": "def_heavy",
        "batch": 3,
        "tag": "P5C_DEF_HEAVY",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_DEF_HEAVY",
        "overrides": _rc(w_to_bsd=(0.05, 0.15, 0.60), w_co_bsd=(0.02, 0.10, 0.35)),
        "intent": "DEF-churn isolated as primary cost driver; SIDE relaxed.",
    },
    {
        "id": "side_heavy",
        "batch": 3,
        "tag": "P5C_SIDE_HEAVY",
        "train_start": datetime(2012, 1, 3),
        "train_end":   datetime(2024, 5, 31),
        "ga_seed": SEED_C,
        "excel_prefix": "SP500_P5C_SIDE_HEAVY",
        "overrides": _rc(w_to_bsd=(0.05, 0.50, 0.30), w_co_bsd=(0.02, 0.30, 0.15)),
        "intent": "F2 SIDE-collapse direct attack (strongest SIDE churn penalty).",
    },
]


DOCS_DIR = os.path.join(HERE, "docs")


def _find_configs(batch: Optional[int], runs: Optional[List[str]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for c in BATCH_CONFIGS:
        if batch is not None and c["batch"] != batch:
            continue
        if runs is not None and c["id"] not in runs:
            continue
        selected.append(c)
    return selected


def _fmt_overrides(ov: Dict[str, Any]) -> str:
    """Compact one-line summary of the penalty overrides in ``ov``.

    Regime-conditional dict (Batch 3) prints as
    ``w_to=(B=0.00,S=0.40,D=0.50) w_co=(B=0.00,S=0.25,D=0.30)``.
    Scalar-only (Batches 1-2) prints the original ``w_to=X w_co=Y``.
    """
    has_rc = any(
        k in ov for k in (
            "w_turnover_bull", "w_turnover_side", "w_turnover_def",
            "w_cost_bull", "w_cost_side", "w_cost_def",
        )
    )
    if has_rc:
        b = ov.get("w_turnover_bull", ov.get("w_turnover", 0.0))
        s = ov.get("w_turnover_side", ov.get("w_turnover", 0.0))
        d = ov.get("w_turnover_def",  ov.get("w_turnover", 0.0))
        cb = ov.get("w_cost_bull",    ov.get("w_cost", 0.0))
        cs = ov.get("w_cost_side",    ov.get("w_cost", 0.0))
        cd = ov.get("w_cost_def",     ov.get("w_cost", 0.0))
        return (
            f"w_to=(B={float(b):.2f},S={float(s):.2f},D={float(d):.2f}) "
            f"w_co=(B={float(cb):.2f},S={float(cs):.2f},D={float(cd):.2f})"
        )
    w_to = ov.get("w_turnover", 0.3)
    w_co = ov.get("w_cost", 0.2)
    return f"w_to={w_to} w_co={w_co}"


def _print_header(configs: List[Dict[str, Any]], dry_run: bool) -> None:
    print("=" * 78)
    print("  Phase B — overnight batch orchestrator (Batches 1 / 2 / 3)")
    print("=" * 78)
    print(f"  mode         : {'DRY-RUN (per-run ~1-2 min)' if dry_run else 'FULL (per-run ~2.0-2.5 h)'}")
    print(f"  runs queued  : {len(configs)}")
    for c in configs:
        print(
            f"    [B{c['batch']}] {c['id']:<10s} tag={c['tag']:<16s} "
            f"window={c['train_start'].date()} → {c['train_end'].date()}  "
            f"seed={c['ga_seed']}  {_fmt_overrides(c['overrides'])}"
        )
    print("=" * 78)


def _write_progress(progress_path: str, payload: Dict[str, Any]) -> None:
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(progress_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as exc:
        print(f"[batch] warn: failed to write progress file: {exc}")


def run_batch(
    batch: Optional[int] = None,
    runs: Optional[List[str]] = None,
    dry_run: bool = False,
    force_rebuild_pack: bool = False,
) -> Dict[str, Any]:
    os.makedirs(DOCS_DIR, exist_ok=True)
    configs = _find_configs(batch, runs)
    if not configs:
        print("[batch] no configs matched filter — nothing to do.")
        return {"status": "empty", "results": []}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    progress_path = os.path.join(DOCS_DIR, f"phase_b_batch_progress_{stamp}.json")

    _print_header(configs, dry_run)

    results: List[Dict[str, Any]] = []
    t_batch0 = time.time()
    progress = {
        "batch_started_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "n_queued": len(configs),
        "queue": [c["id"] for c in configs],
        "current": None,
        "completed": [],
        "failed": [],
        "results": results,
    }
    _write_progress(progress_path, progress)

    for idx, cfg in enumerate(configs, 1):
        print()
        print("#" * 78)
        print(f"#  RUN {idx}/{len(configs)}  —  {cfg['tag']}  ({cfg['intent']})")
        print("#" * 78)
        progress["current"] = {
            "id": cfg["id"],
            "tag": cfg["tag"],
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_progress(progress_path, progress)

        t0 = time.time()
        try:
            res = run_phase5_retrain(
                dry_run=dry_run,
                force_rebuild_pack=force_rebuild_pack,
                train_start=cfg["train_start"],
                train_end=cfg["train_end"],
                run_tag=cfg["tag"],
                ga_seed=cfg["ga_seed"],
                extra_overrides=cfg["overrides"],
                excel_prefix=cfg.get("excel_prefix"),
            )
            elapsed = time.time() - t0
            rec = {
                "id": cfg["id"],
                "tag": cfg["tag"],
                "batch": cfg["batch"],
                "status": "ok",
                "elapsed_sec": round(elapsed, 1),
                "elapsed_min": round(elapsed / 60.0, 1),
                "frozen_signal_path": res.get("frozen_signal_path"),
                "run_log_path":       res.get("run_log_path"),
                "signal_quality":     res.get("signal_quality"),
                "w_turnover":      cfg["overrides"].get("w_turnover"),
                "w_cost":          cfg["overrides"].get("w_cost"),
                "w_turnover_bull": cfg["overrides"].get("w_turnover_bull"),
                "w_turnover_side": cfg["overrides"].get("w_turnover_side"),
                "w_turnover_def":  cfg["overrides"].get("w_turnover_def"),
                "w_cost_bull":     cfg["overrides"].get("w_cost_bull"),
                "w_cost_side":     cfg["overrides"].get("w_cost_side"),
                "w_cost_def":      cfg["overrides"].get("w_cost_def"),
                "train_window": [
                    cfg["train_start"].strftime("%Y-%m-%d"),
                    cfg["train_end"].strftime("%Y-%m-%d"),
                ],
                "ga_seed": cfg["ga_seed"],
            }
            results.append(rec)
            progress["completed"].append(cfg["id"])
            print(f"\n[batch] {cfg['tag']} OK in {elapsed/60.0:.1f} min")
        except Exception as exc:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            print(f"\n[batch] !! {cfg['tag']} FAILED after {elapsed/60.0:.1f} min: {exc}")
            print(tb)
            rec = {
                "id": cfg["id"],
                "tag": cfg["tag"],
                "batch": cfg["batch"],
                "status": "failed",
                "elapsed_sec": round(elapsed, 1),
                "error": str(exc),
                "traceback": tb,
            }
            results.append(rec)
            progress["failed"].append(cfg["id"])
            # fail-soft: continue to next preset
        progress["current"] = None
        _write_progress(progress_path, progress)

    total_sec = time.time() - t_batch0
    progress["batch_finished_at"] = datetime.now().isoformat(timespec="seconds")
    progress["total_sec"] = round(total_sec, 1)
    progress["total_min"] = round(total_sec / 60.0, 1)
    _write_progress(progress_path, progress)

    print()
    print("=" * 78)
    print("  BATCH SUMMARY")
    print("=" * 78)
    print(f"  total elapsed   : {total_sec/60.0:.1f} min ({total_sec/3600.0:.2f} h)")
    print(f"  completed       : {len(progress['completed'])}/{len(configs)}")
    if progress["failed"]:
        print(f"  FAILED          : {progress['failed']}")
    print()
    print(f"  {'tag':<18s} {'status':<8s} {'elapsed':>8s}  frozen_signal")
    print("  " + "-" * 76)
    for r in results:
        sig = (os.path.basename(r.get("frozen_signal_path") or "")) or "—"
        print(
            f"  {r['tag']:<18s} {r['status']:<8s} "
            f"{r.get('elapsed_min', 0):>7.1f}m  {sig}"
        )
    print("=" * 78)
    print(f"[batch] progress log → {progress_path}")

    return {
        "status": "done",
        "progress_path": progress_path,
        "results": results,
        "n_completed": len(progress["completed"]),
        "n_failed": len(progress["failed"]),
        "total_min": round(total_sec / 60.0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B — overnight batch orchestrator (Batches 1 / 2 / 3)")
    parser.add_argument(
        "--batch", type=int, choices=(1, 2, 3),
        help="Run only Batch 1 (scalar profile sweep), Batch 2 (scalar window "
             "sweep), or Batch 3 (Phase B2 regime-conditional). Omit to run "
             "all batches back-to-back.",
    )
    parser.add_argument(
        "--runs", type=str,
        help="Comma-separated subset of run ids: "
             "consv,prop,aggr,win_base,win_fwd,win_back,"
             "mild,balanced,deep,bull_free,def_heavy,side_heavy",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Tiny GA per preset (1-2 min each) to validate wiring end-to-end.",
    )
    parser.add_argument(
        "--force-rebuild-pack", action="store_true",
        help="Delete existing training pack for each run before prepare_inputs.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print the 12 presets (3 batches × 3-6 runs) and exit.",
    )
    args = parser.parse_args()

    if args.list:
        for c in BATCH_CONFIGS:
            print(
                f"[B{c['batch']}] {c['id']:<10s} tag={c['tag']:<16s} "
                f"window={c['train_start'].date()} → {c['train_end'].date()}  "
                f"seed={c['ga_seed']}  {_fmt_overrides(c['overrides'])}  "
                f"| {c['intent']}"
            )
        return 0

    runs = None
    if args.runs:
        runs = [x.strip() for x in args.runs.split(",") if x.strip()]
        valid_ids = {c["id"] for c in BATCH_CONFIGS}
        bad = [r for r in runs if r not in valid_ids]
        if bad:
            print(f"[batch] unknown run ids: {bad}")
            print(f"[batch] valid: {sorted(valid_ids)}")
            return 2

    out = run_batch(
        batch=args.batch,
        runs=runs,
        dry_run=args.dry_run,
        force_rebuild_pack=args.force_rebuild_pack,
    )
    return 0 if out.get("n_failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
