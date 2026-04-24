"""Gmail notification sender for Phase 3 daily runner."""

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def _resolve_password(raw: str) -> str:
    """Resolve password from config, local override, or env var."""
    if raw and not raw.startswith("${"):
        return raw
    env_val = os.environ.get("GMAIL_APP_PASSWORD", "")
    if env_val:
        return env_val
    local_cfg = Path(__file__).parent / "config.local.yaml"
    if local_cfg.exists():
        import yaml
        with open(local_cfg) as f:
            local = yaml.safe_load(f) or {}
        return local.get("email", {}).get("gmail_app_password", "")
    return ""


def _build_trigger_body(
    triggers: List[str],
    recos: pd.DataFrame,
    vix: float,
    regime: str,
    holdings_mgr,
    health: dict,
    daily_buy_limit: float = 0.0,
    universe_delta_text: str = "",
) -> str:
    """Build actionable TODO-list email body."""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    trigger_str = ", ".join(triggers) if triggers else "NONE"

    lines.append(f"Quant Engine — {now}")
    lines.append(f"Regime: {regime} | VIX: {vix:.1f} | Trigger: {trigger_str}")
    lines.append("=" * 55)

    if universe_delta_text:
        lines.append("")
        lines.append(universe_delta_text)
        lines.append("")

    if recos.empty:
        lines.append("\nNo recommendations generated (scoring failed or empty universe).")
        lines.append(_build_portfolio_section(holdings_mgr, regime, vix))
        lines.append(_build_cash_section(holdings_mgr, daily_buy_limit))
        lines.append(_build_health_section(health))
        return "\n".join(lines)

    is_preview = not triggers
    _act = lambda name: recos[recos["Action"] == name] if "Action" in recos.columns else pd.DataFrame()
    stop_losses = _act("STOP_LOSS")
    sells = _act("SELL")
    sell_grace = _act("SELL_GRACE")
    trims = _act("TRIM")
    buy_new = _act("BUY_NEW")
    buy_more = _act("BUY_MORE")
    buy_legacy = _act("BUY")
    all_buys = pd.concat([buy_new, buy_more, buy_legacy])
    holds = _act("HOLD")
    deferred = _act("DEFERRED")

    buy_total = all_buys["Capital"].sum() if not all_buys.empty else 0
    has_gap = "GapPct" in recos.columns

    if is_preview:
        lines.append(f"\n[DAILY PREVIEW — no trigger, recommendations for reference]")
    else:
        lines.append(f"\n[TODAY'S TO-DO LIST]")
    lines.append("")

    if not stop_losses.empty:
        lines.append(f"  *** STOP LOSS ({len(stop_losses)} stocks) — SELL IMMEDIATELY ***")
        for _, r in stop_losses.iterrows():
            val = r["Price"] * r["Shares"]
            lines.append(
                f"  [!] STOP_LOSS  {r['Ticker']:6s}  {int(r['Shares']):3d} shares "
                f"@ ${r['Price']:<8.2f}  -> recover ~${val:,.0f}"
            )
        lines.append("")

    if not sells.empty:
        lines.append(f"  SELL — dropped from universe ({len(sells)} stocks):")
        for _, r in sells.iterrows():
            val = r["Price"] * r["Shares"]
            gap_info = f"  weight {r['ActualPct']:.1f}% -> 0%" if has_gap else ""
            lines.append(
                f"  [ ] SELL  {r['Ticker']:6s}  {int(r['Shares']):3d} shares "
                f"@ ${r['Price']:<8.2f}  -> deposit ~${val:,.0f}{gap_info}"
            )
        lines.append("")

    if not sell_grace.empty:
        lines.append(f"  SELL_GRACE — watch list ({len(sell_grace)} stocks):")
        for _, r in sell_grace.iterrows():
            gc = int(r.get("GraceCount", 0))
            lines.append(
                f"  [~] GRACE  {r['Ticker']:6s}  day {gc} — "
                f"dropped from top_n, holding for now  weight={r.get('ActualPct', 0):.1f}%"
            )
        lines.append("")

    if not trims.empty:
        lines.append(f"  TRIM — reduce overweight ({len(trims)} stocks):")
        for _, r in trims.iterrows():
            val = r["Price"] * r["Shares"]
            lines.append(
                f"  [ ] TRIM  {r['Ticker']:6s}  {int(r['Shares']):3d} shares "
                f"@ ${r['Price']:<8.2f}  -> deposit ~${val:,.0f}"
                f"  (target {r.get('TargetPct', 0):.1f}% / actual {r.get('ActualPct', 0):.1f}%)"
            )
        lines.append("")

    if not all_buys.empty:
        limit_note = f"  (daily limit: ${daily_buy_limit:,.0f})" if daily_buy_limit > 0 else ""
        lines.append(f"  BUY ({len(all_buys)} stocks, ${buy_total:,.0f} total):{limit_note}")
        for _, r in all_buys.iterrows():
            action_tag = str(r['Action']).replace('_', ' ')
            gap_info = ""
            if has_gap:
                gap_info = f"  [{r.get('ActualPct', 0):.1f}%->{r.get('TargetPct', 0):.1f}%]"
            lines.append(
                f"  [ ] {action_tag:9s} {r['Ticker']:6s}  {int(r['Shares']):3d} shares "
                f"@ ${r['Price']:<8.2f}  = ${r['Capital']:>8,.2f}  "
                f"(Score {r['Score']:.1f}){gap_info}"
            )
        if daily_buy_limit > 0:
            remaining = daily_buy_limit - buy_total
            lines.append(f"       Remaining budget: ${remaining:,.2f}")
        lines.append("")

    if not deferred.empty:
        lines.append(f"  DEFERRED — budget exhausted ({len(deferred)} stocks):")
        for _, r in deferred.iterrows():
            gap_info = f"  gap {r.get('GapPct', 0):.1f}%" if has_gap else ""
            lines.append(
                f"  ---  {r['Ticker']:6s}  Score {r['Score']:.1f}  "
                f"@ ${r['Price']:.2f}{gap_info}"
            )
        lines.append("")

    if not holds.empty:
        lines.append(f"  HOLD — at target weight ({len(holds)} stocks):")
        for _, r in holds.iterrows():
            gap_info = ""
            if has_gap:
                gap_info = f"  [{r.get('ActualPct', 0):.1f}%/{r.get('TargetPct', 0):.1f}%]"
            lines.append(
                f"       HOLD  {r['Ticker']:6s}  Score {r['Score']:.1f}{gap_info}"
            )
        lines.append("")

    lines.append("-" * 55)
    n_buy = len(all_buys)
    summary_parts = []
    if len(stop_losses): summary_parts.append(f"{len(stop_losses)} STOP_LOSS")
    summary_parts.append(f"{len(sells)} SELL")
    if len(sell_grace): summary_parts.append(f"{len(sell_grace)} GRACE")
    if len(trims): summary_parts.append(f"{len(trims)} TRIM")
    summary_parts.append(f"{n_buy} BUY (${buy_total:,.0f})")
    summary_parts.append(f"{len(holds)} HOLD")
    if len(deferred): summary_parts.append(f"{len(deferred)} DEFERRED")
    lines.append(f"  Summary: {', '.join(summary_parts)}")

    lines.append(_build_portfolio_section(holdings_mgr, regime, vix))
    lines.append(_build_cash_section(holdings_mgr, daily_buy_limit))
    lines.append(_build_health_section(health))

    lines.append(f"\n{'=' * 55}")
    lines.append("After executing, open the launcher and click")
    lines.append("'T10 Report Execution' to record what you actually traded.")

    return "\n".join(lines)


