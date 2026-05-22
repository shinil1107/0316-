"""Excel-based holdings state manager.

Sheets in holdings_log.xlsx:
  Current         — live portfolio positions
  History         — all buy/sell actions
  DailyLog        — daily trigger check log
  Recommendations — latest buy/sell recommendations
  CashLedger      — cash inflow/outflow ledger
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


CURRENT_COLS = [
    "Ticker", "BuyDate", "BuyPrice", "Shares", "CurrentPrice",
    "PnL_Pct", "Weight", "MarketValue", "Status",
    # D1.6 — Track D dynamic-exit support fields.  Additive only; old
    # holdings_log.xlsx files are auto-migrated by ``_ensure_current_schema``.
    # ``BuyDate`` and ``BuyPrice`` are reused as the snapshot's
    # ``entry_date`` / ``entry_price`` — no duplicate columns needed.
    "EntryScore", "EntryRank", "EntryRegime", "PeakPrice", "LastScore",
    # v2.1 (baseline SIDE_DEF_p12) — tier memory for the profit_target
    # trigger. Stored as JSON-serialised list of floats, e.g. "[30.0]".
    # Empty string means "no tiers hit".  Round-trips through
    # ``holdings`` / ``apply_partial_execution`` so live behaviour
    # matches the in-memory simulator.
    "ProfitTargetsHit",
]

# Defaults for the D1.6 extension columns, applied during migration and
# whenever an older holdings row is read.  Keep in sync with the
# ``HoldingSnapshot`` fallback logic in ``exits/state.py``.
_CURRENT_D1_DEFAULTS = {
    "EntryScore": 0.0,
    "EntryRank": -1,
    "EntryRegime": "",
    "PeakPrice": 0.0,  # 0 signals "unseeded"; readers use max(CurrentPrice, BuyPrice)
    "LastScore": 0.0,
    "ProfitTargetsHit": "",  # JSON list of float tiers, "" when empty
}
HISTORY_COLS = [
    "Date", "Ticker", "Action", "Price", "Shares",
    "Value", "Trigger", "Notes",
]
DAILY_LOG_COLS = [
    "Date", "TriggerFired", "TriggerType", "VIX", "Regime",
    "CashPct", "PortfolioValue", "CashBalance", "TotalCapital",
    "DailyReturn", "TopHolding",
]
RECO_COLS = [
    "Date", "Ticker", "Action", "Score", "TargetPct", "ActualPct",
    "GapPct", "Price", "Shares", "Capital", "Regime", "GraceCount",
    # D1.4: see daily_runner._RECO_COLS — rank within today's scored
    # universe, -1 when not applicable.
    "Rank",
    # v2.1 — profit_target tier (float pct) carried from verdict meta
    # through the Excel sheet to ``apply_partial_execution``; NaN for
    # any action that isn't TRIM_PROFIT / SELL_PROFIT.
    "ProfitTier",
]
CASH_COLS = ["Date", "Type", "Amount", "Balance", "Notes"]

ALL_SHEETS = ["Current", "History", "DailyLog", "Recommendations", "PrevDayRecos", "RecoArchive", "CashLedger"]


def _merge_profit_tier(current: pd.DataFrame, idx: int, tier: float) -> pd.DataFrame:
    """Insert ``tier`` into the JSON-encoded ``ProfitTargetsHit`` cell at ``idx``.

    Idempotent: a tier already present is a no-op.  The set is stored as a
    JSON list of floats (sorted for deterministic serialisation) so Excel
    round-trips produce readable, diffable cells.
    """
    import json as _json

    if "ProfitTargetsHit" not in current.columns:
        current["ProfitTargetsHit"] = ""
    raw = current.at[idx, "ProfitTargetsHit"]
    existing: set = set()
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, (list, tuple)):
                existing = set(float(x) for x in parsed)
        except (ValueError, TypeError):
            existing = set()
    existing.add(float(tier))
    current.at[idx, "ProfitTargetsHit"] = _json.dumps(sorted(existing))
    return current


class HoldingsManager:
    def __init__(self, excel_path: str):
        self.path = excel_path
        self._ensure_file()

    def _ensure_file(self):
        if os.path.exists(self.path):
            self._ensure_schema_up_to_date()
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with pd.ExcelWriter(self.path, engine="openpyxl") as w:
            pd.DataFrame(columns=CURRENT_COLS).to_excel(w, sheet_name="Current", index=False)
            pd.DataFrame(columns=HISTORY_COLS).to_excel(w, sheet_name="History", index=False)
            pd.DataFrame(columns=DAILY_LOG_COLS).to_excel(w, sheet_name="DailyLog", index=False)
            pd.DataFrame(columns=RECO_COLS).to_excel(w, sheet_name="Recommendations", index=False)
            pd.DataFrame(columns=CASH_COLS).to_excel(w, sheet_name="CashLedger", index=False)

    @staticmethod
    def _backfill_current_schema(df: pd.DataFrame) -> pd.DataFrame:
        """Return ``df`` with D1.6 columns present + sensible defaults.

        Called during file migration and on every ``load_current`` read.
        The latter makes the function tolerant of users who manually edit
        the Excel file with an older template.
        """
        if df is None:
            return pd.DataFrame(columns=CURRENT_COLS)
        if df.empty:
            # Preserve existing column order (may include D1.6 fields already)
            # and ensure all legacy + D1 cols are at least declared.
            cols = list(df.columns)
            for c in CURRENT_COLS:
                if c not in cols:
                    cols.append(c)
            return pd.DataFrame(columns=cols)

        out = df.copy()
        for col, default in _CURRENT_D1_DEFAULTS.items():
            if col not in out.columns:
                out[col] = default

        # Normalise dtypes so later ``.at[i, col] = value`` writes don't
        # raise TypeError — Excel round-trips turn all-integer float
        # columns into int64 and empty string columns into float64(NaN).
        #
        # R10F-1: BuyPrice / CurrentPrice / MarketValue / PnL_Pct must
        # be float for ``apply_partial_execution`` to assign a
        # weighted average like 105.7143 into them. Without this,
        # a holdings_log.xlsx whose round-trip happened to land all
        # prices on whole-dollar values would dtype the column as
        # int64 and the next BUY_MORE would raise TypeError.
        for col in ("EntryScore", "PeakPrice", "LastScore",
                    "BuyPrice", "CurrentPrice", "MarketValue", "PnL_Pct"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).astype(float)
        out["EntryRank"] = pd.to_numeric(out["EntryRank"], errors="coerce").fillna(-1).astype(int)
        # EntryRegime may come back as NaN if the column was empty when
        # written; coerce NaN → "" so downstream ``str(...)`` calls
        # produce clean values.
        out["EntryRegime"] = out["EntryRegime"].fillna("").astype(str).replace("nan", "")
        # v2.1 — ProfitTargetsHit comes through as NaN for legacy rows
        # or when the Excel cell was empty; coerce to "" so the JSON
        # parser in ``holdings`` never sees ``nan``.
        out["ProfitTargetsHit"] = (
            out["ProfitTargetsHit"].fillna("").astype(str).replace("nan", "")
        )

        # Seed PeakPrice from the best guess available per row (price
        # history may be lost for legacy holdings).  Use max(CurrentPrice,
        # BuyPrice) so that the drawdown calculation in
        # ``build_holding_snapshots`` starts from a reasonable anchor.
        buy_price = out.get("BuyPrice")
        cur_price = out.get("CurrentPrice")
        if buy_price is not None and cur_price is not None:
            pp = out["PeakPrice"].astype(float).fillna(0.0)
            seed = np.maximum(
                pd.to_numeric(buy_price, errors="coerce").fillna(0.0),
                pd.to_numeric(cur_price, errors="coerce").fillna(0.0),
            )
            out["PeakPrice"] = np.where(pp <= 0.0, seed, pp)

        return out

    def _ensure_schema_up_to_date(self):
        """Migrate existing holdings_log.xlsx to the latest schema.

        Currently handles:
          * (pre-CashLedger era) Missing ``CashLedger`` / ``RecoArchive``
            sheets — seeded from existing Recommendations history.
          * (D1.6) Missing columns on the ``Current`` sheet — added with
            safe defaults (``PeakPrice`` seeded from max(BuyPrice,
            CurrentPrice), other fields zero/empty).

        Runs exactly once per process (no-op if the file is already
        up to date).  Uses a single rewrite of the workbook regardless of
        how many migrations fire.
        """
        need_rewrite = False
        existing = {}
        for name in ALL_SHEETS:
            df = self._read_sheet(name)
            existing[name] = df
            if df.empty and name in ("CashLedger", "RecoArchive"):
                need_rewrite = True

        # D1.6 — detect missing ``Current`` columns.
        cur_df = existing.get("Current", pd.DataFrame())
        if not cur_df.empty:
            missing_cols = [c for c in _CURRENT_D1_DEFAULTS
                            if c not in cur_df.columns]
            if missing_cols:
                need_rewrite = True

        if not need_rewrite:
            return

        # CashLedger / RecoArchive migration (pre-D1.6 logic, unchanged).
        if existing.get("RecoArchive", pd.DataFrame()).empty:
            archive_parts = []
            for src_name in ("PrevDayRecos", "Recommendations"):
                src = existing.get(src_name, pd.DataFrame())
                if not src.empty and "Date" in src.columns:
                    archive_parts.append(src)
            if archive_parts:
                arc = pd.concat(archive_parts, ignore_index=True)
                arc["_d"] = arc["Date"].astype(str).str[:10]
                arc = arc.drop_duplicates(subset=["_d", "Ticker"], keep="last")
                arc = arc.drop(columns=["_d"]).reset_index(drop=True)
                existing["RecoArchive"] = arc

        if existing.get("CashLedger", pd.DataFrame()).empty:
            existing["CashLedger"] = pd.DataFrame(columns=CASH_COLS)

        # D1.6 Current-sheet column backfill (idempotent).
        existing["Current"] = self._backfill_current_schema(existing.get("Current", pd.DataFrame()))

        with pd.ExcelWriter(self.path, engine="openpyxl", mode="w") as w:
            for name in ALL_SHEETS:
                if name in existing:
                    existing[name].to_excel(w, sheet_name=name, index=False)

    def _read_sheet(self, sheet: str) -> pd.DataFrame:
        try:
            return pd.read_excel(self.path, sheet_name=sheet, engine="openpyxl")
        except Exception:
            return pd.DataFrame()

    def _write_sheets(self, sheets: Dict[str, pd.DataFrame]):
        existing = {}
        for name in ALL_SHEETS:
            if name not in sheets:
                existing[name] = self._read_sheet(name)

        merged = {**existing, **sheets}
        with pd.ExcelWriter(self.path, engine="openpyxl", mode="w") as w:
            for name in ALL_SHEETS:
                if name in merged:
                    merged[name].to_excel(w, sheet_name=name, index=False)

    # ── Read operations ──

    def load_current(self) -> pd.DataFrame:
        # ``_backfill_current_schema`` is a cheap no-op when all D1.6
        # columns already exist; keeping it in the read path guards
        # against users manually editing holdings_log.xlsx with an older
        # Excel template that drops the new columns.
        return self._backfill_current_schema(self._read_sheet("Current"))

    def load_history(self) -> pd.DataFrame:
        return self._read_sheet("History")

    def load_daily_log(self) -> pd.DataFrame:
        return self._read_sheet("DailyLog")

    def load_recommendations(self) -> pd.DataFrame:
        return self._read_sheet("Recommendations")

    def load_reco_archive(self) -> pd.DataFrame:
        return self._read_sheet("RecoArchive")

    def load_prev_day_recos(self, today_str: Optional[str] = None) -> pd.DataFrame:
        """Return the most recent archived recommendations strictly before today.

        Falls back to the legacy PrevDayRecos sheet if the archive is empty or
        no ``today_str`` is provided.
        """
        archive = self.load_reco_archive()
        if today_str and not archive.empty and "Date" in archive.columns:
            a = archive.copy()
            a["_d"] = a["Date"].astype(str).str[:10]
            past = a[a["_d"] < str(today_str)[:10]]
            if not past.empty:
                last_date = past["_d"].max()
                return past[past["_d"] == last_date].drop(columns=["_d"]).reset_index(drop=True)
        return self._read_sheet("PrevDayRecos")

    def save_recommendations(self, recos: pd.DataFrame):
        """Write recommendations and append to date-indexed archive.

        - ``Recommendations`` sheet always mirrors the latest run.
        - ``RecoArchive`` keeps one set of rows per date (latest wins on same day).
        - ``PrevDayRecos`` mirrors the most recent archived date that precedes the
          incoming ``recos`` date (kept for backwards compatibility).
        """
        payload: Dict[str, pd.DataFrame] = {"Recommendations": recos}
        new_date = ""
        if not recos.empty and "Date" in recos.columns:
            new_date = str(recos["Date"].iloc[0])[:10]

        archive = self.load_reco_archive()
        if new_date:
            if not archive.empty and "Date" in archive.columns:
                arc = archive.copy()
                arc["_d"] = arc["Date"].astype(str).str[:10]
                arc = arc[arc["_d"] != new_date].drop(columns=["_d"])
                archive = pd.concat([arc, recos], ignore_index=True)
            else:
                archive = recos.copy()
            payload["RecoArchive"] = archive

            if "Date" in archive.columns:
                a = archive.copy()
                a["_d"] = a["Date"].astype(str).str[:10]
                past = a[a["_d"] < new_date]
                if not past.empty:
                    last_date = past["_d"].max()
                    payload["PrevDayRecos"] = past[past["_d"] == last_date].drop(columns=["_d"]).reset_index(drop=True)

        self._write_sheets(payload)

    def get_last_rebalance_date(self) -> Optional[datetime]:
        hist = self.load_history()
        if hist.empty:
            return None
        hist["Date"] = pd.to_datetime(hist["Date"])
        return hist["Date"].max().to_pydatetime()

    # ── Write operations ──

    def update_current_prices(self, price_map: Dict[str, float]):
        """Update current prices, PnL, and PeakPrice high-water mark.

        ``PeakPrice`` is D1.6 territory: every price-update bumps the
        monotonic high-water mark used by ``peak_drawdown`` triggers.  If
        the column is missing (legacy file that skipped migration), the
        ``load_current`` wrapper has already back-filled it with a
        reasonable seed (max of BuyPrice/CurrentPrice).
        """
        df = self.load_current()
        if df.empty:
            return df

        for col in ["CurrentPrice", "PnL_Pct", "MarketValue", "Weight", "PeakPrice"]:
            if col in df.columns:
                df[col] = df[col].astype(float)

        for i, row in df.iterrows():
            ticker = row["Ticker"]
            if ticker in price_map:
                cur = float(price_map[ticker])
                df.at[i, "CurrentPrice"] = cur
                buy = row["BuyPrice"]
                if pd.notna(buy) and float(buy) > 0:
                    df.at[i, "PnL_Pct"] = round((cur - float(buy)) / float(buy) * 100, 2)
                df.at[i, "MarketValue"] = round(cur * float(row["Shares"]), 2)
                prev_peak = float(row.get("PeakPrice", 0.0) or 0.0)
                df.at[i, "PeakPrice"] = max(prev_peak, cur)

        total = df["MarketValue"].sum()
        if total > 0:
            df["Weight"] = (df["MarketValue"] / total * 100).round(2)

        self._write_sheets({"Current": df})
        return df

    def apply_recommendations(
        self, recos: pd.DataFrame, trigger_type: str, date: datetime = None,
    ):
        """Apply buy/sell/trim recommendations to holdings."""
        if date is None:
            date = datetime.now()
        date_str = date.strftime("%Y-%m-%d")

        current = self.load_current()
        history = self.load_history()
        new_hist_rows = []

        # D2-aware dispatch.  ``RecosAction.FULL_CLOSE`` covers SELL / STOP_LOSS
        # **and all new D2 variants** (SELL_PEAK_DD / SELL_SCORE_DECAY / …) so
        # adding new triggers requires zero changes here.  TRIM_GRACE stays
        # outside PARTIAL_CLOSE's main TRIM path — it's handled separately in
        # ``apply_partial_execution`` via sell_grace's dedicated routing.
        from exits import RecosAction as _RA
        _full_close_with_legacy = _RA.FULL_CLOSE | {"DECREASE"}  # legacy alias
        buys = recos[recos["Action"].isin(["BUY", "BUY_NEW", "BUY_MORE"])]
        sells = recos[recos["Action"].isin(_full_close_with_legacy)]
        # Partial-close excluding TRIM_GRACE (handled by sell_grace path).
        _trim_actions = _RA.PARTIAL_CLOSE - {"TRIM_GRACE"}
        trims = recos[recos["Action"].isin(_trim_actions)]

        for _, row in sells.iterrows():
            ticker = row["Ticker"]
            mask = current["Ticker"] == ticker
            if mask.any():
                sold_row = current[mask].iloc[0]
                new_hist_rows.append({
                    "Date": date_str, "Ticker": ticker, "Action": row["Action"],
                    "Price": row["Price"], "Shares": int(sold_row["Shares"]),
                    "Value": round(row["Price"] * sold_row["Shares"], 2),
                    "Trigger": trigger_type, "Notes": f"Score={row.get('Score', ''):.1f}",
                })
                current = current[~mask]

        for _, row in trims.iterrows():
            ticker = row["Ticker"]
            trim_shares = int(row["Shares"])
            action = str(row["Action"])
            mask = current["Ticker"] == ticker
            if mask.any() and trim_shares > 0:
                idx = current.index[mask][0]
                held = int(current.at[idx, "Shares"])
                actual_trim = min(trim_shares, held - 1)
                if actual_trim > 0:
                    current.at[idx, "Shares"] = held - actual_trim
                    current.at[idx, "MarketValue"] = round(
                        row["Price"] * (held - actual_trim), 2)
                    # v2.1 — persist tier memory when this TRIM comes from
                    # the profit_target trigger (carried via ProfitTier col).
                    _tier = row.get("ProfitTier", None)
                    if action in ("TRIM_PROFIT", "SELL_PROFIT") and pd.notna(_tier):
                        current = _merge_profit_tier(current, idx, float(_tier))
                    new_hist_rows.append({
                        "Date": date_str, "Ticker": ticker, "Action": action,
                        "Price": row["Price"], "Shares": actual_trim,
                        "Value": round(row["Price"] * actual_trim, 2),
                        "Trigger": trigger_type, "Notes": f"Score={row.get('Score', ''):.1f}",
                    })

        for _, row in buys.iterrows():
            ticker = row["Ticker"]
            mask = current["Ticker"] == ticker
            # D1.6: pull Score/Regime off the reco row once per BUY so
            # both BUY_MORE (refresh LastScore) and BUY_NEW (seed entry_*)
            # paths share the same extraction.
            reco_score = float(row.get("Score", 0.0) or 0.0)
            reco_regime = str(row.get("Regime", "") or "")
            # D1.4: recos now carries Rank (1-based within today's scored
            # universe; -1 for held tickers outside top-N).  Legacy recos
            # missing the column fall back to -1 for backward compat.
            try:
                reco_rank = int(row.get("Rank", -1))
            except (TypeError, ValueError):
                reco_rank = -1
            if mask.any():
                idx = current.index[mask][0]
                current.at[idx, "Shares"] = int(current.at[idx, "Shares"]) + int(row["Shares"])
                current.at[idx, "CurrentPrice"] = row["Price"]
                current.at[idx, "MarketValue"] = round(
                    row["Price"] * current.at[idx, "Shares"], 2)
                # BUY_MORE: entry_* are frozen at first purchase; only
                # refresh the live LastScore + PeakPrice anchors.
                current.at[idx, "LastScore"] = reco_score
                prev_peak = float(current.at[idx, "PeakPrice"] or 0.0)
                current.at[idx, "PeakPrice"] = max(prev_peak, float(row["Price"]))
            else:
                new_row = {
                    "Ticker": ticker, "BuyDate": date_str,
                    "BuyPrice": row["Price"], "Shares": int(row["Shares"]),
                    "CurrentPrice": row["Price"], "PnL_Pct": 0.0,
                    "Weight": 0, "MarketValue": round(row["Price"] * row["Shares"], 2),
                    "Status": "ACTIVE",
                    # D1.6 entry attribution — now D1.4-complete with
                    # EntryRank sourced from recos.Rank.
                    "EntryScore": reco_score,
                    "EntryRank": reco_rank,
                    "EntryRegime": reco_regime,
                    "PeakPrice": float(row["Price"]),
                    "LastScore": reco_score,
                    "ProfitTargetsHit": "",
                }
                current = pd.concat([current, pd.DataFrame([new_row])], ignore_index=True)

            new_hist_rows.append({
                "Date": date_str, "Ticker": ticker,
                "Action": str(row["Action"]),
                "Price": row["Price"], "Shares": int(row["Shares"]),
                "Value": round(row["Price"] * row["Shares"], 2),
                "Trigger": trigger_type, "Notes": f"Score={row.get('Score', ''):.1f}",
            })

        if new_hist_rows:
            new_hist = pd.DataFrame(new_hist_rows)
            history = pd.concat([history, new_hist], ignore_index=True)

        total = current["MarketValue"].sum()
        if total > 0:
            current["Weight"] = (current["MarketValue"] / total * 100).round(2)

        self._write_sheets({
            "Current": current,
            "History": history,
            "Recommendations": recos,
        })

    def apply_partial_execution(
        self, executed: pd.DataFrame, trigger_type: str = "T10_MANUAL", date: datetime = None,
    ):
        """Apply only the user-confirmed execution rows from the T10 workflow."""
        if date is None:
            date = datetime.now()
        date_str = date.strftime("%Y-%m-%d")

        current = self.load_current()
        history = self.load_history()
        new_hist = []

        from exits import RecosAction as _RA  # noqa: F811

        for _, row in executed.iterrows():
            action = str(row["Action"])
            ticker = row["Ticker"]
            price = float(row["Price"])
            shares = int(row["Shares"])

            if _RA.is_full_close(action):
                mask = current["Ticker"] == ticker
                if mask.any():
                    sold_row = current[mask].iloc[0]
                    new_hist.append({
                        "Date": date_str, "Ticker": ticker, "Action": action,
                        "Price": price, "Shares": int(sold_row["Shares"]),
                        "Value": round(price * sold_row["Shares"], 2),
                        "Trigger": trigger_type, "Notes": "",
                    })
                    current = current[~mask]

            elif _RA.is_partial_close(action) and action != "TRIM_GRACE":
                # TRIM_GRACE has its own dedicated grace-aware emission path
                # elsewhere in the live flow — keep it out of this branch.
                mask = current["Ticker"] == ticker
                if mask.any():
                    idx = current.index[mask][0]
                    held = int(current.at[idx, "Shares"])
                    actual_trim = min(shares, held - 1)
                    if actual_trim > 0:
                        current.at[idx, "Shares"] = held - actual_trim
                        current.at[idx, "CurrentPrice"] = price
                        current.at[idx, "MarketValue"] = round(
                            price * (held - actual_trim), 2)
                        # v2.1 — persist tier memory for profit_target trigger.
                        # Only TRIM_PROFIT / SELL_PROFIT carry a tier; other
                        # partial-close actions leave ProfitTargetsHit untouched.
                        _tier = row.get("ProfitTier", None)
                        if action in ("TRIM_PROFIT", "SELL_PROFIT") and pd.notna(_tier):
                            current = _merge_profit_tier(current, idx, float(_tier))
                        new_hist.append({
                            "Date": date_str, "Ticker": ticker, "Action": action,
                            "Price": price, "Shares": actual_trim,
                            "Value": round(price * actual_trim, 2),
                            "Trigger": trigger_type, "Notes": "",
                        })

            elif action in ("BUY", "BUY_NEW", "BUY_MORE"):
                mask = current["Ticker"] == ticker
                # Same reco-row extraction as apply_recommendations so
                # the two write paths stay in lockstep.
                reco_score = float(row.get("Score", 0.0) or 0.0)
                reco_regime = str(row.get("Regime", "") or "")
                try:
                    reco_rank = int(row.get("Rank", -1))
                except (TypeError, ValueError):
                    reco_rank = -1
                if mask.any():
                    idx = current.index[mask][0]
                    # R10F-1 — BUY_MORE must recompute BuyPrice as a
                    # share-weighted average of the existing cost basis
                    # and the new fill. Until R10F-1 this branch only
                    # added shares and left BuyPrice pinned to the
                    # original entry, so the 5/19 MRNA backfill had to
                    # be done by hand and PnL_Pct on the Current sheet
                    # drifted progressively further from reality.
                    old_shares = int(current.at[idx, "Shares"])
                    old_buy = float(current.at[idx, "BuyPrice"] or 0.0)
                    new_shares = old_shares + shares
                    if new_shares > 0 and old_buy > 0:
                        weighted_buy = (
                            (old_shares * old_buy) + (shares * price)
                        ) / new_shares
                        current.at[idx, "BuyPrice"] = round(weighted_buy, 4)
                    elif new_shares > 0:
                        # Defensive: if the prior cost basis is missing
                        # / zero (legacy rows, migrations), seed with
                        # the new fill price rather than dividing into
                        # a zero numerator.
                        current.at[idx, "BuyPrice"] = round(float(price), 4)
                    current.at[idx, "Shares"] = new_shares
                    current.at[idx, "CurrentPrice"] = price
                    current.at[idx, "MarketValue"] = round(
                        price * current.at[idx, "Shares"], 2
                    )
                    current.at[idx, "LastScore"] = reco_score
                    prev_peak = float(current.at[idx, "PeakPrice"] or 0.0)
                    current.at[idx, "PeakPrice"] = max(prev_peak, float(price))
                else:
                    new_row = {
                        "Ticker": ticker, "BuyDate": date_str,
                        "BuyPrice": price, "Shares": shares,
                        "CurrentPrice": price, "PnL_Pct": 0.0,
                        "Weight": 0, "MarketValue": round(price * shares, 2),
                        "Status": "ACTIVE",
                        "EntryScore": reco_score,
                        "EntryRank": reco_rank,
                        "EntryRegime": reco_regime,
                        "PeakPrice": float(price),
                        "LastScore": reco_score,
                        "ProfitTargetsHit": "",
                    }
                    current = pd.concat(
                        [current, pd.DataFrame([new_row])], ignore_index=True
                    )
                new_hist.append({
                    "Date": date_str, "Ticker": ticker, "Action": action,
                    "Price": price, "Shares": shares,
                    "Value": round(price * shares, 2),
                    "Trigger": trigger_type, "Notes": "",
                })

        if new_hist:
            history = pd.concat(
                [history, pd.DataFrame(new_hist)], ignore_index=True
            )

        total = current["MarketValue"].sum()
        if total > 0:
            current["Weight"] = (current["MarketValue"] / total * 100).round(2)

        self._write_sheets({"Current": current, "History": history})

    # ── Cash Ledger ──

    def load_cash_ledger(self) -> pd.DataFrame:
        return self._read_sheet("CashLedger")

    def get_cash_balance(self) -> float:
        ledger = self.load_cash_ledger()
        if ledger.empty:
            return 0.0
        return float(ledger["Balance"].iloc[-1])

    def get_total_deposited(self) -> float:
        """Sum of all positive inflows (INIT + DEPOSIT)."""
        ledger = self.load_cash_ledger()
        if ledger.empty:
            return 0.0
        inflows = ledger[ledger["Type"].isin(["INIT", "DEPOSIT"])]
        return float(inflows["Amount"].sum()) if not inflows.empty else 0.0

    def record_cash_event(
        self, event_type: str, amount: float, notes: str = "",
    ):
        """Append a row to CashLedger with running balance."""
        ledger = self.load_cash_ledger()
        prev_balance = float(ledger["Balance"].iloc[-1]) if not ledger.empty else 0.0
        new_balance = round(prev_balance + amount, 2)
        new_row = {
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "Type": event_type,
            "Amount": round(amount, 2),
            "Balance": new_balance,
            "Notes": notes,
        }
        ledger = pd.concat([ledger, pd.DataFrame([new_row])], ignore_index=True)
        self._write_sheets({"CashLedger": ledger})
        return new_balance

    def initialize_cash(self, amount: float):
        """Set initial cash balance (only if ledger is empty)."""
        ledger = self.load_cash_ledger()
        if not ledger.empty:
            return float(ledger["Balance"].iloc[-1])
        return self.record_cash_event("INIT", amount, "Initial capital")

    def log_daily(
        self, trigger_fired: bool, trigger_type: str,
        vix: float, regime: str, cash_pct: float,
        portfolio_value: float, cash_balance: float = 0.0,
        total_capital: float = 0.0,
        daily_return: float = 0.0, top_holding: str = "",
    ):
        """Upsert a row to DailyLog (one row per date, last run wins)."""
        log = self.load_daily_log()
        today_str = datetime.now().strftime("%Y-%m-%d")
        new_row = {
            "Date": today_str,
            "TriggerFired": trigger_fired,
            "TriggerType": trigger_type,
            "VIX": round(vix, 2),
            "Regime": regime,
            "CashPct": round(cash_pct, 2),
            "PortfolioValue": round(portfolio_value, 2),
            "CashBalance": round(cash_balance, 2),
            "TotalCapital": round(total_capital, 2),
            "DailyReturn": round(daily_return, 4),
            "TopHolding": top_holding,
        }
        if not log.empty:
            log["_date_str"] = pd.to_datetime(log["Date"]).dt.strftime("%Y-%m-%d")
            mask = log["_date_str"] == today_str
            if mask.any():
                idx = log.index[mask].tolist()[-1]
                for k, v in new_row.items():
                    log.at[idx, k] = v
                log = log.drop(log.index[mask][:-1])
                log = log.drop(columns=["_date_str"]).reset_index(drop=True)
                self._write_sheets({"DailyLog": log})
                return
            log = log.drop(columns=["_date_str"])
        log = pd.concat([log, pd.DataFrame([new_row])], ignore_index=True)
        self._write_sheets({"DailyLog": log})

    # ── Query helpers ──

    def get_portfolio_value(self) -> float:
        df = self.load_current()
        if df.empty:
            return 0.0
        return float(df["MarketValue"].sum())

    def get_pnl_summary(self) -> dict:
        df = self.load_current()
        if df.empty:
            return {"total_value": 0, "total_cost": 0, "total_pnl": 0, "pnl_pct": 0, "holdings_count": 0}
        cost = (df["BuyPrice"] * df["Shares"]).sum()
        value = df["MarketValue"].sum()
        pnl = value - cost
        return {
            "total_value": round(value, 2),
            "total_cost": round(cost, 2),
            "total_pnl": round(pnl, 2),
            "pnl_pct": round(pnl / cost * 100, 2) if cost > 0 else 0,
            "holdings_count": len(df),
        }

    # ── Dynamic-exit interop (Track D / D1.6) ──

    @property
    def holdings(self) -> Dict[str, dict]:
        """Return a **read-only snapshot** of per-ticker holding state.

        Shape matches ``SimPortfolio.holdings`` so that
        ``exits.build_holding_snapshots`` can treat live and sim storage
        identically:

            {
              "AAPL": {
                 "shares":        int,
                 "avg_cost":      float,
                 "current_price": float,
                 "entry_date":    str (YYYY-MM-DD),
                 "entry_price":   float,
                 "entry_score":   float,
                 "entry_rank":    int (-1 if unknown),
                 "entry_regime":  str,
                 "peak_price":    float,
                 "last_score":    float,
              }, ...
            }

        Since this rebuilds from disk on every access, any mutation of
        the returned dict is **lost** — the write path runs through
        ``apply_recommendations`` / ``update_current_prices`` which go
        back to the Excel store.  This matches the "read-only" contract
        requested at D1.6 design time.
        """
        df = self.load_current()
        if df.empty:
            return {}
        import json as _json

        out: Dict[str, dict] = {}
        for _, row in df.iterrows():
            ticker = str(row["Ticker"])

            pt_raw = row.get("ProfitTargetsHit", "") or ""
            pt_set: set = set()
            if isinstance(pt_raw, str) and pt_raw.strip():
                try:
                    parsed = _json.loads(pt_raw)
                    if isinstance(parsed, (list, tuple)):
                        pt_set = set(float(x) for x in parsed)
                except (ValueError, TypeError):
                    pt_set = set()

            out[ticker] = {
                "shares": int(row.get("Shares", 0) or 0),
                "avg_cost": float(row.get("BuyPrice", 0.0) or 0.0),
                "current_price": float(row.get("CurrentPrice", 0.0) or 0.0),
                "entry_date": str(row.get("BuyDate", "") or ""),
                "entry_price": float(row.get("BuyPrice", 0.0) or 0.0),
                "entry_score": float(row.get("EntryScore", 0.0) or 0.0),
                "entry_rank": int(row.get("EntryRank", -1) or -1),
                "entry_regime": str(row.get("EntryRegime", "") or ""),
                "peak_price": float(row.get("PeakPrice", 0.0) or 0.0),
                "last_score": float(row.get("LastScore", 0.0) or 0.0),
                "profit_targets_hit": pt_set,
            }
        return out

    def get_weight_vector(self) -> Dict[str, float]:
        """Return current portfolio weight by ticker (0-1 scale)."""
        df = self.load_current()
        if df.empty:
            return {}
        total = df["MarketValue"].sum()
        if total <= 0:
            return {}
        return dict(zip(df["Ticker"], df["MarketValue"] / total))
