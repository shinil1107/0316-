"""Phase 3 autotrade — broker-connected execution layer.

DESIGN PRINCIPLE
----------------
The broker is the source of truth. `holdings_log.xlsx` becomes a mirror, not
the authority. See `docs/CURSOR_HANDOFF_AUTOTRADING_V0.md` for the full v0
blueprint.

SAFETY (Codex review P1-A — split paper vs live submit gate)
-----------------------------------------------------------
Two INDEPENDENT submit gates resolve via `SafetyGuard.submit_mode`:

  paper_submit ← env=paper, dry_run=False, KIS_PAPER_SUBMIT_OK=true
  live_capital ← env=live,  dry_run=False, KIS_CONFIRM_LIVE=true

Anything else short-circuits to 'dry_run' (logged, no transmission).
Flipping the paper gate cannot unlock live capital and vice-versa.

Both gates additionally require: NOT global_halt and NOT cancel_all_pending.

- BUY_ONLY_MODE default true (v0 Stage 2). SELL raises SafetyError until disabled.
- All REST traffic is mirrored to per-day jsonl audit logs (~/.kis_audit/).
"""

__version__ = "0.0.1"
