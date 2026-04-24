"""D3 — Dynamic Exit Sweep CLI runner (E22).

Runs the full ``SWEEP_D2_EXIT_ARMS`` bank against the current V2 golden
signal, compares every arm against ``BASE_explicit`` (baseline
re-expressed as explicit triggers), and writes a gate-analysis report
next to the normal lab comparison CSV.

Usage::

    python3 _d3_sweep_runner.py                 # full sweep (all 24 arms)
    python3 _d3_sweep_runner.py --quick         # control run only (2 arms)
    python3 _d3_sweep_runner.py --arms X,Y,Z    # explicit subset

The runner purposely uses the same pack/signal loading path as T17
(``phase3_lab.run_lab``) so results are directly comparable with any
existing lab sweep output.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

_THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS))

import phase3_lab  # noqa: E402


def _select_arms(mode: str, explicit: list[str] | None) -> dict:
    bank_v1 = phase3_lab.SWEEP_D2_EXIT_ARMS
    bank_v2 = getattr(phase3_lab, "SWEEP_D2_EXIT_V2_ARMS", {})
    bank_v4 = getattr(phase3_lab, "SWEEP_D4_EXIT_ARMS", {})
    bank_v4v2 = getattr(phase3_lab, "SWEEP_D4_V2_ARMS", {})
    bank_v4v3 = getattr(phase3_lab, "SWEEP_D4_V3_MINI_ARMS", {})
    bank_v4v4 = getattr(phase3_lab, "SWEEP_D4_V4_COMBO_ARMS", {})
    bank_v4v5 = getattr(phase3_lab, "SWEEP_D4_V5_MICRO_ARMS", {})
    # Broader bank — used when explicit arms are requested (so users can
    # include legacy baseline arms like ``A_baseline`` for equivalence
    # checks alongside D2/D4 arms).
    ext_bank = {}
    for name in ("SAMPLE_ARMS", "SWEEP_ARMS", "SWEEP_V2_ARMS",
                 "SWEEP_V3_ARMS", "SWEEP_V4_ARMS", "SWEEP_TWOSTEP_ARMS",
                 "SWEEP_BLEND_ARMS", "SWEEP_BLEND_ASYM_ARMS",
                 "SWEEP_D2_EXIT_ARMS", "SWEEP_D2_EXIT_V2_ARMS",
                 "SWEEP_D4_EXIT_ARMS", "SWEEP_D4_V2_ARMS",
                 "SWEEP_D4_V3_MINI_ARMS", "SWEEP_D4_V4_COMBO_ARMS",
                 "SWEEP_D4_V5_MICRO_ARMS"):
        ext_bank.update(getattr(phase3_lab, name, {}) or {})

    if explicit:
        missing = [a for a in explicit if a not in ext_bank]
        if missing:
            raise SystemExit(f"[d3] unknown arm(s): {missing}. "
                             f"Available arms: {sorted(ext_bank)}")
        return {a: ext_bank[a] for a in explicit}

    if mode == "quick":
        # Control: baseline explicit + one fast D2 arm, for pipeline sanity.
        keep = ["BASE_explicit", "PD_20_SELL"]
        return {a: bank_v1[a] for a in keep}
    if mode == "quick_d4":
        keep = ["BASE_explicit", "ATR_k3_RO_SELL", "PT_50_SELL_SCORE"]
        avail = {**bank_v1, **bank_v4}
        return {a: avail[a] for a in keep if a in avail}

    if mode == "v2":
        return dict(bank_v2)
    if mode == "v4":
        return dict(bank_v4)
    if mode == "v4v2":
        return dict(bank_v4v2)
    if mode == "v4v3":
        return dict(bank_v4v3)
    if mode == "v4v4":
        return dict(bank_v4v4)
    if mode == "v4v5":
        return dict(bank_v4v5)

    return dict(bank_v1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2017-01-03")
    ap.add_argument("--end", default=None,
                    help="default = today")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--mode", choices=["daily", "event_driven"], default="daily")
    ap.add_argument("--arms", default=None,
                    help="comma-separated arm names (overrides --quick)")
    ap.add_argument("--quick", action="store_true",
                    help="control run: BASE_explicit + PD_20_SELL only")
    ap.add_argument("--v2", action="store_true",
                    help="run the D2.v2 re-tune sweep (SWEEP_D2_EXIT_V2_ARMS)")
    ap.add_argument("--v4", action="store_true",
                    help="run the D4 exploratory sweep (SWEEP_D4_EXIT_ARMS)")
    ap.add_argument("--v4v2", action="store_true",
                    help="run the D4.v2 PT_EXT precision sweep "
                         "(SWEEP_D4_V2_ARMS, 19 arms)")
    ap.add_argument("--v4v3", action="store_true",
                    help="run the D4.v3 mini sweep around the v2 winner "
                         "(SWEEP_D4_V3_MINI_ARMS, 10 arms)")
    ap.add_argument("--v4v4", action="store_true",
                    help="run the D4.v4 combo sweep (winner-stacking "
                         "interaction check, SWEEP_D4_V4_COMBO_ARMS, 8 arms)")
    ap.add_argument("--v4v5", action="store_true",
                    help="run the D4.v5 micro sweep (partial_pct peak "
                         "confirmation, SWEEP_D4_V5_MICRO_ARMS, 5 arms)")
    ap.add_argument("--quick-d4", action="store_true",
                    help="D4 quick smoke: BASE + ATR_k3_RO_SELL + PT_50")
    ap.add_argument("--dump-trades", action="store_true",
                    help="dump per-arm D4 trade log CSVs "
                         "(one file per arm, suffixed with --tag)")
    ap.add_argument("--tag", default=None,
                    help="report filename tag (default: timestamp)")
    args = ap.parse_args(argv)

    explicit_list = (
        [x.strip() for x in args.arms.split(",") if x.strip()]
        if args.arms else None
    )
    if args.quick_d4:
        mode = "quick_d4"
    elif args.quick:
        mode = "quick"
    elif args.v4v5:
        mode = "v4v5"
    elif args.v4v4:
        mode = "v4v4"
    elif args.v4v3:
        mode = "v4v3"
    elif args.v4v2:
        mode = "v4v2"
    elif args.v4:
        mode = "v4"
    elif args.v2:
        mode = "v2"
    else:
        mode = "full"
    arms = _select_arms(mode, explicit_list)
    print(f"[d3] running {len(arms)} arms: {list(arms)}")

    end = args.end or datetime.now().strftime("%Y-%m-%d")

    t0 = time.time()
    result = phase3_lab.run_lab(
        arms=arms,
        start_date=args.start,
        end_date=end,
        initial_capital=args.capital,
        rebalance_mode=args.mode,
        progress_fn=lambda m: print(m),
        dump_trades=args.dump_trades,
    )
    elapsed = time.time() - t0
    print(f"\n[d3] lab run finished in {elapsed:.1f}s")

    # Save lab comparison (reuse existing helper).
    with open(_THIS / "config.yaml") as f:
        conf = yaml.safe_load(f)
    out_dir = conf["paths"]["output_dir"]
    comp_path = phase3_lab.save_lab_results(result, out_dir)
    print(f"[d3] lab comparison saved: {comp_path}")

    if args.dump_trades:
        tag_for_trades = args.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
        written = phase3_lab.dump_trade_logs(
            result["results"], out_dir, tag=tag_for_trades,
        )
        if written:
            print(f"\n[d3] D4 trade-log dumps ({len(written)} arm(s)):")
            for arm, path in written.items():
                print(f"  {arm:30s} → {path}")
        else:
            print("[d3] --dump-trades requested but no arm emitted D4 events.")

    # Gate analysis.
    comp = result["comparison"]
    if "BASE_explicit" not in comp.index:
        print("[d3] WARNING: BASE_explicit arm missing — skipping gate analysis.")
        return 0

    gate = phase3_lab.analyze_d2_sweep(comp, baseline_arm="BASE_explicit")
    tag = args.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    gate_path = os.path.join(out_dir, f"d3_gate_analysis_{tag}.csv")
    gate.to_csv(gate_path)

    print(f"\n{'=' * 92}")
    print(" D3 — Dynamic Exit Sweep Gate Analysis")
    print(" Baseline: BASE_explicit")
    print(f"{'=' * 92}")
    print(gate.to_string())
    print(f"\n[d3] gate analysis saved: {gate_path}")

    passes = gate[gate["Verdict"] == "PASS"]
    nears = gate[gate["Verdict"] == "NEAR"]
    print(f"\n[d3] PASS arms ({len(passes)}): {list(passes.index)}")
    print(f"[d3] NEAR arms ({len(nears)}): {list(nears.index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
