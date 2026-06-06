"""Autotrade — Simple Operator Dashboard (V2 full-auto).

A deliberately minimal monitoring panel for the continuous, hands-off
auto-trader. Once the system is armed-standing + launchd-scheduled, the
operator does not push buttons day-to-day — they just want to *see* that
things are firing. This panel surfaces exactly that:

  1. 발사 현황 (natural language)  — today's 07:20 T7 + 22:35 trade fire
     state in plain Korean.
  2. 예약 상태 + ±15일 캘린더       — are both launchd fires scheduled,
     is global-halt set, is standing-arm present; plus a month-style grid
     colouring every past/future fire (green=done, yellow=scheduled,
     red=abnormal, …) so the operator can eyeball reliability at a glance.
  3. 포트폴리오                     — return % at the last T7 mark-to-market
     and day-over-day change.
  4. 최소 조작 버튼                  — STOP / Clear-halt, standing-arm
     on/off, open logs, and an escape hatch to the advanced panel.

Everything heavy lives in the (already tested) modules ``v1_status``,
``v1_arm``, ``global_halt``, ``auto_halt``, ``trading_calendar`` and
``holdings_manager``. The launch outcomes that are not otherwise persisted
(per-day, per-fire) are recovered by parsing the two launchd stdout logs.

The pure helpers below carry no Tk dependency so they can be unit-tested.

Run:  python -m phase3.autotrade.auto_panel
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_REPO_ROOT, _PHASE3):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import global_halt, trading_calendar, v1_arm  # noqa: E402

try:  # optional — fire history is a nice-to-have supplement
    from phase3.autotrade import auto_halt
except Exception:  # noqa: BLE001
    auto_halt = None  # type: ignore

try:
    from phase3.autotrade import v1_status
except Exception:  # noqa: BLE001
    v1_status = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

RUNTIME_DIR = _HERE / "runtime"
T7_LOG = RUNTIME_DIR / "v1_t7_launchd.out.log"
TRADE_LOG = RUNTIME_DIR / "v1_launchd.out.log"
STATUS_PATH = RUNTIME_DIR / "v1_status.json"

LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
T7_LABEL = "com.autotrade.v1.t7"
TRADE_LABEL = "com.autotrade.v1.daily"

# Scheduled local (assumed KST) fire times.
T7_FIRE_TIME = time(7, 20)
TRADE_FIRE_TIME = time(22, 35)

CAL_DAYS_BACK = 15
CAL_DAYS_FWD = 15

# Status enum → colour + short glyph. Up=green good, red bad, yellow waiting.
STATUS_COLORS: Dict[str, str] = {
    "done": "#2e7d32",       # green — completed rc=0
    "fail": "#c62828",       # red — finished rc != 0
    "scheduled": "#f9a825",  # yellow — healthy, waiting to fire
    "blocked": "#ef6c00",    # orange — scheduled but will skip (halt/arm/uninstalled)
    "running": "#1565c0",    # blue — in flight now
    "skip": "#9e9e9e",       # gray — intentionally skipped (past)
    "missing": "#b71c1c",    # dark red — past trading day, no record (missed)
    "preview": "#00897b",    # teal — non-trading-day T7 dry-run preview
    "closed": "#d0d0d0",     # light gray — market closed, fire N/A
    "none": "#e8e8e8",       # blank
}

STATUS_LABEL_KO: Dict[str, str] = {
    "done": "완료",
    "fail": "실패",
    "scheduled": "예약",
    "blocked": "주의",
    "running": "실행중",
    "skip": "건너뜀",
    "missing": "기록없음",
    "preview": "미리보기",
    "closed": "휴장",
    "none": "",
}


# ──────────────────────────────────────────────────────────────────────
# Pure helpers (no Tk) — unit-tested
# ──────────────────────────────────────────────────────────────────────
@dataclass
class FireOutcome:
    """Terminal outcome of one fire on one KST session date."""
    kst_date: date
    kind: str            # "done" | "skip"
    rc: Optional[int]
    ts_kst: datetime
    msg: str = ""


_LOG_LINE_RE = re.compile(r"^\[v1\]\s+(\S+)\s+(.*)$")
_FINISHED_RE = re.compile(r"(?:T7 prefetch|V1 pipeline) FINISHED — rc=(\d+)")
_SKIP_RE = re.compile(r"\bSKIP\b\s+—")


def to_kst_date(iso_utc: str) -> Optional[date]:
    """Convert an ISO-8601 timestamp (UTC) to its KST *session* date.

    The 07:20 KST T7 fire is logged at 22:20 UTC the previous calendar day;
    adding the +09:00 offset recovers the intended trading-session date for
    both the morning T7 fire and the evening trade fire.
    """
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def to_kst(iso_utc: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def parse_fire_log(text: str) -> Dict[date, FireOutcome]:
    """Parse a launchd stdout log into {kst_date: last terminal FireOutcome}.

    Terminal events are ``… FINISHED — rc=N`` (done) and ``… SKIP —`` (skip).
    The last terminal event per KST session date wins (handles manual reruns).
    """
    out: Dict[date, FireOutcome] = {}
    for raw in text.splitlines():
        m = _LOG_LINE_RE.match(raw.strip())
        if not m:
            continue
        ts_str, msg = m.group(1), m.group(2)
        d = to_kst_date(ts_str)
        if d is None:
            continue
        fin = _FINISHED_RE.search(msg)
        if fin:
            out[d] = FireOutcome(d, "done", int(fin.group(1)), to_kst(ts_str), msg)
            continue
        if _SKIP_RE.search(msg):
            out[d] = FireOutcome(d, "skip", 0, to_kst(ts_str), msg)
    return out


def load_fire_outcomes() -> Tuple[Dict[date, FireOutcome], Dict[date, FireOutcome]]:
    """Return (t7_outcomes, trade_outcomes) parsed from the launchd logs."""
    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return parse_fire_log(_read(T7_LOG)), parse_fire_log(_read(TRADE_LOG))


def fire_status(
    *,
    day: date,
    fire: str,                      # "t7" | "trade"
    outcome: Optional[FireOutcome],
    now_kst: datetime,
    is_trading: bool,
    installed: bool,
    halted: bool,
    standing_armed: bool,
    running: bool,
    history_start: Optional[date] = None,
) -> str:
    """Resolve the colour-status for one fire on one day."""
    if running:
        return "running"

    if not is_trading:
        # Market closed: no trade fire; T7 degrades to a dry-run preview.
        if fire == "trade":
            return "closed"
        if outcome and outcome.kind == "done":
            return "preview"
        return "closed"

    if outcome is not None:
        if outcome.kind == "skip":
            return "skip"
        return "done" if (outcome.rc == 0) else "fail"

    # No recorded outcome on a trading day.
    fire_time = T7_FIRE_TIME if fire == "t7" else TRADE_FIRE_TIME
    fire_dt = datetime.combine(day, fire_time, tzinfo=KST)
    if now_kst < fire_dt:
        # Future / later today → scheduled. Flag conditions that would skip it.
        if halted:
            return "blocked"
        if not installed:
            return "blocked"
        if fire == "trade" and not standing_armed:
            return "blocked"
        return "scheduled"
    # Past trading-day fire with no log line. If it predates the recorded
    # history (system wasn't running/logging yet) leave it blank; otherwise
    # it is almost certainly a missed run (Mac asleep / agent not loaded).
    if history_start is None or day < history_start:
        return "none"
    return "missing"


@dataclass
class DayCell:
    day: date
    is_trading: bool
    is_today: bool
    t7_status: str
    trade_status: str


def build_calendar(
    *,
    today: date,
    now_kst: datetime,
    t7_outcomes: Dict[date, FireOutcome],
    trade_outcomes: Dict[date, FireOutcome],
    t7_installed: bool,
    trade_installed: bool,
    halted: bool,
    standing_armed: bool,
    running_fire: str = "",          # "t7" | "trade" | ""
    days_back: int = CAL_DAYS_BACK,
    days_fwd: int = CAL_DAYS_FWD,
    is_trading_day_fn: Callable[[date], bool] = trading_calendar.is_trading_day,
) -> List[DayCell]:
    """Build the per-day fire-status model for [today-back, today+fwd]."""
    cells: List[DayCell] = []
    t7_start = min(t7_outcomes) if t7_outcomes else None
    trade_start = min(trade_outcomes) if trade_outcomes else None
    d = today - timedelta(days=days_back)
    end = today + timedelta(days=days_fwd)
    while d <= end:
        is_trading = is_trading_day_fn(d)
        t7s = fire_status(
            day=d, fire="t7", outcome=t7_outcomes.get(d), now_kst=now_kst,
            is_trading=is_trading, installed=t7_installed, halted=halted,
            standing_armed=standing_armed,
            running=(d == today and running_fire == "t7"),
            history_start=t7_start,
        )
        trs = fire_status(
            day=d, fire="trade", outcome=trade_outcomes.get(d), now_kst=now_kst,
            is_trading=is_trading, installed=trade_installed, halted=halted,
            standing_armed=standing_armed,
            running=(d == today and running_fire == "trade"),
            history_start=trade_start,
        )
        cells.append(DayCell(d, is_trading, d == today, t7s, trs))
        d += timedelta(days=1)
    return cells


def _fmt_hhmm(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M") if dt else "--:--"


def natural_language_status(
    *,
    today: date,
    now_kst: datetime,
    is_trading: bool,
    t7_status: str,
    trade_status: str,
    t7_outcome: Optional[FireOutcome],
    trade_outcome: Optional[FireOutcome],
    running_fire: str = "",
    running_stage: str = "",
    running_elapsed_s: Optional[int] = None,
    t7_recos: Optional[int] = None,
) -> List[str]:
    """Plain-Korean sentences describing today's two fires."""
    wd = "월화수목금토일"[today.weekday()]
    lines: List[str] = [f"오늘 {today.month}/{today.day}({wd}) — "
                        + ("정규 거래일" if is_trading else "휴장일 (자동매매 없음)")]

    def _sentence(fire_ko: str, sched: str, status: str,
                  outcome: Optional[FireOutcome], extra: str = "") -> str:
        glyph = {"done": "🟢", "fail": "🔴", "scheduled": "🟡", "blocked": "🟠",
                 "running": "🔵", "skip": "⚪", "missing": "🔴",
                 "preview": "🟢", "closed": "⚪"}.get(status, "•")
        if status == "running":
            el = f" (경과 {running_elapsed_s}s)" if running_elapsed_s is not None else ""
            return f"{glyph} {sched} {fire_ko}: 지금 실행 중 — {running_stage or '진행'}{el}"
        if status in ("done", "preview"):
            t = _fmt_hhmm(outcome.ts_kst) if outcome else "--:--"
            tag = "미리보기 메일 발송" if status == "preview" else "정상 완료"
            return f"{glyph} {sched} {fire_ko}: {tag} (rc=0, {t} KST){extra}"
        if status == "fail":
            t = _fmt_hhmm(outcome.ts_kst) if outcome else "--:--"
            rc = outcome.rc if outcome else "?"
            return f"{glyph} {sched} {fire_ko}: 실패 (rc={rc}, {t} KST) — 확인 필요"
        if status == "missing":
            return f"{glyph} {sched} {fire_ko}: 기록 없음 — 미발사 의심 (확인 필요)"
        if status == "skip":
            return f"{glyph} {sched} {fire_ko}: 건너뜀"
        if status == "blocked":
            return f"{glyph} {sched} {fire_ko}: 예약됐으나 발사 안 됨 (halt/arm/미설치 — 확인 필요)"
        if status == "scheduled":
            return f"{glyph} {sched} {fire_ko}: 예약 대기 중 (자동 발사 예정)"
        if status == "closed":
            return f"{glyph} {sched} {fire_ko}: 휴장 — 발사 없음"
        return f"{glyph} {sched} {fire_ko}: -"

    extra7 = f", 추천 {t7_recos}종목" if (t7_status == "done" and t7_recos) else ""
    lines.append(_sentence("T7 추천생성", "07:20", t7_status, t7_outcome, extra7))
    lines.append(_sentence("자동매매", "22:35", trade_status, trade_outcome))
    return lines