def _build_portfolio_section(holdings_mgr, regime, vix):
    pnl = holdings_mgr.get_pnl_summary()
    lines = [
        f"\n[Portfolio Status]",
        f"  Value    : ${pnl['total_value']:>12,.2f}",
        f"  Cost     : ${pnl['total_cost']:>12,.2f}",
        f"  PnL      : ${pnl['total_pnl']:>12,.2f} ({pnl['pnl_pct']:+.2f}%)",
        f"  Holdings : {pnl['holdings_count']} stocks",
        f"  Regime   : {regime} (VIX={vix:.1f})",
    ]
    return "\n".join(lines)


def _build_cash_section(holdings_mgr, daily_buy_limit):
    try:
        balance = holdings_mgr.get_cash_balance()
        deposited = holdings_mgr.get_total_deposited()
    except Exception:
        return ""
    budget = daily_buy_limit if daily_buy_limit > 0 else balance
    lines = [
        f"\n[Cash Status]",
        f"  Available      : ${balance:>12,.2f}",
        f"  Today's budget : ${budget:>12,.2f}  (adaptive)",
        f"  Total deposited: ${deposited:>12,.2f}",
    ]
    return "\n".join(lines)


def _build_health_section(health):
    if not health:
        return ""
    lines = [f"\n[Cache Health]"]
    lines.append(f"  Status: {health.get('overall_status', '?')}")
    vix_h = health.get("vix", {})
    lines.append(f"  VIX cache: {vix_h.get('status', '?')} (latest={vix_h.get('latest_date', '?')})")
    if health.get("ohlcv_stale"):
        lines.append(f"  Stale: {health['ohlcv_stale'][:5]}")
    return "\n".join(lines)


