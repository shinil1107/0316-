"""P1 Option A — Surgical BULL Tilt Injection.

Creates T1b_BULL_INJECTED by surgically replacing T1b's BULL-regime weights
(``wb``) with Baseline_V2's wb, while keeping T1b's SIDE (``ws``) and
DEFENSIVE (``wd``) weights unchanged. The ``mask`` is recomputed as the
union of non-zero entries across the three new weight vectors.

Hypothesis
----------
T5 walk-forward + signal_feature_audit showed Baseline_V2 wins BULL-heavy
folds because of a concentrated long-horizon momentum tilt in ``wb`` that
T1b was prevented from developing by w_turnover=0.3/w_cost=0.2 in the
Phase-2 GA fitness. If we transplant Baseline's BULL ``wb`` into T1b we
should keep T1b's SIDE/DEF stability advantages while recovering some of
Baseline's BULL CAGR — directly separating mask+weight tilt from any
scoring-pipeline differences.

Caveat
------
Baseline's ``wb`` is NOT L1-normalised (Σ|wb|≈1.70), whereas T1b's wb
is normalised (Σ|w|=1.0). Ranking within a regime is monotone-invariant
so this does not affect BULL stock selection, but it changes the raw
score magnitude that may surface in diagnostic logs. Documented here
explicitly.

Output
------
A new frozen signal npz saved under ``OUTPUT_SIG_DIR`` as
``frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_<timestamp>.npz`` with the
same 5-key schema (mask, wb, ws, wd, signal_summary).
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
BASELINE_PATH = os.path.join(
    OUTPUT_SIG_DIR, "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"
)
T1B_PATH = os.path.join(
    OUTPUT_SIG_DIR, "frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz"
)


def _load_npz(path: str) -> Dict[str, Any]:
    z = np.load(path, allow_pickle=True)
    out: Dict[str, Any] = {
        "mask": np.asarray(z["mask"], dtype=bool),
        "wb": np.asarray(z["wb"], dtype=np.float64),
        "ws": np.asarray(z["ws"], dtype=np.float64),
        "wd": np.asarray(z["wd"], dtype=np.float64),
    }
    if "signal_summary" in z.files:
        try:
            out["signal_summary"] = json.loads(str(z["signal_summary"]))
        except Exception:
            out["signal_summary"] = {"raw": str(z["signal_summary"])}
    return out


def build_injected_signal() -> Dict[str, Any]:
    base = _load_npz(BASELINE_PATH)
    t1b = _load_npz(T1B_PATH)

    K_base = base["wb"].shape[0]
    K_t1b = t1b["wb"].shape[0]
    if K_base != K_t1b:
        raise RuntimeError(
            f"Feature-count mismatch: baseline K={K_base} vs T1b K={K_t1b}. "
            f"Both must share the same 36-feature taxonomy."
        )

    new_wb = base["wb"].copy()
    new_ws = t1b["ws"].copy()
    new_wd = t1b["wd"].copy()

    active_any = (new_wb != 0) | (new_ws != 0) | (new_wd != 0)
    new_mask = active_any.astype(bool)

    summary = {
        "origin": "P1 Option A — surgical BULL injection",
        "recipe": {
            "wb_source": os.path.basename(BASELINE_PATH),
            "ws_source": os.path.basename(T1B_PATH),
            "wd_source": os.path.basename(T1B_PATH),
            "mask_rule": "union of non-zero wb/ws/wd",
        },
        "baseline_src": BASELINE_PATH,
        "t1b_src": T1B_PATH,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "k_base_mask": int(base["mask"].sum()),
        "k_t1b_mask": int(t1b["mask"].sum()),
        "k_new_mask": int(new_mask.sum()),
        "sum_abs_wb_baseline": float(np.sum(np.abs(base["wb"]))),
        "sum_abs_ws_t1b": float(np.sum(np.abs(t1b["ws"]))),
        "sum_abs_wd_t1b": float(np.sum(np.abs(t1b["wd"]))),
        "caveat": (
            "Baseline's wb is not L1-normalised (Σ|wb|≈1.70); T1b's ws/wd are. "
            "Per-regime ranking is invariant to monotone rescaling so stock "
            "selection is unaffected; only diagnostic score magnitudes differ."
        ),
    }

    return {
        "mask": new_mask,
        "wb": new_wb,
        "ws": new_ws,
        "wd": new_wd,
        "signal_summary": summary,
        "_diagnostic": {
            "base_wb_nonzero": int(np.sum(base["wb"] != 0)),
            "t1b_ws_nonzero": int(np.sum(t1b["ws"] != 0)),
            "t1b_wd_nonzero": int(np.sum(t1b["wd"] != 0)),
            "new_mask_count": int(new_mask.sum()),
        },
    }


def save_signal(signal: Dict[str, Any], tag: str = "P5_RETRAIN_T1b_BULL_INJECTED") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"frozen_signal_{tag}_{ts}.npz"
    out_path = os.path.join(OUTPUT_SIG_DIR, fname)
    np.savez(
        out_path,
        mask=signal["mask"],
        wb=signal["wb"],
        ws=signal["ws"],
        wd=signal["wd"],
        signal_summary=json.dumps(signal["signal_summary"]),
    )
    return out_path


def main() -> int:
    print("[P1 Option A] Building T1b_BULL_INJECTED signal.")
    print(f"  baseline = {BASELINE_PATH}")
    print(f"  t1b      = {T1B_PATH}")

    signal = build_injected_signal()
    diag = signal.pop("_diagnostic")

    print()
    print("  === Diagnostic ===")
    print(f"    Baseline wb non-zero    : {diag['base_wb_nonzero']}")
    print(f"    T1b ws non-zero         : {diag['t1b_ws_nonzero']}")
    print(f"    T1b wd non-zero         : {diag['t1b_wd_nonzero']}")
    print(f"    New union mask count    : {diag['new_mask_count']}")
    print(f"    Σ|wb| (baseline)        : {signal['signal_summary']['sum_abs_wb_baseline']:.3f}")
    print(f"    Σ|ws| (t1b)             : {signal['signal_summary']['sum_abs_ws_t1b']:.3f}")
    print(f"    Σ|wd| (t1b)             : {signal['signal_summary']['sum_abs_wd_t1b']:.3f}")
    print()

    out_path = save_signal(signal)
    print(f"[saved] {out_path}")
    print()
    print("Next: register this path in phase3/tests/step_d_walk_forward.py SIGNALS")
    print("      then run the 6-fold walk-forward for the 5-signal compare.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
