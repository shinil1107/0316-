"""R10E — t10_applicator manage-outcome fallback for ccnl_missing.

In 20260519_220825_daily the manage loop saw DOW (NYSE) fill at
37.85 for 3 shares and wrote that to ``autotrade_daily_report.json``
as ``final_state=filled, qty_filled=3, avg_fill_price=37.85``. Two
minutes later the operator ran T10 apply and KIS paper no longer
returned that ODNO in ``inquire-ccnl`` — status=ccnl_missing — so
``_resolve_against_ccnl`` aborted the entire apply path.

R10E introduces an opt-out fallback: when the manage outcome is a
clean exact-qty FILLED, the resolution borrows qty + price from the
outcome instead of aborting. Conservatism guards:

  - final_state must be 'filled' (no partial, no unknown)
  - outcome.qty_filled must EXACTLY equal recommendations.csv Shares
  - outcome.avg_fill_price must be > 0
  - operator can disable via ``--no-outcome-fallback``

This test file pins down every branch of that fallback so the
guards cannot regress.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import t10_applicator as ta


def _make_recos(rows):
    return pd.DataFrame(rows)


def _submitted(rid, ticker, qty, broker_order_id):
    return {
        "rec_row_id": rid,
        "ticker": ticker,
        "qty_intended": qty,
        "broker_order_id": broker_order_id,
        "client_order_id": f"co-rid-{rid}-B-{qty}-{ticker}",
        "autotrade_run_id": "at-r10e",
        "state": "submitted",
    }


def _outcome_fb(rid, qty, price, final_state="filled"):
    return ta._OutcomeFallback(
        rec_row_id=rid,
        final_state=final_state,
        qty_filled=qty,
        avg_fill_price=price,
        broker_order_id=f"odno-{rid}",
        client_order_id=f"co-rid-{rid}-B-{int(qty)}-X",
    )


# Convenient recos fixture matching 20260519 (DOW + JBL + MRNA).
_RECOS = _make_recos([
    {"RecRowId": 73, "Ticker": "MRNA", "Action": "BUY_MORE",
     "Shares": 2, "Score": 82.68, "Regime": "SIDE", "Rank": 2},
    {"RecRowId": 75, "Ticker": "JBL",  "Action": "BUY_MORE",
     "Shares": 2, "Score": 80.54, "Regime": "SIDE", "Rank": 12},
    {"RecRowId": 74, "Ticker": "DOW",  "Action": "BUY_NEW",
     "Shares": 3, "Score": 79.88, "Regime": "SIDE", "Rank": 15},
])


class TestOutcomeFallbackResolution(unittest.TestCase):

    def test_pre_r10e_baseline_aborts_on_ccnl_missing(self):
        """Without the fallback (allow_outcome_fallback=False) the
        old behaviour stays: ccnl_missing → abort_reason."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={74: _outcome_fb(74, 3, 37.85)},
            allow_outcome_fallback=False,
        )
        r = resolutions[0]
        self.assertFalse(r.matched)
        self.assertIsNotNone(r.abort_reason)
        self.assertIn("not found in inquire-ccnl", r.abort_reason)

    def test_clean_outcome_replaces_ccnl_missing(self):
        """20260519's exact scenario: DOW ODNO missing from ccnl,
        manage outcome FILLED at qty=3, avg=37.85. Resolution now
        matches with the outcome's qty + price."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={74: _outcome_fb(74, 3, 37.85)},
            allow_outcome_fallback=True,
        )
        r = resolutions[0]
        self.assertTrue(r.matched)
        self.assertIsNone(r.abort_reason)
        self.assertEqual(r.filled_qty, 3.0)
        self.assertEqual(r.filled_price, 37.85)
        self.assertIn("manage outcome fallback", r.note)

    def test_partial_outcome_does_not_fallback(self):
        """Conservatism: a partial fill outcome must NOT cover for
        ccnl_missing — operator action required."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={
                74: _outcome_fb(74, 2, 37.85,
                                  final_state="partially_filled"),
            },
            allow_outcome_fallback=True,
        )
        self.assertFalse(resolutions[0].matched)
        self.assertIn("not found", resolutions[0].abort_reason or "")

    def test_unknown_outcome_does_not_fallback(self):
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={
                74: _outcome_fb(74, 3, 37.85, final_state="unknown"),
            },
            allow_outcome_fallback=True,
        )
        self.assertFalse(resolutions[0].matched)

    def test_qty_mismatch_does_not_fallback(self):
        """Outcome says 2 filled but reco says 3 expected — we must
        not silently bless a quantity mismatch."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={74: _outcome_fb(74, 2, 37.85)},
            allow_outcome_fallback=True,
        )
        self.assertFalse(resolutions[0].matched)

    def test_zero_price_does_not_fallback(self):
        """avg_fill_price must be > 0 for fallback to fire."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
            outcome_fallbacks={74: _outcome_fb(74, 3, 0.0)},
            allow_outcome_fallback=True,
        )
        self.assertFalse(resolutions[0].matched)

    def test_ccnl_present_with_zero_fill_uses_outcome(self):
        """ccnl_row IS there but filled_qty=0 (broker shows the
        order existed). When manage outcome agrees on a clean fill,
        override the ccnl zero."""
        ccnl_row = {
            "odno": "0000035625",
            "pdno": "DOW",
            "ft_ord_qty": "3",
            "ft_ccld_qty": "0",
            "ft_ccld_unpr3": "0.00",
        }
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[ccnl_row],
            outcome_fallbacks={74: _outcome_fb(74, 3, 37.85)},
            allow_outcome_fallback=True,
        )
        r = resolutions[0]
        self.assertTrue(r.matched)
        self.assertEqual(r.filled_qty, 3.0)
        self.assertEqual(r.filled_price, 37.85)
        self.assertIn("manage outcome", r.note)

    def test_ccnl_full_fill_ignores_fallback(self):
        """When ccnl already shows a full fill, the fallback path
        is irrelevant. The ccnl row stays the source of truth."""
        ccnl_row = {
            "odno": "0000035625",
            "pdno": "DOW",
            "ft_ord_qty": "3",
            "ft_ccld_qty": "3",
            "ft_ccld_unpr3": "37.85",
        }
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[ccnl_row],
            outcome_fallbacks={74: _outcome_fb(74, 3, 99.99)},
            allow_outcome_fallback=True,
        )
        r = resolutions[0]
        self.assertTrue(r.matched)
        # ccnl wins because there's a real ccnl row.
        self.assertEqual(r.filled_qty, 3.0)
        self.assertEqual(r.filled_price, 37.85)
        self.assertEqual(r.note, "")

    def test_no_fallback_dict_means_no_fallback(self):
        """Backwards-compat: callers that pass nothing for the
        fallback dict get the pre-R10E behaviour."""
        submitted = [_submitted(74, "DOW", 3, "0000035625")]
        resolutions = ta._resolve_against_ccnl(
            submitted, _RECOS, ccnl_rows=[],
        )
        self.assertFalse(resolutions[0].matched)


