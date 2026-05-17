"""P2 — Regime-Composite Ensemble Builder.

Creates regime-composite frozen signals by surgically stitching per-regime
weight vectors (``wb``/``ws``/``wd``) from three different source signals
into a **single npz** that fits the existing 5-key schema and can be
validated by the unchanged 6-fold walk-forward pipeline.

Two composition modes are supported:

1. **Single-source slot (presets A-D)** — ``ws`` (or ``wb``/``wd``) is
   copied verbatim from one source signal. Mask is union of non-zero
   entries across the three slots.
2. **Weighted-average slot (preset E, added 2026-04-26)** — ``ws`` is
   computed as ``Σ w_i × source_i.ws`` over multiple source signals.
   This mirrors how V2_GOLDEN's dense per-regime weights were produced
   (averaging multiple GA candidates fills features by construction)
   and works around the B4 finding that single GA SIDE specialists
   collapse to k=1-2 features under regime-conditional turnover
   pressure (conc_penalty/entropy_bonus auto-degenerate at k=1).

Rationale
---------
After T5 walk-forward (2026-04-25), two non-dominated candidates emerged:

* ``T1b_BULL_INJECTED`` — wins BULL-dominant folds (F0a, F0b, F1, F3, F4)
  via Baseline_V2's momentum-heavy ``wb``.
* ``P5C_BULL_FREE``     — wins the SIDE-dominant fold (F2 +17.12 % vs
  T1b_INJ +7.39 %) thanks to the Phase B2 regime-conditional engine that
  gave BULL full freedom while damping SIDE turnover.

Neither Pareto-dominates the other; the natural next move is to keep the
BULL slot from T1b_INJ (= Baseline's wb) and swap in BULL_FREE's SIDE
slot to recover the F2 gap, then test two DEF alternatives.

Presets
-------
ENSEMBLE_A (explicit tail defense)
    wb ← T1b_BULL_INJECTED     (≈ Baseline_V2.wb, momentum tilt)
    ws ← P5C_BULL_FREE.ws      (SIDE resilience, F2 winner)
    wd ← P5C_DEF_HEAVY.wd      (most protective DEF profile, best worst-fold)

ENSEMBLE_B (stability-consistent)
    wb ← T1b_BULL_INJECTED
    ws ← P5C_BULL_FREE.ws
    wd ← P5C_BULL_FREE.wd      (reuse BULL_FREE's full stability profile)

Mask is recomputed as the **union of non-zero entries** across the new
``wb``/``ws``/``wd`` (same rule as p1_bull_injection.py). All three source
signals share the same 36-feature taxonomy so shape compatibility is
guaranteed; this script asserts that invariant up-front.

Output
------
Two new frozen signals saved under ``OUTPUT_SIG_DIR`` as::

    frozen_signal_P6_ENSEMBLE_A_<timestamp>.npz
    frozen_signal_P6_ENSEMBLE_B_<timestamp>.npz

Each carries the standard 5-key schema (mask, wb, ws, wd, signal_summary).
``signal_summary`` records the recipe, source hashes, mask accounting and
|L1| magnitudes for later traceability.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"

SRC_T1B_INJ = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5_RETRAIN_T1b_BULL_INJECTED_20260423_225842.npz",
)
SRC_BULL_FREE = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5C_BULL_FREE_20260424_192109.npz",
)
SRC_DEF_HEAVY = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5C_DEF_HEAVY_20260424_212616.npz",
)
SRC_BASELINE = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
)
SRC_SIDE_DEEP = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5D_SIDE_DEEP_20260426_024004.npz",
)
SRC_SIDE_PURE = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5D_SIDE_PURE_20260426_003825.npz",
)
SRC_SIDE_WIN = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5D_SIDE_WIN_20260426_042332.npz",
)
SRC_B6_BASELINE_15Y = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P6_BASELINE_15Y_20260428_032446.npz",
)
SRC_B6_SIDE_TECH_15Y = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P6_SIDE_TECH_15Y_20260428_093425.npz",
)
SRC_B6_SIDE_FUND_15Y = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P6_SIDE_FUND_15Y_20260428_151355.npz",
)


def _resolve_latest(tag_prefix: str) -> Optional[str]:
    """Find the latest ``frozen_signal_<tag_prefix>_*.npz`` under
    :data:`OUTPUT_SIG_DIR`. Returns ``None`` if no match.

    Used to lazily resolve Batch-5 specialist outputs whose timestamps
    are unknown until the overnight GA run completes.
    """
    pattern = os.path.join(OUTPUT_SIG_DIR, f"frozen_signal_{tag_prefix}_*.npz")
    matches = sorted(
        m for m in glob.glob(pattern)
        if "DRYRUN" not in os.path.basename(m)
    )
    return matches[-1] if matches else None


def _load_npz(path: str) -> Dict[str, Any]:
    z = np.load(path, allow_pickle=True)
    out: Dict[str, Any] = {
        "mask": np.asarray(z["mask"], dtype=bool),
        "wb":   np.asarray(z["wb"],   dtype=np.float64),
        "ws":   np.asarray(z["ws"],   dtype=np.float64),
        "wd":   np.asarray(z["wd"],   dtype=np.float64),
    }
    if "signal_summary" in z.files:
        try:
            out["signal_summary"] = json.loads(str(z["signal_summary"]))
        except Exception:
            out["signal_summary"] = {"raw": str(z["signal_summary"])}
    return out


def _assert_same_k(name_a: str, a: Dict[str, Any],
                   name_b: str, b: Dict[str, Any]) -> None:
    for key in ("wb", "ws", "wd"):
        if a[key].shape != b[key].shape:
            raise RuntimeError(
                f"Shape mismatch on {key!r}: {name_a}={a[key].shape} "
                f"vs {name_b}={b[key].shape}."
            )


def build_ensemble(
    wb_src_path: str, ws_src_path: str, wd_src_path: str,
    wb_src_label: str, ws_src_label: str, wd_src_label: str,
) -> Dict[str, Any]:
    wb_src = _load_npz(wb_src_path)
    ws_src = _load_npz(ws_src_path)
    wd_src = _load_npz(wd_src_path)

    _assert_same_k("wb_src", wb_src, "ws_src", ws_src)
    _assert_same_k("wb_src", wb_src, "wd_src", wd_src)

    new_wb = wb_src["wb"].copy()
    new_ws = ws_src["ws"].copy()
    new_wd = wd_src["wd"].copy()
    new_mask = ((new_wb != 0) | (new_ws != 0) | (new_wd != 0)).astype(bool)

    summary = {
        "origin": "P2 regime-composite ensemble",
        "recipe": {
            "wb_source_label": wb_src_label,
            "ws_source_label": ws_src_label,
            "wd_source_label": wd_src_label,
            "wb_source_path": os.path.basename(wb_src_path),
            "ws_source_path": os.path.basename(ws_src_path),
            "wd_source_path": os.path.basename(wd_src_path),
            "mask_rule": "union of non-zero wb/ws/wd",
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "k_new_mask": int(new_mask.sum()),
        "k_wb_src_mask": int(wb_src["mask"].sum()),
        "k_ws_src_mask": int(ws_src["mask"].sum()),
        "k_wd_src_mask": int(wd_src["mask"].sum()),
        "wb_nonzero": int(np.sum(new_wb != 0)),
        "ws_nonzero": int(np.sum(new_ws != 0)),
        "wd_nonzero": int(np.sum(new_wd != 0)),
        "sum_abs_wb": float(np.sum(np.abs(new_wb))),
        "sum_abs_ws": float(np.sum(np.abs(new_ws))),
        "sum_abs_wd": float(np.sum(np.abs(new_wd))),
        "caveat": (
            "Per-regime ranking is invariant to monotone rescaling, so Σ|w| "
            "differences across source signals do not affect stock selection "
            "within a regime; they only surface in diagnostic score magnitude."
        ),
    }

    return {
        "mask": new_mask,
        "wb":   new_wb,
        "ws":   new_ws,
        "wd":   new_wd,
        "signal_summary": summary,
    }


def build_ensemble_weighted_ws(
    wb_src_path: str,
    ws_components: Sequence[Tuple[str, float, str]],
    wd_src_path: str,
    wb_src_label: str,
    wd_src_label: str,
) -> Dict[str, Any]:
    """Compose an ensemble where ``ws`` is the weighted average across
    multiple source signals.  ``wb`` and ``wd`` are taken verbatim from
    a single source each (same pattern as :func:`build_ensemble`).

    Parameters
    ----------
    ws_components
        Iterable of ``(path, weight, label)`` tuples.  Weights are
        normalized to sum to 1 internally; absolute scale of the
        resulting ``ws`` is irrelevant for stock ranking (preserved
        caveat from preset A-D).  Missing files (path is ``None`` /
        empty) are silently skipped — useful when B5 specialists have
        not yet been generated.

    The mask is recomputed as the union of non-zero entries across the
    final ``wb``/``ws``/``wd``.
    """
    wb_src = _load_npz(wb_src_path)
    wd_src = _load_npz(wd_src_path)
    _assert_same_k("wb_src", wb_src, "wd_src", wd_src)

    valid: List[Tuple[str, float, str, np.ndarray]] = []
    skipped: List[str] = []
    for path, weight, label in ws_components:
        if not path or not os.path.exists(path):
            skipped.append(f"{label} (missing: {path or '<unset>'})")
            continue
        src = _load_npz(path)
        _assert_same_k("wb_src", wb_src, label, src)
        valid.append((path, float(weight), label, src["ws"].copy()))

    if not valid:
        raise RuntimeError("No valid ws components — cannot build weighted ensemble.")

    total_w = sum(w for _, w, _, _ in valid)
    if total_w <= 0:
        raise RuntimeError(f"ws_components weight sum must be > 0 (got {total_w}).")

    new_ws = np.zeros_like(wb_src["wb"], dtype=np.float64)
    for _, w, _, ws_vec in valid:
        new_ws += (w / total_w) * ws_vec

    new_wb = wb_src["wb"].copy()
    new_wd = wd_src["wd"].copy()
    new_mask = ((new_wb != 0) | (new_ws != 0) | (new_wd != 0)).astype(bool)

    summary = {
        "origin": "P2 regime-composite ensemble (weighted-avg ws)",
        "recipe": {
            "wb_source_label": wb_src_label,
            "wb_source_path":  os.path.basename(wb_src_path),
            "ws_mode": "weighted_average",
            "ws_components": [
                {
                    "label":          label,
                    "path":           os.path.basename(path),
                    "weight_input":   float(w),
                    "weight_normed":  float(w / total_w),
                    "ws_nonzero_in":  int(np.sum(ws_vec != 0)),
                    "ws_sum_abs_in":  float(np.sum(np.abs(ws_vec))),
                }
                for path, w, label, ws_vec in valid
            ],
            "ws_components_skipped": skipped,
            "wd_source_label": wd_src_label,
            "wd_source_path":  os.path.basename(wd_src_path),
            "mask_rule": "union of non-zero wb/ws/wd",
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "k_new_mask": int(new_mask.sum()),
        "k_wb_src_mask": int(wb_src["mask"].sum()),
        "k_wd_src_mask": int(wd_src["mask"].sum()),
        "wb_nonzero": int(np.sum(new_wb != 0)),
        "ws_nonzero": int(np.sum(new_ws != 0)),
        "wd_nonzero": int(np.sum(new_wd != 0)),
        "sum_abs_wb": float(np.sum(np.abs(new_wb))),
        "sum_abs_ws": float(np.sum(np.abs(new_ws))),
        "sum_abs_wd": float(np.sum(np.abs(new_wd))),
        "caveat": (
            "Per-regime ranking is invariant to monotone rescaling, so Σ|w| "
            "differences across source signals do not affect stock selection "
            "within a regime; they only surface in diagnostic score magnitude."
        ),
    }

    return {
        "mask": new_mask,
        "wb":   new_wb,
        "ws":   new_ws,
        "wd":   new_wd,
        "signal_summary": summary,
    }


def build_ensemble_weighted_all_slots(
    wb_components: Sequence[Tuple[str, float, str]],
    ws_components: Sequence[Tuple[str, float, str]],
    wd_components: Sequence[Tuple[str, float, str]],
) -> Dict[str, Any]:
    """Compose an ensemble via weighted average of ALL three regime slots.

    Unlike :func:`build_ensemble_weighted_ws` (which only averages ``ws``),
    this function averages ``wb``, ``ws``, and ``wd`` independently, each
    from their own component lists.  This is how V2_GOLDEN's L3 ensemble
    was originally built — averaging 3+ GA candidates fills features by
    construction (k=8-12 each → combined k=15-20 non-zero).

    Each ``*_components`` is a sequence of ``(path, weight, label)`` tuples.
    Weights are normalised per slot.  Missing files are skipped.
    """
    def _weighted_avg(
        components: Sequence[Tuple[str, float, str]],
        slot: str,
        ref_k: Optional[int] = None,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]], List[str]]:
        valid: List[Tuple[str, float, str, np.ndarray]] = []
        skipped: List[str] = []
        for path, weight, label in components:
            if not path or not os.path.exists(path):
                skipped.append(f"{label} (missing: {path or '<unset>'})")
                continue
            src = _load_npz(path)
            vec = src[slot].copy()
            if ref_k is not None and len(vec) != ref_k:
                raise RuntimeError(
                    f"Feature dimension mismatch for {label}: "
                    f"expected {ref_k}, got {len(vec)}"
                )
            valid.append((path, float(weight), label, vec))

        if not valid:
            raise RuntimeError(f"No valid {slot} components.")

        total_w = sum(w for _, w, _, _ in valid)
        if total_w <= 0:
            raise RuntimeError(f"{slot} component weight sum must be > 0 (got {total_w}).")

        result = np.zeros_like(valid[0][3], dtype=np.float64)
        for _, w, _, vec in valid:
            result += (w / total_w) * vec

        comp_info = [
            {
                "label":         label,
                "path":          os.path.basename(path),
                "weight_input":  float(w),
                "weight_normed": float(w / total_w),
                "nonzero_in":    int(np.sum(vec != 0)),
                "sum_abs_in":    float(np.sum(np.abs(vec))),
            }
            for path, w, label, vec in valid
        ]
        return result, comp_info, skipped

    first_valid_path = next(
        (p for p, _, _ in list(wb_components) + list(ws_components) + list(wd_components)
         if p and os.path.exists(p)),
        None,
    )
    if first_valid_path is None:
        raise RuntimeError("No valid component files found.")
    ref_k = len(_load_npz(first_valid_path)["wb"])

    new_wb, wb_info, wb_skipped = _weighted_avg(wb_components, "wb", ref_k)
    new_ws, ws_info, ws_skipped = _weighted_avg(ws_components, "ws", ref_k)
    new_wd, wd_info, wd_skipped = _weighted_avg(wd_components, "wd", ref_k)
    new_mask = ((new_wb != 0) | (new_ws != 0) | (new_wd != 0)).astype(bool)

    summary = {
        "origin": "L3 multi-candidate ensemble (weighted-avg all 3 slots)",
        "recipe": {
            "wb_mode": "weighted_average",
            "wb_components": wb_info,
            "wb_components_skipped": wb_skipped,
            "ws_mode": "weighted_average",
            "ws_components": ws_info,
            "ws_components_skipped": ws_skipped,
            "wd_mode": "weighted_average",
            "wd_components": wd_info,
            "wd_components_skipped": wd_skipped,
            "mask_rule": "union of non-zero wb/ws/wd",
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "k_new_mask": int(new_mask.sum()),
        "wb_nonzero": int(np.sum(new_wb != 0)),
        "ws_nonzero": int(np.sum(new_ws != 0)),
        "wd_nonzero": int(np.sum(new_wd != 0)),
        "sum_abs_wb": float(np.sum(np.abs(new_wb))),
        "sum_abs_ws": float(np.sum(np.abs(new_ws))),
        "sum_abs_wd": float(np.sum(np.abs(new_wd))),
    }

    return {
        "mask": new_mask,
        "wb":   new_wb,
        "ws":   new_ws,
        "wd":   new_wd,
        "signal_summary": summary,
    }


def save_signal(signal: Dict[str, Any], tag: str) -> str:
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


PRESETS: Dict[str, Dict[str, str]] = {
    "A": {
        "tag": "P6_ENSEMBLE_A",
        "wb_src":   SRC_T1B_INJ,   "wb_label":   "T1b_BULL_INJECTED",
        "ws_src":   SRC_BULL_FREE, "ws_label":   "P5C_BULL_FREE",
        "wd_src":   SRC_DEF_HEAVY, "wd_label":   "P5C_DEF_HEAVY",
    },
    "B": {
        "tag": "P6_ENSEMBLE_B",
        "wb_src":   SRC_T1B_INJ,   "wb_label":   "T1b_BULL_INJECTED",
        "ws_src":   SRC_BULL_FREE, "ws_label":   "P5C_BULL_FREE",
        "wd_src":   SRC_BULL_FREE, "wd_label":   "P5C_BULL_FREE",
    },
    # Preset C — pragmatic fix after full-period A/B (2026-04-25).
    #   Preset A/B's wd slot was underfit by Phase B2 GA (sparse 2-feature
    #   degenerate optimum {f25: 0.5, f26: 0.5}) because DEF days are too
    #   rare in the 2012-2024 training window. Baseline_V2.wd is a rich
    #   15-feature vector that wins DEF regime by +15.6 pp AnnRet.
    #   Preset C keeps Baseline's wb *and* wd (equivalent to Baseline) and
    #   swaps in BULL_FREE.ws to try to recover some SIDE resilience
    #   (F2 walk-forward +9.7 pp over baseline).
    "C": {
        "tag": "P6_ENSEMBLE_C",
        "wb_src":   SRC_BASELINE,  "wb_label":   "Baseline_V2",
        "ws_src":   SRC_BULL_FREE, "ws_label":   "P5C_BULL_FREE",
        "wd_src":   SRC_BASELINE,  "wd_label":   "Baseline_V2",
    },
    # Preset D — SIDE-specialist injection (2026-04-26).
    #   Keeps Baseline's wb + wd (same as Preset C) but swaps in the
    #   Phase B2.1 SIDE-specialist P5D_SIDE_DEEP.ws.  Walk-forward F2
    #   (72% SIDE) showed P5D_SIDE_DEEP at +18.49% CAGR vs baseline's
    #   +4.30% (+14.19 pp).  CV=0.350 (best of all B4 candidates).
    "D": {
        "tag": "P6_ENSEMBLE_D",
        "wb_src":   SRC_BASELINE,   "wb_label":   "Baseline_V2",
        "ws_src":   SRC_SIDE_DEEP,  "ws_label":   "P5D_SIDE_DEEP",
        "wd_src":   SRC_BASELINE,   "wd_label":   "Baseline_V2",
    },
}


# ── Preset E — weighted-avg SIDE specialist (Batch-4 + Batch-5 union).
# ws is computed as Σ w_i × source_i.ws over 3 B4 + 2 B5 SIDE specialists
# (B5 paths auto-resolved from latest matching glob; missing B5 outputs
# are skipped without aborting).  Default weights reflect:
#
#   - Higher trust in B5 specialists (3 h budget × anti-collapse knobs ×
#     orthogonal feature pools)  → 0.30 each.
#   - SIDE_DEEP dominates B4 (best F2 CAGR +18.49%)              → 0.20.
#   - SIDE_PURE / SIDE_WIN added with smaller weights for diversity.
#
# Sum = 1.00.  Re-run after the B5 overnight batch completes.
PRESETS["E"] = {
    "tag": "P6_ENSEMBLE_E",
    "wb_src":   SRC_BASELINE,  "wb_label":   "Baseline_V2",
    "wd_src":   SRC_BASELINE,  "wd_label":   "Baseline_V2",
    "ws_mode":  "weighted_avg",
    # ws_components is resolved at runtime so B5 paths are picked up
    # only after the overnight batch finishes; build the spec lazily.
    "ws_components_spec": [
        ("P5D_SIDE_PURE",         0.10, "P5D_SIDE_PURE",        SRC_SIDE_PURE),
        ("P5D_SIDE_DEEP",         0.20, "P5D_SIDE_DEEP",        SRC_SIDE_DEEP),
        ("P5D_SIDE_WIN",          0.10, "P5D_SIDE_WIN",         SRC_SIDE_WIN),
        ("P5E_SIDE_TECH",         0.30, "P5E_SIDE_TECH",        None),  # latest glob
        ("P5E_SIDE_FUND_BREAKOUT",0.30, "P5E_SIDE_FUND_BRK",    None),  # latest glob
    ],
}


# ── Preset F — V2 wb/wd + P6_BASELINE_15Y ws (Phase 1 Quick Win).
#   Exploits per-fold analysis: V2 dominates BULL (5/6 folds), B6_BASELINE_15Y
#   dominates SIDE (F2: +21.3% vs V2's +14.8%).  Stitch V2's BULL slot with
#   B6's SIDE slot.  DEF kept as V2 (no DEF days in current folds, safe default).
PRESETS["F"] = {
    "tag": "P7_ENSEMBLE_F",
    "wb_src":   SRC_BASELINE,         "wb_label": "Baseline_V2",
    "ws_src":   SRC_B6_BASELINE_15Y,  "ws_label": "P6_BASELINE_15Y",
    "wd_src":   SRC_BASELINE,         "wd_label": "Baseline_V2",
}

# ── Preset G — V2 wb/wd + weighted blend of 3 B6 15Y specialists (Phase 1).
#   ws = 0.5*P6_BASELINE_15Y + 0.3*P6_SIDE_TECH_15Y + 0.2*P6_SIDE_FUND_15Y
PRESETS["G"] = {
    "tag": "P7_ENSEMBLE_G",
    "wb_src":   SRC_BASELINE,  "wb_label": "Baseline_V2",
    "wd_src":   SRC_BASELINE,  "wd_label": "Baseline_V2",
    "ws_mode":  "weighted_avg",
    "ws_components_spec": [
        ("P6_BASELINE_15Y",  0.50, "P6_BL_15Y",     None),
        ("P6_SIDE_TECH_15Y", 0.30, "P6_ST_15Y",     None),
        ("P6_SIDE_FUND_15Y", 0.20, "P6_SF_15Y",     None),
    ],
}


# ── Preset H — L3 multi-candidate ensemble from Phase 2 (Batch 7) outputs.
#   All 3 slots (wb/ws/wd) are weighted-averaged across the best B7 candidates.
#   Averaging 4 signals with k=8-12 each produces a dense combined signal
#   with k=15-20+ non-zero features (the V2_GOLDEN construction method).
PRESETS["H"] = {
    "tag": "P7_L3_ENSEMBLE_H",
    "all_slots_mode": True,
    "wb_components_spec": [
        ("P7_V2_FULL",     0.30, "P7_V2_FULL",     None),
        ("P7_V2_NO_DEPLOY",0.20, "P7_V2_NO_DEPLOY",None),
        ("P7_V2_MEGA",     0.30, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.20, "P7_V2_BULL_AGG", None),
    ],
    "ws_components_spec": [
        ("P7_V2_FULL",     0.30, "P7_V2_FULL",     None),
        ("P7_V2_NO_DEPLOY",0.20, "P7_V2_NO_DEPLOY",None),
        ("P7_V2_MEGA",     0.30, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.20, "P7_V2_BULL_AGG", None),
    ],
    "wd_components_spec": [
        ("P7_V2_FULL",     0.30, "P7_V2_FULL",     None),
        ("P7_V2_NO_DEPLOY",0.20, "P7_V2_NO_DEPLOY",None),
        ("P7_V2_MEGA",     0.30, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.20, "P7_V2_BULL_AGG", None),
    ],
}

# ── Preset I — Hybrid L3: blend B7 candidates with Baseline_V2.
#   wb = 0.6*best_P7 + 0.4*V2, ws = avg(all P7), wd = 0.5*best_P7 + 0.5*V2
PRESETS["I"] = {
    "tag": "P7_L3_HYBRID_I",
    "all_slots_mode": True,
    "wb_components_spec": [
        ("P7_V2_FULL",     0.20, "P7_V2_FULL",     None),
        ("P7_V2_MEGA",     0.20, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.20, "P7_V2_BULL_AGG", None),
        (None,             0.40, "Baseline_V2",     SRC_BASELINE),
    ],
    "ws_components_spec": [
        ("P7_V2_FULL",     0.25, "P7_V2_FULL",     None),
        ("P7_V2_NO_DEPLOY",0.25, "P7_V2_NO_DEPLOY",None),
        ("P7_V2_MEGA",     0.25, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.25, "P7_V2_BULL_AGG", None),
    ],
    "wd_components_spec": [
        ("P7_V2_FULL",     0.17, "P7_V2_FULL",     None),
        ("P7_V2_MEGA",     0.17, "P7_V2_MEGA",     None),
        ("P7_V2_BULL_AGG", 0.16, "P7_V2_BULL_AGG", None),
        (None,             0.50, "Baseline_V2",     SRC_BASELINE),
    ],
}


# ── Preset J — MEGA wb + V2 ws/wd (regime-stitch: best F4 BULL into V2 SIDE).
#   MEGA posted +51.41% F4 (best post-OOS) and +25.94% F0a (best pre-OOS BULL).
#   V2's ws preserves F2 SIDE defense (+14.76%).
PRESETS["J"] = {
    "tag": "P7_STITCH_J",
    "wb_src":   None,           "wb_label": "P7_V2_MEGA",
    "ws_src":   SRC_BASELINE,   "ws_label": "Baseline_V2",
    "wd_src":   SRC_BASELINE,   "wd_label": "Baseline_V2",
    "_wb_resolve": "P7_V2_MEGA",
}

# ── Preset K — NO_DEPLOY wb + V2 ws/wd (best overall B7 mean = 29.74%).
PRESETS["K"] = {
    "tag": "P7_STITCH_K",
    "wb_src":   None,           "wb_label": "P7_V2_NO_DEPLOY",
    "ws_src":   SRC_BASELINE,   "ws_label": "Baseline_V2",
    "wd_src":   SRC_BASELINE,   "wd_label": "Baseline_V2",
    "_wb_resolve": "P7_V2_NO_DEPLOY",
}

# ── Preset L — blended wb (MEGA+NO_DEPLOY) + V2 ws/wd.
#   Averages the two strongest B7 BULL vectors for robustness.
PRESETS["L"] = {
    "tag": "P7_BLEND_L",
    "all_slots_mode": True,
    "wb_components_spec": [
        ("P7_V2_MEGA",      0.50, "P7_V2_MEGA",      None),
        ("P7_V2_NO_DEPLOY", 0.50, "P7_V2_NO_DEPLOY",  None),
    ],
    "ws_components_spec": [
        (None, 1.0, "Baseline_V2", SRC_BASELINE),
    ],
    "wd_components_spec": [
        (None, 1.0, "Baseline_V2", SRC_BASELINE),
    ],
}

# ── Preset M — triple-source wb (MEGA+NO_DEPLOY+V2) + B6 ws + V2 wd.
#   Injects B7 BULL diversity while using B6's SIDE slot (F2 +21.3%).
PRESETS["M"] = {
    "tag": "P7_TRIPLE_M",
    "all_slots_mode": True,
    "wb_components_spec": [
        ("P7_V2_MEGA",      0.35, "P7_V2_MEGA",      None),
        ("P7_V2_NO_DEPLOY", 0.30, "P7_V2_NO_DEPLOY",  None),
        (None,              0.35, "Baseline_V2",       SRC_BASELINE),
    ],
    "ws_components_spec": [
        (None, 1.0, "P6_BASELINE_15Y", SRC_B6_BASELINE_15Y),
    ],
    "wd_components_spec": [
        (None, 1.0, "Baseline_V2", SRC_BASELINE),
    ],
}

# ── Preset N — MEGA wb + B6_BASELINE_15Y ws + V2 wd.
#   Best of each world: MEGA BULL + B6 SIDE + V2 DEF.
PRESETS["N"] = {
    "tag": "P7_STITCH_N",
    "wb_src":   None,                  "wb_label": "P7_V2_MEGA",
    "ws_src":   SRC_B6_BASELINE_15Y,   "ws_label": "P6_BASELINE_15Y",
    "wd_src":   SRC_BASELINE,          "wd_label": "Baseline_V2",
    "_wb_resolve": "P7_V2_MEGA",
}


# ── V2 original member sources ────────────────────────────────────────
SRC_P2_BATCH11 = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P2_BATCH11_20260406_043415.npz",
)
SRC_E2E = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_E2E_20260413_235043.npz",
)
SRC_BULL_GA_V2 = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_BULL_GA_V2_20260418_150012.npz",
)

# ── P8 sources ───────────────────────────────────────────────────────
SRC_P8_BULL_DENSE = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P8_BULL_DENSE_20260506_020833.npz",
)
SRC_P7_STITCH_N = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P7_STITCH_N_20260503_164726.npz",
)
SRC_P8_BALANCED = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P8_BALANCED_20260507_022105.npz",
)
SRC_P8_SIDE_V3 = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P8_SIDE_V3_20260504_174158.npz",
)

# ── OOS-clean sources (train_end ≤ 2024-05-31) — added 2026-05-08 ─────
SRC_P2_BATCH11_OOS = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P2_BATCH11_OOS_20260508_112505.npz",
)
SRC_P5E_FUND_BRK = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5E_SIDE_FUND_BREAKOUT_20260426_152005.npz",
)
SRC_P5D_SIDE_DEEP = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5D_SIDE_DEEP_20260426_024004.npz",
)
SRC_P5C_BALANCED = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5C_BALANCED_20260424_151512.npz",
)
SRC_P5_RETRAIN_T1b = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz",
)

# ── OOS-clean P7/P8 retrains (train_end=2024-05-31) — added 2026-05-09 ─────
# Both retrained from the original P7/P8 GA recipes but with the OOS-safe
# end_date=2024-05-31 cutoff so the post-2024-06 period is a true holdout.
#   P7_V2_NO_DEPLOY_OOS — k=6, alpha=0.5851
#     wb: MOM_6M+SMA_CROSS+HIGH_20_BREAK | ws: STOCH+WILLR+SMA_CROSS
#     wd: WILLR+LEV_DEBT_EQUITY
#   P8_BULL_DENSE_OOS — k=9, alpha=0.6026 (DENSE BULL, the missing piece)
#     wb: QUAL_ROE+MOM_12M_EX1M+SMA_CROSS+SMA50_SLOPE+BREAKOUT_252
#     ws: VWAP_ABOVE+STOCH+WILLR+SMA_CROSS
#     wd: QUAL_ROE+LEV_DEBT_EQUITY
SRC_P7_V2_NO_DEPLOY_OOS = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P7_V2_NO_DEPLOY_OOS_20260509_152907.npz",
)
SRC_P8_BULL_DENSE_OOS = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P8_BULL_DENSE_OOS_20260509_164158.npz",
)

# P12 already-built ensembles, used as parents for P13_X_PLUS_Y.
SRC_P12_X_BULL_INJ = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P12_BULL_INJ_FUNDB_ANCHOR_20260509_165605.npz",
)
SRC_P12_Y_TRIPLE_SPEC = os.path.join(
    OUTPUT_SIG_DIR,
    "frozen_signal_P12_TRIPLE_SPEC_OOS_20260509_165605.npz",
)

# ── Preset O — Triple-Specialist Stitch (Phase B, Candidate A).
#   wb ← P8_BULL_DENSE  (new BULL-specialist GA, targeting BULL_dom 38%+)
#   ws ← P7_STITCH_N    (ws slot only — its ws = P6_BASELINE_15Y, SIDE_dom 10.17%)
#   wd ← Baseline_V2    (proven DEF stability)
PRESETS["O"] = {
    "tag": "P9_TRIPLE_SPEC_A",
    "wb_src":   SRC_P8_BULL_DENSE,  "wb_label": "P8_BULL_DENSE",
    "ws_src":   SRC_P7_STITCH_N,    "ws_label": "P7_STITCH_N (ws)",
    "wd_src":   SRC_BASELINE,       "wd_label": "Baseline_V2",
}

# ── Preset P — Balanced Core + SIDE Boost (Phase B, Candidate B).
#   wb ← P8_BALANCED        (full-regime balanced specialist, k=3 wb)
#   ws ← P8_SIDE_V3         (SIDE specialist v3)
#   wd ← P8_BALANCED (wd)   (same core for DEF stability)
PRESETS["P"] = {
    "tag": "P9_BAL_SIDE_B",
    "wb_src":   SRC_P8_BALANCED,  "wb_label": "P8_BALANCED",
    "ws_src":   SRC_P8_SIDE_V3,   "ws_label": "P8_SIDE_V3",
    "wd_src":   SRC_P8_BALANCED,  "wd_label": "P8_BALANCED",
}

# ── Preset Q — L3 Equal-Weight Blend of Batch 8 (Direction A).
#   Full L3 blending: all 3 slots (wb/ws/wd) are equal-weight averaged
#   across the 3 Batch 8 signals. This mirrors V2_GOLDEN's construction
#   method and should produce a dense combined signal (k=15+).
#   Target: surpass Baseline_V2's mean CAGR while maintaining stability.
PRESETS["Q"] = {
    "tag": "P9_L3_EQUAL_Q",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "ws_components_spec": [
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "wd_components_spec": [
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
}

# ── Preset R — Cross-Era L3: V2 Core + Batch 8 (equal-weight 5-way).
#   Blends the two proven V2 original members (P2_BATCH11 anchor + E2E dense regularizer)
#   with the 3 Batch 8 signals. This keeps critical features [14,33] via P2_BATCH11
#   and full-coverage via E2E, while adding Batch 8's fresh IC-trained patterns.
PRESETS["R"] = {
    "tag": "P10_CROSS_ERA_EQ",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "ws_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "wd_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
}

# ── Preset S — Cross-Era L3: V2-Core-Heavy (V2 members 2x, B8 1x).
#   Same 5 signals but V2 original members receive double weight,
#   preserving V2's proven DNA while adding B8 diversification at half-strength.
PRESETS["S"] = {
    "tag": "P10_CROSS_ERA_V2H",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 2.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 2.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "ws_components_spec": [
        (None, 2.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 2.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "wd_components_spec": [
        (None, 2.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 2.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
}

# ── Preset T — Cross-Era L3: Full V2 Trio + Batch 8 (6-way equal).
#   Includes ALL 3 original V2 members (P2_BATCH11 + BULL_GA_V2 + E2E)
#   plus all 3 Batch 8 signals. Maximizes signal diversity.
PRESETS["T"] = {
    "tag": "P10_CROSS_ERA_FULL",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "BULL_GA_V2",     SRC_BULL_GA_V2),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "ws_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "BULL_GA_V2",     SRC_BULL_GA_V2),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
    "wd_components_spec": [
        (None, 1.0, "P2_BATCH11",     SRC_P2_BATCH11),
        (None, 1.0, "BULL_GA_V2",     SRC_BULL_GA_V2),
        (None, 1.0, "E2E",            SRC_E2E),
        (None, 1.0, "P8_SIDE_V3",     SRC_P8_SIDE_V3),
        (None, 1.0, "P8_BULL_DENSE",  SRC_P8_BULL_DENSE),
        (None, 1.0, "P8_BALANCED",    SRC_P8_BALANCED),
    ],
}


# ── Preset U — OOS-Clean L3 Equal Blend (3 strongest OOS-clean signals).
#   All members have train_end ≤ 2024-05-31, so this ensemble has a true
#   post-OOS validation window F4 (2024-06 → 2026-02).
#   Hypothesis: combining the 3 strongest OOS-clean signals diversifies
#   factor exposure (P5E_FUND_BRK = fundamentals+breakout, P5D_SIDE_DEEP =
#   trend+fundamentals, P2_BATCH11_OOS = composite-momentum) and lifts
#   ensemble performance above any individual member.
PRESETS["U"] = {
    "tag": "P11_OOS_CLEAN_L3_EQ",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
    "ws_components_spec": [
        (None, 1.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
    "wd_components_spec": [
        (None, 1.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
}

# ── Preset V — OOS-Clean L3, P5E-anchored.
#   P5E_FUND_BRK gets 2x weight (highest mean CAGR + Lift), the others 1x.
#   Mimics V2's anchor pattern but with OOS-clean members.
PRESETS["V"] = {
    "tag": "P11_OOS_CLEAN_L3_FUNDB_ANCHOR",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
    "ws_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
    "wd_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",  SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP", SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
}

# ── Preset W — OOS-Clean L3, regime-specialized.
#   Use each component for the slot it was designed/strongest for:
#     wb (BULL): P2_BATCH11_OOS (composite momentum, BULL strength)
#     ws (SIDE): P5E_FUND_BRK + P5D_SIDE_DEEP (both have value-tilt SIDE: BkPx + FCF)
#     wd (DEF):  P5E_FUND_BRK + P5D_SIDE_DEEP (LevDebtEq + ROE — quality)
#   Hypothesis: matching regime-strength to slot maximises the ensemble's
#   regime conditional performance.
PRESETS["W"] = {
    "tag": "P11_OOS_CLEAN_L3_REGIME_SPEC",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 2.0, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
        (None, 1.0, "P5E_FUND_BRK",   SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",  SRC_P5D_SIDE_DEEP),
    ],
    "ws_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",   SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",  SRC_P5D_SIDE_DEEP),
        (None, 0.5, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
    "wd_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",   SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",  SRC_P5D_SIDE_DEEP),
        (None, 0.5, "P2_BATCH11_OOS", SRC_P2_BATCH11_OOS),
    ],
}


# ── Preset X — P12 BULL-Boosted FUNDB Anchor (extends current shadow V).
#   Same as Preset V (FUNDB-anchored OOS-clean L3) but **injects
#   P8_BULL_DENSE_OOS into the wb slot** to recover the BULL-specialist
#   coverage that pure-fundamental P5E/P5D under-served. P8_BULL_DENSE_OOS
#   contributes Quality+Momentum (QUAL_ROE+MOM_12M_EX1M+SMA_CROSS+
#   SMA50_SLOPE+BREAKOUT_252), which is orthogonal to FUNDB's Book/FCF tilt.
#   ws/wd kept identical to V for direct A/B comparison.
#   train_end ≤ 2024-05-31 for every member → OOS-clean.
PRESETS["X"] = {
    "tag": "P12_BULL_INJ_FUNDB_ANCHOR",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 2.0, "P8_BULL_DENSE_OOS", SRC_P8_BULL_DENSE_OOS),
        (None, 2.0, "P5E_FUND_BRK",      SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",     SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS",    SRC_P2_BATCH11_OOS),
    ],
    "ws_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",      SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",     SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS",    SRC_P2_BATCH11_OOS),
    ],
    "wd_components_spec": [
        (None, 2.0, "P5E_FUND_BRK",      SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",     SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P2_BATCH11_OOS",    SRC_P2_BATCH11_OOS),
    ],
}

# ── Preset Y — P12 Triple-Specialist OOS-clean (P9_TRIPLE_SPEC_A re-compose).
#   Mirrors P9_TRIPLE_SPEC_A's recipe (BULL specialist + SIDE specialist +
#   composite DEF) but every member is OOS-clean (train_end ≤ 2024-05-31).
#   This is the "P9 1순위" rebuild flagged by the P9/P10 audit (2026-05-08).
#     wb (BULL) ← P8_BULL_DENSE_OOS  (k=9, dense quality+momentum BULL)
#     ws (SIDE) ← P5E_FUND_BRK       (best OOS SIDE specialist by CAGR/Lift)
#     wd (DEF)  ← P2_BATCH11_OOS     (OOS-clean composite, broad coverage)
#   Single-source-per-slot stitch (not weighted average) — keeps the
#   "specialist purity" semantic that made P9_TRIPLE_SPEC_A pass V2 hard
#   gates in surge analysis.
PRESETS["Y"] = {
    "tag": "P12_TRIPLE_SPEC_OOS",
    "wb_src":   SRC_P8_BULL_DENSE_OOS, "wb_label": "P8_BULL_DENSE_OOS",
    "ws_src":   SRC_P5E_FUND_BRK,      "ws_label": "P5E_FUND_BRK",
    "wd_src":   SRC_P2_BATCH11_OOS,    "wd_label": "P2_BATCH11_OOS",
}

# ── Preset Z — P12 Full OOS-clean L3 (5-way equal blend).
#   Diversification ceiling: averages every available OOS-clean leaf with
#   equal weight across all three slots. If this beats individual leaves
#   on multiple fold-sets, it confirms ensemble alpha-stacking still
#   produces a net win in the OOS-clean regime.
PRESETS["Z"] = {
    "tag": "P12_FULL_OOS_L3",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P2_BATCH11_OOS",      SRC_P2_BATCH11_OOS),
        (None, 1.0, "P5E_FUND_BRK",        SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",       SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P7_V2_NO_DEPLOY_OOS", SRC_P7_V2_NO_DEPLOY_OOS),
        (None, 1.0, "P8_BULL_DENSE_OOS",   SRC_P8_BULL_DENSE_OOS),
    ],
    "ws_components_spec": [
        (None, 1.0, "P2_BATCH11_OOS",      SRC_P2_BATCH11_OOS),
        (None, 1.0, "P5E_FUND_BRK",        SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",       SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P7_V2_NO_DEPLOY_OOS", SRC_P7_V2_NO_DEPLOY_OOS),
        (None, 1.0, "P8_BULL_DENSE_OOS",   SRC_P8_BULL_DENSE_OOS),
    ],
    "wd_components_spec": [
        (None, 1.0, "P2_BATCH11_OOS",      SRC_P2_BATCH11_OOS),
        (None, 1.0, "P5E_FUND_BRK",        SRC_P5E_FUND_BRK),
        (None, 1.0, "P5D_SIDE_DEEP",       SRC_P5D_SIDE_DEEP),
        (None, 1.0, "P7_V2_NO_DEPLOY_OOS", SRC_P7_V2_NO_DEPLOY_OOS),
        (None, 1.0, "P8_BULL_DENSE_OOS",   SRC_P8_BULL_DENSE_OOS),
    ],
}

# ── Preset XY — P13 X+Y blend (added 2026-05-09).
#   50:50 weighted average of the two strongest P12 candidates per slot:
#     X = P12_BULL_INJ_FUNDB_ANCHOR (BULL-boosted, F4 CAGR rank 1)
#     Y = P12_TRIPLE_SPEC_OOS       (3/3 fold-set hardgate pass, robust)
#   Goal: combine X's BULL-phase upside with Y's cross-regime stability.
#   Downstream evaluation (step_d 3 fold-sets + surge) is left for later.
PRESETS["XY"] = {
    "tag": "P13_X_PLUS_Y",
    "all_slots_mode": True,
    "wb_components_spec": [
        (None, 1.0, "P12_BULL_INJ_FUNDB_ANCHOR", SRC_P12_X_BULL_INJ),
        (None, 1.0, "P12_TRIPLE_SPEC_OOS",       SRC_P12_Y_TRIPLE_SPEC),
    ],
    "ws_components_spec": [
        (None, 1.0, "P12_BULL_INJ_FUNDB_ANCHOR", SRC_P12_X_BULL_INJ),
        (None, 1.0, "P12_TRIPLE_SPEC_OOS",       SRC_P12_Y_TRIPLE_SPEC),
    ],
    "wd_components_spec": [
        (None, 1.0, "P12_BULL_INJ_FUNDB_ANCHOR", SRC_P12_X_BULL_INJ),
        (None, 1.0, "P12_TRIPLE_SPEC_OOS",       SRC_P12_Y_TRIPLE_SPEC),
    ],
}


def _resolve_components_spec(
    spec: Sequence[Tuple[Optional[str], float, str, Optional[str]]],
) -> List[Tuple[str, float, str]]:
    """Resolve a ``*_components_spec`` into concrete ``(path, weight, label)``
    tuples by globbing for the latest match when an explicit path is not provided.
    Works for all slots (wb/ws/wd), generalizing the original Preset-E resolver.
    """
    resolved: List[Tuple[str, float, str]] = []
    for tag_prefix, weight, label, explicit_path in spec:
        if explicit_path and os.path.exists(explicit_path):
            resolved.append((explicit_path, float(weight), label))
            continue
        if tag_prefix is None:
            resolved.append(("", float(weight), label))
            continue
        latest = _resolve_latest(tag_prefix)
        if latest is None:
            print(f"  [warn] component missing — no glob match for "
                  f"frozen_signal_{tag_prefix}_*.npz (label={label})")
            resolved.append(("", float(weight), label))
            continue
        resolved.append((latest, float(weight), label))
    return resolved


def _resolve_ws_components_for_preset_e(
    spec: Sequence[Tuple[str, float, str, Optional[str]]],
) -> List[Tuple[str, float, str]]:
    """Resolve the ``ws_components_spec`` of preset E into concrete
    ``(path, weight, label)`` tuples by globbing for the latest match
    when an explicit path is not provided.
    """
    resolved: List[Tuple[str, float, str]] = []
    for tag_prefix, weight, label, explicit_path in spec:
        if explicit_path and os.path.exists(explicit_path):
            resolved.append((explicit_path, float(weight), label))
            continue
        latest = _resolve_latest(tag_prefix)
        if latest is None:
            print(f"  [warn] preset E component missing — no glob match for "
                  f"frozen_signal_{tag_prefix}_*.npz (label={label})")
            resolved.append(("", float(weight), label))
            continue
        resolved.append((latest, float(weight), label))
    return resolved


def _print_slot_components(slot_name: str, recipe: Dict, fallback_label: Optional[str] = None, fallback_src: Optional[str] = None):
    key = f"{slot_name}_components"
    if key in recipe:
        print(f"    {slot_name}  ← weighted average across components:")
        for comp in recipe[key]:
            nz_key = "weight_normed" if "weight_normed" in comp else "weight_input"
            label_key = "label"
            path_key = "path"
            nz_count = comp.get("nonzero_in", comp.get(f"{slot_name}_nonzero_in", "?"))
            print(f"            w={comp[nz_key]:.3f}  "
                  f"{comp[label_key]:<22s}  ({comp[path_key]})  k={nz_count}")
        skipped_key = f"{slot_name}_components_skipped"
        if recipe.get(skipped_key):
            for skip in recipe[skipped_key]:
                print(f"            [skipped] {skip}")
    elif fallback_label and fallback_src:
        print(f"    {slot_name}  ← {fallback_label:<22s}  ({os.path.basename(fallback_src)})")


def _print_diag(preset_key: str, preset: Dict[str, Any], signal: Dict[str, Any]) -> None:
    s = signal["signal_summary"]
    recipe = s.get("recipe", {})
    print()
    print("─" * 60)
    print(f"  PRESET {preset_key}  →  {preset['tag']}")
    print("─" * 60)

    if preset.get("all_slots_mode"):
        _print_slot_components("wb", recipe)
        _print_slot_components("ws", recipe)
        _print_slot_components("wd", recipe)
    else:
        print(f"    wb  ← {preset['wb_label']:<22s}  ({os.path.basename(preset['wb_src'])})")
        if preset.get("ws_mode") == "weighted_avg":
            _print_slot_components("ws", recipe)
        else:
            print(f"    ws  ← {preset['ws_label']:<22s}  ({os.path.basename(preset['ws_src'])})")
        print(f"    wd  ← {preset['wd_label']:<22s}  ({os.path.basename(preset['wd_src'])})")

    print(f"    mask (union)    : {s['k_new_mask']:3d}")
    print(f"    wb non-zero     : {s['wb_nonzero']:3d}  |  Σ|wb| = {s['sum_abs_wb']:.3f}")
    print(f"    ws non-zero     : {s['ws_nonzero']:3d}  |  Σ|ws| = {s['sum_abs_ws']:.3f}")
    print(f"    wd non-zero     : {s['wd_nonzero']:3d}  |  Σ|wd| = {s['sum_abs_wd']:.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 Regime-Composite Ensemble Builder")
    parser.add_argument(
        "--preset", default="D",
        choices=("A", "B", "C", "D", "E", "F", "G", "H", "I",
                 "J", "K", "L", "M", "N", "O", "P", "Q",
                 "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
                 "XY",
                 "AB", "CD", "FG", "HI", "JKLMN", "OP", "OPQ",
                 "RST", "UVW", "XYZ", "P12", "P13", "all"),
        help="Which preset(s) to build. F/G = Phase 1 Quick Win; "
             "H/I = Phase 3 L3 ensemble; J-N = regime-stitch combos; "
             "O/P = P9 stitched ensembles; Q = P9 L3 equal-weight blend; "
             "R/S/T = P10 Cross-Era L3 blends; U/V/W = P11 OOS-clean L3 "
             "(BATCH11_OOS+P5E+P5D); X/Y/Z = P12 OOS-clean (incl. P7/P8 "
             "OOS retrains). Aliases UVW/XYZ/P12 build the corresponding "
             "trio.",
    )
    parser.add_argument(
        "--ws-weight", action="append", default=[],
        help="Override Preset E ws-component weight via tag_prefix=weight "
             "(e.g. --ws-weight P5D_SIDE_DEEP=0.30 --ws-weight P5E_SIDE_TECH=0.35). "
             "Unspecified components keep their default. Weights are "
             "renormalized to sum=1.",
    )
    args = parser.parse_args()

    _multi_map = {
        "all":   list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "OPQ":   list("OPQ"),
        "RST":   list("RST"),
        "OP":    list("OP"),
        "AB":    list("AB"),
        "CD":    list("CD"),
        "FG":    list("FG"),
        "HI":    list("HI"),
        "JKLMN": list("JKLMN"),
        "UVW":   list("UVW"),
        "XYZ":   list("XYZ"),
        "P12":   list("XYZ"),
        "P13":   ["XY"],
    }
    presets_to_run = _multi_map.get(args.preset, [args.preset])

    weight_overrides: Dict[str, float] = {}
    for tok in args.ws_weight:
        if "=" not in tok:
            print(f"  [warn] ignoring malformed --ws-weight {tok!r} (expected tag=weight)")
            continue
        k, v = tok.split("=", 1)
        try:
            weight_overrides[k.strip()] = float(v)
        except ValueError:
            print(f"  [warn] ignoring non-numeric weight in {tok!r}")

    print("[P2 Ensemble Composer]")
    print(f"  preset(s): {', '.join(presets_to_run)}")
    print(f"  source    T1b_INJ    : {os.path.basename(SRC_T1B_INJ)}")
    print(f"  source    BULL_FREE  : {os.path.basename(SRC_BULL_FREE)}")
    print(f"  source    DEF_HEAVY  : {os.path.basename(SRC_DEF_HEAVY)}")
    print(f"  source    SIDE_PURE  : {os.path.basename(SRC_SIDE_PURE)}")
    print(f"  source    SIDE_DEEP  : {os.path.basename(SRC_SIDE_DEEP)}")
    print(f"  source    SIDE_WIN   : {os.path.basename(SRC_SIDE_WIN)}")
    e_tech = _resolve_latest("P5E_SIDE_TECH")
    e_fund = _resolve_latest("P5E_SIDE_FUND_BREAKOUT")
    print(f"  source    SIDE_TECH  (B5): {os.path.basename(e_tech) if e_tech else '(not yet generated)'}")
    print(f"  source    SIDE_FUNDB (B5): {os.path.basename(e_fund) if e_fund else '(not yet generated)'}")
    print(f"  source    BASELINE   : {os.path.basename(SRC_BASELINE)}")
    print(f"  source    B6_BL_15Y  : {os.path.basename(SRC_B6_BASELINE_15Y)}")
    print(f"  source    B6_ST_15Y  : {os.path.basename(SRC_B6_SIDE_TECH_15Y)}")
    print(f"  source    B6_SF_15Y  : {os.path.basename(SRC_B6_SIDE_FUND_15Y)}")

    out_paths = []
    for key in presets_to_run:
        preset = dict(PRESETS[key])
        if "_wb_resolve" in preset:
            resolved = _resolve_latest(preset["_wb_resolve"])
            if resolved is None:
                print(f"  [ERROR] preset {key}: cannot resolve wb for {preset['_wb_resolve']}")
                continue
            preset["wb_src"] = resolved
        if preset.get("all_slots_mode"):
            wb_comps = _resolve_components_spec(preset["wb_components_spec"])
            ws_comps = _resolve_components_spec(preset["ws_components_spec"])
            wd_comps = _resolve_components_spec(preset["wd_components_spec"])
            signal = build_ensemble_weighted_all_slots(
                wb_components=wb_comps,
                ws_components=ws_comps,
                wd_components=wd_comps,
            )
        elif preset.get("ws_mode") == "weighted_avg":
            spec = preset["ws_components_spec"]
            if weight_overrides:
                spec = [
                    (tag, weight_overrides.get(tag, w), label, path)
                    for (tag, w, label, path) in spec
                ]
            ws_components = _resolve_ws_components_for_preset_e(spec)
            signal = build_ensemble_weighted_ws(
                wb_src_path=preset["wb_src"], ws_components=ws_components, wd_src_path=preset["wd_src"],
                wb_src_label=preset["wb_label"], wd_src_label=preset["wd_label"],
            )
        else:
            signal = build_ensemble(
                wb_src_path=preset["wb_src"], ws_src_path=preset["ws_src"], wd_src_path=preset["wd_src"],
                wb_src_label=preset["wb_label"], ws_src_label=preset["ws_label"], wd_src_label=preset["wd_label"],
            )
        _print_diag(key, preset, signal)
        out_path = save_signal(signal, tag=preset["tag"])
        print(f"    [saved] {out_path}")
        out_paths.append(out_path)

    print()
    print("=" * 60)
    print("  Done.")
    print("=" * 60)
    for p in out_paths:
        print(f"    {p}")
    print()
    print("Next:")
    print("  1) Register the paths in phase3/tests/step_d_walk_forward.py (SIGNALS).")
    print("  2) Run 6-fold walk-forward (T26 or CLI) for the 6-signal compare.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
