"""P5 — α_realized portfolio-level Vol-Targeting sweep.

Sweep design
------------
Tests whether scaling ``target_invest_pct`` by the ratio
``annual_target / realized_vol_30d`` (clipped to [min_scale, 1.0]) Pareto-
improves over v1.3 production (no vol target).

Configurations
~~~~~~~~~~~~~~
    off               # vol_target disabled (production reference)
    measure_only      # enabled but min_scale=max_scale=1.0 → records the
                      #   baseline realized-vol distribution without
                      #   modifying behaviour. Diagnostic only.
    target_22         # annual_target = 22%, lookback 30d, min_scale 0.30
    target_20         # annual_target = 20%
    target_18         # annual_target = 18%
    target_18_lb60    # annual_target = 18%, lookback 60d (smoother)
    target_20_floor50 # 20% target with min_scale=0.50 (more conservative cut)

Total: 7 runs × ~30 s each ≈ 3.5 min.

Reads
~~~~~
- `Baseline_V2` frozen signal (V2_GOLDEN_ENS_L3_v1)
- 15-y precompute pack
- SIDE_DEF_p12 strategy + buy_grace_days=3 (v1.3 production)

Writes
~~~~~~
- `phase3/docs/p5_vol_target_sweep_<ts>.md`
- `phase3/docs/p5_vol_target_sweep_<ts>.json`

CLI
~~~
    python3 -u phase3/tests/p5_vol_target_sweep.py
    python3 -u phase3/tests/p5_vol_target_sweep.py --custom \
        "T18_lb45:annual_target=0.18,lookback_days=45,min_scale=0.30"
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

BUY_GRACE_DAYS = 3   # v1.3 production


# ── Sweep configurations ─────────────────────────────────────────────
DEFAULT_CONFIGS: List[Tuple[str, Dict[str, Any]]] = [
    ("off",                None),
    ("measure_only",       {"enabled": True,  "annual_target": 0.20,
                            "lookback_days": 30,
                            "min_scale": 1.0, "max_scale": 1.0}),
    ("target_22",          {"enabled": True,  "annual_target": 0.22,
                            "lookback_days": 30,
                            "min_scale": 0.30, "max_scale": 1.0}),
    ("target_20",          {"enabled": True,  "annual_target": 0.20,
                            "lookback_days": 30,
                            "min_scale": 0.30, "max_scale": 1.0}),
    ("target_18",          {"enabled": True,  "annual_target": 0.18,
                            "lookback_days": 30,
                            "min_scale": 0.30, "max_scale": 1.0}),
    ("target_18_lb60",     {"enabled": True,  "annual_target": 0.18,
                            "lookback_days": 60,
                            "min_scale": 0.30, "max_scale": 1.0}),
    ("target_20_floor50",  {"enabled": True,  "annual_target": 0.20,
                            "lookback_days": 30,
                            "min_scale": 0.50, "max_scale": 1.0}),
]


def _parse_custom(custom: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Parse '<tag>:k=v,k=v;<tag>:...' into a config list."""
    out: List[Tuple[str, Dict[str, Any]]] = []
    for entry in custom.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        tag, body = entry.split(":", 1)
        cfg: Dict[str, Any] = {"enabled": True}
        for tok in body.split(","):
            tok = tok.strip()
            if not tok or "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k = k.strip(); v = v.strip()
            if k in ("lookback_days", "min_warmup_days"):
                cfg[k] = int(v)
            elif k in ("annual_target", "min_scale", "max_scale"):
                cfg[k] = float(v)
            elif k == "enabled":
                cfg[k] = v.lower() in ("1", "true", "yes")
        out.append((tag.strip(), cfg))
    return out


