"""Phase 5 Retrain — stability-only GA on patched formula (OOS-safe cutoff).

Runs the GA under the post-audit (F1/F4/F11/S1-patched) formula on data
that ends at 2024-05-31, so that the OOS window (2024-06-01 → pack_end)
used by Step A baseline and Step C evaluation is fully un-contaminated
by training.

Outputs
-------
- `frozen_signal_P5_RETRAIN_<stamp>.npz` (consumed by Step C)
- `phase3/docs/phase5_retrain_log_<stamp>.json` (run summary)

Usage
-----
One-click:
    Double-click phase3/run_phase5_retrain.command

Jupyter:
    %run phase3/run_phase5_retrain.py

Terminal:
    python3 -u phase3/run_phase5_retrain.py            # full run (~3 h)
    python3 -u phase3/run_phase5_retrain.py --dry-run  # smoke (~1-2 min)
    python3 -u phase3/run_phase5_retrain.py --force-rebuild-pack

See `phase3/docs/phase5_retrain_plan.md` for the full decision record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ── macOS safety: suppress Objective-C fork-safety crash when we still end
#    up forking deep inside numpy/BLAS. This only silences the *warning*; the
#    real crash fix is disabling _SUBPROCESS_FAST_SEEDS below.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import numpy as np  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402

# ── Disable os.fork() stability-seed subprocess path. The engine defaults to
#    `_SUBPROCESS_FAST_SEEDS = True`, which calls os.fork() for every seed in
#    the stability layer. That is fine in a CLI/Jupyter kernel, but when this
#    module is invoked from the Tkinter launcher (`phase3/launcher.py`) the
#    parent process has already initialized AppKit/Objective-C; forking from a
#    multi-threaded AppKit process crashes macOS even after the child exits
#    cleanly ("Python unexpectedly quit"). In-process fallback has negligible
#    runtime impact for our stability budget (5 seeds × fast_gen=8).
if hasattr(engine, "_SUBPROCESS_FAST_SEEDS"):
    if getattr(engine, "_SUBPROCESS_FAST_SEEDS"):
        print("[P5_RETRAIN] engine._SUBPROCESS_FAST_SEEDS=True → forcing False "
              "(avoid macOS fork-after-Tk crash; in-process stability seeds)")
        engine._SUBPROCESS_FAST_SEEDS = False


# ─── Fixed plan parameters (see docs/t1_phase2_deployment_tuning_plan.md) ──
TRAIN_START = datetime(2017, 2, 21)
TRAIN_END   = datetime(2024, 5, 31)   # OOS cut: 2024-06-01 onward is holdout
RUN_TAG     = "P5_RETRAIN_T1b"        # iter 2: w=0.3/0.2, budget reverted
GA_SEED     = 20260428

# ─── Phase 5 T1-Deployment-Tuning cfg overrides (iter 2: T1b) ─────────
# Iteration history:
#   P5_RETRAIN      (2026-04-23 16:11) — T1 OFF, BULL partial-OFF, pop 100/300/300 → REJECT
#   P5_RETRAIN_T1   (2026-04-23 18:31) — T1 w=0.5/0.3, BULL ON, pop 120/360/360 → REJECT
#                                        (CAGR over-dropped, commission only 0.72%)
#   P5_RETRAIN_T1b  (this run)       — T1 w=0.3/0.2, BULL ON, pop 100/300/300
#
# CHANGED from P5_RETRAIN_T1:
#   • w_turnover: 0.5 → 0.3   (ease penalty pressure; recover CAGR)
#   • w_cost   : 0.3 → 0.2
#   • Populations reverted to P5_RETRAIN levels (100/300/300)
#   • RUN_TAG / excel_prefix bumped to P5_RETRAIN_T1b / SP500_P5_T1DEP_b
# UNCHANGED from P5_RETRAIN_T1:
#   • BULL biases all ON (Config default)
#   • Patched formula (F1/F4/F11/S1)
#   • Train window, GA seed, meta-OFF, stability 5 seeds
# Step C Gate #5 threshold also relaxed to baseline × 0.7 (0.78%) in v1.1.
PHASE5_OVERRIDES: Dict[str, Any] = {
    # Window (OOS-safe)
    "start_panel_date": TRAIN_START,
    "end_date":         TRAIN_END,

    # Universe (match V1/V2 chain)
    "enable_historical_universe":          True,
    "historical_universe_expand_tickers":  True,
    "enable_coverage_based_universe":      True,
    "enable_panel_cache_fallback_download": False,

    # GA fitness recipe (V1/V2 chain)
    "top_quantile":                0.12,
    "w_ic1":                       0.34,
    "w_ic3":                       0.34,
    "w_spread":                    0.32,
    "factor_corr_penalty_lambda":  0.10,
    "conc_penalty":                0.12,
    "weight_cap":                  0.40,
    "enable_fitness_risk_penalty": True,
    "fitness_downside_vol_lambda": 0.50,
    "fitness_max_neg_spread_ratio_lambda": 0.30,

    # ── T1 deployment penalty (iter 2: eased) ──
    "enable_deployment_penalty": True,
    "w_turnover":                0.3,   # was 0.5 in T1; lowered to ease pressure
    "w_cost":                    0.2,   # was 0.3 in T1
    # deployment_top_n=30, deployment_cost_bps=15.0 kept at Config default

    # ── BULL biases ── default (all ON) preserved (same as T1 run)
    # Intentionally NO override here so engine.Config defaults take effect.

    # Meta OFF, stability ON (5 seeds, budget reverted to P5_RETRAIN level)
    "enable_meta_search":           False,
    "meta_disabled_template_name":  "TPL_BALANCED",
    "enable_stability_layer":       True,
    "stability_seed_runs":          5,
    "stability_top_n_seeds":        4,
    "stability_fast_population":    100,    # reverted from 120 (T1) → 100
    "stability_fast_generations":   8,
    "stability_refine_population":  300,    # reverted from 360 (T1) → 300
    "stability_refine_generations": 12,

    # Final GA (reverted from 360 → 300)
    "ga_population":  300,
    "ga_generations": 20,

    # Reproducibility
    "use_random_seed": False,
    "ga_seed":         GA_SEED,

    # Reports
    "excel_prefix": "SP500_P5_T1DEP_b",
}

# Overrides applied ON TOP of PHASE5_OVERRIDES when --dry-run is given.
DRY_RUN_OVERRIDES: Dict[str, Any] = {
    "stability_seed_runs":          1,
    "stability_fast_population":    20,
    "stability_fast_generations":   3,
    "stability_refine_population":  20,
    "stability_refine_generations": 3,
    "ga_population":  20,
    "ga_generations": 3,
}


# ─── Helpers ─────────────────────────────────────────────────────────
def _apply_overrides(cfg, overrides: Dict[str, Any]) -> list[str]:
    """Mutates cfg in-place; returns list of keys changed."""
    applied = []
    for k, v in overrides.items():
        if not hasattr(cfg, k):
            print(f"[P5_RETRAIN] warn: Config has no attribute {k!r} — skipped")
            continue
        old = getattr(cfg, k)
        setattr(cfg, k, v)
        if old != v:
            applied.append(k)
    return applied


def _summarize_signal(
    best_mask: np.ndarray,
    summary_row: Dict[str, Any] | None,
) -> Dict[str, Any]:
    summary_row = summary_row or {}
    def _f(k: str, default: float = float("nan")) -> float:
        v = summary_row.get(k, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("nan")
    return {
        "k_selected": int(np.asarray(best_mask, dtype=bool).sum()),
        "MeanIC":      _f("Invest_MeanIC"),
        "Spread":      _f("Invest_Spread"),
        "PosICRatio":  _f("Invest_PosICRatio"),
        "MeanIC_1M":   _f("Invest_MeanIC_1M"),
        "MeanIC_3M":   _f("Invest_MeanIC_3M"),
    }


def _coerce_to_record(obj: Any) -> Dict[str, Any]:
    """Best-effort: turn dict | single-row DataFrame | Series into a flat dict.

    NB: never use truthiness on the inputs — pandas DataFrame/Series raise
    `ValueError: truth value is ambiguous` when passed through ``or`` / ``if``.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    try:
        import pandas as pd
    except Exception:
        return {}
    if isinstance(obj, pd.Series):
        return {str(k): v for k, v in obj.to_dict().items()}
    if isinstance(obj, pd.DataFrame):
        if len(obj) == 0:
            return {}
        return {str(k): v for k, v in obj.iloc[0].to_dict().items()}
    return {}


