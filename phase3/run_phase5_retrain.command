#!/bin/bash
# One-click Phase 5 retrain launcher.
# See phase3/docs/phase5_retrain_plan.md for the full decision record.
#
# Runtime: ~2.5–3.5 hours on Apple Silicon (pack build + GA).
# Add --dry-run as argument for a 1–2 minute smoke test.

cd "$(dirname "$0")/.."   # → 0316- (project root)

echo "============================================================"
echo "  Phase 5 Retrain — stability-only GA on patched formula"
echo "  Start time: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

python3 -u phase3/run_phase5_retrain.py "$@"
status=$?

echo ""
echo "============================================================"
echo "  End time: $(date '+%Y-%m-%d %H:%M:%S')  (exit code $status)"
echo "============================================================"

# Keep Terminal open so the user sees the saved-signal path.
read -rp "Press ENTER to close this window… "
exit "$status"
