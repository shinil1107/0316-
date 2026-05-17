"""Step E — SPY buy-and-hold benchmark + G7 gate evaluation.

Computes the buy-and-hold SPY benchmark over the same windows used by
Step D walk-forward (full period + 6 folds), then evaluates G7 gates
against an existing walk-forward result JSON:

    G7-A  full-period CAGR(strat)   ≥ CAGR(SPY)
    G7-B  full-period Sharpe(strat) ≥ Sharpe(SPY)
    G7-C  per-fold:  CAGR(strat,fold) ≥ CAGR(SPY,fold)  in ≥ 5/6 folds
    G7-D  Information Ratio          ≥ 0.5
              IR = mean(active_daily_ret) / std(active_daily_ret) * sqrt(252)
              active = strat_daily_ret − SPY_daily_ret
          (per-fold; aggregated as mean across folds for the gate)

Why this exists
---------------
Until now every gate has been *relative to Baseline_V2*. There has been
no *absolute* benchmark against the market itself. F2 (2021-2022) showed
Baseline +4.30% CAGR which we tagged as "worst fold" — but if SPY was
deeply negative in 2022, +4.30% is in fact strong alpha, not weakness.
G7 establishes that absolute reference.

Usage
-----
    # Standalone — print SPY metrics for full period + 6 folds
    python3 -u phase3/tests/step_e_spy_benchmark.py

    # Merge with a walk-forward result and compute G7 gates
    python3 -u phase3/tests/step_e_spy_benchmark.py \
        --walk-forward phase3/docs/t5_walk_forward_results_<TS>.json \
        --signals baseline,t1b_inj,p6_ens_c

Outputs
-------
- Console SPY benchmark table + (if --walk-forward) G7 verdict per signal
- `phase3/docs/step_e_spy_benchmark_<TS>.json`
- `phase3/docs/step_e_spy_benchmark_<TS>.md`
"""
from __future__ import annotations

import os as _os
_os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import argparse
import json
import os
import sys
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


# ── 6-fold design (mirrors step_d_walk_forward.py) ───────────────────
PACK_START_STR = "2011-01-03"
PACK_END_STR   = "2026-02-27"

FOLDS: List[Dict[str, str]] = [
    {"id": "F0a", "start": "2012-01-01", "end": "2014-12-31", "group": "pre_oos"},
    {"id": "F0b", "start": "2015-01-01", "end": "2016-12-31", "group": "pre_oos"},
    {"id": "F1",  "start": "2019-01-01", "end": "2020-12-31", "group": "in_sample"},
    {"id": "F2",  "start": "2021-01-01", "end": "2022-12-31", "group": "in_sample"},
    {"id": "F3",  "start": "2023-01-01", "end": "2024-05-31", "group": "in_sample"},
    {"id": "F4",  "start": "2024-06-01", "end": PACK_END_STR, "group": "post_oos"},
]
FULL = {"id": "FULL", "start": "2012-01-01", "end": PACK_END_STR, "group": "full"}

DOCS_DIR = os.path.join(PHASE3_DIR, "docs")


# ── Config builder (light — we only need fmp_cache_root) ─────────────
def _build_minimal_cfg() -> Any:
    with open(os.path.join(PHASE3_DIR, "config.yaml"), "r") as f:
        conf = yaml.safe_load(f)
    cfg = engine.Config()
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]
    cfg.save_dir = conf["paths"]["output_dir"]
    return cfg


# ── SPY series loader (cached + auto-download fallback) ──────────────
_SPY_CACHE: Optional[pd.DataFrame] = None


