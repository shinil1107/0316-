"""
Phase 3 Lab — multi-arm strategy sweep.

Build the pack once, then run N strategy variants in parallel and compare.
"""

import sys, os, copy, time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable

sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────
# Default arms
# ─────────────────────────────────────────────

BASELINE_STRATEGY = {
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

SAMPLE_ARMS = {
    "A_baseline": {},
    "B_grace14": {"sell_grace_days": 14},
    "C_deploy15": {"adaptive_deploy_rate": 0.15, "target_invest_pct": 0.98},
    "D_grace14_deploy15": {
        "sell_grace_days": 14,
        "adaptive_deploy_rate": 0.15,
        "target_invest_pct": 0.98,
    },
    "E_no_stoploss": {"enable_stop_loss": False},
    "F_tight_trim": {"trim_threshold": 0.02, "enable_trim": True},
}

# Extended sweep: B/D/E 기반 확장 탐색 (legacy, baseline=grace7 기준)
SWEEP_ARMS = {
    "B1_grace10": {"sell_grace_days": 10},
    "B2_grace14": {"sell_grace_days": 14},
    "B3_grace21": {"sell_grace_days": 21},
    "B4_grace30": {"sell_grace_days": 30},
    "D1_g14_d15": {
        "sell_grace_days": 14, "adaptive_deploy_rate": 0.15,
        "target_invest_pct": 0.98,
    },
    "D2_g21_d15": {
        "sell_grace_days": 21, "adaptive_deploy_rate": 0.15,
        "target_invest_pct": 0.98,
    },
    "D3_g14_d20": {
        "sell_grace_days": 14, "adaptive_deploy_rate": 0.20,
        "target_invest_pct": 0.98,
    },
    "D4_g21_d20": {
        "sell_grace_days": 21, "adaptive_deploy_rate": 0.20,
        "target_invest_pct": 0.98,
    },
    "D5_g14_d15_inv99": {
        "sell_grace_days": 14, "adaptive_deploy_rate": 0.15,
        "target_invest_pct": 0.99,
    },
    "E1_noSL_g14": {
        "enable_stop_loss": False, "sell_grace_days": 14,
    },
    "E2_noSL_g14_d15": {
        "enable_stop_loss": False, "sell_grace_days": 14,
        "adaptive_deploy_rate": 0.15, "target_invest_pct": 0.98,
    },
    "E3_noSL_g21_d15": {
        "enable_stop_loss": False, "sell_grace_days": 21,
        "adaptive_deploy_rate": 0.15, "target_invest_pct": 0.98,
    },
    "E4_noSL_g21_d20": {
        "enable_stop_loss": False, "sell_grace_days": 21,
        "adaptive_deploy_rate": 0.20, "target_invest_pct": 0.98,
    },
    "G1_gap1pct": {"rebalance_gap_threshold": 0.01, "sell_grace_days": 14},
    "G2_gap3pct": {"rebalance_gap_threshold": 0.03, "sell_grace_days": 14},
}

# ─────────────────────────────────────────────
# Sweep V2: B4_grace30 baseline + grace saturation + regime-adaptive
# ─────────────────────────────────────────────
# regime_overrides: per-regime parameter overrides merged on top of base
#   regime key mapping: BULL / SIDE / DEF (DEFENSIVE|CRASH → DEF)

SWEEP_V2_ARMS = {
    # ── Grace saturation reference ──
    "GS_grace45": {"sell_grace_days": 45},
    "GS_grace60": {"sell_grace_days": 60},
    "GS_grace90": {"sell_grace_days": 90},
    # Flat references
    "REF_g60_noSL": {"sell_grace_days": 60, "enable_stop_loss": False},
    "REF_g60_d15":  {"sell_grace_days": 60, "adaptive_deploy_rate": 0.15,
                     "target_invest_pct": 0.98},
}

# ─────────────────────────────────────────────
# Sweep V3: grace는 전 regime 60 고정, deploy/SL만 regime별 조절
# ─────────────────────────────────────────────
# 설계 원칙 (V2 실험 교훈):
#   1. grace는 모든 regime에서 60 유지 (줄이면 SIDE/DEF 손실)
#   2. deploy rate만 BULL↑ / SIDE·DEF 유지 or ↓
#   3. SL은 BULL 유지 / DEF 비활성 (반등 수익 보호)
#   4. target_invest_pct는 95%+ 유지 (현금 과다보유 금지)

SWEEP_V3_ARMS = {
    # ── R7: 기본형 — BULL deploy↑, DEF noSL ──
    "R7_base": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── R8: BULL 더 공격적 deploy ──
    "R8_bull_d20": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── R9: 전 regime noSL + BULL deploy↑ ──
    "R9_allNoSL_d15": {
        "sell_grace_days": 60,
        "enable_stop_loss": False,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15},
            "SIDE": {"adaptive_deploy_rate": 0.10},
            "DEF":  {"adaptive_deploy_rate": 0.10},
        },
    },

    # ── R10: SIDE deploy를 약간 줄여서 횡보장 현금 보존 ──
    "R10_side_cautious": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.07, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── R11: DEF에서도 SL 유지 (DEF noSL 효과 재검증) ──
    "R11_def_withSL": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": True},
        },
    },

    # ── R12: BULL max deploy + SIDE/DEF noSL ──
    "R12_bull_max_rest_noSL": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── R13: 전 regime deploy 15% 균일 + DEF만 noSL ──
    "R13_uniform_d15": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.15, "enable_stop_loss": False},
        },
    },

    # ── R14: BULL d20 + invest 98% / 나머지 유지 ──
    "R14_bull_invest98": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": True,
                     "target_invest_pct": 0.97},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False,
                     "target_invest_pct": 0.97},
        },
    },

    # ── R15: SIDE에서 deploy 살짝 올려보기 (횡보장 기회포착) ──
    "R15_side_d12": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"adaptive_deploy_rate": 0.12, "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── R16: grace 90 for SIDE only (SIDE는 더 길게) ──
    "R16_side_g90": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },
}