def launchctl_loaded(label: str, uid: Optional[int] = None) -> bool:
    """True if the launchd agent is currently loaded for this GUI session."""
    if uid is None:
        uid = os.getuid()
    try:
        cp = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=5,
        )
        return cp.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def agent_installed(label: str) -> bool:
    """True if the agent plist exists in ~/Library/LaunchAgents."""
    return (LAUNCH_AGENTS / f"{label}.plist").exists()


@dataclass
class ReservationHealth:
    t7_installed: bool
    t7_loaded: bool
    trade_installed: bool
    trade_loaded: bool
    standing_armed: bool
    standing_note: str
    halted: bool
    halt_reason: str
    attention: List[str] = field(default_factory=list)


def reservation_health() -> ReservationHealth:
    h = global_halt.read_halt()
    standing = v1_arm.read_standing_arm(runtime_dir=RUNTIME_DIR)
    rh = ReservationHealth(
        t7_installed=agent_installed(T7_LABEL),
        t7_loaded=launchctl_loaded(T7_LABEL),
        trade_installed=agent_installed(TRADE_LABEL),
        trade_loaded=launchctl_loaded(TRADE_LABEL),
        standing_armed=bool(standing),
        standing_note=str((standing or {}).get("note", "")),
        halted=bool(h.halted),
        halt_reason=str(getattr(h, "reason", "") or ""),
    )
    if rh.halted:
        rh.attention.append(f"Global halt 설정됨 — 모든 발사 중단 ({rh.halt_reason})")
    if not (rh.t7_installed and rh.t7_loaded):
        rh.attention.append("T7(07:20) launchd 예약이 설치/로드되지 않음")
    if not (rh.trade_installed and rh.trade_loaded):
        rh.attention.append("auto-trade(22:35) launchd 예약이 설치/로드되지 않음")
    if not rh.standing_armed:
        rh.attention.append("영속 Arm 없음 — 매매 발사가 실행돼도 거래는 건너뜀")
    return rh


