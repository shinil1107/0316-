"""R10B — Generate Intent File UI tests.

These tests cover the *pure* projector helpers added in R10B:

  - ``intents_io.load_buy_candidates``
  - ``intents_io.candidate_to_intent_row``
  - ``intents_io.write_intent_file_from_candidate``
  - ``intents_io.build_intent_client_order_id``

…and the dashboard-side wiring that the Tk loop depends on:

  - ``control_panel.compute_panel_state`` now surfaces
    ``recommendations_csv_exists`` + ``recommendations_buy_count`` +
    ``buy_candidates``.
  - ``control_panel.compute_button_gates`` exposes a new
    ``generate_intent`` gate that mirrors R10B §3.1.
  - ``control_panel.build_command_preview('generate_intent', ...)``
    is a non-shell side-effect description (no subprocess).

The Tkinter loop is *not* exercised — only the pure helpers, the
on-disk file layout, and the panel-state contract. The button
callback (`_on_generate_intent`) is verified indirectly by
asserting that the dashboard refresh after a successful write
flips the Paper Submit blocker (``submitted_intents.json missing``)
off and clears the Generate gate (existing file now blocks
overwrite by default).
"""
from __future__ import annotations

import csv
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

from phase3.autotrade import control_panel as cp
from phase3.autotrade import global_halt
from phase3.autotrade import intents_io


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────
_REC_FIELDS = (
    "RunId", "ScoringDate", "Actionable", "RecRowId", "Date", "Ticker",
    "Action", "Score", "TargetPct", "ActualPct", "GapPct",
    "Price", "Shares", "Capital", "Regime", "GraceCount", "Rank",
)


def _write_recommendations(run_dir: Path, rows: list[dict]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "recommendations.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_REC_FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _REC_FIELDS})
    return p


def _row(
    *, rec_row_id: int, ticker: str, action: str,
    shares: int = 2, price: float = 50.0, rank: int = 1,
    actionable: bool = True, regime: str = "RISK_ON",
    run_id: str = "20260516_210451_daily",
) -> dict:
    return {
        "RunId": run_id, "ScoringDate": "2026-05-16",
        "Actionable": str(actionable), "RecRowId": rec_row_id,
        "Ticker": ticker, "Action": action,
        "Score": 0.5, "TargetPct": 1.0, "ActualPct": 0.0, "GapPct": 1.0,
        "Price": price, "Shares": shares, "Capital": 1000.0,
        "Regime": regime, "GraceCount": 0, "Rank": rank,
    }


def _write_run_meta(base: Path, run_id: str,
                     status: str = "awaiting_execution") -> Path:
    rd = base / "daily_runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "run_meta.json").write_text(json.dumps(
        {"schema_version": "artifact/v1", "run_id": run_id, "status": status},
        indent=2))
    return rd


# ──────────────────────────────────────────────────────────────────────
# 1. load_buy_candidates — filter contract
# ──────────────────────────────────────────────────────────────────────
class TestLoadBuyCandidatesFilter(unittest.TestCase):
    def test_filters_to_buy_actions_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            _write_recommendations(rd, [
                _row(rec_row_id=74, ticker="MRNA", action="BUY_NEW",
                      price=49.04, shares=2, rank=2),
                _row(rec_row_id=75, ticker="APA",  action="BUY_MORE",
                      price=38.98, shares=2, rank=4),
                _row(rec_row_id=10, ticker="XYZ",  action="SELL_GRACE",
                      price=10.0,  shares=1, rank=20),
                _row(rec_row_id=11, ticker="ABC",  action="HOLD",
                      price=5.0,   shares=1, rank=21),
                _row(rec_row_id=12, ticker="DEF",  action="DEFERRED",
                      price=2.0,   shares=1, rank=22),
            ])
            cands = intents_io.load_buy_candidates(rd)
            tickers = [c.ticker for c in cands]
            self.assertEqual(sorted(tickers), ["APA", "MRNA"])

    def test_drops_zero_shares_or_zero_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            _write_recommendations(rd, [
                _row(rec_row_id=1, ticker="ZERO_QTY", action="BUY_NEW",
                      shares=0, price=10.0),
                _row(rec_row_id=2, ticker="ZERO_PX",  action="BUY_NEW",
                      shares=1, price=0.0),
                _row(rec_row_id=3, ticker="OK",       action="BUY_NEW",
                      shares=1, price=10.0),
            ])
            cands = intents_io.load_buy_candidates(rd)
            self.assertEqual([c.ticker for c in cands], ["OK"])

    def test_respects_actionable_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            _write_recommendations(rd, [
                _row(rec_row_id=1, ticker="NOACT", action="BUY_NEW",
                      actionable=False, shares=1, price=10.0),
                _row(rec_row_id=2, ticker="OK",    action="BUY_NEW",
                      actionable=True,  shares=1, price=10.0),
            ])
            cands = intents_io.load_buy_candidates(rd)
            self.assertEqual([c.ticker for c in cands], ["OK"])

    def test_returns_empty_when_csv_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(intents_io.load_buy_candidates(Path(tmp)), [])

    def test_sort_order_by_rank_then_rec_row_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            _write_recommendations(rd, [
                _row(rec_row_id=3, ticker="C", action="BUY_NEW", rank=10),
                _row(rec_row_id=1, ticker="A", action="BUY_NEW", rank=1),
                _row(rec_row_id=2, ticker="B", action="BUY_NEW", rank=5),
            ])
            cands = intents_io.load_buy_candidates(rd)
            self.assertEqual([c.ticker for c in cands], ["A", "B", "C"])