# ─────────────────────────────────────────────
# Sweep V4 (Final): V3 최적 조합 확인
# ─────────────────────────────────────────────
# V3 교훈:
#   SIDE grace=90 → SIDE +3.9%p (R16)
#   DEF noSL → DEF +23%p (R7 vs R11)
#   SIDE noSL → SIDE +3%p (R12)
#   BULL d20% → BULL +1.3%p (R8)
# → 이론적 최강 = BULL(g60,d20,SL,inv98) + SIDE(g90,noSL) + DEF(g60,noSL)

SWEEP_V4_ARMS = {
    # ── F1: 이론적 최강 조합 ──
    "F1_best_combo": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── F2: F1 + BULL d15% (deploy 효과 확인) ──
    "F2_best_d15": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── F3: F1 + SIDE도 d15 (SIDE deploy 상향) ──
    "F3_side_d15": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.15,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── F4: 전 regime noSL + g90 SIDE ──
    "F4_allNoSL_g90side": {
        "sell_grace_days": 60,
        "enable_stop_loss": False,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10},
            "DEF":  {"adaptive_deploy_rate": 0.10},
        },
    },

    # ── F5: F1 + SIDE grace=120 (SIDE grace 한계 탐색) ──
    "F5_side_g120": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 120, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── F6: F1 + BULL grace=90 (BULL도 길게?) ──
    "F6_bull_g90": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.20,
                     "enable_stop_loss": True, "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # ── REF: V3 승자들 재포함 (대조군) ──
    "REF_R16": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.15, "enable_stop_loss": True},
            "SIDE": {"sell_grace_days": 90, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": True},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },
    "REF_R12": {
        "sell_grace_days": 60,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },
    "REF_flat_g60": {"sell_grace_days": 60},
}


def make_strategy(overrides: dict, base: dict = None) -> dict:
    s = dict(base or BASELINE_STRATEGY)
    for k, v in overrides.items():
        if k == "regime_overrides":
            s["regime_overrides"] = copy.deepcopy(v)
        else:
            s[k] = v
    return s


# ─────────────────────────────────────────────
# Lab runner
# ─────────────────────────────────────────────

