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


# ─────────────────────────────────────────────
# Two-Step Grace Sweep Arms
# ─────────────────────────────────────────────

SWEEP_TWOSTEP_ARMS = {
    # Baseline: current strategy (no two-step)
    "BASE_g60": {"sell_grace_days": 60},
    "BASE_g120": {
        "sell_grace_days": 120,
        "regime_overrides": {
            "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                     "target_invest_pct": 0.98},
            "SIDE": {"sell_grace_days": 120, "adaptive_deploy_rate": 0.10,
                     "enable_stop_loss": False},
            "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
        },
    },

    # Two-step: half-sell at step1, full-sell at grace end
    "TS_30_60": {"sell_grace_days": 60, "grace_step1_days": 30,
                 "grace_step1_sell_pct": 0.5},
    "TS_20_60": {"sell_grace_days": 60, "grace_step1_days": 20,
                 "grace_step1_sell_pct": 0.5},
    "TS_40_60": {"sell_grace_days": 60, "grace_step1_days": 40,
                 "grace_step1_sell_pct": 0.5},

    "TS_60_120": {"sell_grace_days": 120, "grace_step1_days": 60,
                  "grace_step1_sell_pct": 0.5,
                  "regime_overrides": {
                      "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                               "target_invest_pct": 0.98},
                      "SIDE": {"sell_grace_days": 120, "grace_step1_days": 60,
                               "adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
                      "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
                  }},
    "TS_40_120": {"sell_grace_days": 120, "grace_step1_days": 40,
                  "grace_step1_sell_pct": 0.5,
                  "regime_overrides": {
                      "BULL": {"adaptive_deploy_rate": 0.20, "enable_stop_loss": True,
                               "target_invest_pct": 0.98},
                      "SIDE": {"sell_grace_days": 120, "grace_step1_days": 40,
                               "adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
                      "DEF":  {"adaptive_deploy_rate": 0.10, "enable_stop_loss": False},
                  }},

    # Different sell percentages at step1
    "TS_30_60_30pct": {"sell_grace_days": 60, "grace_step1_days": 30,
                       "grace_step1_sell_pct": 0.3},
    "TS_30_60_70pct": {"sell_grace_days": 60, "grace_step1_days": 30,
                       "grace_step1_sell_pct": 0.7},
}


SWEEP_BLEND_ARMS = {
    "STEP_baseline": {"regime_blend_enabled": False},
    "BLEND_w2_w3": {"regime_blend_enabled": True,
                     "bull_side_blend_width": 2.0, "side_def_blend_width": 3.0},
    "BLEND_w3_w4": {"regime_blend_enabled": True,
                     "bull_side_blend_width": 3.0, "side_def_blend_width": 4.0},
    "BLEND_w4_w5": {"regime_blend_enabled": True,
                     "bull_side_blend_width": 4.0, "side_def_blend_width": 5.0},
    "BLEND_w1_w2": {"regime_blend_enabled": True,
                     "bull_side_blend_width": 1.0, "side_def_blend_width": 2.0},
}

SWEEP_BLEND_ASYM_ARMS = {
    "STEP_baseline":  {"regime_blend_enabled": False},
    # BULL-only blend (DEF = step), varying BULL width
    "ASYM_b0.5_d0":   {"regime_blend_enabled": True,
                       "bull_side_blend_width": 0.5, "side_def_blend_width": 0.0},
    "ASYM_b1_d0":     {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.0, "side_def_blend_width": 0.0},
    "ASYM_b1.5_d0":   {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.5, "side_def_blend_width": 0.0},
    "ASYM_b2_d0":     {"regime_blend_enabled": True,
                       "bull_side_blend_width": 2.0, "side_def_blend_width": 0.0},
    # BULL blend + small DEF buffer (prevent chattering)
    "ASYM_b1_d0.5":   {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.0, "side_def_blend_width": 0.5},
    "ASYM_b1_d1":     {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.0, "side_def_blend_width": 1.0},
    "ASYM_b1.5_d0.5": {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.5, "side_def_blend_width": 0.5},
    "ASYM_b1.5_d1":   {"regime_blend_enabled": True,
                       "bull_side_blend_width": 1.5, "side_def_blend_width": 1.0},
}


def make_strategy(overrides: dict, base: dict = None) -> dict:
    s = dict(base or BASELINE_STRATEGY)
    for k, v in overrides.items():
        if k == "regime_overrides":
            s["regime_overrides"] = copy.deepcopy(v)
        elif k == "exit_triggers":
            # Lists — replace wholesale (no deep merge into baseline).
            # D3 sweep arms use this to install their own trigger stack.
            s["exit_triggers"] = copy.deepcopy(v)
        else:
            s[k] = v
    return s


# ─────────────────────────────────────────────────────────────────────
# D3 — Dynamic Exit Sweep (E22) arms
# ─────────────────────────────────────────────────────────────────────
#
# Baseline exit behaviour encoded as an explicit ``exit_triggers`` list
# so each D2 arm can add a single new trigger on top without losing
# the production regime-gated stop_loss / sell_grace semantics.
#
# Production semantics (from ``config.yaml`` regime_overrides):
#   * enable_stop_loss=True only in BULL.
#   * sell_grace_days=60 in BULL / DEF.
#   * sell_grace_days=120 in SIDE.
#
# When ``exit_triggers`` is present in the strategy, ``build_triggers``
# goes explicit mode and ignores ``enable_stop_loss`` / ``sell_grace_days``
# legacy keys — so the list below is the single source of truth for
# exit behaviour in D3 arms.
# ─────────────────────────────────────────────────────────────────────

BASELINE_EXITS_EXPLICIT = [
    {"type": "stop_loss",
     "regimes": ["BULL"],
     "params": {"threshold_pct": -15.0}},
    {"type": "sell_grace",
     "regimes": ["BULL"],
     "params": {"days": 60}},
    {"type": "sell_grace",
     "regimes": ["SIDE"],
     "params": {"days": 120}},
    {"type": "sell_grace",
     "regimes": ["DEF"],
     "params": {"days": 60}},
]


def _with_baseline_exits(*extra_triggers) -> dict:
    """Arm override builder: baseline stop_loss+sell_grace + extra D2 triggers.

    Returns an override dict that replaces the strategy's ``exit_triggers``
    with the baseline stack plus any passed-in extras.  Use::

        _with_baseline_exits(
            {"type": "peak_drawdown", "params": {"drawdown_pct": -20.0}},
        )
    """
    return {"exit_triggers": list(BASELINE_EXITS_EXPLICIT) + list(extra_triggers)}


# One-trigger-at-a-time isolation sweep.  Each arm adds exactly one D2
# trigger on top of the baseline stack, keeping the baseline regime gating
# intact.  Naming convention: <trigger>_<param>_<action>.
SWEEP_D2_EXIT_ARMS = {
    # ── Control ───────────────────────────────────────────────────────
    # Baseline re-expressed as explicit triggers.  Should match legacy
    # baseline metrics to within numerical noise — if it doesn't, the
    # D1/D2 pipeline has regressed.
    "BASE_explicit": _with_baseline_exits(),

    # ── D2.1 peak_drawdown ────────────────────────────────────────────
    "PD_15_SELL":  _with_baseline_exits(
        {"type": "peak_drawdown",
         "params": {"drawdown_pct": -15.0, "action": "SELL",
                    "min_days_held": 3}}),
    "PD_20_SELL":  _with_baseline_exits(
        {"type": "peak_drawdown",
         "params": {"drawdown_pct": -20.0, "action": "SELL",
                    "min_days_held": 3}}),
    "PD_25_SELL":  _with_baseline_exits(
        {"type": "peak_drawdown",
         "params": {"drawdown_pct": -25.0, "action": "SELL",
                    "min_days_held": 3}}),
    "PD_30_SELL":  _with_baseline_exits(
        {"type": "peak_drawdown",
         "params": {"drawdown_pct": -30.0, "action": "SELL",
                    "min_days_held": 3}}),
    "PD_20_TRIM":  _with_baseline_exits(
        {"type": "peak_drawdown",
         "params": {"drawdown_pct": -20.0, "action": "TRIM",
                    "partial_pct": 0.5, "min_days_held": 3}}),

    # ── D2.2 score_decay ──────────────────────────────────────────────
    "SD_40_SELL":  _with_baseline_exits(
        {"type": "score_decay",
         "params": {"decay_pct": -40.0, "action": "SELL",
                    "min_entry_score": 0.10, "min_days_held": 5}}),
    "SD_50_SELL":  _with_baseline_exits(
        {"type": "score_decay",
         "params": {"decay_pct": -50.0, "action": "SELL",
                    "min_entry_score": 0.10, "min_days_held": 5}}),
    "SD_60_SELL":  _with_baseline_exits(
        {"type": "score_decay",
         "params": {"decay_pct": -60.0, "action": "SELL",
                    "min_entry_score": 0.10, "min_days_held": 5}}),
    "SD_50_TRIM":  _with_baseline_exits(
        {"type": "score_decay",
         "params": {"decay_pct": -50.0, "action": "TRIM",
                    "partial_pct": 0.5, "min_entry_score": 0.10,
                    "min_days_held": 5}}),

    # ── D2.3 trend_break ──────────────────────────────────────────────
    # MA50/200 crossdown is rare → soft (TRIM) to avoid whipsaw.
    "TB_50_200_X":  _with_baseline_exits(
        {"type": "trend_break",
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}}),
    "TB_50_200_BELOW":  _with_baseline_exits(
        {"type": "trend_break",
         "params": {"fast": 50, "slow": 200, "mode": "below",
                    "action": "TRIM", "partial_pct": 0.5}}),
    "TB_20_50_X":  _with_baseline_exits(
        {"type": "trend_break",
         "params": {"fast": 20, "slow": 50, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}}),

    # ── D2.4 rank_velocity ────────────────────────────────────────────
    "RV_5_30_TRIM":  _with_baseline_exits(
        {"type": "rank_velocity",
         "params": {"lookback": 5, "drop_threshold": 30,
                    "action": "TRIM", "partial_pct": 0.4,
                    "min_days_held": 5}}),
    "RV_10_50_TRIM":  _with_baseline_exits(
        {"type": "rank_velocity",
         "params": {"lookback": 10, "drop_threshold": 50,
                    "action": "TRIM", "partial_pct": 0.4,
                    "min_days_held": 5}}),
    "RV_5_50_SELL":  _with_baseline_exits(
        {"type": "rank_velocity",
         "params": {"lookback": 5, "drop_threshold": 50,
                    "action": "SELL", "min_days_held": 5}}),

    # ── D2.5 relative_rebar ───────────────────────────────────────────
    "RR_0.8_TRIM":  _with_baseline_exits(
        {"type": "relative_rebar",
         "params": {"reference": "topN_median", "floor_multiplier": 0.8,
                    "action": "TRIM", "partial_pct": 0.4,
                    "min_days_held": 5}}),
    "RR_0.7_TRIM":  _with_baseline_exits(
        {"type": "relative_rebar",
         "params": {"reference": "topN_median", "floor_multiplier": 0.7,
                    "action": "TRIM", "partial_pct": 0.4,
                    "min_days_held": 5}}),
    "RR_0.6_SELL":  _with_baseline_exits(
        {"type": "relative_rebar",
         "params": {"reference": "topN_median", "floor_multiplier": 0.6,
                    "action": "SELL", "min_days_held": 5}}),

    # ── D2.6 regime_switch ────────────────────────────────────────────
    "RS_BEAR_TRIM":  _with_baseline_exits(
        {"type": "regime_switch",
         "params": {"mode": "bear_only", "action": "TRIM",
                    "partial_pct": 0.5, "grace_days": 3}}),
    "RS_DOWN_TRIM":  _with_baseline_exits(
        {"type": "regime_switch",
         "params": {"mode": "downgrade", "action": "TRIM",
                    "partial_pct": 0.5, "grace_days": 3}}),
    "RS_BEAR_SELL":  _with_baseline_exits(
        {"type": "regime_switch",
         "params": {"mode": "bear_only", "action": "SELL",
                    "grace_days": 3}}),
}


# ─────────────────────────────────────────────────────────────────────
# D3.v2 — β-direction re-tune sweep (E22.2)
# ─────────────────────────────────────────────────────────────────────
#
# Hypotheses from the v1 sweep (all 22 arms FAILed the strict gate):
#
#   H1. Every D2 trigger cut MDD (−0.2 ~ −15pp) but also cut CAGR
#       (−1.5 ~ −29pp). Risk control works; parameters are too aggressive.
#   H2. BULL regime is the main profit source (Ann% ≈ 49%); firing D2
#       triggers in BULL is where most of the CAGR cost lives. The
#       baseline already gates stop_loss to BULL → BULL is not where
#       drawdown accrues anyway.
#   H3. SIDE regime has no stop_loss and grace_days=120 → it is where
#       drawdowns accrue.  Hypothesis: D2 triggers limited to SIDE (or
#       SIDE+DEF) should shave MDD without touching BULL upside.
#   H4. TB_50_200_X (MA50/200 cross-down, TRIM) was closest to break-even
#       in v1 (−1.5pp CAGR, −0.7pp MDD).  Its narrow fire-rate makes it
#       a "structural regime-break" rather than "volatility stop", so it
#       plausibly survives regime gating too.
#   H5. The simplest alternative hypothesis: add a moderate stop_loss
#       purely in SIDE (no D2 trigger needed).
#
# Design rules:
#   * Default scope = SIDE only (override via ``regimes`` in each entry).
#   * Thresholds relaxed 1-2 notches vs v1 (e.g. PD_30→PD_40, SD_50→SD_70).
#   * Include a "SIDE stop_loss" control group — no D2 trigger at all,
#     just an extra stop_loss entry in the exit_triggers list for SIDE.
#   * Two combo arms stacking TB + PD40 and SIDE_SL + PD40 to test
#     additivity (can two mild triggers compound MDD gains without
#     killing CAGR).
# ─────────────────────────────────────────────────────────────────────

SWEEP_D2_EXIT_V2_ARMS = {
    # ── Control (carry-over; lets v2 sweep stand alone) ──────────────
    "BASE_explicit":  _with_baseline_exits(),

    # ── Group A — SIDE-gated D2 with relaxed thresholds ──────────────
    # A1. peak_drawdown SIDE-only, deep-cut thresholds.
    "A1_PD40_SIDE": _with_baseline_exits(
        {"type": "peak_drawdown", "regimes": ["SIDE"],
         "params": {"drawdown_pct": -40.0, "action": "SELL",
                    "min_days_held": 5}}),
    "A2_PD30_SIDE": _with_baseline_exits(
        {"type": "peak_drawdown", "regimes": ["SIDE"],
         "params": {"drawdown_pct": -30.0, "action": "SELL",
                    "min_days_held": 5}}),
    "A3_PD25_TRIM_SIDE": _with_baseline_exits(
        {"type": "peak_drawdown", "regimes": ["SIDE"],
         "params": {"drawdown_pct": -25.0, "action": "TRIM",
                    "partial_pct": 0.4, "min_days_held": 5}}),
    # A4. peak_drawdown SIDE+DEF (extend risk-off regimes).
    "A4_PD30_SIDE_DEF": _with_baseline_exits(
        {"type": "peak_drawdown", "regimes": ["SIDE", "DEF"],
         "params": {"drawdown_pct": -30.0, "action": "SELL",
                    "min_days_held": 5}}),

    # A5. score_decay SIDE-only with very deep threshold
    #     (v1 SD_50_SELL destroyed -29pp CAGR — here we fire only on
    #     near-total score collapses, in SIDE only).
    "A5_SD70_SIDE": _with_baseline_exits(
        {"type": "score_decay", "regimes": ["SIDE"],
         "params": {"decay_pct": -70.0, "action": "SELL",
                    "min_entry_score": 0.10, "min_days_held": 10}}),
    "A6_SD80_TRIM_SIDE": _with_baseline_exits(
        {"type": "score_decay", "regimes": ["SIDE"],
         "params": {"decay_pct": -80.0, "action": "TRIM",
                    "partial_pct": 0.4, "min_entry_score": 0.10,
                    "min_days_held": 10}}),

    # A7/A8. relative_rebar SIDE-only with relaxed floor_multiplier.
    "A7_RR_0.5_SIDE": _with_baseline_exits(
        {"type": "relative_rebar", "regimes": ["SIDE"],
         "params": {"reference": "topN_median",
                    "floor_multiplier": 0.5, "action": "TRIM",
                    "partial_pct": 0.3, "min_days_held": 10}}),
    "A8_RR_0.4_SELL_SIDE": _with_baseline_exits(
        {"type": "relative_rebar", "regimes": ["SIDE"],
         "params": {"reference": "topN_median",
                    "floor_multiplier": 0.4, "action": "SELL",
                    "min_days_held": 10}}),

    # A9. rank_velocity SIDE-only, deeper drop threshold.
    "A9_RV_10_80_SIDE": _with_baseline_exits(
        {"type": "rank_velocity", "regimes": ["SIDE"],
         "params": {"lookback": 10, "drop_threshold": 80,
                    "action": "TRIM", "partial_pct": 0.3,
                    "min_days_held": 10}}),

    # ── Group B — No D2, just add a SIDE-only stop_loss ─────────────
    # B. Simplest alternative: baseline structure + moderate SIDE SL.
    "B1_SL_SIDE_20": _with_baseline_exits(
        {"type": "stop_loss", "regimes": ["SIDE"],
         "params": {"threshold_pct": -20.0}}),
    "B2_SL_SIDE_25": _with_baseline_exits(
        {"type": "stop_loss", "regimes": ["SIDE"],
         "params": {"threshold_pct": -25.0}}),
    "B3_SL_SIDE_30": _with_baseline_exits(
        {"type": "stop_loss", "regimes": ["SIDE"],
         "params": {"threshold_pct": -30.0}}),

    # ── Group C — TB (v1 best-Calmar survivor) regime-gated variants ─
    # C. MA50/200 cross_down is a slow, structural trend-break signal.
    #    v1 TB_50_200_X (all regimes, TRIM 50%) was −1.5pp CAGR, −0.7pp
    #    MDD.  Gate to SIDE and shrink partial_pct to see if we can
    #    recover CAGR while keeping MDD benefit.
    "C1_TB_50_200_X_SIDE": _with_baseline_exits(
        {"type": "trend_break", "regimes": ["SIDE"],
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}}),
    "C2_TB_50_200_X_SIDEDEF": _with_baseline_exits(
        {"type": "trend_break", "regimes": ["SIDE", "DEF"],
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}}),
    "C3_TB_50_200_X_ALL_30": _with_baseline_exits(
        {"type": "trend_break",
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.3}}),

    # ── Group D — Combos of mild singletons ──────────────────────────
    # D1. SIDE SL (moderate) + SIDE PD (deep) — two-line risk net in SIDE.
    "D1_SL_SIDE25_PD40_SIDE": _with_baseline_exits(
        {"type": "stop_loss", "regimes": ["SIDE"],
         "params": {"threshold_pct": -25.0}},
        {"type": "peak_drawdown", "regimes": ["SIDE"],
         "params": {"drawdown_pct": -40.0, "action": "SELL",
                    "min_days_held": 5}}),
    # D2. TB_SIDE + SIDE SL — structural + absolute combo.
    "D2_TB_SIDE_SL_SIDE25": _with_baseline_exits(
        {"type": "trend_break", "regimes": ["SIDE"],
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}},
        {"type": "stop_loss", "regimes": ["SIDE"],
         "params": {"threshold_pct": -25.0}}),
    # D3. TB_SIDE + SIDE PD40 — both gentle, no stop_loss overlap.
    "D3_TB_SIDE_PD40_SIDE": _with_baseline_exits(
        {"type": "trend_break", "regimes": ["SIDE"],
         "params": {"fast": 50, "slow": 200, "mode": "cross_down",
                    "action": "TRIM", "partial_pct": 0.5}},
        {"type": "peak_drawdown", "regimes": ["SIDE"],
         "params": {"drawdown_pct": -40.0, "action": "SELL",
                    "min_days_held": 5}}),
}


