"""
Phase 3 Streamlit Dashboard

Run:  streamlit run dashboard.py
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from holdings_manager import HoldingsManager
from cache_health import run_full_health_check, load_config


def _load_conf():
    return load_config(str(_THIS_DIR / "config.yaml"))


# ─── Page config ───

st.set_page_config(page_title="Quant Dashboard", layout="wide", page_icon="Q")

conf = _load_conf()
hm = HoldingsManager(conf["paths"]["holdings_log"])

# ─── Sidebar ───

st.sidebar.title("Quant Engine")
st.sidebar.caption("Phase 3 Live Dashboard")
page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Holdings", "Recommendations", "History", "Cache Health"],
)

# ─── Overview ───

if page == "Overview":
    st.title("Portfolio Overview")

    pnl = hm.get_pnl_summary()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Portfolio Value", f"${pnl['total_value']:,.0f}")
    col2.metric("Total PnL", f"${pnl['total_pnl']:,.0f}", f"{pnl['pnl_pct']:+.1f}%")
    col3.metric("Holdings", f"{pnl['holdings_count']} stocks")

    # Latest daily log
    log = hm.load_daily_log()
    if not log.empty:
        latest = log.iloc[-1]
        col4.metric("VIX", f"{latest.get('VIX', '?')}", latest.get("Regime", "?"))

        st.subheader("Recent Daily Log")
        st.dataframe(
            log.tail(14).sort_values("Date", ascending=False),
            use_container_width=True, hide_index=True,
        )
    else:
        col4.metric("VIX", "—", "No data")
        st.info("No daily log data yet. Run daily_runner.py to start collecting data.")

    # Portfolio value chart
    if not log.empty and "PortfolioValue" in log.columns:
        log["Date"] = pd.to_datetime(log["Date"])
        chart_data = log[["Date", "PortfolioValue"]].set_index("Date")
        if len(chart_data) > 1:
            st.subheader("Portfolio Value Over Time")
            st.line_chart(chart_data)

# ─── Holdings ───

elif page == "Holdings":
    st.title("Current Holdings")

    current = hm.load_current()
    if current.empty:
        st.info("No holdings yet. Run daily_runner.py --force-rebalance to initialize.")
    else:
        # Summary metrics
        total = current["MarketValue"].sum()
        col1, col2 = st.columns(2)
        col1.metric("Total Market Value", f"${total:,.0f}")
        col2.metric("Number of Positions", len(current))

        # Color PnL
        def color_pnl(val):
            if pd.isna(val):
                return ""
            color = "green" if val >= 0 else "red"
            return f"color: {color}"

        styled = current.style.applymap(color_pnl, subset=["PnL_Pct"])
        st.dataframe(
            current.sort_values("MarketValue", ascending=False),
            use_container_width=True, hide_index=True,
        )

        # Weight distribution
        if "Weight" in current.columns and len(current) > 0:
            st.subheader("Weight Distribution")
            top10 = current.nlargest(10, "Weight")
            st.bar_chart(top10.set_index("Ticker")["Weight"])

# ─── Recommendations ───

elif page == "Recommendations":
    st.title("Latest Recommendations")

    recos = hm.load_recommendations()
    if recos.empty:
        st.info("No recommendations yet. Recommendations are generated when a trigger fires.")
    else:
        latest_date = recos["Date"].iloc[0] if "Date" in recos.columns else "?"
        st.caption(f"Generated: {latest_date}")

        buys = recos[recos["Action"] == "BUY"] if "Action" in recos.columns else pd.DataFrame()
        sells = recos[recos["Action"].isin(["SELL", "DECREASE"])] if "Action" in recos.columns else pd.DataFrame()
        holds = recos[recos["Action"] == "HOLD"] if "Action" in recos.columns else pd.DataFrame()

        col1, col2, col3 = st.columns(3)
        col1.metric("BUY", len(buys))
        col2.metric("SELL", len(sells))
        col3.metric("HOLD", len(holds))

        if not buys.empty:
            st.subheader("Buy Recommendations")
            st.dataframe(buys, use_container_width=True, hide_index=True)
        if not sells.empty:
            st.subheader("Sell Signals")
            st.dataframe(sells, use_container_width=True, hide_index=True)
        if not holds.empty:
            st.subheader("Hold (no change)")
            st.dataframe(holds, use_container_width=True, hide_index=True)

        # Total capital allocation
        if "Capital" in recos.columns:
            total_alloc = recos[recos["Action"] == "BUY"]["Capital"].sum()
            st.caption(f"Total BUY allocation: ${total_alloc:,.0f}")

# ─── History ───

elif page == "History":
    st.title("Trading History")

    history = hm.load_history()
    if history.empty:
        st.info("No trading history yet.")
    else:
        # Filter
        actions = ["ALL"] + sorted(history["Action"].unique().tolist())
        selected_action = st.selectbox("Filter by Action", actions)
        if selected_action != "ALL":
            history = history[history["Action"] == selected_action]

        st.dataframe(
            history.sort_values("Date", ascending=False),
            use_container_width=True, hide_index=True,
        )

        # Trigger distribution
        if "Trigger" in history.columns:
            st.subheader("Trigger Distribution")
            trigger_counts = history["Trigger"].value_counts()
            st.bar_chart(trigger_counts)

    # Daily log with trigger markers
    log = hm.load_daily_log()
    if not log.empty:
        st.subheader("Daily Trigger Log")
        triggered = log[log["TriggerFired"] == True]
        st.caption(f"Total triggers fired: {len(triggered)} / {len(log)} days")
        st.dataframe(
            log.sort_values("Date", ascending=False),
            use_container_width=True, hide_index=True,
        )

# ─── Cache Health ───

elif page == "Cache Health":
    st.title("Cache Health Check")

    if st.button("Run Health Check"):
        with st.spinner("Checking cache integrity..."):
            health = run_full_health_check(str(_THIS_DIR / "config.yaml"))

        status_color = {"OK": "green", "WARNING": "orange", "CRITICAL": "red"}
        overall = health["overall_status"]
        st.markdown(
            f"### Overall: :{status_color.get(overall, 'gray')}[{overall}]"
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("SP500 Tickers", health["sp500_ticker_count"])
        col2.metric("OHLCV Sampled", health["ohlcv_sampled"])
        col3.metric("File Issues", health["file_integrity_issues"])

        # VIX
        vix = health.get("vix", {})
        st.subheader("VIX Data")
        vcol1, vcol2, vcol3 = st.columns(3)
        vcol1.metric("Status", vix.get("status", "?"))
        vcol2.metric("Latest Close", f"{vix.get('latest_close', '?')}")
        vcol3.metric("Latest Date", vix.get("latest_date", "?"))

        # Missing / Stale
        if health.get("ohlcv_missing"):
            st.warning(f"Missing tickers: {health['ohlcv_missing']}")
        if health.get("ohlcv_stale"):
            st.warning(f"Stale tickers: {health['ohlcv_stale']}")
        if not health.get("ohlcv_missing") and not health.get("ohlcv_stale"):
            st.success("All sampled tickers are up to date.")
    else:
        st.info("Click 'Run Health Check' to scan cache integrity.")
