"""Step 3C — T10 parity dry-run.

Compares what `autotrade.intents` would emit against what the **T10 manual
execution loop** (`launcher.py:613` + `holdings_manager.apply_partial_execution`)
would do, for the same actionable artifact + local holdings state.

NETWORK-FREE. Reads only:
- `holdings_log.xlsx` → `Current` sheet
- `daily_runs/<run_id>/recommendations.csv`

T10 reference logic (extracted from `holdings_manager.py:511-548` and
`launcher.py:660-674`):

    if action in FULL_CLOSE:
        qty = current[ticker].Shares          # broker-truth qty
    elif action in PARTIAL_CLOSE and action != "TRIM_GRACE":
        qty = min(reco.Shares, held - 1)      # never fully close via partial
    elif action in {BUY, BUY_NEW, BUY_MORE}:
        qty = reco.Shares                     # add to position
    else:
        qty = SKIP                            # SELL_GRACE / HOLD / DEFERRED

Current `autotrade.intents` always uses `reco.Shares`. This module surfaces
the diff so the operator decides whether to:

(a) accept T10 logic in autotrade (preferred — broker is source of truth)
(b) keep autotrade as-is (only OK if reco was just generated and untouched)

The report includes per-row diff, by-action counts, and a top-line verdict
(`parity_ok` boolean).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from cache_health import load_config  # type: ignore[import-not-found]
from exits import RecosAction  # type: ignore[import-not-found]
from holdings_manager import HoldingsManager  # type: ignore[import-not-found]

from phase3.autotrade.kis_broker_adapter import load_env_config
from phase3.autotrade.intents import classify_action, _BUY_ACTIONS, _INFO_ACTIONS  # noqa: F401
from phase3.autotrade.reconcile import _PROFILE_CONFIG, _resolve_config_path
from phase3.autotrade.intents import load_artifact


# ─── T10 reference qty resolver ─────────────────────────────────────────
def t10_qty_for_row(
    row: pd.Series, holdings_qty_by_ticker: Dict[str, int],
) -> Tuple[Optional[int], str]:
    """Return (qty, role). role ∈ {'buy','full_close','partial_close','skip','grace_special'}.

    Mirrors `HoldingsManager.apply_partial_execution`. TRIM_GRACE is excluded
    from the partial-close path (handled by daily_runner directly), so we tag
    it 'grace_special' and emit no qty.
    """
    action = str(row.get("Action", "") or "").upper()
    ticker = str(row.get("Ticker", "") or "").upper()
    reco_shares = _to_int(row.get("Shares", 0))
    held = int(holdings_qty_by_ticker.get(ticker, 0))

    if action in _BUY_ACTIONS:
        return reco_shares, "buy"
    if RecosAction.is_full_close(action):
        # T10 always uses live broker/holdings qty for full close.
        return held, "full_close"
    if RecosAction.is_partial_close(action):
        if action == "TRIM_GRACE":
            return None, "grace_special"
        # Never fully close via partial — keep at least 1 share.
        if held <= 1:
            return 0, "partial_close"   # cannot trim further
        return min(reco_shares, held - 1), "partial_close"
    if RecosAction.is_no_op(action) or action in _INFO_ACTIONS:
        return None, "skip"
    return None, "skip"


def autotrade_qty_for_row(row: pd.Series) -> Tuple[Optional[int], str]:
    """Reflects current `intents.py` behaviour: always reco.Shares."""
    action = str(row.get("Action", "") or "").upper()
    side = classify_action(action)
    if side == "SKIP":
        return None, "skip"
    qty = _to_int(row.get("Shares", 0))
    if action in _BUY_ACTIONS:
        return qty, "buy"
    if RecosAction.is_full_close(action):
        return qty, "full_close"
    if RecosAction.is_partial_close(action):
        if action == "TRIM_GRACE":
            return None, "grace_special"
        return qty, "partial_close"
    return None, "skip"


def _to_int(x: Any) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


# ─── parity report ──────────────────────────────────────────────────────
@dataclass
class ParityRow:
    ticker: str
    action: str
    role: str
    held: int
    reco_shares: int
    t10_qty: Optional[int]
    autotrade_qty: Optional[int]
    delta: Optional[int]    # autotrade - t10 (positive ⇒ autotrade is larger)
    verdict: str            # 'match' | 'mismatch_qty' | 'mismatch_role' | 'both_skip'


@dataclass
class ParityReport:
    timestamp: str
    profile: str
    run_id: str
    run_status: str
    holdings_log_path: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    by_verdict: Dict[str, int] = field(default_factory=dict)
    by_role: Dict[str, int] = field(default_factory=dict)
    parity_ok: bool = True


def build_parity(
    recos: pd.DataFrame,
    holdings_current: pd.DataFrame,
    *,
    run_id: str,
    run_status: str,
    profile: str,
    holdings_log_path: str,
) -> ParityReport:
    held_by_ticker: Dict[str, int] = {}
    if not holdings_current.empty:
        for _, r in holdings_current.iterrows():
            t = str(r.get("Ticker", "") or "").upper()
            try:
                held_by_ticker[t] = int(r.get("Shares") or 0)
            except (TypeError, ValueError):
                held_by_ticker[t] = 0

    rows: List[ParityRow] = []
    by_verdict: Dict[str, int] = {}
    by_role: Dict[str, int] = {}

    for _, row in recos.iterrows():
        ticker = str(row.get("Ticker", "") or "").upper()
        action = str(row.get("Action", "") or "").upper()

        t10_qty, t10_role = t10_qty_for_row(row, held_by_ticker)
        at_qty, at_role = autotrade_qty_for_row(row)

        if t10_role == "skip" and at_role == "skip":
            verdict = "both_skip"
        elif t10_role != at_role:
            verdict = "mismatch_role"
        elif t10_qty == at_qty:
            verdict = "match"
        else:
            verdict = "mismatch_qty"

        delta: Optional[int] = None
        if t10_qty is not None and at_qty is not None:
            delta = at_qty - t10_qty

        rows.append(ParityRow(
            ticker=ticker, action=action, role=t10_role,
            held=int(held_by_ticker.get(ticker, 0)),
            reco_shares=_to_int(row.get("Shares", 0)),
            t10_qty=t10_qty, autotrade_qty=at_qty,
            delta=delta, verdict=verdict,
        ))
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        by_role[t10_role] = by_role.get(t10_role, 0) + 1

    parity_ok = (by_verdict.get("mismatch_qty", 0) == 0
                 and by_verdict.get("mismatch_role", 0) == 0)

    return ParityReport(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        profile=profile, run_id=run_id, run_status=run_status,
        holdings_log_path=holdings_log_path,
        rows=[asdict(r) for r in rows],
        by_verdict=by_verdict, by_role=by_role,
        parity_ok=parity_ok,
    )


# ─── output ─────────────────────────────────────────────────────────────
def print_parity(rep: ParityReport, *, max_rows: int = 60) -> None:
    bar = "─" * 86
    print(bar)
    print(f"  PARITY  profile={rep.profile}  run_id={rep.run_id} ({rep.run_status})  "
          f"ts={rep.timestamp}")
    print(bar)
    print(f"  holdings_log : {rep.holdings_log_path}")
    print(f"  by role      : {rep.by_role}")
    print(f"  by verdict   : {rep.by_verdict}")
    print(f"  parity_ok    : {rep.parity_ok}  "
          f"({'no qty/role diff' if rep.parity_ok else 'differences found — review below'})")

    diff_rows = [r for r in rep.rows
                 if r["verdict"] in ("mismatch_qty", "mismatch_role")]
    if not diff_rows:
        print("\n  (all actionable rows match the T10 reference logic)")
    else:
        print(f"\n  ── differences ({len(diff_rows)} rows)")
        print(f"     {'ticker':6s} {'action':16s} {'role':14s} "
              f"{'held':>5s} {'reco':>5s} {'t10':>5s} {'autotr':>7s} "
              f"{'delta':>6s} {'verdict':18s}")
        for r in diff_rows[:max_rows]:
            print(f"     {r['ticker']:6s} {r['action']:16s} {r['role']:14s} "
                  f"{r['held']:>5d} {r['reco_shares']:>5d} "
                  f"{(str(r['t10_qty']) if r['t10_qty'] is not None else '-'):>5s} "
                  f"{(str(r['autotrade_qty']) if r['autotrade_qty'] is not None else '-'):>7s} "
                  f"{(str(r['delta']) if r['delta'] is not None else '-'):>6s} "
                  f"{r['verdict']}")
        if len(diff_rows) > max_rows:
            print(f"     … {len(diff_rows) - max_rows} more (see JSON dump)")
    print(bar)


def dump_json(rep: ParityReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = rep.timestamp.replace(":", "").replace("-", "")[:15]
    path = out_dir / f"parity_{ts_compact}_{rep.profile}.json"
    path.write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# ─── CLI ────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 3C — T10 parity check (network-free).",
    )
    ap.add_argument("--profile", default="paper", choices=list(_PROFILE_CONFIG))
    ap.add_argument("--run-id", default=None,
                    help="specific run id; default = latest awaiting_execution")
    ap.add_argument("--json-out-dir", default=None)
    args = ap.parse_args(argv)

    cfg_path = _resolve_config_path(args.profile)
    cfg = load_config(str(cfg_path))

    run_dir, run_meta, recos = load_artifact(
        cfg["paths"]["output_dir"], run_id=args.run_id,
    )
    run_id = str(run_meta.get("run_id") or run_dir.name)
    run_status = str(run_meta.get("status") or "")

    hm = HoldingsManager(cfg["paths"]["holdings_log"])
    current = hm.load_current()

    print(f"[load] profile={args.profile}  artifact={run_dir}")
    print(f"[load] reco rows={len(recos)}  current holdings={len(current)} tickers")

    rep = build_parity(
        recos=recos, holdings_current=current,
        run_id=run_id, run_status=run_status,
        profile=args.profile,
        holdings_log_path=cfg["paths"]["holdings_log"],
    )
    print()
    print_parity(rep)

    env_cfg = load_env_config()
    out_dir = Path(args.json_out_dir).expanduser() if args.json_out_dir else env_cfg.log_dir
    out_path = dump_json(rep, out_dir)
    print(f"\n[ok] JSON dump → {out_path}")
    return 0 if rep.parity_ok else 1


if __name__ == "__main__":
    sys.exit(main())
