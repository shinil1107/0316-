"""Step C — Gate Evaluation for a Phase 5 retrained signal.

Evaluates a candidate `frozen_signal_P5_RETRAIN_*.npz` against the
live baseline (`V2_ENS_L3_v1_SIDE_DEF_p12`) on the fixed OOS window
and emits a 6-criterion gate verdict per `baseline_benchmark.md §5`.

Gate criteria
-------------
1. OOS CAGR              ≥ +56.33%             (baseline value)
2. OOS MDD               ≤ 34.61%              (baseline × 1.1)      ← mandatory
3. OOS Calmar            ≥ 1.881               (baseline × 1.05)
4. OOS realized IC       ≥ baseline_IC + 0.005
5. Realized cost drag    ≤ 0.78%               (baseline × 0.7)      ← mandatory  [v1.1]
6. Temporal stability T5 (deferred — DEFERRED)

Pass rule: ≥ 4 of 6 criteria AND both mandatory (#2, #5). #6 is
deferred, so the effective rule is ≥ 3 of 5 with mandatory intact.

Usage
-----
    python3 -u phase3/tests/step_c_gate_evaluation.py \\
        --signal /Users/.../frozen_signal_P5_RETRAIN_<stamp>.npz \\
        --arm-name P5_RETRAIN

Outputs
-------
- Console gate report
- `phase3/docs/step_c_<arm>_<stamp>.json`  (raw metrics)
- Appended row in `phase3/docs/phase5_step_c_results.jsonl`
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from phase3.engine_loader import engine  # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3 import simulator  # noqa: E402


# ── Fixed paths / protocol ───────────────────────────────────────────
OOS_START = "2024-06-01"
DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
BASELINE_JSON = os.path.join(DOCS_DIR, "baseline_benchmark_metrics.json")
BASELINE_ARM = "V2_ENS_L3_v1_SIDE_DEF_p12"
BASELINE_V2_SIGNAL = (
    "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output/"
    "frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"
)
STEP_C_RESULTS_JSONL = os.path.join(DOCS_DIR, "phase5_step_c_results.jsonl")


# ── SIDE_DEF_p12 trigger stack (identical to Step A, arm 3) ──────────
LEGACY_STRATEGY: Dict[str, Any] = {
    "rebalance_mode": "daily",
    "rebalance_gap_threshold": 0.02,
    "buy_allocation_mode": "gap_proportional",
    "enable_trim": True,
    "trim_threshold": 0.03,
    "sell_grace_days": 60,
    "min_buy_shares": 1,
    "enable_stop_loss": True,
    "stop_loss_pct": -15.0,
    "buy_limit_mode": "adaptive",
    "adaptive_deploy_rate": 0.10,
    "adaptive_min_limit": 500.0,
    "target_invest_pct": 0.97,
    "regime_overrides": {
        "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                 "target_invest_pct": 0.98},
        "SIDE": {"sell_grace_days": 120, "adaptive_deploy_rate": 0.10,
                 "enable_stop_loss": False},
        "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
    },
}
SIDE_DEF_P12_TRIGGERS: List[Dict[str, Any]] = [
    {"type": "stop_loss",   "regimes": ["BULL"],        "params": {"threshold_pct": -15.0}},
    {"type": "sell_grace",  "regimes": ["BULL"],        "params": {"days": 60}},
    {"type": "sell_grace",  "regimes": ["SIDE"],        "params": {"days": 120}},
    {"type": "sell_grace",  "regimes": ["DEF"],         "params": {"days": 60}},
    {"type": "profit_target","regimes": ["SIDE", "DEF"],
     "params": {"target_pct": 30.0, "action": "TRIM", "partial_pct": 0.12,
                "score_gate_enabled": False,
                "extension_enabled": True, "extension_window": 20,
                "extension_threshold": 0.20, "min_days_held": 10}},
]


def _make_strategy() -> Dict[str, Any]:
    s = copy.deepcopy(LEGACY_STRATEGY)
    s["exit_triggers"] = copy.deepcopy(SIDE_DEF_P12_TRIGGERS)
    return s


# ── Pack selection (same logic as step_a) ────────────────────────────
def _pick_oos_pack(save_dir: str) -> Tuple[str, str, str]:
    pattern = os.path.join(save_dir, "precompute_qresearch_v4_12_*.npz")
    all_packs = glob.glob(pattern)
    if not all_packs:
        raise RuntimeError(f"No precomputed pack found under {pattern}")
    qualified: List[Tuple[str, str, str]] = []
    for p in all_packs:
        stem = os.path.splitext(os.path.basename(p))[0]
        parts = stem.split("_")
        start, end = parts[-2], parts[-1]
        if start <= OOS_START and end >= OOS_START:
            qualified.append((p, start, end))
    if not qualified:
        raise RuntimeError(
            "No pack covers OOS_START. Candidates: "
            + ", ".join(os.path.basename(p) for p in all_packs)
        )
    qualified.sort(key=lambda t: (t[2], t[1]), reverse=True)
    return qualified[0]


def _build_cfg(conf: Dict[str, Any], pack_start: str, pack_end: str):
    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except Exception:
                pass
    cfg.start_panel_date = datetime.strptime(pack_start, "%Y-%m-%d")
    cfg.end_date = datetime.strptime(pack_end, "%Y-%m-%d")
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


def _load_vix(cfg, start: str, end: str):
    vix_df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=90),
        datetime.strptime(end, "%Y-%m-%d"),
    )
    c_map, r_map, s_map = {}, {}, {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            c_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
            r_map[d_str] = str(row.get("regime", "SIDE"))
            if "vix_smooth" in row.index:
                s_map[d_str] = float(row["vix_smooth"])
    return c_map, r_map, s_map


# ── Realized OOS IC (portfolio-independent) ──────────────────────────
def _realized_oos_ic(
    pack: dict,
    signal: dict,
    cfg,
    vix_regime_map: Dict[str, str],
    oos_start: str,
    oos_end: str,
) -> Dict[str, float]:
    """Average 1M / 3M Spearman IC over OOS dates.

    For each OOS date `di` in [oos_start, oos_end]:
      - score = score_vector_for_day(pack, di, mask, mask, mask, wb, ws, wd,
                                     score_regime=<VIX regime that day>, cfg)
      - mask to tradable assets
      - spearman(score, fwd1[di]) and spearman(score, fwd3[di])
    Returns mean across OOS dates (skipping NaN).
    """
    dates = np.asarray(pack["dates"])  # array of 'YYYY-MM-DD' strings
    mask   = np.asarray(signal["mask"],  dtype=bool)
    wb     = np.asarray(signal["wb"],    dtype=np.float64)
    ws     = np.asarray(signal["ws"],    dtype=np.float64)
    wd     = np.asarray(signal["wd"],    dtype=np.float64)

    tradable = pack["tradable"]  # (D, N)
    fwd1 = pack["fwd1"]           # (D, N)
    fwd3 = pack.get("fwd3")       # (D, N) or None
    D = len(dates)

    ic1_vals: List[float] = []
    ic3_vals: List[float] = []
    sp1_vals: List[float] = []
    sp3_vals: List[float] = []
    h1 = int(getattr(cfg, "horizon_1m", 21))
    h3 = int(getattr(cfg, "horizon_3m", 63))

    for di in range(D):
        d_str = str(dates[di])[:10]
        if d_str < oos_start or d_str > oos_end:
            continue
        rg = vix_regime_map.get(d_str, "SIDE").upper()
        # Engine names: BULL, SIDE, DEFENSIVE
        if rg in ("DEF", "DEFENSIVE"):
            score_regime = "DEFENSIVE"
        elif rg == "BULL":
            score_regime = "BULL"
        else:
            score_regime = "SIDE"

        svec = engine.score_vector_for_day(
            pack, di, mask, mask, mask, wb, ws, wd,
            score_regime=score_regime, cfg=cfg,
        )
        svec = np.asarray(svec, dtype=np.float64)
        svec = np.where(tradable[di], svec, np.nan)

        # IC 1M
        y1 = fwd1[di]
        if di + h1 < D:
            m = np.isfinite(svec) & np.isfinite(y1)
            if int(np.sum(m)) >= 30:
                ic1_vals.append(float(engine._spearman_corr(svec[m], y1[m])))
                sp1_vals.append(float(engine._calc_spread(svec[m], y1[m], float(cfg.top_quantile))))
        # IC 3M
        if fwd3 is not None and di + h3 < D:
            y3 = fwd3[di]
            m = np.isfinite(svec) & np.isfinite(y3)
            if int(np.sum(m)) >= 30:
                ic3_vals.append(float(engine._spearman_corr(svec[m], y3[m])))
                sp3_vals.append(float(engine._calc_spread(svec[m], y3[m], float(cfg.top_quantile))))

    def _mean(xs: List[float]) -> float:
        xs = [x for x in xs if np.isfinite(x)]
        return float(np.mean(xs)) if xs else float("nan")

    return {
        "oos_mean_ic_1m":     _mean(ic1_vals),
        "oos_mean_ic_3m":     _mean(ic3_vals),
        "oos_mean_spread_1m": _mean(sp1_vals),
        "oos_mean_spread_3m": _mean(sp3_vals),
        "oos_days_1m":        int(len(ic1_vals)),
        "oos_days_3m":        int(len(ic3_vals)),
    }


# ── Per-arm sim ──────────────────────────────────────────────────────
def _run_sim(
    arm_name: str,
    signal_path: str,
    cfg,
    pack,
    vix_close_map: Dict[str, float],
    vix_regime_map: Dict[str, str],
    vix_smooth_map: Dict[str, float],
    trigger_conf,
    oos_start: str,
    oos_end: str,
    buy_grace_days: int = 0,
    vol_target_per_regime: Optional[Dict[str, Dict[str, Any]]] = None,
    strategy_patch: Optional[Dict[str, Any]] = None,
    regime_overrides_patch: Optional[Dict[str, Dict[str, Any]]] = None,
    blend_conf: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    print(f"\n{'='*72}")
    print(f"  SIM  {arm_name}")
    print(f"  signal = {os.path.basename(signal_path)}")
    print(f"  window = {oos_start} → {oos_end}")
    print('=' * 72)

    signal = load_frozen_signal(signal_path)
    strat = _make_strategy()
    # T-buy-grace — Idea 2: optional execution-layer knob.  Default 0
    # (legacy byte-identical).  See phase3/tests/p3_buy_grace_sweep.py
    # for the sweep that motivated baseline = 3 adoption.
    if buy_grace_days and int(buy_grace_days) > 0:
        strat["buy_grace_days"] = int(buy_grace_days)
    # A1 — optional per-regime vol_target overlay.  Same regime_overrides
    # mechanism as buy_grace; absent ⇒ byte-identical to legacy.
    if vol_target_per_regime:
        import copy as _copy
        rovr = _copy.deepcopy(strat.get("regime_overrides", {}))
        for rg, vt in vol_target_per_regime.items():
            rovr.setdefault(rg, {})
            rovr[rg]["vol_target"] = _copy.deepcopy(vt)
        strat["regime_overrides"] = rovr
    # R1 — generic execution-policy hooks for the sensitivity matrix
    # (``p6_execution_sensitivity_matrix.py``).  ``strategy_patch`` shallow-
    # merges into the top-level strategy dict; ``regime_overrides_patch``
    # deep-merges per-regime overrides on top of the current map.  Both
    # default to None (legacy byte-identical).
    if regime_overrides_patch:
        import copy as _copy
        rovr = _copy.deepcopy(strat.get("regime_overrides", {}))
        for rg, ovr in regime_overrides_patch.items():
            rovr.setdefault(rg, {})
            for k, v in ovr.items():
                rovr[rg][k] = _copy.deepcopy(v)
        strat["regime_overrides"] = rovr
    if strategy_patch:
        for k, v in strategy_patch.items():
            strat[k] = v

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
        start_date=oos_start, end_date=oos_end,
        progress_fn=lambda c, t, m: None,
        blend_conf=blend_conf if blend_conf is not None else {"regime_blend_enabled": False},
        vix_smooth_by_date=vix_smooth_map,
    )
    elapsed = time.time() - t0

    m = res["metrics"]
    print(f"  CAGR      = {m.get('CAGR', 0)*100:+.2f}%")
    print(f"  Sharpe    = {m.get('Net_Sharpe', 0):.3f}")
    print(f"  MDD       = {m.get('Max_Drawdown', 0)*100:.2f}%")
    print(f"  Calmar    = {m.get('Calmar_Ratio', 0):.3f}")
    print(f"  Commission = ${m.get('Total_Commission', 0):,.2f} ({m.get('Commission_Pct', 0):.2f}%)")
    print(f"  Rebal     = {m.get('Rebalance_Days', 0)}")
    print(f"  elapsed   = {elapsed:.1f}s")

    trades = res.get("trades", pd.DataFrame())
    traded_value = 0.0
    if not trades.empty and "Value" in trades.columns:
        traded_value = float(trades["Value"].abs().sum())
    years = m.get("Years", 1.0) or 1.0
    avg_pv = (m.get("Initial_Capital", 100000.0) + m.get("Final_Value", 100000.0)) / 2.0
    turnover_proxy = traded_value / (avg_pv * years) if avg_pv > 0 else 0.0

    # Realized OOS IC (portfolio-independent signal quality)
    print("  [IC] computing realized OOS IC…")
    ic = _realized_oos_ic(pack, signal, cfg, vix_regime_map, oos_start, oos_end)
    print(f"  OOS_IC_1M = {ic['oos_mean_ic_1m']:+.4f}  (n={ic['oos_days_1m']})")
    print(f"  OOS_IC_3M = {ic['oos_mean_ic_3m']:+.4f}  (n={ic['oos_days_3m']})")

    return {
        "arm": arm_name,
        "signal_path": signal_path,
        "window_start": oos_start, "window_end": oos_end,
        "metrics": {
            "CAGR": float(m.get("CAGR", 0.0)),
            "Max_Drawdown": float(m.get("Max_Drawdown", 0.0)),
            "Net_Sharpe": float(m.get("Net_Sharpe", 0.0)),
            "Calmar_Ratio": float(m.get("Calmar_Ratio", 0.0)),
            "Total_Return": float(m.get("Total_Return", 0.0)),
            "Daily_Win_Rate": float(m.get("Daily_Win_Rate", 0.0)),
            "Monthly_Win_Rate": float(m.get("Monthly_Win_Rate", 0.0)),
            "Years": float(m.get("Years", 0.0)),
            "Trading_Days": int(m.get("Trading_Days", 0)),
            "Rebalance_Days": int(m.get("Rebalance_Days", 0)),
            "Total_Commission": float(m.get("Total_Commission", 0.0)),
            "Commission_Pct_of_Capital": float(m.get("Commission_Pct", 0.0)),
            "Final_Value": float(m.get("Final_Value", 0.0)),
            "turnover_annualized_proxy": round(turnover_proxy, 4),
            "vol_target_rebal_days_total":   int(m.get("vol_target_rebal_days_total", 0)),
            "vol_target_rebal_days_warmed":  int(m.get("vol_target_rebal_days_warmed", 0)),
            "vol_target_rebal_days_engaged": int(m.get("vol_target_rebal_days_engaged", 0)),
            "vol_target_scale_mean":         float(m.get("vol_target_scale_mean", 1.0)),
            "vol_target_scale_min":          float(m.get("vol_target_scale_min", 1.0)),
            "vol_target_realized_vol_p50":   float(m.get("vol_target_realized_vol_p50", 0.0)),
            "vol_target_realized_vol_p95":   float(m.get("vol_target_realized_vol_p95", 0.0)),
            "restricted_universe_filtered_total":           int(m.get("restricted_universe_filtered_total", 0)),
            "restricted_universe_rebal_days_with_filter":   int(m.get("restricted_universe_rebal_days_with_filter", 0)),
            "sector_cap_filtered_total":                    int(m.get("sector_cap_filtered_total", 0)),
            "sector_cap_rebal_days_with_filter":            int(m.get("sector_cap_rebal_days_with_filter", 0)),
            "sector_cap_max_breach_pct":                    float(m.get("sector_cap_max_breach_pct", 0.0)),
            "sector_cap_breach_days":                       int(m.get("sector_cap_breach_days", 0)),
        },
        "oos_ic": ic,
        "elapsed_sec": round(elapsed, 1),
    }


# ── Gate logic ───────────────────────────────────────────────────────
def _build_gate(cand: Dict[str, Any], baseline: Dict[str, Any],
                baseline_ic: Dict[str, float]) -> Dict[str, Any]:
    """Evaluate the 6 gate criteria.

    Thresholds (per baseline_benchmark.md §5, v1.1):
      #1 CAGR       >= baseline.CAGR
      #2 MDD        <= baseline.MDD * 1.10        (mandatory)
      #3 Calmar     >= baseline.Calmar * 1.05
      #4 IC_3M      >= baseline_IC_3M + 0.005
      #5 Commission <= baseline.Commission * 0.70 (mandatory)  [v1.1: was 0.50]
      #6 Temporal   DEFERRED (T5 not yet)
    """
    c = cand["metrics"]
    b = baseline["metrics"]

    th_cagr     = float(b["CAGR"])
    th_mdd      = float(b["Max_Drawdown"]) * 1.10
    th_calmar   = float(b["Calmar_Ratio"]) * 1.05
    th_ic       = float(baseline_ic.get("oos_mean_ic_3m", float("nan"))) + 0.005
    th_comm     = float(b["Commission_Pct_of_Capital"]) * 0.70   # v1.1: relaxed 0.50 → 0.70

    ic_cand = float(cand["oos_ic"].get("oos_mean_ic_3m", float("nan")))

    rows = []

    def _add(idx: int, name: str, cand_v: float, gate_str: str, passed: bool,
             mandatory: bool = False):
        rows.append({
            "#": idx, "criterion": name,
            "cand": cand_v, "gate": gate_str,
            "pass": passed, "mandatory": mandatory,
        })

    _add(1, "OOS CAGR",      float(c["CAGR"]),
         f">= {th_cagr:+.4f}", float(c["CAGR"]) >= th_cagr - 1e-9)
    _add(2, "OOS MDD",       float(c["Max_Drawdown"]),
         f"<= {th_mdd:.4f}",  float(c["Max_Drawdown"]) <= th_mdd + 1e-9, mandatory=True)
    _add(3, "OOS Calmar",    float(c["Calmar_Ratio"]),
         f">= {th_calmar:.4f}", float(c["Calmar_Ratio"]) >= th_calmar - 1e-9)
    _add(4, "OOS IC (3M)",   ic_cand,
         f">= {th_ic:.5f}",    ic_cand >= th_ic - 1e-9)
    _add(5, "Commission %",  float(c["Commission_Pct_of_Capital"]),
         f"<= {th_comm:.4f}",  float(c["Commission_Pct_of_Capital"]) <= th_comm + 1e-9,
         mandatory=True)
    rows.append({
        "#": 6, "criterion": "Temporal (T5)",
        "cand": float("nan"), "gate": "fold CAGR std <= mean * 0.5",
        "pass": None, "mandatory": False, "deferred": True,
    })

    # Pass rule: >= 4 of 6 AND #2 + #5 mandatory. #6 deferred → uses 5-basis rule:
    # >= 3 of 5 AND both mandatories pass.
    scored = [r for r in rows if r["#"] != 6]
    n_pass = sum(1 for r in scored if r["pass"])
    mandatory_pass = all(r["pass"] for r in scored if r["mandatory"])
    verdict_full = "PROMOTE" if (n_pass >= 4 and mandatory_pass) else (
        "HOLD" if (mandatory_pass and n_pass >= 2) else "REJECT"
    )
    # With T5 deferred, use 3-of-5 basis rule for an interim verdict
    verdict_interim = "PROMOTE*" if (n_pass >= 3 and mandatory_pass) else (
        "HOLD*" if (mandatory_pass and n_pass >= 2) else "REJECT*"
    )

    return {
        "criteria": rows,
        "n_pass_of_5": n_pass,
        "mandatory_pass": mandatory_pass,
        "verdict_full": verdict_full,
        "verdict_interim": verdict_interim,
        "thresholds": {
            "CAGR_ge":    th_cagr,
            "MDD_le":     th_mdd,
            "Calmar_ge":  th_calmar,
            "IC3M_ge":    th_ic,
            "Comm_le":    th_comm,
        },
    }


# ── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Step C — Phase 5 retrain gate evaluation")
    parser.add_argument("--signal", required=True,
                        help="Path to frozen_signal_P5_RETRAIN_<stamp>.npz")
    parser.add_argument("--arm-name", default="P5_RETRAIN",
                        help="Label for reporting (default: P5_RETRAIN)")
    parser.add_argument("--skip-baseline-ic", action="store_true",
                        help="Skip recomputing the V2 baseline IC (assumes oos_ic already stored).")
    args = parser.parse_args()

    if not os.path.exists(args.signal):
        print(f"[ERROR] signal not found: {args.signal}")
        return 1

    print("=" * 72)
    print(f"  Step C — Gate Evaluation  (arm={args.arm_name})")
    print("=" * 72)
    print(f"  candidate signal = {os.path.basename(args.signal)}")
    print(f"  baseline         = {BASELINE_ARM}  (baseline_benchmark.md v1.0)")
    print(f"  OOS window start = {OOS_START}")
    print("=" * 72)

    # Load baseline JSON
    if not os.path.exists(BASELINE_JSON):
        print(f"[ERROR] baseline JSON not found: {BASELINE_JSON}")
        print("        Run step_a_baseline_benchmark.py first.")
        return 1
    with open(BASELINE_JSON, "r") as f:
        baseline_all = json.load(f)
    baseline = next((a for a in baseline_all["arms"] if a.get("arm") == BASELINE_ARM), None)
    if baseline is None:
        print(f"[ERROR] arm {BASELINE_ARM!r} not found in {BASELINE_JSON}")
        return 1

    # Load live config
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)

    # Pick pack
    save_dir = conf["paths"]["output_dir"]
    pack_path, pack_start, pack_end = _pick_oos_pack(save_dir)
    print(f"[pack] using {os.path.basename(pack_path)}  ({pack_start} → {pack_end})")

    cfg = _build_cfg(conf, pack_start, pack_end)
    pack = engine.load_precompute_panel(cfg, pack_start, pack_end)
    if pack is None:
        prepared = engine.prepare_inputs(cfg)
        pack = prepared["pack"] if isinstance(prepared, dict) else prepared
    print(f"[pack] loaded {len(pack['tickers'])} tickers × {len(pack['dates'])} dates")

    oos_start = max(OOS_START, pack_start)
    oos_end = pack_end
    print(f"[sim ] OOS window = {oos_start} → {oos_end}")

    print("[VIX ] building regime timeseries…")
    vix_c, vix_r, vix_s = _load_vix(cfg, oos_start, oos_end)
    print(f"[VIX ] {len(vix_c)} dates")

    trigger_conf = conf.get("triggers", {})

    # Baseline IC — recompute unless told otherwise
    if not args.skip_baseline_ic:
        print(f"\n[baseline] computing OOS IC for {BASELINE_ARM} signal…")
        if not os.path.exists(BASELINE_V2_SIGNAL):
            print(f"[ERROR] baseline V2 signal path not found: {BASELINE_V2_SIGNAL}")
            return 1
        v2_sig = load_frozen_signal(BASELINE_V2_SIGNAL)
        baseline_ic = _realized_oos_ic(pack, v2_sig, cfg, vix_r, oos_start, oos_end)
        print(f"[baseline] IC_3M = {baseline_ic['oos_mean_ic_3m']:+.4f}  "
              f"(n={baseline_ic['oos_days_3m']})")
    else:
        baseline_ic = {"oos_mean_ic_3m": float("nan"), "oos_days_3m": 0}

    # Candidate sim
    cand = _run_sim(
        arm_name=args.arm_name, signal_path=args.signal,
        cfg=cfg, pack=pack,
        vix_close_map=vix_c, vix_regime_map=vix_r, vix_smooth_map=vix_s,
        trigger_conf=trigger_conf,
        oos_start=oos_start, oos_end=oos_end,
    )

    # Gate
    gate = _build_gate(cand, baseline, baseline_ic)

    # ── Report ──
    print("\n" + "=" * 72)
    print(f"  GATE REPORT  (arm={args.arm_name})")
    print("=" * 72)
    print(f"  baseline: {BASELINE_ARM}")
    print()
    print(f"  {'#':<2} {'Criterion':<20} {'Cand':>12} {'Gate':<30} {'Pass':<6} {'Mand?'}")
    print(f"  {'-'*2} {'-'*20} {'-'*12} {'-'*30} {'-'*6} {'-'*5}")
    for r in gate["criteria"]:
        passed = r.get("pass")
        if passed is None:
            pstr = "DEFER"
        else:
            pstr = "YES" if passed else "NO"
        mstr = "YES" if r.get("mandatory") else ""
        cand_str = f"{r['cand']:+.4f}" if np.isfinite(r["cand"]) else "n/a"
        print(f"  {r['#']:<2} {r['criterion']:<20} {cand_str:>12} {r['gate']:<30} {pstr:<6} {mstr}")

    print()
    print(f"  passed (of 5 scored) : {gate['n_pass_of_5']}")
    print(f"  mandatory ok         : {gate['mandatory_pass']}")
    print(f"  VERDICT (interim, T5 deferred) : {gate['verdict_interim']}")
    print(f"  VERDICT (full,   needs T5)     : {gate['verdict_full']}")
    print("=" * 72)
    print()
    print("  Legend:  * = interim verdict; T5 (criterion #6) will be added after")
    print("            walk-forward analysis is in place.")
    print("  Decision matrix: see phase3/docs/phase5_retrain_plan.md §6.")
    print("=" * 72)

    # ── Persist ──
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(DOCS_DIR, f"step_c_{args.arm_name}_{stamp}.json")
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "oos_window": {"start": oos_start, "end": oos_end},
            "pack": os.path.basename(pack_path),
            "initial_capital": 100000.0,
            "commission_bps": 10.0, "slippage_bps": 5.0,
            "baseline_arm": BASELINE_ARM,
        },
        "candidate": cand,
        "baseline_metrics": baseline["metrics"],
        "baseline_ic": baseline_ic,
        "gate": gate,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=float)
    print(f"[saved] {out_path}")

    # Append to history log
    hist_row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "arm": args.arm_name,
        "signal": os.path.basename(args.signal),
        "CAGR": cand["metrics"]["CAGR"],
        "MDD": cand["metrics"]["Max_Drawdown"],
        "Calmar": cand["metrics"]["Calmar_Ratio"],
        "Sharpe": cand["metrics"]["Net_Sharpe"],
        "Comm_Pct": cand["metrics"]["Commission_Pct_of_Capital"],
        "OOS_IC_3M": cand["oos_ic"]["oos_mean_ic_3m"],
        "n_pass_of_5": gate["n_pass_of_5"],
        "verdict_interim": gate["verdict_interim"],
        "verdict_full": gate["verdict_full"],
    }
    with open(STEP_C_RESULTS_JSONL, "a") as f:
        f.write(json.dumps(hist_row, default=float) + "\n")
    print(f"[appended] {STEP_C_RESULTS_JSONL}")

    # Exit code reflects interim verdict (0 = PROMOTE*, 2 = HOLD*, 3 = REJECT*)
    return {"PROMOTE*": 0, "HOLD*": 2, "REJECT*": 3}.get(gate["verdict_interim"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