# ─────────────────────────────────────────────────────────────────────
# D4 EXPLORATORY EXIT SWEEP — γ direction (new architecture)
# ─────────────────────────────────────────────────────────────────────
# Principles (user-requested, gamma direction):
#   1. Identify unrecoverable crashes → ATR trailing stop + risk-off gate.
#   2. Tactical "hit-and-run" profit-taking → profit_target (tiered).
# Baseline (BASE_explicit) is re-run alongside so every arm can be
# diff'd against it under the same sweep rig.  Strict gate (CAGR ≥,
# MDD ≤, Sharpe ≥) still applies in the user's pass criterion.
#
# Arm naming: <family>_<key params>.
#   ATR: ATR_k<k>_<gate>_<action>
#        gate ∈ {ALL, RO}  (ALL = fires always; RO = risk_off_only=True)
#   RO:  RO_<threshold_count>_<vix_level>   (risk-off assessor only — no
#        direct arm since risk_off_gate is not a trigger; these arms
#        instead vary the RiskOffAssessor config via strat["risk_off"]
#        while keeping ATR triggers to verify behaviour).
#   PT:  PT_<target_pct>_<action>_<gates>  (profit_target tiers)

# Risk-off assessor presets (strat["risk_off"] dict).
_RISK_OFF_DEFAULT = {  # 2-of-4 threshold, sensible defaults
    "vix_critical": 30.0, "vix_spike_delta": 10.0, "vix_lookback": 7,
    "regime_transition_days": 5, "portfolio_dd_threshold": 10.0,
    "threshold_count": 2,
}
_RISK_OFF_STRICT = {   # higher bar — only extreme stress counts
    "vix_critical": 35.0, "vix_spike_delta": 12.0, "vix_lookback": 7,
    "regime_transition_days": 5, "portfolio_dd_threshold": 12.0,
    "threshold_count": 2,
}
_RISK_OFF_LOOSE = {    # easier to trip (stress-testing)
    "vix_critical": 28.0, "vix_spike_delta": 8.0, "vix_lookback": 7,
    "regime_transition_days": 7, "portfolio_dd_threshold": 8.0,
    "threshold_count": 2,
}