@dataclass
class PortfolioSummary:
    ok: bool
    total_value: float = 0.0      # holdings MarketValue only
    cash: float = 0.0
    total_capital: float = 0.0    # holdings + cash (account equity)
    deposited: float = 0.0        # principal (INIT + deposits)
    cum_return_pct: float = 0.0   # (equity - principal) / principal
    holdings_count: int = 0
    dod_pct: Optional[float] = None
    last_date: str = ""
    prev_date: str = ""
    error: str = ""


def portfolio_summary(holdings_log_path: Optional[Path] = None) -> PortfolioSummary:
    """Return account-level return % + day-over-day equity change.

    "수익율" is the account return on principal (equity vs deposited), and the
    day-over-day change is computed on TotalCapital (holdings + cash) so it
    reflects true performance rather than cash being deployed into positions.
    """
    try:
        from holdings_manager import HoldingsManager  # noqa: E402
        if holdings_log_path is None:
            holdings_log_path = _resolve_holdings_path()
        hm = HoldingsManager(str(holdings_log_path))
        pnl = hm.get_pnl_summary()
        value = float(pnl.get("total_value", 0.0))
        cash = float(hm.get_cash_balance())
        deposited = float(hm.get_total_deposited())
        equity = value + max(cash, 0.0)
        cum = ((equity - deposited) / deposited * 100.0) if deposited else 0.0
        dod, last_d, prev_d = _dod_from_daily_log(hm)
        return PortfolioSummary(
            ok=True, total_value=value, cash=cash, total_capital=equity,
            deposited=deposited, cum_return_pct=cum,
            holdings_count=int(pnl.get("holdings_count", 0)),
            dod_pct=dod, last_date=last_d, prev_date=prev_d,
        )
    except Exception as e:  # noqa: BLE001
        return PortfolioSummary(ok=False, error=f"{type(e).__name__}: {e}")


