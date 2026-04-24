"""End-to-End GA v2: optimize frozen signal directly against Phase 3 portfolio simulation.

v2 changes over v1:
  - Stronger Phase 1 health gate (IC≥0.015, PosIC≥55%, Spread≥0.005)
  - L2 weight regularization to prevent weight norm explosion
  - Immigration: inject fresh individuals every generation
  - Stronger mutation + no frozen-signal elite protection
  - 70% budget target (~8 hours with P=30, G=32)
"""

import copy
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from phase3_lab import BASELINE_STRATEGY, make_strategy


# ─────────────────────────────────────────────
# Walk-Forward Window
# ─────────────────────────────────────────────

def build_walk_forward_windows(
    start: str = "2017-01-03",
    end: str = None,
    train_years: int = 4,
    val_years: int = 2,
    step_years: int = 2,
) -> List[Dict[str, str]]:
    """Generate rolling walk-forward train/val/oos windows."""
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    windows = []

    cursor = start_dt
    while True:
        train_end = cursor + timedelta(days=train_years * 365)
        val_end = train_end + timedelta(days=val_years * 365)
        oos_end = val_end + timedelta(days=val_years * 365)

        if train_end >= end_dt:
            break

        w = {
            "train_start": cursor.strftime("%Y-%m-%d"),
            "train_end": min(train_end, end_dt).strftime("%Y-%m-%d"),
            "val_start": train_end.strftime("%Y-%m-%d"),
            "val_end": min(val_end, end_dt).strftime("%Y-%m-%d"),
        }
        if val_end < end_dt:
            w["oos_start"] = val_end.strftime("%Y-%m-%d")
            w["oos_end"] = min(oos_end, end_dt).strftime("%Y-%m-%d")

        windows.append(w)
        cursor += timedelta(days=step_years * 365)

    if not windows:
        mid = start_dt + (end_dt - start_dt) * 2 // 3
        windows.append({
            "train_start": start,
            "train_end": mid.strftime("%Y-%m-%d"),
            "val_start": mid.strftime("%Y-%m-%d"),
            "val_end": end,
        })

    return windows


# ─────────────────────────────────────────────
# Fitness Evaluation
# ─────────────────────────────────────────────

def _individual_to_signal(ind: tuple) -> dict:
    """Convert GA individual (mb, ms, md, wb, ws, wd, alpha) to simulator signal dict."""
    mb, ms, md, wb, ws, wd, alpha = ind
    mask = mb | ms | md
    return {
        "mask": mask,
        "wb": np.array(wb, dtype=np.float64),
        "ws": np.array(ws, dtype=np.float64),
        "wd": np.array(wd, dtype=np.float64),
    }


def _compute_phase1_metrics(
    ind: tuple, engine, cfg, pack: dict,
    regime_by_date: Optional[Dict[str, str]] = None,
) -> dict:
    """Run the original Phase 1 evaluator to get IC/spread health metrics."""
    mb, ms, md, wb, ws, wd, alpha = ind
    try:
        fit, meta, *_ = engine.evaluate_individual_qresearch(
            pack=pack, cfg=cfg,
            mask_bull=mb, mask_side=ms, mask_def=md,
            w_bull=wb, w_side=ws, w_def=wd,
            alpha=alpha,
            regime_by_date=regime_by_date,
            lightweight=True,
        )
        return {
            "p1_fitness": float(fit),
            "mean_ic": float(meta.get("mean_ic_1m", 0)),
            "mean_ic_3m": float(meta.get("mean_ic_3m", 0)),
            "pos_ic_ratio": float(meta.get("positive_ic_ratio", 0)),
            "mean_spread": float(meta.get("mean_spread_mix", 0)),
            "regime_ic": float(meta.get("regime_weighted_ic_1m", 0)),
            "regime_spread": float(meta.get("regime_weighted_spread_mix", 0)),
            "k_used": int(meta.get("k_used", 0)),
        }
    except Exception as e:
        return {"p1_fitness": -999.0, "mean_ic": 0, "pos_ic_ratio": 0,
                "mean_spread": 0, "error": str(e)}


