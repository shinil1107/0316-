"""Signal Feature Audit — cross-compare feature selection & regime weights
between Baseline_V2 and 3 P5_RETRAIN variants.

Purpose (P1 follow-up to T5 Phase A REJECT/HOLD verdicts):
  Walk-forward showed Baseline_V2 has +5-15pp CAGR advantage in BULL folds
  (F1/F3/F4) despite WORSE mean IC (+0.0036 vs retrain +0.0149). The gap
  must originate from *factor tilt* — which stocks each signal picks.
  This script decomposes the 36-feature mask and per-regime weights to
  identify which features drive baseline's BULL-regime CAGR advantage.

Inputs:
  - 4 frozen_signal npz files (mask / wb / ws / wd, all (36,))
  - 14-year pack's indicator_names (36 features)

Outputs:
  - `phase3/docs/signal_feature_audit_<stamp>.md`  (human-readable)
  - `phase3/docs/signal_feature_audit_<stamp>.json` (machine-readable)
  - Console summary

Feature taxonomy (hard-coded per engine cell 0 indicator_names ordering):
  Tech short-horizon momentum:   RSI, MACD, SMA_CROSS, BBP, CCI, VOL_SPIKE,
                                 STOCH, OBV_POS, ATR_LOW, MFI, ADX, WILLR,
                                 ROC, VWAP_ABOVE
  Long-horizon momentum:         MOM_3M, MOM_6M, MOM_12M_EX1M
  Breakout / trend strength:     BREAKOUT_252, RSI_TREND, SMA50_SLOPE,
                                 BREAKOUT_126, DIST_FROM_SMA50, HIGH_20_BREAK
  Valuation:                     VAL_EARN_YIELD, VAL_BOOK2PRICE
  Quality:                       QUAL_ROE
  Leverage:                      LEV_DEBT_EQUITY
  Cash-flow:                     CF_FCF_YIELD
  Interactions (composites):     8 interaction factors (QUAL×MOM, VAL×MOM, etc.)
"""
from __future__ import annotations

import os
import sys
import json
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


DOCS_DIR = os.path.join(PHASE3_DIR, "docs")

# ── Signal set (identical to step_d_walk_forward) ─────────────────────
OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
SIGNALS: List[Dict[str, str]] = [
    {"id": "baseline", "arm": "Baseline_V2",    "path": f"{OUTPUT_SIG_DIR}/frozen_signal_V2_GOLDEN_ENS_L3_v1_20260419.npz"},
    {"id": "p5",       "arm": "P5_RETRAIN",     "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_20260423_153457.npz"},
    {"id": "t1",       "arm": "P5_RETRAIN_T1",  "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1_20260423_183119.npz"},
    {"id": "t1b",      "arm": "P5_RETRAIN_T1b", "path": f"{OUTPUT_SIG_DIR}/frozen_signal_P5_RETRAIN_T1b_20260423_205332.npz"},
]
PACK_PATH = f"{OUTPUT_SIG_DIR}/precompute_qresearch_v4_12_2011-01-03_2026-02-27.npz"

