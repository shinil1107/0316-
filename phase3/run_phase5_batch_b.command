#!/bin/bash
# Phase B — 6-way scalar-sweep batch launcher.
# See phase3/docs/phase_b_batch_plan.md for the full decision record.
#
# Defaults to Batch 1 (profile sweep). Pass "2" for Batch 2 (window sweep)
# or "all" for the full 6-run overnight pass. "dry" runs a ~12-minute smoke
# test over every preset.
#
#   Double-click                → runs Batch 1 (3 runs, ~9 h)
#   ./run_phase5_batch_b.command 2    → Batch 2 (3 runs, ~9 h)
#   ./run_phase5_batch_b.command all  → both batches (6 runs, ~18 h)
#   ./run_phase5_batch_b.command dry  → 12-min wiring smoke test

cd "$(dirname "$0")/.."   # → 0316- (project root)

TARGET="${1:-1}"
EXTRA=""
case "$TARGET" in
    1|2)
        ARGS="--batch $TARGET"
        ;;
    all)
        ARGS=""
        ;;
    dry)
        ARGS="--dry-run"
        EXTRA="  (dry-run smoke)"
        ;;
    *)
        echo "usage: $0 [1|2|all|dry]"
        echo "  1    — Batch 1 only (profile sweep: consv/prop/aggr)"
        echo "  2    — Batch 2 only (window sweep: win_base/win_fwd/win_back)"
        echo "  all  — both batches back-to-back"
        echo "  dry  — 12-min wiring smoke test (all 6 presets)"
        exit 2
        ;;
esac

echo "============================================================"
echo "  Phase B — Scalar-Sweep Batch Orchestrator$EXTRA"
echo "  Target      : $TARGET"
echo "  Start time  : $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

python3 -u phase3/run_phase5_batch_b.py $ARGS
status=$?

echo ""
echo "============================================================"
echo "  End time    : $(date '+%Y-%m-%d %H:%M:%S')  (exit code $status)"
echo "============================================================"

# Keep Terminal open so the user sees the final summary.
read -rp "Press ENTER to close this window… "
exit "$status"
