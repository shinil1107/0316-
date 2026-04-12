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
]
HISTORY_COLS = [
    "Date", "Ticker", "Action", "Price", "Shares",
    "Value", "Trigger", "Notes",
]
DAILY_LOG_COLS = [
    "Date", "TriggerFired", "TriggerType", "VIX", "Regime",
    "CashPct", "PortfolioValue", "DailyReturn", "TopHolding",
]
RECO_COLS = [
    "Date", "Ticker", "Action", "Score", "TargetPct", "ActualPct",
    "GapPct", "Price", "Shares", "Capital", "Regime", "GraceCount",
]
CASH_COLS = ["Date", "Type", "Amount", "Balance", "Notes"]

ALL_SHEETS = ["Current", "History", "DailyLog", "Recommendations", "CashLedger"]


class HoldingsManager:
    def __init__(self, excel_path: str):
        self.path = excel_path
        self._ensure_file()

    def _ensure_file(self):
        if os.path.exists(self.path):
            self._ensure_cash_ledger_sheet()
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with pd.ExcelWriter(self.path, engine="openpyxl") as w:
            pd.DataFrame(columns=CURRENT_COLS).to_excel(w, sheet_name="Current", index=False)
            pd.DataFrame(columns=HISTORY_COLS).to_excel(w, sheet_name="History", index=False)
            pd.DataFrame(columns=DAILY_LOG_COLS).to_excel(w, sheet_name="DailyLog", index=False)
            pd.DataFrame(columns=RECO_COLS).to_excel(w, sheet_name="Recommendations", index=False)
            pd.DataFrame(columns=CASH_COLS).to_excel(w, sheet_name="CashLedger", index=False)

    def _ensure_cash_ledger_sheet(self):
        """Add CashLedger sheet to existing files that don't have it yet."""
        try:
            pd.read_excel(self.path, sheet_name="CashLedger", engine="openpyxl")
        except Exception:
            existing = {}
            for name in ["Current", "History", "DailyLog", "Recommendations"]:
                existing[name] = self._read_sheet(name)
            existing["CashLedger"] = pd.DataFrame(columns=CASH_COLS)
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
        return self._read_sheet("Current")

    def load_history(self) -> pd.DataFrame:
        return self._read_sheet("History")

    def load_daily_log(self) -> pd.DataFrame:
        return self._read_sheet("DailyLog")

    def load_recommendations(self) -> pd.DataFrame:
        return self._read_sheet("Recommendations")

    def save_recommendations(self, recos: pd.DataFrame):
        """Write recommendations without touching Current/History."""
        self._write_sheets({"Recommendations": recos})

    def get_last_rebalance_date(self) -> Optional[datetime]:
        hist = self.load_history()
        if hist.empty:
            return None
        hist["Date"] = pd.to_datetime(hist["Date"])
        return hist["Date"].max().to_pydatetime()

    # ── Write operations ──

    def update_current_prices(self, price_map: Dict[str, float]):
        """Update current prices and PnL for all holdings."""
        df = self.load_current()
        if df.empty:
            return df

        for col in ["CurrentPrice", "PnL_Pct", "MarketValue", "Weight"]:
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

        buys = recos[recos["Action"].isin(["BUY", "BUY_NEW", "BUY_MORE"])]
        sells = recos[recos["Action"].isin(["SELL", "DECREASE", "STOP_LOSS"])]
        trims = recos[recos["Action"] == "TRIM"]

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
            mask = current["Ticker"] == ticker
            if mask.any() and trim_shares > 0:
                idx = current.index[mask][0]
                held = int(current.at[idx, "Shares"])
                actual_trim = min(trim_shares, held - 1)
                if actual_trim > 0:
                    current.at[idx, "Shares"] = held - actual_trim
                    current.at[idx, "MarketValue"] = round(
                        row["Price"] * (held - actual_trim), 2)
                    new_hist_rows.append({
                        "Date": date_str, "Ticker": ticker, "Action": "TRIM",
                        "Price": row["Price"], "Shares": actual_trim,
                        "Value": round(row["Price"] * actual_trim, 2),
                        "Trigger": trigger_type, "Notes": f"Score={row.get('Score', ''):.1f}",
                    })

        for _, row in buys.iterrows():
            ticker = row["Ticker"]
            mask = current["Ticker"] == ticker
            if mask.any():
                idx = current.index[mask][0]
                current.at[idx, "Shares"] = int(current.at[idx, "Shares"]) + int(row["Shares"])
                current.at[idx, "CurrentPrice"] = row["Price"]
                current.at[idx, "MarketValue"] = round(
                    row["Price"] * current.at[idx, "Shares"], 2)
            else:
                new_row = {
                    "Ticker": ticker, "BuyDate": date_str,
                    "BuyPrice": row["Price"], "Shares": int(row["Shares"]),
                    "CurrentPrice": row["Price"], "PnL_Pct": 0.0,
                    "Weight": 0, "MarketValue": round(row["Price"] * row["Shares"], 2),
                    "Status": "ACTIVE",
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
        self, executed: pd.DataFrame, trigger_type: str = "PARTIAL", date: datetime = None,
    ):
        """Apply only the rows the user actually executed (subset of recommendations)."""
        if date is None:
            date = datetime.now()
        date_str = date.strftime("%Y-%m-%d")

        current = self.load_current()
        history = self.load_history()
        new_hist = []

        for _, row in executed.iterrows():
            action = str(row["Action"])
            ticker = row["Ticker"]
            price = float(row["Price"])
            shares = int(row["Shares"])

            if action in ("SELL", "STOP_LOSS"):
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

            elif action == "TRIM":
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
                        new_hist.append({
                            "Date": date_str, "Ticker": ticker, "Action": "TRIM",
                            "Price": price, "Shares": actual_trim,
                            "Value": round(price * actual_trim, 2),
                            "Trigger": trigger_type, "Notes": "",
                        })

            elif action in ("BUY", "BUY_NEW", "BUY_MORE"):
                mask = current["Ticker"] == ticker
                if mask.any():
                    idx = current.index[mask][0]
                    current.at[idx, "Shares"] = int(current.at[idx, "Shares"]) + shares
                    current.at[idx, "CurrentPrice"] = price
                    current.at[idx, "MarketValue"] = round(
                        price * current.at[idx, "Shares"], 2
                    )
                else:
                    new_row = {
                        "Ticker": ticker, "BuyDate": date_str,
                        "BuyPrice": price, "Shares": shares,
                        "CurrentPrice": price, "PnL_Pct": 0.0,
                        "Weight": 0, "MarketValue": round(price * shares, 2),
                        "Status": "ACTIVE",
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
        portfolio_value: float, daily_return: float = 0.0,
        top_holding: str = "",
    ):
        """Append a row to DailyLog."""
        log = self.load_daily_log()
        new_row = {
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "TriggerFired": trigger_fired,
            "TriggerType": trigger_type,
            "VIX": round(vix, 2),
            "Regime": regime,
            "CashPct": round(cash_pct, 2),
            "PortfolioValue": round(portfolio_value, 2),
            "DailyReturn": round(daily_return, 4),
            "TopHolding": top_holding,
        }
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

    def get_weight_vector(self) -> Dict[str, float]:
        """Return current portfolio weight by ticker (0-1 scale)."""
        df = self.load_current()
        if df.empty:
            return {}
        total = df["MarketValue"].sum()
        if total <= 0:
            return {}
        return dict(zip(df["Ticker"], df["MarketValue"] / total))