def _compute_weight_penalty(ind: tuple) -> float:
    """L2 regularization penalty on weight vectors."""
    _, _, _, wb, ws, wd, _ = ind
    norm_b = np.linalg.norm(wb)
    norm_s = np.linalg.norm(ws)
    norm_d = np.linalg.norm(wd)
    avg_norm = (norm_b + norm_s + norm_d) / 3.0
    # Soft penalty: 0 when norm ≤ 0.8, scales linearly above
    if avg_norm <= 0.8:
        return 0.0
    return -0.15 * (avg_norm - 0.8)


def evaluate_e2e(
    ind: tuple,
    engine,
    cfg,
    pack: dict,
    vix_close_map: dict,
    vix_regime_map: dict,
    strategy_conf: dict,
    trigger_conf: dict,
    windows: List[Dict[str, str]],
    initial_capital: float = 100000.0,
    daily_buy_limit: float = 1000.0,
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    ic_min_threshold: float = 0.015,
    pos_ic_min: float = 0.55,
    spread_min_threshold: float = 0.005,
    train_weight: float = 0.3,
    val_weight: float = 0.7,
    regime_by_date: Optional[Dict[str, str]] = None,
) -> Tuple[float, dict]:
    """Evaluate one individual: Phase 1 gate → simulator walk-forward → regularization."""
    import simulator

    signal = _individual_to_signal(ind)
    mask = signal["mask"]

    if mask.sum() < 3:
        return -999.0, {"reason": "too_few_factors", "k": int(mask.sum())}

    # ── Stage 1: Phase 1 health gate ──
    p1 = _compute_phase1_metrics(ind, engine, cfg, pack, regime_by_date)

    p1_adj = 0.0
    ic_val = p1["mean_ic"]
    spread_val = p1["mean_spread"]
    posic_val = p1["pos_ic_ratio"]

    if ic_val < ic_min_threshold:
        deficit = (ic_min_threshold - ic_val) / max(ic_min_threshold, 1e-9)
        p1_adj -= 1.0 * deficit
    else:
        surplus = min(ic_val / ic_min_threshold - 1.0, 3.0)
        p1_adj += 0.2 * surplus

    if spread_val < spread_min_threshold:
        p1_adj -= 0.5 * (spread_min_threshold - spread_val) / max(spread_min_threshold, 1e-9)
    else:
        p1_adj += 0.15 * min(spread_val / max(spread_min_threshold, 1e-9) - 1.0, 3.0)

    if posic_val < pos_ic_min:
        p1_adj -= 0.5 * (pos_ic_min - posic_val) / max(pos_ic_min, 1e-9)
    else:
        p1_adj += 0.1

    # Hard gate: IC below 30% of threshold AND negative spread → skip sim
    if ic_val < ic_min_threshold * 0.3 and spread_val <= 0:
        return p1_adj - 5.0, {
            "reason": "phase1_health_gate_fail",
            "k_used": int(mask.sum()),
            "phase1": p1,
            "fitness": p1_adj - 5.0,
        }

    # ── Stage 2: Walk-forward simulator fitness ──
    window_scores = []
    window_details = []

    for wi, w in enumerate(windows):
        train_res = simulator.run_simulation(
            engine=engine, cfg=cfg, pack=pack, signal=signal,
            vix_close_by_date=vix_close_map,
            vix_regime_by_date=vix_regime_map,
            initial_capital=initial_capital,
            daily_buy_limit=daily_buy_limit,
            strategy_conf=strategy_conf,
            trigger_conf=trigger_conf,
            rebalance_mode="daily",
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            start_date=w["train_start"],
            end_date=w["train_end"],
            progress_fn=lambda c, t, m: None,
        )
        tm = train_res.get("metrics", {})

        val_res = simulator.run_simulation(
            engine=engine, cfg=cfg, pack=pack, signal=signal,
            vix_close_by_date=vix_close_map,
            vix_regime_by_date=vix_regime_map,
            initial_capital=initial_capital,
            daily_buy_limit=daily_buy_limit,
            strategy_conf=strategy_conf,
            trigger_conf=trigger_conf,
            rebalance_mode="daily",
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            start_date=w["val_start"],
            end_date=w["val_end"],
            progress_fn=lambda c, t, m: None,
        )
        vm = val_res.get("metrics", {})

        train_score = _compute_window_score(tm)
        val_score = _compute_window_score(vm)

        overfit_penalty = 0.0
        if train_score > 0 and val_score > 0:
            degradation = 1.0 - (val_score / train_score)
            if degradation > 0.5:
                overfit_penalty = -0.5 * degradation

        combined = train_weight * train_score + val_weight * val_score + overfit_penalty

        detail = {
            "window": wi,
            "train_cagr": tm.get("CAGR", 0),
            "train_sharpe": tm.get("Net_Sharpe", 0),
            "train_mdd": tm.get("Max_Drawdown", 0),
            "val_cagr": vm.get("CAGR", 0),
            "val_sharpe": vm.get("Net_Sharpe", 0),
            "val_mdd": vm.get("Max_Drawdown", 0),
            "train_score": train_score,
            "val_score": val_score,
            "overfit_penalty": overfit_penalty,
            "combined": combined,
        }

        if "oos_start" in w:
            oos_res = simulator.run_simulation(
                engine=engine, cfg=cfg, pack=pack, signal=signal,
                vix_close_by_date=vix_close_map,
                vix_regime_by_date=vix_regime_map,
                initial_capital=initial_capital,
                daily_buy_limit=daily_buy_limit,
                strategy_conf=strategy_conf,
                trigger_conf=trigger_conf,
                rebalance_mode="daily",
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
                start_date=w["oos_start"],
                end_date=w["oos_end"],
                progress_fn=lambda c, t, m: None,
            )
            om = oos_res.get("metrics", {})
            detail["oos_cagr"] = om.get("CAGR", 0)
            detail["oos_sharpe"] = om.get("Net_Sharpe", 0)
            detail["oos_mdd"] = om.get("Max_Drawdown", 0)

        window_scores.append(combined)
        window_details.append(detail)

    fitness = float(np.mean(window_scores)) if window_scores else -999.0

    # ── Stage 3: Structural penalties ──
    k_used = int(mask.sum())
    k_target = 12
    if k_used < 5:
        fitness -= 0.3 * (5 - k_used)
    elif k_used > k_target:
        fitness -= 0.05 * (k_used - k_target)

    w_pen = _compute_weight_penalty(ind)
    fitness += p1_adj + w_pen

    meta = {
        "fitness": fitness,
        "k_used": k_used,
        "phase1": p1,
        "p1_adj": p1_adj,
        "w_pen": w_pen,
        "windows": window_details,
        "avg_val_cagr": float(np.mean([d["val_cagr"] for d in window_details])),
        "avg_val_sharpe": float(np.mean([d["val_sharpe"] for d in window_details])),
    }
    return fitness, meta


