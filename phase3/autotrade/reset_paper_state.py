"""Operator tool — reset LOCAL autotrade state to match a fresh KIS
paper-account reset.

Context
-------
When the operator resets the KIS paper account back to its initial state
(0 positions, starting cash), the LOCAL source of truth is now out of
sync: the broker says "flat" while ``holdings_log.xlsx`` still shows the
pre-reset portfolio. The very next reconcile would flag every holding as
``local_only`` and a huge ``cash_drift``, and the t10/reconcile guards
would (correctly) hard-stop. This tool brings local state back to a clean
initial slate, *safely*.

What it does
------------
1. Refuses to run unless the trader is STOPPED (``global_halt`` set) or
   ``--force`` is given — so a launchd fire cannot run mid-reset.
2. ARCHIVES (moves, never deletes) the current ``holdings_log.xlsx`` and
   the ``daily_runs/`` artifacts into a timestamped archive folder, so
   the full pre-reset trading history is preserved for the record.
3. Recreates a fresh ``holdings_log.xlsx`` seeded with ``--initial-cash``
   — set this to the broker's ACTUAL post-reset cash so the next
   reconcile shows ~0 drift.
4. Prints the verification step (reconcile).

Safety
------
* Dry-run by DEFAULT. Nothing is moved or created until ``--yes``.
* Archiving is a ``move`` (the data still exists, just relocated), so a
  mistake is fully recoverable by moving the archive back.
* ``--initial-cash`` is REQUIRED for ``--yes`` (we will not guess the
  starting capital).

Usage
-----
    # dry-run (shows the plan, touches nothing)
    python -m phase3.autotrade.reset_paper_state --initial-cash 100000

    # execute (trader must be halted, or pass --force)
    python -m phase3.autotrade.reset_paper_state --initial-cash 100000 --yes
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _log(msg: str) -> None:
    print(f"[reset_paper_state] {msg}")


def _resolve_paths(config_path: Optional[Path]) -> Tuple[Path, Path]:
    """Return ``(holdings_log, output_dir)`` from the phase3 config."""
    from cache_health import load_config  # type: ignore[import-not-found]
    cfg_path = (config_path if config_path is not None
                else _PHASE3 / "config.yaml")
    cfg = load_config(str(cfg_path))
    paths = cfg.get("paths", {})
    holdings_log = Path(paths["holdings_log"])
    output_dir = Path(paths["output_dir"])
    return holdings_log, output_dir


def _is_halted() -> bool:
    try:
        from phase3.autotrade import global_halt
        return global_halt.is_halted()
    except Exception:  # noqa: BLE001
        return False


def plan_actions(
    *,
    holdings_log: Path,
    output_dir: Path,
    archive_dir: Path,
    keep_daily_runs: bool,
    runtime_dir: Optional[Path] = None,
) -> List[str]:
    """Human-readable description of what --yes would do."""
    actions: List[str] = []
    if holdings_log.exists():
        actions.append(f"MOVE  {holdings_log}  ->  {archive_dir / holdings_log.name}")
    else:
        actions.append(f"(holdings_log not found at {holdings_log} — fresh create only)")
    daily_runs = output_dir / "daily_runs"
    if not keep_daily_runs and daily_runs.exists():
        n = sum(1 for _ in daily_runs.iterdir())
        actions.append(f"MOVE  {daily_runs}  ({n} run dirs)  ->  {archive_dir / 'daily_runs'}")
    elif keep_daily_runs:
        actions.append("KEEP  daily_runs/ (--keep-daily-runs)")
    fh = _fire_history_path(runtime_dir)
    if fh is not None and fh.exists():
        actions.append(
            f"MOVE  {fh}  ->  {archive_dir / fh.name}  "
            f"(V2 lockout history; re-anchors drawdown baseline)")
    actions.append(f"CREATE fresh {holdings_log.name} seeded with INIT cash")
    return actions


def _fire_history_path(runtime_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate the V2 auto-lockout fire-history file, if the module is
    importable. Returns None when V2 isn't present (older checkout).

    ``runtime_dir`` MUST be threaded through by callers so tests can point
    at a temp runtime — otherwise ``auto_halt.history_path()`` resolves to
    the REAL ``phase3/autotrade/runtime/`` dir and a test run would archive
    (and then, via tempdir cleanup, destroy) the operator's live history.
    Production callers pass ``None`` to get the real default."""
    try:
        from phase3.autotrade import auto_halt
        return auto_halt.history_path(runtime_dir=runtime_dir)
    except Exception:  # noqa: BLE001
        return None