def _dod_from_daily_log(hm) -> Tuple[Optional[float], str, str]:
    """Day-over-day equity change from the last two DailyLog rows.

    Prefers ``TotalCapital`` (holdings+cash) over ``PortfolioValue`` so the
    figure is a true return, not an artefact of cash being invested.
    """
    try:
        df = hm.load_daily_log()
    except Exception:  # noqa: BLE001
        return None, "", ""
    if df is None or df.empty:
        return None, "", ""
    col = "TotalCapital" if "TotalCapital" in df.columns else (
        "PortfolioValue" if "PortfolioValue" in df.columns else None)
    if col is None:
        return None, "", ""
    d = df.copy()
    if "Date" in d.columns:
        d = d.sort_values("Date")
    d = d[d[col].notna() & (d[col] != 0)]
    if len(d) < 2:
        last = d.iloc[-1] if len(d) else None
        return None, (str(last["Date"])[:10] if last is not None and "Date" in d.columns else ""), ""
    last, prev = d.iloc[-1], d.iloc[-2]
    pv, ppv = float(last[col]), float(prev[col])
    dod = ((pv - ppv) / ppv * 100.0) if ppv else None
    last_d = str(last["Date"])[:10] if "Date" in d.columns else ""
    prev_d = str(prev["Date"])[:10] if "Date" in d.columns else ""
    return dod, last_d, prev_d


