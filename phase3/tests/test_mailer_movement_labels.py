"""Report-only Top-N movement labels in the daily email (V — mail lens).

These tests pin the *mailer* side of the handoff in
``docs/CURSOR_HANDOFF_TOPN_MOVEMENT_MAIL_LABELS_20260605.md``:

* the movement section is inserted when ``movement_text`` is supplied;
* per-row inline badges appear only for noteworthy rows and only when the
  optional Movement* columns are present;
* the feature is strictly additive — a recos table WITHOUT movement columns
  renders exactly as before, and building the body never mutates the caller's
  DataFrame (the report-only / parity guarantee).
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PHASE3 = os.path.dirname(_THIS_DIR)
for p in (_PHASE3,):
    if p not in sys.path:
        sys.path.insert(0, p)

import mailer  # noqa: E402


class _StubHoldings:
    def get_pnl_summary(self):
        return {
            "total_value": 1000.0,
            "total_cost": 900.0,
            "total_pnl": 100.0,
            "pnl_pct": 11.11,
            "holdings_count": 3,
        }

    def get_cash_balance(self):
        return 500.0

    def get_total_deposited(self):
        return 1000.0


def _recos(with_labels: bool) -> pd.DataFrame:
    rows = [
        {"Ticker": "GLW", "Action": "BUY_NEW", "Shares": 2, "Price": 86.1,
         "Capital": 172.2, "Score": 93.2, "TargetPct": 5.0, "ActualPct": 0.0, "GapPct": 5.0},
        {"Ticker": "CSCO", "Action": "BUY_MORE", "Shares": 8, "Price": 125.0,
         "Capital": 1000.0, "Score": 94.0, "TargetPct": 5.0, "ActualPct": 3.0, "GapPct": 2.0},
        {"Ticker": "MU", "Action": "HOLD", "Shares": 0, "Price": 200.0,
         "Capital": 0.0, "Score": 97.0, "TargetPct": 5.0, "ActualPct": 5.0, "GapPct": 0.0},
    ]
    df = pd.DataFrame(rows)
    if with_labels:
        labels = {
            "GLW": ("RISING", "NEW_ENTRY,FAST_RISER", 3, 37, 13),
            "CSCO": ("SIDEWAYS", "CORE_STABLE", 1, 2, 3),
            "MU": ("SIDEWAYS", "", 0, -1, 0),  # unremarkable -> no badge
        }
        df["MovementLabel"] = df["Ticker"].map(lambda t: labels[t][0])
        df["MovementTags"] = df["Ticker"].map(lambda t: labels[t][1])
        df["RankDelta1d"] = df["Ticker"].map(lambda t: labels[t][2])
        df["RankDelta3d"] = df["Ticker"].map(lambda t: labels[t][3])
        df["RankDelta5d"] = df["Ticker"].map(lambda t: labels[t][4])
    return df


class MovementBadgeTest(unittest.TestCase):
    def test_no_metadata_no_badge(self):
        row = pd.Series({"Ticker": "AAA"})
        self.assertEqual(mailer._movement_badge(row), "")

    def test_rising_new_entry_badge(self):
        row = pd.Series({
            "MovementLabel": "RISING", "MovementTags": "NEW_ENTRY,FAST_RISER",
            "RankDelta1d": 3, "RankDelta3d": 37, "RankDelta5d": 13,
        })
        badge = mailer._movement_badge(row)
        self.assertIn("RISING", badge)
        self.assertIn("NEW_ENTRY", badge)
        self.assertIn("d3=+37", badge)

    def test_unremarkable_sideways_no_badge(self):
        row = pd.Series({
            "MovementLabel": "SIDEWAYS", "MovementTags": "",
            "RankDelta1d": 0, "RankDelta3d": -1, "RankDelta5d": 0,
        })
        self.assertEqual(mailer._movement_badge(row), "")

    def test_core_stable_sideways_shows_badge(self):
        row = pd.Series({
            "MovementLabel": "SIDEWAYS", "MovementTags": "CORE_STABLE",
            "RankDelta1d": 1, "RankDelta3d": 2, "RankDelta5d": 3,
        })
        badge = mailer._movement_badge(row)
        self.assertIn("CORE_STABLE", badge)

    def test_nan_deltas_render_as_na(self):
        row = pd.Series({
            "MovementLabel": "RISING", "MovementTags": "NEW_ENTRY",
            "RankDelta1d": float("nan"), "RankDelta3d": float("nan"),
            "RankDelta5d": float("nan"),
        })
        badge = mailer._movement_badge(row)
        self.assertIn("n/a", badge)


class BuildTriggerBodyTest(unittest.TestCase):
    def test_section_inserted_when_movement_text_present(self):
        body = mailer._build_trigger_body(
            triggers=["DAILY"], recos=_recos(with_labels=True), vix=15.0,
            regime="BULL", holdings_mgr=_StubHoldings(), health={},
            movement_text="\n[Top-N Movement Labels]\n  Rising/New:\n    GLW #20",
        )
        self.assertIn("[Top-N Movement Labels]", body)

    def test_inline_badges_on_hot_rows_only(self):
        body = mailer._build_trigger_body(
            triggers=["DAILY"], recos=_recos(with_labels=True), vix=15.0,
            regime="BULL", holdings_mgr=_StubHoldings(), health={},
        )
        # GLW is RISING+NEW_ENTRY -> badge present on its BUY row.
        glw_line = [ln for ln in body.splitlines() if "GLW" in ln and "shares" in ln]
        self.assertTrue(glw_line and "RISING" in glw_line[0])
        # MU is unremarkable SIDEWAYS -> HOLD row carries no movement badge
        # (the trailing "[5.0%/5.0%]" is the pre-existing gap_info, not a badge).
        mu_line = [ln for ln in body.splitlines() if "HOLD" in ln and "MU" in ln]
        self.assertTrue(mu_line)
        for kw in ("RISING", "FALLING", "SIDEWAYS", "CORE_STABLE", "d1=", "d3="):
            self.assertNotIn(kw, mu_line[0])

    def test_backward_compatible_without_movement_columns(self):
        # No Movement* columns and no movement_text: body builds, no badges.
        body = mailer._build_trigger_body(
            triggers=["DAILY"], recos=_recos(with_labels=False), vix=15.0,
            regime="BULL", holdings_mgr=_StubHoldings(), health={},
        )
        self.assertNotIn("[Top-N Movement Labels]", body)
        self.assertIn("GLW", body)
        self.assertNotIn("RISING", body)

    def test_preview_note_banner_rendered(self):
        body = mailer._build_trigger_body(
            triggers=["DAILY"], recos=_recos(with_labels=False), vix=15.0,
            regime="BULL", holdings_mgr=_StubHoldings(), health={},
            preview_note="*** NON-TRADING-DAY PREVIEW (dry-run) ***",
        )
        self.assertIn("NON-TRADING-DAY PREVIEW", body)
        # Banner sits up top, before the recommendation rows.
        self.assertLess(body.index("PREVIEW"), body.index("GLW"))

    def test_no_preview_note_no_banner(self):
        body = mailer._build_trigger_body(
            triggers=["DAILY"], recos=_recos(with_labels=False), vix=15.0,
            regime="BULL", holdings_mgr=_StubHoldings(), health={},
        )
        self.assertNotIn("PREVIEW", body)


class TestHtmlColorize(unittest.TestCase):
    """HTML alternative colours notable rank moves (up=red, down=blue) and
    leaves neutral labels (CORE_STABLE/SIDEWAYS) uncoloured."""

    def test_up_labels_and_marker_red(self):
        html = mailer._html_from_plain("  APA  new \u25b2  RISING FAST_RISER NEW_ENTRY")
        self.assertIn(mailer._UP_COLOR, html)
        # every up token wrapped in the up colour
        for tok in ("RISING", "FAST_RISER", "NEW_ENTRY", "\u25b2"):
            self.assertIn(
                f'color:{mailer._UP_COLOR};font-weight:bold">{tok}</span>', html)

    def test_down_labels_and_marker_blue(self):
        html = mailer._html_from_plain("  HPE  -8 \u25bc  FALLING")
        self.assertIn(
            f'color:{mailer._DOWN_COLOR};font-weight:bold">FALLING</span>', html)
        self.assertIn(
            f'color:{mailer._DOWN_COLOR};font-weight:bold">\u25bc</span>', html)

    def test_neutral_labels_uncoloured(self):
        html = mailer._html_from_plain("  MU  +3  CORE_STABLE")
        self.assertNotIn("CORE_STABLE</span>", html)
        self.assertIn("CORE_STABLE", html)
        self.assertNotIn(mailer._UP_COLOR, html)  # no ▲/up tokens here
        self.assertNotIn(mailer._DOWN_COLOR, html)

    def test_html_escapes_specials(self):
        html = mailer._html_from_plain("a < b & c > d")
        self.assertIn("&lt;", html)
        self.assertIn("&amp;", html)
        self.assertIn("&gt;", html)
        self.assertTrue(html.startswith("<html><body><pre"))

    def test_body_does_not_mutate_recos(self):
        df = _recos(with_labels=True)
        before_cols = list(df.columns)
        before_csv = df.to_csv(index=False)
        mailer._build_trigger_body(
            triggers=["DAILY"], recos=df, vix=15.0, regime="BULL",
            holdings_mgr=_StubHoldings(), health={}, movement_text="x",
        )
        self.assertEqual(list(df.columns), before_cols)
        self.assertEqual(df.to_csv(index=False), before_csv)


if __name__ == "__main__":
    unittest.main()
