"""R10-1 / R10-1b — submitted_intents.json validation tests.

Two layers covered:

  - ``intents_io.validate_submitted_intents`` — file-level classifier
  - ``daily_runner.main(['--paper-submit', ...])`` — refuses to enter
    the manage loop when the file is missing / malformed / empty
    (R10 §3.3 "do not silently submit zero orders").
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import daily_runner as dr
from phase3.autotrade import intents_io


def _write_run_meta(base: Path, run_id: str, status: str = "awaiting_execution") -> Path:
    rd = base / "daily_runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "run_meta.json").write_text(json.dumps(
        {"schema_version": "artifact/v1", "run_id": run_id, "status": status},
        indent=2))
    return rd


def _good_row(symbol: str = "APA", qty: int = 1, limit: float = 18.85) -> dict:
    return {
        "client_order_id": f"co-20260516-test-B-{qty}-aaaa",
        "symbol": symbol,
        "market": "NASD",
        "side": "BUY",
        "qty": qty,
        "ord_type": "LIMIT",
        "limit_price": limit,
    }


class TestIntentFileValidator(unittest.TestCase):
    def test_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "missing")
            self.assertFalse(s.is_ok)
            self.assertIn("submitted_intents.json", s.reason)

    def test_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "submitted_intents.json").write_text("not-json{")
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("invalid JSON", s.reason)

    def test_malformed_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "submitted_intents.json").write_text(json.dumps({"foo": "bar"}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("expected an object with 'intents'", s.reason)

    def test_empty_intents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": []}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "empty")
            self.assertFalse(s.is_ok)

    def test_bare_list_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "submitted_intents.json").write_text(json.dumps([_good_row()]))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "ok")
            self.assertEqual(s.intent_count, 1)
            self.assertEqual(s.buy_count, 1)

    def test_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "submitted_intents.json").write_text(json.dumps({
                "intents": [_good_row("APA", 1, 18.85),
                             _good_row("KO", 2, 60.0)]
            }))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "ok")
            self.assertEqual(s.intent_count, 2)
            self.assertEqual(s.buy_count, 2)

    def test_sell_row_rejected_as_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            bad = _good_row(); bad["side"] = "SELL"
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": [bad]}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("BUY only", s.reason)

    def test_market_order_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            bad = _good_row(); bad["ord_type"] = "MARKET"
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": [bad]}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("LIMIT", s.reason)

    def test_zero_qty_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            bad = _good_row(); bad["qty"] = 0
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": [bad]}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("qty", s.reason)

    def test_missing_limit_price_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            bad = _good_row(); bad.pop("limit_price")
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": [bad]}))
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "malformed")
            self.assertIn("limit_price", s.reason)


class TestIntentFileWriter(unittest.TestCase):
    def test_make_buy_intent_row_validates(self) -> None:
        row = intents_io.make_buy_intent_row(
            client_order_id="co-test-1", symbol="APA",
            qty=1, limit_price=18.85,
        )
        self.assertEqual(row["side"], "BUY")
        self.assertEqual(row["ord_type"], "LIMIT")
        self.assertEqual(row["symbol"], "APA")

    def test_make_buy_intent_row_rejects_zero_qty(self) -> None:
        with self.assertRaises(ValueError):
            intents_io.make_buy_intent_row(
                client_order_id="x", symbol="APA", qty=0, limit_price=18.85,
            )

    def test_write_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            row = intents_io.make_buy_intent_row(
                client_order_id="co-rt-1", symbol="APA",
                qty=1, limit_price=18.85,
            )
            p = intents_io.write_submitted_intents(rd, [row], run_id="rt")
            self.assertTrue(p.exists())
            s = intents_io.validate_submitted_intents(rd)
            self.assertEqual(s.state, "ok")
            self.assertEqual(s.buy_count, 1)

    def test_write_refuses_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            row = intents_io.make_buy_intent_row(
                client_order_id="co-rt-1", symbol="APA",
                qty=1, limit_price=18.85,
            )
            intents_io.write_submitted_intents(rd, [row], run_id="rt")
            with self.assertRaises(FileExistsError):
                intents_io.write_submitted_intents(rd, [row], run_id="rt")
            intents_io.write_submitted_intents(rd, [row], run_id="rt", overwrite=True)


class TestDailyRunnerPaperSubmitGuard(unittest.TestCase):
    """``daily_runner.main(--paper-submit)`` must refuse to enter the
    manage loop when intents are missing / empty / malformed."""

    def _basic_paths(self, base: Path) -> dict:
        return {"config_path": base / "cfg.yaml",
                 "output_dir": base, "holdings_log": base / "hl.xlsx"}

    def _setup_env_for_submit(self):
        saved = {k: os.environ.get(k) for k in
                 (dr.SUBMIT_ENV_GATE, dr.CANCEL_ENV_GATE, dr.APPLY_ENV_GATE)}
        os.environ[dr.SUBMIT_ENV_GATE] = "true"
        os.environ[dr.CANCEL_ENV_GATE] = "true"
        os.environ.pop(dr.APPLY_ENV_GATE, None)
        return saved

    def _restore_env(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run_main(self, run_id: str, base: Path, factories):
        stdout, stderr = io.StringIO(), io.StringIO()
        saved = self._setup_env_for_submit()
        try:
            rc = dr.main(
                ["--run-id", run_id, "--paper-submit"],
                factories=factories,
                paths_resolver=lambda _p: self._basic_paths(base),
                stdout=stdout, stderr=stderr,
            )
        finally:
            self._restore_env(saved)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _fake_factories(self, *, intents_loader=None):
        """Factories that fail the test if anything but intents_loader
        is invoked — we want the runner to bail at the intent step."""
        called = {"manage": 0, "t10": 0}

        def _pre(_ctx):
            return dr.ReconcileSummary(
                qty_mismatch_count=0, local_only_count=0,
                broker_only_managed_count=0,
                cash_drift_usd=0.0, settlement_pending_usd=0.0,
            )

        def _manage(_ctx, _i):
            called["manage"] += 1
            return []

        def _t10(_ctx, _apply_mode):
            called["t10"] += 1
            return {"rc": 0}

        return dr.DefaultFactories(
            pre_reconcile_fn=_pre, post_reconcile_fn=_pre,
            intents_loader=(intents_loader
                             or dr.default_intents_loader),
            manage_loop_fn=_manage, t10_apply_fn=_t10,
        ), called

    def test_missing_intents_blocks_paper_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_run_meta(base, "20260516_R10_missing")
            factories, called = self._fake_factories()
            rc, out, err = self._run_main(
                "20260516_R10_missing", base, factories,
            )
            self.assertEqual(rc, 2, msg=f"got rc={rc} stderr={err}")
            self.assertIn("submitted_intents.json", err)
            self.assertEqual(called["manage"], 0)
            self.assertEqual(called["t10"], 0)

    def test_empty_intents_blocks_paper_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _write_run_meta(base, "20260516_R10_empty")
            (rd / "submitted_intents.json").write_text(json.dumps({"intents": []}))
            factories, called = self._fake_factories()
            rc, out, err = self._run_main(
                "20260516_R10_empty", base, factories,
            )
            self.assertEqual(rc, 2)
            self.assertIn("empty", err.lower())
            self.assertEqual(called["manage"], 0)

    def test_malformed_intents_blocks_paper_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rd = _write_run_meta(base, "20260516_R10_malformed")
            (rd / "submitted_intents.json").write_text("not-json{")
            factories, called = self._fake_factories()
            rc, out, err = self._run_main(
                "20260516_R10_malformed", base, factories,
            )
            self.assertEqual(rc, 2)
            self.assertIn("malformed", err.lower())
            self.assertEqual(called["manage"], 0)

    def test_dry_run_does_not_require_intents_file(self) -> None:
        """Dry-run is the only mode that's allowed to run without an
        intents file — the report just shows zero intents."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_run_meta(base, "20260516_R10_dry_no_intents")
            factories, called = self._fake_factories()
            stdout, stderr = io.StringIO(), io.StringIO()
            rc = dr.main(
                ["--run-id", "20260516_R10_dry_no_intents", "--dry-run"],
                factories=factories,
                paths_resolver=lambda _p: self._basic_paths(base),
                stdout=stdout, stderr=stderr,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(called["manage"], 0)  # dry-run skips manage


if __name__ == "__main__":
    unittest.main(verbosity=2)