# ── Feature taxonomy — hard-coded by indicator_names ordering ─────────
FEATURE_TAXONOMY: Dict[str, str] = {
    # 0-13 : short-horizon technical (RSI-family)
    "RSI": "tech_short", "MACD": "tech_short", "SMA_CROSS": "tech_short",
    "BBP": "tech_short", "CCI": "tech_short", "VOL_SPIKE": "tech_short",
    "STOCH": "tech_short", "OBV_POS": "tech_short", "ATR_LOW": "tech_short",
    "MFI": "tech_short", "ADX": "tech_short", "WILLR": "tech_short",
    "ROC": "tech_short", "VWAP_ABOVE": "tech_short",
    # 14-16 : long-horizon momentum
    "MOM_3M": "mom_long", "MOM_6M": "mom_long", "MOM_12M_EX1M": "mom_long",
    # 17-22 : breakout / trend strength
    "BREAKOUT_252": "breakout", "RSI_TREND": "breakout",
    "SMA50_SLOPE": "breakout", "BREAKOUT_126": "breakout",
    "DIST_FROM_SMA50": "breakout", "HIGH_20_BREAK": "breakout",
    # 23-27 : fundamental (value / quality / leverage / cashflow)
    "VAL_EARN_YIELD": "fund_value", "VAL_BOOK2PRICE": "fund_value",
    "QUAL_ROE": "fund_quality",
    "LEV_DEBT_EQUITY": "fund_leverage",
    "CF_FCF_YIELD": "fund_cashflow",
    # 28-35 : interaction (composites)
    "QUAL_ROE_X_MOM_6M": "interact",
    "VAL_EARN_YIELD_X_MOM_6M": "interact",
    "CF_FCF_YIELD_X_MOM_6M": "interact",
    "QUAL_ROE_X_BREAKOUT_126": "interact",
    "VAL_BOOK2PRICE_X_MOM_6M": "interact",
    "MOM_12M_EX1M_X_QUAL_ROE": "interact",
    "BREAKOUT_252_X_CF_FCF_YIELD": "interact",
    "LEV_DEBT_EQUITY_X_MOM_6M": "interact",
}

CATEGORY_ORDER = [
    "tech_short", "mom_long", "breakout",
    "fund_value", "fund_quality", "fund_leverage", "fund_cashflow",
    "interact",
]
CATEGORY_LABEL = {
    "tech_short":   "Technical short-horizon",
    "mom_long":     "Long-horizon momentum",
    "breakout":     "Breakout / trend strength",
    "fund_value":   "Fundamental: value",
    "fund_quality": "Fundamental: quality",
    "fund_leverage":"Fundamental: leverage",
    "fund_cashflow":"Fundamental: cash flow",
    "interact":     "Interaction composites",
}


# ── Helpers ───────────────────────────────────────────────────────────
def _load_signals(pack_ind_names: np.ndarray) -> List[Dict[str, Any]]:
    loaded: List[Dict[str, Any]] = []
    for s in SIGNALS:
        npz = np.load(s["path"], allow_pickle=True)
        mask = np.asarray(npz["mask"], dtype=bool)
        wb   = np.asarray(npz["wb"],   dtype=np.float64)
        ws   = np.asarray(npz["ws"],   dtype=np.float64)
        wd   = np.asarray(npz["wd"],   dtype=np.float64)
        if mask.shape != (36,):
            raise RuntimeError(f"{s['arm']} mask shape != (36,): {mask.shape}")
        if not (len(wb) == len(ws) == len(wd) == 36):
            raise RuntimeError(f"{s['arm']} weights shape mismatch")
        loaded.append({
            "id": s["id"], "arm": s["arm"], "path": s["path"],
            "mask": mask, "wb": wb, "ws": ws, "wd": wd,
        })
    return loaded


def _classify_features(ind_names: np.ndarray) -> List[str]:
    cats: List[str] = []
    for n in ind_names:
        cats.append(FEATURE_TAXONOMY.get(str(n), "other"))
    return cats


def _regime_exposure(signal: Dict[str, Any], ind_names: np.ndarray,
                     categories: List[str]) -> Dict[str, Dict[str, float]]:
    """For each regime (B/S/D), for each category, sum abs(weight) of active features."""
    per_regime: Dict[str, Dict[str, float]] = {}
    for reg, wkey in (("B", "wb"), ("S", "ws"), ("D", "wd")):
        w = signal[wkey]
        mask = signal["mask"]
        by_cat: Dict[str, float] = {c: 0.0 for c in CATEGORY_ORDER}
        for i, (c, active, weight) in enumerate(zip(categories, mask, w)):
            if not active:
                continue
            by_cat.setdefault(c, 0.0)
            by_cat[c] += float(abs(weight))
        total = sum(by_cat.values()) or 1e-9
        pct = {k: (v / total * 100.0) for k, v in by_cat.items()}
        per_regime[reg] = {
            "abs_sum": by_cat,
            "pct": pct,
            "total_abs_weight": total,
        }
    return per_regime