class TestOutcomeFallbackLoader(unittest.TestCase):

    def test_loads_clean_fills_from_daily_report(self):
        td = tempfile.mkdtemp(prefix="r10e_fb_")
        run_dir = Path(td)
        (run_dir / "autotrade_daily_report.json").write_text(
            json.dumps({
                "schema_version": "autotrade_daily_report/v1",
                "outcomes": [
                    {
                        "client_order_id": "co-rid-73-B-2-MRNA",
                        "ticker": "MRNA", "qty_intended": 2,
                        "qty_filled": 2.0, "avg_fill_price": 46.095,
                        "final_state": "filled",
                        "last_broker_order_id": "0000035623",
                    },
                    {
                        "client_order_id": "co-rid-74-B-3-DOW",
                        "ticker": "DOW", "qty_intended": 3,
                        "qty_filled": 3.0, "avg_fill_price": 37.85,
                        "final_state": "filled",
                        "last_broker_order_id": "0000035625",
                    },
                ],
            }),
            encoding="utf-8",
        )
        fbs = ta._load_outcome_fallbacks(run_dir)
        self.assertEqual(set(fbs.keys()), {73, 74})
        self.assertEqual(fbs[74].qty_filled, 3.0)
        self.assertEqual(fbs[74].avg_fill_price, 37.85)

    def test_missing_report_returns_empty_dict(self):
        td = tempfile.mkdtemp(prefix="r10e_fb_empty_")
        self.assertEqual(ta._load_outcome_fallbacks(Path(td)), {})

    def test_corrupt_report_returns_empty_dict(self):
        td = tempfile.mkdtemp(prefix="r10e_fb_bad_")
        run_dir = Path(td)
        (run_dir / "autotrade_daily_report.json").write_text(
            "{ not valid json", encoding="utf-8")
        self.assertEqual(ta._load_outcome_fallbacks(run_dir), {})

    def test_outcomes_missing_client_order_id_are_skipped(self):
        td = tempfile.mkdtemp(prefix="r10e_fb_skip_")
        run_dir = Path(td)
        (run_dir / "autotrade_daily_report.json").write_text(
            json.dumps({"outcomes": [{"final_state": "filled"}]}),
            encoding="utf-8",
        )
        self.assertEqual(ta._load_outcome_fallbacks(run_dir), {})


if __name__ == "__main__":
    unittest.main()