def _compute_window_score(metrics: dict) -> float:
    """Convert simulation metrics to a scalar score."""
    cagr = metrics.get("CAGR", 0)
    sharpe = metrics.get("Net_Sharpe", 0)
    mdd = abs(metrics.get("Max_Drawdown", 0))
    calmar = metrics.get("Calmar_Ratio", 0)

    if cagr <= -0.5 or sharpe < -2.0:
        return -10.0

    score = (
        0.35 * sharpe
        + 0.35 * (cagr * 5.0)
        + 0.20 * min(calmar, 3.0)
        + 0.10 * max(0, 1.0 - mdd * 2.0)
    )
    return float(score)


# ─────────────────────────────────────────────
# GA Engine
# ─────────────────────────────────────────────

def run_e2e_ga(
    population_size: int = 30,
    generations: int = 32,
    elite_frac: float = 0.15,
    crossover_prob: float = 0.6,
    mutation_rate_mask: float = 0.12,
    mutation_rate_weight: float = 0.25,
    weight_noise_sd: float = 0.3,
    immigration_rate: float = 0.15,
    seed: int = 42,
    train_years: int = 4,
    val_years: int = 2,
    step_years: int = 2,
    initial_capital: float = 100000.0,
    daily_buy_limit: float = 1000.0,
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    train_weight: float = 0.3,
    val_weight: float = 0.7,
    ic_min_threshold: float = 0.015,
    pos_ic_min: float = 0.55,
    spread_min_threshold: float = 0.005,
    start_date: str = "2017-01-03",
    end_date: str = None,
    config_yaml_path: str = None,
    progress_fn: Callable = None,
) -> Dict:
    """Run end-to-end GA v2 optimizing Phase 3 simulator performance.

    Key v2 improvements:
    - Stronger Phase 1 health gate
    - L2 weight regularization
    - Immigration: fresh random individuals injected each generation
    - No frozen-signal elite lock
    """
    import yaml
    from engine_loader import engine
    from daily_runner import load_frozen_signal
    import dataclasses

    _log = progress_fn or (lambda m: print(m))
    rng = np.random.default_rng(seed)

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    config_path = config_yaml_path or os.path.join(
        os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        conf = yaml.safe_load(f)

    _log("[E2E GA v2 Step 1/4] Building data pack...")
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
    K = pack["feat"].shape[0]
    _log(f"  Pack ready: {len(pack['tickers'])} tickers, "
         f"{len(pack['dates'])} dates, K={K} factors")

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

    _log("[E2E GA v2 Step 2/4] Building walk-forward windows...")
    windows = build_walk_forward_windows(
        start=start_date, end=end_date,
        train_years=train_years, val_years=val_years,
        step_years=step_years,
    )
    for i, w in enumerate(windows):
        parts = [f"train={w['train_start']}~{w['train_end']}",
                 f"val={w['val_start']}~{w['val_end']}"]
        if "oos_start" in w:
            parts.append(f"oos={w['oos_start']}~{w['oos_end']}")
        _log(f"  Window {i}: {' | '.join(parts)}")

    strategy_conf = make_strategy({})
    trigger_conf = conf.get("triggers", {})

    # ── Load reference signal (seed, not protected elite) ──
    _log(f"\n[E2E GA v2 Step 3/4] Loading reference signal (seed only, no elite lock)...")
    ref_signal = load_frozen_signal(conf["paths"]["frozen_signal"])
    ref_mask = np.asarray(ref_signal["mask"], dtype=bool)
    ref_wb = np.array(ref_signal["wb"], dtype=np.float64)
    ref_ws = np.array(ref_signal["ws"], dtype=np.float64)
    ref_wd = np.array(ref_signal["wd"], dtype=np.float64)
    ref_k = int(ref_mask.sum())
    _log(f"  Reference signal: K={ref_k} factors")

    # ── Individual constructors ──

    def _make_seeded():
        """Variant of reference: drop 1-4 factors, add 0-2, perturb weights."""
        mb = ref_mask.copy()
        ms = ref_mask.copy()
        md = ref_mask.copy()
        wb = ref_wb.copy() + rng.normal(0, weight_noise_sd * 0.3, K)
        ws = ref_ws.copy() + rng.normal(0, weight_noise_sd * 0.3, K)
        wd = ref_wd.copy() + rng.normal(0, weight_noise_sd * 0.3, K)

        for m in (mb, ms, md):
            on = np.where(m)[0]
            n_drop = rng.integers(1, max(2, len(on) // 3) + 1)
            if len(on) > 4:
                m[rng.choice(on, min(n_drop, len(on) - 4), replace=False)] = False
            off = np.where(~m)[0]
            n_add = rng.integers(0, 3)
            if n_add > 0 and len(off) > 0:
                m[rng.choice(off, min(n_add, len(off)), replace=False)] = True

        return (mb, ms, md, wb, ws, wd, float(rng.uniform(0.3, 0.7)))

    def _make_sparse_random():
        """Random individual with controlled sparsity (5-12 factors per mask)."""
        def _mk():
            n = rng.integers(5, 13)
            m = np.zeros(K, dtype=bool)
            m[rng.choice(K, min(n, K), replace=False)] = True
            return m
        mb, ms, md = _mk(), _mk(), _mk()
        wb = rng.normal(0, 0.4, K)
        ws = rng.normal(0, 0.4, K)
        wd = rng.normal(0, 0.4, K)
        return (mb, ms, md, wb, ws, wd, float(rng.uniform(0.2, 0.8)))

    # ── Initial population: 30% seeded, 70% random ──
    _log(f"  Creating population of {population_size}...")
    pop = []
    pop.append((ref_mask.copy(), ref_mask.copy(), ref_mask.copy(),
                ref_wb.copy(), ref_ws.copy(), ref_wd.copy(), 0.5))

    n_seeded = max(1, int(population_size * 0.30))
    for _ in range(n_seeded - 1):
        pop.append(_make_seeded())
    for _ in range(population_size - n_seeded):
        pop.append(_make_sparse_random())

    # ── Genetic operators ──

    def _crossover(a, b):
        mb_a, ms_a, md_a, wb_a, ws_a, wd_a, al_a = a
        mb_b, ms_b, md_b, wb_b, ws_b, wd_b, al_b = b
        lam = rng.uniform(0.3, 0.7)

        c_mb = np.where(rng.random(K) < lam, mb_a, mb_b)
        c_ms = np.where(rng.random(K) < lam, ms_a, ms_b)
        c_md = np.where(rng.random(K) < lam, md_a, md_b)
        c_wb = lam * wb_a + (1 - lam) * wb_b
        c_ws = lam * ws_a + (1 - lam) * ws_b
        c_wd = lam * wd_a + (1 - lam) * wd_b
        c_al = lam * al_a + (1 - lam) * al_b

        return (c_mb.astype(bool), c_ms.astype(bool), c_md.astype(bool),
                c_wb, c_ws, c_wd, float(np.clip(c_al, 0, 1)))

    def _mutate(ind):
        mb, ms, md, wb, ws, wd, alpha = ind
        mb, ms, md = mb.copy(), ms.copy(), md.copy()
        wb, ws, wd = wb.copy(), ws.copy(), wd.copy()

        for m in (mb, ms, md):
            on_bits = np.where(m)[0]
            off_bits = np.where(~m)[0]
            # Asymmetric: 2.5x more likely to turn OFF
            if len(on_bits) > 0:
                turn_off = rng.random(len(on_bits)) < mutation_rate_mask * 2.5
                m[on_bits[turn_off]] = False
            if len(off_bits) > 0:
                turn_on = rng.random(len(off_bits)) < mutation_rate_mask
                m[off_bits[turn_on]] = True
            if m.sum() < 3:
                off = np.where(~m)[0]
                if len(off) > 0:
                    m[rng.choice(off, min(3 - m.sum(), len(off)), replace=False)] = True

        for w in (wb, ws, wd):
            mut = rng.random(K) < mutation_rate_weight
            w[mut] += rng.normal(0, weight_noise_sd, mut.sum())
            # Weight decay: pull toward zero
            w *= 0.98

        alpha += rng.normal(0, 0.08)
        alpha = float(np.clip(alpha, 0, 1))

        return (mb, ms, md, wb, ws, wd, alpha)

    # ── Evolution loop ──
    n_elite = max(2, int(population_size * elite_frac))
    n_immigrants = max(1, int(population_size * immigration_rate))

    _log(f"\n[E2E GA v2 Step 4/4] Evolution: P={population_size}, G={generations}")
    _log(f"  Windows: {len(windows)}, train_w={train_weight}, val_w={val_weight}")
    _log(f"  Elites: {n_elite}, Immigrants/gen: {n_immigrants}")
    _log(f"  P1 gates: IC≥{ic_min_threshold}, PosIC≥{pos_ic_min:.0%}, "
         f"Spread≥{spread_min_threshold}")
    _log(f"  Mutation: mask={mutation_rate_mask}, weight={mutation_rate_weight}, "
         f"immigration={immigration_rate}")

    gen_log = []
    best_ever_fitness = -999.0
    best_ever_ind = None
    best_ever_meta = None

    for gen in range(generations):
        t0 = time.time()
        fitnesses = []
        metas = []

        for i, ind in enumerate(pop):
            f, m = evaluate_e2e(
                ind, engine, cfg, pack,
                vix_close_map, vix_regime_map,
                strategy_conf, trigger_conf,
                windows,
                initial_capital=initial_capital,
                daily_buy_limit=daily_buy_limit,
                commission_bps=commission_bps,
                slippage_bps=slippage_bps,
                ic_min_threshold=ic_min_threshold,
                pos_ic_min=pos_ic_min,
                spread_min_threshold=spread_min_threshold,
                train_weight=train_weight,
                val_weight=val_weight,
                regime_by_date=vix_regime_map,
            )
            fitnesses.append(f)
            metas.append(m)

            if (i + 1) % 5 == 0 or i == len(pop) - 1:
                _log(f"    Gen {gen+1}/{generations} | "
                     f"eval {i+1}/{len(pop)} | "
                     f"best_so_far={max(fitnesses):+.4f}")

        ranked = np.argsort(fitnesses)[::-1]
        best_idx = ranked[0]
        best_fit = fitnesses[best_idx]
        best_meta = metas[best_idx]

        if best_fit > best_ever_fitness:
            best_ever_fitness = best_fit
            best_ever_ind = pop[best_idx]
            best_ever_meta = best_meta

        p1_gate_fails = sum(1 for m in metas
                            if m.get("reason") == "phase1_health_gate_fail")
        best_p1 = best_meta.get("phase1", {})
        best_p1_ic = best_p1.get("mean_ic", 0)
        best_p1_spd = best_p1.get("mean_spread", 0)
        best_w_pen = best_meta.get("w_pen", 0)

        gen_info = {
            "gen": gen + 1,
            "best_fitness": best_fit,
            "mean_fitness": float(np.mean(fitnesses)),
            "best_val_cagr": best_meta.get("avg_val_cagr", 0),
            "best_val_sharpe": best_meta.get("avg_val_sharpe", 0),
            "best_k": best_meta.get("k_used", 0),
            "best_ic": best_p1_ic,
            "best_spread": best_p1_spd,
            "best_w_pen": best_w_pen,
            "p1_gate_fails": p1_gate_fails,
            "elapsed_s": time.time() - t0,
        }
        gen_log.append(gen_info)

        elapsed = gen_info["elapsed_s"]
        _log(f"\n  Gen {gen+1}/{generations} ({elapsed:.0f}s) | "
             f"best={best_fit:+.4f} mean={gen_info['mean_fitness']:+.4f} "
             f"valCAGR={gen_info['best_val_cagr']*100:+.1f}% "
             f"Shp={gen_info['best_val_sharpe']:.3f} "
             f"IC={best_p1_ic:.4f} Spd={best_p1_spd:.4f} "
             f"k={gen_info['best_k']} wPen={best_w_pen:+.3f} "
             f"p1fail={p1_gate_fails}/{len(pop)}")

        # ── Breeding ──
        if gen < generations - 1:
            elites = [pop[ranked[i]] for i in range(n_elite)]
            new_pop = list(elites)

            # Immigration: inject fresh individuals
            for _ in range(n_immigrants):
                if rng.random() < 0.3:
                    new_pop.append(_make_seeded())
                else:
                    new_pop.append(_make_sparse_random())

            # Fill rest with crossover + mutation
            breed_pool_size = min(n_elite * 3, len(pop))
            while len(new_pop) < population_size:
                p1_idx = rng.choice(ranked[:breed_pool_size])
                p2_idx = rng.choice(ranked[:breed_pool_size])
                if rng.random() < crossover_prob:
                    child = _crossover(pop[p1_idx], pop[p2_idx])
                else:
                    child = pop[p1_idx]
                child = _mutate(child)
                new_pop.append(child)

            pop = new_pop

    # ── Final report ──
    best_signal = _individual_to_signal(best_ever_ind)
    best_p1 = best_ever_meta.get("phase1", {})
    best_signal["signal_summary"] = json.dumps({
        "E2E_Fitness": round(best_ever_fitness, 6),
        "E2E_Val_CAGR": round(best_ever_meta.get("avg_val_cagr", 0), 6),
        "E2E_Val_Sharpe": round(best_ever_meta.get("avg_val_sharpe", 0), 6),
        "E2E_K_Used": best_ever_meta.get("k_used", 0),
        "P1_MeanIC": round(best_p1.get("mean_ic", 0), 6),
        "P1_PosIC": round(best_p1.get("pos_ic_ratio", 0), 4),
        "P1_Spread": round(best_p1.get("mean_spread", 0), 6),
        "P1_Fitness": round(best_p1.get("p1_fitness", 0), 6),
        "W_Penalty": round(best_ever_meta.get("w_pen", 0), 6),
        "E2E_Version": "v2",
        "E2E_Windows": len(windows),
        "E2E_Generations": generations,
        "E2E_Population": population_size,
    })

    _log(f"\n{'='*60}")
    _log(f"  E2E GA v2 Complete")
    _log(f"{'='*60}")
    _log(f"  [Portfolio]")
    _log(f"    E2E Fitness:  {best_ever_fitness:+.4f}")
    _log(f"    Val CAGR:     {best_ever_meta.get('avg_val_cagr',0)*100:+.2f}%")
    _log(f"    Val Sharpe:   {best_ever_meta.get('avg_val_sharpe',0):.3f}")
    _log(f"  [Signal Health — Phase 1]")
    _log(f"    Mean IC:      {best_p1.get('mean_ic',0):.4f}  "
         f"(min {ic_min_threshold})")
    _log(f"    Pos IC Ratio: {best_p1.get('pos_ic_ratio',0):.2%}  "
         f"(min {pos_ic_min:.0%})")
    _log(f"    Mean Spread:  {best_p1.get('mean_spread',0):.4f}  "
         f"(min {spread_min_threshold})")
    _log(f"    P1 Fitness:   {best_p1.get('p1_fitness',0):+.4f}")
    _log(f"    P1 Adjust:    {best_ever_meta.get('p1_adj',0):+.4f}")
    _log(f"  [Regularization]")
    _log(f"    Weight Pen:   {best_ever_meta.get('w_pen',0):+.4f}")
    _log(f"  [Structure]")
    _log(f"    Factors used: {best_ever_meta.get('k_used',0)}")
    _log(f"{'='*60}")

    return {
        "best_signal": best_signal,
        "best_individual": best_ever_ind,
        "best_fitness": best_ever_fitness,
        "best_meta": best_ever_meta,
        "generation_log": gen_log,
        "windows": windows,
        "config": {
            "population_size": population_size,
            "generations": generations,
            "train_years": train_years,
            "val_years": val_years,
            "train_weight": train_weight,
            "val_weight": val_weight,
            "seed": seed,
            "version": "v2",
        },
    }


def save_e2e_signal(result: dict, output_dir: str, label: str = "E2E_v2") -> str:
    """Save best signal as frozen .npz file."""
    sig = result["best_signal"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"frozen_signal_{label}_{ts}.npz"
    path = os.path.join(output_dir, name)

    np.savez(
        path,
        mask=sig["mask"],
        wb=sig["wb"],
        ws=sig["ws"],
        wd=sig["wd"],
        signal_summary=sig.get("signal_summary", "{}"),
    )
    return path
