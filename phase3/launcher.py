#!/usr/bin/env python3
"""
Phase 3 Control Center — single GUI for all test & operation functions.

Launch:
    python3 launcher.py
    (or double-click run_phase3.command on macOS)
"""

import io
import os
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime

_THIS_DIR = Path(__file__).resolve().parent
os.chdir(str(_THIS_DIR))
sys.path.insert(0, str(_THIS_DIR))

_engine_loaded = False
_conf = None
_cfg = None
_signal = None
_hm = None
_pack = None


class OutputCapture(io.StringIO):
    """Captures print output and feeds it to the GUI log in real-time.

    Handles \\r (carriage return) from tqdm by replacing the last
    line instead of appending, preventing scroll flooding.
    """

    def __init__(self, callback, replace_callback=None):
        super().__init__()
        self._cb = callback
        self._replace_cb = replace_callback

    def write(self, s):
        if not s or not s.strip():
            return super().write(s)
        if "\r" in s and "\n" not in s and self._replace_cb:
            clean = s.split("\r")[-1].strip()
            if clean:
                self._replace_cb(clean)
        else:
            for part in s.split("\r"):
                text = part.strip()
                if text:
                    self._cb(text)
        return super().write(s)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Phase 3 Control Center")
        self.geometry("900x700")
        self.configure(bg="#1e1e2e")
        self._running = False
        self._build_ui()

    def _build_ui(self):
        top = tk.Frame(self, bg="#1e1e2e")
        top.pack(fill=tk.X, padx=10, pady=(10, 0))

        title = tk.Label(
            top, text="Phase 3 — Live Trading System",
            font=("Helvetica", 18, "bold"), fg="#cdd6f4", bg="#1e1e2e",
        )
        title.pack(side=tk.LEFT)

        self._status = tk.Label(
            top, text="READY", font=("Helvetica", 12),
            fg="#a6e3a1", bg="#1e1e2e",
        )
        self._status.pack(side=tk.RIGHT, padx=10)

        sep = tk.Frame(self, height=2, bg="#45475a")
        sep.pack(fill=tk.X, padx=10, pady=8)

        btn_frame = tk.Frame(self, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, padx=10)

        sec_infra = tk.LabelFrame(
            btn_frame, text=" Infrastructure ", font=("Helvetica", 11, "bold"),
            fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        sec_infra.pack(fill=tk.X, pady=4)

        sec_core = tk.LabelFrame(
            btn_frame, text=" Core Functions ", font=("Helvetica", 11, "bold"),
            fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        sec_core.pack(fill=tk.X, pady=4)

        sec_ops = tk.LabelFrame(
            btn_frame, text=" Operations ", font=("Helvetica", 11, "bold"),
            fg="#f38ba8", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        sec_ops.pack(fill=tk.X, pady=4)

        btn_style = dict(
            font=("Helvetica", 11, "bold"), width=22, height=1,
            bg="#b4befe", fg="#1e1e2e", activebackground="#89b4fa",
            activeforeground="#1e1e2e", bd=0, relief=tk.FLAT, cursor="hand2",
        )

        buttons_infra = [
            ("T1  Engine Check", self._t1_engine),
            ("T2  Cache Health", self._t2_cache),
            ("T3  Holdings CRUD", self._t3_holdings),
            ("T13 Cache Backfill", self._t13_cache_backfill),
            ("T15 Force Overwrite 7d", self._t15_force_overwrite),
        ]
        buttons_core = [
            ("T4  Load Signal + Score", self._t4_signal_score),
            ("T5  VIX & Trigger Check", self._t5_vix_trigger),
            ("T6  Daily Run (Dry)", self._t6_dry_run),
        ]
        buttons_ops_row1 = [
            ("T7  Daily Run (Live)", self._t7_live_run),
            ("T8  Open Dashboard", self._t8_dashboard),
            ("T9  Email Test", self._t9_email),
        ]
        buttons_ops_row2 = [
            ("T10 Report Execution", self._t10_report_exec),
            ("T11 Deposit Cash", self._t11_deposit),
            ("T14 Manual Trade", self._t14_manual_trade),
        ]
        buttons_ops_row3 = [
            ("T12 Cache Heatmap", self._t12_cache_heatmap),
            ("T16 Backtest Sim", self._t16_backtest_sim),
            ("T17 Lab Sweep", self._t17_lab_sweep),
        ]

        for section, btns in [
            (sec_infra, buttons_infra),
            (sec_core, buttons_core),
            (sec_ops, buttons_ops_row1),
        ]:
            row = tk.Frame(section, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=8, pady=6)
            for text, cmd in btns:
                b = tk.Button(row, text=text, command=cmd, **btn_style)
                b.pack(side=tk.LEFT, padx=4)

        row2 = tk.Frame(sec_ops, bg="#1e1e2e")
        row2.pack(fill=tk.X, padx=8, pady=(0, 6))
        for text, cmd in buttons_ops_row2:
            b = tk.Button(row2, text=text, command=cmd, **btn_style)
            b.pack(side=tk.LEFT, padx=4)

        row3 = tk.Frame(sec_ops, bg="#1e1e2e")
        row3.pack(fill=tk.X, padx=8, pady=(0, 6))
        for text, cmd in buttons_ops_row3:
            b = tk.Button(row3, text=text, command=cmd, **btn_style)
            b.pack(side=tk.LEFT, padx=4)

        clear_btn = tk.Button(
            btn_frame, text="Clear Log", command=self._clear_log,
            font=("Helvetica", 10), bg="#45475a", fg="#bac2de",
            activebackground="#585b70", bd=0, cursor="hand2",
        )
        clear_btn.pack(anchor=tk.E, padx=4, pady=4)

        log_frame = tk.Frame(self, bg="#1e1e2e")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

        self._log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD,
            font=("Menlo", 11), bg="#11111b", fg="#cdd6f4",
            insertbackground="#cdd6f4", selectbackground="#585b70",
            selectforeground="#cdd6f4",
            bd=0, relief=tk.FLAT,
        )
        self._log.pack(fill=tk.BOTH, expand=True)

        self._log.bind("<Key>", lambda e: "break" if e.keysym not in (
            "c", "C", "a", "A", "Left", "Right", "Up", "Down", "Home", "End",
        ) and not (e.state & 0x8 or e.state & 0x4) else None)
        self._log.bind("<Command-c>", lambda e: None)
        self._log.bind("<Command-a>", lambda e: (
            self._log.tag_add(tk.SEL, "1.0", tk.END), "break",
        ))

        copy_bar = tk.Frame(log_frame, bg="#11111b")
        copy_bar.pack(fill=tk.X)
        tk.Button(
            copy_bar, text="Copy All", command=self._copy_all,
            font=("Helvetica", 10), bg="#45475a", fg="#cdd6f4",
            activebackground="#585b70", bd=0, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4, pady=2)
        tk.Button(
            copy_bar, text="Copy Selection", command=self._copy_selection,
            font=("Helvetica", 10), bg="#45475a", fg="#cdd6f4",
            activebackground="#585b70", bd=0, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4, pady=2)

    def _log_write(self, text: str):
        def _update():
            self._log.insert(tk.END, text + "\n")
            self._log.see(tk.END)
        self.after(0, _update)

    def _log_replace_last(self, text: str):
        """Replace the last line in the log widget (for progress updates)."""
        def _update():
            last_line_start = self._log.index("end-2l linestart")
            last_line_end = self._log.index("end-2l lineend")
            self._log.delete(last_line_start, last_line_end)
            self._log.insert(last_line_start, text)
            self._log.see(tk.END)
        self.after(0, _update)

    def _clear_log(self):
        self._log.delete("1.0", tk.END)

    def _copy_all(self):
        content = self._log.get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(content)
        self._set_status("Copied all to clipboard", "#a6e3a1")

    def _copy_selection(self):
        try:
            content = self._log.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(content)
            self._set_status("Copied selection to clipboard", "#a6e3a1")
        except tk.TclError:
            self._set_status("No text selected", "#f9e2af")

    def _set_status(self, text, color="#a6e3a1"):
        def _update():
            self._status.configure(text=text, fg=color)
        self.after(0, _update)

    def _run_task(self, name, func):
        if self._running:
            self._log_write("[BUSY] Another task is running, please wait.")
            return
        self._running = True
        self._set_status(f"RUNNING: {name}", "#f9e2af")
        self._log_write(f"\n{'='*60}")
        self._log_write(f"  {name}  —  {datetime.now().strftime('%H:%M:%S')}")
        self._log_write(f"{'='*60}")

        def _worker():
            cap = OutputCapture(self._log_write, self._log_replace_last)
            try:
                with redirect_stdout(cap), redirect_stderr(cap):
                    func()
                self._set_status("DONE", "#a6e3a1")
                self._log_write(f"\n  [{name}] Completed successfully.")
            except Exception as e:
                self._set_status("ERROR", "#f38ba8")
                self._log_write(f"\n  [ERROR] {e}")
                import traceback
                self._log_write(traceback.format_exc())
            finally:
                self._running = False

        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_engine(self):
        global _engine_loaded, _conf, _cfg
        if _engine_loaded:
            return True
        self._log_write("  Loading engine (first time, may take 30-60s)...")
        from engine_loader import engine  # noqa: F811
        from cache_health import load_config
        _conf = load_config()
        from daily_runner import build_engine_cfg
        _cfg = build_engine_cfg(_conf)
        _engine_loaded = True
        self._log_write("  Engine loaded.")
        return True

    def _ensure_signal(self):
        global _signal
        if _signal is not None:
            return True
        self._ensure_engine()
        from daily_runner import load_frozen_signal
        path = _conf["paths"]["frozen_signal"]
        if not os.path.exists(path):
            self._log_write(f"  [ERROR] Frozen signal not found: {path}")
            return False
        _signal = load_frozen_signal(path)
        summary = _signal.get("signal_summary", {})
        self._log_write(
            f"  Signal loaded: k={int(_signal['mask'].sum())} "
            f"MeanIC={summary.get('Invest_MeanIC', '?')} "
            f"Spread={summary.get('Invest_Spread', '?')}"
        )
        return True

    def _ensure_pack(self):
        global _pack
        if _pack is not None:
            return True
        self._ensure_engine()
        import dataclasses
        from engine_loader import engine
        now = datetime.now()
        from datetime import timedelta
        cfg_for_pack = dataclasses.replace(_cfg)
        cfg_for_pack.start_panel_date = now - timedelta(days=365)
        cfg_for_pack.end_date = now
        cfg_for_pack.enable_historical_universe = True
        cfg_for_pack.historical_universe_expand_tickers = True
        cfg_for_pack.enable_coverage_based_universe = True
        self._log_write("  Preparing data inputs (may take 1-2 min)...")
        result = engine.prepare_inputs(cfg_for_pack)
        _pack = result["pack"]
        self._log_write(f"  Pack ready: {len(_pack['tickers'])} tickers, {len(_pack['dates'])} dates")
        return True

    # ── T1: Engine Check ──
    def _t1_engine(self):
        def _run():
            self._ensure_engine()
            from engine_loader import engine
            cfg = engine.Config()
            funcs = [x for x in dir(engine) if callable(getattr(engine, x, None)) and not x.startswith("_")]
            print(f"  Config type: {type(cfg).__name__}")
            print(f"  Public functions: {len(funcs)}")
            print(f"  Key functions: {funcs[:15]}")
            print(f"  Config fields: {len(cfg.__dataclass_fields__)}")
        self._run_task("T1: Engine Check", _run)

    # ── T2: Cache Health ──
    def _t2_cache(self):
        def _run():
            self._ensure_engine()
            from cache_health import run_full_health_check
            result = run_full_health_check()
            print(f"  Overall status: {result['overall_status']}")
            print(f"  SP500 tickers: {result['sp500_ticker_count']}")
            print(f"  OHLCV sampled: {result['ohlcv_sampled']}")
            vix = result.get("vix", {})
            print(f"  VIX status: {vix.get('status', '?')}")
            print(f"  VIX latest: {vix.get('latest_close', '?')} ({vix.get('latest_date', '?')})")
            print(f"  File issues: {result['file_integrity_issues']}")
            if result.get("ohlcv_stale"):
                print(f"  Stale tickers: {result['ohlcv_stale']}")
            if result.get("ohlcv_missing"):
                print(f"  Missing tickers: {result['ohlcv_missing']}")
        self._run_task("T2: Cache Health", _run)

    # ── T3: Holdings CRUD ──
    def _t3_holdings(self):
        def _run():
            import tempfile
            from holdings_manager import HoldingsManager
            test_path = os.path.join(tempfile.gettempdir(), "test_phase3_holdings.xlsx")
            hm = HoldingsManager(test_path)
            print(f"  Created test file: {test_path}")
            print(f"  Current holdings: {len(hm.load_current())} rows")
            print(f"  Portfolio value: ${hm.get_portfolio_value():,.2f}")
            pnl = hm.get_pnl_summary()
            print(f"  PnL summary: {pnl}")
            hm.log_daily(
                trigger_fired=False, trigger_type="TEST",
                vix=18.5, regime="BULL", cash_pct=0.0,
                portfolio_value=0.0, top_holding="",
            )
            print(f"  Daily log after insert: {len(hm.load_daily_log())} rows")
            os.remove(test_path)
            print(f"  Test file cleaned up.")
        self._run_task("T3: Holdings CRUD", _run)

    # ── T15: Force Overwrite Recent Cache ──
    def _t15_force_overwrite(self):
        if not messagebox.askokcancel(
            "T15: Force Overwrite",
            "최근 7일 캐시를 전 종목 강제 재다운로드합니다.\n"
            "장중 partial data 오염이 의심될 때 사용하세요.\n\n"
            "약 10-20분 소요될 수 있습니다. 진행할까요?",
        ):
            return

        def _run():
            self._ensure_engine()
            import importlib, daily_runner
            importlib.reload(daily_runner)
            from engine_loader import engine
            tickers, _ = engine.load_sp500_tickers_ttl(_cfg, ttl_days=30)
            print(f"  Loaded {len(tickers)} SP500 tickers")
            daily_runner.force_overwrite_recent_cache(_cfg, tickers, days=7)
        self._run_task("T15: Force Overwrite 7d", _run)

    # ── T13: Cache Backfill ──
    def _t13_cache_backfill(self):
        def _run():
            self._ensure_engine()
            import importlib, daily_runner
            importlib.reload(daily_runner)
            from engine_loader import engine
            tickers, _ = engine.load_sp500_tickers_ttl(_cfg, ttl_days=30)
            print(f"  Loaded {len(tickers)} SP500 tickers")
            daily_runner.backfill_cache(_cfg, tickers, scan_days=180, max_gap_days=5)
        self._run_task("T13: Cache Backfill (6mo)", _run)

    # ── T4: Signal + Score ──
    def _t4_signal_score(self):
        def _run():
            if not self._ensure_signal():
                return
            if not self._ensure_pack():
                return
            from daily_runner import get_current_vix, compute_today_scores
            vix, regime = get_current_vix(_cfg)
            print(f"  VIX={vix:.2f}, Regime={regime}")
            scores = compute_today_scores(_cfg, _pack, _signal, regime)
            print(f"  Scored {len(scores)} stocks")
            print(f"\n  Top 10:")
            print(scores.head(10).to_string(index=False))
        self._run_task("T4: Signal + Score", _run)

    # ── T5: VIX & Trigger ──
    def _t5_vix_trigger(self):
        def _run():
            if not self._ensure_signal():
                return
            from daily_runner import get_current_vix, check_triggers
            from holdings_manager import HoldingsManager
            vix, regime = get_current_vix(_cfg)
            print(f"  VIX={vix:.2f}, Regime={regime}")

            hm = HoldingsManager(_conf["paths"]["holdings_log"])
            triggers = check_triggers(
                _cfg, vix, regime, hm, pack=None, signal=_signal, force=False,
            )
            print(f"  Normal triggers: {triggers if triggers else 'NONE'}")

            triggers_f = check_triggers(
                _cfg, vix, regime, hm, pack=None, signal=_signal, force=True,
            )
            print(f"  Forced triggers: {triggers_f}")

            last = hm.get_last_rebalance_date()
            if last:
                days = (datetime.now() - last).days
                print(f"  Last rebalance: {last.strftime('%Y-%m-%d')} ({days}d ago)")
            else:
                print(f"  Last rebalance: never (new portfolio)")

            curr = hm.load_current()
            print(f"  Current holdings: {len(curr)} positions")
        self._run_task("T5: VIX & Trigger", _run)

    # ── T6: Daily Dry Run ──
    def _t6_dry_run(self):
        def _run():
            import importlib, daily_runner
            importlib.reload(daily_runner)
            daily_runner.run_daily(dry_run=True, force=True)
        self._run_task("T6: Daily Run (Dry)", _run)

    # ── T7: Daily Live Run ──
    def _t7_live_run(self):
        def _confirm_and_run():
            from holdings_manager import HoldingsManager
            hm = HoldingsManager(_conf["paths"]["holdings_log"])
            today_str = datetime.now().strftime("%Y-%m-%d")
            daily_log = hm.load_daily_log()
            already_ran = (
                not daily_log.empty
                and today_str in daily_log["Date"].astype(str).values
            )

            if already_ran:
                msg = (
                    f"Already ran today ({today_str}).\n"
                    "Re-running will create duplicate log entries.\n\n"
                    "Proceed anyway?"
                )
            else:
                msg = "This will modify holdings_log.xlsx.\nProceed?"

            if not messagebox.askyesno("Confirm Live Run", msg):
                self._log_write("  Cancelled by user.")
                return

            def _run():
                import importlib, daily_runner
                importlib.reload(daily_runner)
                daily_runner.run_daily(dry_run=False, force=True)
            self._run_task("T7: Daily Run (LIVE)", _run)

        _confirm_and_run()

    # ── T8: Dashboard ──
    def _t8_dashboard(self):
        def _run():
            import subprocess
            dash_path = str(_THIS_DIR / "dashboard.py")
            print(f"  Launching Streamlit dashboard...")
            print(f"  URL: http://localhost:8501")
            subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", dash_path],
                cwd=str(_THIS_DIR),
            )
            print(f"  Dashboard process started (runs in background).")
            print(f"  Open http://localhost:8501 in your browser.")
        self._run_task("T8: Dashboard", _run)

    # ── T9: Email Test ──
    def _t9_email(self):
        def _run():
            self._ensure_engine()
            email_conf = _conf.get("email", {})
            if not email_conf.get("enabled"):
                print("  Email is DISABLED in config.yaml")
                print("  To enable: set email.enabled=true and fill in credentials.")
                return
            print(f"  Email enabled: {email_conf.get('gmail_address', '?')}")
            print(f"  Recipient: {email_conf.get('recipient', '?')}")
            from mailer import send_daily_email
            from holdings_manager import HoldingsManager
            import pandas as pd
            hm = HoldingsManager(_conf["paths"]["holdings_log"])
            send_daily_email(
                _conf, triggers=["TEST_EMAIL"],
                recos=pd.DataFrame(), vix=20.0, regime="BULL",
                holdings_mgr=hm, health={"overall_status": "OK"},
            )
            print("  Test email sent!")
        self._run_task("T9: Email Test", _run)

    # ── T10: Report Execution ──
    def _t10_report_exec(self):
        self._ensure_engine()
        import pandas as pd
        from holdings_manager import HoldingsManager
        hm = HoldingsManager(_conf["paths"]["holdings_log"])
        recos = hm.load_recommendations()

        if recos.empty:
            self._log_write("  No recommendations to report. Run T7 first.")
            return

        actionable = recos[recos["Action"].isin(["BUY", "SELL"])].copy()
        if actionable.empty:
            self._log_write("  No BUY/SELL items in recommendations.")
            return

        popup = tk.Toplevel(self)
        popup.title("Report Execution")
        popup.geometry("700x500")
        popup.configure(bg="#1e1e2e")
        popup.transient(self)
        popup.grab_set()

        tk.Label(
            popup, text="Check the items you actually executed:",
            font=("Helvetica", 13, "bold"), fg="#cdd6f4", bg="#1e1e2e",
        ).pack(padx=10, pady=(10, 5))

        canvas = tk.Canvas(popup, bg="#1e1e2e", highlightthickness=0)
        scrollbar = tk.Scrollbar(popup, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e1e2e")

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        check_vars = []
        price_entries = []
        share_entries = []

        for idx, (_, row) in enumerate(actionable.iterrows()):
            item_frame = tk.Frame(scroll_frame, bg="#1e1e2e")
            item_frame.pack(fill=tk.X, padx=5, pady=3)

            var = tk.BooleanVar(value=False)
            check_vars.append(var)

            action_color = "#f38ba8" if row["Action"] == "SELL" else "#a6e3a1"
            cb = tk.Checkbutton(
                item_frame, variable=var, bg="#1e1e2e",
                activebackground="#1e1e2e", selectcolor="#313244",
            )
            cb.pack(side=tk.LEFT)

            tk.Label(
                item_frame, text=f"{row['Action']:4s}",
                font=("Menlo", 11, "bold"), fg=action_color, bg="#1e1e2e",
                width=5,
            ).pack(side=tk.LEFT)

            tk.Label(
                item_frame, text=f"{row['Ticker']:6s}",
                font=("Menlo", 11), fg="#cdd6f4", bg="#1e1e2e", width=7,
            ).pack(side=tk.LEFT)

            tk.Label(
                item_frame, text="shares:", font=("Helvetica", 10),
                fg="#9399b2", bg="#1e1e2e",
            ).pack(side=tk.LEFT, padx=(8, 2))

            shares_var = tk.StringVar(value=str(int(row["Shares"])))
            se = tk.Entry(
                item_frame, textvariable=shares_var, width=6,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            )
            se.pack(side=tk.LEFT)
            share_entries.append(shares_var)

            tk.Label(
                item_frame, text="@ $", font=("Helvetica", 10),
                fg="#9399b2", bg="#1e1e2e",
            ).pack(side=tk.LEFT, padx=(8, 2))

            price_var = tk.StringVar(value=f"{row['Price']:.2f}")
            pe = tk.Entry(
                item_frame, textvariable=price_var, width=10,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            )
            pe.pack(side=tk.LEFT)
            price_entries.append(price_var)

            cost = row["Price"] * row["Shares"]
            tk.Label(
                item_frame, text=f"= ${cost:,.0f}",
                font=("Helvetica", 10), fg="#9399b2", bg="#1e1e2e",
            ).pack(side=tk.LEFT, padx=8)

        def _apply():
            executed_rows = []
            for i, (_, row) in enumerate(actionable.iterrows()):
                if check_vars[i].get():
                    try:
                        price = float(price_entries[i].get())
                        shares = int(share_entries[i].get())
                    except ValueError:
                        messagebox.showerror("Error", f"Invalid price/shares for {row['Ticker']}")
                        return
                    executed_rows.append({
                        "Ticker": row["Ticker"],
                        "Action": row["Action"],
                        "Price": price,
                        "Shares": shares,
                    })

            if not executed_rows:
                messagebox.showwarning("Nothing selected", "Check at least one item to apply.")
                return

            executed_df = pd.DataFrame(executed_rows)

            total_buy_cost = 0.0
            for r in executed_rows:
                cost = r["Price"] * r["Shares"]
                if r["Action"] == "BUY":
                    total_buy_cost += cost

            cash_before = hm.get_cash_balance()
            if total_buy_cost > cash_before + 0.01:
                messagebox.showerror(
                    "Insufficient Cash",
                    f"Buy total ${total_buy_cost:,.2f} exceeds "
                    f"cash balance ${cash_before:,.2f}",
                )
                return

            hm.apply_partial_execution(executed_df, trigger_type="PARTIAL")

            for r in executed_rows:
                cost = round(r["Price"] * r["Shares"], 2)
                if r["Action"] == "BUY":
                    hm.record_cash_event("BUY", -cost, f"{r['Ticker']} {r['Shares']}sh")
                elif r["Action"] == "SELL":
                    hm.record_cash_event("SELL", cost, f"{r['Ticker']} {r['Shares']}sh")

            popup.destroy()

            self._log_write(f"\n  [T10] Applied {len(executed_rows)} executions:")
            for r in executed_rows:
                self._log_write(
                    f"    {r['Action']:4s}  {r['Ticker']:6s}  "
                    f"{r['Shares']} shares @ ${r['Price']:.2f}"
                )
            cash_after = hm.get_cash_balance()
            pnl = hm.get_pnl_summary()
            self._log_write(
                f"  Portfolio: ${pnl['total_value']:,.2f} | "
                f"{pnl['holdings_count']} holdings | "
                f"Cash: ${cash_after:,.2f}"
            )

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Button(
            btn_frame, text="Apply Checked", command=_apply,
            font=("Helvetica", 12, "bold"),
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
            bd=0, cursor="hand2", width=18,
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame, text="Cancel", command=popup.destroy,
            font=("Helvetica", 12),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", width=12,
        ).pack(side=tk.LEFT, padx=5)


    # ── T11: Deposit Cash ──
    def _t11_deposit(self):
        from holdings_manager import HoldingsManager
        hm = HoldingsManager(_conf["paths"]["holdings_log"])

        initial_cash = _conf["portfolio"].get(
            "initial_cash", _conf["portfolio"]["total_capital"]
        )
        hm.initialize_cash(initial_cash)

        popup = tk.Toplevel(self)
        popup.title("Deposit Cash")
        popup.geometry("420x280")
        popup.configure(bg="#1e1e2e")
        popup.transient(self)
        popup.grab_set()

        balance = hm.get_cash_balance()
        tk.Label(
            popup, text=f"Current Cash: ${balance:,.2f}",
            font=("Helvetica", 14, "bold"), fg="#a6e3a1", bg="#1e1e2e",
        ).pack(padx=15, pady=(15, 10))

        form = tk.Frame(popup, bg="#1e1e2e")
        form.pack(fill=tk.X, padx=20, pady=5)

        tk.Label(
            form, text="Amount ($):", font=("Helvetica", 12),
            fg="#cdd6f4", bg="#1e1e2e",
        ).grid(row=0, column=0, sticky=tk.W, pady=5)
        amount_var = tk.StringVar()
        tk.Entry(
            form, textvariable=amount_var, width=18,
            font=("Menlo", 13), bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4", bd=0,
        ).grid(row=0, column=1, padx=8, pady=5)

        tk.Label(
            form, text="Notes:", font=("Helvetica", 12),
            fg="#cdd6f4", bg="#1e1e2e",
        ).grid(row=1, column=0, sticky=tk.W, pady=5)
        notes_var = tk.StringVar()
        tk.Entry(
            form, textvariable=notes_var, width=18,
            font=("Menlo", 13), bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4", bd=0,
        ).grid(row=1, column=1, padx=8, pady=5)

        def _do_deposit():
            try:
                amt = float(amount_var.get())
            except ValueError:
                messagebox.showerror("Error", "Enter a valid number.")
                return
            if amt <= 0:
                messagebox.showerror("Error", "Amount must be positive.")
                return
            notes = notes_var.get().strip() or "Manual deposit"
            new_bal = hm.record_cash_event("DEPOSIT", amt, notes)
            popup.destroy()
            self._log_write(
                f"\n  [T11] Deposited ${amt:,.2f} ({notes})"
                f"\n  New cash balance: ${new_bal:,.2f}"
            )

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, padx=20, pady=15)

        tk.Button(
            btn_frame, text="Deposit", command=_do_deposit,
            font=("Helvetica", 12, "bold"),
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
            bd=0, cursor="hand2", width=14,
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame, text="Cancel", command=popup.destroy,
            font=("Helvetica", 12),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", width=10,
        ).pack(side=tk.LEFT, padx=5)


    # ── T14: Manual Trade Entry (retroactive) ──
    def _t14_manual_trade(self):
        import pandas as pd
        from holdings_manager import HoldingsManager
        hm = HoldingsManager(_conf["paths"]["holdings_log"])

        initial_cash = _conf["portfolio"].get(
            "initial_cash", _conf["portfolio"]["total_capital"]
        )
        hm.initialize_cash(initial_cash)

        popup = tk.Toplevel(self)
        popup.title("Manual Trade Entry")
        popup.geometry("620x520")
        popup.configure(bg="#1e1e2e")
        popup.transient(self)
        popup.grab_set()

        balance = hm.get_cash_balance()
        tk.Label(
            popup, text=f"Cash: ${balance:,.2f}",
            font=("Helvetica", 14, "bold"), fg="#a6e3a1", bg="#1e1e2e",
        ).pack(padx=15, pady=(15, 5))

        tk.Label(
            popup,
            text="Enter trades manually (including past dates).",
            font=("Helvetica", 11), fg="#9399b2", bg="#1e1e2e",
        ).pack(padx=15, pady=(0, 10))

        trade_rows = []
        trades_frame = tk.Frame(popup, bg="#1e1e2e")
        trades_frame.pack(fill=tk.BOTH, expand=True, padx=15)

        header = tk.Frame(trades_frame, bg="#1e1e2e")
        header.pack(fill=tk.X, pady=(0, 4))
        for text, w in [("Date", 12), ("Action", 7), ("Ticker", 8), ("Shares", 7), ("Price", 10)]:
            tk.Label(
                header, text=text, font=("Helvetica", 10, "bold"),
                fg="#9399b2", bg="#1e1e2e", width=w, anchor=tk.W,
            ).pack(side=tk.LEFT, padx=2)

        canvas = tk.Canvas(trades_frame, bg="#1e1e2e", highlightthickness=0, height=240)
        scrollbar = tk.Scrollbar(trades_frame, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg="#1e1e2e")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        today_str = datetime.now().strftime("%Y-%m-%d")

        def _add_row():
            row_frame = tk.Frame(inner, bg="#1e1e2e")
            row_frame.pack(fill=tk.X, pady=2)

            date_var = tk.StringVar(value=today_str)
            tk.Entry(
                row_frame, textvariable=date_var, width=12,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            ).pack(side=tk.LEFT, padx=2)

            action_var = tk.StringVar(value="BUY")
            action_menu = tk.OptionMenu(row_frame, action_var, "BUY", "SELL")
            action_menu.configure(
                font=("Menlo", 10), bg="#313244", fg="#a6e3a1",
                activebackground="#45475a", highlightthickness=0, bd=0, width=4,
            )
            action_menu["menu"].configure(bg="#313244", fg="#cdd6f4")
            action_menu.pack(side=tk.LEFT, padx=2)

            ticker_var = tk.StringVar()
            tk.Entry(
                row_frame, textvariable=ticker_var, width=8,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            ).pack(side=tk.LEFT, padx=2)

            shares_var = tk.StringVar()
            tk.Entry(
                row_frame, textvariable=shares_var, width=7,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            ).pack(side=tk.LEFT, padx=2)

            price_var = tk.StringVar()
            tk.Entry(
                row_frame, textvariable=price_var, width=10,
                font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                insertbackground="#cdd6f4", bd=0,
            ).pack(side=tk.LEFT, padx=2)

            trade_rows.append((date_var, action_var, ticker_var, shares_var, price_var))

        _add_row()

        add_btn_frame = tk.Frame(popup, bg="#1e1e2e")
        add_btn_frame.pack(fill=tk.X, padx=15, pady=5)
        tk.Button(
            add_btn_frame, text="+ Add Row", command=_add_row,
            font=("Helvetica", 11), bg="#45475a", fg="#cdd6f4",
            activebackground="#585b70", bd=0, cursor="hand2",
        ).pack(side=tk.LEFT)

        def _apply_trades():
            entries = []
            for date_var, action_var, ticker_var, shares_var, price_var in trade_rows:
                ticker = ticker_var.get().strip().upper()
                if not ticker:
                    continue
                try:
                    trade_date = datetime.strptime(date_var.get().strip(), "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Error", f"Invalid date: {date_var.get()}")
                    return
                try:
                    shares = int(shares_var.get())
                    price = float(price_var.get())
                except ValueError:
                    messagebox.showerror("Error", f"Invalid shares/price for {ticker}")
                    return
                if shares <= 0 or price <= 0:
                    messagebox.showerror("Error", f"Shares and price must be > 0 for {ticker}")
                    return
                entries.append({
                    "date": trade_date,
                    "action": action_var.get(),
                    "ticker": ticker,
                    "shares": shares,
                    "price": price,
                })

            if not entries:
                messagebox.showwarning("Empty", "Enter at least one trade.")
                return

            total_buy = sum(e["price"] * e["shares"] for e in entries if e["action"] == "BUY")
            total_sell = sum(e["price"] * e["shares"] for e in entries if e["action"] == "SELL")
            cash_now = hm.get_cash_balance()
            if total_buy - total_sell > cash_now + 0.01:
                messagebox.showerror(
                    "Insufficient Cash",
                    f"Net buy ${total_buy - total_sell:,.2f} exceeds "
                    f"cash ${cash_now:,.2f}",
                )
                return

            summary_lines = []
            for e in entries:
                summary_lines.append(
                    f"  {e['date'].strftime('%Y-%m-%d')}  {e['action']:4s}  "
                    f"{e['ticker']:6s}  {e['shares']} sh @ ${e['price']:.2f}"
                )
            confirm_msg = "Apply these trades?\n\n" + "\n".join(summary_lines)
            if not messagebox.askyesno("Confirm", confirm_msg):
                return

            entries.sort(key=lambda x: x["date"])

            for e in entries:
                trade_df = pd.DataFrame([{
                    "Ticker": e["ticker"],
                    "Action": e["action"],
                    "Price": e["price"],
                    "Shares": e["shares"],
                }])
                hm.apply_partial_execution(
                    trade_df, trigger_type="MANUAL", date=e["date"],
                )
                cost = round(e["price"] * e["shares"], 2)
                note = f"{e['ticker']} {e['shares']}sh ({e['date'].strftime('%m/%d')})"
                if e["action"] == "BUY":
                    hm.record_cash_event("BUY", -cost, note)
                else:
                    hm.record_cash_event("SELL", cost, note)

            popup.destroy()

            self._log_write(f"\n  [T14] Applied {len(entries)} manual trades:")
            for e in entries:
                self._log_write(
                    f"    {e['date'].strftime('%Y-%m-%d')}  {e['action']:4s}  "
                    f"{e['ticker']:6s}  {e['shares']} sh @ ${e['price']:.2f}"
                )
            cash_after = hm.get_cash_balance()
            pnl = hm.get_pnl_summary()
            self._log_write(
                f"  Portfolio: ${pnl['total_value']:,.2f} | "
                f"{pnl['holdings_count']} holdings | "
                f"Cash: ${cash_after:,.2f}"
            )

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        tk.Button(
            btn_frame, text="Apply Trades", command=_apply_trades,
            font=("Helvetica", 12, "bold"),
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
            bd=0, cursor="hand2", width=16,
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame, text="Cancel", command=popup.destroy,
            font=("Helvetica", 12),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", width=10,
        ).pack(side=tk.LEFT, padx=5)

    # ── T12: Cache Heatmap ──
    def _t12_cache_heatmap(self):
        def _run():
            self._ensure_engine()
            import numpy as np
            import dataclasses
            from engine_loader import engine
            from daily_runner import load_frozen_signal

            print("  Loading pack for heatmap (this may take a moment)...")
            cfg_hm = dataclasses.replace(_cfg)
            now = datetime.now()
            from datetime import timedelta
            cfg_hm.start_panel_date = now - timedelta(days=365)
            cfg_hm.end_date = now
            cfg_hm.enable_historical_universe = True
            cfg_hm.historical_universe_expand_tickers = True
            cfg_hm.enable_coverage_based_universe = True

            result = engine.prepare_inputs(cfg_hm)
            pack = result["pack"]

            tickers = list(pack["tickers"])
            indicator_names = list(pack["indicator_names"])
            feat_valid = np.asarray(pack["feat_valid"], dtype=np.uint8)
            tradable = np.asarray(pack["tradable"], dtype=np.uint8)
            close = np.asarray(pack["close"], dtype=np.float64)
            marketcap = np.asarray(pack["marketcap"], dtype=np.float64)

            K, D, N = feat_valid.shape
            last_di = D - 1

            tradable_last = tradable[last_di]
            close_valid = np.isfinite(close[last_di]) & (close[last_di] > 0)
            mcap_valid = np.isfinite(marketcap[last_di]) & (marketcap[last_di] > 0)
            usable_mask = (tradable_last == 1) & close_valid & mcap_valid
            usable_idx = np.where(usable_mask)[0]

            print(f"  Tickers: {N} total, {len(usable_idx)} usable (tradable + price + mcap)")
            print(f"  Features: {K} | Dates: {D}")

            if len(usable_idx) == 0:
                print("  [ERROR] No usable tickers found.")
                return

            heatmap_data = feat_valid[:, last_di, :][:, usable_idx].T.astype(np.float32)
            ticker_labels = [tickers[i] for i in usable_idx]

            ticker_valid_pct = np.nanmean(heatmap_data, axis=1) * 100
            feature_valid_pct = np.nanmean(heatmap_data, axis=0) * 100

            sort_idx = np.argsort(-ticker_valid_pct)
            heatmap_data = heatmap_data[sort_idx]
            ticker_labels = [ticker_labels[i] for i in sort_idx]
            ticker_valid_pct = ticker_valid_pct[sort_idx]

            print(f"  Feature validity range: {feature_valid_pct.min():.0f}% — {feature_valid_pct.max():.0f}%")
            print(f"  Ticker validity range:  {ticker_valid_pct.min():.0f}% — {ticker_valid_pct.max():.0f}%")
            low_features = [indicator_names[i] for i in range(K) if feature_valid_pct[i] < 80]
            if low_features:
                print(f"  Low coverage features (<80%): {low_features[:10]}")

            print("  Rendering heatmap...")
            self.after(0, lambda: self._show_heatmap_popup(
                heatmap_data, ticker_labels, indicator_names,
                ticker_valid_pct, feature_valid_pct,
            ))

        self._run_task("T12: Cache Heatmap", _run)

    def _show_heatmap_popup(
        self, data, ticker_labels, feature_labels,
        ticker_pct, feature_pct,
    ):
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import numpy as np

        popup = tk.Toplevel(self)
        popup.title("Cache Integrity Heatmap")
        popup.geometry("1200x800")
        popup.configure(bg="#1e1e2e")

        n_tickers, n_features = data.shape
        show_n = n_tickers

        fig, axes = plt.subplots(
            2, 1, figsize=(max(14, n_features * 0.5), show_n * 0.18 + 3),
            gridspec_kw={"height_ratios": [1, show_n]},
            facecolor="#1e1e2e",
        )

        ax_bar = axes[0]
        ax_bar.set_facecolor("#1e1e2e")
        colors_bar = ["#a6e3a1" if v >= 80 else "#f9e2af" if v >= 50 else "#f38ba8"
                       for v in feature_pct]
        ax_bar.bar(range(n_features), feature_pct, color=colors_bar, width=0.8)
        ax_bar.set_xticks([])
        ax_bar.set_ylabel("Feature %", color="#cdd6f4", fontsize=9)
        ax_bar.set_ylim(0, 105)
        ax_bar.tick_params(colors="#9399b2", labelsize=7)
        ax_bar.set_title(
            f"Cache Integrity — {n_tickers} usable tickers x {n_features} features",
            color="#cdd6f4", fontsize=11, fontweight="bold",
        )
        for spine in ax_bar.spines.values():
            spine.set_color("#45475a")

        ax_hm = axes[1]
        ax_hm.set_facecolor("#1e1e2e")
        from matplotlib.colors import ListedColormap
        cmap = ListedColormap(["#f38ba8", "#a6e3a1"])
        display_data = data[:show_n]
        ax_hm.imshow(display_data, aspect="auto", cmap=cmap, interpolation="nearest")
        ax_hm.set_yticks(range(show_n))
        ax_hm.set_yticklabels(
            [f"{ticker_labels[i]} ({ticker_pct[i]:.0f}%)" for i in range(show_n)],
            fontsize=6, color="#cdd6f4",
        )
        ax_hm.set_xticks(range(n_features))
        ax_hm.set_xticklabels(feature_labels, rotation=90, fontsize=6, color="#cdd6f4")
        ax_hm.tick_params(colors="#9399b2", length=2)
        for spine in ax_hm.spines.values():
            spine.set_color("#45475a")

        fig.tight_layout()

        canvas_frame = tk.Frame(popup, bg="#1e1e2e")
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        canvas_widget = tk.Canvas(canvas_frame, bg="#1e1e2e", highlightthickness=0)
        scrollbar_y = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=canvas_widget.yview)
        scrollbar_x = tk.Scrollbar(popup, orient=tk.HORIZONTAL, command=canvas_widget.xview)

        canvas_widget.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        canvas_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_x.pack(fill=tk.X)

        inner_frame = tk.Frame(canvas_widget, bg="#1e1e2e")
        canvas_widget.create_window((0, 0), window=inner_frame, anchor=tk.NW)

        chart = FigureCanvasTkAgg(fig, master=inner_frame)
        chart.draw()
        chart.get_tk_widget().pack()

        inner_frame.update_idletasks()
        canvas_widget.configure(scrollregion=canvas_widget.bbox("all"))

        def _on_close():
            plt.close(fig)
            popup.destroy()
        popup.protocol("WM_DELETE_WINDOW", _on_close)


    # ── T16: Backtest Simulation ──
    def _t16_backtest_sim(self):
        popup = tk.Toplevel(self)
        popup.title("T16: Backtest Simulation Settings")
        popup.geometry("480x380")
        popup.configure(bg="#1e1e2e")

        tk.Label(popup, text="Phase 3 Backtest Simulation",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(12, 8))

        frame = tk.Frame(popup, bg="#1e1e2e")
        frame.pack(padx=20, fill=tk.X)
        entry_style = dict(font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                           insertbackground="#cdd6f4", relief=tk.FLAT)
        lbl_style = dict(font=("Helvetica", 11), bg="#1e1e2e", fg="#bac2de", anchor=tk.W)

        fields = {}
        for label, default in [
            ("Start Date", "2017-01-03"),
            ("End Date", datetime.now().strftime("%Y-%m-%d")),
            ("Initial Capital ($)", "100000"),
            ("Daily Buy Limit ($)", "1000"),
            ("Commission (bps)", "10"),
            ("Slippage (bps)", "5"),
        ]:
            row = tk.Frame(frame, bg="#1e1e2e")
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, width=22, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, default)
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
            fields[label] = e

        mode_var = tk.StringVar(value="event_driven")
        mode_frame = tk.Frame(frame, bg="#1e1e2e")
        mode_frame.pack(fill=tk.X, pady=6)
        tk.Label(mode_frame, text="Rebalance Mode", width=22, **lbl_style
                 ).pack(side=tk.LEFT)
        for text, val in [("Event-Driven", "event_driven"), ("Daily", "daily")]:
            tk.Radiobutton(mode_frame, text=text, variable=mode_var, value=val,
                           bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                           activebackground="#1e1e2e", activeforeground="#cdd6f4",
                           font=("Helvetica", 10)).pack(side=tk.LEFT, padx=8)

        tk.Label(popup, text="⚠ Pack generation may take 30+ minutes for long date ranges.",
                 font=("Helvetica", 9), bg="#1e1e2e", fg="#f9e2af"
                 ).pack(pady=4)

        def _start():
            start_d = fields["Start Date"].get().strip()
            end_d = fields["End Date"].get().strip()
            capital = float(fields["Initial Capital ($)"].get().strip())
            buy_limit = float(fields["Daily Buy Limit ($)"].get().strip())
            comm = float(fields["Commission (bps)"].get().strip())
            slip = float(fields["Slippage (bps)"].get().strip())
            mode = mode_var.get()
            popup.destroy()

            def _run():
                import importlib
                import simulator
                importlib.reload(simulator)

                self._ensure_engine()
                import yaml
                conf_path = str(_THIS_DIR / "config.yaml")
                with open(conf_path) as f:
                    conf = yaml.safe_load(f)

                print(f"  Config: {start_d} ~ {end_d}")
                print(f"  Capital=${capital:,.0f}  DailyLimit=${buy_limit:,.0f}")
                print(f"  Commission={comm}bps  Slippage={slip}bps  Mode={mode}")
                print()

                # Step 1: Build pack
                print("[Step 1/3] Building data pack...")
                from engine_loader import engine
                from daily_runner import load_frozen_signal
                import dataclasses

                signal_path = conf["paths"]["frozen_signal"]
                signal = load_frozen_signal(signal_path)
                print(f"  Loaded frozen signal: {os.path.basename(signal_path)}")

                cfg = engine.Config()
                for k, v in conf.get("regime", {}).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, type(getattr(cfg, k))(v))

                cfg.start_panel_date = datetime.strptime(start_d, "%Y-%m-%d")
                cfg.end_date = datetime.strptime(end_d, "%Y-%m-%d")
                cfg.enable_historical_universe = True
                cfg.historical_universe_expand_tickers = True
                cfg.enable_coverage_based_universe = True
                cfg.fmp_cache_root = conf["paths"]["fmp_cache_root"]

                result = engine.prepare_inputs(cfg)
                pack = result["pack"] if isinstance(result, dict) and "pack" in result else result
                print(f"  Pack ready: {len(pack['tickers'])} tickers, {len(pack['dates'])} dates")

                # Step 2: Build VIX regime
                print("\n[Step 2/3] Building VIX regime timeseries...")
                from datetime import timedelta
                vix_df = engine.build_vix_regime_timeseries(
                    cfg,
                    datetime.strptime(start_d, "%Y-%m-%d") - timedelta(days=60),
                    datetime.strptime(end_d, "%Y-%m-%d"),
                )

                vix_close_map = {}
                vix_regime_map = {}
                if vix_df is not None and not vix_df.empty:
                    for _, row in vix_df.iterrows():
                        d_str = str(row.get("date", row.name))[:10]
                        vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
                        vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
                print(f"  VIX data: {len(vix_close_map)} dates")

                # Step 3: Run simulation
                print(f"\n[Step 3/3] Running simulation ({mode} mode)...")
                result = simulator.run_simulation(
                    engine=engine,
                    cfg=cfg,
                    pack=pack,
                    signal=signal,
                    vix_close_by_date=vix_close_map,
                    vix_regime_by_date=vix_regime_map,
                    initial_capital=capital,
                    daily_buy_limit=buy_limit,
                    strategy_conf=conf.get("strategy", {}),
                    trigger_conf=conf.get("triggers", {}),
                    rebalance_mode=mode,
                    commission_bps=comm,
                    slippage_bps=slip,
                    start_date=start_d,
                    end_date=end_d,
                    progress_fn=lambda c, t, m: print(m),
                )

                report = simulator.format_report(result)
                print(f"\n{report}")

                # Save results
                out_dir = conf["paths"]["output_dir"]
                ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
                ts_path = os.path.join(out_dir, f"sim_daily_ts_{ts_tag}.csv")
                result["daily_ts"].to_csv(ts_path, index=False)
                print(f"\n  Daily timeseries saved: {ts_path}")

                if not result["trades"].empty:
                    tr_path = os.path.join(out_dir, f"sim_trades_{ts_tag}.csv")
                    result["trades"].to_csv(tr_path, index=False)
                    print(f"  Trades log saved: {tr_path}")

                # Show chart
                self._show_sim_chart(result["daily_ts"])

            self._run_task("T16: Backtest Simulation", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="  Run Simulation  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2", command=_start).pack()

    # ── T17: Lab Sweep ──
    def _t17_lab_sweep(self):
        popup = tk.Toplevel(self)
        popup.title("T17: Phase 3 Lab Sweep")
        popup.geometry("580x520")
        popup.configure(bg="#1e1e2e")

        tk.Label(popup, text="Phase 3 Lab — Multi-Arm Sweep",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(12, 4))

        frame = tk.Frame(popup, bg="#1e1e2e")
        frame.pack(padx=20, fill=tk.X)
        entry_style = dict(font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                           insertbackground="#cdd6f4", relief=tk.FLAT)
        lbl_style = dict(font=("Helvetica", 11), bg="#1e1e2e", fg="#bac2de", anchor=tk.W)

        fields = {}
        for label, default in [
            ("Start Date", "2017-01-03"),
            ("End Date", datetime.now().strftime("%Y-%m-%d")),
            ("Initial Capital ($)", "100000"),
        ]:
            row = tk.Frame(frame, bg="#1e1e2e")
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, width=22, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, default)
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
            fields[label] = e

        mode_var = tk.StringVar(value="daily")
        mode_frame = tk.Frame(frame, bg="#1e1e2e")
        mode_frame.pack(fill=tk.X, pady=6)
        tk.Label(mode_frame, text="Rebalance Mode", width=22, **lbl_style
                 ).pack(side=tk.LEFT)
        for text, val in [("Daily", "daily"), ("Event-Driven", "event_driven")]:
            tk.Radiobutton(mode_frame, text=text, variable=mode_var, value=val,
                           bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                           activebackground="#1e1e2e", activeforeground="#cdd6f4",
                           font=("Helvetica", 10)).pack(side=tk.LEFT, padx=8)

        tk.Label(popup, text="Arms (check to include):",
                 font=("Helvetica", 11, "bold"), bg="#1e1e2e", fg="#cdd6f4",
                 anchor=tk.W).pack(padx=20, pady=(8, 2), anchor=tk.W)

        import phase3_lab

        arm_set_var = tk.StringVar(value="v4")
        set_frame = tk.Frame(popup, bg="#1e1e2e")
        set_frame.pack(fill=tk.X, padx=20, pady=4)
        tk.Label(set_frame, text="Arm Set", width=22, **lbl_style).pack(side=tk.LEFT)

        n_v3 = len(getattr(phase3_lab, "SWEEP_V3_ARMS", {}))
        n_v4 = len(getattr(phase3_lab, "SWEEP_V4_ARMS", {}))

        for text, val in [
            (f"V3 ({n_v3})", "v3"),
            (f"V4 Final ({n_v4})", "v4"),
            ("All", "all"),
        ]:
            tk.Radiobutton(set_frame, text=text, variable=arm_set_var, value=val,
                           bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                           activebackground="#1e1e2e", activeforeground="#cdd6f4",
                           font=("Helvetica", 10)).pack(side=tk.LEFT, padx=4)

        all_arms = {}
        all_arms.update(phase3_lab.SAMPLE_ARMS)
        all_arms.update(phase3_lab.SWEEP_ARMS)
        all_arms.update(phase3_lab.SWEEP_V2_ARMS)
        all_arms.update(getattr(phase3_lab, "SWEEP_V3_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_V4_ARMS", {}))

        arm_vars = {}
        canvas_outer = tk.Frame(popup, bg="#1e1e2e")
        canvas_outer.pack(padx=20, fill=tk.BOTH, expand=True)
        arm_canvas = tk.Canvas(canvas_outer, bg="#1e1e2e", highlightthickness=0, height=180)
        arm_sb = tk.Scrollbar(canvas_outer, orient=tk.VERTICAL, command=arm_canvas.yview)
        arm_canvas.configure(yscrollcommand=arm_sb.set)
        arm_sb.pack(side=tk.RIGHT, fill=tk.Y)
        arm_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        arm_inner = tk.Frame(arm_canvas, bg="#1e1e2e")
        arm_canvas.create_window((0, 0), window=arm_inner, anchor=tk.NW)

        for arm_name, overrides in all_arms.items():
            var = tk.BooleanVar(value=arm_name in phase3_lab.SWEEP_ARMS)
            desc = ", ".join(f"{k}={v}" for k, v in overrides.items()) if overrides else "(baseline)"
            tk.Checkbutton(
                arm_inner, text=f"{arm_name}: {desc}",
                variable=var, bg="#1e1e2e", fg="#cdd6f4",
                selectcolor="#313244", activebackground="#1e1e2e",
                activeforeground="#cdd6f4", font=("Menlo", 9),
                anchor=tk.W,
            ).pack(fill=tk.X)
            arm_vars[arm_name] = var

        arm_inner.update_idletasks()
        arm_canvas.configure(scrollregion=arm_canvas.bbox("all"))

        _v3_arms = getattr(phase3_lab, "SWEEP_V3_ARMS", {})
        _v4_arms = getattr(phase3_lab, "SWEEP_V4_ARMS", {})

        def _set_arms(*_):
            s = arm_set_var.get()
            for n, v in arm_vars.items():
                if s == "v3":
                    v.set(n in _v3_arms)
                elif s == "v4":
                    v.set(n in _v4_arms)
                else:
                    v.set(True)
        arm_set_var.trace_add("write", _set_arms)
        _set_arms()

        tk.Label(popup, text="⚠ Pack is built once; each arm adds ~60-120s.",
                 font=("Helvetica", 9), bg="#1e1e2e", fg="#f9e2af"
                 ).pack(pady=4)

        def _start():
            start_d = fields["Start Date"].get().strip()
            end_d = fields["End Date"].get().strip()
            capital = float(fields["Initial Capital ($)"].get().strip())
            mode = mode_var.get()
            selected = {n: all_arms[n]
                        for n, v in arm_vars.items() if v.get() and n in all_arms}
            popup.destroy()

            if not selected:
                messagebox.showwarning("Lab", "No arms selected.")
                return

            def _run():
                import importlib
                importlib.reload(phase3_lab)

                print(f"  Lab config: {start_d} ~ {end_d}, ${capital:,.0f}, mode={mode}")
                print(f"  Selected arms: {list(selected.keys())}")
                print()

                lab_result = phase3_lab.run_lab(
                    arms=selected,
                    start_date=start_d,
                    end_date=end_d,
                    initial_capital=capital,
                    daily_buy_limit=1000.0,
                    rebalance_mode=mode,
                    progress_fn=lambda m: print(m),
                )

                import yaml
                conf_path = str(_THIS_DIR / "config.yaml")
                with open(conf_path) as f:
                    conf = yaml.safe_load(f)
                out_dir = conf["paths"]["output_dir"]
                comp_path = phase3_lab.save_lab_results(lab_result, out_dir)
                print(f"\n  Comparison saved: {comp_path}")

                self._show_lab_chart(lab_result)

            self._run_task("T17: Lab Sweep", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="  Run Lab Sweep  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2", command=_start).pack()

    def _show_lab_chart(self, lab_result):
        """Overlay portfolio value + rolling return curves for all arms."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import pandas as _pd
        import numpy as _np

        popup = tk.Toplevel(self)
        popup.title("Lab Sweep — Arm Comparison")
        popup.geometry("1400x920")
        popup.configure(bg="#1e1e2e")

        n_arms = len(lab_result["results"])
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7),
                                        gridspec_kw={"height_ratios": [3, 2]},
                                        sharex=True)
        fig.patch.set_facecolor("#1e1e2e")
        ax1.set_facecolor("#11111b")
        ax2.set_facecolor("#11111b")

        palette = ["#89b4fa", "#a6e3a1", "#f9e2af", "#f38ba8",
                    "#cba6f7", "#94e2d5", "#fab387", "#74c7ec",
                    "#b4befe", "#f2cdcd", "#89dceb", "#eba0ac",
                    "#a6d189", "#e78284", "#85c1dc", "#f4b8e4",
                    "#ca9ee6", "#e5c890", "#81c8be", "#babbf1"]

        arms_sorted = sorted(
            lab_result["results"].items(),
            key=lambda kv: kv[1]["metrics"].get("CAGR", 0), reverse=True)

        roll_window = 60

        for i, (name, res) in enumerate(arms_sorted):
            ts = res["daily_ts"]
            dates = _pd.to_datetime(ts["Date"])
            vals = ts["PortfolioValue"].values
            m = res["metrics"]
            clr = palette[i % len(palette)]
            label = f"{name} ({m.get('CAGR',0)*100:+.1f}%)"

            ax1.plot(dates, vals, color=clr, linewidth=1.2,
                     label=label, alpha=0.85)

            cum_ret = vals / vals[0] - 1.0
            roll_ret = _pd.Series(cum_ret).rolling(roll_window).apply(
                lambda w: (w.iloc[-1] - w.iloc[0]) / max(abs(w.iloc[0]) + 1, 1e-6) * (252 / roll_window),
                raw=False)
            ax2.plot(dates, roll_ret * 100, color=clr, linewidth=0.9, alpha=0.75)

        ax1.set_ylabel("Portfolio Value ($)", color="#cdd6f4")
        ax1.set_title("Phase 3 Lab — Portfolio Value & Rolling Return",
                       color="#cdd6f4", fontsize=13)
        ncol = max(1, (n_arms + 9) // 10)
        ax1.legend(facecolor="#313244", edgecolor="#45475a", labelcolor="#cdd6f4",
                   fontsize=7, loc="upper left", ncol=ncol)
        ax1.tick_params(colors="#9399b2")
        ax1.grid(True, alpha=0.2, color="#45475a")

        ax2.axhline(0, color="#585b70", linewidth=0.8, linestyle="--")
        ax2.set_ylabel(f"Rolling {roll_window}d Ann. Return (%)", color="#cdd6f4")
        ax2.set_xlabel("Date", color="#cdd6f4")
        ax2.tick_params(colors="#9399b2")
        ax2.grid(True, alpha=0.2, color="#45475a")

        for ax in (ax1, ax2):
            for spine in ax.spines.values():
                spine.set_color("#45475a")

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=popup)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=False, pady=(5, 0))

        import phase3_lab as _pl
        report = _pl.format_lab_report(lab_result)
        txt = tk.Text(popup, bg="#11111b", fg="#cdd6f4",
                      font=("Menlo", 9), wrap=tk.NONE,
                      insertbackground="#cdd6f4", relief=tk.FLAT, height=20)
        txt_sb_y = tk.Scrollbar(popup, orient=tk.VERTICAL, command=txt.yview)
        txt_sb_x = tk.Scrollbar(popup, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=txt_sb_y.set, xscrollcommand=txt_sb_x.set)
        txt_sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        txt_sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        txt.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        txt.insert("1.0", report)
        txt.config(state=tk.DISABLED)

        btn_bar = tk.Frame(popup, bg="#1e1e2e")
        btn_bar.pack(fill=tk.X, padx=5, pady=(0, 5))

        def _copy_all():
            popup.clipboard_clear()
            popup.clipboard_append(report)
            _copy_btn.config(text="  Copied!  ")
            popup.after(1500, lambda: _copy_btn.config(text="  Copy Report  "))

        _copy_btn = tk.Button(
            btn_bar, text="  Copy Report  ",
            font=("Helvetica", 11, "bold"),
            bg="#89b4fa", fg="#1e1e2e", activebackground="#74c7ec",
            bd=0, cursor="hand2", command=_copy_all)
        _copy_btn.pack(side=tk.LEFT, padx=4)

        def _on_close():
            plt.close(fig)
            popup.destroy()
        popup.protocol("WM_DELETE_WINDOW", _on_close)

    def _show_sim_chart(self, daily_ts):
        """Display simulation result chart in a popup."""
        if daily_ts.empty:
            return

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        popup = tk.Toplevel(self)
        popup.title("Backtest Simulation — Portfolio Value")
        popup.geometry("1000x600")
        popup.configure(bg="#1e1e2e")

        import pandas as _pd
        import numpy as _np

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7),
                                        gridspec_kw={"height_ratios": [3, 1]})
        fig.patch.set_facecolor("#1e1e2e")

        dates = _pd.to_datetime(daily_ts["Date"])
        values = daily_ts["PortfolioValue"].values

        ax1.set_facecolor("#11111b")
        ax1.plot(dates, values, color="#89b4fa", linewidth=1.2, label="Portfolio Value")
        ax1.fill_between(dates, values, alpha=0.1, color="#89b4fa")
        ax1.set_ylabel("Portfolio Value ($)", color="#cdd6f4")
        ax1.set_title("Phase 3 Backtest — Daily Portfolio Value", color="#cdd6f4", fontsize=13)
        ax1.legend(facecolor="#313244", edgecolor="#45475a", labelcolor="#cdd6f4")
        ax1.tick_params(colors="#9399b2")
        ax1.grid(True, alpha=0.2, color="#45475a")
        for spine in ax1.spines.values():
            spine.set_color("#45475a")

        cummax = _np.maximum.accumulate(values)
        dd = (values - cummax) / _np.where(cummax > 0, cummax, 1.0) * 100
        ax2.set_facecolor("#11111b")
        ax2.fill_between(dates, dd, color="#f38ba8", alpha=0.6)
        ax2.set_ylabel("Drawdown (%)", color="#cdd6f4")
        ax2.set_xlabel("Date", color="#cdd6f4")
        ax2.tick_params(colors="#9399b2")
        ax2.grid(True, alpha=0.2, color="#45475a")
        for spine in ax2.spines.values():
            spine.set_color("#45475a")

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=popup)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        def _on_close():
            plt.close(fig)
            popup.destroy()
        popup.protocol("WM_DELETE_WINDOW", _on_close)


if __name__ == "__main__":
    app = App()
    app.mainloop()