def _with_d4_exits(*extra_triggers, risk_off=None) -> dict:
    """Like ``_with_baseline_exits`` but also stamps strat['risk_off'].

    Keeps the baseline SL+grace untouched so the arm is only *adding*
    D4 layers, not replacing legacy exits.
    """
    out = {"exit_triggers": list(BASELINE_EXITS_EXPLICIT) + list(extra_triggers)}
    if risk_off is not None:
        out["risk_off"] = dict(risk_off)
    return out


SWEEP_D4_EXIT_ARMS = {
    # ── Control (same as D3 v1/v2 baseline) ─────────────────────────────
    "BASE_explicit": _with_baseline_exits(),

    # ── D4.1 ATR trailing stop (always-on) ──────────────────────────────
    # k = multiplier on ATR20; typical crash-protection range 2.5~4.0.
    "ATR_k3_ALL_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5}}),
    "ATR_k35_ALL_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.5, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5}}),
    "ATR_k25_ALL_TRIM": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 2.5, "atr_window": 20, "action": "TRIM",
                    "partial_pct": 0.5, "min_days_held": 5}}),
    "ATR_k3_SIDE_DEF_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop", "regimes": ["SIDE", "DEF"],
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5}}),

    # ── D4.1 × D4.2 risk-off gated ATR trailing stop ────────────────────
    # Only fires when market in risk-off mode — the "crash-only
    # circuit breaker" variant. This is the user-principle #1 target.
    "ATR_k3_RO_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5, "risk_off_only": True}},
        risk_off=_RISK_OFF_DEFAULT),
    "ATR_k25_RO_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 2.5, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5, "risk_off_only": True}},
        risk_off=_RISK_OFF_DEFAULT),
    "ATR_k3_RO_STRICT_SELL": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5, "risk_off_only": True}},
        risk_off=_RISK_OFF_STRICT),
    "ATR_k25_RO_LOOSE_TRIM": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 2.5, "atr_window": 20, "action": "TRIM",
                    "partial_pct": 0.5, "min_days_held": 5,
                    "risk_off_only": True}},
        risk_off=_RISK_OFF_LOOSE),

    # ── D4.3 profit_target single-tier (score-gated) ────────────────────
    # Principle #2: take profit on rallies when score is weakening.
    "PT_30_TRIM_SCORE": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 30.0, "action": "TRIM", "partial_pct": 0.3,
                    "score_gate_enabled": True, "score_decay_pct": -15.0,
                    "min_days_held": 10}}),
    "PT_50_TRIM_SCORE": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 50.0, "action": "TRIM", "partial_pct": 0.5,
                    "score_gate_enabled": True, "score_decay_pct": -12.0,
                    "min_days_held": 10}}),
    "PT_100_SELL_SCORE": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 100.0, "action": "SELL",
                    "score_gate_enabled": True, "score_decay_pct": -8.0,
                    "min_days_held": 10}}),

    # ── D4.3 profit_target with extension gate ──────────────────────────
    "PT_30_TRIM_EXT": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 30.0, "action": "TRIM", "partial_pct": 0.3,
                    "score_gate_enabled": False,
                    "extension_enabled": True, "extension_threshold": 0.20,
                    "min_days_held": 10}}),
    "PT_50_TRIM_BOTH_OR": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 50.0, "action": "TRIM", "partial_pct": 0.5,
                    "score_gate_enabled": True, "score_decay_pct": -10.0,
                    "extension_enabled": True, "extension_threshold": 0.25,
                    "gate_mode": "or", "min_days_held": 10}}),

    # ── D4.3 tiered stack (3-tier cascade, score-gated) ─────────────────
    # User-style "치고빠지기": partial trims along the rally, then full sell.
    "PT_TIERED_3_SCORE": _with_d4_exits(
        {"type": "profit_target",
         "params": {"target_pct": 30.0, "action": "TRIM", "partial_pct": 0.3,
                    "score_gate_enabled": True, "score_decay_pct": -15.0,
                    "min_days_held": 10}},
        {"type": "profit_target",
         "params": {"target_pct": 50.0, "action": "TRIM", "partial_pct": 0.5,
                    "score_gate_enabled": True, "score_decay_pct": -12.0,
                    "min_days_held": 10}},
        {"type": "profit_target",
         "params": {"target_pct": 100.0, "action": "SELL",
                    "score_gate_enabled": True, "score_decay_pct": -8.0,
                    "min_days_held": 10}}),

    # ── D4 COMBO: ATR crash-gate + profit-target hit-and-run ────────────
    # The combined configuration — both principles together.
    "D4_COMBO_RO_PT_3T": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5, "risk_off_only": True}},
        {"type": "profit_target",
         "params": {"target_pct": 30.0, "action": "TRIM", "partial_pct": 0.3,
                    "score_gate_enabled": True, "score_decay_pct": -15.0,
                    "min_days_held": 10}},
        {"type": "profit_target",
         "params": {"target_pct": 50.0, "action": "TRIM", "partial_pct": 0.5,
                    "score_gate_enabled": True, "score_decay_pct": -12.0,
                    "min_days_held": 10}},
        {"type": "profit_target",
         "params": {"target_pct": 100.0, "action": "SELL",
                    "score_gate_enabled": True, "score_decay_pct": -8.0,
                    "min_days_held": 10}},
        risk_off=_RISK_OFF_DEFAULT),
    "D4_COMBO_RO_PT_SIMPLE": _with_d4_exits(
        {"type": "atr_trailing_stop",
         "params": {"k": 3.0, "atr_window": 20, "action": "SELL",
                    "min_days_held": 5, "risk_off_only": True}},
        {"type": "profit_target",
         "params": {"target_pct": 50.0, "action": "SELL",
                    "score_gate_enabled": True, "score_decay_pct": -10.0,
                    "min_days_held": 10}},
        risk_off=_RISK_OFF_DEFAULT),
}