def send_daily_email(
    conf: dict,
    triggers: List[str],
    recos: pd.DataFrame,
    vix: float,
    regime: str,
    holdings_mgr,
    health: dict,
    computed_daily_limit: float = 0.0,
    universe_delta_text: str = "",
):
    """Send daily email via Gmail SMTP."""
    email_conf = conf.get("email", {})
    if not email_conf.get("enabled", False):
        return

    gmail_addr = email_conf["gmail_address"]
    gmail_pass = _resolve_password(email_conf.get("gmail_app_password", ""))
    recipient = email_conf.get("recipient", gmail_addr)

    if not gmail_addr or not gmail_pass:
        print("  [WARN] Gmail credentials not configured, skipping email.")
        return

    rebalance_mode = conf.get("strategy", {}).get("rebalance_mode", "daily")
    trigger_str = ", ".join(triggers) if triggers else "NO_TRIGGER"
    is_preview = (not triggers) and (rebalance_mode != "daily")

    should_send = True
    if is_preview and not email_conf.get("send_daily_summary", True):
        should_send = False
    if health and health.get("overall_status") != "OK" and email_conf.get("send_on_cache_error", True):
        should_send = True

    if not should_send:
        return

    buy_actions = ["BUY", "BUY_NEW", "BUY_MORE"]
    buys = recos[recos["Action"].isin(buy_actions)] if not recos.empty else pd.DataFrame()
    buy_total = buys["Capital"].sum() if not buys.empty else 0
    sells = recos[recos["Action"].isin(["SELL", "TRIM"])] if not recos.empty else pd.DataFrame()
    stop_losses = recos[recos["Action"] == "STOP_LOSS"] if not recos.empty else pd.DataFrame()

    today = datetime.now().strftime("%Y-%m-%d")
    parts = []
    if not stop_losses.empty:
        parts.append(f"{len(stop_losses)} STOP_LOSS")
    if not sells.empty:
        parts.append(f"{len(sells)} SELL")
    if not buys.empty:
        parts.append(f"{len(buys)} BUY ${buy_total:,.0f}")

    if is_preview:
        action_summary = f" | {' / '.join(parts)}" if parts else ""
        subject = f"[Quant Preview] {today}{action_summary} | {regime} VIX={vix:.1f}"
    elif parts:
        prefix = "[URGENT]" if not stop_losses.empty else "[Quant TODO]"
        subject = f"{prefix} {today} | {' / '.join(parts)} | {regime} VIX={vix:.1f}"
    else:
        subject = f"[Quant] {today} | {trigger_str} | {regime} VIX={vix:.1f}"

    daily_limit = computed_daily_limit if computed_daily_limit > 0 else \
        conf.get("portfolio", {}).get("daily_buy_limit", 0.0)
    body = _build_trigger_body(
        triggers, recos, vix, regime, holdings_mgr, health,
        daily_buy_limit=daily_limit,
        universe_delta_text=universe_delta_text,
    )

    msg = MIMEMultipart()
    msg["From"] = gmail_addr
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_addr, gmail_pass)
        server.send_message(msg)

    print(f"  Email sent to {recipient}: {subject}")