def _load_spy_series(cfg, start: str, end: str) -> pd.DataFrame:
    """Return DataFrame with columns ['date' (datetime64), 'close' (float)]
    sorted ascending, covering [start, end] inclusive."""
    global _SPY_CACHE

    sd = datetime.strptime(start, "%Y-%m-%d") - timedelta(days=10)
    ed = datetime.strptime(end,   "%Y-%m-%d") + timedelta(days=10)

    if _SPY_CACHE is None:
        df = engine.load_ohlcv_from_cache(cfg, "SPY", sd, ed)
        if df is None or df.empty:
            print("[SPY] cache empty → downloading…")
            engine.download_ohlcv_to_cache_chunked(cfg, "SPY", sd, ed, overwrite=True)
            df = engine.load_ohlcv_from_cache(cfg, "SPY", sd, ed)
        if df is None or df.empty:
            raise RuntimeError("Failed to obtain SPY OHLCV data.")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        _SPY_CACHE = df[["date", "close"]].copy()
        _SPY_CACHE["close"] = _SPY_CACHE["close"].astype(float)

    sd_ts = pd.Timestamp(start)
    ed_ts = pd.Timestamp(end)
    out = _SPY_CACHE[(_SPY_CACHE["date"] >= sd_ts) & (_SPY_CACHE["date"] <= ed_ts)].copy()
    out = out.reset_index(drop=True)
    return out