# ── Main audit ────────────────────────────────────────────────────────
def run_audit() -> Dict[str, Any]:
    pack = np.load(PACK_PATH, allow_pickle=True)
    ind_names = np.asarray(pack["indicator_names"])
    if len(ind_names) != 36:
        raise RuntimeError(f"expected 36 indicators, got {len(ind_names)}")
    categories = _classify_features(ind_names)

    sigs = _load_signals(ind_names)
    baseline = next(s for s in sigs if s["id"] == "baseline")

    # ── Per-signal summary ─────────────────────────────────────────
    summary: List[Dict[str, Any]] = []
    for s in sigs:
        n_active = int(s["mask"].sum())
        exposure = _regime_exposure(s, ind_names, categories)
        # total magnitude of weights on active features
        abs_wb = float(np.abs(s["wb"][s["mask"]]).sum())
        abs_ws = float(np.abs(s["ws"][s["mask"]]).sum())
        abs_wd = float(np.abs(s["wd"][s["mask"]]).sum())
        summary.append({
            "id": s["id"], "arm": s["arm"],
            "n_active": n_active,
            "active_features": [str(ind_names[i]) for i, a in enumerate(s["mask"]) if a],
            "category_counts": {c: int(sum(1 for i, a in enumerate(s["mask"])
                                           if a and categories[i] == c))
                                for c in CATEGORY_ORDER},
            "abs_weight_sum_B": abs_wb,
            "abs_weight_sum_S": abs_ws,
            "abs_weight_sum_D": abs_wd,
            "exposure_by_category": exposure,
        })

    # ── Cross-signal feature matrix (mask × 4 signals) ────────────
    feature_matrix: List[Dict[str, Any]] = []
    for i, nm in enumerate(ind_names):
        row = {
            "idx": i,
            "name": str(nm),
            "category": categories[i],
            "active": {},
            "weight_B": {}, "weight_S": {}, "weight_D": {},
        }
        for s in sigs:
            row["active"][s["id"]]   = bool(s["mask"][i])
            row["weight_B"][s["id"]] = float(s["wb"][i])
            row["weight_S"][s["id"]] = float(s["ws"][i])
            row["weight_D"][s["id"]] = float(s["wd"][i])
        feature_matrix.append(row)

    # ── Baseline-exclusive / retrain-exclusive (set-theoretic) ────
    retrain_ids = [s["id"] for s in sigs if s["id"] != "baseline"]
    baseline_only: List[str] = []         # in baseline, in 0 retrains
    retrain_consensus: List[str] = []     # in all 3 retrains, not in baseline
    shared_all: List[str] = []            # in all 4
    no_one: List[str] = []                # in none
    for row in feature_matrix:
        b_active = row["active"]["baseline"]
        r_actives = [row["active"][r] for r in retrain_ids]
        if b_active and not any(r_actives):
            baseline_only.append(row["name"])
        if (not b_active) and all(r_actives):
            retrain_consensus.append(row["name"])
        if b_active and all(r_actives):
            shared_all.append(row["name"])
        if (not b_active) and not any(r_actives):
            no_one.append(row["name"])

    # ── BULL-tilt delta: baseline wb vs retrain (t1b) wb on shared features
    bull_tilt_deltas: List[Dict[str, Any]] = []
    for row in feature_matrix:
        if not (row["active"]["baseline"] and row["active"]["t1b"]):
            continue
        wb_base = row["weight_B"]["baseline"]
        wb_t1b  = row["weight_B"]["t1b"]
        ws_base = row["weight_S"]["baseline"]
        ws_t1b  = row["weight_S"]["t1b"]
        bull_tilt_deltas.append({
            "name": row["name"], "category": row["category"],
            "wb_baseline": wb_base, "wb_t1b": wb_t1b,
            "wb_delta": wb_base - wb_t1b,
            "ws_baseline": ws_base, "ws_t1b": ws_t1b,
            "ws_delta": ws_base - ws_t1b,
            "bull_vs_side_tilt_base": wb_base - ws_base,
            "bull_vs_side_tilt_t1b":  wb_t1b  - ws_t1b,
        })
    bull_tilt_deltas.sort(key=lambda r: abs(r["wb_delta"]), reverse=True)

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pack": os.path.basename(PACK_PATH),
            "n_indicators": 36,
        },
        "summary": summary,
        "feature_matrix": feature_matrix,
        "set_analysis": {
            "baseline_only":     baseline_only,
            "retrain_consensus": retrain_consensus,
            "shared_all":        shared_all,
            "no_one":            no_one,
        },
        "bull_tilt_deltas": bull_tilt_deltas,
        "indicator_names": [str(n) for n in ind_names],
        "categories": categories,
    }