# ─────────────────────────────────────────────────────────────────────
# D4 v2 — PT_EXT precision grid (19 arms).
#
# Anchored on D4 v1 "soft NEAR" winner: PT_30_TRIM_EXT
#   target_pct=30, action=TRIM, partial_pct=0.3,
#   extension_window=20, extension_threshold=0.20, min_days_held=10
# All arms turn OFF the score gate and ON the extension gate (the
# combination that dominated v1).  Each arm perturbs one dimension at
# a time, plus four sweet-spot combos and two regime-gated variants.
# ─────────────────────────────────────────────────────────────────────


def _pt_ext_arm(
    *,
    target_pct: float = 30.0,
    action: str = "TRIM",
    partial_pct: float = 0.3,
    extension_window: int = 20,
    extension_threshold: float = 0.20,
    min_days_held: int = 10,
    regimes: list[str] | None = None,
) -> dict:
    """Return a single PT_EXT arm config (extension-gated, no score gate)."""
    trig: dict = {
        "type": "profit_target",
        "params": {
            "target_pct": target_pct,
            "action": action,
            "partial_pct": partial_pct,
            "score_gate_enabled": False,
            "extension_enabled": True,
            "extension_window": extension_window,
            "extension_threshold": extension_threshold,
            "min_days_held": min_days_held,
        },
    }
    if regimes is not None:
        trig["regimes"] = list(regimes)
    return _with_d4_exits(trig)