# ──────────────────────────────────────────────────────────────────────
# 2. candidate_to_intent_row + build_intent_client_order_id
# ──────────────────────────────────────────────────────────────────────
class TestCandidateToIntentRow(unittest.TestCase):
    def _make_candidate(self) -> intents_io.BuyCandidate:
        return intents_io.BuyCandidate(
            run_id="20260516_210451_daily", rec_row_id=75,
            ticker="APA", action="BUY_MORE",
            reco_shares=2, reco_price=38.98, rank=4,
        )

    def test_qty_override_one(self) -> None:
        cand = self._make_candidate()
        row = intents_io.candidate_to_intent_row(
            cand, qty_override=1, limit_price=38.50)
        self.assertEqual(row["qty"], 1)
        self.assertEqual(row["side"], "BUY")
        self.assertEqual(row["ord_type"], "LIMIT")
        self.assertEqual(row["symbol"], "APA")
        self.assertAlmostEqual(row["limit_price"], 38.50)

    def test_rejects_zero_qty(self) -> None:
        cand = self._make_candidate()
        with self.assertRaises(ValueError):
            intents_io.candidate_to_intent_row(
                cand, qty_override=0, limit_price=10.0)

    def test_rejects_zero_limit(self) -> None:
        cand = self._make_candidate()
        with self.assertRaises(ValueError):
            intents_io.candidate_to_intent_row(
                cand, qty_override=1, limit_price=0.0)

    def test_default_uses_reco_shares_and_price(self) -> None:
        cand = self._make_candidate()
        row = intents_io.candidate_to_intent_row(cand)
        self.assertEqual(row["qty"], 2)
        self.assertAlmostEqual(row["limit_price"], 38.98)

    def test_client_order_id_deterministic_and_human_readable(self) -> None:
        a = intents_io.build_intent_client_order_id(
            run_id="20260516_210451_daily", rec_row_id=75,
            ticker="APA", qty=1)
        b = intents_io.build_intent_client_order_id(
            run_id="20260516_210451_daily", rec_row_id=75,
            ticker="APA", qty=1)
        self.assertEqual(a, b)
        self.assertEqual(a, "co-20260516_210451_daily-75-B-1-APA")

    def test_client_order_id_sanitizes_unsafe_chars(self) -> None:
        cid = intents_io.build_intent_client_order_id(
            run_id="run with spaces/and slash",
            rec_row_id=1, ticker="bad!ticker", qty=1)
        for ch in cid:
            self.assertTrue(ch.isalnum() or ch in "_-",
                             f"unexpected char {ch!r} in {cid!r}")
        self.assertTrue(cid.startswith("co-"))
        # `bad!ticker` should sanitize to `badticker` (case preserved).
        self.assertIn("badticker", cid)


# ──────────────────────────────────────────────────────────────────────
# 3. write_intent_file_from_candidate — overwrite contract
# ──────────────────────────────────────────────────────────────────────
class TestWriteIntentFile(unittest.TestCase):
    def _make_candidate(self) -> intents_io.BuyCandidate:
        return intents_io.BuyCandidate(
            run_id="20260516_210451_daily", rec_row_id=75,
            ticker="APA", action="BUY_MORE",
            reco_shares=2, reco_price=38.98, rank=4,
        )

    def test_writes_valid_intent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            p = intents_io.write_intent_file_from_candidate(
                rd, self._make_candidate(),
                qty_override=1, limit_price=38.50)
            self.assertTrue(p.exists())
            data = json.loads(p.read_text())
            self.assertEqual(data["schema_version"], "intents/v1")
            self.assertEqual(data["run_id"], "20260516_210451_daily")
            self.assertEqual(len(data["intents"]), 1)
            row = data["intents"][0]
            self.assertEqual(row["symbol"], "APA")
            self.assertEqual(row["qty"], 1)
            self.assertEqual(row["side"], "BUY")
            self.assertEqual(row["ord_type"], "LIMIT")

    def test_refuses_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            intents_io.write_intent_file_from_candidate(
                rd, self._make_candidate(),
                qty_override=1, limit_price=38.50)
            with self.assertRaises(FileExistsError):
                intents_io.write_intent_file_from_candidate(
                    rd, self._make_candidate(),
                    qty_override=1, limit_price=38.50)

    def test_allows_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            intents_io.write_intent_file_from_candidate(
                rd, self._make_candidate(),
                qty_override=1, limit_price=38.50)
            p = intents_io.write_intent_file_from_candidate(
                rd, self._make_candidate(),
                qty_override=2, limit_price=39.00, overwrite=True)
            data = json.loads(p.read_text())
            self.assertEqual(data["intents"][0]["qty"], 2)
            self.assertAlmostEqual(data["intents"][0]["limit_price"], 39.0)


