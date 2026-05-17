"""R8-E — `t10_applicator` idempotency / recovery acceptance tests.

Covers R8 §7 acceptance list:

  - started marker blocks rerun
  - applied marker blocks rerun
  - crash after holdings mutation but before artifact marker enters recovery
  - History/CashLedger duplicate blocks rerun even if execution_applied.csv missing
  - normal apply still writes the same R7 artifact outputs

Plus pure-unit tests for ``compute_apply_batch_id`` and ``read_journal``.
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

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import t10_applicator as ta
from phase3.autotrade import t10_apply_journal as tj


# ──────────────────────────────────────────────────────────────────────
# Pure-unit: apply_batch_id + journal IO
# ──────────────────────────────────────────────────────────────────────
class TestApplyBatchIdDeterminism(unittest.TestCase):
    def test_same_inputs_same_id(self) -> None:
        a = tj.compute_apply_batch_id("RID1", [
            {"rec_row_id": 1, "broker_order_id": "0000099999",
             "ticker": "APA", "filled_qty": 4, "filled_price": 37.815},
            {"rec_row_id": 2, "broker_order_id": "0000099998",
             "ticker": "AMD", "filled_qty": 1, "filled_price": 430.795},
        ])
        b = tj.compute_apply_batch_id("RID1", [
            {"rec_row_id": 2, "broker_order_id": "0000099998",
             "ticker": "AMD", "filled_qty": 1, "filled_price": 430.795},
            {"rec_row_id": 1, "broker_order_id": "0000099999",
             "ticker": "APA", "filled_qty": 4, "filled_price": 37.815},
        ])
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("apply-"))

    def test_different_run_id_yields_different_id(self) -> None:
        items = [{"rec_row_id": 1, "broker_order_id": "X",
                  "ticker": "APA", "filled_qty": 1, "filled_price": 1.0}]
        self.assertNotEqual(
            tj.compute_apply_batch_id("RID1", items),
            tj.compute_apply_batch_id("RID2", items),
        )

    def test_different_price_yields_different_id(self) -> None:
        a = tj.compute_apply_batch_id("RID1", [
            {"rec_row_id": 1, "broker_order_id": "X",
             "ticker": "APA", "filled_qty": 1, "filled_price": 1.0},
        ])
        b = tj.compute_apply_batch_id("RID1", [
            {"rec_row_id": 1, "broker_order_id": "X",
             "ticker": "APA", "filled_qty": 1, "filled_price": 1.000001},
        ])
        self.assertNotEqual(a, b)


class TestJournalIO(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "daily_runs" / "RID"
        self.run_dir.mkdir(parents=True)

    def test_read_empty_journal(self) -> None:
        self.assertEqual(tj.read_journal(self.run_dir), [])
        self.assertIsNone(tj.latest_status_for_batch(self.run_dir, "apply-x"))

    def test_write_then_read(self) -> None:
        tj.write_marker(self.run_dir, batch_id="apply-x", status="started", run_id="RID")
        tj.write_marker(self.run_dir, batch_id="apply-x", status="applied", run_id="RID")
        rows = tj.read_journal(self.run_dir)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["status"] for r in rows], ["started", "applied"])
        self.assertEqual(tj.latest_status_for_batch(self.run_dir, "apply-x"), "applied")

    def test_inspect_batch_filters_by_id(self) -> None:
        tj.write_marker(self.run_dir, batch_id="A", status="started", run_id="RID")
        tj.write_marker(self.run_dir, batch_id="B", status="started", run_id="RID")
        tj.write_marker(self.run_dir, batch_id="A", status="applied", run_id="RID")
        a = tj.inspect_batch(self.run_dir, "A")
        b = tj.inspect_batch(self.run_dir, "B")
        self.assertEqual(a.prior_status, "applied")
        self.assertEqual(b.prior_status, "started")
        self.assertIsNotNone(a.started_at)
        self.assertIsNotNone(a.applied_at)
        self.assertIsNone(b.applied_at)


class TestLocalDuplicateDetect(unittest.TestCase):
    def test_same_day_same_price_shares_is_duplicate(self) -> None:
        ex = pd.DataFrame([
            {"Ticker": "APA", "Action": "BUY_MORE",
             "ExecutedPrice": 37.815, "ExecutedShares": 4},
        ])
        hist = pd.DataFrame([
            {"Date": "2026-05-15", "Ticker": "APA", "Action": "BUY_MORE",
             "Price": 37.815, "Shares": 4, "Value": 151.26,
             "Trigger": "AUTOTRADE", "Notes": ""},
        ])
        dupes = tj.local_duplicate_present(
            executed_df=ex, history_df=hist, today_str="2026-05-15",
        )
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["Ticker"], "APA")

    def test_different_day_is_not_duplicate(self) -> None:
        ex = pd.DataFrame([
            {"Ticker": "APA", "Action": "BUY",
             "ExecutedPrice": 37.815, "ExecutedShares": 4},
        ])
        hist = pd.DataFrame([
            {"Date": "2026-05-14", "Ticker": "APA", "Action": "BUY",
             "Price": 37.815, "Shares": 4, "Value": 151.26,
             "Trigger": "AUTOTRADE", "Notes": ""},
        ])
        dupes = tj.local_duplicate_present(
            executed_df=ex, history_df=hist, today_str="2026-05-15",
        )
        self.assertEqual(dupes, [])

    def test_different_shares_is_not_duplicate(self) -> None:
        ex = pd.DataFrame([
            {"Ticker": "APA", "Action": "BUY",
             "ExecutedPrice": 37.815, "ExecutedShares": 4},
        ])
        hist = pd.DataFrame([
            {"Date": "2026-05-15", "Ticker": "APA", "Action": "BUY",
             "Price": 37.815, "Shares": 5, "Value": 189.075,
             "Trigger": "AUTOTRADE", "Notes": ""},
        ])
        dupes = tj.local_duplicate_present(
            executed_df=ex, history_df=hist, today_str="2026-05-15",
        )
        self.assertEqual(dupes, [])


# ──────────────────────────────────────────────────────────────────────
# Integration with cmd_apply (mirrors R7-A fixtures)
# ──────────────────────────────────────────────────────────────────────
def _make_args(*, run_id: str, dry_run: bool = False, apply: bool = True,
               allow_partial: bool = False,
               allow_duplicate_apply: bool = False,
               allow_recovery_apply: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=run_id,
        profile="paper",
        dry_run=dry_run,
        apply=apply,
        allow_partial=allow_partial,
        allow_duplicate_apply=allow_duplicate_apply,
        allow_recovery_apply=allow_recovery_apply,
    )


def _write_run_dir(base: Path, run_id: str, *,
                   submitted_intents: List[Dict[str, Any]],
                   recos: List[Dict[str, Any]]) -> Path:
    run_dir = base / "daily_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"schema_version": "artifact/v1", "run_id": run_id,
            "status": "awaiting_execution"}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
    pd.DataFrame(recos).to_csv(run_dir / "recommendations.csv", index=False)
    with (run_dir / "autotrade_orders.jsonl").open("w") as fh:
        for i, raw in enumerate(submitted_intents):
            ev = {
                "event_kind": "transition", "autotrade_run_id": "at-FAKE",
                "mode": "paper_submit", "run_id": run_id,
                "rec_row_id": int(raw["rec_row_id"]),
                "ticker": raw["ticker"], "side": "BUY",
                "qty_intended": int(raw["qty"]), "qty_filled": 0.0,
                "qty_remaining": int(raw["qty"]),
                "limit_price": float(raw.get("limit", 100.0)),
                "client_order_id": f"co-FAKE-{raw['rec_row_id']}-B-{raw['qty']}-aaaaaa",
                "broker_order_id": raw["broker_order_id"],
                "state": "submitted", "status_source": "place_order_ack",
                "raw_broker_row": {}, "echo": None,
                "fill_price": None, "fill_price_source": None,
                "error": None, "note": None,
                "schema_version": "autotrade_order_event/v1",
                "event_id": f"ev-FAKE-{i:04d}",
                "event_ts": f"2026-05-15T00:00:0{i}.000+00:00",
            }
            fh.write(json.dumps(ev) + "\n")
    return run_dir


def _ccnl_row(*, odno, ticker, ord_qty, filled_qty, filled_price):
    return {
        "odno": odno, "pdno": ticker, "sll_buy_dvsn_cd": "02",
        "ft_ord_qty3": str(ord_qty), "ft_ccld_qty3": str(filled_qty),
        "ft_ord_unpr3": f"{filled_price:.6f}",
        "ft_ccld_unpr3": f"{filled_price:.6f}" if filled_qty > 0 else "",
        "dmst_ord_dt": "20260515", "ord_tmd": "224700",
    }


class _FakeAdapter:
    def __init__(self, ccnl_rows): self.ccnl_rows = list(ccnl_rows)
    def get_order_history(self): return list(self.ccnl_rows)


class _FakeHM:
    def __init__(self, *, history_df=None):
        self.apply_calls: List[pd.DataFrame] = []
        self.cash_events: List[Dict[str, Any]] = []
        self.history = history_df if history_df is not None else pd.DataFrame(
            columns=["Date", "Ticker", "Action", "Price", "Shares", "Value", "Trigger", "Notes"])
        self.current = pd.DataFrame(columns=["Ticker", "Shares", "MarketValue"])
        self._cash = 100_000.0
    def apply_partial_execution(self, df, *, trigger_type): self.apply_calls.append(df.copy())
    def record_cash_event(self, event_type, amount, notes=""):
        self.cash_events.append({"type": event_type, "amount": float(amount), "notes": notes})
        self._cash += float(amount)
    def load_current(self): return self.current.copy()
    def load_history(self): return self.history.copy()
    def get_cash_balance(self): return float(self._cash)
    def get_portfolio_value(self): return 0.0


class _FakeArtifactRecorder:
    def __init__(self, *, raise_with: Exception | None = None):
        self.calls = []
        self.raise_with = raise_with
    def __call__(self, run_dir, executed_df, *, source, total_checkable_count,
                 portfolio_after_execution_df, cash_balance, total_capital,
                 operator_note=""):
        self.calls.append({"run_dir": Path(run_dir),
                            "executed_rows": int(len(executed_df))})
        if self.raise_with is not None:
            raise self.raise_with
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
        return {"execution_status": status,
                "executed_row_count_total": executed_total}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Holdings log path is referenced for backup, so it must exist.
        self.holdings_log = self.base / "holdings_log.xlsx"
        self.holdings_log.write_bytes(b"\x00\x00fakebytes\x00")  # not a real xlsx but copy2 works
        self._orig_resolve = ta._resolve_paths
        ta._resolve_paths = lambda profile: (  # type: ignore[assignment]
            self.base, self.holdings_log, self.base / "fake_config.yaml",
        )
        self.addCleanup(lambda: setattr(ta, "_resolve_paths", self._orig_resolve))
        self._orig_count = ta._count_checkable
        self.addCleanup(lambda: setattr(ta, "_count_checkable", self._orig_count))
        os.environ[ta.APPLY_ENV_GATE] = "true"
        self.addCleanup(lambda: os.environ.pop(ta.APPLY_ENV_GATE, None))

    def _run(self, args, *, ccnl_rows, hm=None, recorder=None):
        adapter = _FakeAdapter(ccnl_rows)
        hm = hm or _FakeHM()
        recorder = recorder or _FakeArtifactRecorder()
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = ta.cmd_apply(
                args,
                make_adapter=lambda *, paper_only=True: adapter,
                make_hm=lambda _: hm,
                record_artifact=recorder,
            )
        return rc, out_buf.getvalue(), err_buf.getvalue(), adapter, hm, recorder


class TestNormalApplyWritesAppliedMarker(_Base):
    def test_first_apply_writes_started_then_applied(self) -> None:
        run_id = "20260515_R8E_normal"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        rc, out, err, _, hm, recorder = self._run(
            _make_args(run_id=run_id),
            ccnl_rows=[_ccnl_row(odno="99999", ticker="APA",
                                  ord_qty=4, filled_qty=4, filled_price=37.815)],
        )
        self.assertEqual(rc, 0, msg=f"err={err}\nout={out}")
        # Journal must contain started + applied.
        rows = tj.read_journal(run_dir)
        statuses = [r["status"] for r in rows]
        self.assertIn("started", statuses)
        self.assertIn("applied", statuses)
        self.assertEqual(statuses[-1], "applied")
        # Mutation actually happened.
        self.assertEqual(len(hm.apply_calls), 1)
        self.assertEqual(len(recorder.calls), 1)
        # Backup was created.
        backups = list(run_dir.glob("holdings_log.preapply.*.xlsx"))
        self.assertEqual(len(backups), 1)


class TestAppliedMarkerBlocksRerun(_Base):
    def test_second_apply_with_same_batch_aborts(self) -> None:
        run_id = "20260515_R8E_doubleapply"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        # Run #1 — normal apply.
        ccnl = [_ccnl_row(odno="99999", ticker="APA", ord_qty=4,
                          filled_qty=4, filled_price=37.815)]
        rc1, _, _, _, hm1, recorder1 = self._run(
            _make_args(run_id=run_id), ccnl_rows=ccnl,
        )
        self.assertEqual(rc1, 0)
        # Need a fresh recos -> existing_applied loop. The recorder writes
        # execution_applied.csv on run #1, so on run #2 the existing
        # applied detection would also fire — but the R8-E journal must
        # short-circuit FIRST with rc=2.
        # Also clear the previously-written execution_applied.csv so we
        # can prove the journal is what's blocking.
        (run_dir / "execution_applied.csv").unlink(missing_ok=True)
        meta = json.loads((run_dir / "run_meta.json").read_text())
        meta["status"] = "awaiting_execution"
        (run_dir / "run_meta.json").write_text(json.dumps(meta))

        rc2, out2, err2, _, hm2, recorder2 = self._run(
            _make_args(run_id=run_id), ccnl_rows=ccnl,
        )
        self.assertEqual(rc2, 2, msg=f"err={err2}\nout={out2}")
        self.assertIn("already marked applied", err2)
        # Run #2 must not mutate or record again.
        self.assertEqual(len(hm2.apply_calls), 0)
        self.assertEqual(len(recorder2.calls), 0)


class TestStartedMarkerBlocksRerunUnlessRecovery(_Base):
    def test_started_without_applied_enters_recovery_returns_rc3(self) -> None:
        run_id = "20260515_R8E_recovery"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        ccnl = [_ccnl_row(odno="99999", ticker="APA", ord_qty=4,
                          filled_qty=4, filled_price=37.815)]
        # Run #1 — recorder raises so we land at "started" but never
        # reach the "applied" marker.
        recorder_fail = _FakeArtifactRecorder(
            raise_with=RuntimeError("disk full"),
        )
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                ta.cmd_apply(
                    _make_args(run_id=run_id),
                    make_adapter=lambda *, paper_only=True: _FakeAdapter(ccnl),
                    make_hm=lambda _: _FakeHM(),
                    record_artifact=recorder_fail,
                )
        except RuntimeError:
            pass  # expected — simulates a crash mid-apply

        statuses = [r["status"] for r in tj.read_journal(run_dir)]
        self.assertIn("started", statuses)
        self.assertNotIn("applied", statuses)

        # Run #2 — same intent, no override flag → recovery mode rc=3.
        rc2, out2, err2, _, hm2, recorder2 = self._run(
            _make_args(run_id=run_id), ccnl_rows=ccnl,
        )
        self.assertEqual(rc2, 3, msg=f"err={err2}\nout={out2}")
        self.assertIn("recovery mode", err2)
        self.assertEqual(len(hm2.apply_calls), 0)
        statuses2 = [r["status"] for r in tj.read_journal(run_dir)]
        self.assertIn("recovery", statuses2)

    def test_recovery_override_proceeds_and_writes_applied(self) -> None:
        run_id = "20260515_R8E_recovery_override"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        ccnl = [_ccnl_row(odno="99999", ticker="APA", ord_qty=4,
                          filled_qty=4, filled_price=37.815)]
        # Pre-stage: write a 'started' marker manually using the same
        # batch id the applicator would compute.
        from phase3.autotrade.t10_applicator import _resolve_against_ccnl, _apply_policy
        # Reproduce policy.applicable just enough to compute batch_id
        # the same way cmd_apply will. Easiest path is to invoke a normal
        # run-1 and capture the batch_id from the journal.
        recorder_fail = _FakeArtifactRecorder(raise_with=RuntimeError("simulated crash"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                ta.cmd_apply(
                    _make_args(run_id=run_id),
                    make_adapter=lambda *, paper_only=True: _FakeAdapter(ccnl),
                    make_hm=lambda _: _FakeHM(),
                    record_artifact=recorder_fail,
                )
            except RuntimeError:
                pass
        # Now run #2 with --allow-recovery-apply. Should proceed and
        # finally write the applied marker.
        rc2, out2, err2, _, hm2, recorder2 = self._run(
            _make_args(run_id=run_id, allow_recovery_apply=True),
            ccnl_rows=ccnl,
        )
        self.assertEqual(rc2, 0, msg=f"err={err2}\nout={out2}")
        statuses = [r["status"] for r in tj.read_journal(run_dir)]
        # We expect: started (crash) → started (override re-attempt) →
        # applied. Recovery marker should NOT be auto-written here
        # because we passed --allow-recovery-apply.
        self.assertEqual(statuses.count("applied"), 1)
        self.assertEqual(len(hm2.apply_calls), 1)


class TestLocalHistoryDuplicateBlocks(_Base):
    def test_existing_history_blocks_without_override(self) -> None:
        run_id = "20260515_R8E_localdup"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        # Pre-populate fake history with a matching same-day row.
        hist = pd.DataFrame([{
            "Date": today, "Ticker": "APA", "Action": "BUY_MORE",
            "Price": 37.815, "Shares": 4, "Value": 151.26,
            "Trigger": "T10_MANUAL", "Notes": "",
        }])
        fake_hm = _FakeHM(history_df=hist)
        rc, out, err, _, hm, recorder = self._run(
            _make_args(run_id=run_id), hm=fake_hm,
            ccnl_rows=[_ccnl_row(odno="99999", ticker="APA",
                                  ord_qty=4, filled_qty=4, filled_price=37.815)],
        )
        self.assertEqual(rc, 2, msg=f"err={err}\nout={out}")
        self.assertIn("local-Excel duplicate", err)
        # Apply was refused; recorder should not have been called.
        self.assertEqual(len(recorder.calls), 0)
        self.assertEqual(len(hm.apply_calls), 0)
        # Journal must end with aborted marker.
        statuses = [r["status"] for r in tj.read_journal(run_dir)]
        self.assertIn("aborted", statuses)

    def test_override_allows_apply(self) -> None:
        run_id = "20260515_R8E_localdup_override"
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        hist = pd.DataFrame([{
            "Date": today, "Ticker": "APA", "Action": "BUY_MORE",
            "Price": 37.815, "Shares": 4, "Value": 151.26,
            "Trigger": "T10_MANUAL", "Notes": "",
        }])
        fake_hm = _FakeHM(history_df=hist)
        rc, out, err, _, hm, recorder = self._run(
            _make_args(run_id=run_id, allow_duplicate_apply=True),
            hm=fake_hm,
            ccnl_rows=[_ccnl_row(odno="99999", ticker="APA",
                                  ord_qty=4, filled_qty=4, filled_price=37.815)],
        )
        self.assertEqual(rc, 0, msg=f"err={err}\nout={out}")
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(len(hm.apply_calls), 1)


# ──────────────────────────────────────────────────────────────────────
# R9-A2 — recovery marker re-block
# ──────────────────────────────────────────────────────────────────────
class TestRecoveryMarkerReBlock(_Base):
    """R9 §4: a prior 'recovery' marker must keep blocking the next
    apply attempt for the same apply_batch_id until the operator
    explicitly passes --allow-recovery-apply. The R8-E implementation
    only checked 'started', so a single recovery marker silently
    allowed the very next run to proceed."""

    def _stage_recovery(self, run_id: str):
        """Run the applicator once with a recorder that crashes mid-
        apply (writes 'started' but not 'applied'), then run it again
        with NO override so it lands on the 'recovery' marker path.
        Returns the run_dir."""
        run_dir = _write_run_dir(self.base, run_id,
            submitted_intents=[{"rec_row_id": 10, "ticker": "APA", "qty": 4,
                                 "broker_order_id": "0000099999"}],
            recos=[{"RunId": run_id, "RecRowId": 10, "Ticker": "APA",
                    "Action": "BUY_MORE", "Shares": 4, "Price": 37.82,
                    "Score": 90, "Regime": "RISK_ON", "Rank": 1}])
        ta._count_checkable = lambda _r: 1
        ccnl = [_ccnl_row(odno="99999", ticker="APA", ord_qty=4,
                          filled_qty=4, filled_price=37.815)]
        recorder_fail = _FakeArtifactRecorder(raise_with=RuntimeError("simulated crash"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                ta.cmd_apply(
                    _make_args(run_id=run_id),
                    make_adapter=lambda *, paper_only=True: _FakeAdapter(ccnl),
                    make_hm=lambda _: _FakeHM(),
                    record_artifact=recorder_fail,
                )
            except RuntimeError:
                pass
        # 1st rerun without override → writes the 'recovery' marker, returns rc=3.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = ta.cmd_apply(
                _make_args(run_id=run_id),
                make_adapter=lambda *, paper_only=True: _FakeAdapter(ccnl),
                make_hm=lambda _: _FakeHM(),
                record_artifact=_FakeArtifactRecorder(),
            )
        self.assertEqual(rc1, 3)
        # The journal should now end with a 'recovery' marker.
        statuses = [r["status"] for r in tj.read_journal(run_dir)]
        self.assertEqual(statuses[-1], "recovery")
        return run_dir, ccnl

    def test_prior_recovery_marker_blocks_without_override(self) -> None:
        """The second rerun (still no override) must STILL be blocked,
        even though prior_status is now 'recovery' rather than 'started'."""
        run_dir, ccnl = self._stage_recovery("20260516_R9A2_block")
        rc2, out2, err2, _, hm2, recorder2 = self._run(
            _make_args(run_id="20260516_R9A2_block"), ccnl_rows=ccnl,
        )
        self.assertEqual(rc2, 3, msg=f"got rc={rc2} err={err2}")
        self.assertIn("recovery", err2.lower())
        self.assertEqual(len(hm2.apply_calls), 0)
        self.assertEqual(len(recorder2.calls), 0)
        # A new 'recovery' marker should have been appended with the
        # 'prior_recovery_requires_operator' reason — the R9 advice.
        rows = tj.read_journal(run_dir)
        recovery_rows = [r for r in rows if r.get("status") == "recovery"]
        self.assertGreaterEqual(len(recovery_rows), 2,
                                 msg="second block should append another recovery marker")
        last = recovery_rows[-1]
        self.assertEqual(last.get("reason"), "prior_recovery_requires_operator")
        self.assertIn("advice", last)

    def test_prior_recovery_marker_allows_with_allow_recovery_apply(self) -> None:
        """The override must still let the apply proceed exactly once,
        producing an 'applied' marker and not regressing the R8-E
        happy-path behaviour."""
        run_dir, ccnl = self._stage_recovery("20260516_R9A2_override")
        rc, out, err, _, hm, recorder = self._run(
            _make_args(run_id="20260516_R9A2_override",
                        allow_recovery_apply=True),
            ccnl_rows=ccnl,
        )
        self.assertEqual(rc, 0, msg=f"got rc={rc} err={err}")
        statuses = [r["status"] for r in tj.read_journal(run_dir)]
        self.assertEqual(statuses.count("applied"), 1)
        self.assertEqual(len(hm.apply_calls), 1)
        self.assertEqual(len(recorder.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
