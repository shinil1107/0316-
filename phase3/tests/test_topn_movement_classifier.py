from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from phase3.topn_movement_classifier import (
    MovementConfig,
    classify_topn_movement,
    discover_recent_score_csvs,
    format_movement_email_section,
    load_score_snapshots,
)


def _scores(date: str, ranked: list[str], *, regime: str = "BULL", run_id: str = "") -> pd.DataFrame:
    rows = []
    n = len(ranked)
    for i, ticker in enumerate(ranked):
        rows.append(
            {
                "RunId": run_id or f"{date}_daily",
                "ScoringDate": date,
                "Regime": regime,
                "Ticker": ticker,
                "Score": float(n - i),
                "Price": 100.0 + i,
            }
        )
    return pd.DataFrame(rows)


def _ranked_with(overrides: dict[str, int], n: int = 60) -> list[str]:
    slots: list[str | None] = [None] * n
    for ticker, rank in overrides.items():
        slots[rank - 1] = ticker
    filler_i = 1
    for i in range(n):
        if slots[i] is None:
            while f"T{filler_i:03d}" in overrides:
                filler_i += 1
            slots[i] = f"T{filler_i:03d}"
            filler_i += 1
    return [str(x) for x in slots]


class TopNMovementClassifierTest(unittest.TestCase):
    def test_new_fast_riser_is_labeled_for_topn_entry(self) -> None:
        cfg = MovementConfig(top_n=5)
        hist = [
            _scores("2026-06-01", _ranked_with({"DELL": 55, "HPE": 42})),
            _scores("2026-06-02", _ranked_with({"DELL": 52, "HPE": 39})),
            _scores("2026-06-03", _ranked_with({"DELL": 50, "HPE": 38})),
        ]
        today = _scores("2026-06-04", _ranked_with({"DELL": 4, "HPE": 3}))

        out = classify_topn_movement(today, hist, config=cfg)
        dell = out[out["ticker"].eq("DELL")].iloc[0]
        hpe = out[out["ticker"].eq("HPE")].iloc[0]

        self.assertEqual(dell["primary_label"], "RISING")
        self.assertIn("FAST_RISER", dell["tags"])
        self.assertIn("NEW_ENTRY", dell["tags"])
        self.assertEqual(dell["delta_rank_3d"], 51.0)
        self.assertEqual(hpe["primary_label"], "RISING")
        self.assertIn("NEW_ENTRY", hpe["tags"])

    def test_target_tickers_can_label_portfolio_names_outside_topn(self) -> None:
        cfg = MovementConfig(top_n=5)
        hist = [
            _scores("2026-06-01", _ranked_with({"AAPL": 2})),
            _scores("2026-06-02", _ranked_with({"AAPL": 10})),
            _scores("2026-06-03", _ranked_with({"AAPL": 18})),
        ]
        today = _scores("2026-06-04", _ranked_with({"AAPL": 35}))

        out = classify_topn_movement(today, hist, config=cfg, target_tickers=["AAPL"])
        row = out.iloc[0]

        self.assertFalse(row["in_topn_today"])
        self.assertEqual(row["primary_label"], "FALLING")
        self.assertEqual(row["delta_rank_3d"], -33.0)

    def test_core_stable_and_regime_switch_tags(self) -> None:
        cfg = MovementConfig(top_n=5)
        hist = []
        for i in range(1, 10):
            hist.append(
                _scores(
                    f"2026-06-{i:02d}",
                    _ranked_with({"MSFT": 3}, n=30),
                    regime="SIDE" if i == 9 else "BULL",
                )
            )
        today = _scores("2026-06-10", _ranked_with({"MSFT": 3}, n=30), regime="BULL")

        out = classify_topn_movement(today, hist, config=cfg, target_tickers=["MSFT"])
        row = out.iloc[0]

        self.assertEqual(row["primary_label"], "SIDEWAYS")
        self.assertIn("CORE_STABLE", row["tags"])
        self.assertIn("REGIME_SWITCHED", row["tags"])
        self.assertEqual(row["confidence"], "medium")

    def test_loader_discovery_and_email_format(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "daily_runs"
            root.mkdir()
            for day in ("20260601_070000_daily", "20260602_070000_daily"):
                run_dir = root / day
                run_dir.mkdir()
                _scores(day[:4] + "-" + day[4:6] + "-" + day[6:8], _ranked_with({"DELL": 10})).to_csv(
                    run_dir / "scores.csv", index=False
                )
            shadow_dir = root / "20260602_070000_shadow"
            shadow_dir.mkdir()
            _scores("2026-06-02", _ranked_with({"DELL": 1})).to_csv(shadow_dir / "scores.csv", index=False)

            paths = discover_recent_score_csvs(root, limit=5)
            self.assertEqual(len(paths), 2)
            snaps = load_score_snapshots(paths)
            out = classify_topn_movement(snaps[-1], snaps[:-1], config=MovementConfig(top_n=5))
            text = format_movement_email_section(out)

            self.assertIn("[Top-N Movement Labels]", text)
            self.assertIn("Rising/New", text)


if __name__ == "__main__":
    unittest.main()
