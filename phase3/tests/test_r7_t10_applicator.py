"""Round 7-A acceptance — phase3.autotrade.t10_applicator.

Each test builds a synthetic ``run_dir`` on disk (tempfile) plus a fake
broker adapter and fake HoldingsManager, then calls ``cmd_apply`` with
the injection points the applicator exposes. No network, no real
HoldingsManager, no real KIS.

The nine cases match the R7 handoff §5.1 list verbatim:

  1. ODNO normalize (padded vs raw) still resolves to a fully-filled row.
  2. ``--dry-run`` writes preview + report files but leaves
     execution_applied.csv / run_meta.json / holdings untouched.
  3. ``--apply`` without ``AUTOTRADE_T10_APPLY_OK=true`` aborts with rc=2.
  4. A pre-existing RecRowId in execution_applied.csv aborts the batch.
  5. A submitted ODNO missing from ccnl aborts the batch.
  6. A ccnl row with ``filled_qty=0`` aborts the batch.
  7. A partial fill aborts unless ``--allow-partial`` is set.
  8. A clean full-fill batch in ``--apply`` mutates the fake hm and
     calls record_execution_artifact exactly once.
  9. A mixed-checkable artifact (BUY + SELL) gets ``partially_executed``
     because R7-A only applies BUY rows.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# Ensure phase3/ is importable (the module under test does this too).
_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import t10_applicator as ta


# ──────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────
def _make_args(*, run_id: str, dry_run: bool = True, apply: bool = False,
               allow_partial: bool = False,
               allow_duplicate_apply: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=run_id,
        profile="paper",
        dry_run=dry_run,
        apply=apply,
        allow_partial=allow_partial,
        allow_duplicate_apply=allow_duplicate_apply,
    )


def _write_run_dir(
    base: Path,
    run_id: str,
    *,
    submitted_intents: List[Dict[str, Any]],
    recos: List[Dict[str, Any]],
    status: str = "awaiting_execution",
    existing_applied: List[Dict[str, Any]] | None = None,
) -> Path:
    run_dir = base / "daily_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "schema_version": "artifact/v1",
        "run_id": run_id,
        "status": status,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))

    pd.DataFrame(recos).to_csv(run_dir / "recommendations.csv", index=False)

    with (run_dir / "autotrade_orders.jsonl").open("w") as fh:
        for i, raw in enumerate(submitted_intents):
            ev = {
                "event_kind": "transition",
                "autotrade_run_id": "at-FAKE-RUN",
                "mode": "paper_submit",
                "run_id": run_id,
                "rec_row_id": int(raw["rec_row_id"]),
                "ticker": raw["ticker"],
                "side": raw.get("side", "BUY"),
                "qty_intended": int(raw["qty"]),
                "qty_filled": 0.0,
                "qty_remaining": int(raw["qty"]),
                "limit_price": float(raw.get("limit", 100.0)),
                "client_order_id": raw.get(
                    "client_order_id",
                    f"co-FAKE-{raw['rec_row_id']}-B-{raw['qty']}-aaaaaa",
                ),
                "broker_order_id": raw["broker_order_id"],
                "state": "submitted",
                "status_source": "place_order_ack",
                "raw_broker_row": {},
                "echo": None,
                "fill_price": None,
                "fill_price_source": None,
                "error": None,
                "note": None,
                "schema_version": "autotrade_order_event/v1",
                "event_id": f"ev-FAKE-{i:04d}",
                "event_ts": f"2026-05-15T00:00:0{i}.000+00:00",
            }
            fh.write(json.dumps(ev) + "\n")

    if existing_applied:
        pd.DataFrame(existing_applied).to_csv(
            run_dir / "execution_applied.csv", index=False
        )
    return run_dir


def _ccnl_row(
    *,
    odno: str,
    ticker: str,
    ord_qty: int,
    filled_qty: int,
    filled_price: float = 0.0,
    side: str = "02",
) -> Dict[str, Any]:
    return {
        "odno": odno,
        "pdno": ticker,
        "sll_buy_dvsn_cd": side,
        "ft_ord_qty3": str(ord_qty),
        "ft_ccld_qty3": str(filled_qty),
        "ft_ord_unpr3": f"{filled_price or 1.0:.6f}",
        "ft_ccld_unpr3": f"{filled_price:.6f}" if filled_price else "",
        "dmst_ord_dt": "20260515",
        "ord_tmd": "224700",
    }


class _FakeAdapter:
    """Implements only what ``cmd_apply`` reaches for."""

    def __init__(self, ccnl_rows: List[Dict[str, Any]]):
        self.ccnl_rows = list(ccnl_rows)
        self.call_count = 0

    def get_order_history(self) -> List[Dict[str, Any]]:
        self.call_count += 1
        return list(self.ccnl_rows)


class _FakeHoldingsManager:
    """In-memory stand-in. Captures what would have been written."""

    def __init__(self):
        self.apply_calls: List[pd.DataFrame] = []
        self.cash_events: List[Dict[str, Any]] = []
        self.current = pd.DataFrame(columns=["Ticker", "Shares", "MarketValue"])
        self._cash = 100_000.0

    def apply_partial_execution(self, df: pd.DataFrame, *, trigger_type: str) -> None:
        self.apply_calls.append(df.copy())

    def record_cash_event(self, event_type: str, amount: float, notes: str = "") -> None:
        self.cash_events.append(
            {"type": event_type, "amount": float(amount), "notes": notes}
        )
        self._cash += float(amount)

    def load_current(self) -> pd.DataFrame:
        return self.current.copy()

    def get_cash_balance(self) -> float:
        return float(self._cash)

    def get_portfolio_value(self) -> float:
        return 0.0


class _FakeArtifactRecorder:
    """Stand-in for ``run_artifact.record_execution_artifact``."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, run_dir, executed_df, *, source, total_checkable_count,
                 portfolio_after_execution_df, cash_balance, total_capital,
                 operator_note=""):
        self.calls.append({
            "run_dir": Path(run_dir),
            "executed_rows": int(len(executed_df)),
            "source": source,
            "total_checkable_count": int(total_checkable_count),
            "cash_balance": float(cash_balance),
            "total_capital": float(total_capital),
            "operator_note": operator_note,
        })
        executed_total = int(len(executed_df))
        if total_checkable_count > 0 and executed_total >= total_checkable_count:
            status = "executed"
        else:
            status = "partially_executed"
        meta_path = Path(run_dir) / "run_meta.json"
        meta = json.loads(meta_path.read_text())
        meta["status"] = status
        meta_path.write_text(json.dumps(meta, indent=2))
        applied_path = Path(run_dir) / "execution_applied.csv"
        if applied_path.exists():
            prev = pd.read_csv(applied_path)
            combined = pd.concat([prev, executed_df], ignore_index=True)
        else:
            combined = executed_df.copy()
        combined.to_csv(applied_path, index=False)
        return {"execution_status": status, "executed_row_count_total": executed_total}