SWEEP_D4_V2_ARMS = {
    # ── Controls ────────────────────────────────────────────────────────
    "BASE_explicit": _with_baseline_exits(),
    # v1 reference (target=30, win=20, thresh=0.20, partial=0.3, all-regime)
    "PT_30_TRIM_EXT_REF": _pt_ext_arm(),

    # ── Dim 1: target_pct (hold window=20, thresh=0.20, partial=0.3) ────
    "PT_20_TRIM_EXT": _pt_ext_arm(target_pct=20.0),
    "PT_25_TRIM_EXT": _pt_ext_arm(target_pct=25.0),
    "PT_35_TRIM_EXT": _pt_ext_arm(target_pct=35.0),

    # ── Dim 2: extension_threshold (hold target=30, win=20, partial=0.3)
    "PT_30_TRIM_EXT_th15": _pt_ext_arm(extension_threshold=0.15),
    "PT_30_TRIM_EXT_th18": _pt_ext_arm(extension_threshold=0.18),
    "PT_30_TRIM_EXT_th22": _pt_ext_arm(extension_threshold=0.22),
    "PT_30_TRIM_EXT_th25": _pt_ext_arm(extension_threshold=0.25),

    # ── Dim 3: extension_window (MA length) ─────────────────────────────
    "PT_30_TRIM_EXT_w30": _pt_ext_arm(extension_window=30),
    "PT_30_TRIM_EXT_w50": _pt_ext_arm(extension_window=50),

    # ── Dim 4: partial_pct (trim fraction) ──────────────────────────────
    "PT_30_TRIM_EXT_p2": _pt_ext_arm(partial_pct=0.2),
    "PT_30_TRIM_EXT_p5": _pt_ext_arm(partial_pct=0.5),

    # ── Dim 5: regime-gating ────────────────────────────────────────────
    "PT_30_TRIM_EXT_BULL": _pt_ext_arm(regimes=["BULL"]),
    "PT_30_TRIM_EXT_SIDE_DEF": _pt_ext_arm(regimes=["SIDE", "DEF"]),

    # ── Dim 6: sweet-spot combos (multi-dimension) ──────────────────────
    # Gentler profile: earlier target, lower threshold.
    "PT_25_th18_w20": _pt_ext_arm(
        target_pct=25.0, extension_threshold=0.18,
    ),
    # Stricter: higher target + higher threshold (only big movers).
    "PT_35_th22_w20": _pt_ext_arm(
        target_pct=35.0, extension_threshold=0.22,
    ),
    # Longer MA + slightly lower threshold (captures sustained extension).
    "PT_30_th18_w30": _pt_ext_arm(
        extension_threshold=0.18, extension_window=30,
    ),
    # Bigger trim at stricter threshold (less frequent but heavier cut).
    "PT_30_th25_p5": _pt_ext_arm(
        extension_threshold=0.25, partial_pct=0.5,
    ),
}


