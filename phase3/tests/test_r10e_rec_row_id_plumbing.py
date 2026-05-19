"""R10E — rec_row_id plumbing.

In 20260519_220825_daily we observed three orders submitted with the
correct client_order_id (carrying RecRowId 73 / 74 / 75) but EVERY
``submitted`` event in ``autotrade_orders.jsonl`` was logged with
``rec_row_id=0``. That made t10_applicator abort with
``recommendations.csv has no RecRowId=0`` and blocked the entire
T10 apply path.

Root cause: ``default_manage_loop_fn`` hard-coded ``rec_row_id=0``
(with a TODO note saying "follow-up"). ``OrderIntent`` had no
``rec_row_id`` field so the value never reached manage_order.

This test surface locks in the R10E fix:

1. ``rec_row_id_from_client_order_id`` recovers the integer from
   the canonical CID pattern and handles malformed shapes safely.
2. ``make_buy_intent_row`` writes an explicit ``rec_row_id`` field
   into the row, auto-deriving it from the CID when not supplied.
3. ``candidate_to_intent_row`` propagates ``BuyCandidate.rec_row_id``
   onto the row.
4. ``submitted_intents.json`` round-trip preserves the field.
5. ``OrderIntent`` carries ``rec_row_id`` and defaults to 0 for
   backwards-compat with probe scripts / unit fixtures.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import intents_io
from phase3.autotrade.kis_broker_adapter import OrderIntent


class TestRecRowIdFromClientOrderId(unittest.TestCase):

    def test_canonical_cid_extracts_rid(self):
        cid = "co-20260519_220825_daily-73-B-2-MRNA"
        self.assertEqual(intents_io.rec_row_id_from_client_order_id(cid), 73)

    def test_run_id_with_dashes_is_still_handled(self):
        """We walk back from the ``-B|S-<qty>-<ticker>`` tail rather
        than split off the run_id, so run_ids that themselves contain
        dashes do not break parsing."""
        cid = "co-rid-with-dashes-and-things-99-B-3-AAPL"
        self.assertEqual(intents_io.rec_row_id_from_client_order_id(cid), 99)

    def test_sell_side_cid_works_too(self):
        cid = "co-20260519_daily-12-S-4-GOOG"
        self.assertEqual(intents_io.rec_row_id_from_client_order_id(cid), 12)

    def test_malformed_cid_returns_none(self):
        for bad in [
            None, "", "not-a-cid", "co-foo", "co-foo-bar",
            "co-rid-XYZ-B-2-AAPL",  # rid not int
            "garbage-rid-99-B-3-AAPL",  # no co prefix
        ]:
            with self.subTest(cid=bad):
                self.assertIsNone(
                    intents_io.rec_row_id_from_client_order_id(bad))


class TestMakeBuyIntentRowWritesRecRowId(unittest.TestCase):

    def test_explicit_rec_row_id_persists(self):
        row = intents_io.make_buy_intent_row(
            client_order_id="co-rid-73-B-2-MRNA",
            symbol="MRNA", qty=2, limit_price=48.11, market="NASD",
            rec_row_id=73,
        )
        self.assertEqual(row["rec_row_id"], 73)

    def test_rec_row_id_auto_derived_from_cid_when_omitted(self):
        row = intents_io.make_buy_intent_row(
            client_order_id="co-rid-75-B-2-JBL",
            symbol="JBL", qty=2, limit_price=338.73, market="NYSE",
        )
        self.assertEqual(row["rec_row_id"], 75)

    def test_unparseable_cid_omits_rec_row_id_field(self):
        row = intents_io.make_buy_intent_row(
            client_order_id="co-anything",
            symbol="X", qty=1, limit_price=1.0, market="NASD",
        )
        # No rid -> field simply absent; loader falls back to 0.
        self.assertNotIn("rec_row_id", row)


class TestCandidateToIntentRowPropagatesRecRowId(unittest.TestCase):

    def test_buy_candidate_rid_persists_through_batch_helper(self):
        c = intents_io.BuyCandidate(
            run_id="20260519_daily", rec_row_id=75, ticker="JBL",
            action="BUY_MORE", reco_shares=2, reco_price=338.73,
            rank=12, regime="SIDE", market="NYSE", actionable=True,
            raw_row={},
        )
        rows = intents_io.candidates_to_intent_rows([c])
        self.assertEqual(rows[0]["rec_row_id"], 75)

    def test_round_trip_through_submitted_intents_json(self):
        td = tempfile.mkdtemp(prefix="r10e_rid_")
        run_dir = Path(td)
        cands = [
            intents_io.BuyCandidate(
                run_id="rid", rec_row_id=73, ticker="MRNA",
                action="BUY_MORE", reco_shares=2, reco_price=48.11,
                rank=2, regime="SIDE", market="NASD",
                actionable=True, raw_row={},
            ),
            intents_io.BuyCandidate(
                run_id="rid", rec_row_id=75, ticker="JBL",
                action="BUY_MORE", reco_shares=2, reco_price=338.73,
                rank=12, regime="SIDE", market="NYSE",
                actionable=True, raw_row={},
            ),
        ]
        out = intents_io.write_intent_file_from_candidates(
            run_dir, cands, run_id="rid")
        data = json.loads(out.read_text(encoding="utf-8"))
        rid_by_ticker = {
            r["symbol"]: r.get("rec_row_id") for r in data["intents"]
        }
        self.assertEqual(rid_by_ticker, {"MRNA": 73, "JBL": 75})


class TestOrderIntentCarriesRecRowId(unittest.TestCase):

    def test_default_is_zero(self):
        oi = OrderIntent(symbol="X", market="NASD", side="BUY", qty=1)
        self.assertEqual(oi.rec_row_id, 0)

    def test_explicit_value_persists(self):
        oi = OrderIntent(
            symbol="MRNA", market="NASD", side="BUY", qty=2,
            order_type="LIMIT", limit_price=48.11,
            client_order_id="co-rid-73-B-2-MRNA",
            rec_row_id=73,
        )
        self.assertEqual(oi.rec_row_id, 73)


class TestDailyRunnerIntentsLoaderPropagatesRecRowId(unittest.TestCase):
    """End-to-end-ish: write a run-shaped submitted_intents.json and
    drive ``default_intents_loader`` against it. The loader must
    return OrderIntent objects whose ``rec_row_id`` reflects the row
    field (when present) or the parsed CID (when only the legacy
    shape is on disk)."""

    def _run_dir_with_rows(self, rows):
        td = tempfile.mkdtemp(prefix="r10e_loader_")
        output_dir = Path(td)
        run_dir = output_dir / "daily_runs" / "rid"
        run_dir.mkdir(parents=True)
        out = run_dir / "submitted_intents.json"
        out.write_text(json.dumps({
            "schema_version": "intents/v1",
            "run_id": "rid",
            "generated_at": "2026-05-19T14:00:00+00:00",
            "intents": rows,
        }), encoding="utf-8")
        return output_dir

    def _ctx(self, output_dir):
        from phase3.autotrade.daily_runner import DailyRunContext
        return DailyRunContext(
            run_id="rid",
            autotrade_run_id="at-test", profile="paper",
            dry_run=False,
            output_dir=output_dir,
        )

    def test_explicit_field_wins(self):
        from phase3.autotrade.daily_runner import default_intents_loader
        output_dir = self._run_dir_with_rows([
            {
                "client_order_id": "co-rid-73-B-2-MRNA",
                "symbol": "MRNA", "market": "NASD", "side": "BUY",
                "qty": 2, "ord_type": "LIMIT", "limit_price": 48.11,
                "rec_row_id": 73,
            },
        ])
        intents = default_intents_loader(self._ctx(output_dir))
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].rec_row_id, 73)

    def test_legacy_row_without_field_recovers_from_cid(self):
        """A submitted_intents.json written before R10E does not
        contain rec_row_id. The loader recovers it from
        client_order_id so the rest of the pipeline still works."""
        from phase3.autotrade.daily_runner import default_intents_loader
        output_dir = self._run_dir_with_rows([
            {
                "client_order_id": "co-rid-75-B-2-JBL",
                "symbol": "JBL", "market": "NASD", "side": "BUY",
                "qty": 2, "ord_type": "LIMIT", "limit_price": 338.73,
            },
        ])
        intents = default_intents_loader(self._ctx(output_dir))
        self.assertEqual(intents[0].rec_row_id, 75)

    def test_unparseable_cid_falls_back_to_zero(self):
        from phase3.autotrade.daily_runner import default_intents_loader
        output_dir = self._run_dir_with_rows([
            {
                "client_order_id": "co-rid-XYZ-mixed-cid",
                "symbol": "X", "market": "NASD", "side": "BUY",
                "qty": 1, "ord_type": "LIMIT", "limit_price": 10.0,
            },
        ])
        intents = default_intents_loader(self._ctx(output_dir))
        # Loader stays robust: degraded behaviour is the pre-R10E
        # behaviour, not a crash.
        self.assertEqual(intents[0].rec_row_id, 0)


if __name__ == "__main__":
    unittest.main()
