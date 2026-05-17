"""C2 — Live vs Backtest Drift Tracker.

Reads the live `DailyLog` from `holdings_log.xlsx`, runs the simulator
over the *exact same window / starting state / config* using the
production frozen signal, and reports the divergence between live and
simulated portfolio trajectories.

Why we need this
----------------
Live trade execution differs from the simulator on multiple axes:
- order fill prices vs cached close,
- partial fills, manual interventions, holiday timing,
- live P&L reconciliation lag,
- regime / VIX / score evaluation timing.

Backtest is the ground truth we calibrate against. Drift between
backtest and live undermines every other result. This tool gives us a
*standing* metric to detect divergence early.

Drift metrics
-------------
1. **Cumulative-return drift** (`Δ_cum`)
   `cum_live[t] / cum_sim[t] - 1` over the window.
2. **Daily-return drift** (`Δ_daily`)
   per-day `ret_live - ret_sim`. Reports mean, std, max abs.
3. **Correlation** between daily returns (>= 0.85 healthy).
4. **Tracking error** (`std(Δ_daily) × √252`).
5. **Final-value drift** in dollars and %.
6. **Ramp-up flag**: any day with `CashPct > 50` is tagged so we can
   read post-ramp metrics separately (the live runner deploys gradually
   while the simulator can deploy faster).

Alert thresholds (user-configurable)
------------------------------------
- `|Δ_cum_30d| > 2.0pp`         → WARN
- `|Δ_cum_30d| > 5.0pp`         → ALERT
- `corr_30d < 0.70`             → WARN
- `corr_30d < 0.50`             → ALERT
- `tracking_error > 5%/yr`      → WARN

Outputs
-------
- console summary
- `phase3/docs/live_drift_<ts>.md`
- `phase3/docs/live_drift_<ts>.json`

CLI
---
    python3 -u phase3/tests/step_f_live_drift_tracker.py
    python3 -u phase3/tests/step_f_live_drift_tracker.py --start 2026-04-12
    python3 -u phase3/tests/step_f_live_drift_tracker.py --end 2026-04-25
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
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
from phase3 import simulator  # noqa: E402
from phase3.daily_runner import load_frozen_signal  # noqa: E402
from phase3.tests.step_c_gate_evaluation import (  # noqa: E402
    _build_cfg, _load_vix, _make_strategy,
)
from phase3.tests.step_e_spy_benchmark import (  # noqa: E402
    _spy_window_metrics, _load_spy_series,
)

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")
PACK_START_STR = "2011-01-03"

WARN_CUM_PP = 2.0
ALERT_CUM_PP = 5.0
WARN_CORR = 0.70
ALERT_CORR = 0.50
WARN_TE_PCT = 5.0
RAMP_CASH_PCT_THRESHOLD = 50.0


def _norm_date(x) -> str:
    if isinstance(x, str):
        return x[:10]
    if hasattr(x, "strftime"):
        return x.strftime("%Y-%m-%d")
    return str(x)[:10]


def _load_live_daily_log(holdings_path: str) -> pd.DataFrame:
    if not os.path.exists(holdings_path):
        raise FileNotFoundError(f"holdings_log.xlsx not found at {holdings_path}")
    df = pd.read_excel(holdings_path, sheet_name="DailyLog")
    if df.empty:
        return df
    df["Date"] = df["Date"].apply(_norm_date)
    df = df.sort_values("Date").reset_index(drop=True)
    df["TotalCapital"] = df["TotalCapital"].astype(float)
    df["CashBalance"] = df["CashBalance"].astype(float)
    df["PortfolioValue"] = df["PortfolioValue"].astype(float)
    df["CashPct"] = df["CashPct"].astype(float)
    return df


def _build_fresh_pack(conf, start_d: str, end_d: str) -> Tuple[Any, Any, str, str]:
    """Build an in-memory pack from FMP cache, the same way the live
    runner does (``engine.prepare_inputs``). Required because the
    saved precompute pack typically ends weeks before the live window
    starts.

    Returns (cfg, pack, panel_start, panel_end).
    """
    import dataclasses
    from datetime import datetime as _dt
    panel_start = (_dt.strptime(start_d, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    panel_end = end_d
    cfg = _build_cfg(conf, panel_start, panel_end)
    cfg_for_pack = dataclasses.replace(cfg)
    cfg_for_pack.start_panel_date = _dt.strptime(panel_start, "%Y-%m-%d")
    cfg_for_pack.end_date = _dt.strptime(panel_end, "%Y-%m-%d")
    cfg_for_pack.enable_historical_universe = True
    cfg_for_pack.historical_universe_expand_tickers = True
    cfg_for_pack.enable_coverage_based_universe = True
    print(f"[pack] building fresh pack {panel_start} → {panel_end} (engine.prepare_inputs)…")
    result = engine.prepare_inputs(cfg_for_pack)
    pack = result["pack"] if isinstance(result, dict) else result
    print(f"[pack] built {len(pack['tickers'])} tickers × {len(pack['dates'])} dates "
          f"(actual {pack['dates'][0]} → {pack['dates'][-1]})")
    return cfg, pack, panel_start, panel_end


def _run_simulator(
    cfg, pack, signal_path: str,
    vc, vr, vs, trigger_conf,
    initial_capital: float, daily_buy_limit: float,
    start_d: str, end_d: str,
    buy_grace_days: int = 3,
) -> pd.DataFrame:
    """Run simulator, return its daily_ts (PortfolioValue / DailyReturn)."""
    signal = load_frozen_signal(signal_path)
    strat = _make_strategy()
    if buy_grace_days > 0:
        strat["buy_grace_days"] = int(buy_grace_days)
    res = simulator.run_simulation(
        engine=engine, cfg=cfg, pack=pack, signal=signal,
        vix_close_by_date=vc, vix_regime_by_date=vr,
        initial_capital=float(initial_capital),
        daily_buy_limit=float(daily_buy_limit),
        strategy_conf=strat, trigger_conf=trigger_conf,
        rebalance_mode="daily",
        commission_bps=10.0, slippage_bps=5.0,
        start_date=start_d, end_date=end_d,
        progress_fn=lambda c, t, m: None,
        blend_conf={"regime_blend_enabled": False},
        vix_smooth_by_date=vs,
    )
    df = res["daily_ts"].copy()
    if df.empty:
        return df
    df["Date"] = df["Date"].apply(_norm_date)
    return df


def _compute_drift(
    live: pd.DataFrame, sim: pd.DataFrame, initial_capital: float,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Inner-join live and sim by date, compute drift columns + summary.

    Important caveats:
    - Live ``DailyReturn`` column is unreliable (always 0 in current
      runner output), so we recompute live daily returns from
      ``TotalCapital``.
    - The first joined day has no prior live value; we anchor it to
      ``initial_capital`` to keep the first ``ret_live`` on the same
      footing as ``ret_sim``.
    """
    # Live daily returns from TotalCapital — anchor at initial_capital.
    live_ord = live.sort_values("Date").reset_index(drop=True).copy()
    live_ord["ret_live"] = (
        live_ord["TotalCapital"]
        / live_ord["TotalCapital"].shift(1).fillna(initial_capital)
        - 1.0
    )
    live_ord["cum_live"] = live_ord["TotalCapital"] / float(initial_capital) - 1.0
    live_keep = live_ord[["Date", "TotalCapital", "CashPct", "Regime",
                          "VIX", "ret_live", "cum_live"]].copy()

    sim_ord = sim.sort_values("Date").reset_index(drop=True).copy()
    sim_ord["cum_sim"] = sim_ord["PortfolioValue"] / float(initial_capital) - 1.0
    sim_keep = sim_ord[["Date", "PortfolioValue", "DailyReturn", "cum_sim"]].rename(
        columns={"PortfolioValue": "TotalCapital_sim", "DailyReturn": "ret_sim"}
    )

    merged = pd.merge(live_keep, sim_keep, on="Date", how="inner")
    if merged.empty:
        return merged, {"error": "no_overlap"}

    merged["delta_ret"] = merged["ret_live"] - merged["ret_sim"]
    merged["delta_cum_pp"] = (merged["cum_live"] - merged["cum_sim"]) * 100.0
    merged["ramp_up"] = merged["CashPct"] > RAMP_CASH_PCT_THRESHOLD

    n = len(merged)
    delta_ret = merged["delta_ret"].to_numpy(dtype=float)
    ret_live = merged["ret_live"].to_numpy(dtype=float)
    ret_sim = merged["ret_sim"].to_numpy(dtype=float)

    # Correlation requires variance in both series.
    if n >= 2 and float(np.std(ret_live)) > 1e-12 and float(np.std(ret_sim)) > 1e-12:
        corr_full = float(np.corrcoef(ret_live, ret_sim)[0, 1])
    else:
        corr_full = float("nan")

    # 30-day window — last min(30, n) rows.
    window = merged.tail(min(30, n))
    delta_ret_30 = window["delta_ret"].to_numpy(dtype=float)
    ret_live_30 = window["ret_live"].to_numpy(dtype=float)
    ret_sim_30 = window["ret_sim"].to_numpy(dtype=float)
    if (len(window) >= 2 and float(np.std(ret_live_30)) > 1e-12
            and float(np.std(ret_sim_30)) > 1e-12):
        corr_30 = float(np.corrcoef(ret_live_30, ret_sim_30)[0, 1])
    else:
        corr_30 = float("nan")

    last = merged.iloc[-1]
    summary = {
        "n_days_compared": int(n),
        "first_date": merged.iloc[0]["Date"],
        "last_date": merged.iloc[-1]["Date"],
        "ramp_up_days": int(merged["ramp_up"].sum()),
        "steady_state_days": int((~merged["ramp_up"]).sum()),
        "live_total_cum_pct":     float(last["cum_live"]) * 100.0,
        "sim_total_cum_pct":      float(last["cum_sim"]) * 100.0,
        "delta_cum_pp_total":     float(last["delta_cum_pp"]),
        "delta_cum_pp_30d":       float(window.iloc[-1]["delta_cum_pp"] -
                                        (window.iloc[0]["delta_cum_pp"] if len(window) > 1 else 0.0)),
        "delta_ret_mean_bps":     float(np.mean(delta_ret)) * 10000.0,
        "delta_ret_std_bps":      float(np.std(delta_ret, ddof=1) if n > 1 else 0.0) * 10000.0,
        "delta_ret_max_abs_bps":  float(np.max(np.abs(delta_ret)) if n > 0 else 0.0) * 10000.0,
        "tracking_error_pct":     float(np.std(delta_ret, ddof=1) if n > 1 else 0.0)
                                  * float(np.sqrt(252.0)) * 100.0,
        "corr_full":              corr_full,
        "corr_30d":               corr_30,
        "live_final_capital":     float(last["TotalCapital"]),
        "sim_final_capital":      float(last["TotalCapital_sim"]),
        "delta_final_dollars":    float(last["TotalCapital"] - last["TotalCapital_sim"]),
    }
    return merged, summary