# ─────────────────────────────────────────────────────────────────────
# D4 v3 — Mini sweep around the v2 winner (PT_30_TRIM_EXT_SIDE_DEF).
#
# Goal: confirm the regime-gate sweet-spot and diagnose whether the
# DEF-regime CAGR drop (−2.99pp on 140 days) is noise or a structural
# cost.  Ten arms total (2 controls + 2 split-regime probes + 4 param
# variants inside SIDE+DEF + 2 extras).
# ─────────────────────────────────────────────────────────────────────

SWEEP_D4_V3_MINI_ARMS = {
    # ── Controls ────────────────────────────────────────────────────────
    "BASE_explicit": _with_baseline_exits(),
    "PT_30_TRIM_EXT_SIDE_DEF_REF": _pt_ext_arm(regimes=["SIDE", "DEF"]),

    # ── Regime split: isolate which regime actually drives the win ──────
    "PT_30_SIDE_ONLY": _pt_ext_arm(regimes=["SIDE"]),
    "PT_30_DEF_ONLY": _pt_ext_arm(regimes=["DEF"]),

    # ── Target variations inside SIDE+DEF ───────────────────────────────
    "SIDE_DEF_tgt25": _pt_ext_arm(target_pct=25.0, regimes=["SIDE", "DEF"]),
    "SIDE_DEF_tgt35": _pt_ext_arm(target_pct=35.0, regimes=["SIDE", "DEF"]),

    # ── Threshold variations inside SIDE+DEF ────────────────────────────
    "SIDE_DEF_th18": _pt_ext_arm(
        extension_threshold=0.18, regimes=["SIDE", "DEF"],
    ),
    "SIDE_DEF_th22": _pt_ext_arm(
        extension_threshold=0.22, regimes=["SIDE", "DEF"],
    ),

    # ── Extras: partial + window variants inside SIDE+DEF ───────────────
    "SIDE_DEF_p2": _pt_ext_arm(partial_pct=0.2, regimes=["SIDE", "DEF"]),
    "SIDE_DEF_w30": _pt_ext_arm(
        extension_window=30, regimes=["SIDE", "DEF"],
    ),
}