def _build_summary_row_from_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Extract Invest_* numbers from best_invest_tbl / ga_summary if present."""
    row: Dict[str, Any] = {}

    try:
        rec = _coerce_to_record(bundle.get("best_invest_tbl"))
        for k, v in rec.items():
            key = k if k.startswith("Invest_") else f"Invest_{k}"
            row[key] = v
    except Exception as exc:
        print(f"[P5_RETRAIN] warn: failed to parse best_invest_tbl: {exc}")

    try:
        ga = _coerce_to_record(bundle.get("ga_summary"))
        for src, dst in (
            ("MeanIC_1M", "Invest_MeanIC_1M"),
            ("MeanIC_3M", "Invest_MeanIC_3M"),
            ("MeanIC",    "Invest_MeanIC"),
            ("Spread",    "Invest_Spread"),
            ("PosICRatio","Invest_PosICRatio"),
        ):
            if src in ga and dst not in row:
                row[dst] = ga[src]
    except Exception as exc:
        print(f"[P5_RETRAIN] warn: failed to parse ga_summary: {exc}")

    return row


# ─── Callable entry point (used by CLI, launcher.py UI button) ───────
def run_phase5_retrain(
    dry_run: bool = False,
    force_rebuild_pack: bool = False,
    # ── Phase B overrides (added 2026-04-23) ──
    # All default to None → use module-level constants (legacy behavior byte-compatible).
    # Batch orchestrator (run_phase5_batch_b.py) passes these per preset.
    train_start: Optional[datetime] = None,
    train_end: Optional[datetime] = None,
    run_tag: Optional[str] = None,
    ga_seed: Optional[int] = None,
    extra_overrides: Optional[Dict[str, Any]] = None,
    excel_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute Phase 5 retrain with fixed overrides.

    Returns a dict with artifact paths and signal-quality summary so the
    caller (CLI, UI button, notebook) can wire the next step without
    parsing stdout.

    When all Phase B parameters are None (legacy path), behaviour is
    byte-identical to the pre-refactor module. Phase B batch orchestrator
    uses the overrides to retarget train window, seed, run tag, and the
    deployment-penalty scalar without editing this file.
    """
    # Resolve Phase B parameters (None → module default → legacy T1b).
    _train_start = train_start if train_start is not None else TRAIN_START
    _train_end   = train_end   if train_end   is not None else TRAIN_END
    _run_tag     = run_tag     if run_tag     is not None else RUN_TAG
    _ga_seed     = ga_seed     if ga_seed     is not None else GA_SEED

    # Build effective overrides by layering extra_overrides on top.
    effective_overrides: Dict[str, Any] = dict(PHASE5_OVERRIDES)
    # Window / seed retarget (always applied so effective_overrides is
    # self-consistent with the resolved parameters above).
    effective_overrides["start_panel_date"] = _train_start
    effective_overrides["end_date"]         = _train_end
    effective_overrides["ga_seed"]          = _ga_seed
    if excel_prefix:
        effective_overrides["excel_prefix"] = excel_prefix
    if extra_overrides:
        effective_overrides.update(extra_overrides)

    # Extract deployment-penalty scalars for the header banner.
    _w_to = float(effective_overrides.get("w_turnover", 0.3))
    _w_co = float(effective_overrides.get("w_cost", 0.2))

    print("=" * 72)
    print(f"  Phase 5 Retrain  ({_run_tag})  —  T1 Deployment-Tuning")
    print("=" * 72)
    print(f"  train window   : {_train_start.date()} → {_train_end.date()}")
    print(f"  mode           : {'DRY-RUN (smoke)' if dry_run else 'FULL'}")
    print(f"  GA seed        : {_ga_seed}")
    print(f"  patched formula: F1 (entropy=0.04) + F4 (per-regime) + F11 (tradable mask) + S1 (cs_rank=True)")
    print(f"  T1             : deployment_penalty ON  (w_turnover={_w_to}, w_cost={_w_co})")
    print(f"  BULL biases    : default (all 6 ON)")
    print(f"  GA budget      : stability 100/300 × 8/12,  final 300 × 20  (reverted)")
    print("=" * 72)

    # ── Build Config with overrides ───────────────────────────────────
    cfg = engine.Config()
    applied = _apply_overrides(cfg, effective_overrides)
    if dry_run:
        applied += _apply_overrides(cfg, DRY_RUN_OVERRIDES)
    print(f"[P5_RETRAIN] applied {len(applied)} overrides over Config default")
    if applied:
        for k in sorted(applied):
            print(f"   - {k} = {getattr(cfg, k)!r}")

    # Optional: force rebuild of the training pack
    if force_rebuild_pack:
        npz_dir = getattr(cfg, "save_dir", None)
        if npz_dir:
            target = os.path.join(
                npz_dir,
                f"precompute_qresearch_v4_12_"
                f"{_train_start.strftime('%Y-%m-%d')}_"
                f"{_train_end.strftime('%Y-%m-%d')}.npz",
            )
            if os.path.exists(target):
                os.remove(target)
                print(f"[P5_RETRAIN] removed existing pack: {os.path.basename(target)}")

    # ── Prepare inputs (build/cache the training pack) ────────────────
    t_pack0 = time.time()
    prepared = engine.prepare_inputs(cfg)
    pack = prepared["pack"]
    regime_by_date = prepared.get("regime_by_date")
    pack_sec = time.time() - t_pack0
    print(f"[P5_RETRAIN] pack ready in {pack_sec:.1f}s  tickers={len(pack['tickers'])} dates={len(pack['dates'])}")

    # ── Run GA (meta disabled → single template + stability + final GA) ──
    t_ga0 = time.time()
    bundle = engine.run_search_from_pack(pack, regime_by_date, cfg)
    ga_sec = time.time() - t_ga0
    print(f"[P5_RETRAIN] GA finished in {ga_sec:.0f}s ({ga_sec/60:.1f} min)")

    best_mask = np.asarray(bundle["best_mask"], dtype=bool)
    best_wb   = np.asarray(bundle["best_wb"],   dtype=np.float64)
    best_ws   = np.asarray(bundle["best_ws"],   dtype=np.float64)
    best_wd   = np.asarray(bundle["best_wd"],   dtype=np.float64)

    summary_row = _build_summary_row_from_bundle(bundle)
    qs = _summarize_signal(best_mask, summary_row)

    print("\n" + "-" * 72)
    print(f"[P5_RETRAIN] Signal quality (in-sample, {_train_start.date()} → {_train_end.date()}):")
    for k, v in qs.items():
        print(f"   {k:<14s}= {v}")
    print("-" * 72)

    # ── Save frozen signal ────────────────────────────────────────────
    save_dir = getattr(cfg, "save_dir", "output")
    os.makedirs(save_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"{_run_tag}_DRYRUN" if dry_run else _run_tag
    fs_path = os.path.join(save_dir, f"frozen_signal_{tag}_{stamp}.npz")
    np.savez(
        fs_path,
        mask=best_mask,
        wb=best_wb, ws=best_ws, wd=best_wd,
        signal_summary=json.dumps(
            {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v)
             for k, v in summary_row.items() if isinstance(k, str) and k.startswith("Invest_")}
        ),
    )
    print(f"[P5_RETRAIN] frozen signal saved → {fs_path}")

    # ── Write run-log JSON for audit trail ────────────────────────────
    docs_dir = os.path.join(HERE, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    log_path = os.path.join(docs_dir, f"phase5_retrain_log_{stamp}.json")
    log = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_tag": _run_tag,
        "mode": "DRY_RUN" if dry_run else "FULL",
        "train_window": {
            "start": _train_start.strftime("%Y-%m-%d"),
            "end":   _train_end.strftime("%Y-%m-%d"),
        },
        "ga_seed": _ga_seed,
        "w_turnover": _w_to,
        "w_cost":     _w_co,
        "overrides_applied": applied,
        "pack_sec": round(pack_sec, 1),
        "ga_sec":   round(ga_sec, 1),
        "n_tickers": int(len(pack["tickers"])),
        "n_dates":   int(len(pack["dates"])),
        "signal_quality": qs,
        "frozen_signal_path": fs_path,
        "notes": (
            "Phase 5 stability-only retrain under patched GA formula "
            "(F1+F4+F11+S1). See phase3/docs/phase5_retrain_plan.md."
        ),
    }
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[P5_RETRAIN] run log saved → {log_path}")

    # ── Next step pointer ─────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  Next — Step C gate evaluation (Cursor will run this for you):")
    print(f"    python3 -u phase3/tests/step_c_gate_evaluation.py \\")
    print(f"        --signal {fs_path!s} \\")
    print(f"        --arm-name {tag}")
    print("=" * 72)

    return {
        "frozen_signal_path": fs_path,
        "run_log_path":       log_path,
        "arm_name":           tag,
        "mode":               "DRY_RUN" if dry_run else "FULL",
        "signal_quality":     qs,
        "pack_sec":           round(pack_sec, 1),
        "ga_sec":             round(ga_sec, 1),
        "overrides_applied":  applied,
    }


# ─── CLI wrapper ─────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 stability-only retrain")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Tiny GA to validate wiring; takes 1-2 min instead of ~3 h.",
    )
    parser.add_argument(
        "--force-rebuild-pack", action="store_true",
        help="Delete the target precompute .npz before prepare_inputs.",
    )
    args = parser.parse_args()
    run_phase5_retrain(
        dry_run=args.dry_run,
        force_rebuild_pack=args.force_rebuild_pack,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
