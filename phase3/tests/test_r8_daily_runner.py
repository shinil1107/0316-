"""R8-F / R8-G — daily_runner pipeline + daily report acceptance tests.

Covers R8 §8 hard stops and R8 §9 daily report shape:

  - happy path dry-run produces coherent plan + report
  - missing run_id → preflight stop
  - artifact status != awaiting_execution → preflight stop
  - prior recovery marker in journal → preflight stop (rc=3)
  - pre-reconcile qty_mismatch → pre_reconcile stop
  - UNKNOWN order outcome → manage_loop stop
  - stale OPEN/CANCEL_REQUESTED outcome → manage_loop stop
  - t10_apply rc=3 → t10_apply recovery stop
  - post-reconcile qty_mismatch → post_reconcile stop (rc=4)
  - happy path apply (paper_submit + apply_t10 + gates) writes both
    sections of the report (apply + post_reconcile)

Everything runs against in-memory fakes; no broker, no real reconcile,
no real t10 applicator.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import daily_runner as dr
from phase3.autotrade import t10_apply_journal as tj
from phase3.autotrade.kis_broker_adapter import OrderIntent
from phase3.autotrade.order_manager import ManagedOrderOutcome
from phase3.autotrade.order_state import OrderState
from phase3.autotrade.order_store import build_client_order_id


# ──────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────
def _make_ctx(*, base: Path, run_id: str, status: str = "awaiting_execution",
              **overrides) -> dr.DailyRunContext:
    output_dir = base
    run_dir = output_dir / "daily_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"schema_version": "artifact/v1", "run_id": run_id, "status": status}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
    kwargs = dict(
        run_id=run_id, profile="paper",
        dry_run=True, paper_submit=False, apply_t10=False,
        output_dir=output_dir,
        autotrade_run_id="at-fixture",
        started_at="2026-05-16T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return dr.DailyRunContext(**kwargs)


def _intent(symbol="APA", qty=1, limit=18.85, side="BUY", run_id="20260516",
             rec_row_id=1) -> OrderIntent:
    cid = build_client_order_id(run_id=run_id, rec_row_id=rec_row_id,
                                 side=side, qty=qty)
    return OrderIntent(
        symbol=symbol, market="NASD", side=side, qty=qty,
        order_type="LIMIT", limit_price=limit, client_order_id=cid,
    )


def _ok_summary(**fields) -> dr.ReconcileSummary:
    base = dict(
        qty_mismatch_count=0, local_only_count=0,
        broker_only_managed_count=0, cash_drift_usd=0.0,
        settlement_pending_usd=0.0, raw={},
    )
    base.update(fields)
    return dr.ReconcileSummary(**base)


def _outcome(*, state: OrderState, intent: OrderIntent = None,
             qty_filled=0.0, reprice_attempts=0, cancel_attempts=0,
             note="") -> ManagedOrderOutcome:
    intent = intent or _intent()
    return ManagedOrderOutcome(
        final_state=state, intent=intent,
        last_broker_order_id="0000099999", last_normalized_odno="99999",
        qty_filled=qty_filled, qty_remaining=float(intent.qty) - qty_filled,
        avg_fill_price=18.85 if qty_filled > 0 else None,
        last_limit_price=float(intent.limit_price),
        cancel_attempts=cancel_attempts, reprice_attempts=reprice_attempts,
        elapsed_sec=0.01, note=note,
    )


def _no_intents(_ctx): return []


def _ok_t10(_ctx, _apply): return {"rc": 0, "executed_rows": 0}


# ──────────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────────
class TestHappyPathDryRun(unittest.TestCase):
    def test_dry_run_no_intents_writes_clean_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_OK_dry")
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [],
                intents_loader=_no_intents,
                t10_apply_fn=_ok_t10,
            )
            self.assertEqual(result.rc, 0)
            self.assertIsNone(result.hard_stop)
            self.assertEqual(result.outcomes, [])
            rep = json.loads(Path(result.report_paths["json"]).read_text())
            self.assertEqual(rep["rc"], 0)
            self.assertIsNone(rep["operator_action_required"])
            self.assertEqual(rep["mode"], "dry_run")


class TestHappyPathFullApply(unittest.TestCase):
    def test_paper_submit_plus_apply_writes_post_reconcile_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(
                base=base, run_id="20260516_OK_apply",
                paper_submit=True, apply_t10=True,
            )
            os.environ[dr.APPLY_ENV_GATE] = "true"
            try:
                intent = _intent()
                result = dr.run_daily(
                    ctx,
                    pre_reconcile_fn=lambda _c: _ok_summary(),
                    post_reconcile_fn=lambda _c: _ok_summary(),
                    manage_loop_fn=lambda _c, _i: [
                        _outcome(state=OrderState.FILLED, intent=intent,
                                  qty_filled=1.0, note="FILLED"),
                    ],
                    intents_loader=lambda _c: [intent],
                    t10_apply_fn=_ok_t10,
                )
                self.assertEqual(result.rc, 0)
                self.assertIsNone(result.hard_stop)
                self.assertIsNotNone(result.post_reconcile)
                self.assertEqual(result.t10_apply_summary.get("apply", {}).get("rc"), 0)
                rep = json.loads(Path(result.report_paths["json"]).read_text())
                self.assertEqual(rep["mode"], "apply")
                self.assertIsNone(rep["operator_action_required"])
            finally:
                os.environ.pop(dr.APPLY_ENV_GATE, None)

    def test_apply_t10_without_env_gate_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(
                base=base, run_id="20260516_OK_apply_nogate",
                paper_submit=True, apply_t10=True,
            )
            os.environ.pop(dr.APPLY_ENV_GATE, None)
            intent = _intent()
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [
                    _outcome(state=OrderState.FILLED, intent=intent,
                              qty_filled=1.0)
                ],
                intents_loader=lambda _c: [intent],
                t10_apply_fn=_ok_t10,
            )
        self.assertEqual(result.rc, 2)
        self.assertIsNotNone(result.hard_stop)
        self.assertEqual(result.hard_stop.where, "t10_apply")
        self.assertIn("AUTOTRADE_T10_APPLY_OK", result.hard_stop.reason)


# ──────────────────────────────────────────────────────────────────────
# R8 §8 hard stops
# ──────────────────────────────────────────────────────────────────────
class TestPreflightStops(unittest.TestCase):
    def test_missing_run_id(self) -> None:
        ctx = dr.DailyRunContext(run_id="", output_dir=Path("/tmp/never"))
        stop = dr.preflight(ctx)
        self.assertIsNotNone(stop)
        self.assertEqual(stop.where, "preflight")
        self.assertIn("--run-id", stop.reason)

    def test_run_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = dr.DailyRunContext(
                run_id="NEVEREXISTS", output_dir=Path(tmp),
            )
            stop = dr.preflight(ctx)
        self.assertIsNotNone(stop)
        self.assertIn("does not exist", stop.reason)

    def test_status_not_awaiting_execution_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="bad_status", status="executed")
            stop = dr.preflight(ctx)
        self.assertIsNotNone(stop)
        self.assertIn("awaiting_execution", stop.reason)

    def test_recovery_marker_in_journal_blocks_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="recovery_blocked")
            # Pre-stage: started without applied.
            tj.write_marker(ctx.run_dir, batch_id="apply-deadbeef",
                              status="started", run_id="recovery_blocked")
            stop = dr.preflight(ctx)
        self.assertIsNotNone(stop)
        self.assertEqual(stop.rc, 3)
        self.assertIn("recovery", stop.reason.lower())


class TestPreReconcileStops(unittest.TestCase):
    def test_qty_mismatch_blocks_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_pre_mismatch")
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(qty_mismatch_count=1),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [],
                intents_loader=_no_intents,
                t10_apply_fn=_ok_t10,
            )
        self.assertEqual(result.rc, 2)
        self.assertEqual(result.hard_stop.where, "pre_reconcile")


class TestManageLoopStops(unittest.TestCase):
    def test_unknown_outcome_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_unknown",
                             paper_submit=True)
            intent = _intent()
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [
                    _outcome(state=OrderState.UNKNOWN, intent=intent,
                              note="UNKNOWN during poll — verify before retry"),
                ],
                intents_loader=lambda _c: [intent],
                t10_apply_fn=_ok_t10,
            )
            self.assertEqual(result.rc, 2)
            self.assertEqual(result.hard_stop.where, "manage_loop")
            self.assertIn("UNKNOWN", result.hard_stop.reason)
            rep = json.loads(Path(result.report_paths["json"]).read_text())
            self.assertIsNotNone(rep["operator_action_required"])
            self.assertIn("UNKNOWN", rep["operator_action_required"])

    def test_stale_open_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_stale",
                             paper_submit=True)
            intent = _intent()
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [
                    _outcome(state=OrderState.OPEN_OR_PENDING, intent=intent,
                              note="timeout"),
                ],
                intents_loader=lambda _c: [intent],
                t10_apply_fn=_ok_t10,
            )
        self.assertEqual(result.rc, 2)
        self.assertEqual(result.hard_stop.where, "manage_loop")
        self.assertIn("still working", result.hard_stop.reason)

    def test_cancel_requested_unconfirmed_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_cr",
                             paper_submit=True)
            intent = _intent()
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [
                    _outcome(state=OrderState.CANCEL_REQUESTED, intent=intent,
                              note="cancel_dry_run"),
                ],
                intents_loader=lambda _c: [intent],
                t10_apply_fn=_ok_t10,
            )
        self.assertEqual(result.rc, 2)
        self.assertEqual(result.hard_stop.where, "manage_loop")


class TestT10ApplyStops(unittest.TestCase):
    def test_t10_recovery_rc3_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_t10rec",
                             paper_submit=True, apply_t10=True)
            os.environ[dr.APPLY_ENV_GATE] = "true"
            try:
                intent = _intent()
                result = dr.run_daily(
                    ctx,
                    pre_reconcile_fn=lambda _c: _ok_summary(),
                    post_reconcile_fn=lambda _c: _ok_summary(),
                    manage_loop_fn=lambda _c, _i: [
                        _outcome(state=OrderState.FILLED, intent=intent,
                                  qty_filled=1.0)
                    ],
                    intents_loader=lambda _c: [intent],
                    t10_apply_fn=lambda _c, apply: (
                        {"rc": 0} if not apply else {"rc": 3, "error": "recovery"}
                    ),
                )
            finally:
                os.environ.pop(dr.APPLY_ENV_GATE, None)
        self.assertEqual(result.rc, 3)
        self.assertEqual(result.hard_stop.where, "t10_apply")


class TestPostReconcileStops(unittest.TestCase):
    def test_post_mismatch_aborts_with_rc4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_post_mismatch",
                             paper_submit=True, apply_t10=True)
            os.environ[dr.APPLY_ENV_GATE] = "true"
            try:
                intent = _intent()
                result = dr.run_daily(
                    ctx,
                    pre_reconcile_fn=lambda _c: _ok_summary(),
                    post_reconcile_fn=lambda _c: _ok_summary(qty_mismatch_count=1),
                    manage_loop_fn=lambda _c, _i: [
                        _outcome(state=OrderState.FILLED, intent=intent,
                                  qty_filled=1.0)
                    ],
                    intents_loader=lambda _c: [intent],
                    t10_apply_fn=_ok_t10,
                )
            finally:
                os.environ.pop(dr.APPLY_ENV_GATE, None)
        self.assertEqual(result.rc, 4)
        self.assertEqual(result.hard_stop.where, "post_reconcile")


# ──────────────────────────────────────────────────────────────────────
# Report shape (R8 §9)
# ──────────────────────────────────────────────────────────────────────
class TestDailyReportShape(unittest.TestCase):
    def test_report_has_all_required_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_report_shape",
                             paper_submit=True, apply_t10=True)
            os.environ[dr.APPLY_ENV_GATE] = "true"
            try:
                intent = _intent()
                result = dr.run_daily(
                    ctx,
                    pre_reconcile_fn=lambda _c: _ok_summary(cash_drift_usd=0.0),
                    post_reconcile_fn=lambda _c: _ok_summary(cash_drift_usd=0.0),
                    manage_loop_fn=lambda _c, _i: [
                        _outcome(state=OrderState.FILLED, intent=intent,
                                  qty_filled=1.0, note="FILLED")
                    ],
                    intents_loader=lambda _c: [intent],
                    t10_apply_fn=lambda _c, apply: {"rc": 0, "executed_rows": 1},
                )
                rep = json.loads(Path(result.report_paths["json"]).read_text())
                for k in (
                    "schema_version", "ts", "operator_action_required",
                    "run_id", "autotrade_run_id", "profile", "market_session_started_at",
                    "mode", "rc", "hard_stop", "preflight_passed",
                    "pre_reconcile", "orders_submitted", "outcome_counts",
                    "outcomes", "t10_apply", "post_reconcile", "artifact_paths",
                    "cash_drift_usd",
                ):
                    self.assertIn(k, rep, msg=f"missing key {k!r} in daily report")
                md = Path(result.report_paths["md"]).read_text(encoding="utf-8")
                self.assertIn("Autotrade daily report", md)
                self.assertIn("clean", md)
            finally:
                os.environ.pop(dr.APPLY_ENV_GATE, None)

    def test_operator_action_first_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ctx = _make_ctx(base=base, run_id="20260516_blocked",
                             paper_submit=True)
            intent = _intent()
            result = dr.run_daily(
                ctx,
                pre_reconcile_fn=lambda _c: _ok_summary(),
                post_reconcile_fn=lambda _c: _ok_summary(),
                manage_loop_fn=lambda _c, _i: [
                    _outcome(state=OrderState.UNKNOWN, intent=intent,
                              note="UNKNOWN during poll")
                ],
                intents_loader=lambda _c: [intent],
                t10_apply_fn=_ok_t10,
            )
            md = Path(result.report_paths["md"]).read_text(encoding="utf-8")
            op_idx = md.find("Operator action required")
            orders_idx = md.find("Orders managed")
            self.assertGreater(op_idx, -1)
            if orders_idx > -1:
                self.assertLess(op_idx, orders_idx,
                                msg="Operator action section must precede orders table")


# ──────────────────────────────────────────────────────────────────────
# R9-B — main() real wiring acceptance tests
#
# These cover the bridge between argparse + env-gate enforcement and
# run_daily(). They MUST pass with no KIS network, so the test injects
# fakes for `factories=` and `paths_resolver=`.
# ──────────────────────────────────────────────────────────────────────
import io


def _build_fake_factories(
    *,
    pre: dr.ReconcileSummary,
    post: dr.ReconcileSummary,
    outcomes: List[ManagedOrderOutcome],
    intents: List[OrderIntent],
    t10_dry: Dict[str, Any],
    t10_apply: Dict[str, Any],
) -> dr.DefaultFactories:
    def _pre(_ctx):
        return pre

    def _post(_ctx):
        return post

    def _loader(_ctx):
        return list(intents)

    def _manage(_ctx, _ints):
        return list(outcomes)

    def _t10(_ctx, apply_mode: bool):
        return dict(t10_apply if apply_mode else t10_dry)

    return dr.DefaultFactories(
        pre_reconcile_fn=_pre, post_reconcile_fn=_post,
        intents_loader=_loader, manage_loop_fn=_manage,
        t10_apply_fn=_t10,
    )


def _save_env(*names: str):
    return {n: os.environ.get(n) for n in names}


def _restore_env(saved: Dict[str, Any]):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestDefaultFactoryLazyImports(unittest.TestCase):
    """Smoke test for the lazy imports inside ``default_pre_reconcile_fn``
    / ``default_manage_loop_fn`` / ``default_t10_apply_fn``.

    These factories are wired through ``DefaultFactories`` only when
    ``main()`` runs without injected fakes — i.e. exactly the path the
    R10 control panel calls. R10B uncovered a regression where one of
    them imported ``phase3.autotrade.kis_config`` (a module that has
    never existed; the real symbol lives in
    ``phase3.autotrade.kis_broker_adapter``). The fake-injected tests
    never exercised the real factories, so the typo only surfaced when
    the operator hit ``1. Dry Run Preflight / Report`` for real.

    This test resolves every module + symbol the lazy imports name, so
    a future rename / typo fails at unittest time, not market open."""

    def test_lazy_import_targets_exist(self) -> None:
        import importlib
        for mod in (
            "phase3.autotrade.kis_broker_adapter",
            "phase3.autotrade.reconcile",
            "phase3.autotrade.t10_applicator",
            "phase3.autotrade.intents_io",
            "phase3.autotrade.order_manager",
            "phase3.autotrade.global_halt",
            "cache_health",
        ):
            try:
                importlib.import_module(mod)
            except ImportError as e:
                self.fail(f"daily_runner lazy import target {mod!r} "
                           f"is not importable: {e}")
        from phase3.autotrade.kis_broker_adapter import (  # noqa: F401
            KisBrokerAdapter, OrderIntent, load_env_config,
        )


class TestMainDryRunRealWiring(unittest.TestCase):
    """`daily_runner.main(['--run-id', X, '--dry-run'], ...)` must
    return rc=0 against fake-safe wiring and emit both report paths
    plus the rc line on stdout. This is the path the R9-C control
    panel will call."""

    def test_main_dry_run_executes_real_wiring_with_fake_safe_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_dry"
            _make_ctx(base=base, run_id=run_id)  # populates run_meta
            paths = {"config_path": base / "cfg.yaml",
                     "output_dir": base, "holdings_log": base / "hl.xlsx"}
            factories = _build_fake_factories(
                pre=_ok_summary(), post=_ok_summary(),
                outcomes=[], intents=[],
                t10_dry={"rc": 0, "apply_mode": False, "stdout_tail": "(plan-only)"},
                t10_apply={"rc": 0, "apply_mode": True},
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            saved = _save_env(dr.SUBMIT_ENV_GATE, dr.CANCEL_ENV_GATE,
                              dr.APPLY_ENV_GATE)
            try:
                rc = dr.main(
                    ["--run-id", run_id, "--dry-run"],
                    factories=factories,
                    paths_resolver=lambda _p: paths,
                    stdout=stdout, stderr=stderr,
                )
            finally:
                _restore_env(saved)
            self.assertEqual(rc, 0, msg=f"stdout={stdout.getvalue()} stderr={stderr.getvalue()}")
            out = stdout.getvalue()
            self.assertIn(f"rc=0 run_id={run_id}", out)
            self.assertIn("mode=dry-run", out)
            self.assertIn("report.md", out)
            self.assertIn("report.json", out)
            run_dir = base / "daily_runs" / run_id
            self.assertTrue((run_dir / dr.DAILY_REPORT_MD).exists())
            self.assertTrue((run_dir / dr.DAILY_REPORT_JSON).exists())

    def test_main_returns_nonzero_on_hard_stop(self) -> None:
        """Status != awaiting_execution → rc=2 + hard_stop line on stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_badstatus"
            _make_ctx(base=base, run_id=run_id, status="dispatched")
            paths = {"config_path": base / "cfg.yaml",
                     "output_dir": base, "holdings_log": base / "hl.xlsx"}
            factories = _build_fake_factories(
                pre=_ok_summary(), post=_ok_summary(),
                outcomes=[], intents=[],
                t10_dry={"rc": 0}, t10_apply={"rc": 0},
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            saved = _save_env(dr.SUBMIT_ENV_GATE, dr.CANCEL_ENV_GATE,
                              dr.APPLY_ENV_GATE)
            try:
                rc = dr.main(
                    ["--run-id", run_id, "--dry-run"],
                    factories=factories,
                    paths_resolver=lambda _p: paths,
                    stdout=stdout, stderr=stderr,
                )
            finally:
                _restore_env(saved)
            self.assertEqual(rc, 2)
            self.assertIn("hard_stop", stderr.getvalue())


class TestMainEnvGates(unittest.TestCase):
    """Env-gate enforcement happens BEFORE any side effect: factories
    must never be invoked when a gate is missing."""

    def _run_main(self, args, *, env, paths, factories=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        saved = _save_env(dr.SUBMIT_ENV_GATE, dr.CANCEL_ENV_GATE,
                          dr.APPLY_ENV_GATE)
        try:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            rc = dr.main(
                args, factories=factories,
                paths_resolver=lambda _p: paths,
                stdout=stdout, stderr=stderr,
            )
        finally:
            _restore_env(saved)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _basic_paths(self, base: Path) -> Dict[str, Path]:
        return {"config_path": base / "cfg.yaml",
                 "output_dir": base, "holdings_log": base / "hl.xlsx"}

    def test_paper_submit_requires_submit_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_submit_gate"
            _make_ctx(base=base, run_id=run_id)
            called = {"pre": 0, "manage": 0}

            def _pre(_ctx):
                called["pre"] += 1
                return _ok_summary()

            def _manage(_ctx, _i):
                called["manage"] += 1
                return []

            factories = dr.DefaultFactories(
                pre_reconcile_fn=_pre, post_reconcile_fn=_pre,
                intents_loader=lambda _c: [],
                manage_loop_fn=_manage,
                t10_apply_fn=lambda _c, _a: {"rc": 0},
            )
            rc, out, err = self._run_main(
                ["--run-id", run_id, "--paper-submit"],
                env={dr.SUBMIT_ENV_GATE: None,
                     dr.CANCEL_ENV_GATE: None,
                     dr.APPLY_ENV_GATE: None},
                paths=self._basic_paths(base),
                factories=factories,
            )
            self.assertEqual(rc, 2)
            self.assertIn(dr.SUBMIT_ENV_GATE, err)
            # Critical: gate fires BEFORE we touch any factory.
            self.assertEqual(called["pre"], 0)
            self.assertEqual(called["manage"], 0)

    def test_paper_submit_requires_cancel_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_cancel_gate"
            _make_ctx(base=base, run_id=run_id)
            rc, out, err = self._run_main(
                ["--run-id", run_id, "--paper-submit"],
                env={dr.SUBMIT_ENV_GATE: "true",
                     dr.CANCEL_ENV_GATE: None,
                     dr.APPLY_ENV_GATE: None},
                paths=self._basic_paths(base),
            )
            self.assertEqual(rc, 2)
            self.assertIn(dr.CANCEL_ENV_GATE, err)

    def test_apply_t10_without_submit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_apply_no_submit"
            _make_ctx(base=base, run_id=run_id)
            rc, out, err = self._run_main(
                ["--run-id", run_id, "--apply-t10"],
                env={dr.SUBMIT_ENV_GATE: None,
                     dr.CANCEL_ENV_GATE: None,
                     dr.APPLY_ENV_GATE: "true"},
                paths=self._basic_paths(base),
            )
            self.assertEqual(rc, 2)
            self.assertIn("--apply-t10 requires --paper-submit", err)

    def test_apply_t10_requires_apply_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_id = "20260516_R9B_apply_gate"
            _make_ctx(base=base, run_id=run_id)
            rc, out, err = self._run_main(
                ["--run-id", run_id, "--paper-submit", "--apply-t10"],
                env={dr.SUBMIT_ENV_GATE: "true",
                     dr.CANCEL_ENV_GATE: "true",
                     dr.APPLY_ENV_GATE: None},
                paths=self._basic_paths(base),
            )
            self.assertEqual(rc, 2)
            self.assertIn(dr.APPLY_ENV_GATE, err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