# ──────────────────────────────────────────────────────────────────────
# 4. Panel state + button-gate wiring
# ──────────────────────────────────────────────────────────────────────
def _empty_env() -> dict:
    """Tests must not see the developer's real arming env."""
    return {"KIS_ENV": "paper"}


class TestPanelStateAndGenerateGate(unittest.TestCase):
    def _setup_run(self, base: Path, *, with_recs: bool = True,
                    status: str = "awaiting_execution") -> tuple[str, Path]:
        rid = "20260516_210451_daily"
        rd = _write_run_meta(base, rid, status=status)
        if with_recs:
            _write_recommendations(rd, [
                _row(rec_row_id=74, ticker="MRNA", action="BUY_NEW",
                      shares=2, price=49.04, rank=2),
                _row(rec_row_id=75, ticker="APA",  action="BUY_MORE",
                      shares=2, price=38.98, rank=4),
                _row(rec_row_id=10, ticker="X",    action="SELL_GRACE",
                      shares=1, price=5.0,  rank=20),
            ])
        return rid, rd

    def test_panel_state_surfaces_buy_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, _ = self._setup_run(base)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)
            ps = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            self.assertTrue(ps.recommendations_csv_exists)
            self.assertEqual(ps.recommendations_buy_count, 2)
            self.assertEqual({c.ticker for c in ps.buy_candidates},
                              {"MRNA", "APA"})

    def test_generate_button_enabled_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, _ = self._setup_run(base)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)
            ps = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            gates = cp.compute_button_gates(ps)
            self.assertTrue(gates["generate_intent"].enabled,
                             gates["generate_intent"].reason)

    def test_generate_button_disabled_without_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)
            ps = cp.compute_panel_state(
                output_dir=base, run_id="",
                env=_empty_env(), halt_path=halt_path,
            )
            gates = cp.compute_button_gates(ps)
            self.assertFalse(gates["generate_intent"].enabled)
            self.assertIn("no run_id", gates["generate_intent"].reason.lower())

    def test_generate_button_disabled_when_recs_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, _ = self._setup_run(base, with_recs=False)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)
            ps = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            gates = cp.compute_button_gates(ps)
            self.assertFalse(gates["generate_intent"].enabled)
            self.assertIn("recommendations.csv missing",
                           gates["generate_intent"].reason.lower())

    def test_generate_button_disabled_when_halted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, _ = self._setup_run(base)
            halt_path = base / "halt.json"
            global_halt.write_halt(halt=True, reason="test",
                                    operator="t", path=halt_path)
            try:
                ps = cp.compute_panel_state(
                    output_dir=base, run_id=rid,
                    env=_empty_env(), halt_path=halt_path,
                )
                gates = cp.compute_button_gates(ps)
                self.assertFalse(gates["generate_intent"].enabled)
                self.assertIn("global_halt",
                               gates["generate_intent"].reason.lower())
            finally:
                global_halt.clear_halt(path=halt_path)

    def test_generate_button_disabled_when_intents_exist_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, rd = self._setup_run(base)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)
            cand = intents_io.load_buy_candidates(rd)[0]
            intents_io.write_intent_file_from_candidate(
                rd, cand, qty_override=1, limit_price=cand.reco_price)
            ps = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            gates_no_overwrite = cp.compute_button_gates(
                ps, overwrite_intents_checked=False)
            self.assertFalse(gates_no_overwrite["generate_intent"].enabled)
            self.assertIn("already exists",
                           gates_no_overwrite["generate_intent"].reason.lower())
            gates_with_overwrite = cp.compute_button_gates(
                ps, overwrite_intents_checked=True)
            self.assertTrue(gates_with_overwrite["generate_intent"].enabled)

    def test_generate_intent_clears_paper_submit_missing_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rid, rd = self._setup_run(base)
            halt_path = base / "halt.json"
            global_halt.clear_halt(path=halt_path)

            ps_before = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            self.assertFalse(ps_before.intents.is_ok)

            cand = intents_io.load_buy_candidates(rd)[0]
            intents_io.write_intent_file_from_candidate(
                rd, cand, qty_override=1, limit_price=cand.reco_price)

            ps_after = cp.compute_panel_state(
                output_dir=base, run_id=rid,
                env=_empty_env(), halt_path=halt_path,
            )
            self.assertTrue(ps_after.intents.is_ok)
            self.assertEqual(ps_after.intents.buy_count, 1)
            self.assertEqual(ps_after.intents.intent_count, 1)