def _resolve_holdings_path() -> Path:
    import yaml
    cfg_path = _PHASE3 / "config.yaml"
    if not cfg_path.exists():
        cfg_path = _PHASE3 / "config_real.yaml"
    conf = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Path(conf.get("paths", {}).get("holdings_log", "")).expanduser()


def read_status_snapshot():
    """Return (fire_label, running_fire, current_stage, elapsed_s, final_rc,
    finished_kst, run_id, t7_recos) from v1_status.json, best-effort."""
    if v1_status is None:
        return None
    try:
        snap = v1_status.read_status(STATUS_PATH)
    except Exception:  # noqa: BLE001
        return None
    return snap


# ──────────────────────────────────────────────────────────────────────
# Tk UI
# ──────────────────────────────────────────────────────────────────────
def launch_panel() -> None:  # pragma: no cover - UI glue
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Autotrade — 발사 현황 (Simple)")
    root.geometry("760x880")

    body = ttk.Frame(root, padding=10)
    body.pack(fill="both", expand=True)

    # ── Section 1: 발사 현황 ──
    f_status = ttk.LabelFrame(body, text="① 오늘 발사 현황", padding=8)
    f_status.pack(fill="x", pady=(0, 8))
    status_text = tk.Text(f_status, height=4, wrap="word", relief="flat",
                          font=("Helvetica", 13), bg=root.cget("bg"),
                          borderwidth=0, highlightthickness=0)
    status_text.pack(fill="x")
    status_text.configure(state="disabled")

    # ── Section 2: 예약 상태 + 캘린더 ──
    f_res = ttk.LabelFrame(body, text="② 예약 상태 & 발사 캘린더 (±15일)", padding=8)
    f_res.pack(fill="both", expand=True, pady=(0, 8))
    res_text = tk.Text(f_res, height=4, wrap="word", relief="flat",
                       font=("Helvetica", 11), bg=root.cget("bg"),
                       borderwidth=0, highlightthickness=0)
    res_text.pack(fill="x")
    res_text.configure(state="disabled")

    legend = ttk.Frame(f_res)
    legend.pack(fill="x", pady=(4, 2))
    for st in ("done", "scheduled", "blocked", "fail", "missing", "running",
               "skip", "preview", "closed"):
        chip = tk.Label(legend, text=f" {STATUS_LABEL_KO[st]} ",
                        bg=STATUS_COLORS[st], fg="white", font=("Helvetica", 8))
        chip.pack(side="left", padx=1)

    cal_holder = ttk.Frame(f_res)
    cal_holder.pack(fill="both", expand=True, pady=(4, 0))

    # ── Section 3: 포트폴리오 ──
    f_pf = ttk.LabelFrame(body, text="③ 포트폴리오 (마지막 T7 시점)", padding=8)
    f_pf.pack(fill="x", pady=(0, 8))
    pf_text = tk.Text(f_pf, height=3, wrap="word", relief="flat",
                      font=("Helvetica", 13), bg=root.cget("bg"),
                      borderwidth=0, highlightthickness=0)
    pf_text.pack(fill="x")
    pf_text.configure(state="disabled")

    # ── Section 4: 버튼 ──
    f_btn = ttk.LabelFrame(body, text="④ 조작", padding=8)
    f_btn.pack(fill="x")
    row1 = ttk.Frame(f_btn); row1.pack(fill="x", pady=2)
    row2 = ttk.Frame(f_btn); row2.pack(fill="x", pady=2)

    def _set(widget: "tk.Text", text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _draw_calendar(cells: List[DayCell]) -> None:
        for w in cal_holder.winfo_children():
            w.destroy()
        headers = ["월", "화", "수", "목", "금", "토", "일"]
        for c, h in enumerate(headers):
            ttk.Label(cal_holder, text=h, anchor="center",
                      font=("Helvetica", 9, "bold")).grid(
                row=0, column=c, sticky="nsew", padx=1, pady=1)
            cal_holder.grid_columnconfigure(c, weight=1, uniform="cal")
        if not cells:
            return
        start = cells[0].day
        start -= timedelta(days=start.weekday())  # back to Monday
        by_day = {c.day: c for c in cells}
        last = cells[-1].day
        rownum, d = 1, start
        while d <= last:
            for col in range(7):
                cur = d + timedelta(days=col)
                cell = by_day.get(cur)
                fr = tk.Frame(cal_holder, bd=1, relief="solid",
                              bg="#ffffff" if cell else "#f4f4f4")
                fr.grid(row=rownum, column=col, sticky="nsew", padx=1, pady=1)
                if cell is None:
                    tk.Label(fr, text=f"{cur.day}", fg="#bbbbbb",
                             bg="#f4f4f4", font=("Helvetica", 9)).pack(anchor="w")
                    continue
                hdr_fg = "#222" if cell.is_trading else "#aaaaaa"
                top = tk.Frame(fr, bg="#fff7cc" if cell.is_today else "#ffffff")
                top.pack(fill="x")
                tk.Label(top, text=f"{cur.month}/{cur.day}", fg=hdr_fg,
                         bg=top.cget("bg"),
                         font=("Helvetica", 9,
                               "bold" if cell.is_today else "normal")
                         ).pack(side="left")
                badges = tk.Frame(fr, bg="#ffffff"); badges.pack(fill="x", pady=1)
                for tag, st in (("T7", cell.t7_status), ("매매", cell.trade_status)):
                    tk.Label(badges, text=f" {tag} ", bg=STATUS_COLORS.get(st, "#eee"),
                             fg="white" if st not in ("closed", "none") else "#888",
                             font=("Helvetica", 8)).pack(side="left", padx=1)
            rownum += 1
            d += timedelta(days=7)

    def refresh() -> None:
        now_kst = datetime.now(KST)
        today = now_kst.date()
        t7_out, trade_out = load_fire_outcomes()
        rh = reservation_health()

        snap = read_status_snapshot()
        running_fire, running_stage, running_elapsed, t7_recos = "", "", None, None
        if snap is not None and getattr(snap, "in_progress", False):
            fl = getattr(snap, "fire_label", "")
            running_fire = "t7" if fl == "t7_prefetch" else ("trade" if fl == "trade" else "")
            running_stage = getattr(snap, "current_stage", "")
            started = to_kst(getattr(snap, "started_at_utc", "") or "")
            if started:
                running_elapsed = max(0, int((now_kst - started).total_seconds()))
        # recos count for today's T7, if the last status was a t7 fire
        if snap is not None and getattr(snap, "fire_label", "") == "t7_prefetch":
            for s in getattr(snap, "stages_done", []) or []:
                ex = s.get("extra", {}) if isinstance(s, dict) else {}
                if isinstance(ex, dict) and "recommendations_count" in ex:
                    t7_recos = ex.get("recommendations_count")

        is_trading = trading_calendar.is_trading_day(today)
        cells = build_calendar(
            today=today, now_kst=now_kst,
            t7_outcomes=t7_out, trade_outcomes=trade_out,
            t7_installed=rh.t7_installed and rh.t7_loaded,
            trade_installed=rh.trade_installed and rh.trade_loaded,
            halted=rh.halted, standing_armed=rh.standing_armed,
            running_fire=running_fire,
        )
        today_cell = next((c for c in cells if c.is_today), None)
        t7s = today_cell.t7_status if today_cell else "none"
        trs = today_cell.trade_status if today_cell else "none"

        # Section 1
        lines = natural_language_status(
            today=today, now_kst=now_kst, is_trading=is_trading,
            t7_status=t7s, trade_status=trs,
            t7_outcome=t7_out.get(today), trade_outcome=trade_out.get(today),
            running_fire=running_fire, running_stage=running_stage,
            running_elapsed_s=running_elapsed, t7_recos=t7_recos,
        )
        _set(status_text, "\n".join(lines))

        # Section 2
        def yn(ok: bool) -> str:
            return "✅" if ok else "❌"
        rlines = [
            f"T7(07:20) 예약: {yn(rh.t7_installed and rh.t7_loaded)}    "
            f"auto-trade(22:35) 예약: {yn(rh.trade_installed and rh.trade_loaded)}",
            f"영속 Arm: {'✅ ' + (rh.standing_note or 'on') if rh.standing_armed else '⚠️ 없음'}    "
            f"Global Halt: {'🛑 ' + rh.halt_reason if rh.halted else '✅ 정상'}",
        ]
        if rh.attention:
            rlines.append("⚠️ 확인 필요: " + " · ".join(rh.attention))
        else:
            rlines.append("✅ 모든 예약 정상 — 대기 시 자동 발사됩니다.")
        _set(res_text, "\n".join(rlines))
        _draw_calendar(cells)

        # Section 3
        pf = portfolio_summary()
        if pf.ok:
            dod = (f"{pf.dod_pct:+.2f}%" if pf.dod_pct is not None else "n/a")
            _set(pf_text,
                 f"총자산: ${pf.total_capital:,.2f}  "
                 f"(평가 ${pf.total_value:,.2f} + 현금 ${pf.cash:,.2f}, 보유 {pf.holdings_count}종목)\n"
                 f"누적 수익률: {pf.cum_return_pct:+.2f}%  (원금 ${pf.deposited:,.0f} 대비)\n"
                 f"전거래일 대비: {dod}"
                 + (f"  ({pf.prev_date} → {pf.last_date})" if pf.last_date else ""))
        else:
            _set(pf_text, f"포트폴리오 로드 실패: {pf.error}")

    # ── buttons ──
    def _do_stop() -> None:
        reason = "operator STOP (simple panel)"
        global_halt.write_halt(halt=True, reason=reason, operator="auto_panel")
        messagebox.showwarning("STOP", "Global halt 설정됨 — 다음 발사부터 모두 중단됩니다.")
        refresh()

    def _do_clear() -> None:
        if not messagebox.askyesno("Clear halt", "Global halt을 해제할까요? (자동 발사 재개)"):
            return
        global_halt.clear_halt(operator="auto_panel")
        refresh()

    def _do_arm() -> None:
        if not messagebox.askyesno(
                "영속 Arm", "영속 Arm을 설정할까요?\n(매일 22:35 자동매매가 계속 실행됩니다)"):
            return
        v1_arm.write_standing_arm(runtime_dir=RUNTIME_DIR, note="armed via simple panel")
        refresh()

    def _do_disarm() -> None:
        if not messagebox.askyesno(
                "영속 Arm 해제", "영속 Arm을 해제할까요?\n(이후 22:35 매매가 건너뛰어집니다)"):
            return
        v1_arm.clear_standing_arm(runtime_dir=RUNTIME_DIR)
        refresh()

    def _open(path: Path) -> None:
        try:
            subprocess.run(["open", str(path)], check=False)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("열기 실패", str(e))

    def _open_advanced() -> None:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "phase3.autotrade.control_panel"],
                cwd=str(_REPO_ROOT),
                env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("고급 패널 실행 실패", str(e))

    ttk.Button(row1, text="새로고침", command=refresh).pack(side="left", padx=2)
    ttk.Button(row1, text="🛑 STOP (halt)", command=_do_stop).pack(side="left", padx=2)
    ttk.Button(row1, text="Clear halt", command=_do_clear).pack(side="left", padx=2)
    ttk.Button(row1, text="영속 Arm 설정", command=_do_arm).pack(side="left", padx=2)
    ttk.Button(row1, text="영속 Arm 해제", command=_do_disarm).pack(side="left", padx=2)
    ttk.Button(row2, text="T7 로그", command=lambda: _open(T7_LOG)).pack(side="left", padx=2)
    ttk.Button(row2, text="매매 로그", command=lambda: _open(TRADE_LOG)).pack(side="left", padx=2)
    ttk.Button(row2, text="고급 패널 열기", command=_open_advanced).pack(side="left", padx=2)

    def _tick() -> None:
        try:
            refresh()
        finally:
            root.after(5000, _tick)

    refresh()
    root.after(5000, _tick)
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    launch_panel()
