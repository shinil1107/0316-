"""P5b — Regime-conditional vol-target follow-up.

P5 found that flat vol-targeting hurts V2's DEF alpha (+108% AnnRet → +96%).
This script tests whether *enabling vol-target only in BULL/SIDE* (or
BULL only) preserves the DEF alpha while still dampening some
drawdowns.

Implementation note
-------------------
``resolve_strategy(strat_base, regime)`` already merges
``strat_base["regime_overrides"][regime]`` on top of the base dict at
each rebalance. Putting ``vol_target`` inside a regime override engages
the overlay only on rebal days where that regime is active.

Configurations
~~~~~~~~~~~~~~
- `off`               : reference
- `vt_BS_18`          : BULL+SIDE target 18%, DEF off
- `vt_BS_20`          : BULL+SIDE target 20%, DEF off
- `vt_S_18`           : SIDE only target 18% (BULL & DEF off)
- `vt_BULL_22`        : BULL only target 22% (SIDE & DEF off)
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import copy
import glob
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3 import simulator  # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg, _load_vix, _make_strategy,
)

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
PACK_START_STR = "2011-01-03"
PACK_END_STR = "2026-02-27"
DEFAULT_SIGNAL = os.path.join(
    OUTPUT_SIG_DIR, "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
)
BUY_GRACE_DAYS = 3


def _vt_block(target: float, lookback: int = 30, min_scale: float = 0.30) -> Dict[str, Any]:
    return {
        "enabled": True,
        "annual_target": target,
        "lookback_days": lookback,
        "min_scale": min_scale,
        "max_scale": 1.0,
    }


# (tag, regime_overrides_patch_for_vol_target)
DEFAULT_CONFIGS: List[Tuple[str, Dict[str, Dict[str, Any]] | None]] = [
    ("off",          None),
    ("vt_BS_18",     {"BULL": _vt_block(0.18), "SIDE": _vt_block(0.18)}),
    ("vt_BS_20",     {"BULL": _vt_block(0.20), "SIDE": _vt_block(0.20)}),
    ("vt_S_18",      {"SIDE": _vt_block(0.18)}),
    ("vt_BULL_22",   {"BULL": _vt_block(0.22)}),
]


def _make_strat(per_regime_vt: Dict[str, Dict[str, Any]] | None) -> Dict[str, Any]:
    strat = _make_strategy()
    if BUY_GRACE_DAYS > 0:
        strat["buy_grace_days"] = int(BUY_GRACE_DAYS)
    if per_regime_vt:
        rovr = copy.deepcopy(strat.get("regime_overrides", {}))
        for rg, vt in per_regime_vt.items():
            rovr.setdefault(rg, {})
            rovr[rg]["vol_target"] = copy.deepcopy(vt)
        strat["regime_overrides"] = rovr
    return strat


def _pick_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(f"No pack found")
    p = candidates[0]
    parts = os.path.splitext(os.path.basename(p))[0].split("_")
    return p, parts[-2], parts[-1]


def _run_one(tag, vt_patch, signal_path, cfg, pack, vc, vr, vs, tc, sd, ed):
    print()
    print("=" * 72)
    print(f"  RUN  {tag}")
    print(f"  per-regime vt = {vt_patch}")
    print("=" * 72)
    signal = load_frozen_signal(signal_path)
    strat = _make_strat(vt_patch)
    t0 = time.time()
    res = simulator.run_simulation(
        engine=engine, cfg=cfg, pack=pack, signal=signal,
        vix_close_by_date=vc, vix_regime_by_date=vr,
        initial_capital=100000.0, daily_buy_limit=1000.0,
        strategy_conf=strat, trigger_conf=tc,
        rebalance_mode="daily",
        commission_bps=10.0, slippage_bps=5.0,
        start_date=sd, end_date=ed,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vs,
    )
    elapsed = time.time() - t0
    m = res["metrics"]; rb = m.get("regime_breakdown", {}) or {}
    print(f"  CAGR={m.get('CAGR',0)*100:+.2f}%  Sharpe={m.get('Net_Sharpe',0):.3f}  "
          f"MDD={m.get('Max_Drawdown',0)*100:.2f}%  Calmar={m.get('Calmar_Ratio',0):.3f}  "
          f"Comm={m.get('Commission_Pct',0):.2f}%")
    print(f"  vt: total={int(m.get('vol_target_rebal_days_total',0))} "
          f"warmed={int(m.get('vol_target_rebal_days_warmed',0))} "
          f"engaged={int(m.get('vol_target_rebal_days_engaged',0))} "
          f"scale_mean={m.get('vol_target_scale_mean',1.0):.3f}")
    print(f"  [Regime] BULL={float(rb.get('BULL',{}).get('AnnRet',0))*100:+.2f}%  "
          f"SIDE={float(rb.get('SIDE',{}).get('AnnRet',0))*100:+.2f}%  "
          f"DEF={float(rb.get('DEF',{}).get('AnnRet',0))*100:+.2f}%  ({elapsed:.1f}s)")
    return {
        "tag": tag, "vt_patch": vt_patch, "elapsed_sec": round(elapsed, 1),
        "metrics": {k: (float(v) if isinstance(v, float) else
                        (int(v) if isinstance(v, (int, np.integer)) else v))
                    for k, v in m.items() if not isinstance(v, dict)},
        "regime_breakdown": {rg: {k: (int(v) if isinstance(v, (int, np.integer)) else float(v))
                                  for k, v in (rb.get(rg) or {}).items()}
                             for rg in ("BULL", "SIDE", "DEF")},
    }


def _summary(rows):
    by_tag = {r["tag"]: r for r in rows}
    ref = by_tag.get("off", rows[0])
    print()
    print("=" * 110)
    print(f"  Δ vs off")
    print("-" * 110)
    print(f"  {'tag':<14s} {'CAGR':>9s} {'Δ':>7s} {'Sharpe':>7s} "
          f"{'MDD':>7s} {'ΔMDD':>7s} {'Calmar':>7s} {'Comm':>7s} {'Final $':>14s}")
    print("-" * 110)
    for r in rows:
        m = r["metrics"]
        d_cagr = (m["CAGR"] - ref["metrics"]["CAGR"]) * 100
        d_mdd = (m["Max_Drawdown"] - ref["metrics"]["Max_Drawdown"]) * 100
        print(f"  {r['tag']:<14s} {m['CAGR']*100:>+8.2f}% {d_cagr:>+6.2f}% "
              f"{m['Net_Sharpe']:>7.3f} {m['Max_Drawdown']*100:>6.2f}% {d_mdd:>+6.2f}% "
              f"{m['Calmar_Ratio']:>7.3f} {m['Commission_Pct']:>6.2f}% "
              f"${m['Final_Value']:>13,.0f}")
    print("=" * 110)
    print()
    print("  Regime AnnRet (Δ vs off)")
    print("-" * 100)
    base_b = ref["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = ref["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = ref["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab, aS, ad = rb["BULL"]["AnnRet"], rb["SIDE"]["AnnRet"], rb["DEF"]["AnnRet"]
        print(f"  {r['tag']:<14s}  BULL {ab*100:>+7.2f}% / {(ab-base_b)*100:>+5.2f}    "
              f"SIDE {aS*100:>+7.2f}% / {(aS-base_s)*100:>+5.2f}    "
              f"DEF {ad*100:>+7.2f}% / {(ad-base_d)*100:>+5.2f}")
    print("=" * 100)


def main():
    print("=" * 72)
    print("  P5b — Regime-Conditional Vol-Target")
    print("=" * 72)
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    save_dir = conf["paths"]["output_dir"]
    pack_path, pack_start, pack_end = _pick_pack(save_dir)
    print(f"[pack] {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")
    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] loaded {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")
    vc, vr, vs = _load_vix(cfg, pack_start, pack_end)
    tc = conf.get("triggers", {})
    sd, ed = "2012-01-01", PACK_END_STR

    rows = []
    t_total = time.time()
    for tag, vt in DEFAULT_CONFIGS:
        rows.append(_run_one(tag, vt, DEFAULT_SIGNAL, cfg, pack, vc, vr, vs, tc, sd, ed))
    elapsed = time.time() - t_total
    _summary(rows)
    print(f"  total elapsed: {elapsed:.1f}s ({elapsed/60:.1f} min)")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(DOCS_DIR, f"p5b_vol_target_regime_aware_{stamp}.json")
    with open(out, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pack_basename": os.path.basename(pack_path),
            "window_start": sd, "window_end": ed,
            "buy_grace_days": BUY_GRACE_DAYS,
            "rows": rows,
        }, f, indent=2, default=str)
    print(f"[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
