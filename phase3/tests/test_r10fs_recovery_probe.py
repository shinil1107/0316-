"""R10F-S — broker-state probe for operator-driven cid clearing.

The probe sits between the operator clicking "Probe broker" in the
panel and the call to ``OrderStore.log_operator_cleared``. It must:

* Treat a missing broker_order_id (duplicate-guard-only history) as
  safe to clear with ``broker_state_at_clear='no_broker_contact'``.
* Map each ``classify_from_full_ccnl`` outcome to the canonical clear
  string and decide ``safe_to_clear`` accordingly.
* Refuse to declare safety when the broker probe itself raised.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.recovery import (
    SAFE_TO_CLEAR_STATES,
    BrokerProbeResult,
    probe_broker_state,
)


def _ccnl_row(
    *,
    odno: str,
    side_name: str = "매수",
    pdno: str = "CIEN",
    ord_qty: int = 1,
    ccld_qty: int = 0,
    ccld_unpr: float = 0.0,
    nccs_qty: int = 0,
    rvse_cncl_name: str = "보통",
    rjct_rson_name: str = "",
) -> Dict[str, Any]:
    return {
        "odno": str(int(odno)),
        "orgn_odno": "",
        "pdno": pdno,
        "sll_buy_dvsn_cd_name": side_name,
        "ft_ord_qty": str(int(ord_qty)),
        "ft_ccld_qty": str(int(ccld_qty)),
        "nccs_qty": str(int(nccs_qty)),
        "ft_ord_unpr3": "100.0000",
        "ft_ccld_unpr3": f"{float(ccld_unpr):.8f}",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": rvse_cncl_name,
        "prcs_stat_name": "",
        "rjct_rson_name": rjct_rson_name,
    }


class _FakeAdapter:
    def __init__(self, rows: List[Dict[str, Any]],
                 *, raise_on_call: Exception = None):
        self.rows = list(rows)
        self.raise_on_call = raise_on_call
        self.calls = 0

    def get_order_history(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls += 1
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return [dict(r) for r in self.rows]


class TestProbeBrokerState(unittest.TestCase):

    def test_no_broker_order_id_means_safe_to_clear(self):
        """Duplicate-guard rows often have no ODNO; the broker has
        nothing live, so this is the easiest clear case."""
        adapter = _FakeAdapter(rows=[])  # never called
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-1",
            broker_order_id=None,
        )
        self.assertEqual(r.broker_state_at_clear, "no_broker_contact")
        self.assertTrue(r.safe_to_clear)
        self.assertEqual(adapter.calls, 0)
        self.assertIn("no ODNO", r.summary)

    def test_cancelled_ccnl_is_safe(self):
        # Two rows: the original + a cancel sibling. ``classify_from_full_ccnl``
        # finds the original (nccs_qty=0 after cancel) and labels it CANCELLED.
        rows = [
            _ccnl_row(odno="35200", ord_qty=1, ccld_qty=0, nccs_qty=0),
            {
                "odno": "35201", "orgn_odno": "35200",
                "pdno": "CIEN",
                "sll_buy_dvsn_cd_name": "매수취소",
                "ft_ord_qty": "1", "ft_ccld_qty": "0", "nccs_qty": "0",
                "ft_ord_unpr3": "0.00000000",
                "ft_ccld_unpr3": "0.00000000",
                "rvse_cncl_dvsn": "02",
                "rvse_cncl_dvsn_name": "취소",
            },
        ]
        adapter = _FakeAdapter(rows=rows)
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-2",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "cancelled")
        self.assertTrue(r.safe_to_clear)
        self.assertIn("ODNO=0000035200", r.summary)
        self.assertEqual(r.raw["ccnl_state"], "cancelled")

    def test_filled_ccnl_is_NOT_safe(self):
        """A filled order must never be cleared: T10 still owes a
        ledger entry. Clearing here would lose track of the position."""
        rows = [
            _ccnl_row(odno="35200", ord_qty=1, ccld_qty=1,
                      ccld_unpr=99.5, nccs_qty=0),
        ]
        adapter = _FakeAdapter(rows=rows)
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-3",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "filled")
        self.assertFalse(r.safe_to_clear)
        self.assertIn("NOT safe", r.summary)

    def test_rejected_ccnl_is_safe(self):
        rows = [
            _ccnl_row(odno="35200", ord_qty=1, ccld_qty=0, nccs_qty=0,
                      rjct_rson_name="잔고부족"),
        ]
        adapter = _FakeAdapter(rows=rows)
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-4",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "rejected")
        self.assertTrue(r.safe_to_clear)

    def test_open_or_pending_is_NOT_safe(self):
        rows = [
            _ccnl_row(odno="35200", ord_qty=1, ccld_qty=0, nccs_qty=1),
        ]
        adapter = _FakeAdapter(rows=rows)
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-5",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "open_or_pending")
        self.assertFalse(r.safe_to_clear)

    def test_absent_ccnl_row_is_safe(self):
        """The probe found broker history but not the target ODNO —
        the broker has no record of it. Safe to clear; the cid had
        no live order."""
        rows = [
            _ccnl_row(odno="99999", ord_qty=1, ccld_qty=1, ccld_unpr=10.0,
                      nccs_qty=0),
        ]
        adapter = _FakeAdapter(rows=rows)
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-6",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "absent")
        self.assertTrue(r.safe_to_clear)

    def test_adapter_exception_is_NOT_safe(self):
        """Connection error from broker → cannot prove anything, must
        refuse the clear."""
        adapter = _FakeAdapter(
            rows=[],
            raise_on_call=ConnectionError("kis down"),
        )
        r = probe_broker_state(
            adapter,
            client_order_id="co-stuck-7",
            broker_order_id="0000035200",
        )
        self.assertEqual(r.broker_state_at_clear, "error")
        self.assertFalse(r.safe_to_clear)
        self.assertIn("ConnectionError", r.summary)


class TestSafeToClearMatrix(unittest.TestCase):
    """Lock the canonical safety list so we don't accidentally widen
    it without a corresponding code review."""

    def test_canonical_safe_states(self):
        self.assertEqual(
            SAFE_TO_CLEAR_STATES,
            frozenset({"cancelled", "rejected", "absent", "no_broker_contact"}),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
