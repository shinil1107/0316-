"""Step B — Pre-Phase 2 Patch sanity check.

After F11 / F1 / F4 / S1 are applied in cell 0, re-evaluate the current live
V2 signal to confirm:

  1. The fit function still runs end-to-end and produces sensible metrics.
  2. The entropy_bonus contribution no longer dominates the base alpha
     signal (F1 audit finding).
  3. Spread changes modestly when the tradable mask is enforced (F11
     audit finding); an extreme drop (>30 %) would indicate that V2 was
     riding illiquid names and needs de-risking.
  4. The per-regime entropy / conc_pen (F4) numerically differ from the
     previous w_avg collapse — without this, F4 had no effect.

Reference numbers from the pre-patch diagnostics (phase12_signal_health
_report.md and first T1 verify run) are used for qualitative comparison.

This script does NOT replace Step C (Phase 5 retrain gate). It only
verifies that the patch did what it was supposed to do.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
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


def _get_pack(cfg) -> Tuple[dict, str, str]:
    import glob
    pattern = os.path.join(cfg.save_dir, f"{cfg.precompute_npz_prefix}_*.npz")
    all_packs = glob.glob(pattern)
    # Pick the largest-span pack ending at the latest date — gives a more
    # representative fit signal than the tiny 1-year daily-runner pack.
    def _parse(p: str) -> Tuple[str, str]:
        stem = os.path.splitext(os.path.basename(p))[0]
        parts = stem.split("_")
        return parts[-2], parts[-1]

    def _span_days(p: str) -> int:
        s, e = _parse(p)
        from datetime import datetime
        ds = datetime.strptime(s, "%Y-%m-%d")
        de = datetime.strptime(e, "%Y-%m-%d")
        return (de - ds).days

    # Reuse the same pack that Step A baseline benchmark picked
    # (precompute_qresearch_v4_12_2017-01-03_2026-04-17.npz) so F1/F4/F11
    # diagnostics are comparable against the frozen baseline metrics. It
    # covers the OOS window (2024-06 → 2026-04) with decent tradable coverage.
    PREFERRED_END = "2026-04-17"
    PREFERRED_START = "2017-01-03"
    preferred = [p for p in all_packs
                 if _parse(p) == (PREFERRED_START, PREFERRED_END)]
    if preferred:
        chosen = preferred[0]
    else:
        # Fallback: latest-end pack whose end ≥ 2026-04-17 and span ≥ 5y.
        candidates = []
        for p in all_packs:
            s, e = _parse(p)
            if e >= PREFERRED_END and _span_days(p) >= 365 * 5:
                candidates.append(p)
        if not candidates:
            candidates = all_packs
        candidates.sort(key=lambda p: (_parse(p)[1], _span_days(p)), reverse=True)
        chosen = candidates[0]
    s, e = _parse(chosen)
    pack = engine.load_precompute_panel(cfg, s, e)
    if pack is None:
        raise RuntimeError(f"Failed to load pack {chosen}")
    return pack, s, e


def _evaluate(cfg, pack, sig) -> Tuple[float, dict]:
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


def _explore_meta(meta: Dict) -> Dict[str, float]:
    def _g(k, default=np.nan):
        v = meta.get(k, default)
        try:
            return float(v)
        except Exception:
            return default

    return {
        "regime_ic1": _g("regime_weighted_ic_1m"),
        "regime_ic3": _g("regime_weighted_ic_3m"),
        "regime_spmix": _g("regime_weighted_spread_mix"),
        "mic1": _g("mean_ic_1m"),
        "mic3": _g("mean_ic_3m"),
        "mspmix": _g("mean_spread_mix"),
        "entropy": _g("entropy"),
        "maxw": _g("maxw"),
        "conc_pen": _g("conc_pen"),
        "k_pen": _g("k_pen"),
        "k_used": _g("k_used"),
        "corr_pen": _g("corr_pen"),
        "bull_floor_pen": _g("bull_floor_pen"),
        "bull_spread_bonus": _g("bull_spread_bonus"),
    }


def _compute_base(meta_scalars: Dict[str, float], cfg) -> float:
    ic1 = meta_scalars["regime_ic1"] if np.isfinite(meta_scalars["regime_ic1"]) else meta_scalars["mic1"]
    ic3 = meta_scalars["regime_ic3"] if np.isfinite(meta_scalars["regime_ic3"]) else meta_scalars["mic3"]
    sp = meta_scalars["regime_spmix"] if np.isfinite(meta_scalars["regime_spmix"]) else meta_scalars["mspmix"]
    base = 0.0
    if np.isfinite(ic1):
        base += float(cfg.w_ic1) * ic1
    if np.isfinite(ic3):
        base += float(cfg.w_ic3) * ic3
    if np.isfinite(sp):
        base += float(cfg.w_spread) * sp
    return base


def main():
    print("=" * 72)
    print("Step B — Pre-Phase 2 Patch Sanity Check")
    print("=" * 72)

    from phase3.daily_runner import build_engine_cfg
    import yaml
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    cfg = build_engine_cfg(conf)

    # Show which patches are in effect
    print("\n[patches in Config]")
    print(f"  entropy_bonus                   = {cfg.entropy_bonus}  (was 0.08 pre-F1)")
    print(f"  conc_penalty                    = {cfg.conc_penalty}")
    print(f"  enable_cs_rank_features default = {engine.Config().enable_cs_rank_features}  (was False pre-S1)")
    print(f"  meta_entropy_bonus_candidates   = {cfg.meta_entropy_bonus_candidates}")

    pack, pack_start, pack_end = _get_pack(cfg)
    D, N = pack["close"].shape
    K = pack["feat"].shape[0]
    print(f"\n[pack] {os.path.basename(pack_start)} span = {pack_start} → {pack_end}")
    print(f"       shape D={D}  N={N}  K={K}")

    sig = _load_signal(SIGNAL_PATH)
    print(f"[sig ] V2_GOLDEN_ENS_L3_v1  k_active={int(sig['mask'].sum())}")

    # Canonical run — patches active
    print("\n── Run (post-patch) ─────────────────────────────────")
    fit, meta = _evaluate(cfg, pack, sig)
    scalars = _explore_meta(meta)
    base = _compute_base(scalars, cfg)
    entropy_contrib = float(cfg.entropy_bonus) * scalars["entropy"]

    print(f"  fit                  = {fit:+.6f}")
    print(f"  base (IC+Spread)     = {base:+.6f}")
    print(f"    w_ic1={cfg.w_ic1}  regime_ic1={scalars['regime_ic1']:+.6f}  mic1={scalars['mic1']:+.6f}")
    print(f"    w_ic3={cfg.w_ic3}  regime_ic3={scalars['regime_ic3']:+.6f}  mic3={scalars['mic3']:+.6f}")
    print(f"    w_spread={cfg.w_spread}  regime_spmix={scalars['regime_spmix']:+.6f}  mspmix={scalars['mspmix']:+.6f}")
    print(f"  entropy              = {scalars['entropy']:.6f}  (per-regime weighted, F4)")
    print(f"  entropy_contrib      = {cfg.entropy_bonus} × {scalars['entropy']:.4f} = {entropy_contrib:+.6f}")
    print(f"  conc_pen             = {scalars['conc_pen']:+.6f}  (per-regime weighted, F4)")
    print(f"  maxw (weighted blend)= {scalars['maxw']:.6f}")
    print(f"  k_pen                = {scalars['k_pen']:+.6f}")
    print(f"  corr_pen             = {scalars['corr_pen']:+.6f}")
    print(f"  bull_floor_pen       = {scalars['bull_floor_pen']:+.6f}")
    print(f"  bull_spread_bonus    = {scalars['bull_spread_bonus']:+.6f}")

    # F1 evaluation: entropy_contrib vs base magnitude.
    base_abs = abs(base) if abs(base) > 1e-9 else 1e-9
    entropy_over_base = abs(entropy_contrib) / base_abs
    print(f"\n[F1] |entropy_contrib| / |base| = {entropy_over_base:.2f}")
    print(f"     Audit finding (pre-patch): ≈ 10× (entropy_bonus=0.08 × entropy=0.9 / base≈0.007)")
    print(f"     Expected post-patch       : ≈ 5× (0.04 × entropy / base)")
    if entropy_over_base <= 6.0:
        print("     → PASS: bonus scale within one order of base.")
    elif entropy_over_base <= 10.0:
        print("     → WARN: still larger than base but reduced; re-inspect after Phase 5.")
    else:
        print("     → FAIL: entropy still dominates; consider dropping to 0.02 immediately.")

    # F4 evaluation: confirm per-regime aggregation changed something.
    # Compare with what the OLD w_avg-based entropy WOULD have produced on
    # this signal. (Synthetic w_avg computation for diagnostic only.)
    from phase3.engine_loader import engine as eng
    _mask_union = np.asarray(sig["mask"] | sig["mask"] | sig["mask"], dtype=bool)  # V2 shares mask
    _w_avg = (np.asarray(sig["wb"]) + np.asarray(sig["ws"]) + np.asarray(sig["wd"])) / 3.0
    _div_old = eng._diversity_terms(_mask_union, _w_avg)
    old_entropy = float(_div_old["entropy"])
    print(f"\n[F4] entropy (per-regime weighted, post-patch)  = {scalars['entropy']:.6f}")
    print(f"     entropy (old w_avg single-call reference)   = {old_entropy:.6f}")
    delta_entropy = scalars["entropy"] - old_entropy
    print(f"     Δ = {delta_entropy:+.6f}")
    if abs(delta_entropy) < 1e-9:
        print("     → NOTE: V2 signal reuses the same mask/weights across regimes,")
        print("             so per-regime vs w_avg converge to the same value. This")
        print("             is expected for non-regime-specialized signals; F4 will")
        print("             show impact once retrains produce regime-specialized masks.")
    else:
        print(f"     → F4 patch numerically active (Δ={delta_entropy:+.4f}).")

    # F11 evaluation: quantify the Spread change due to tradable mask.
    # We re-read the dates loop and compute Spread with vs without the mask
    # on the same pack/signal. Fast path: just the first 30 eval dates.
    dates = list(pack["dates"])
    feat = pack["feat"]
    tradable = np.asarray(pack["tradable"], dtype=bool)
    fwd1 = np.asarray(pack["fwd1"])
    close = np.asarray(pack["close"])
    # Compose a simplified svec via Config.score_vector_for_day - but this
    # calls the same cs_rank logic. To decouple, use raw svec from feat × w.
    # Actually the cleanest path: call score_vector_for_day for a handful of
    # days and measure _calc_spread with and without tradable.
    q = float(cfg.top_quantile)

    # Use the LAST 30 usable dates — early days of some packs have tradable=0
    # during burn-in and would yield empty samples.
    horizon = int(cfg.horizon_3m)
    end_di = len(dates) - horizon - 1
    n_sample = min(30, end_di)
    start_di = max(end_di - n_sample, 0)
    spreads_with = []
    spreads_without = []
    for di in range(start_di, end_di):
        svec = eng.score_vector_for_day(
            pack, di,
            sig["mask"], sig["mask"], sig["mask"],
            sig["wb"], sig["ws"], sig["wd"],
            score_regime="SIDE", cfg=cfg,
        )
        sp_without = eng._calc_spread(svec, fwd1[di], q=q)
        sp_with = eng._calc_spread(np.where(tradable[di], svec, np.nan), fwd1[di], q=q)
        if np.isfinite(sp_without):
            spreads_without.append(sp_without)
        if np.isfinite(sp_with):
            spreads_with.append(sp_with)
    mean_w = float(np.mean(spreads_with)) if spreads_with else np.nan
    mean_wo = float(np.mean(spreads_without)) if spreads_without else np.nan
    delta_pct = (mean_w - mean_wo) / abs(mean_wo) * 100 if abs(mean_wo) > 1e-9 else 0.0
    print(f"\n[F11] Spread on last {n_sample} eval dates (V2 signal, SIDE regime)")
    print(f"      without tradable mask = {mean_wo:+.6f}")
    print(f"      with    tradable mask = {mean_w:+.6f}")
    print(f"      Δ                      = {mean_w - mean_wo:+.6f}  ({delta_pct:+.1f}%)")
    # Direction matters: filter adds alpha when non-tradable names were a drag.
    if delta_pct > 10.0:
        print("      → PASS (quality-up): tradable filter IMPROVES Spread by ≥ 10%,")
        print("                         meaning non-tradable names were dragging V2 quality.")
        print("                         GA retrained under F11 should pick cleaner alphas.")
    elif delta_pct < -30.0:
        print("      → ALERT (capacity): V2 Spread drops ≥ 30% under tradable filter —")
        print("                        V2 was riding illiquid names for its edge.")
    elif delta_pct < -10.0:
        print("      → NOTE: moderate illiquid exposure in V2; Phase 5 retrain will re-balance.")
    else:
        print("      → NEUTRAL: Spread change under tradable filter < 10% in magnitude.")

    # Summary
    print("\n" + "=" * 72)
    print("Step B complete. Patches are live on next GA retrain.")
    print("Next: Phase 5 retrain under the new formula, evaluate via Step A protocol,")
    print("      promote only if 4+/6 Step C gate criteria are met.")
    print("=" * 72)


if __name__ == "__main__":
    main()