def _capture_run(args, *, ccnl_rows, hm=None, recorder=None):
    """Invoke cmd_apply with fakes and capture stdout/stderr + exit code."""
    adapter = _FakeAdapter(ccnl_rows)
    hm = hm or _FakeHoldingsManager()
    recorder = recorder or _FakeArtifactRecorder()

    def _make_adapter(*, paper_only: bool = True):  # noqa: ARG001
        return adapter

    def _make_hm(_):
        return hm

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = ta.cmd_apply(
            args,
            make_adapter=_make_adapter,
            make_hm=_make_hm,
            record_artifact=recorder,
        )
    return rc, out_buf.getvalue(), err_buf.getvalue(), adapter, hm, recorder


# Monkey-patch ``_resolve_paths`` so we don't need a real config.yaml on
# disk during tests.
class _BaseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Pretend the output_dir is our temp dir; the holdings_log path
        # is never opened by the fake hm, so it's fine to stub.
        self._orig_resolve = ta._resolve_paths
        ta._resolve_paths = lambda profile: (  # type: ignore[assignment]
            self.base,
            self.base / "fake_holdings_log.xlsx",
            self.base / "fake_config.yaml",
        )
        self.addCleanup(self._restore_resolve)
        # Also stub _count_checkable so we don't have to import RecosAction
        # in every test fixture. Tests that need a specific count override
        # this attribute again.
        self._orig_count = ta._count_checkable
        self.addCleanup(self._restore_count)

    def _restore_resolve(self):
        ta._resolve_paths = self._orig_resolve  # type: ignore[assignment]

    def _restore_count(self):
        ta._count_checkable = self._orig_count  # type: ignore[assignment]


