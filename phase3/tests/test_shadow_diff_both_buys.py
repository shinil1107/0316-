"""Regression test for the ``Both BUY`` row in ``shadow_diff``.

Before this change, ``compare_recommendations`` exposed only
``shadow_only_buys`` / ``live_only_buys`` and the email section never
listed the tickers both signals wanted to buy on the same day. The
overlap count was already in the header (``Top-N Overlap: x/y``), but
operators could not see which specific tickers were in that overlap.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

_THIS = Path(__file__).resolve()
_PHASE3 = _THIS.parent.parent
_ROOT = _PHASE3.parent
for _p in (str(_PHASE3), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import shadow_diff  # noqa: E402


def _scores(rows):
    return pd.DataFrame(rows, columns=["Ticker", "Score"])


def _recos(rows):
    return pd.DataFrame(rows, columns=["Ticker", "Action", "Score"])


class TestBothBuysSurface(unittest.TestCase):
    def test_both_buys_populated_with_live_and_shadow_scores(self):
        live_scores = _scores([("AAA", 80.0), ("BBB", 75.0), ("CCC", 70.0)])
        shadow_scores = _scores([("AAA", 78.0), ("BBB", 76.0), ("DDD", 72.0)])
        live_recos = _recos([
            ("AAA", "BUY_NEW", 80.0),
            ("BBB", "BUY_NEW", 75.0),
            ("CCC", "BUY_NEW", 70.0),
        ])
        shadow_recos = _recos([
            ("AAA", "BUY_NEW", 78.0),
            ("BBB", "BUY_NEW", 76.0),
            ("DDD", "BUY_NEW", 72.0),
        ])

        diff = shadow_diff.compare_recommendations(
            live_scores, shadow_scores, live_recos, shadow_recos,
            label="P11_FUNDB_ANCHOR", day_number=1, duration_days=30,
            top_n=3,
        )

        tickers = [b["ticker"] for b in diff["both_buys"]]
        self.assertEqual(tickers, ["AAA", "BBB"])
        self.assertEqual(diff["both_buy_count"], 2)

        aaa = next(b for b in diff["both_buys"] if b["ticker"] == "AAA")
        self.assertAlmostEqual(aaa["live_score"], 80.0)
        self.assertAlmostEqual(aaa["shadow_score"], 78.0)

        only_shadow = [b["ticker"] for b in diff["shadow_only_buys"]]
        only_live = [b["ticker"] for b in diff["live_only_buys"]]
        self.assertEqual(only_shadow, ["DDD"])
        self.assertEqual(only_live, ["CCC"])

    def test_email_section_lists_both_buy_row(self):
        diff = {
            "label": "L", "day_number": 1, "duration_days": 30,
            "topn_overlap_count": 2, "topn_union_count": 4,
            "topn_overlap_rate": 0.5,
            "both_buys": [
                {"ticker": "AAA", "live_score": 80.0, "shadow_score": 78.0},
                {"ticker": "BBB", "live_score": 75.0, "shadow_score": 76.0},
            ],
            "shadow_only_buys": [{"ticker": "DDD", "score": 72.0}],
            "live_only_buys": [{"ticker": "CCC", "score": 70.0}],
            "rank_correlation": 0.92,
            "top_n": 3,
        }
        out = shadow_diff.format_email_section(diff)
        self.assertIn("Both BUY:", out)
        self.assertIn("AAA (L+80.0/S+78.0)", out)
        self.assertIn("BBB (L+75.0/S+76.0)", out)
        # Both BUY appears before the existing only-side rows.
        self.assertLess(out.index("Both BUY:"), out.index("Shadow-only BUY:"))
        self.assertLess(out.index("Both BUY:"), out.index("Live-only BUY:"))

    def test_no_overlap_renders_none(self):
        diff = {
            "label": "L", "day_number": 1, "duration_days": 30,
            "topn_overlap_count": 0, "topn_union_count": 4,
            "topn_overlap_rate": 0.0,
            "both_buys": [],
            "shadow_only_buys": [{"ticker": "DDD", "score": 72.0}],
            "live_only_buys": [{"ticker": "CCC", "score": 70.0}],
            "rank_correlation": None,
            "top_n": 3,
        }
        out = shadow_diff.format_email_section(diff)
        self.assertIn("Both BUY:        (none)", out)

    def test_more_than_five_overlaps_get_truncated(self):
        both = [{"ticker": f"T{i}", "live_score": 50.0 + i, "shadow_score": 50.0 + i}
                for i in range(7)]
        diff = {
            "label": "L", "day_number": 1, "duration_days": 30,
            "topn_overlap_count": 7, "topn_union_count": 10,
            "topn_overlap_rate": 0.7,
            "both_buys": both,
            "shadow_only_buys": [],
            "live_only_buys": [],
            "rank_correlation": 0.9,
            "top_n": 10,
        }
        out = shadow_diff.format_email_section(diff)
        self.assertIn("+2 more", out)


class TestBothTopnSurface(unittest.TestCase):
    """``Both Top-N`` is a superset of ``Both BUY`` — it surfaces tickers
    that both signals rank in their top-N pool even if one side blocks
    the BUY (sector cap, buy_grace_days, daily-limit, etc.)."""

    def test_both_topn_includes_tickers_blocked_from_buy_on_one_side(self):
        # Both signals rank AAPL/MSFT/CNC in top-3. AAPL/MSFT both BUY,
        # but the live side does NOT issue a BUY for CNC (simulating a
        # buy-grace/sector-cap block). Both Top-N must still include CNC.
        live_scores = _scores([("AAPL", 80.0), ("MSFT", 78.0), ("CNC", 65.0)])
        shadow_scores = _scores([("AAPL", 79.0), ("MSFT", 77.0), ("CNC", 74.9)])
        live_recos = _recos([
            ("AAPL", "BUY_NEW", 80.0),
            ("MSFT", "BUY_NEW", 78.0),
        ])
        shadow_recos = _recos([
            ("AAPL", "BUY_NEW", 79.0),
            ("MSFT", "BUY_NEW", 77.0),
            ("CNC", "BUY_NEW", 74.9),
        ])
        diff = shadow_diff.compare_recommendations(
            live_scores, shadow_scores, live_recos, shadow_recos,
            label="L", day_number=1, duration_days=30, top_n=3,
        )

        both_topn_tickers = [b["ticker"] for b in diff["both_topn"]]
        self.assertCountEqual(both_topn_tickers, ["AAPL", "MSFT", "CNC"])
        both_buy_tickers = [b["ticker"] for b in diff["both_buys"]]
        self.assertCountEqual(both_buy_tickers, ["AAPL", "MSFT"])

        # Both Top-N is sorted by mean(live, shadow) score descending,
        # so the strongest shared conviction appears first.
        self.assertEqual(both_topn_tickers[0], "AAPL")
        cnc = next(b for b in diff["both_topn"] if b["ticker"] == "CNC")
        self.assertAlmostEqual(cnc["live_score"], 65.0)
        self.assertAlmostEqual(cnc["shadow_score"], 74.9)

    def test_email_section_lists_both_topn_above_both_buy(self):
        diff = {
            "label": "L", "day_number": 1, "duration_days": 30,
            "topn_overlap_count": 3, "topn_union_count": 5,
            "topn_overlap_rate": 0.6,
            "both_topn": [
                {"ticker": "AAPL", "live_score": 80.0, "shadow_score": 79.0},
                {"ticker": "MSFT", "live_score": 78.0, "shadow_score": 77.0},
                {"ticker": "CNC",  "live_score": 65.0, "shadow_score": 74.9},
            ],
            "both_buys": [
                {"ticker": "AAPL", "live_score": 80.0, "shadow_score": 79.0},
                {"ticker": "MSFT", "live_score": 78.0, "shadow_score": 77.0},
            ],
            "shadow_only_buys": [{"ticker": "CNC", "score": 74.9}],
            "live_only_buys": [],
            "rank_correlation": 0.92,
            "top_n": 3,
        }
        out = shadow_diff.format_email_section(diff)
        self.assertIn("Both Top-N:", out)
        self.assertIn("AAPL (L+80.0/S+79.0)", out)
        self.assertIn("CNC (L+65.0/S+74.9)", out)
        # Both Top-N appears between the Overlap header and Both BUY row.
        self.assertLess(out.index("Top-N Overlap:"), out.index("Both Top-N:"))
        self.assertLess(out.index("Both Top-N:"), out.index("Both BUY:"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