# ─────────────────────────────────────────────────────────────────────
# D4 v4 — Combo sweep (winner × winner interaction check).
#
# v3 established single-dimension winners inside the SIDE+DEF gate:
#   partial_pct=0.20, extension_threshold=0.18, target_pct=35
#   DEF-only gating also beat the SIDE+DEF combo on MDD & Calmar.
#
# v4 asks: do these stack super-linearly, or is there an interaction
# penalty?  Six combo arms + 2 controls.  Also probes partial_pct
# neighbourhood (0.15 / 0.25) to confirm p=0.2 is the sweet spot,
# not just the boundary of the v3 grid.
# ─────────────────────────────────────────────────────────────────────

SWEEP_D4_V4_COMBO_ARMS = {
    # ── Controls ────────────────────────────────────────────────────────
    "BASE_explicit": _with_baseline_exits(),
    # v3 champion reference (partial=0.2 inside SIDE+DEF, all other defaults)
    "SIDE_DEF_p2_REF": _pt_ext_arm(partial_pct=0.2, regimes=["SIDE", "DEF"]),

    # ── DEF-only combos (DEF_ONLY had best MDD and clean strict pass) ──
    "DEF_ONLY_p2": _pt_ext_arm(partial_pct=0.2, regimes=["DEF"]),
    "DEF_ONLY_p2_th18": _pt_ext_arm(
        partial_pct=0.2, extension_threshold=0.18, regimes=["DEF"],
    ),

    # ── SIDE+DEF combo: two winners stacked ─────────────────────────────
    "SIDE_DEF_p2_th18": _pt_ext_arm(
        partial_pct=0.2, extension_threshold=0.18,
        regimes=["SIDE", "DEF"],
    ),
    "SIDE_DEF_p2_tgt35": _pt_ext_arm(
        partial_pct=0.2, target_pct=35.0,
        regimes=["SIDE", "DEF"],
    ),

    # ── partial_pct neighbourhood check (is p=0.2 the local maximum?) ──
    "SIDE_DEF_p15": _pt_ext_arm(partial_pct=0.15, regimes=["SIDE", "DEF"]),
    "SIDE_DEF_p25": _pt_ext_arm(partial_pct=0.25, regimes=["SIDE", "DEF"]),
}


# ─────────────────────────────────────────────────────────────────────
# D4 v5 — Micro sweep to confirm partial_pct peak.
#
# v4 established monotonic CAGR improvement as partial_pct shrank:
#     p25 → 36.93  ,  p20 → 37.10  ,  p15 → 37.46
# Is p=0.15 the true peak, or would smaller values (p10 / p05) push
# further?  Five arms only (3 probes + 2 controls).  Below p=0.05
# we stop — the floor rule ``max(1, floor(shares * pct))`` degenerates
# to "1 share per trim" for typical position sizes, making smaller
# pcts mechanically equivalent.
# ─────────────────────────────────────────────────────────────────────

SWEEP_D4_V5_MICRO_ARMS = {
    # ── Controls ────────────────────────────────────────────────────────
    "BASE_explicit": _with_baseline_exits(),
    # v4 champion reference (partial=0.15 inside SIDE+DEF)
    "SIDE_DEF_p15_REF": _pt_ext_arm(partial_pct=0.15, regimes=["SIDE", "DEF"]),

    # ── Micro probes: partial_pct below 0.15 ────────────────────────────
    "SIDE_DEF_p12": _pt_ext_arm(partial_pct=0.12, regimes=["SIDE", "DEF"]),
    "SIDE_DEF_p10": _pt_ext_arm(partial_pct=0.10, regimes=["SIDE", "DEF"]),
    "SIDE_DEF_p05": _pt_ext_arm(partial_pct=0.05, regimes=["SIDE", "DEF"]),
}


# ─────────────────────────────────────────────────────────────────────
# Pass-gate analysis (used by UI + CLI to summarise D3 sweep results)
# ─────────────────────────────────────────────────────────────────────

