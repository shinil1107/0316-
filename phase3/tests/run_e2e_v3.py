"""Run E2E GA v3 — expanded 15Y window (2011-01-03 → 2026-03-31).

This produces a DENSE frozen signal (all 36 features potentially active)
to serve as a "regularizer" component in cross-era L3 blending.

Changes from the original E2E (v2):
  - Start date pushed back to 2011-01-03 (vs 2017 in original)
  - Population P=40, Generations G=40 (vs P=30, G=32)
  - Uses the SAME reference signal as original (V2_GOLDEN as seed)
  - Target: k=12-18 active features, dense weights across all features

Expected runtime: ~8-12 hours depending on machine load.
"""
import functools
import json
import os
import sys
import time
from datetime import datetime

print = functools.partial(print, flush=True)

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(PHASE3_DIR)
for _p in (ROOT, PHASE3_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phase3.e2e_ga import run_e2e_ga, save_e2e_signal  # noqa: E402

OUTPUT_SIG_DIR = "/Users/shin-il/Documents/my stock/cache_fmp_c2_1/output"
DOCS_DIR = os.path.join(PHASE3_DIR, "docs")


def main():
    print("=" * 70)
    print("  E2E GA v3 — 15Y Expanded Data Run")
    print("=" * 70)
    print(f"  Start: {datetime.now().isoformat(timespec='seconds')}")
    print(f"  Window: 2011-01-03 → 2026-03-31")
    print(f"  P=40, G=40, train_years=4, val_years=2, step_years=2")
    print(f"  Seed: 20260507")
    print()

    t0 = time.time()

    result = run_e2e_ga(
        population_size=40,
        generations=40,
        elite_frac=0.15,
        crossover_prob=0.6,
        mutation_rate_mask=0.10,
        mutation_rate_weight=0.25,
        weight_noise_sd=0.3,
        immigration_rate=0.15,
        seed=20260507,
        train_years=4,
        val_years=2,
        step_years=2,
        initial_capital=100000.0,
        daily_buy_limit=1000.0,
        commission_bps=10.0,
        slippage_bps=5.0,
        train_weight=0.3,
        val_weight=0.7,
        ic_min_threshold=0.012,
        pos_ic_min=0.52,
        spread_min_threshold=0.004,
        start_date="2011-01-03",
        end_date="2026-03-31",
    )

    elapsed = time.time() - t0
    print(f"\n  Total elapsed: {elapsed/60:.1f} min ({elapsed/3600:.2f} h)")

    sig_path = save_e2e_signal(result, OUTPUT_SIG_DIR, label="E2E_v3")
    print(f"\n  [saved] {sig_path}")

    log_path = os.path.join(DOCS_DIR, f"e2e_v3_run_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    log_data = {
        "label": "E2E_v3",
        "start_date": "2011-01-03",
        "end_date": "2026-03-31",
        "population_size": 40,
        "generations": 40,
        "seed": 20260507,
        "elapsed_sec": round(elapsed, 1),
        "elapsed_hours": round(elapsed / 3600, 2),
        "best_fitness": result["best_fitness"],
        "best_val_cagr": result["best_meta"].get("avg_val_cagr", 0),
        "best_val_sharpe": result["best_meta"].get("avg_val_sharpe", 0),
        "best_k_used": result["best_meta"].get("k_used", 0),
        "signal_path": sig_path,
        "generation_log": result["generation_log"],
    }
    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)
    print(f"  [log]   {log_path}")

    print()
    print("=" * 70)
    print("  Next steps:")
    print("  1) Register E2E_v3 in step_d_walk_forward.py")
    print("  2) Build Cross-Era L3 blend: P2_BATCH11 + E2E_v3 + B8 signals")
    print("  3) Compare against Baseline_V2")
    print("=" * 70)


if __name__ == "__main__":
    main()
