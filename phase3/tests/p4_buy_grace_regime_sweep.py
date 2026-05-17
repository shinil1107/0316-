"""P4 — Regime-conditional buy_grace_days sweep (variant d).

Tests whether *regime-aware* grace levels can Pareto-improve over the
v1.3 production setting (`g=3` flat). Driving observation from the
scalar sweep (p3_buy_grace_sweep, 2026-04-25):

| grace | BULL Δ | SIDE Δ | DEF Δ |
|------:|-------:|-------:|------:|
| 1     | -0.59  | +0.03  |  +8.89|
| 2     | -0.35  | -0.95  | +15.18|
| 3     | -0.39  | -0.53  | +17.72|
| 5     | **-0.02** | **-2.51** | +14.07|

So **BULL is essentially indifferent to grace**, **SIDE is hurt by grace ≥ 2**,
and **DEF benefits saturate around grace = 3**. This suggests a profile
like BULL high / SIDE low / DEF moderate-high may strictly dominate the
flat g=3 setting on aggregate.

Configurations
--------------
Reference rows (re-runs of p3 for direct comparison):
  - `g0_flat`   : BULL=0, SIDE=0, DEF=0                  (legacy)
  - `g3_flat`   : BULL=3, SIDE=3, DEF=3                  (v1.3 production)

Regime-conditional candidates:
  - `RC_a`      : BULL=1, SIDE=3, DEF=5     (original hypothesis: light BULL / heavy DEF)
  - `RC_b`      : BULL=5, SIDE=3, DEF=3     (exploit "BULL grace=5 no-harm" finding)
  - `RC_c`      : BULL=3, SIDE=2, DEF=5     (protect SIDE from −0.95pp loss)
  - `RC_d`      : BULL=5, SIDE=2, DEF=5     (combined; max commission squeeze)

6 runs × ~30s each ≈ 3 min total.

Implementation note
-------------------
``resolve_strategy(strat_base, regime)`` already merges
``strat_base["regime_overrides"][regime]`` on top of the base dict. The
simulator's grace filter reads the post-resolve strat, so no engine
change is required — just inject ``buy_grace_days`` per-regime.

Usage
-----
    python3 -u phase3/tests/p4_buy_grace_regime_sweep.py
    python3 -u phase3/tests/p4_buy_grace_regime_sweep.py \
        --custom "RC_x:B=2,S=1,D=5;RC_y:B=4,S=2,D=4"
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
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
PACK_END_STR   = "2026-02-27"

DEFAULT_SIGNAL = os.path.join(
    OUTPUT_SIG_DIR, "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz",
)
DEFAULT_LEG_NAME = "Baseline_V2"


# ── Sweep configurations ─────────────────────────────────────────────
# Each entry: tag → (BULL, SIDE, DEF) grace days.
DEFAULT_CONFIGS: List[Tuple[str, Tuple[int, int, int]]] = [
    ("g0_flat",  (0, 0, 0)),
    ("g3_flat",  (3, 3, 3)),
    ("RC_a",     (1, 3, 5)),
    ("RC_b",     (5, 3, 3)),
    ("RC_c",     (3, 2, 5)),
    ("RC_d",     (5, 2, 5)),
]


def _parse_custom(custom: str) -> List[Tuple[str, Tuple[int, int, int]]]:
    """Parse e.g. ``RC_x:B=2,S=1,D=5;RC_y:B=4,S=2,D=4`` into config list."""
    out: List[Tuple[str, Tuple[int, int, int]]] = []
    for entry in custom.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(f"missing ':' in custom config: {entry}")
        tag, body = entry.split(":", 1)
        bsd = {"B": None, "S": None, "D": None}
        for tok in body.split(","):
            tok = tok.strip()
            if "=" not in tok:
                raise ValueError(f"missing '=' in custom config token: {tok}")
            k, v = tok.split("=", 1)
            k = k.strip().upper()
            if k not in bsd:
                raise ValueError(f"unknown regime key {k} (expect B/S/D)")
            bsd[k] = int(v)
        if any(v is None for v in bsd.values()):
            raise ValueError(f"custom config {tag} missing some of B/S/D")
        out.append((tag.strip(), (bsd["B"], bsd["S"], bsd["D"])))
    return out


def _make_strategy_with_regime_grace(b: int, s: int, d: int) -> Dict[str, Any]:
    """Clone the SIDE_DEF_p12 LEGACY strat and inject regime-conditional grace.

    Implementation: leaves the base ``buy_grace_days`` field absent (i.e. 0
    by default) and adds the per-regime override under
    ``regime_overrides`` so that ``resolve_strategy`` flows them in.
    """
    strat = _make_strategy()
    if "regime_overrides" not in strat:
        strat["regime_overrides"] = {}
    rovr = copy.deepcopy(strat.get("regime_overrides", {}))
    for rg, val in (("BULL", b), ("SIDE", s), ("DEF", d)):
        rovr.setdefault(rg, {})
        rovr[rg]["buy_grace_days"] = int(val)
    strat["regime_overrides"] = rovr
    return strat


def _pick_walk_forward_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(save_dir, f"precompute_qresearch_v4_12_{PACK_START_STR}_*.npz")
    candidates = sorted(glob.glob(pattern), reverse=True)
    if not candidates:
        raise RuntimeError(f"No walk-forward pack found matching {pattern}")
    p = candidates[0]
    stem = os.path.splitext(os.path.basename(p))[0]
    parts = stem.split("_")
    start, end = parts[-2], parts[-1]
    return p, start, end


def _run_one(
    tag: str, bsd: Tuple[int, int, int],
    signal_path: str, leg_name: str,
    cfg, pack,
    vix_close_map: Dict[str, float],
    vix_regime_map: Dict[str, str],
    vix_smooth_map: Dict[str, float],
    trigger_conf,
    start_d: str, end_d: str,
) -> Dict[str, Any]:
    b, s, d = bsd
    print()
    print("=" * 72)
    print(f"  RUN  {tag}  ·  BULL={b}  SIDE={s}  DEF={d}")
    print(f"  signal = {os.path.basename(signal_path)}")
    print(f"  window = {start_d} → {end_d}")
    print("=" * 72)

    signal = load_frozen_signal(signal_path)
    strat = _make_strategy_with_regime_grace(b, s, d)

    t0 = time.time()
    res = simulator.run_simulation(
        engine=engine, cfg=cfg, pack=pack, signal=signal,
        vix_close_by_date=vix_close_map,
        vix_regime_by_date=vix_regime_map,
        initial_capital=100000.0,
        daily_buy_limit=1000.0,
        strategy_conf=strat,
        trigger_conf=trigger_conf,
        rebalance_mode="daily",
        commission_bps=10.0, slippage_bps=5.0,
        start_date=start_d, end_date=end_d,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vix_smooth_map,
    )
    elapsed = time.time() - t0

    m = res["metrics"]
    rb = m.get("regime_breakdown", {}) or {}
    print(f"  CAGR      = {m.get('CAGR', 0)*100:+.2f}%   "
          f"Sharpe = {m.get('Net_Sharpe', 0):.3f}   "
          f"MDD = {m.get('Max_Drawdown', 0)*100:.2f}%")
    print(f"  Calmar    = {m.get('Calmar_Ratio', 0):.3f}    "
          f"Comm%  = {m.get('Commission_Pct', 0):.2f}%   "
          f"Final$ = ${m.get('Final_Value', 0):,.0f}")
    print(f"  filtered  buys total = {int(m.get('buy_grace_filtered_total', 0)):,}    "
          f"days w/ filter = {int(m.get('buy_grace_rebal_days_with_filter', 0)):,}")
    print(f"  elapsed   = {elapsed:.1f}s")
    print(f"  [Regime] BULL Ann = {float(rb.get('BULL', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"SIDE Ann = {float(rb.get('SIDE', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"DEF Ann = {float(rb.get('DEF', {}).get('AnnRet', 0))*100:+.2f}%")

    return {
        "tag": tag,
        "regime_grace": {"BULL": b, "SIDE": s, "DEF": d},
        "leg_name": leg_name,
        "signal_path": signal_path,
        "window_start": start_d, "window_end": end_d,
        "elapsed_sec": round(elapsed, 1),
        "metrics": {
            "CAGR":             float(m.get("CAGR", 0.0)),
            "Max_Drawdown":     float(m.get("Max_Drawdown", 0.0)),
            "Net_Sharpe":       float(m.get("Net_Sharpe", 0.0)),
            "Calmar_Ratio":     float(m.get("Calmar_Ratio", 0.0)),
            "Total_Return":     float(m.get("Total_Return", 0.0)),
            "Final_Value":      float(m.get("Final_Value", 0.0)),
            "Years":            float(m.get("Years", 0.0)),
            "Trading_Days":     int(m.get("Trading_Days", 0)),
            "Rebalance_Days":   int(m.get("Rebalance_Days", 0)),
            "Total_Commission": float(m.get("Total_Commission", 0.0)),
            "Commission_Pct":   float(m.get("Commission_Pct", 0.0)),
            "Daily_Win_Rate":   float(m.get("Daily_Win_Rate", 0.0)),
            "Monthly_Win_Rate": float(m.get("Monthly_Win_Rate", 0.0)),
            "buy_grace_filtered_total":          int(m.get("buy_grace_filtered_total", 0)),
            "buy_grace_rebal_days_with_filter":  int(m.get("buy_grace_rebal_days_with_filter", 0)),
        },
        "regime_breakdown": {
            rg: {
                k: (int(v) if isinstance(v, (int, np.integer)) else float(v))
                for k, v in (rb.get(rg) or {}).items()
            } for rg in ("BULL", "SIDE", "DEF")
        },
    }


def _print_summary(rows: List[Dict[str, Any]],
                   ref_tag: str = "g3_flat") -> None:
    if not rows:
        return
    by_tag = {r["tag"]: r for r in rows}
    ref = by_tag.get(ref_tag, rows[0])
    g0 = by_tag.get("g0_flat", ref)

    print()
    print("=" * 110)
    print(f"  HEADLINE SWEEP  (Δ vs {ref_tag} · ΔΔ vs g0_flat)")
    print("=" * 110)
    print(f"  {'tag':<10s} {'BSD':<11s} {'CAGR':>9s} {'Δref':>7s} {'ΔΔg0':>7s} "
          f"{'Sharpe':>7s} {'MDD':>7s} {'Calmar':>7s} "
          f"{'Comm%':>7s} {'Δref':>7s} {'Final $':>14s}")
    print("-" * 110)
    for r in rows:
        m = r["metrics"]
        rg = r["regime_grace"]
        bsd_str = f"{rg['BULL']}/{rg['SIDE']}/{rg['DEF']}"
        d_cagr_ref = (m["CAGR"] - ref["metrics"]["CAGR"]) * 100
        d_cagr_g0 = (m["CAGR"] - g0["metrics"]["CAGR"]) * 100
        d_comm_ref = m["Commission_Pct"] - ref["metrics"]["Commission_Pct"]
        print(
            f"  {r['tag']:<10s} {bsd_str:<11s} "
            f"{m['CAGR']*100:>+8.2f}% "
            f"{d_cagr_ref:>+6.2f}% "
            f"{d_cagr_g0:>+6.2f}% "
            f"{m['Net_Sharpe']:>7.3f} "
            f"{m['Max_Drawdown']*100:>6.2f}% "
            f"{m['Calmar_Ratio']:>7.3f} "
            f"{m['Commission_Pct']:>6.2f}% "
            f"{d_comm_ref:>+6.2f}% "
            f"${m['Final_Value']:>13,.0f}"
        )
    print("=" * 110)
    print()
    print("  Regime AnnRet (Δ vs g0_flat)")
    print("-" * 110)
    print(f"  {'tag':<10s}  {'BULL':>14s}  {'SIDE':>14s}  {'DEF':>14s}")
    base_b = g0["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = g0["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = g0["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        print(
            f"  {r['tag']:<10s}  "
            f"{ab*100:>+7.2f}% / {(ab-base_b)*100:>+5.2f}  "
            f"{aS*100:>+7.2f}% / {(aS-base_s)*100:>+5.2f}  "
            f"{ad*100:>+7.2f}% / {(ad-base_d)*100:>+5.2f}"
        )
    print("=" * 110)


def _identify_best(rows: List[Dict[str, Any]],
                   ref_tag: str = "g3_flat") -> Dict[str, Any]:
    """Identify the row that Pareto-dominates ``ref_tag`` (or comes
    closest). A candidate Pareto-dominates if:
      ΔCAGR ≥ −0.10 pp  AND  ΔComm ≤ 0  AND  ΔMDD ≤ 0  AND  ΔCalmar ≥ 0.
    Otherwise rank by a simple weighted score:
      score = (ΔCAGR) − 0.5 × ΔComm − 5 × ΔMDD + 2 × ΔCalmar
    (ΔComm/ΔMDD inverted so lower is better → multiplied by negative).
    """
    by_tag = {r["tag"]: r for r in rows}
    if ref_tag not in by_tag:
        return {"verdict": "no-ref", "ref_tag": ref_tag}

    ref = by_tag[ref_tag]["metrics"]
    pareto: List[Tuple[str, float]] = []
    scored: List[Tuple[str, float]] = []
    for r in rows:
        if r["tag"] == ref_tag:
            continue
        m = r["metrics"]
        d_cagr = (m["CAGR"] - ref["CAGR"]) * 100
        d_comm = m["Commission_Pct"] - ref["Commission_Pct"]
        d_mdd = (m["Max_Drawdown"] - ref["Max_Drawdown"]) * 100
        d_calmar = m["Calmar_Ratio"] - ref["Calmar_Ratio"]
        is_pareto = (
            d_cagr >= -0.10 and d_comm <= 0.0 and d_mdd <= 0.0 and d_calmar >= 0.0
        )
        if is_pareto:
            pareto.append((r["tag"], d_cagr - 0.5 * d_comm))
        score = d_cagr - 0.5 * d_comm - 5.0 * d_mdd + 2.0 * d_calmar
        scored.append((r["tag"], score))
    pareto.sort(key=lambda t: -t[1])
    scored.sort(key=lambda t: -t[1])
    return {
        "verdict": "pareto" if pareto else "no_pareto",
        "ref_tag": ref_tag,
        "pareto_winners": [t for t, _ in pareto],
        "score_ranked": [t for t, _ in scored[:5]],
    }


def _write_md(rows: List[Dict[str, Any]], leg_name: str,
              start_d: str, end_d: str, pack_name: str,
              best: Dict[str, Any], md_path: str) -> None:
    by_tag = {r["tag"]: r for r in rows}
    ref = by_tag.get("g3_flat", rows[0])
    g0 = by_tag.get("g0_flat", ref)
    L: List[str] = []
    L.append(f"# P4 — Regime-Conditional Buy-Grace Sweep · {leg_name}")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Signal**: {os.path.basename(rows[0]['signal_path'])}")
    L.append(f"**Window**: {start_d} → {end_d}")
    L.append(f"**Pack**: `{pack_name}`")
    L.append(f"**Protocol**: $100K · $1K/day · 10/5 bps · SIDE_DEF_p12 · daily rebal · variant=d (regime-conditional strict)")
    L.append("")
    L.append(f"**Ref (Δ)**: `g3_flat` (BSD = 3/3/3 — v1.3 production)")
    L.append(f"**ΔΔ baseline**: `g0_flat` (BSD = 0/0/0 — legacy)")
    L.append("")

    L.append("## 1. Headline")
    L.append("")
    L.append("| tag | B/S/D | CAGR | Δ ref | ΔΔ g0 | Sharpe | MDD | Calmar | Comm % | Δ Comm vs ref | Final $ |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        m = r["metrics"]; rg = r["regime_grace"]
        bsd_str = f"{rg['BULL']}/{rg['SIDE']}/{rg['DEF']}"
        d_cagr_ref = (m["CAGR"] - ref["metrics"]["CAGR"]) * 100
        d_cagr_g0 = (m["CAGR"] - g0["metrics"]["CAGR"]) * 100
        d_comm_ref = m["Commission_Pct"] - ref["metrics"]["Commission_Pct"]
        L.append(
            f"| **{r['tag']}** | {bsd_str} "
            f"| {m['CAGR']*100:+.2f}% "
            f"| {d_cagr_ref:+.2f}pp "
            f"| {d_cagr_g0:+.2f}pp "
            f"| {m['Net_Sharpe']:+.3f} "
            f"| {m['Max_Drawdown']*100:.2f}% "
            f"| {m['Calmar_Ratio']:.3f} "
            f"| {m['Commission_Pct']:.2f}% "
            f"| {d_comm_ref:+.2f}pp "
            f"| ${m['Final_Value']:,.0f} |"
        )
    L.append("")

    L.append("## 2. Regime breakdown — AnnRet (Δ vs g0_flat)")
    L.append("")
    L.append("| tag | B/S/D | BULL | Δ | SIDE | Δ | DEF | Δ |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    base_b = g0["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = g0["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = g0["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rg = r["regime_grace"]
        bsd_str = f"{rg['BULL']}/{rg['SIDE']}/{rg['DEF']}"
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        L.append(
            f"| **{r['tag']}** | {bsd_str} "
            f"| {ab*100:+.2f}% | {(ab-base_b)*100:+.2f}pp "
            f"| {aS*100:+.2f}% | {(aS-base_s)*100:+.2f}pp "
            f"| {ad*100:+.2f}% | {(ad-base_d)*100:+.2f}pp |"
        )
    L.append("")

    L.append("## 3. Verdict")
    L.append("")
    L.append(f"- **Verdict against `{best['ref_tag']}`**: `{best['verdict']}`")
    if best["verdict"] == "pareto":
        L.append(f"- **Pareto winners** (ΔCAGR ≥ −0.10pp AND ΔComm ≤ 0 AND ΔMDD ≤ 0 AND ΔCalmar ≥ 0):")
        for tag in best["pareto_winners"]:
            L.append(f"    - `{tag}`")
    L.append(f"- **Score ranking** (top 5; score = ΔCAGR − 0.5×ΔComm − 5×ΔMDD + 2×ΔCalmar):")
    for tag in best["score_ranked"]:
        L.append(f"    - `{tag}`")
    L.append("")
    L.append("---")
    L.append("")
    L.append("**Reading guide**")
    L.append("")
    L.append("- A regime-conditional config is a *meaningful* improvement over `g3_flat` only if it:")
    L.append("  (i) holds CAGR within −0.10pp of `g3_flat`,")
    L.append("  (ii) further reduces commission, **and**")
    L.append("  (iii) preserves Calmar / MDD.")
    L.append("- If no candidate Pareto-dominates `g3_flat`, the v1.3 production setting (`g=3` flat) remains optimal.")
    L.append("- `score_ranked` is a tie-breaker proxy when no strict Pareto winner exists; treat as suggestive only.")

    with open(md_path, "w") as f:
        f.write("\n".join(L))


def main() -> int:
    parser = argparse.ArgumentParser(description="P4 — regime-conditional grace sweep")
    parser.add_argument("--signal", default=DEFAULT_SIGNAL,
                        help="Signal npz path (default: Baseline_V2)")
    parser.add_argument("--leg-name", default=DEFAULT_LEG_NAME,
                        help=f"Leg display name (default: {DEFAULT_LEG_NAME})")
    parser.add_argument("--start", default="2012-01-01")
    parser.add_argument("--end", default=PACK_END_STR)
    parser.add_argument("--custom", default=None,
                        help="Replace default configs with a custom set, e.g. "
                             "'RC_x:B=2,S=1,D=5;RC_y:B=4,S=2,D=4'")
    parser.add_argument("--ref-tag", default="g3_flat",
                        help="Reference tag for Pareto / Δ analysis (default: g3_flat)")
    args = parser.parse_args()

    if args.custom:
        configs = _parse_custom(args.custom)
    else:
        configs = list(DEFAULT_CONFIGS)

    print("=" * 72)
    print("  P4 — Regime-Conditional Buy-Grace Sweep (variant d)")
    print("=" * 72)
    print(f"  signal   : {args.leg_name}  ({os.path.basename(args.signal)})")
    print(f"  window   : {args.start} → {args.end}")
    print(f"  ref      : {args.ref_tag}")
    print(f"  configs  :")
    for tag, (b, s, d) in configs:
        print(f"    - {tag:<10s}  BULL={b}  SIDE={s}  DEF={d}")
    print("=" * 72)

    if not os.path.exists(args.signal):
        print(f"[ERROR] missing signal: {args.signal}")
        return 2

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
    print(f"[pack] loaded {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    print("[VIX ] building regime timeseries…")
    vix_c, vix_r, vix_s = _load_vix(cfg, pack_start, pack_end)
    print(f"[VIX ] {len(vix_c)} dates")
    trigger_conf = conf.get("triggers", {})

    rows: List[Dict[str, Any]] = []
    t_total = time.time()
    for tag, bsd in configs:
        rec = _run_one(
            tag, bsd, args.signal, args.leg_name, cfg, pack,
            vix_c, vix_r, vix_s, trigger_conf, args.start, args.end,
        )
        rows.append(rec)
    total_elapsed = time.time() - t_total

    _print_summary(rows, ref_tag=args.ref_tag)
    print()
    best = _identify_best(rows, ref_tag=args.ref_tag)
    print(f"  Verdict vs {best['ref_tag']}: {best['verdict']}")
    if best["verdict"] == "pareto":
        print(f"  Pareto winners: {best['pareto_winners']}")
    print(f"  Score-ranked  : {best['score_ranked']}")
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path   = os.path.join(DOCS_DIR, f"p4_buy_grace_regime_sweep_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"p4_buy_grace_regime_sweep_{stamp}.json")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_basename": os.path.basename(pack_path),
        "pack_start": pack_start, "pack_end": pack_end,
        "window_start": args.start, "window_end": args.end,
        "leg_name": args.leg_name,
        "signal_path": args.signal,
        "variant": "d_regime_conditional_strict",
        "ref_tag": args.ref_tag,
        "configs": [{"tag": t, "BULL": b, "SIDE": s, "DEF": d} for t, (b, s, d) in configs],
        "total_elapsed_sec": round(total_elapsed, 1),
        "rows": rows,
        "verdict": best,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    _write_md(rows, args.leg_name, args.start, args.end,
              os.path.basename(pack_path), best, md_path)
    print(f"[saved] {md_path}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
