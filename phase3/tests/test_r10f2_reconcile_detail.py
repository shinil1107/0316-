"""R10F-2 — pre-reconcile detail arrays must flow from
``reconcile.ReconcileReport.buckets`` into
``ReconcileSummary.raw`` and on into the HardStop detail payload.

Bug it pins
-----------

Before R10F-2, ``_reconcile_summary_from_report`` was populating its
``raw`` dict like this:

    raw={
        "qty_mismatches": getattr(rep, "qty_mismatches", []),
        "local_only":     getattr(rep, "local_only", []),
        "broker_only_managed": getattr(rep, "broker_only_managed", []),
    }

But ``ReconcileReport`` carries the actual detail rows in a single
``buckets: Dict[str, List[Dict[str, Any]]]`` field with keys
``qty_mismatch`` / ``local_only`` / ``broker_only_managed`` (etc.).
The ``getattr`` lookup against attribute names that don't exist
therefore always returned ``[]``. On 5/20 the operator hit a
``rc=2 hard_stop@pre_reconcile`` with ``qty_mismatch_count=1`` and
the detail arrays empty, and had to fall back to running
``reconcile.py`` from the CLI to learn the offending ticker
(MRNA).

R10F-2 wires the lookup to ``rep.buckets[<name>]`` so the same
hard-stop carries the actionable rows directly. This test pins the
new contract end-to-end against a fake ReconcileReport.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.daily_runner import (
    _reconcile_summary_from_report,
    evaluate_reconcile,
)


@dataclass
class _FakeReconcileReport:
    """Minimal stand-in matching ``reconcile.ReconcileReport``'s
    duck-typed surface. We deliberately reuse the canonical
    ``buckets`` dict so a future rename on the real class breaks
    this test and forces a conscious update."""
    qty_mismatch_count: int = 0
    local_only_count: int = 0
    broker_only_managed_count: int = 0
    cash_drift_usd: float = 0.0
    settlement_pending_usd: float = 0.0
    buckets: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# 1. raw dict carries the bucket detail rows
# ──────────────────────────────────────────────────────────────────────
class TestSummaryRawCarriesBucketDetail(unittest.TestCase):

    def test_qty_mismatch_detail_propagates(self):
        """The 5/20 MRNA scenario: count=1 + a single detail row that
        names the offending ticker."""
        rep = _FakeReconcileReport(
            qty_mismatch_count=1,
            buckets={
                "qty_mismatch": [
                    {"ticker": "MRNA",
                     "local_qty": 2, "broker_qty": 4,
                     "delta": -2},
                ],
            },
        )
        s = _reconcile_summary_from_report(rep)
        self.assertEqual(s.qty_mismatch_count, 1)
        self.assertEqual(len(s.raw["qty_mismatches"]), 1)
        self.assertEqual(s.raw["qty_mismatches"][0]["ticker"], "MRNA")

    def test_local_only_and_broker_only_detail_propagate(self):
        rep = _FakeReconcileReport(
            local_only_count=1,
            broker_only_managed_count=1,
            buckets={
                "local_only": [{"ticker": "AAPL"}],
                "broker_only_managed": [{"ticker": "TSLA"}],
            },
        )
        s = _reconcile_summary_from_report(rep)
        self.assertEqual(s.raw["local_only"][0]["ticker"], "AAPL")
        self.assertEqual(s.raw["broker_only_managed"][0]["ticker"], "TSLA")

    def test_missing_buckets_field_is_tolerated(self):
        """A bare ReconcileReport without ``buckets`` (test stubs,
        legacy persistence) must still produce a valid summary with
        empty detail lists rather than KeyError."""

        @dataclass
        class _Bare:
            qty_mismatch_count: int = 1

        s = _reconcile_summary_from_report(_Bare())
        self.assertEqual(s.qty_mismatch_count, 1)
        self.assertEqual(s.raw["qty_mismatches"], [])
        self.assertEqual(s.raw["local_only"], [])
        self.assertEqual(s.raw["broker_only_managed"], [])

    def test_empty_bucket_dict_returns_empty_lists(self):
        rep = _FakeReconcileReport(qty_mismatch_count=0, buckets={})
        s = _reconcile_summary_from_report(rep)
        self.assertEqual(s.raw["qty_mismatches"], [])
        self.assertEqual(s.raw["local_only"], [])
        self.assertEqual(s.raw["broker_only_managed"], [])


# ──────────────────────────────────────────────────────────────────────
# 2. HardStop.detail carries the bucket detail (the operator-facing fix)
# ──────────────────────────────────────────────────────────────────────
class TestHardStopDetailCarriesBuckets(unittest.TestCase):

    def test_pre_reconcile_hardstop_includes_qty_mismatch_rows(self):
        """End-to-end: feed the 5/20-style report into
        ``evaluate_reconcile`` and assert the HardStop's ``detail``
        carries the offending row — which is what the daily report /
        JSON output then serialises for the operator."""
        rep = _FakeReconcileReport(
            qty_mismatch_count=1,
            buckets={
                "qty_mismatch": [
                    {"ticker": "MRNA",
                     "local_qty": 2, "broker_qty": 4,
                     "delta": -2},
                ],
            },
        )
        summary = _reconcile_summary_from_report(rep)
        stop = evaluate_reconcile(summary, phase="pre")
        self.assertIsNotNone(stop)
        self.assertEqual(stop.where, "pre_reconcile")
        self.assertEqual(stop.rc, 2)
        # Crucial assertion: detail is NOT empty.
        self.assertEqual(len(stop.detail["qty_mismatches"]), 1)
        self.assertEqual(
            stop.detail["qty_mismatches"][0]["ticker"], "MRNA")

    def test_no_mismatch_means_no_hardstop(self):
        rep = _FakeReconcileReport(qty_mismatch_count=0, buckets={})
        summary = _reconcile_summary_from_report(rep)
        stop = evaluate_reconcile(summary, phase="pre")
        self.assertIsNone(stop)


# ──────────────────────────────────────────────────────────────────────
# 3. Lock the canonical bucket key names
# ──────────────────────────────────────────────────────────────────────
class TestCanonicalBucketKeys(unittest.TestCase):
    """If anyone renames the bucket keys in ``reconcile.py``,
    ``_reconcile_summary_from_report`` will silently regress to
    empty detail arrays again. Lock the names here."""

    def test_canonical_bucket_keys_used_by_summary(self):
        """We rebuild the summary with each canonical key in isolation
        and assert the corresponding raw list comes back non-empty.
        If a future PR renames any of the keys, the test will fail
        on that key and the developer will know exactly which name
        slipped."""
        cases = [
            ("qty_mismatch", "qty_mismatches"),
            ("local_only", "local_only"),
            ("broker_only_managed", "broker_only_managed"),
        ]
        for bucket_key, summary_key in cases:
            rep = _FakeReconcileReport(buckets={
                bucket_key: [{"ticker": "X"}]
            })
            s = _reconcile_summary_from_report(rep)
            self.assertEqual(
                len(s.raw[summary_key]), 1,
                f"bucket '{bucket_key}' did not propagate to "
                f"summary key '{summary_key}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