def execute_reset(
    *,
    holdings_log: Path,
    output_dir: Path,
    archive_dir: Path,
    initial_cash: float,
    keep_daily_runs: bool,
    runtime_dir: Optional[Path] = None,
) -> float:
    """Perform the archive + reseed. Returns the new INIT balance."""
    archive_dir.mkdir(parents=True, exist_ok=True)

    if holdings_log.exists():
        dest = archive_dir / holdings_log.name
        shutil.move(str(holdings_log), str(dest))
        _log(f"archived holdings_log -> {dest}")

    daily_runs = output_dir / "daily_runs"
    if not keep_daily_runs and daily_runs.exists():
        dest = archive_dir / "daily_runs"
        shutil.move(str(daily_runs), str(dest))
        _log(f"archived daily_runs -> {dest}")

    # V2 — archive the auto-lockout fire history so the drawdown baseline
    # re-anchors to the new seed (the earliest equity post-reset). Without
    # this the new account's first equity reading would be compared to the
    # pre-reset starting equity and could falsely (or fail to) trip.
    fh = _fire_history_path(runtime_dir)
    if fh is not None and fh.exists():
        dest = archive_dir / fh.name
        shutil.move(str(fh), str(dest))
        _log(f"archived fire history -> {dest}")

    # Recreate a fresh holdings_log and seed the opening cash. A brand-new
    # HoldingsManager writes empty sheets (cash=0); initialize_cash writes
    # the INIT ledger event so reconcile matches the reset broker cash.
    from phase3.holdings_manager import HoldingsManager
    hm = HoldingsManager(str(holdings_log))
    new_balance = hm.initialize_cash(float(initial_cash))
    _log(f"seeded fresh holdings_log INIT cash = ${float(new_balance):,.2f}")
    return float(new_balance)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reset local autotrade state after a KIS paper reset.")
    ap.add_argument("--config", type=Path, default=None,
                    help="phase3 config yaml (default: phase3/config.yaml)")
    ap.add_argument("--initial-cash", type=float, default=None,
                    help="opening cash to seed (match the broker's actual "
                         "post-reset cash). REQUIRED with --yes.")
    ap.add_argument("--archive-dir", type=Path, default=None,
                    help="archive destination (default: "
                         "<output_dir>/_archive/reset_<UTC ts>)")
    ap.add_argument("--keep-daily-runs", action="store_true",
                    help="do not archive daily_runs/ (only reset holdings)")
    ap.add_argument("--force", action="store_true",
                    help="run even if global_halt is NOT set (use only "
                         "when you are sure no fire is running)")
    ap.add_argument("--yes", action="store_true",
                    help="actually perform the reset (default is dry-run)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    holdings_log, output_dir = _resolve_paths(args.config)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir = (args.archive_dir if args.archive_dir is not None
                   else output_dir / "_archive" / f"reset_{ts}")

    _log(f"holdings_log = {holdings_log}")
    _log(f"output_dir   = {output_dir}")
    _log(f"archive_dir  = {archive_dir}")

    halted = _is_halted()
    _log(f"global_halt set = {halted}")

    _log("planned actions:")
    for a in plan_actions(holdings_log=holdings_log, output_dir=output_dir,
                          archive_dir=archive_dir,
                          keep_daily_runs=args.keep_daily_runs):
        _log(f"  - {a}")

    # Dry-run previews the plan ANYTIME (no halt required); only actual
    # execution (--yes) is gated on the trader being stopped.
    if not args.yes:
        _log("DRY-RUN — nothing changed. Re-run with --initial-cash <N> "
             "--yes to execute.")
        return 0

    if not halted and not args.force:
        _log("REFUSING: trader is not halted. Press STOP in the panel "
             "(or `v1_runner` global_halt) first, or pass --force if you "
             "are certain no launchd fire will run during the reset.")
        return 2

    if args.initial_cash is None:
        _log("ERROR: --initial-cash is required with --yes (set it to the "
             "broker's actual post-reset cash).")
        return 2

    execute_reset(holdings_log=holdings_log, output_dir=output_dir,
                  archive_dir=archive_dir, initial_cash=args.initial_cash,
                  keep_daily_runs=args.keep_daily_runs)
    _log("DONE. Verify before re-arming:")
    _log("  python -m phase3.autotrade.reconcile --profile paper "
         "   # expect 0 positions, cash_drift ~ 0")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
