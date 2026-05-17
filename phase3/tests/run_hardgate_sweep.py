"""Hardgate promotion sweep — runs walk-forward on all 3 fold-sets and
emits a unified promotion verdict.

Usage:
    python3 -u phase3/tests/run_hardgate_sweep.py --signals baseline,p7_j,p7_k
    python3 -u phase3/tests/run_hardgate_sweep.py --signals all
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg,
    _load_vix,
)
from phase3.tests.step_d_walk_forward import (  # noqa: E402
    FOLD_SETS, SIGNALS, DOCS_DIR,
    _pick_walk_forward_pack,
    _run_signal_over_folds, _aggregate, _compute_gates_v2,
    _write_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hardgate promotion sweep — all 3 fold-sets"
    )
    parser.add_argument(
        "--signals", default="all",
        help="Comma-separated signal ids or 'all'.",
    )
    parser.add_argument(
        "--fold-sets", default="default,rolling,regime",
        help="Comma-separated fold-set names to evaluate (default: all three).",
    )
    parser.add_argument(
        "--buy-grace-days", type=int, default=0,
    )
    args = parser.parse_args()

    print("=" * 80)
    print("  HARDGATE PROMOTION SWEEP")
    print("=" * 80)

    if args.signals == "all":
        signals = list(SIGNALS)
    else:
        wanted = {s.strip() for s in args.signals.split(",")}
        signals = [s for s in SIGNALS if s["id"] in wanted]

    missing_files = [s for s in signals if not os.path.exists(s["path"])]
    if missing_files:
        print(f"[WARN] skipping {len(missing_files)} signals with missing files")
        signals = [s for s in signals if os.path.exists(s["path"])]
    if not signals:
        print("[ERROR] no valid signals")
        return 1

    fold_set_names = [n.strip() for n in args.fold_sets.split(",")]
    for name in fold_set_names:
        if name not in FOLD_SETS:
            print(f"[ERROR] unknown fold-set: {name}")
            return 1

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]
    pack_path, pack_start, pack_end = _pick_walk_forward_pack(save_dir)
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")

    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    vix_c, vix_r, vix_s = _load_vix(cfg, pack_start, pack_end)
    trigger_conf = conf.get("triggers", {})

    all_results: Dict[str, Dict[str, Any]] = {}  # fold_set → {per_signal: [...]}
    t0_total = time.time()

    for fs_name in fold_set_names:
        folds = FOLD_SETS[fs_name]
        print()
        print("=" * 80)
        print(f"  FOLD-SET: {fs_name}  ({len(folds)} folds × {len(signals)} signals)")
        print("=" * 80)

        per_signal: List[Dict[str, Any]] = []
        for sig_cfg in signals:
            result = _run_signal_over_folds(
                sig_cfg, folds,
                cfg=cfg, pack=pack,
                vix_c=vix_c, vix_r=vix_r, vix_s=vix_s,
                trigger_conf=trigger_conf,
                buy_grace_days=int(args.buy_grace_days),
            )
            per_signal.append(result)

        baseline = next((s for s in per_signal if s["signal_id"] == "baseline"), None)
        if baseline is not None:
            for sig in per_signal:
                sig["gates"] = _compute_gates_v2(
                    sig["aggregate"], baseline["aggregate"],
                    sig["folds"], baseline["folds"],
                )

        all_results[fs_name] = {"per_signal": per_signal, "folds": folds}

    total_elapsed = time.time() - t0_total

    # ── Unified verdict ──
    print()
    print("=" * 80)
    print("  HARDGATE PROMOTION VERDICT")
    print("=" * 80)
    print(f"{'Signal':<25s}", end="")
    for fs_name in fold_set_names:
        print(f" {fs_name:>10s}", end="")
    print("   VERDICT")
    print("-" * 80)

    verdicts: Dict[str, Dict[str, Any]] = {}
    for sig in signals:
        sid = sig["id"]
        if sid == "baseline":
            continue
        row_pass = {}
        for fs_name in fold_set_names:
            ps = all_results[fs_name]["per_signal"]
            match = next((s for s in ps if s["signal_id"] == sid), None)
            if match and match.get("gates"):
                row_pass[fs_name] = match["gates"].get("all_hard_pass", False)
            else:
                row_pass[fs_name] = None

        n_pass = sum(1 for v in row_pass.values() if v is True)
        n_total = len(fold_set_names)
        if n_pass == n_total:
            verdict = "PROMOTE"
        elif n_pass >= n_total - 1:
            verdict = "CONDITIONAL"
        else:
            verdict = "REJECT"

        verdicts[sid] = {"per_foldset": row_pass, "verdict": verdict}

        print(f"{sig['arm']:<25s}", end="")
        for fs_name in fold_set_names:
            v = row_pass.get(fs_name)
            s = "   PASS   " if v else "   FAIL   " if v is not None else "    —     "
            print(s, end="")
        color = {"PROMOTE": "✓", "CONDITIONAL": "~", "REJECT": "✗"}
        print(f"   {color[verdict]} {verdict}")

    print("=" * 80)
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print("=" * 80)

    # ── Save per-foldset markdown + combined JSON ──
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for fs_name in fold_set_names:
        data = all_results[fs_name]
        report = {
            "meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "pack_path": pack_path,
                "pack_basename": os.path.basename(pack_path),
                "pack_start": pack_start, "pack_end": pack_end,
                "fold_set": fs_name,
                "total_elapsed_sec": round(total_elapsed, 1),
                "signals": [{"id": s["id"], "arm": s["arm"], "path": s["path"]} for s in signals],
                "folds": [{"id": f["id"], "start": f["start"], "end": f["end"], "group": f["group"]}
                          for f in data["folds"]],
                "protocol": {
                    "initial_capital": 100000.0,
                    "daily_buy_limit": 1000.0,
                    "commission_bps": 10.0, "slippage_bps": 5.0,
                    "rebalance_mode": "daily",
                    "strategy_stack": "SIDE_DEF_p12",
                    "regime_blend": False,
                },
            },
            "per_signal": data["per_signal"],
        }
        md_path = os.path.join(DOCS_DIR, f"hardgate_{fs_name}_{stamp}.md")
        _write_markdown(report, md_path)
        print(f"[saved] {md_path}")

    combined = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "fold_sets": fold_set_names,
            "total_elapsed_sec": round(total_elapsed, 1),
        },
        "verdicts": verdicts,
    }
    json_path = os.path.join(DOCS_DIR, f"hardgate_verdict_{stamp}.json")
    with open(json_path, "w") as f:
        json.dump(combined, f, indent=2, default=float)
    print(f"[saved] {json_path}")

    any_promote = any(v["verdict"] == "PROMOTE" for v in verdicts.values())
    return 0 if any_promote else 2


if __name__ == "__main__":
    raise SystemExit(main())