def run_lab(
    arms: Dict[str, dict],
    start_date: str = "2017-01-03",
    end_date: Optional[str] = None,
    initial_capital: float = 100000.0,
    daily_buy_limit: float = 1000.0,
    rebalance_mode: str = "daily",
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    config_yaml_path: Optional[str] = None,
    progress_fn: Optional[Callable] = None,
) -> Dict:
    """
    Run multiple strategy arms sharing one pack.

    Parameters
    ----------
    arms : dict
        {arm_name: strategy_override_dict}. Each override is merged
        onto BASELINE_STRATEGY.
    start_date, end_date : str
    initial_capital, daily_buy_limit, rebalance_mode : see simulator
    config_yaml_path : str
        Path to config.yaml for paths / regime params.
    progress_fn : callable(msg: str)

    Returns
    -------
    dict with:
        results : {arm_name: simulator result dict}
        comparison : pd.DataFrame — side-by-side metrics
    """
    import yaml
    from engine_loader import engine
    from daily_runner import load_frozen_signal
    import simulator
    import dataclasses

    _log = progress_fn or (lambda m: print(m))

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    config_path = config_yaml_path or os.path.join(
        os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        conf = yaml.safe_load(f)

    # ── Step 1: Load signal ──
    _log("[Lab Step 1/3] Loading frozen signal...")
    signal = load_frozen_signal(conf["paths"]["frozen_signal"])

    # ── Step 2: Build pack (one-time, shared across arms) ──
    _log(f"[Lab Step 2/3] Building pack ({start_date} ~ {end_date})...")
    _log("  This may take 30+ minutes for long date ranges.")

    cfg = engine.Config()
    for k, v in conf.get("regime", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, type(getattr(cfg, k))(v))

    cfg.start_panel_date = datetime.strptime(start_date, "%Y-%m-%d")
    cfg.end_date = datetime.strptime(end_date, "%Y-%m-%d")
    cfg.enable_historical_universe = True
    cfg.historical_universe_expand_tickers = True
    cfg.enable_coverage_based_universe = True
    cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]

    result = engine.prepare_inputs(cfg)
    pack = result["pack"] if isinstance(result, dict) and "pack" in result else result
    _log(f"  Pack ready: {len(pack['tickers'])} tickers, {len(pack['dates'])} dates")

    # VIX regime
    _log("  Building VIX regime...")
    vix_df = engine.build_vix_regime_timeseries(
        cfg,
        datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=60),
        datetime.strptime(end_date, "%Y-%m-%d"),
    )
    vix_close_map, vix_regime_map = {}, {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
            vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
    _log(f"  VIX data: {len(vix_close_map)} dates")

    # ── Step 3: Run each arm ──
    _log(f"\n[Lab Step 3/3] Running {len(arms)} arms...")
    results = {}
    trigger_conf = conf.get("triggers", {})

    for i, (arm_name, overrides) in enumerate(arms.items(), 1):
        strat = make_strategy(overrides)
        _log(f"\n  ── Arm {i}/{len(arms)}: {arm_name} ──")
        changes = {k: v for k, v in overrides.items()} if overrides else {"(baseline)": ""}
        for k, v in changes.items():
            _log(f"    {k} = {v}")

        t0 = time.time()
        res = simulator.run_simulation(
            engine=engine,
            cfg=cfg,
            pack=pack,
            signal=signal,
            vix_close_by_date=vix_close_map,
            vix_regime_by_date=vix_regime_map,
            initial_capital=initial_capital,
            daily_buy_limit=daily_buy_limit,
            strategy_conf=strat,
            trigger_conf=trigger_conf,
            rebalance_mode=rebalance_mode,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            start_date=start_date,
            end_date=end_date,
            progress_fn=lambda c, t, m: None,
        )
        elapsed = time.time() - t0
        m = res["metrics"]
        _log(f"    CAGR={m.get('CAGR',0)*100:+.2f}%  "
             f"Sharpe={m.get('Net_Sharpe',0):.3f}  "
             f"MDD={m.get('Max_Drawdown',0)*100:.1f}%  "
             f"Calmar={m.get('Calmar_Ratio',0):.3f}  "
             f"Commission=${m.get('Total_Commission',0):,.0f}  "
             f"({elapsed:.1f}s)")
        results[arm_name] = res

    # ── Comparison table ──
    comparison = _build_comparison(results)
    regime_comp = _build_regime_comparison(results)

    _log(f"\n{'='*90}")
    _log(" Phase 3 Lab — Overall Comparison")
    _log(f"{'='*90}")
    _log(comparison.to_string())

    _log(f"\n{'='*90}")
    _log(" Phase 3 Lab — Regime Breakdown")
    _log(f"{'='*90}")
    _log(regime_comp.to_string())

    return {
        "results": results,
        "comparison": comparison,
        "regime_comparison": regime_comp,
    }


def _build_comparison(results: Dict) -> pd.DataFrame:
    rows = []
    for name, res in results.items():
        m = res["metrics"]
        tr = res.get("trades", pd.DataFrame())
        ts = res.get("daily_ts", pd.DataFrame())

        avg_util = 0
        if not ts.empty:
            avg_util = (ts["HoldingsValue"] / ts["PortfolioValue"]).mean() * 100

        n_sells = len(tr[tr["Action"].isin(["SELL", "STOP_LOSS"])]) if not tr.empty else 0
        n_buys = len(tr[tr["Action"].isin(["BUY_NEW", "BUY_MORE"])]) if not tr.empty else 0

        rows.append({
            "Arm": name,
            "CAGR%": round(m.get("CAGR", 0) * 100, 2),
            "Sharpe": round(m.get("Net_Sharpe", 0), 3),
            "MDD%": round(m.get("Max_Drawdown", 0) * 100, 2),
            "Calmar": round(m.get("Calmar_Ratio", 0), 3),
            "Return%": round(m.get("Total_Return", 0) * 100, 1),
            "AvgUtil%": round(avg_util, 1),
            "Sells": n_sells,
            "Comm$": round(m.get("Total_Commission", 0)),
            "Final$": round(m.get("Final_Value", 0)),
        })

    df = pd.DataFrame(rows).set_index("Arm")
    df = df.sort_values("CAGR%", ascending=False)
    return df


def _build_regime_comparison(results: Dict) -> pd.DataFrame:
    """Build side-by-side regime metrics for all arms."""
    rows = []
    for name, res in results.items():
        m = res["metrics"]
        row = {"Arm": name}
        for rg in ["BULL", "SIDE", "DEF"]:
            row[f"{rg}_Days"] = m.get(f"{rg}_Days", 0)
            row[f"{rg}_MaxStr"] = m.get(f"{rg}_MaxStreak", 0)
            row[f"{rg}_Ann%"] = round(m.get(f"{rg}_AnnRet", 0) * 100, 2)
            row[f"{rg}_Shrp"] = round(m.get(f"{rg}_Sharpe", 0), 3)
            row[f"{rg}_MDD%"] = round(m.get(f"{rg}_MDD", 0) * 100, 2)
            row[f"{rg}_Calm"] = round(m.get(f"{rg}_Calmar", 0), 3)
            row[f"{rg}_Win%"] = round(m.get(f"{rg}_WinRate", 0) * 100, 1)
        rows.append(row)

    df = pd.DataFrame(rows).set_index("Arm")
    df = df.sort_values("BULL_Ann%", ascending=False)
    return df


def format_lab_report(lab_result: dict) -> str:
    comp = lab_result["comparison"]
    rcomp = lab_result.get("regime_comparison", pd.DataFrame())
    lines = [
        "=" * 90,
        " Phase 3 Lab — Overall Comparison",
        "=" * 90,
        "",
        comp.to_string(),
        "",
    ]
    if not rcomp.empty:
        lines += [
            "=" * 90,
            " Phase 3 Lab — Regime Breakdown",
            "=" * 90,
            "",
            rcomp.to_string(),
            "",
        ]
    lines.append("=" * 90)
    return "\n".join(lines)


def save_lab_results(lab_result: dict, output_dir: str):
    """Save all arm results to CSV files."""
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")

    comp_path = os.path.join(output_dir, f"lab_comparison_{tag}.csv")
    lab_result["comparison"].to_csv(comp_path)

    rcomp = lab_result.get("regime_comparison")
    if rcomp is not None and not rcomp.empty:
        rcomp.to_csv(os.path.join(output_dir, f"lab_regime_{tag}.csv"))

    for name, res in lab_result["results"].items():
        safe_name = name.replace(" ", "_")
        ts_path = os.path.join(output_dir, f"lab_{safe_name}_ts_{tag}.csv")
        res["daily_ts"].to_csv(ts_path, index=False)

    return comp_path