# ── Metric computation ───────────────────────────────────────────────
def _compute_metrics_from_prices(prices: np.ndarray, dates: np.ndarray) -> Dict[str, float]:
    """Pure buy-and-hold metrics from a daily close-price series.

    Convention: $1 invested at prices[0]; capital trajectory follows
    prices / prices[0]. Returns CAGR, Sharpe (daily*sqrt(252), ann),
    MDD, Calmar. No cost, no slippage — this is the benchmark.
    """
    p = np.asarray(prices, dtype=float)
    if len(p) < 2 or not np.isfinite(p[0]) or p[0] <= 0:
        return {"CAGR": float("nan"), "Sharpe": float("nan"),
                "MDD": float("nan"), "Calmar": float("nan"),
                "Total_Return": float("nan"), "Years": 0.0,
                "n_days": 0}

    capital = p / p[0]
    daily_rets = np.diff(capital) / capital[:-1]
    daily_rets = daily_rets[np.isfinite(daily_rets)]

    n_days = len(p)
    years = max(
        (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25,
        1.0 / 365.25,
    )
    cagr = float(capital[-1] ** (1.0 / years) - 1.0)
    total_ret = float(capital[-1] - 1.0)

    if daily_rets.size > 1 and daily_rets.std(ddof=0) > 1e-12:
        sharpe = float(daily_rets.mean() / daily_rets.std(ddof=0) * np.sqrt(252.0))
    else:
        sharpe = float("nan")

    peaks = np.maximum.accumulate(capital)
    dd = capital / peaks - 1.0
    mdd = float(-dd.min()) if dd.size else float("nan")

    calmar = float(cagr / mdd) if mdd > 1e-9 else float("nan")

    return {
        "CAGR":         cagr,
        "Sharpe":       sharpe,
        "MDD":          mdd,
        "Calmar":       calmar,
        "Total_Return": total_ret,
        "Years":        round(years, 3),
        "n_days":       int(n_days),
    }


def _spy_window_metrics(cfg, start: str, end: str) -> Dict[str, Any]:
    df = _load_spy_series(cfg, start, end)
    if df.empty:
        return {"window_start": start, "window_end": end, "metrics": {},
                "n_days": 0, "error": "no SPY data in window"}
    prices = df["close"].to_numpy()
    dates = df["date"].to_numpy()
    metrics = _compute_metrics_from_prices(prices, dates)
    return {"window_start": start, "window_end": end,
            "actual_start": str(pd.Timestamp(dates[0]).date()),
            "actual_end":   str(pd.Timestamp(dates[-1]).date()),
            "metrics": metrics}


# ── G7 gate evaluation against walk-forward report ──────────────────
def _strategy_full_period_metrics(
    cfg, signal_record: Dict[str, Any], walk_forward_pack_path: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Read full-period metrics for a signal from the existing
    p2_p6_ensemble_*_vs_baseline JSONs (if available). Else returns None
    and G7-A/B/D fall back to per-fold approximation."""
    return None  # placeholder — currently we only compute G7-C via folds


def _g7_evaluate(
    signal_arm: str,
    signal_folds: List[Dict[str, Any]],
    spy_folds_by_id: Dict[str, Dict[str, Any]],
    full_strat: Optional[Dict[str, float]] = None,
    full_spy: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute G7-A/B/C/D verdicts.

    G7-A,B require a `full_strat` and `full_spy` (full-period metrics).
    G7-C is purely per-fold and always computable.
    G7-D needs per-fold daily returns (not in standard walk-forward
    output); for now we approximate via per-fold CAGR Sharpe difference.
    """
    out: Dict[str, Any] = {}

    # G7-A / G7-B (full-period)
    if full_strat is not None and full_spy is not None:
        cs = full_strat.get("CAGR", float("nan"))
        cy = full_spy.get("CAGR", float("nan"))
        ss = full_strat.get("Sharpe", float("nan"))
        sy = full_spy.get("Sharpe", float("nan"))
        out["G7_A_cagr_ge_spy"] = {
            "cagr_strat": cs, "cagr_spy": cy,
            "delta": cs - cy if np.isfinite(cs) and np.isfinite(cy) else float("nan"),
            "pass": bool(np.isfinite(cs) and np.isfinite(cy) and cs >= cy - 1e-9),
        }
        out["G7_B_sharpe_ge_spy"] = {
            "sharpe_strat": ss, "sharpe_spy": sy,
            "delta": ss - sy if np.isfinite(ss) and np.isfinite(sy) else float("nan"),
            "pass": bool(np.isfinite(ss) and np.isfinite(sy) and ss >= sy - 1e-9),
        }

    # G7-C (per-fold, ≥ 5/6 folds)
    win_folds: List[str] = []
    loss_folds: List[str] = []
    for fr in signal_folds:
        fid = fr["fold"]
        spy_rec = spy_folds_by_id.get(fid)
        if spy_rec is None:
            continue
        cs = float(fr["metrics"].get("CAGR", float("nan")))
        cy = float(spy_rec["metrics"].get("CAGR", float("nan")))
        if not (np.isfinite(cs) and np.isfinite(cy)):
            continue
        if cs >= cy - 1e-9:
            win_folds.append(fid)
        else:
            loss_folds.append(fid)
    n_win = len(win_folds)
    n_total = n_win + len(loss_folds)
    out["G7_C_per_fold_beats_spy"] = {
        "wins": n_win,
        "total": n_total,
        "win_folds": win_folds,
        "loss_folds": loss_folds,
        "threshold": "≥5/6",
        "pass": bool(n_win >= 5 and n_total >= 6),
    }

    # G7-D (Information Ratio approximation: per-fold mean alpha CAGR /
    # cross-fold std). True IR uses daily active returns; we use fold-level
    # means as a coarse proxy.
    alpha_per_fold: List[float] = []
    for fr in signal_folds:
        fid = fr["fold"]
        spy_rec = spy_folds_by_id.get(fid)
        if spy_rec is None:
            continue
        cs = float(fr["metrics"].get("CAGR", float("nan")))
        cy = float(spy_rec["metrics"].get("CAGR", float("nan")))
        if np.isfinite(cs) and np.isfinite(cy):
            alpha_per_fold.append(cs - cy)
    if len(alpha_per_fold) >= 2:
        arr = np.asarray(alpha_per_fold)
        mu = float(arr.mean())
        sd = float(arr.std(ddof=0))
        ir = float(mu / sd) if sd > 1e-9 else float("nan")
    else:
        mu, sd, ir = float("nan"), float("nan"), float("nan")
    out["G7_D_info_ratio_proxy"] = {
        "mean_alpha": mu,
        "std_alpha": sd,
        "ir_proxy":  ir,
        "threshold": "≥0.5 (proxy: per-fold alpha mean/std, NOT true daily IR)",
        "pass": bool(np.isfinite(ir) and ir >= 0.5 - 1e-9),
    }

    return out


# ── Output writers ───────────────────────────────────────────────────
def _print_spy_table(spy_full: Dict[str, Any], spy_folds: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 88)
    print("  SPY BUY-AND-HOLD BENCHMARK (no commission, no slippage)")
    print("=" * 88)
    print(f"{'Window':<20s} {'Range':<28s} {'CAGR':>9s} {'Sharpe':>8s} "
          f"{'MDD':>7s} {'Calmar':>8s} {'TotalRet':>10s}")
    print("-" * 88)

    def _row(label: str, rec: Dict[str, Any]) -> None:
        m = rec.get("metrics", {})
        rng = f"{rec.get('actual_start','?')} → {rec.get('actual_end','?')}"
        cagr   = f"{m['CAGR']*100:+.2f}%"   if np.isfinite(m.get("CAGR", float("nan")))   else "n/a"
        sharpe = f"{m['Sharpe']:+.3f}"      if np.isfinite(m.get("Sharpe", float("nan"))) else "n/a"
        mdd    = f"{m['MDD']*100:.2f}%"     if np.isfinite(m.get("MDD", float("nan")))    else "n/a"
        calmar = f"{m['Calmar']:.3f}"       if np.isfinite(m.get("Calmar", float("nan"))) else "n/a"
        tret   = f"{m['Total_Return']*100:+.1f}%" if np.isfinite(m.get("Total_Return", float("nan"))) else "n/a"
        print(f"{label:<20s} {rng:<28s} {cagr:>9s} {sharpe:>8s} {mdd:>7s} {calmar:>8s} {tret:>10s}")

    _row("FULL", spy_full)
    print("-" * 88)
    for f in spy_folds:
        _row(f["fold_id"], f)
    print("=" * 88)


def _print_g7_block(arm: str, gates: Dict[str, Any]) -> None:
    print()
    print("─" * 70)
    print(f"  G7 verdict — {arm}")
    print("─" * 70)
    if "G7_A_cagr_ge_spy" in gates:
        g = gates["G7_A_cagr_ge_spy"]
        mark = "✓" if g["pass"] else "✗"
        print(f"  [{mark}] G7-A  full CAGR vs SPY    "
              f"strat={g['cagr_strat']*100:+.2f}%  spy={g['cagr_spy']*100:+.2f}%  "
              f"Δ={g['delta']*100:+.2f}pp")
    if "G7_B_sharpe_ge_spy" in gates:
        g = gates["G7_B_sharpe_ge_spy"]
        mark = "✓" if g["pass"] else "✗"
        print(f"  [{mark}] G7-B  full Sharpe vs SPY  "
              f"strat={g['sharpe_strat']:+.3f}    spy={g['sharpe_spy']:+.3f}    "
              f"Δ={g['delta']:+.3f}")
    g = gates["G7_C_per_fold_beats_spy"]
    mark = "✓" if g["pass"] else "✗"
    print(f"  [{mark}] G7-C  per-fold beats SPY  {g['wins']}/{g['total']} folds  "
          f"(wins={','.join(g['win_folds']) or '-'}; loss={','.join(g['loss_folds']) or '-'})")
    g = gates["G7_D_info_ratio_proxy"]
    mark = "✓" if g["pass"] else "✗"
    print(f"  [{mark}] G7-D  IR proxy             "
          f"mean(α)={g['mean_alpha']*100:+.2f}pp  std(α)={g['std_alpha']*100:.2f}pp  "
          f"IR≈{g['ir_proxy']:+.3f}")


def _write_md(out_path: str, spy_full: Dict[str, Any], spy_folds: List[Dict[str, Any]],
              merge: Optional[Dict[str, Any]]) -> None:
    L: List[str] = []
    L.append("# Step E — SPY Benchmark + G7 Gates")
    L.append("")
    L.append(f"**Generated**: {datetime.now().isoformat(timespec='seconds')}")
    L.append("")
    L.append("## 1. SPY Buy-and-Hold (no cost)")
    L.append("")
    L.append("| Window | Range | CAGR | Sharpe | MDD | Calmar | Total Ret |")
    L.append("|---|---|---:|---:|---:|---:|---:|")

    def _md_row(label: str, rec: Dict[str, Any]) -> str:
        m = rec.get("metrics", {})
        rng = f"{rec.get('actual_start','?')} → {rec.get('actual_end','?')}"
        cagr   = f"{m['CAGR']*100:+.2f}%"   if np.isfinite(m.get("CAGR", float("nan")))   else "n/a"
        sharpe = f"{m['Sharpe']:+.3f}"      if np.isfinite(m.get("Sharpe", float("nan"))) else "n/a"
        mdd    = f"{m['MDD']*100:.2f}%"     if np.isfinite(m.get("MDD", float("nan")))    else "n/a"
        calmar = f"{m['Calmar']:.3f}"       if np.isfinite(m.get("Calmar", float("nan"))) else "n/a"
        tret   = f"{m['Total_Return']*100:+.1f}%" if np.isfinite(m.get("Total_Return", float("nan"))) else "n/a"
        return f"| **{label}** | {rng} | {cagr} | {sharpe} | {mdd} | {calmar} | {tret} |"

    L.append(_md_row("FULL", spy_full))
    for f in spy_folds:
        L.append(_md_row(f["fold_id"], f))
    L.append("")

    if merge:
        L.append("## 2. G7 Gate Verdicts (per signal)")
        L.append("")
        L.append("| Signal | G7-A (CAGR≥SPY) | G7-B (Sharpe≥SPY) | G7-C (per-fold ≥5/6) | G7-D (IR≥0.5 proxy) | mean α (pp) | wins/total |")
        L.append("|---|:---:|:---:|:---:|:---:|---:|:---:|")
        def _yn(d: Optional[Dict[str, Any]]) -> str:
            if d is None: return "—"
            return "✓" if d.get("pass") else "✗"
        for arm, gates in merge.items():
            ga = gates.get("G7_A_cagr_ge_spy")
            gb = gates.get("G7_B_sharpe_ge_spy")
            gc = gates["G7_C_per_fold_beats_spy"]
            gd = gates["G7_D_info_ratio_proxy"]
            mu = gd.get("mean_alpha", float("nan"))
            mu_s = f"{mu*100:+.2f}" if np.isfinite(mu) else "n/a"
            L.append(f"| **{arm}** | {_yn(ga)} | {_yn(gb)} | {_yn(gc)} | {_yn(gd)} | {mu_s} | {gc['wins']}/{gc['total']} |")
        L.append("")
        L.append("### Per-fold alpha breakdown")
        L.append("")
        L.append("| Signal | F0a | F0b | F1 | F2 | F3 | F4 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        for arm, gates in merge.items():
            gc = gates["G7_C_per_fold_beats_spy"]
            row = [f"**{arm}**"]
            wins_set = set(gc["win_folds"])
            losses_set = set(gc["loss_folds"])
            for fid in ["F0a", "F0b", "F1", "F2", "F3", "F4"]:
                if fid in wins_set:
                    row.append("✓")
                elif fid in losses_set:
                    row.append("✗")
                else:
                    row.append("—")
            L.append("| " + " | ".join(row) + " |")
        L.append("")

    L.append("---")
    L.append("")
    L.append("**Notes**")
    L.append("")
    L.append("- SPY is buy-and-hold from window start to window end at daily close, no costs/slippage.")
    L.append("- G7-A/B require a strategy *full-period* metric; if absent (e.g. only walk-forward folds were provided), they are skipped.")
    L.append("- G7-D is a *per-fold-CAGR* proxy IR, not a true daily-return IR. Treat as directional signal only.")

    with open(out_path, "w") as f:
        f.write("\n".join(L))


# ── CLI driver ───────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Step E — SPY benchmark + G7 gate evaluation")
    parser.add_argument(
        "--walk-forward", default=None,
        help="Path to a step_d_walk_forward results JSON to merge & evaluate G7-C/D against.",
    )
    parser.add_argument(
        "--full-period-json", default=None,
        help="Optional: path to a full-period A/B JSON (e.g. p2_p6_ensemble_*_vs_baseline_*.json) "
             "for G7-A/B. If multiple, comma-separated.",
    )
    parser.add_argument(
        "--signals", default="all",
        help="Filter merge to comma-separated signal arm names (default: all in walk-forward report).",
    )
    args = parser.parse_args()

    cfg = _build_minimal_cfg()

    print("=" * 72)
    print("  Step E — SPY Benchmark + G7 Gate Evaluation")
    print("=" * 72)

    # 1. SPY full + folds
    print(f"[SPY] loading SPY OHLCV from cache ({PACK_START_STR} → {PACK_END_STR})…")
    spy_full = _spy_window_metrics(cfg, FULL["start"], FULL["end"])
    spy_full["fold_id"] = "FULL"
    spy_folds: List[Dict[str, Any]] = []
    for fold in FOLDS:
        rec = _spy_window_metrics(cfg, fold["start"], fold["end"])
        rec["fold_id"] = fold["id"]
        rec["group"] = fold["group"]
        spy_folds.append(rec)

    _print_spy_table(spy_full, spy_folds)

    # 2. Merge with walk-forward + optional full-period A/B JSONs
    merge: Dict[str, Dict[str, Any]] = {}
    if args.walk_forward:
        wf_path = args.walk_forward
        if not os.path.isabs(wf_path):
            wf_path = os.path.join(ROOT, wf_path) if not wf_path.startswith("phase3") \
                else os.path.join(ROOT, wf_path)
        with open(wf_path, "r") as f:
            wf = json.load(f)

        spy_folds_by_id = {f["fold_id"]: f for f in spy_folds}

        # Load full-period A/B JSON(s) → arm_name -> metrics dict
        full_strat_lookup: Dict[str, Dict[str, float]] = {}
        if args.full_period_json:
            for p in args.full_period_json.split(","):
                p = p.strip()
                if not os.path.isabs(p):
                    p = os.path.join(ROOT, p)
                with open(p, "r") as fh:
                    fp = json.load(fh)
                # leg_a / leg_b structure (from p2_ensemble_vs_baseline.py)
                for leg_key in ("leg_a", "leg_b"):
                    leg = fp.get(leg_key)
                    if leg is None:
                        continue
                    name = leg.get("name")
                    m = leg.get("metrics", {})
                    if name and "CAGR" in m:
                        full_strat_lookup[name] = {
                            "CAGR":   float(m.get("CAGR", float("nan"))),
                            "Sharpe": float(m.get("Net_Sharpe", float("nan"))),
                            "MDD":    float(m.get("Max_Drawdown", float("nan"))),
                        }

        # Filter signals
        if args.signals == "all":
            wanted: Optional[set] = None
        else:
            wanted = {s.strip() for s in args.signals.split(",")}

        for sig in wf["per_signal"]:
            arm = sig["arm"]
            sig_id = sig["signal_id"]
            if wanted is not None and (sig_id not in wanted and arm not in wanted):
                continue
            full_strat = full_strat_lookup.get(arm)
            full_spy = spy_full["metrics"] if full_strat is not None else None
            gates = _g7_evaluate(arm, sig["folds"], spy_folds_by_id,
                                 full_strat=full_strat, full_spy=full_spy)
            merge[arm] = gates
            _print_g7_block(arm, gates)

    # 3. Persist
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(DOCS_DIR, f"step_e_spy_benchmark_{ts}.json")
    md_path   = os.path.join(DOCS_DIR, f"step_e_spy_benchmark_{ts}.md")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "spy_full":  spy_full,
        "spy_folds": spy_folds,
        "g7_per_signal": merge,
        "args": {
            "walk_forward":     args.walk_forward,
            "full_period_json": args.full_period_json,
            "signals":          args.signals,
        },
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    _write_md(md_path, spy_full, spy_folds, merge if merge else None)

    print()
    print(f"[saved] {json_path}")
    print(f"[saved] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