def _classify(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return list of (level, message) flags."""
    out: List[Tuple[str, str]] = []
    cum_30 = abs(summary.get("delta_cum_pp_30d", 0.0))
    cum_total = abs(summary.get("delta_cum_pp_total", 0.0))
    if cum_30 > ALERT_CUM_PP:
        out.append(("ALERT", f"|Δ_cum_30d| = {cum_30:.2f}pp > {ALERT_CUM_PP:.1f}pp"))
    elif cum_30 > WARN_CUM_PP:
        out.append(("WARN", f"|Δ_cum_30d| = {cum_30:.2f}pp > {WARN_CUM_PP:.1f}pp"))
    else:
        out.append(("OK", f"|Δ_cum_30d| = {cum_30:.2f}pp ≤ {WARN_CUM_PP:.1f}pp"))

    corr = summary.get("corr_30d")
    if corr is None or (isinstance(corr, float) and np.isnan(corr)):
        out.append(("INFO", "corr_30d unavailable (insufficient variance)"))
    elif corr < ALERT_CORR:
        out.append(("ALERT", f"corr_30d = {corr:.3f} < {ALERT_CORR:.2f}"))
    elif corr < WARN_CORR:
        out.append(("WARN", f"corr_30d = {corr:.3f} < {WARN_CORR:.2f}"))
    else:
        out.append(("OK", f"corr_30d = {corr:.3f} ≥ {WARN_CORR:.2f}"))

    te = summary.get("tracking_error_pct", 0.0)
    if te > WARN_TE_PCT:
        out.append(("WARN", f"tracking_error = {te:.2f}% > {WARN_TE_PCT:.1f}%/yr"))
    else:
        out.append(("OK", f"tracking_error = {te:.2f}% ≤ {WARN_TE_PCT:.1f}%/yr"))

    out.append(("INFO", f"cum_total Δ = {cum_total:.2f}pp (whole window)"))
    return out


def _print_console(merged: pd.DataFrame, summary: Dict[str, Any],
                    flags: List[Tuple[str, str]],
                    spy_metrics: Optional[Dict[str, float]],
                    initial_capital: float) -> None:
    print()
    print("=" * 84)
    print("  Live vs Backtest Drift Tracker")
    print("=" * 84)
    print(f"  window         : {summary['first_date']} → {summary['last_date']}  ({summary['n_days_compared']} days)")
    print(f"  ramp / steady  : {summary['ramp_up_days']} / {summary['steady_state_days']}")
    print(f"  live final     : ${summary['live_final_capital']:>13,.2f}  "
          f"({summary['live_total_cum_pct']:+.2f}%)")
    print(f"  sim  final     : ${summary['sim_final_capital']:>13,.2f}  "
          f"({summary['sim_total_cum_pct']:+.2f}%)")
    print(f"  Δ final $      : ${summary['delta_final_dollars']:>+13,.2f}  "
          f"({summary['delta_cum_pp_total']:+.2f}pp)")
    if spy_metrics:
        print(f"  SPY (window)   : CAGR {spy_metrics.get('CAGR', 0)*100:+.2f}%  "
              f"Total {spy_metrics.get('Total_Return', 0)*100:+.2f}%")
    print("-" * 84)
    print(f"  daily Δ        : mean {summary['delta_ret_mean_bps']:+.2f} bps  "
          f"std {summary['delta_ret_std_bps']:.2f} bps  "
          f"max|Δ| {summary['delta_ret_max_abs_bps']:.2f} bps")
    print(f"  tracking error : {summary['tracking_error_pct']:.2f}%/yr")
    print(f"  correlation    : full {summary['corr_full']:.3f}   "
          f"30d {summary['corr_30d']:.3f}")
    print("=" * 84)
    print()
    print("  Daily detail (last 15 rows):")
    print("-" * 84)
    cols = ["Date", "Regime", "CashPct", "TotalCapital", "TotalCapital_sim",
            "ret_live", "ret_sim", "delta_ret", "delta_cum_pp"]
    show = merged.tail(15)[cols].copy()
    show["TotalCapital"] = show["TotalCapital"].map(lambda v: f"${v:>10,.2f}")
    show["TotalCapital_sim"] = show["TotalCapital_sim"].map(lambda v: f"${v:>10,.2f}")
    show["ret_live"] = show["ret_live"].map(lambda v: f"{v*100:+.3f}%")
    show["ret_sim"]  = show["ret_sim"].map(lambda v: f"{v*100:+.3f}%")
    show["delta_ret"] = show["delta_ret"].map(lambda v: f"{v*10000:+.2f}bp")
    show["delta_cum_pp"] = show["delta_cum_pp"].map(lambda v: f"{v:+.2f}pp")
    show["CashPct"] = show["CashPct"].map(lambda v: f"{v:5.1f}%")
    print(show.to_string(index=False))
    print()
    print("  Verdict")
    print("-" * 84)
    for level, msg in flags:
        print(f"  [{level:5s}]  {msg}")
    print("=" * 84)


def _write_md(merged: pd.DataFrame, summary: Dict[str, Any],
              flags: List[Tuple[str, str]],
              spy_metrics: Optional[Dict[str, float]],
              md_path: str, signal_basename: str,
              initial_capital: float, daily_buy_limit: float) -> None:
    L: List[str] = []
    L.append(f"# C2 — Live vs Backtest Drift Report")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"**Window**: {summary['first_date']} → {summary['last_date']}  "
             f"({summary['n_days_compared']} trading days, "
             f"{summary['ramp_up_days']} ramp + {summary['steady_state_days']} steady)")
    L.append(f"**Signal**: `{signal_basename}`")
    L.append(f"**Init cap / Buy limit**: $${initial_capital:,.0f} / $${daily_buy_limit:,.0f}/day  "
             f"· buy_grace_days=3 · 10/5 bps")
    L.append("")
    L.append("## 1. Headline")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---:|")
    L.append(f"| Live cumulative return | {summary['live_total_cum_pct']:+.2f}% |")
    L.append(f"| Sim cumulative return | {summary['sim_total_cum_pct']:+.2f}% |")
    L.append(f"| Δ cumulative (live − sim) | **{summary['delta_cum_pp_total']:+.2f}pp** |")
    L.append(f"| Δ cumulative — last 30d | {summary['delta_cum_pp_30d']:+.2f}pp |")
    L.append(f"| Live final $ | ${summary['live_final_capital']:,.2f} |")
    L.append(f"| Sim final $ | ${summary['sim_final_capital']:,.2f} |")
    L.append(f"| Δ final $ | ${summary['delta_final_dollars']:+,.2f} |")
    L.append(f"| Daily Δ — mean | {summary['delta_ret_mean_bps']:+.2f} bps |")
    L.append(f"| Daily Δ — std | {summary['delta_ret_std_bps']:.2f} bps |")
    L.append(f"| Daily Δ — max abs | {summary['delta_ret_max_abs_bps']:.2f} bps |")
    L.append(f"| Tracking error | {summary['tracking_error_pct']:.2f}%/yr |")
    L.append(f"| Correlation (full) | {summary['corr_full']:.3f} |")
    L.append(f"| Correlation (30d) | {summary['corr_30d']:.3f} |")
    if spy_metrics:
        L.append(f"| SPY total return (window) | {spy_metrics.get('Total_Return', 0)*100:+.2f}% |")
    L.append("")
    L.append("## 2. Verdict")
    L.append("")
    for level, msg in flags:
        emoji = {"OK": "✓", "WARN": "⚠", "ALERT": "✗", "INFO": "ℹ"}.get(level, "?")
        L.append(f"- {emoji}  **{level}** — {msg}")
    L.append("")
    L.append("## 3. Daily detail")
    L.append("")
    L.append("| Date | Regime | Cash% | Live $ | Sim $ | ret_live | ret_sim | Δ ret | Δ cum |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in merged.iterrows():
        L.append(
            f"| {r['Date']} | {r['Regime']} | {r['CashPct']:.1f}% "
            f"| ${r['TotalCapital']:,.2f} | ${r['TotalCapital_sim']:,.2f} "
            f"| {r['ret_live']*100:+.3f}% | {r['ret_sim']*100:+.3f}% "
            f"| {r['delta_ret']*10000:+.2f}bp | {r['delta_cum_pp']:+.2f}pp |"
        )
    L.append("")
    L.append("---")
    L.append("")
    L.append("**Reading guide**")
    L.append("")
    L.append("- During the ramp-up period (`Cash% > 50`) the simulator deploys faster "
             "than the live runner; some divergence is expected and not actionable.")
    L.append("- The steady-state divergence is what to watch. A consistently negative "
             "`Δ ret` across many days suggests live execution is leaking value vs the "
             "backtest (slippage, fill timing, manual interventions).")
    L.append("- A correlation drop without a Δ_cum drift change indicates **noise** "
             "(uncorrelated trade timing); a Δ_cum drift without correlation drop "
             "indicates **bias** (systematic over- or under-performance).")
    with open(md_path, "w") as f:
        f.write("\n".join(L))


def main() -> int:
    parser = argparse.ArgumentParser(description="C2 — live vs backtest drift tracker")
    parser.add_argument("--start", default=None, help="Window start (default: live first date)")
    parser.add_argument("--end", default=None, help="Window end (default: live last date)")
    parser.add_argument("--initial-capital", type=float, default=None,
                        help="Override (default: live first day TotalCapital)")
    parser.add_argument("--daily-buy-limit", type=float, default=None,
                        help="Override (default: from config)")
    parser.add_argument("--no-spy", action="store_true",
                        help="Skip SPY benchmark fetch (avoids cache touches)")
    parser.add_argument("--buy-grace-days", type=int, default=None,
                        help="Override simulator buy_grace_days (default: read from config). "
                             "Use 0 to match a live run that did not yet have grace persistence.")
    args = parser.parse_args()

    print("=" * 72)
    print("  C2 — Live vs Backtest Drift Tracker")
    print("=" * 72)

    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    holdings_path = conf["paths"]["holdings_log"]
    signal_path = conf["paths"]["frozen_signal"]
    fixed_buy_limit = float(conf["portfolio"].get("daily_buy_limit", 1000.0)) \
        if args.daily_buy_limit is None else float(args.daily_buy_limit)
    initial_cap_cfg = float(conf["portfolio"].get("initial_cash", 100000.0))

    print(f"[live] holdings = {holdings_path}")
    print(f"[live] signal   = {os.path.basename(signal_path)}")

    live = _load_live_daily_log(holdings_path)
    if live.empty:
        print("[live] DailyLog is empty — nothing to compare.")
        return 0

    start_d = args.start or live.iloc[0]["Date"]
    end_d   = args.end   or live.iloc[-1]["Date"]
    live = live[(live["Date"] >= start_d) & (live["Date"] <= end_d)].reset_index(drop=True)
    if live.empty:
        print(f"[live] no rows in [{start_d}, {end_d}]")
        return 0

    initial_capital = (
        float(args.initial_capital) if args.initial_capital is not None
        else float(live.iloc[0]["TotalCapital"])
    )
    print(f"[live] window       = {start_d} → {end_d}  ({len(live)} rows)")
    print(f"[live] initial cap  = ${initial_capital:,.2f}")
    print(f"[live] daily limit  = ${fixed_buy_limit:,.2f}")

    cfg, pack, pack_start, pack_end = _build_fresh_pack(conf, start_d, end_d)
    vc, vr, vs = _load_vix(cfg, pack_start, pack_end)
    trigger_conf = conf.get("triggers", {})

    _bg_cfg = int(conf["strategy"].get("buy_grace_days", 0) or 0)
    _bg_eff = int(args.buy_grace_days) if args.buy_grace_days is not None else _bg_cfg
    print(f"[sim ] running simulator over {start_d} → {end_d}  "
          f"(buy_grace_days={_bg_eff}{' [override; cfg=%d]' % _bg_cfg if _bg_eff != _bg_cfg else ''})…")
    t0 = time.time()
    sim = _run_simulator(
        cfg, pack, signal_path, vc, vr, vs, trigger_conf,
        initial_capital, fixed_buy_limit, start_d, end_d,
        buy_grace_days=_bg_eff,
    )
    print(f"[sim ] done ({time.time() - t0:.1f}s, {len(sim)} sim days)")

    merged, summary = _compute_drift(live, sim, initial_capital)
    if isinstance(summary, dict) and summary.get("error"):
        print(f"[ERROR] {summary['error']} — no overlap between live and sim dates")
        return 2

    spy_metrics = None
    if not args.no_spy:
        try:
            spy_series = _load_spy_series(cfg, pack_start, pack_end)
            spy_metrics = _spy_window_metrics(spy_series, start_d, end_d)
        except Exception as e:
            print(f"[spy ] skipped: {e}")
            spy_metrics = None

    flags = _classify(summary)
    _print_console(merged, summary, flags, spy_metrics, initial_capital)

    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(DOCS_DIR, f"live_drift_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"live_drift_{stamp}.json")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_start": start_d, "window_end": end_d,
        "signal_basename": os.path.basename(signal_path),
        "initial_capital": initial_capital,
        "daily_buy_limit": fixed_buy_limit,
        "summary": summary,
        "flags": [{"level": lvl, "message": msg} for lvl, msg in flags],
        "spy": spy_metrics,
        "rows": merged.assign(
            TotalCapital=merged["TotalCapital"].astype(float),
            TotalCapital_sim=merged["TotalCapital_sim"].astype(float),
            ret_live=merged["ret_live"].astype(float),
            ret_sim=merged["ret_sim"].astype(float),
            delta_ret=merged["delta_ret"].astype(float),
            delta_cum_pp=merged["delta_cum_pp"].astype(float),
            ramp_up=merged["ramp_up"].astype(bool),
        ).to_dict(orient="records"),
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    _write_md(merged, summary, flags, spy_metrics, md_path,
              os.path.basename(signal_path), initial_capital, fixed_buy_limit)
    print(f"[saved] {md_path}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
