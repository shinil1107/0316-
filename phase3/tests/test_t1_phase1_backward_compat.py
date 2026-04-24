"""Phase 1 (T1) backward-compat and correctness verification.

Verifies that adding deployment penalty hooks to `evaluate_individual_qresearch`:
  (A) Default (enable_deployment_penalty=False) leaves fitness identical to prior
  (B) enable_deployment_penalty=True with w_turnover=w_cost=0 also produces identical fitness
      (but populates deployment_turnover / deployment_cost_drag diagnostics)
  (C) Non-zero weights reduce fitness by exactly (to_pen + cost_pen)
  (D) Helper _compute_deployment_penalties correctness on synthetic input
"""
from __future__ import annotations

import os
import sys
import math
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))            # .../phase3/tests
PHASE3_DIR = os.path.dirname(HERE)                           # .../phase3
ROOT = os.path.dirname(PHASE3_DIR)                           # .../0316-
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from phase3.engine_loader import engine  # noqa: E402


SIGNAL_PATH = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"


def _load_signal(path: str) -> dict:
    fs = np.load(path, allow_pickle=True)
    return {
        "mask": np.asarray(fs["mask"], dtype=bool),
        "wb": np.asarray(fs["wb"], dtype=np.float64),
        "ws": np.asarray(fs["ws"], dtype=np.float64),
        "wd": np.asarray(fs["wd"], dtype=np.float64),
    }


def _get_pack(cfg) -> dict:
    """Try to load the most recent precompute pack matching cfg.precompute_npz_prefix."""
    import glob
    pattern = os.path.join(cfg.save_dir, f"{cfg.precompute_npz_prefix}_*.npz")
    candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
    if not candidates:
        raise RuntimeError(f"No pack found for pattern={pattern}")
    # Derive start/end from filename: <prefix>_<start>_<end>.npz
    fname = os.path.splitext(os.path.basename(candidates[0]))[0]
    parts = fname.split("_")
    start_s, end_s = parts[-2], parts[-1]
    pack = engine.load_precompute_panel(cfg, start_s, end_s)
    if pack is None:
        raise RuntimeError(f"Failed to load pack {candidates[0]}")
    return pack


def _call_eval(cfg, pack: dict, sig: dict):
    regime_by_date = {d: "SIDE" for d in list(pack["dates"])}
    fit, meta, *_ = engine.evaluate_individual_qresearch(
        pack, cfg,
        sig["mask"], sig["mask"], sig["mask"],
        sig["wb"], sig["ws"], sig["wd"],
        alpha=0.5,
        regime_by_date=regime_by_date,
        lightweight=True,
    )
    return float(fit), meta


def _fmt(x) -> str:
    if x is None:
        return "None"
    if isinstance(x, (bool, int)):
        return str(x)
    try:
        return f"{float(x):.10f}"
    except Exception:
        return str(x)


def test_helper_synthetic():
    print("\n### (D) _compute_deployment_penalties synthetic test")
    # 3 rebalances; top_n=4
    r0 = np.array([1, 2, 3, 4], dtype=np.int64)
    r1 = np.array([1, 2, 5, 6], dtype=np.int64)  # overlap=2/4 -> turnover 0.5
    r2 = np.array([5, 6, 7, 8], dtype=np.int64)  # overlap vs r1 = 2/4 -> turnover 0.5
    tr, cost = engine._compute_deployment_penalties([r0, r1, r2], top_n=4, cost_bps=20.0, eval_freq="W")
    assert abs(tr - 0.5) < 1e-12, f"turnover expected 0.5 got {tr}"
    exp_cost = 0.5 * (20.0 / 10000.0) * 52.0
    assert abs(cost - exp_cost) < 1e-12, f"cost expected {exp_cost} got {cost}"
    print(f"  turnover={tr:.4f}  cost_drag={cost:.6f} (expected {exp_cost:.6f})  OK")

    # Edge cases
    tr0, c0 = engine._compute_deployment_penalties([r0], top_n=4, cost_bps=15.0, eval_freq="W")
    assert tr0 == 0.0 and c0 == 0.0
    print("  single rebalance -> 0/0 OK")

    # Full turnover
    a = np.array([1, 2, 3, 4], dtype=np.int64)
    b = np.array([9, 10, 11, 12], dtype=np.int64)
    tr_full, _ = engine._compute_deployment_penalties([a, b], top_n=4, cost_bps=15.0, eval_freq="W")
    assert abs(tr_full - 1.0) < 1e-12
    print("  disjoint sets -> turnover=1.0 OK")

    # No turnover (identical picks)
    tr_none, _ = engine._compute_deployment_penalties([r0, r0, r0], top_n=4, cost_bps=15.0, eval_freq="W")
    assert abs(tr_none - 0.0) < 1e-12
    print("  identical sets -> turnover=0.0 OK")