def _strategy_with_vol_target(vt: Dict[str, Any] | None) -> Dict[str, Any]:
    """Clone SIDE_DEF_p12 LEGACY strat, inject buy_grace_days=3, and
    optionally a flat ``vol_target`` block."""
    strat = _make_strategy()
    if BUY_GRACE_DAYS > 0:
        strat["buy_grace_days"] = int(BUY_GRACE_DAYS)
    if vt:
        strat["vol_target"] = copy.deepcopy(vt)
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
    tag: str, vt_cfg: Dict[str, Any] | None,
    signal_path: str, leg_name: str,
    cfg, pack,
    vix_close_map: Dict[str, float],
    vix_regime_map: Dict[str, str],
    vix_smooth_map: Dict[str, float],
    trigger_conf,
    start_d: str, end_d: str,
) -> Dict[str, Any]:
    print()
    print("=" * 72)
    print(f"  RUN  {tag}")
    print(f"  vol_target = {vt_cfg if vt_cfg else 'OFF'}")
    print(f"  signal     = {os.path.basename(signal_path)}")
    print(f"  window     = {start_d} → {end_d}")
    print("=" * 72)

    signal = load_frozen_signal(signal_path)
    strat = _strategy_with_vol_target(vt_cfg)

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
    events = res.get("vol_target_events", []) or []

    print(f"  CAGR      = {m.get('CAGR', 0)*100:+.2f}%   "
          f"Sharpe = {m.get('Net_Sharpe', 0):.3f}   "
          f"MDD = {m.get('Max_Drawdown', 0)*100:.2f}%")
    print(f"  Calmar    = {m.get('Calmar_Ratio', 0):.3f}    "
          f"Comm%  = {m.get('Commission_Pct', 0):.2f}%   "
          f"Final$ = ${m.get('Final_Value', 0):,.0f}")
    print(f"  vt: rebal_days total={int(m.get('vol_target_rebal_days_total', 0)):,}  "
          f"warmed={int(m.get('vol_target_rebal_days_warmed', 0)):,}  "
          f"engaged={int(m.get('vol_target_rebal_days_engaged', 0)):,}")
    if events:
        print(f"  vt: scale mean={m.get('vol_target_scale_mean', 1.0):.3f}  "
              f"min={m.get('vol_target_scale_min', 1.0):.3f}  "
              f"realized_vol p50={m.get('vol_target_realized_vol_p50', 0)*100:.2f}%  "
              f"p95={m.get('vol_target_realized_vol_p95', 0)*100:.2f}%")
    print(f"  elapsed   = {elapsed:.1f}s")
    print(f"  [Regime] BULL Ann = {float(rb.get('BULL', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"SIDE Ann = {float(rb.get('SIDE', {}).get('AnnRet', 0))*100:+.2f}%   "
          f"DEF Ann = {float(rb.get('DEF', {}).get('AnnRet', 0))*100:+.2f}%")

    # Down-sample the event list to a manageable size for the JSON dump.
    if len(events) > 2000:
        idxs = np.linspace(0, len(events) - 1, 2000).astype(int)
        events_keep = [events[i] for i in idxs]
    else:
        events_keep = events

    return {
        "tag": tag,
        "vol_target": vt_cfg,
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
            "vol_target_rebal_days_total":   int(m.get("vol_target_rebal_days_total", 0)),
            "vol_target_rebal_days_warmed":  int(m.get("vol_target_rebal_days_warmed", 0)),
            "vol_target_rebal_days_engaged": int(m.get("vol_target_rebal_days_engaged", 0)),
            "vol_target_scale_mean":         float(m.get("vol_target_scale_mean", 1.0)),
            "vol_target_scale_min":          float(m.get("vol_target_scale_min", 1.0)),
            "vol_target_realized_vol_mean":  float(m.get("vol_target_realized_vol_mean", 0.0)),
            "vol_target_realized_vol_p50":   float(m.get("vol_target_realized_vol_p50", 0.0)),
            "vol_target_realized_vol_p95":   float(m.get("vol_target_realized_vol_p95", 0.0)),
        },
        "regime_breakdown": {
            rg: {
                k: (int(v) if isinstance(v, (int, np.integer)) else float(v))
                for k, v in (rb.get(rg) or {}).items()
            } for rg in ("BULL", "SIDE", "DEF")
        },
        "events_sample": events_keep,
    }