# ──────────────────────────────────────────────────────────────────────
# 1. ODNO normalization
# ──────────────────────────────────────────────────────────────────────
class TestOdnoNormalization(_BaseFixture):
    def test_padded_submit_matches_raw_ccnl(self) -> None:
        run_id = "20260515_TEST_dailyA"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 10, "ticker": "AAPL",
                 "qty": 2, "broker_order_id": "0000099999"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 10, "Ticker": "AAPL",
                 "Action": "BUY_MORE", "Shares": 2, "Price": 200.0,
                 "Score": 90, "Regime": "RISK_ON", "Rank": 1},
            ],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, out, err, adapter, _, _ = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="99999", ticker="AAPL",
                                 ord_qty=2, filled_qty=2,
                                 filled_price=199.5)],
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("fully_filled", out)
        self.assertIn("199.5", out)


# ──────────────────────────────────────────────────────────────────────
# 2. Dry-run does NOT mutate disk
# ──────────────────────────────────────────────────────────────────────
class TestDryRunImmutability(_BaseFixture):
    def test_dry_run_writes_preview_and_reports_only(self) -> None:
        run_id = "20260515_TEST_dailyB"
        run_dir = _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 11, "ticker": "MSFT",
                 "qty": 1, "broker_order_id": "0000088888"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 11, "Ticker": "MSFT",
                 "Action": "BUY_MORE", "Shares": 1, "Price": 400.0,
                 "Score": 88, "Regime": "RISK_ON", "Rank": 2},
            ],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        # snapshot meta + applied state
        meta_before = (run_dir / "run_meta.json").read_text()
        self.assertFalse((run_dir / "execution_applied.csv").exists())

        args = _make_args(run_id=run_id, dry_run=True)
        recorder = _FakeArtifactRecorder()
        rc, _, err, _, hm, _ = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="88888", ticker="MSFT",
                                 ord_qty=1, filled_qty=1,
                                 filled_price=405.0)],
            recorder=recorder,
        )
        self.assertEqual(rc, 0, msg=err)
        # Reports written
        self.assertTrue((run_dir / ta.PREVIEW_CSV).exists())
        self.assertTrue((run_dir / ta.REPORT_MD).exists())
        self.assertTrue((run_dir / ta.REPORT_JSON).exists())
        # No mutation
        self.assertFalse((run_dir / "execution_applied.csv").exists())
        self.assertEqual((run_dir / "run_meta.json").read_text(), meta_before)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(hm.cash_events, [])
        self.assertEqual(recorder.calls, [])


# ──────────────────────────────────────────────────────────────────────
# 3. --apply requires AUTOTRADE_T10_APPLY_OK=true
# ──────────────────────────────────────────────────────────────────────
class TestApplyRequiresEnvGate(_BaseFixture):
    def test_apply_without_gate_aborts(self) -> None:
        run_id = "20260515_TEST_dailyC"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 20, "ticker": "X",
                                "qty": 1, "broker_order_id": "0000077777"}],
            recos=[{"RunId": run_id, "RecRowId": 20, "Ticker": "X",
                    "Action": "BUY_MORE", "Shares": 1, "Price": 10.0,
                    "Score": 50, "Regime": "SIDE", "Rank": 99}],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        # Ensure the env var is cleared for this test.
        prior = os.environ.pop(ta.APPLY_ENV_GATE, None)
        try:
            args = _make_args(run_id=run_id, dry_run=False, apply=True)
            rc, _, err, _, hm, recorder = _capture_run(
                args,
                ccnl_rows=[_ccnl_row(odno="77777", ticker="X",
                                     ord_qty=1, filled_qty=1,
                                     filled_price=10.5)],
            )
            self.assertEqual(rc, 2)
            self.assertIn(ta.APPLY_ENV_GATE, err)
            self.assertEqual(hm.apply_calls, [])
            self.assertEqual(recorder.calls, [])
        finally:
            if prior is not None:
                os.environ[ta.APPLY_ENV_GATE] = prior