def main():
    print("=" * 70)
    print("T1 Phase 1 Backward-Compat Verification")
    print("=" * 70)

    # Helper tests first (fast, no pack needed)
    test_helper_synthetic()

    # Build cfg matching the live system
    from phase3.daily_runner import build_engine_cfg
    import yaml
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    cfg_off = build_engine_cfg(conf)

    pack = _get_pack(cfg_off)
    print(f"\n[pack] D={pack['close'].shape[0]} N={pack['close'].shape[1]} K={pack['feat'].shape[0]}")

    sig = _load_signal(SIGNAL_PATH)
    print(f"[sig ] k_active={int(sig['mask'].sum())}")

    # (A) Default OFF
    print("\n### (A) default (enable_deployment_penalty=False)")
    fit_A, meta_A = _call_eval(cfg_off, pack, sig)
    print(f"  fit               = {fit_A:.10f}")
    print(f"  deployment_enabled= {meta_A.get('deployment_enabled')}")
    print(f"  deployment_turnover={_fmt(meta_A.get('deployment_turnover'))}")
    print(f"  deployment_cost_drag={_fmt(meta_A.get('deployment_cost_drag'))}")
    print(f"  to_pen={_fmt(meta_A.get('to_pen'))}  cost_pen={_fmt(meta_A.get('cost_pen'))}")
    assert meta_A.get("deployment_enabled") is False
    assert meta_A.get("deployment_turnover") == 0.0
    assert meta_A.get("to_pen") == 0.0
    assert meta_A.get("cost_pen") == 0.0

    # (B) ON with zero weights
    print("\n### (B) enable=True, w_turnover=0, w_cost=0")
    cfg_B = replace(cfg_off, enable_deployment_penalty=True, w_turnover=0.0, w_cost=0.0)
    fit_B, meta_B = _call_eval(cfg_B, pack, sig)
    print(f"  fit                = {fit_B:.10f}")
    print(f"  deployment_enabled = {meta_B.get('deployment_enabled')}")
    print(f"  deployment_turnover= {meta_B.get('deployment_turnover'):.6f}")
    print(f"  deployment_cost_drag={meta_B.get('deployment_cost_drag'):.6f}")
    print(f"  deployment_rebal_count={meta_B.get('deployment_rebal_count')}")
    print(f"  to_pen={meta_B.get('to_pen'):.6f}  cost_pen={meta_B.get('cost_pen'):.6f}")

    # Assertions: fit equal to A; turnover non-zero (live signal is not static)
    fit_delta = abs(fit_B - fit_A)
    print(f"  |fit_B - fit_A|    = {fit_delta:.3e}")
    assert fit_delta < 1e-12, f"fit should equal A when weights=0, got delta={fit_delta}"
    assert meta_B.get("deployment_enabled") is True
    assert meta_B.get("deployment_rebal_count", 0) > 10, "expected many rebalances"
    assert meta_B.get("to_pen") == 0.0
    assert meta_B.get("cost_pen") == 0.0

    # (C) Non-zero weights
    print("\n### (C) enable=True, w_turnover=0.5, w_cost=0.3")
    cfg_C = replace(cfg_off, enable_deployment_penalty=True, w_turnover=0.5, w_cost=0.3)
    fit_C, meta_C = _call_eval(cfg_C, pack, sig)
    print(f"  fit                = {fit_C:.10f}")
    print(f"  deployment_turnover= {meta_C.get('deployment_turnover'):.6f}")
    print(f"  deployment_cost_drag={meta_C.get('deployment_cost_drag'):.6f}")
    print(f"  to_pen={meta_C.get('to_pen'):.6f}  cost_pen={meta_C.get('cost_pen'):.6f}")

    expected_delta = -(meta_C["to_pen"] + meta_C["cost_pen"])
    actual_delta = fit_C - fit_A
    print(f"  fit_C - fit_A      = {actual_delta:.10f}")
    print(f"  expected (-to-cost)= {expected_delta:.10f}")
    print(f"  diff               = {abs(actual_delta - expected_delta):.3e}")
    assert abs(actual_delta - expected_delta) < 1e-10, "fit decomposition mismatch"

    # (C sanity) deployment metrics should match between B and C
    assert abs(meta_B["deployment_turnover"] - meta_C["deployment_turnover"]) < 1e-12
    assert abs(meta_B["deployment_cost_drag"] - meta_C["deployment_cost_drag"]) < 1e-12

    print("\n" + "=" * 70)
    print("ALL CHECKS PASSED")
    print("=" * 70)
    print(f"Summary for V2_GOLDEN_ENS_L3_v1 signal:")
    print(f"  avg per-rebalance turnover  = {meta_B['deployment_turnover']:.2%}")
    print(f"  estimated annual cost drag  = {meta_B['deployment_cost_drag']:.2%}")
    print(f"  rebalances observed         = {meta_B['deployment_rebal_count']}")


if __name__ == "__main__":
    main()