def analyze_d2_sweep(
    comparison: pd.DataFrame,
    baseline_arm: str = "BASE_explicit",
    tol_cagr: float = 0.001,  # 0.1 pp — absolute metric units are raw pct here
    tol_mdd: float = 0.001,
    tol_sharpe: float = 0.001,
) -> pd.DataFrame:
    """Classify each arm vs baseline along CAGR / MDD / Sharpe.

    Pass criteria (all three must hold within tolerance):
      * CAGR%   >= baseline CAGR%   - tol_cagr × 100
      * MDD%    <= baseline MDD%    + tol_mdd  × 100   (MDD is positive %)
      * Sharpe  >= baseline Sharpe  - tol_sharpe

    Returns a DataFrame indexed by arm with columns:
      dCAGR%, dMDD%, dSharpe, dCalmar, Verdict, Reason.
    ``Verdict`` ∈ {"BASE", "PASS", "NEAR", "FAIL"}; ``NEAR`` = within ~0.3 pp
    of the gate on exactly one metric (user can judge subjective wins).
    """
    if baseline_arm not in comparison.index:
        raise ValueError(
            f"analyze_d2_sweep: baseline_arm {baseline_arm!r} not in comparison "
            f"(got {list(comparison.index)})"
        )
    b = comparison.loc[baseline_arm]
    b_cagr = float(b["CAGR%"])
    b_mdd = float(b["MDD%"])
    b_sharpe = float(b["Sharpe"])
    b_calmar = float(b["Calmar"])

    rows = []
    for arm, row in comparison.iterrows():
        cagr = float(row["CAGR%"])
        mdd = float(row["MDD%"])
        sharpe = float(row["Sharpe"])
        calmar = float(row["Calmar"])

        d_cagr = cagr - b_cagr
        d_mdd = mdd - b_mdd          # positive → worse
        d_sharpe = sharpe - b_sharpe
        d_calmar = calmar - b_calmar

        if arm == baseline_arm:
            verdict, reason = "BASE", "(baseline)"
        else:
            fails = []
            near_count = 0
            if d_cagr < -tol_cagr * 100:
                if d_cagr >= -0.30:
                    near_count += 1
                else:
                    fails.append(f"CAGR {d_cagr:+.2f}pp")
            if d_mdd > tol_mdd * 100:
                if d_mdd <= 0.30:
                    near_count += 1
                else:
                    fails.append(f"MDD {d_mdd:+.2f}pp")
            if d_sharpe < -tol_sharpe:
                if d_sharpe >= -0.05:
                    near_count += 1
                else:
                    fails.append(f"Sharpe {d_sharpe:+.3f}")

            if fails:
                verdict = "FAIL"
                reason = "; ".join(fails)
            elif near_count >= 1:
                verdict = "NEAR"
                reason = "within tolerance"
            else:
                verdict = "PASS"
                reason = "all metrics >= baseline"

        rows.append({
            "Arm": arm,
            "dCAGR%": round(d_cagr, 2),
            "dMDD%": round(d_mdd, 2),
            "dSharpe": round(d_sharpe, 3),
            "dCalmar": round(d_calmar, 3),
            "Verdict": verdict,
            "Reason": reason,
        })
    out = pd.DataFrame(rows).set_index("Arm")
    # Sort: BASE first, then PASS (by dCalmar desc), NEAR, FAIL last.
    rank = {"BASE": 0, "PASS": 1, "NEAR": 2, "FAIL": 3}
    out["_rank"] = out["Verdict"].map(rank)
    out = out.sort_values(["_rank", "dCalmar"], ascending=[True, False])
    out = out.drop(columns=["_rank"])
    return out


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
    compose_mode: bool = False,
    # D4 diagnostics — when True, a per-arm trade log (list of dicts) is
    # attached to each arm's result under key ``"d4_trade_log"``.  Caller
    # can dump to CSV; the simulator does no I/O itself.
    dump_trades: bool = False,
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
    from daily_runner import load_frozen_signal, load_composed_signal, describe_signal
    import simulator
    import dataclasses

    _log = progress_fn or (lambda m: print(m))

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    config_path = config_yaml_path or os.path.join(
        os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        conf = yaml.safe_load(f)

    # ── Step 1: Load signal (compose-aware when compose_mode=True) ──
    if compose_mode:
        conf_c = dict(conf)
        rc = dict(conf_c.get("regime_compose") or {})
        rc["enabled"] = True
        conf_c["regime_compose"] = rc
        _log("[Lab Step 1/3] Loading composed signal...")
        signal = load_composed_signal(conf_c)
        _log(f"  signal: {describe_signal(signal)}")
    else:
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
    vix_close_map, vix_regime_map, vix_smooth_map = {}, {}, {}
    if vix_df is not None and not vix_df.empty:
        for _, row in vix_df.iterrows():
            d_str = str(row.get("date", row.name))[:10]
            vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
            vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
            if "vix_smooth" in row.index:
                vix_smooth_map[d_str] = float(row["vix_smooth"])
    _log(f"  VIX data: {len(vix_close_map)} dates")

    # ── Step 3: Run each arm ──
    _log(f"\n[Lab Step 3/3] Running {len(arms)} arms...")
    results = {}
    trigger_conf = conf.get("triggers", {})

    base_blend_conf = conf.get("regime", {})

    for i, (arm_name, overrides) in enumerate(arms.items(), 1):
        arm_overrides = dict(overrides) if overrides else {}
        arm_blend_conf = dict(base_blend_conf)
        for bk in ("regime_blend_enabled", "bull_side_blend_width", "side_def_blend_width"):
            if bk in arm_overrides:
                arm_blend_conf[bk] = arm_overrides.pop(bk)

        strat = make_strategy(arm_overrides)
        _log(f"\n  ── Arm {i}/{len(arms)}: {arm_name} ──")
        changes = {k: v for k, v in (overrides or {}).items()} if overrides else {"(baseline)": ""}
        for k, v in changes.items():
            _log(f"    {k} = {v}")

        t0 = time.time()
        arm_trade_log: Optional[list] = [] if dump_trades else None
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
            blend_conf=arm_blend_conf,
            vix_smooth_by_date=vix_smooth_map,
            trade_log=arm_trade_log,
        )
        elapsed = time.time() - t0
        m = res["metrics"]
        _log(f"    CAGR={m.get('CAGR',0)*100:+.2f}%  "
             f"Sharpe={m.get('Net_Sharpe',0):.3f}  "
             f"MDD={m.get('Max_Drawdown',0)*100:.1f}%  "
             f"Calmar={m.get('Calmar_Ratio',0):.3f}  "
             f"Commission=${m.get('Total_Commission',0):,.0f}  "
             f"({elapsed:.1f}s)")
        if arm_trade_log is not None:
            res["d4_trade_log"] = arm_trade_log
            _log(f"    D4 trade-log: {len(arm_trade_log)} events captured")
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


def dump_trade_logs(
    results: Dict[str, Dict],
    out_dir: str,
    tag: Optional[str] = None,
) -> Dict[str, str]:
    """Write each arm's D4 trade-log (if captured) to its own CSV.

    Writes ``<out_dir>/d4_trades_<arm>_<tag>.csv`` for every arm whose
    result dict contains a non-empty ``d4_trade_log`` list.  Returns
    {arm_name: path} for the written files.

    ``Meta`` is flattened into string form for CSV friendliness.
    """
    import pandas as pd
    tag = tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    written: Dict[str, str] = {}
    for arm, res in results.items():
        log = res.get("d4_trade_log")
        if not log:
            continue
        df = pd.DataFrame(log)
        if "Meta" in df.columns:
            df["Meta"] = df["Meta"].astype(str)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in arm)
        path = os.path.join(out_dir, f"d4_trades_{safe}_{tag}.csv")
        df.to_csv(path, index=False)
        written[arm] = path
    return written


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