# ──────────────────────────────────────────────────────────────────────
# 4. Duplicate RecRowId aborts
# ──────────────────────────────────────────────────────────────────────
class TestDuplicateApplyAborts(_BaseFixture):
    def test_duplicate_rec_row_id_aborts(self) -> None:
        run_id = "20260515_TEST_dailyD"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 30, "ticker": "Y",
                                "qty": 5, "broker_order_id": "0000066666"}],
            recos=[{"RunId": run_id, "RecRowId": 30, "Ticker": "Y",
                    "Action": "BUY_MORE", "Shares": 5, "Price": 1.0,
                    "Score": 1, "Regime": "SIDE", "Rank": 1}],
            existing_applied=[{
                "RunId": run_id, "RecRowId": 30, "Ticker": "Y",
                "Action": "BUY_MORE", "ExecutedPrice": 1.0,
                "ExecutedShares": 5,
            }],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, _, _, _, hm, recorder = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="66666", ticker="Y",
                                 ord_qty=5, filled_qty=5,
                                 filled_price=1.0)],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(recorder.calls, [])


# ──────────────────────────────────────────────────────────────────────
# 5. Missing ccnl row aborts
# ──────────────────────────────────────────────────────────────────────
class TestMissingCcnlAborts(_BaseFixture):
    def test_missing_ccnl_aborts_batch(self) -> None:
        run_id = "20260515_TEST_dailyE"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 40, "ticker": "GOOD",
                 "qty": 1, "broker_order_id": "0000055555"},
                {"rec_row_id": 41, "ticker": "MISS",
                 "qty": 1, "broker_order_id": "0000044444"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 40, "Ticker": "GOOD",
                 "Action": "BUY_MORE", "Shares": 1, "Price": 5.0,
                 "Score": 50, "Regime": "SIDE", "Rank": 1},
                {"RunId": run_id, "RecRowId": 41, "Ticker": "MISS",
                 "Action": "BUY_MORE", "Shares": 1, "Price": 7.0,
                 "Score": 40, "Regime": "SIDE", "Rank": 2},
            ],
        )
        ta._count_checkable = lambda _r: 2  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, _, _, _, hm, recorder = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="55555", ticker="GOOD",
                                 ord_qty=1, filled_qty=1,
                                 filled_price=5.0)],
            # 0000044444 deliberately absent.
        )
        self.assertEqual(rc, 1)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(recorder.calls, [])


# ──────────────────────────────────────────────────────────────────────
# 6. filled_qty=0 aborts
# ──────────────────────────────────────────────────────────────────────
class TestZeroFillAborts(_BaseFixture):
    def test_ccnl_zero_fill_is_benign_skip(self) -> None:
        """V1-H — a lone zero-fill (order placed, broker-confirmed 0
        filled, e.g. a reprice-ceiling cancel) is no longer a batch
        ABORT (rc=1). It is a clean no-op (rc=0): nothing to apply,
        nothing mutated, but NOT an operator-action error. Pre-V1-H
        this returned rc=1 which hard-stopped the unattended fire and
        raised a false 'Operator action required' (5/29 HPE)."""
        run_id = "20260515_TEST_dailyF"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 50, "ticker": "Z",
                                "qty": 2, "broker_order_id": "0000033333"}],
            recos=[{"RunId": run_id, "RecRowId": 50, "Ticker": "Z",
                    "Action": "BUY_MORE", "Shares": 2, "Price": 12.0,
                    "Score": 75, "Regime": "SIDE", "Rank": 5}],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, out, _, _, hm, recorder = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="33333", ticker="Z",
                                 ord_qty=2, filled_qty=0,
                                 filled_price=0.0)],
        )
        self.assertEqual(rc, 0)
        self.assertIn("clean no-op", out)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(recorder.calls, [])