# ── Markdown writer ───────────────────────────────────────────────────
def _fmt_weight(v: float) -> str:
    if not np.isfinite(v):
        return "  n/a "
    return f"{v:+.3f}"


def _fmt_active(a: bool) -> str:
    return "●" if a else "·"


def write_markdown(report: Dict[str, Any], path: str) -> None:
    lines: List[str] = []
    meta = report["meta"]
    sigs = report["summary"]
    ind_names = report["indicator_names"]
    cats = report["categories"]

    lines.append("# Signal Feature Audit — Factor Tilt Decomposition")
    lines.append("")
    lines.append(f"**Generated**: {meta['generated_at']}")
    lines.append(f"**Pack**: `{meta['pack']}`")
    lines.append(f"**Signals**: {', '.join(s['arm'] for s in sigs)}")
    lines.append("")
    lines.append("**Purpose**: Identify *factor tilt* that explains Baseline_V2's BULL-regime "
                 "CAGR advantage despite inferior mean IC (T5 Phase A diagnostic).")
    lines.append("")

    # ── §1 Active count + category split ──
    lines.append("## 1. Active features — count & category breakdown")
    lines.append("")
    hdr = "| Signal | #active | " + " | ".join(CATEGORY_LABEL[c] for c in CATEGORY_ORDER) + " | Σ\\|wb\\| | Σ\\|ws\\| | Σ\\|wd\\| |"
    sep = "|" + "---|" * (2 + len(CATEGORY_ORDER) + 3) + ""
    lines.append(hdr)
    lines.append(sep)
    for s in sigs:
        cc = s["category_counts"]
        row = f"| **{s['arm']}** | {s['n_active']} | " + \
              " | ".join(str(cc[c]) for c in CATEGORY_ORDER) + \
              f" | {s['abs_weight_sum_B']:.2f} | {s['abs_weight_sum_S']:.2f} | {s['abs_weight_sum_D']:.2f} |"
        lines.append(row)
    lines.append("")

    # ── §2 Set analysis ──
    sa = report["set_analysis"]
    lines.append("## 2. Set analysis — who selects what")
    lines.append("")
    lines.append("| Set | # | Features |")
    lines.append("|---|---:|---|")
    lines.append(f"| **Baseline-only** (baseline uses, 0 retrains) | {len(sa['baseline_only'])} | "
                 f"{', '.join(sa['baseline_only']) or '—'} |")
    lines.append(f"| **Retrain-consensus** (all 3 retrains, not baseline) | {len(sa['retrain_consensus'])} | "
                 f"{', '.join(sa['retrain_consensus']) or '—'} |")
    lines.append(f"| **Shared-all** (all 4 signals) | {len(sa['shared_all'])} | "
                 f"{', '.join(sa['shared_all']) or '—'} |")
    lines.append(f"| **Rejected-all** (no signal uses) | {len(sa['no_one'])} | "
                 f"{', '.join(sa['no_one']) or '—'} |")
    lines.append("")

    # ── §3 Per-regime category exposure (% of |weight|) ──
    lines.append("## 3. Per-regime category exposure — % of Σ|weight|")
    lines.append("")
    for reg, label in (("B", "BULL"), ("S", "SIDE"), ("D", "DEFENSIVE")):
        lines.append(f"### {label} regime")
        lines.append("")
        lines.append("| Signal | " + " | ".join(CATEGORY_LABEL[c] for c in CATEGORY_ORDER) + " |")
        lines.append("|" + "---|" * (1 + len(CATEGORY_ORDER)))
        for s in sigs:
            pct = s["exposure_by_category"][reg]["pct"]
            cells = " | ".join(f"{pct.get(c, 0.0):5.1f}%" for c in CATEGORY_ORDER)
            lines.append(f"| **{s['arm']}** | {cells} |")
        lines.append("")

    # ── §4 Full feature matrix (mask ● active ·) ──
    lines.append("## 4. Full feature matrix (36 × 4 signals)")
    lines.append("")
    lines.append("Active = ●   Inactive = ·   (wb = BULL weight)")
    lines.append("")
    lines.append("| # | Category | Feature | Base active | P5 active | T1 active | T1b active | wb base | wb T1b |")
    lines.append("|---|---|---|:---:|:---:|:---:|:---:|---|---|")
    for row in report["feature_matrix"]:
        cat_short = row["category"]
        lines.append(
            f"| {row['idx']:2d} | {cat_short} | `{row['name']}` | "
            f"{_fmt_active(row['active']['baseline'])} | "
            f"{_fmt_active(row['active']['p5'])} | "
            f"{_fmt_active(row['active']['t1'])} | "
            f"{_fmt_active(row['active']['t1b'])} | "
            f"{_fmt_weight(row['weight_B']['baseline'])} | "
            f"{_fmt_weight(row['weight_B']['t1b'])} |"
        )
    lines.append("")

    # ── §5 BULL tilt delta on shared features ──
    lines.append("## 5. BULL-regime weight delta (baseline − T1b) — shared active features")
    lines.append("")
    lines.append("Rows with positive Δwb mean **baseline weighs this factor more heavily in BULL** than T1b. "
                 "Negative Δwb means retrain weighs more heavily.  `BvsS_base = wb_base − ws_base` "
                 "quantifies **how much baseline amplifies this factor in BULL vs SIDE** (internal tilt).")
    lines.append("")
    lines.append("| Feature | Cat | wb base | wb T1b | Δwb | ws base | ws T1b | Δws | BvsS base | BvsS T1b |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in report["bull_tilt_deltas"]:
        lines.append(
            f"| `{r['name']}` | {r['category']} | "
            f"{r['wb_baseline']:+.3f} | {r['wb_t1b']:+.3f} | **{r['wb_delta']:+.3f}** | "
            f"{r['ws_baseline']:+.3f} | {r['ws_t1b']:+.3f} | {r['ws_delta']:+.3f} | "
            f"{r['bull_vs_side_tilt_base']:+.3f} | {r['bull_vs_side_tilt_t1b']:+.3f} |"
        )
    lines.append("")

    # ── §6 Interpretation scaffold ──
    lines.append("## 6. Interpretation (automated scaffold)")
    lines.append("")
    baseline_sum = next(s for s in sigs if s["id"] == "baseline")
    t1b_sum      = next(s for s in sigs if s["id"] == "t1b")
    lines.append(f"- **Active count**: baseline uses {baseline_sum['n_active']}, T1b uses {t1b_sum['n_active']}.")
    bcat = baseline_sum["category_counts"]
    tcat = t1b_sum["category_counts"]
    for c in CATEGORY_ORDER:
        if bcat[c] != tcat[c]:
            lines.append(f"- **{CATEGORY_LABEL[c]}**: baseline picks {bcat[c]}, T1b picks {tcat[c]}  (Δ = {bcat[c]-tcat[c]:+d}).")
    # Highlight BULL-heavy baseline categories
    bull_pct_base = baseline_sum["exposure_by_category"]["B"]["pct"]
    side_pct_base = baseline_sum["exposure_by_category"]["S"]["pct"]
    lines.append("")
    lines.append("- **Baseline's internal BULL-vs-SIDE tilt (% points, top 3 categories)**:")
    tilts = [(c, bull_pct_base[c] - side_pct_base[c]) for c in CATEGORY_ORDER]
    tilts.sort(key=lambda x: x[1], reverse=True)
    for c, dlt in tilts[:3]:
        lines.append(f"    - {CATEGORY_LABEL[c]}: **{dlt:+.1f}pp** (BULL exposure ↑ vs SIDE)")
    lines.append("")
    lines.append("- **Top 3 features with largest positive Δwb (baseline > T1b in BULL)**:")
    pos = [r for r in report["bull_tilt_deltas"] if r["wb_delta"] > 0][:3]
    for r in pos:
        lines.append(f"    - `{r['name']}` ({r['category']}): Δwb = {r['wb_delta']:+.3f}")
    lines.append("")
    lines.append("- **Top 3 features with largest negative Δwb (T1b > baseline in BULL)**:")
    neg = sorted([r for r in report["bull_tilt_deltas"] if r["wb_delta"] < 0],
                 key=lambda r: r["wb_delta"])[:3]
    for r in neg:
        lines.append(f"    - `{r['name']}` ({r['category']}): Δwb = {r['wb_delta']:+.3f}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 7. Takeaway scaffold (human to fill in)")
    lines.append("")
    lines.append("1. **Dominant factor family in baseline BULL**: see §3 BULL row, identify the 1-2 categories "
                 "baseline allocates disproportionally high % to.")
    lines.append("2. **Baseline-exclusive features**: see §2 baseline_only set. These are candidate features "
                 "to *inject* into T1b (via mask union + weight copy).")
    lines.append("3. **Shared features with wb_delta > 0**: baseline weighs them heavier in BULL; "
                 "candidate for partial weight-blending (Option A in T2 blend).")
    lines.append("4. **Pure BULL-specific**: features where `BvsS_base` is large positive but retrains keep small → "
                 "asymmetric amplification is the baseline's trick.")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def main() -> int:
    print("=" * 72)
    print("  Signal Feature Audit — factor tilt decomposition")
    print("=" * 72)
    report = run_audit()

    print()
    print("── Active feature counts ──")
    for s in report["summary"]:
        print(f"  {s['arm']:<22s}  n_active={s['n_active']:<3d}  "
              f"Σ|wb|={s['abs_weight_sum_B']:.2f}  Σ|ws|={s['abs_weight_sum_S']:.2f}  "
              f"Σ|wd|={s['abs_weight_sum_D']:.2f}")

    sa = report["set_analysis"]
    print()
    print("── Set analysis ──")
    print(f"  baseline-only     ({len(sa['baseline_only']):2d}): {sa['baseline_only']}")
    print(f"  retrain-consensus ({len(sa['retrain_consensus']):2d}): {sa['retrain_consensus']}")
    print(f"  shared-all        ({len(sa['shared_all']):2d}): {sa['shared_all']}")
    print(f"  rejected-all      ({len(sa['no_one']):2d}): {sa['no_one']}")

    print()
    print("── Baseline BULL vs SIDE tilt (top 3 by Δ in pct-points) ──")
    base = next(s for s in report["summary"] if s["id"] == "baseline")
    bull_pct = base["exposure_by_category"]["B"]["pct"]
    side_pct = base["exposure_by_category"]["S"]["pct"]
    tilts = [(c, bull_pct[c] - side_pct[c]) for c in CATEGORY_ORDER]
    tilts.sort(key=lambda x: x[1], reverse=True)
    for c, dlt in tilts[:5]:
        print(f"  {CATEGORY_LABEL[c]:<32s}  ΔBvsS = {dlt:+.1f}pp  "
              f"(B={bull_pct[c]:5.1f}%  S={side_pct[c]:5.1f}%)")

    # Persist
    os.makedirs(DOCS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path   = os.path.join(DOCS_DIR, f"signal_feature_audit_{stamp}.md")
    json_path = os.path.join(DOCS_DIR, f"signal_feature_audit_{stamp}.json")
    write_markdown(report, md_path)
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=float)
    print()
    print(f"[saved] {md_path}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
