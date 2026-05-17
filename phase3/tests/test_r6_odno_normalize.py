"""Round 6 acceptance — ODNO format normalization in echo / find_open_order.

Background
----------
On the first orchestrator paper submit (2026-05-15) three real KIS-paper
fills (APA/AMD/INTC) were classified UNKNOWN because:

  place_order ODNO   :  "0000041461"   ← zero-padded 10 chars
  inquire-ccnl odno  :  "41461"        ← leading zeros stripped
  inquire-nccs odno  :  (same, leading zeros stripped)

A literal ``str == str`` compare missed every fill. Cash deltas
independently confirmed the fills, so the bug is purely on the echo /
visibility side, not on submit.

These tests pin the normalization invariant down with synthetic adapters
(no network, no real KIS) so the next round can't regress it.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from phase3.autotrade.echo import _norm_odno, _try_ccnl, _try_nccs, echo_poll
from phase3.autotrade.kis_broker_adapter import OpenOrder


def _make_open_order(odno: str, symbol: str = "AMD", side: str = "BUY",
                     qty_order: float = 1.0, qty_filled: float = 0.0,
                     qty_remaining: float = 1.0,
                     limit_price: float = 430.82) -> OpenOrder:
    return OpenOrder(
        broker_order_id=odno,
        ord_dt="20260515", ord_tmd="134752",
        symbol=symbol, market="NASD", side=side,
        qty_order=qty_order, qty_filled=qty_filled,
        qty_remaining=qty_remaining,
        limit_price=limit_price, status_text="",
        raw={},
    )


class _FakeAdapter:
    """Minimal stand-in implementing only what echo helpers need."""

    def __init__(self, *, open_orders: List[OpenOrder] | None = None,
                 ccnl_rows: List[Dict[str, Any]] | None = None) -> None:
        self.open_orders = open_orders or []
        self.ccnl_rows = ccnl_rows or []

    def get_open_orders(self, *, market: str = "NASD") -> List[OpenOrder]:
        return list(self.open_orders)

    def get_order_history(self) -> List[Dict[str, Any]]:
        return list(self.ccnl_rows)


class TestNormOdno(unittest.TestCase):
    """`_norm_odno` is the single source of truth for ODNO comparisons."""

    def test_zero_padded_normalizes_to_raw(self) -> None:
        self.assertEqual(_norm_odno("0000041461"), "41461")
        self.assertEqual(_norm_odno("41461"), "41461")
        self.assertEqual(_norm_odno("  0000041461  "), "41461")

    def test_zero_preserved(self) -> None:
        # KIS won't emit "0", but defensively make sure "0" stays "0".
        self.assertEqual(_norm_odno("0"), "0")
        self.assertEqual(_norm_odno("00000"), "0")

    def test_empty_and_none(self) -> None:
        self.assertEqual(_norm_odno(""), "")
        self.assertEqual(_norm_odno(None), "")

    def test_idempotent(self) -> None:
        once = _norm_odno("0000041461")
        twice = _norm_odno(once)
        self.assertEqual(once, twice)


class TestTryNccsPaddingMismatch(unittest.TestCase):
    """nccs match must succeed across zero-padded ↔ raw ODNO."""

    def test_padded_request_matches_raw_row(self) -> None:
        adapter = _FakeAdapter(open_orders=[
            _make_open_order(odno="41467", symbol="AMD"),
        ])
        out = _try_nccs(adapter, "0000041467", market="NASD")
        self.assertIsNotNone(out["matched"])
        self.assertEqual(out["matched"].broker_order_id, "41467")
        self.assertIsNone(out["error"])

    def test_raw_request_matches_padded_row(self) -> None:
        # Defensive (unlikely in practice).
        adapter = _FakeAdapter(open_orders=[
            _make_open_order(odno="0000041467", symbol="AMD"),
        ])
        out = _try_nccs(adapter, "41467", market="NASD")
        self.assertIsNotNone(out["matched"])

    def test_unrelated_orders_do_not_match(self) -> None:
        adapter = _FakeAdapter(open_orders=[
            _make_open_order(odno="40840"),
            _make_open_order(odno="40867"),
        ])
        out = _try_nccs(adapter, "0000041467", market="NASD")
        self.assertIsNone(out["matched"])
        self.assertEqual(out["rows"], 2)


class TestTryCcnlPaddingMismatch(unittest.TestCase):
    """ccnl match must succeed across zero-padded ↔ raw ODNO."""

    def test_padded_request_matches_raw_row(self) -> None:
        adapter = _FakeAdapter(ccnl_rows=[
            {"odno": "41467", "pdno": "AMD",
             "ft_ord_qty3": "1", "ft_ccld_qty3": "1",
             "ft_ord_unpr3": "430.82000000",
             "ft_ccld_unpr3": "430.79500000",
             "sll_buy_dvsn_cd": "02"},
        ])
        out = _try_ccnl(adapter, "0000041467")
        self.assertIsNotNone(out["matched"])
        self.assertEqual(out["matched"]["pdno"], "AMD")

    def test_unrelated_history_does_not_match(self) -> None:
        adapter = _FakeAdapter(ccnl_rows=[
            {"odno": "40840", "pdno": "MSFT"},
            {"odno": "49652", "pdno": "TSLA"},
        ])
        out = _try_ccnl(adapter, "0000041467")
        self.assertIsNone(out["matched"])
        self.assertEqual(out["rows"], 2)


class TestEchoPollMatchesViaNccsThenCcnl(unittest.TestCase):
    """End-to-end: echo_poll uses nccs → ccnl, both go through _norm_odno."""

    def test_matches_via_nccs_with_padding_diff(self) -> None:
        adapter = _FakeAdapter(open_orders=[
            _make_open_order(odno="41471", symbol="INTC", qty_order=3,
                             qty_remaining=3),
        ])
        res = echo_poll(
            adapter, broker_order_id="0000041471",
            market="NASD", max_polls=2, interval_sec=0.0,
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["source"], "nccs")

    def test_falls_back_to_ccnl_with_padding_diff(self) -> None:
        adapter = _FakeAdapter(
            open_orders=[],
            ccnl_rows=[
                {"odno": "41471", "pdno": "INTC",
                 "ft_ord_qty3": "3", "ft_ccld_qty3": "3",
                 "ft_ord_unpr3": "107.80",
                 "ft_ccld_unpr3": "107.80",
                 "sll_buy_dvsn_cd": "02"},
            ],
        )
        res = echo_poll(
            adapter, broker_order_id="0000041471",
            market="NASD", max_polls=2, interval_sec=0.0,
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["source"], "ccnl")

    def test_no_match_when_truly_absent(self) -> None:
        adapter = _FakeAdapter(
            open_orders=[_make_open_order(odno="99999")],
            ccnl_rows=[{"odno": "88888"}],
        )
        res = echo_poll(
            adapter, broker_order_id="0000041471",
            market="NASD", max_polls=2, interval_sec=0.0,
        )
        self.assertFalse(res["matched"])
        self.assertIsNone(res["source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
