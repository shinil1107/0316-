"""R8-B — ccnl row → BrokerOrderState classifier acceptance tests.

Covers R8 §4 acceptance list:

    full fill row -> FILLED
    partial fill row -> PARTIALLY_FILLED
    zero fill + nccs_qty>0 -> OPEN_OR_PENDING
    cancel row sample -> CANCELLED
    reject reason row -> REJECTED
    missing filled qty + no reliable remaining -> UNKNOWN
    ccnl_zero + position_delta>0 conflict remains UNKNOWN

Plus the R8-day-1 finding: cancel sibling row contract.
Plus alias-field coverage.

Test rows are synthetic but follow the KIS overseas ccnl wire shape we
observed during R7-B and R8-day-1 probes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.order_state import (
    BrokerOrderState,
    OrderState,
    classify_ccnl_row,
    classify_from_full_ccnl,
)


# ──────────────────────────────────────────────────────────────────────
# Row fixtures (mirror real KIS shapes observed in R6 / R7-B / R8-day-1)
# ──────────────────────────────────────────────────────────────────────
def _full_fill_row(odno="41461", qty=4, fill_price=37.815, limit=37.82) -> dict:
    return {
        "odno": odno,
        "orgn_odno": "",
        "pdno": "APA",
        "sll_buy_dvsn_cd_name": "매수",
        "ft_ord_qty": str(qty),
        "ft_ccld_qty": str(qty),
        "nccs_qty": "0",
        "ft_ord_unpr3": f"{limit:.8f}",
        "ft_ccld_unpr3": f"{fill_price:.8f}",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": "보통",
        "prcs_stat_name": "",
        "rjct_rson_name": "",
    }


def _partial_fill_row(odno="41499", ordered=5, filled=2,
                       fill_price=10.00, limit=10.00) -> dict:
    return {
        "odno": odno,
        "orgn_odno": "",
        "pdno": "AMD",
        "sll_buy_dvsn_cd_name": "매수",
        "ft_ord_qty": str(ordered),
        "ft_ccld_qty": str(filled),
        "nccs_qty": str(ordered - filled),
        "ft_ord_unpr3": f"{limit:.8f}",
        "ft_ccld_unpr3": f"{fill_price:.8f}",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": "보통",
        "prcs_stat_name": "",
        "rjct_rson_name": "",
    }


def _open_row(odno="42031", limit=18.85) -> dict:
    """Mirrors the real R7-B probe row at T+0."""
    return {
        "odno": odno,
        "orgn_odno": "",
        "pdno": "APA",
        "sll_buy_dvsn_cd_name": "매수",
        "ft_ord_qty": "1",
        "ft_ccld_qty": "0",
        "nccs_qty": "1",
        "ft_ord_unpr3": f"{limit:.8f}",
        "ft_ccld_unpr3": "0.00000000",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": "보통",
        "prcs_stat_name": "",
        "rjct_rson_name": "",
    }


def _original_row_post_cancel(odno="42031") -> dict:
    """Original row after the cancel landed — nccs_qty went 1→0 but the
    in-place cancel marker did NOT flip. Mirrors the real R8-day-1
    state of ODNO 42031 at T+3s post cancel."""
    r = _open_row(odno=odno)
    r["nccs_qty"] = "0"
    return r


def _cancel_sibling_row(*, new_odno="42493", orgn_odno="42031") -> dict:
    """Sibling cancel-instruction row that KIS appends to ccnl. Mirrors
    the real R8-day-1 cancel row body."""
    return {
        "odno": new_odno,
        "orgn_odno": orgn_odno,
        "pdno": "APA",
        "sll_buy_dvsn_cd_name": "매수취소",
        "ft_ord_qty": "1",
        "ft_ccld_qty": "0",
        "nccs_qty": "0",
        "ft_ord_unpr3": "0.00000000",
        "ft_ccld_unpr3": "0.00000000",
        "rvse_cncl_dvsn": "02",
        "rvse_cncl_dvsn_name": "취소",
        "prcs_stat_name": "",
        "rjct_rson_name": "",
    }


def _rejected_row(odno="49000", reason="잔고부족") -> dict:
    return {
        "odno": odno,
        "orgn_odno": "",
        "pdno": "APA",
        "sll_buy_dvsn_cd_name": "매수",
        "ft_ord_qty": "1",
        "ft_ccld_qty": "0",
        "nccs_qty": "0",
        "ft_ord_unpr3": "18.85000000",
        "ft_ccld_unpr3": "0.00000000",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": "보통",
        "prcs_stat_name": "거부",
        "rjct_rson_name": reason,
    }


def _ambiguous_row(odno="49100") -> dict:
    return {
        "odno": odno,
        "orgn_odno": "",
        "pdno": "APA",
        "sll_buy_dvsn_cd_name": "매수",
        "ft_ord_qty": "",
        "ft_ccld_qty": "",
        "nccs_qty": "",
        "ft_ord_unpr3": "18.85000000",
        "ft_ccld_unpr3": "0.00000000",
        "rvse_cncl_dvsn": "00",
        "rvse_cncl_dvsn_name": "보통",
        "prcs_stat_name": "",
        "rjct_rson_name": "",
    }


# ──────────────────────────────────────────────────────────────────────
# R8 §4 acceptance list
# ──────────────────────────────────────────────────────────────────────
class TestPaperInterpretationContract(unittest.TestCase):
    def test_full_fill_row_is_FILLED(self) -> None:
        row = _full_fill_row()
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.FILLED)
        self.assertEqual(bs.ordered_qty, 4.0)
        self.assertEqual(bs.filled_qty, 4.0)
        self.assertEqual(bs.avg_fill_price, 37.815)
        self.assertEqual(bs.symbol, "APA")
        self.assertEqual(bs.normalized_odno, "41461")
        self.assertEqual(bs.cancel_row_odno, None)
        self.assertEqual(bs.source, "ccnl")

    def test_partial_fill_row_is_PARTIALLY_FILLED(self) -> None:
        row = _partial_fill_row(ordered=5, filled=2)
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(bs.ordered_qty, 5.0)
        self.assertEqual(bs.filled_qty, 2.0)
        self.assertEqual(bs.remaining_qty, 3.0)

    def test_zero_fill_plus_nccs_qty_is_OPEN_OR_PENDING(self) -> None:
        row = _open_row()
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.OPEN_OR_PENDING)
        self.assertEqual(bs.filled_qty, 0.0)
        self.assertEqual(bs.remaining_qty, 1.0)
        self.assertIsNone(bs.avg_fill_price)
        self.assertEqual(bs.limit_price, 18.85)

    def test_reject_reason_is_REJECTED(self) -> None:
        row = _rejected_row(reason="잔고부족")
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.REJECTED)
        self.assertIn("잔고부족", bs.note)

    def test_ambiguous_missing_fields_is_UNKNOWN(self) -> None:
        row = _ambiguous_row()
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.UNKNOWN)
        self.assertIn("ambiguous", bs.note)

    def test_ccnl_zero_vs_position_delta_remains_UNKNOWN(self) -> None:
        """R5B-P2.3 conflict rule kept under R8-B parser."""
        row = _open_row()
        # zero fill in ccnl, but position moved by +1 share
        bs = classify_ccnl_row(row, position_delta=1.0)
        self.assertEqual(bs.state, OrderState.UNKNOWN)
        self.assertIn("position moved", bs.note)


# ──────────────────────────────────────────────────────────────────────
# Cancel sibling contract (R8-day-1 finding)
# ──────────────────────────────────────────────────────────────────────
class TestCancelSiblingContract(unittest.TestCase):
    def test_cancel_sibling_row_makes_target_CANCELLED(self) -> None:
        target = _original_row_post_cancel(odno="42031")
        sibling = _cancel_sibling_row(new_odno="42493", orgn_odno="42031")
        rows = [target, sibling]
        bs = classify_ccnl_row(target, all_rows=rows)
        self.assertEqual(bs.state, OrderState.CANCELLED)
        self.assertEqual(bs.cancel_row_odno, "42493")
        self.assertIn("42493", bs.note)

    def test_cancel_sibling_with_padded_orgn_odno_still_matches(self) -> None:
        """Future-proofing — KIS could surface orgn_odno padded."""
        target = _original_row_post_cancel(odno="42031")
        sibling = _cancel_sibling_row(orgn_odno="0000042031")  # padded
        sibling["odno"] = "42493"
        rows = [target, sibling]
        bs = classify_ccnl_row(target, all_rows=rows)
        self.assertEqual(bs.state, OrderState.CANCELLED)

    def test_cancel_sibling_with_position_moved_is_UNKNOWN(self) -> None:
        """Partial-fill-then-cancel grey area — never auto-classify."""
        target = _original_row_post_cancel(odno="42031")
        sibling = _cancel_sibling_row(orgn_odno="42031")
        rows = [target, sibling]
        bs = classify_ccnl_row(target, all_rows=rows, position_delta=2.0)
        self.assertEqual(bs.state, OrderState.UNKNOWN)
        self.assertIn("partial-fill-before-cancel", bs.note)
        self.assertEqual(bs.cancel_row_odno, "42493")

    def test_cancel_sibling_with_only_korean_name_marker(self) -> None:
        """Sibling detection should accept any of: rvse_cncl_dvsn=='02',
        rvse_cncl_dvsn_name endswith '취소', or sll_buy_dvsn_cd_name
        endswith '취소'."""
        target = _original_row_post_cancel(odno="55001")
        sibling = {
            "odno": "55002",
            "orgn_odno": "55001",
            "rvse_cncl_dvsn": "",         # missing code
            "rvse_cncl_dvsn_name": "",
            "sll_buy_dvsn_cd_name": "매도취소",
        }
        bs = classify_ccnl_row(target, all_rows=[target, sibling])
        self.assertEqual(bs.state, OrderState.CANCELLED)
        self.assertEqual(bs.cancel_row_odno, "55002")

    def test_no_cancel_sibling_and_remaining_zero_is_UNKNOWN(self) -> None:
        """If the original row says zero filled AND zero remaining AND
        there is no cancel sibling, the order's economic status is
        ambiguous — classify UNKNOWN, not CANCELLED."""
        target = _original_row_post_cancel(odno="42031")
        bs = classify_ccnl_row(target, all_rows=[target])
        self.assertEqual(bs.state, OrderState.UNKNOWN)


# ──────────────────────────────────────────────────────────────────────
# Field-alias coverage
# ──────────────────────────────────────────────────────────────────────
class TestFieldAliases(unittest.TestCase):
    def test_ft_ord_qty3_filled_qty3_aliases(self) -> None:
        row = {
            "odno": "44000", "pdno": "AMD",
            "ft_ord_qty3": "10",      # alias of ord_qty
            "ft_ccld_qty3": "10",     # alias of filled
            "nccs_qty": "0",
            "ft_ccld_unpr3": "100.00",
            "ft_ord_unpr3": "100.00",
        }
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.FILLED)
        self.assertEqual(bs.ordered_qty, 10.0)
        self.assertEqual(bs.filled_qty, 10.0)

    def test_tot_ccld_qty_alias(self) -> None:
        row = {
            "odno": "44100", "pdno": "AMD",
            "ord_qty": "3",
            "tot_ccld_qty": "1",      # partial via legacy alias
            "rmn_qty": "2",
        }
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(bs.remaining_qty, 2.0)

    def test_ord_psbl_qty_remaining_alias(self) -> None:
        row = {
            "odno": "44200", "pdno": "AMD",
            "ord_qty": "1",
            "ccld_qty": "0",
            "ord_psbl_qty": "1",      # alias of nccs_qty / rmn_qty
            "ord_unpr": "5.00",
        }
        bs = classify_ccnl_row(row)
        self.assertEqual(bs.state, OrderState.OPEN_OR_PENDING)
        self.assertEqual(bs.remaining_qty, 1.0)
        self.assertEqual(bs.limit_price, 5.0)


# ──────────────────────────────────────────────────────────────────────
# Full-ccnl convenience
# ──────────────────────────────────────────────────────────────────────
class TestClassifyFromFullCcnl(unittest.TestCase):
    def test_padded_target_odno_matches_raw_row(self) -> None:
        rows = [_full_fill_row(odno="41461")]
        bs = classify_from_full_ccnl(rows, target_odno="0000041461")
        self.assertEqual(bs.state, OrderState.FILLED)
        self.assertEqual(bs.normalized_odno, "41461")

    def test_missing_target_is_UNKNOWN_not_raise(self) -> None:
        rows = [_full_fill_row(odno="11111")]
        bs = classify_from_full_ccnl(rows, target_odno="0000099999")
        self.assertEqual(bs.state, OrderState.UNKNOWN)
        self.assertEqual(bs.normalized_odno, "99999")
        self.assertIn("not in ccnl", bs.note)

    def test_real_R8day1_cancel_shape_end_to_end(self) -> None:
        """Replays the actual ccnl shape we captured during the R8
        day-1 acceptance test (ODNO 42031 → cancel ODNO 42493)."""
        target = _original_row_post_cancel(odno="42031")
        sibling = _cancel_sibling_row(new_odno="42493", orgn_odno="42031")
        rows = [target, sibling]
        bs = classify_from_full_ccnl(rows, target_odno="0000042031")
        self.assertEqual(bs.state, OrderState.CANCELLED)
        self.assertEqual(bs.cancel_row_odno, "42493")


if __name__ == "__main__":
    unittest.main(verbosity=2)