# ──────────────────────────────────────────────────────────────────────
# 7. Partial fill aborts by default
# ──────────────────────────────────────────────────────────────────────
class TestPartialFillAborts(_BaseFixture):
    def test_partial_fill_aborts_by_default(self) -> None:
        run_id = "20260515_TEST_dailyG"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 60, "ticker": "P",
                                "qty": 5, "broker_order_id": "0000022222"}],
            recos=[{"RunId": run_id, "RecRowId": 60, "Ticker": "P",
                    "Action": "BUY_MORE", "Shares": 5, "Price": 50.0,
                    "Score": 60, "Regime": "SIDE", "Rank": 1}],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, _, _, _, hm, recorder = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="22222", ticker="P",
                                 ord_qty=5, filled_qty=2,
                                 filled_price=50.5)],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(recorder.calls, [])

    def test_partial_fill_applies_with_allow_partial_flag(self) -> None:
        run_id = "20260515_TEST_dailyG2"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 61, "ticker": "P2",
                                "qty": 5, "broker_order_id": "0000022223"}],
            recos=[{"RunId": run_id, "RecRowId": 61, "Ticker": "P2",
                    "Action": "BUY_MORE", "Shares": 5, "Price": 50.0,
                    "Score": 60, "Regime": "SIDE", "Rank": 1}],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True, allow_partial=True)
        rc, out, err, _, _, _ = _capture_run(
            args,
            ccnl_rows=[_ccnl_row(odno="22223", ticker="P2",
                                 ord_qty=5, filled_qty=2,
                                 filled_price=50.5)],
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("partially_filled", out)
        self.assertIn("would apply", out)


# ──────────────────────────────────────────────────────────────────────
# V1-H — per-ticker resilience: a zero-fill sibling must NOT abort the
# batch; the ticker that DID fill still applies.
# ──────────────────────────────────────────────────────────────────────
class TestPartialBatchResilience(_BaseFixture):
    def test_one_filled_one_zero_fill_applies_only_the_fill(self) -> None:
        """Reproduces the fixed 5/29 shape: ticker A gaps away and
        cannot fill (zero), ticker B fills cleanly. Pre-V1-H the
        zero-fill 'blocked' the whole batch (rc=1, NOTHING applied,
        state drift). Post-V1-H A is a benign skip and B applies
        normally (rc=0)."""
        run_id = "20260515_TEST_dailyMIX"
        run_dir = _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 90, "ticker": "MISS",
                 "qty": 2, "broker_order_id": "0000044440"},
                {"rec_row_id": 91, "ticker": "FILL",
                 "qty": 3, "broker_order_id": "0000044441"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 90, "Ticker": "MISS",
                 "Action": "BUY_NEW", "Shares": 2, "Price": 40.0,
                 "Score": 70, "Regime": "SIDE", "Rank": 1},
                {"RunId": run_id, "RecRowId": 91, "Ticker": "FILL",
                 "Action": "BUY_MORE", "Shares": 3, "Price": 25.0,
                 "Score": 80, "Regime": "RISK_ON", "Rank": 2},
            ],
        )
        ta._count_checkable = lambda _r: 2  # type: ignore[assignment]
        os.environ[ta.APPLY_ENV_GATE] = "true"
        try:
            args = _make_args(run_id=run_id, dry_run=False, apply=True)
            rc, out, err, _, hm, recorder = _capture_run(
                args,
                ccnl_rows=[
                    _ccnl_row(odno="44440", ticker="MISS",
                              ord_qty=2, filled_qty=0, filled_price=0.0),
                    _ccnl_row(odno="44441", ticker="FILL",
                              ord_qty=3, filled_qty=3, filled_price=24.9),
                ],
            )
            self.assertEqual(rc, 0, msg=err)
            # Only FILL applied; MISS skipped.
            self.assertEqual(len(hm.apply_calls), 1)
            applied = hm.apply_calls[0]
            self.assertEqual(list(applied["Ticker"]), ["FILL"])
            self.assertEqual(int(applied["Shares"].iloc[0]), 3)
            self.assertEqual(len(recorder.calls), 1)
            self.assertEqual(recorder.calls[0]["executed_rows"], 1)
        finally:
            os.environ.pop(ta.APPLY_ENV_GATE, None)

    def test_one_filled_one_partial_still_aborts_whole_batch(self) -> None:
        """Guardrail: a PARTIAL fill (real shares changed hands) is NOT
        a benign skip — it still conservatively aborts the whole batch
        without --allow-partial, so we never silently drop a partial."""
        run_id = "20260515_TEST_dailyMIX2"
        _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 92, "ticker": "PART",
                 "qty": 5, "broker_order_id": "0000044450"},
                {"rec_row_id": 93, "ticker": "FILL2",
                 "qty": 3, "broker_order_id": "0000044451"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 92, "Ticker": "PART",
                 "Action": "BUY_NEW", "Shares": 5, "Price": 40.0,
                 "Score": 70, "Regime": "SIDE", "Rank": 1},
                {"RunId": run_id, "RecRowId": 93, "Ticker": "FILL2",
                 "Action": "BUY_MORE", "Shares": 3, "Price": 25.0,
                 "Score": 80, "Regime": "RISK_ON", "Rank": 2},
            ],
        )
        ta._count_checkable = lambda _r: 2  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        rc, out, _, _, hm, recorder = _capture_run(
            args,
            ccnl_rows=[
                _ccnl_row(odno="44450", ticker="PART",
                          ord_qty=5, filled_qty=2, filled_price=40.1),
                _ccnl_row(odno="44451", ticker="FILL2",
                          ord_qty=3, filled_qty=3, filled_price=24.9),
            ],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(hm.apply_calls, [])
        self.assertEqual(recorder.calls, [])


# ──────────────────────────────────────────────────────────────────────
# 8. Full fill apply mutates fake hm
# ──────────────────────────────────────────────────────────────────────
class TestFullFillApply(_BaseFixture):
    def test_apply_full_fill_uses_holdings_manager_and_recorder(self) -> None:
        run_id = "20260515_TEST_dailyH"
        run_dir = _write_run_dir(
            self.base, run_id,
            submitted_intents=[{"rec_row_id": 70, "ticker": "FULL",
                                "qty": 3, "broker_order_id": "0000011111"}],
            recos=[{"RunId": run_id, "RecRowId": 70, "Ticker": "FULL",
                    "Action": "BUY_MORE", "Shares": 3, "Price": 25.0,
                    "Score": 80, "Regime": "RISK_ON", "Rank": 1}],
        )
        ta._count_checkable = lambda _r: 1  # type: ignore[assignment]
        os.environ[ta.APPLY_ENV_GATE] = "true"
        try:
            args = _make_args(run_id=run_id, dry_run=False, apply=True)
            rc, out, err, _, hm, recorder = _capture_run(
                args,
                ccnl_rows=[_ccnl_row(odno="11111", ticker="FULL",
                                     ord_qty=3, filled_qty=3,
                                     filled_price=24.75)],
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(len(hm.apply_calls), 1)
            applied = hm.apply_calls[0]
            self.assertEqual(int(applied["Shares"].iloc[0]), 3)
            self.assertAlmostEqual(float(applied["Price"].iloc[0]), 24.75, places=4)
            self.assertEqual(len(hm.cash_events), 1)
            self.assertAlmostEqual(
                hm.cash_events[0]["amount"], -(3 * 24.75), places=2
            )
            self.assertEqual(len(recorder.calls), 1)
            call = recorder.calls[0]
            self.assertEqual(call["source"], ta.APPLY_SOURCE)
            self.assertEqual(call["executed_rows"], 1)
            self.assertEqual(call["total_checkable_count"], 1)
            # run_meta status flipped to executed.
            meta = json.loads((run_dir / "run_meta.json").read_text())
            self.assertEqual(meta["status"], "executed")
            self.assertIn("executed", out)
        finally:
            os.environ.pop(ta.APPLY_ENV_GATE, None)


# ──────────────────────────────────────────────────────────────────────
# 9. Mixed artifact (BUY + unhandled SELL) becomes partially_executed
# ──────────────────────────────────────────────────────────────────────
class TestMixedArtifactPartiallyExecuted(_BaseFixture):
    def test_unhandled_sell_keeps_artifact_partial(self) -> None:
        run_id = "20260515_TEST_dailyI"
        run_dir = _write_run_dir(
            self.base, run_id,
            submitted_intents=[
                {"rec_row_id": 80, "ticker": "BUYME",
                 "qty": 1, "broker_order_id": "0000000111"},
            ],
            recos=[
                {"RunId": run_id, "RecRowId": 80, "Ticker": "BUYME",
                 "Action": "BUY_MORE", "Shares": 1, "Price": 100.0,
                 "Score": 70, "Regime": "RISK_ON", "Rank": 1},
                {"RunId": run_id, "RecRowId": 81, "Ticker": "SELLME",
                 "Action": "SELL", "Shares": 2, "Price": 50.0,
                 "Score": 40, "Regime": "RISK_OFF", "Rank": 99},
            ],
        )
        # Pretend the workspace has 2 checkable rows (1 BUY + 1 SELL).
        ta._count_checkable = lambda _r: 2  # type: ignore[assignment]
        os.environ[ta.APPLY_ENV_GATE] = "true"
        try:
            args = _make_args(run_id=run_id, dry_run=False, apply=True)
            rc, out, err, _, _, recorder = _capture_run(
                args,
                ccnl_rows=[_ccnl_row(odno="111", ticker="BUYME",
                                     ord_qty=1, filled_qty=1,
                                     filled_price=99.5)],
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(len(recorder.calls), 1)
            self.assertEqual(recorder.calls[0]["total_checkable_count"], 2)
            meta = json.loads((run_dir / "run_meta.json").read_text())
            self.assertEqual(meta["status"], "partially_executed")
            self.assertIn("partially_executed", out)
        finally:
            os.environ.pop(ta.APPLY_ENV_GATE, None)


# ──────────────────────────────────────────────────────────────────────
# R10B regression — autotrade_orders.jsonl missing in a fresh artifact
# ──────────────────────────────────────────────────────────────────────
class TestMissingAutotradeOrdersJsonl(_BaseFixture):
    """``daily_runner --dry-run`` on a never-submitted artifact reaches
    the T10 step before any ``autotrade_orders.jsonl`` exists. R10B saw
    ``cmd_apply`` propagate ``FileNotFoundError`` up into
    ``run_daily``'s catch-all, which surfaced as rc=2
    ``run_daily.exception`` instead of the intended "nothing to do".

    This locks the graceful behaviour: the file's absence is an
    informational rc=2 with a clear message and zero raised exceptions.
    """

    def test_missing_jsonl_returns_rc2_without_raising(self) -> None:
        run_id = "20260517_TEST_no_submit"
        run_dir = self.base / "daily_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_meta.json").write_text(json.dumps({
            "schema_version": "artifact/v1",
            "run_id": run_id,
            "status": "awaiting_execution",
        }))
        ta._count_checkable = lambda _r: 0  # type: ignore[assignment]
        args = _make_args(run_id=run_id, dry_run=True)
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = ta.cmd_apply(args)
        self.assertEqual(rc, 2)
        self.assertIn("autotrade_orders.jsonl", err_buf.getvalue())
        self.assertIn("no submissions to apply", err_buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