def _print_summary(rows: List[Dict[str, Any]],
                   ref_tag: str = "off") -> None:
    if not rows:
        return
    by_tag = {r["tag"]: r for r in rows}
    ref = by_tag.get(ref_tag, rows[0])

    print()
    print("=" * 116)
    print(f"  HEADLINE SWEEP  (Δ vs {ref_tag})")
    print("=" * 116)
    print(f"  {'tag':<22s} {'CAGR':>8s} {'Δref':>7s} {'Sharpe':>7s} "
          f"{'MDD':>7s} {'ΔMDD':>6s} {'Calmar':>7s} {'Comm%':>7s} "
          f"{'Final $':>14s}  {'Eng/Tot':>9s}  {'rv_p50':>6s}")
    print("-" * 116)
    for r in rows:
        m = r["metrics"]
        d_cagr = (m["CAGR"] - ref["metrics"]["CAGR"]) * 100
        d_mdd = (m["Max_Drawdown"] - ref["metrics"]["Max_Drawdown"]) * 100
        eng = m["vol_target_rebal_days_engaged"]
        warmed = m["vol_target_rebal_days_warmed"]
        eng_str = f"{eng}/{warmed}" if warmed else "—"
        rv50 = m["vol_target_realized_vol_p50"] * 100
        print(
            f"  {r['tag']:<22s} "
            f"{m['CAGR']*100:>+7.2f}% "
            f"{d_cagr:>+6.2f}% "
            f"{m['Net_Sharpe']:>7.3f} "
            f"{m['Max_Drawdown']*100:>6.2f}% "
            f"{d_mdd:>+5.2f}% "
            f"{m['Calmar_Ratio']:>7.3f} "
            f"{m['Commission_Pct']:>6.2f}% "
            f"${m['Final_Value']:>13,.0f}  "
            f"{eng_str:>9s}  "
            f"{rv50:>5.2f}%"
        )
    print("=" * 116)
    print()
    print("  Regime AnnRet (Δ vs ref)")
    print("-" * 92)
    print(f"  {'tag':<22s}  {'BULL':>14s}  {'SIDE':>14s}  {'DEF':>14s}")
    base_b = ref["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = ref["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = ref["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        print(
            f"  {r['tag']:<22s}  "
            f"{ab*100:>+7.2f}% / {(ab-base_b)*100:>+5.2f}  "
            f"{aS*100:>+7.2f}% / {(aS-base_s)*100:>+5.2f}  "
            f"{ad*100:>+7.2f}% / {(ad-base_d)*100:>+5.2f}"
        )
    print("=" * 92)


def _identify_winners(rows: List[Dict[str, Any]],
                      ref_tag: str = "off") -> Dict[str, Any]:
    """Strict Pareto vs ``ref_tag``:
        ΔCAGR ≥ −2.0pp  AND  ΔMDD ≤ −2.0pp  AND  ΔCalmar ≥ +0.05  AND  ΔSharpe ≥ +0.02.

    Score for tie-break:
        score = ΔCalmar × 5  +  ΔSharpe × 10  −  max(0, −ΔCAGR) × 0.5
    """
    by_tag = {r["tag"]: r for r in rows}
    if ref_tag not in by_tag:
        return {"verdict": "no-ref", "ref_tag": ref_tag}
    ref = by_tag[ref_tag]["metrics"]
    pareto: List[Tuple[str, float]] = []
    scored: List[Tuple[str, float]] = []
    for r in rows:
        if r["tag"] in (ref_tag, "measure_only"):
            continue
        m = r["metrics"]
        d_cagr   = (m["CAGR"] - ref["CAGR"]) * 100
        d_mdd    = (m["Max_Drawdown"] - ref["Max_Drawdown"]) * 100
        d_calmar = m["Calmar_Ratio"] - ref["Calmar_Ratio"]
        d_sharpe = m["Net_Sharpe"] - ref["Net_Sharpe"]
        is_pareto = (
            d_cagr >= -2.0 and d_mdd <= -2.0
            and d_calmar >= 0.05 and d_sharpe >= 0.02
        )
        score = d_calmar * 5 + d_sharpe * 10 - max(0.0, -d_cagr) * 0.5
        if is_pareto:
            pareto.append((r["tag"], score))
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
    ref = by_tag.get("off", rows[0])
    L: List[str] = []
    L.append(f"# P5 — α_realized Vol-Target Sweep · {leg_name}")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Signal**: {os.path.basename(rows[0]['signal_path'])}")
    L.append(f"**Window**: {start_d} → {end_d}")
    L.append(f"**Pack**: `{pack_name}`")
    L.append(f"**Protocol**: $100K · $1K/day · 10/5 bps · SIDE_DEF_p12 · daily rebal · "
             f"buy_grace_days={BUY_GRACE_DAYS} · vol-target lookback default 30d / floor 0.30")
    L.append("")
    L.append("**Variant**: α_realized — at each rebalance day, compute realized "
             "30-day vol of the portfolio (annualized), then scale "
             "`target_invest_pct` by `clip(annual_target / realized_vol, [min_scale, 1.0])`.")
    L.append("")
    L.append(f"**Reference**: `off` (vol-target disabled, v1.3 production).")
    L.append("")

    L.append("## 1. Headline")
    L.append("")
    L.append("| tag | CAGR | Δ ref | Sharpe | MDD | ΔMDD | Calmar | Comm % | Final $ | engage / warmed | rv p50 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        m = r["metrics"]
        d_cagr = (m["CAGR"] - ref["metrics"]["CAGR"]) * 100
        d_mdd = (m["Max_Drawdown"] - ref["metrics"]["Max_Drawdown"]) * 100
        eng = m["vol_target_rebal_days_engaged"]; warmed = m["vol_target_rebal_days_warmed"]
        eng_str = f"{eng}/{warmed}" if warmed else "—"
        L.append(
            f"| **{r['tag']}** "
            f"| {m['CAGR']*100:+.2f}% "
            f"| {d_cagr:+.2f}pp "
            f"| {m['Net_Sharpe']:+.3f} "
            f"| {m['Max_Drawdown']*100:.2f}% "
            f"| {d_mdd:+.2f}pp "
            f"| {m['Calmar_Ratio']:.3f} "
            f"| {m['Commission_Pct']:.2f}% "
            f"| ${m['Final_Value']:,.0f} "
            f"| {eng_str} "
            f"| {m['vol_target_realized_vol_p50']*100:.2f}% |"
        )
    L.append("")

    L.append("## 2. Realized-vol distribution (measure_only, baseline)")
    L.append("")
    if "measure_only" in by_tag:
        m_meas = by_tag["measure_only"]["metrics"]
        L.append(
            f"- Mean realized vol (30d): **{m_meas['vol_target_realized_vol_mean']*100:.2f}%**\n"
            f"- p50 realized vol: **{m_meas['vol_target_realized_vol_p50']*100:.2f}%**\n"
            f"- p95 realized vol: **{m_meas['vol_target_realized_vol_p95']*100:.2f}%**\n"
            f"- Rebal days warmed: {m_meas['vol_target_rebal_days_warmed']:,}"
        )
        L.append("")
        L.append(
            f"→ Targets `0.18` / `0.20` / `0.22` engage when realized vol exceeds "
            f"those levels. The fraction of rebal days where each target engages "
            f"is reported above as `engage / warmed`."
        )
    L.append("")

    L.append("## 3. Regime breakdown — AnnRet (Δ vs `off`)")
    L.append("")
    L.append("| tag | BULL | Δ | SIDE | Δ | DEF | Δ |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    base_b = ref["regime_breakdown"]["BULL"]["AnnRet"]
    base_s = ref["regime_breakdown"]["SIDE"]["AnnRet"]
    base_d = ref["regime_breakdown"]["DEF"]["AnnRet"]
    for r in rows:
        rb = r["regime_breakdown"]
        ab = rb["BULL"]["AnnRet"]; aS = rb["SIDE"]["AnnRet"]; ad = rb["DEF"]["AnnRet"]
        L.append(
            f"| **{r['tag']}** "
            f"| {ab*100:+.2f}% | {(ab-base_b)*100:+.2f}pp "
            f"| {aS*100:+.2f}% | {(aS-base_s)*100:+.2f}pp "
            f"| {ad*100:+.2f}% | {(ad-base_d)*100:+.2f}pp |"
        )
    L.append("")

    L.append("## 4. Verdict")
    L.append("")
    L.append("Pareto criterion vs `off`: ΔCAGR ≥ −2.0pp **and** ΔMDD ≤ −2.0pp "
             "**and** ΔCalmar ≥ +0.05 **and** ΔSharpe ≥ +0.02.")
    L.append("")
    L.append(f"- Verdict: **`{best['verdict']}`**")
    if best["verdict"] == "pareto":
        L.append(f"- Pareto winners (sorted): {', '.join(f'`{t}`' for t in best['pareto_winners'])}")
    L.append(f"- Score-ranked top 5: {', '.join(f'`{t}`' for t in best['score_ranked'])}")
    L.append("")
    L.append("---")
    L.append("")
    L.append("**Reading guide**")
    L.append("")
    L.append("- `engage / warmed` shows how often the vol-target actually "
             "deleveraged. If engage = 0, the target was never breached → "
             "pick a lower target. If engage ≈ warmed, the target is too "
             "tight and the strategy is forced into a permanently smaller "
             "exposure.")
    L.append("- `rv p50` is the median 30-day realized vol over rebal days. "
             "A reasonable target sits at or just below this number.")
    L.append("- Vol targeting is one-sided (`max_scale = 1.0`): no leverage "
             "is ever applied; the overlay can only reduce exposure.")

    with open(md_path, "w") as f:
        f.write("\n".join(L))


def main() -> int:
    parser = argparse.ArgumentParser(description="P5 — α_realized vol-target sweep")
    parser.add_argument("--signal", default=DEFAULT_SIGNAL,
                        help="Signal npz path (default: Baseline_V2)")
    parser.add_argument("--leg-name", default=DEFAULT_LEG_NAME,
                        help=f"Leg display name (default: {DEFAULT_LEG_NAME})")
    parser.add_argument("--start", default="2012-01-01")
    parser.add_argument("--end", default=PACK_END_STR)
    parser.add_argument("--custom", default=None,
                        help="Replace default configs with custom set")
    parser.add_argument("--ref-tag", default="off",
                        help="Reference tag for Pareto / Δ analysis (default: off)")
    args = parser.parse_args()

    configs = _parse_custom(args.custom) if args.custom else list(DEFAULT_CONFIGS)

    print("=" * 72)
    print("  P5 — α_realized Vol-Target Sweep")
    print("=" * 72)
    print(f"  signal   : {args.leg_name}  ({os.path.basename(args.signal)})")
    print(f"  window   : {args.start} → {args.end}")
    print(f"  ref      : {args.ref_tag}")
    print(f"  configs  :")
    for tag, vt in configs:
        if vt is None:
            print(f"    - {tag:<24s}  OFF")
        else:
            kvs = ",".join(f"{k}={vt[k]}" for k in
                           ("annual_target", "lookback_days", "min_scale", "max_scale")
                           if k in vt)
            print(f"    - {tag:<24s}  {kvs}")
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
    for tag, vt in configs:
        rec = _run_one(
            tag, vt, args.signal, args.leg_name, cfg, pack,
            vix_c, vix_r, vix_s, trigger_conf, args.start, args.end,
        )
        rows.append(rec)
    total_elapsed = time.time() - t_total

    _print_summary(rows, ref_tag=args.ref_tag)
    print()
    best = _identify_winners(rows, ref_tag=args.ref_tag)
    print(f"  Verdict vs {best['ref_tag']}: {best['verdict']}")
    if best["verdict"] == "pareto":
        print(f"  Pareto winners: {best['pareto_winners']}")
    print(f"  Score-ranked  : {best['score_ranked']}")
    print(f"  total elapsed : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path   = os.path.join(DOCS_DIR, f"p5_vol_target_sweep_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"p5_vol_target_sweep_{stamp}.json")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pack_basename": os.path.basename(pack_path),
        "pack_start": pack_start, "pack_end": pack_end,
        "window_start": args.start, "window_end": args.end,
        "leg_name": args.leg_name,
        "signal_path": args.signal,
        "buy_grace_days": BUY_GRACE_DAYS,
        "variant": "alpha_realized_vol_target",
        "ref_tag": args.ref_tag,
        "configs": [{"tag": t, "vol_target": vt} for t, vt in configs],
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