# ──────────────────────────────────────────────────────────────────────
# 5. Command preview for generate_intent
# ──────────────────────────────────────────────────────────────────────
class TestGenerateIntentCommandPreview(unittest.TestCase):
    def test_preview_is_description_not_shell(self) -> None:
        prev = cp.build_command_preview(
            "generate_intent", run_id="20260516_210451_daily")
        self.assertIn("submitted_intents.json", prev)
        self.assertIn("no broker call", prev.lower())
        self.assertNotIn("daily_runner", prev)
        self.assertNotIn("paper-submit", prev)


# ──────────────────────────────────────────────────────────────────────
# 6. R10B-fix — Intent Preparation reset logic
# ──────────────────────────────────────────────────────────────────────
class TestIntentPrepResetLogic(unittest.TestCase):
    """Operator-error guard: a previously typed limit price must NOT
    survive a run_id change or a recommendations.csv mutation."""

    def _cand(self, *, rec_row_id: int, ticker: str,
                price: float = 50.0) -> intents_io.BuyCandidate:
        return intents_io.BuyCandidate(
            run_id="r", rec_row_id=rec_row_id, ticker=ticker,
            action="BUY_NEW", reco_shares=1, reco_price=price,
            rank=1,
        )

    def test_first_refresh_resets(self) -> None:
        cands = [self._cand(rec_row_id=1, ticker="APA")]
        self.assertTrue(cp.intent_prep_should_reset(
            prev_run_id=None, prev_signature=None,
            new_run_id="run-A", new_candidates=cands,
        ))

    def test_same_run_same_candidates_does_not_reset(self) -> None:
        cands = [self._cand(rec_row_id=1, ticker="APA"),
                  self._cand(rec_row_id=2, ticker="MRNA")]
        sig = cp.intent_candidate_signature(cands)
        self.assertFalse(cp.intent_prep_should_reset(
            prev_run_id="run-A", prev_signature=sig,
            new_run_id="run-A", new_candidates=cands,
        ))

    def test_run_id_change_resets(self) -> None:
        cands_a = [self._cand(rec_row_id=1, ticker="APA")]
        cands_b = [self._cand(rec_row_id=9, ticker="MRNA")]
        sig_a = cp.intent_candidate_signature(cands_a)
        self.assertTrue(cp.intent_prep_should_reset(
            prev_run_id="run-A", prev_signature=sig_a,
            new_run_id="run-B", new_candidates=cands_b,
        ))

    def test_candidate_set_change_same_run_resets(self) -> None:
        cands_old = [self._cand(rec_row_id=1, ticker="APA")]
        cands_new = [self._cand(rec_row_id=1, ticker="APA"),
                      self._cand(rec_row_id=2, ticker="MRNA")]
        sig_old = cp.intent_candidate_signature(cands_old)
        self.assertTrue(cp.intent_prep_should_reset(
            prev_run_id="run-A", prev_signature=sig_old,
            new_run_id="run-A", new_candidates=cands_new,
        ))

    def test_price_change_only_does_not_reset(self) -> None:
        cands_old = [self._cand(rec_row_id=1, ticker="APA", price=10.0)]
        cands_new = [self._cand(rec_row_id=1, ticker="APA", price=20.0)]
        sig_old = cp.intent_candidate_signature(cands_old)
        self.assertFalse(cp.intent_prep_should_reset(
            prev_run_id="run-A", prev_signature=sig_old,
            new_run_id="run-A", new_candidates=cands_new,
        ))

    def test_candidate_order_change_resets(self) -> None:
        cands_old = [self._cand(rec_row_id=1, ticker="APA"),
                      self._cand(rec_row_id=2, ticker="MRNA")]
        cands_new = [self._cand(rec_row_id=2, ticker="MRNA"),
                      self._cand(rec_row_id=1, ticker="APA")]
        sig_old = cp.intent_candidate_signature(cands_old)
        self.assertTrue(cp.intent_prep_should_reset(
            prev_run_id="run-A", prev_signature=sig_old,
            new_run_id="run-A", new_candidates=cands_new,
        ))

    def test_signature_only_uses_rec_row_id_and_ticker(self) -> None:
        c1 = self._cand(rec_row_id=1, ticker="APA", price=10.0)
        c2 = self._cand(rec_row_id=1, ticker="APA", price=99.0)
        self.assertEqual(
            cp.intent_candidate_signature([c1]),
            cp.intent_candidate_signature([c2]),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
