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
            ("T15 Force Overwrite 90d", self._t15_force_overwrite),
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
            ("T18 Performance", self._t18_performance),
            ("T19 E2E GA", self._t19_e2e_ga),
            ("T20 Regime Blend", self._t20_regime_blend),
        ]
        buttons_ops_row4 = [
            ("T23 Compose Backtest", self._t23_compose_backtest),
            ("T24 Compose vs Baseline", self._t24_compose_vs_baseline),
            ("T25 P5 Retrain (GA)", self._t25_p5_retrain),
            ("T26 Walk-Forward (T5)", self._t26_walk_forward),
            ("T27 Phase B Batch (B1/B2/B3)", self._t27_phase_b_batch),
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

        row4 = tk.Frame(sec_ops, bg="#1e1e2e")
        row4.pack(fill=tk.X, padx=8, pady=(0, 6))
        for text, cmd in buttons_ops_row4:
            b = tk.Button(row4, text=text, command=cmd, **btn_style)
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
            "최근 90일 캐시를 전 종목 강제 재다운로드합니다.\n"
            "빈 구간/장중 partial data 오염 복구용.\n\n"
            "약 30-60분 소요될 수 있습니다. 진행할까요?",
        ):
            return

        def _run():
            self._ensure_engine()
            import importlib, daily_runner
            importlib.reload(daily_runner)
            from engine_loader import engine
            tickers, _ = engine.load_sp500_tickers_ttl(_cfg, ttl_days=30)
            print(f"  Loaded {len(tickers)} SP500 tickers")
            daily_runner.force_overwrite_recent_cache(_cfg, tickers, days=90)
        self._run_task("T15: Force Overwrite 90d", _run)

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
            vix, regime, _alphas = get_current_vix(_cfg)
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
            vix, regime, _alphas = get_current_vix(_cfg)
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
            self._ensure_engine()
            import importlib, daily_runner
            importlib.reload(daily_runner)
            daily_runner.run_daily(dry_run=True, force=True)
        self._run_task("T6: Daily Run (Dry)", _run)

    # ── T7: Daily Live Run ──
    def _t7_live_run(self):
        def _confirm_and_run():
            from cache_health import load_config as _load_conf
            from holdings_manager import HoldingsManager
            conf = _load_conf()
            hm = HoldingsManager(conf["paths"]["holdings_log"])
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
                self._ensure_engine()
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
        from run_artifact import (
            find_matching_execution_run,
            load_last_execution_batch,
            record_execution_artifact,
            record_execution_reversal,
        )
        hm = HoldingsManager(_conf["paths"]["holdings_log"])
        recos = hm.load_recommendations()

        if recos.empty:
            self._log_write("  No recommendations to report. Run T7 first.")
            return

        artifact_run_dir, artifact_meta, artifact_recos = find_matching_execution_run(
            _conf["paths"]["output_dir"], recos,
        )
        artifact_run_id = ""
        if artifact_run_dir is not None and artifact_meta is not None and not artifact_recos.empty:
            artifact_run_id = str(artifact_meta.get("run_id", artifact_run_dir.name))
            recos = recos.copy()
            if len(artifact_recos) == len(recos):
                recos["RunId"] = artifact_recos["RunId"].values
                recos["RecRowId"] = artifact_recos["RecRowId"].values
        else:
            artifact_run_dir = None
            artifact_meta = None

        last_exec_batch = load_last_execution_batch(artifact_run_dir) if artifact_run_dir is not None else pd.DataFrame()

        # v2.1 — derive from RecosAction constants so every new D2/D4
        # trigger action (TRIM_PROFIT, SELL_PROFIT, TRIM_ATR_TRAIL,
        # SELL_ATR_TRAIL, SELL_PEAK_DD, SELL_SCORE_DECAY, …) is
        # automatically checkable in the T10 UI without code changes.
        # TRIM_GRACE keeps its legacy dedicated routing; SELL_GRACE
        # stays info-only (grace carry-over is written by the daily
        # runner, not by user confirmation).
        from exits import RecosAction as _RA
        _SELL_ACTIONS = tuple(
            sorted((_RA.FULL_CLOSE | _RA.PARTIAL_CLOSE) - {"SELL_GRACE"})
        )
        _BUY_ACTIONS = ("BUY", "BUY_NEW", "BUY_MORE")
        _INFO_ACTIONS = ("SELL_GRACE", "HOLD", "DEFERRED")
        _CHECKABLE = _SELL_ACTIONS + _BUY_ACTIONS
        # Display ordering: legacy STOP_LOSS/SELL/TRIM first (common case),
        # then v2.1 profit_target, then any other D2/D4 extras, then buys/info.
        _ACTION_ORDER_HEAD = ["STOP_LOSS", "SELL", "TRIM", "TRIM_GRACE",
                              "TRIM_PROFIT", "SELL_PROFIT",
                              "TRIM_ATR_TRAIL", "SELL_ATR_TRAIL"]
        _ACTION_ORDER_TAIL = ["BUY_NEW", "BUY_MORE", "BUY",
                              "SELL_GRACE", "HOLD", "DEFERRED"]
        _ACTION_ORDER = (
            _ACTION_ORDER_HEAD
            + [a for a in _SELL_ACTIONS if a not in _ACTION_ORDER_HEAD]
            + _ACTION_ORDER_TAIL
        )
        _ACTION_COLORS = {
            "STOP_LOSS": "#f38ba8", "SELL": "#fab387", "TRIM": "#f9e2af",
            "TRIM_GRACE": "#f9e2af", "SELL_GRACE": "#9399b2",
            # v2.1 profit_target — distinct green-ish hue so users can
            # tell "익절" trims apart from rebalance trims at a glance.
            "TRIM_PROFIT": "#a6e3a1", "SELL_PROFIT": "#94e2d5",
            "TRIM_ATR_TRAIL": "#f9e2af", "SELL_ATR_TRAIL": "#fab387",
            "BUY": "#a6e3a1", "BUY_NEW": "#a6e3a1",
            "BUY_MORE": "#94e2d5", "HOLD": "#585b70", "DEFERRED": "#585b70",
        }

        cash_balance = hm.get_cash_balance()
        current = hm.load_current()

        def _sort_key(action):
            return _ACTION_ORDER.index(action) if action in _ACTION_ORDER else 99

        recos = recos.copy()
        recos["_sort"] = recos["Action"].apply(_sort_key)
        recos = recos.sort_values("_sort").drop(columns=["_sort"])

        popup = tk.Toplevel(self)
        popup.title("T10: Report Execution")
        popup.geometry("900x700")
        popup.configure(bg="#1e1e2e")
        popup.transient(self)
        popup.grab_set()

        # ── Summary bar ──
        summary_frame = tk.Frame(popup, bg="#181825", relief=tk.GROOVE, bd=1)
        summary_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        cash_lbl = tk.Label(summary_frame, text=f"Cash: ${cash_balance:,.0f}",
                            font=("Menlo", 11, "bold"), fg="#a6e3a1", bg="#181825")
        cash_lbl.pack(side=tk.LEFT, padx=10, pady=6)

        regime_txt = recos["Regime"].iloc[0] if "Regime" in recos.columns and not recos.empty else "?"
        tk.Label(summary_frame, text=f"Regime: {regime_txt}",
                 font=("Menlo", 10), fg="#cdd6f4", bg="#181825").pack(side=tk.LEFT, padx=10)

        impact_lbl = tk.Label(summary_frame, text="BUY: $0  |  SELL: $0  |  Net: $0",
                              font=("Menlo", 10), fg="#9399b2", bg="#181825")
        impact_lbl.pack(side=tk.RIGHT, padx=10, pady=6)

        if artifact_run_id:
            tk.Label(summary_frame, text=f"Run: {artifact_run_id}",
                     font=("Menlo", 9), fg="#89b4fa", bg="#181825"
                     ).pack(side=tk.RIGHT, padx=10)

        # ── Scrollable items area ──
        canvas = tk.Canvas(popup, bg="#1e1e2e", highlightthickness=0)
        scrollbar = tk.Scrollbar(popup, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1e1e2e")
        scroll_frame.bind("<Configure>",
                          lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)

        check_vars = []
        price_vars = []
        share_vars = []
        row_actions = []
        row_tickers = []
        row_run_ids = []
        row_rec_ids = []
        row_profit_tiers = []  # v2.1 — carry ProfitTier through execution

        prev_group = None
        for _, row in recos.iterrows():
            action = str(row.get("Action", ""))
            ticker = str(row.get("Ticker", ""))
            is_checkable = action in _CHECKABLE
            color = _ACTION_COLORS.get(action, "#585b70")

            group = "SELL" if action in _SELL_ACTIONS else (
                "BUY" if action in _BUY_ACTIONS else "INFO")
            if group != prev_group:
                sep = tk.Frame(scroll_frame, bg="#45475a", height=1)
                sep.pack(fill=tk.X, padx=5, pady=(8, 2))
                group_labels = {"SELL": "SELL / STOP_LOSS / TRIM",
                                "BUY": "BUY_NEW / BUY_MORE",
                                "INFO": "Info Only (not checkable)"}
                tk.Label(scroll_frame, text=f"  {group_labels.get(group, group)}",
                         font=("Helvetica", 9), fg="#585b70", bg="#1e1e2e"
                         ).pack(anchor=tk.W, padx=8)
                prev_group = group

            item_frame = tk.Frame(scroll_frame, bg="#1e1e2e")
            item_frame.pack(fill=tk.X, padx=5, pady=2)

            if is_checkable:
                auto_check = action == "STOP_LOSS"
                var = tk.BooleanVar(value=auto_check)
                check_vars.append(var)
                row_actions.append(action)
                row_tickers.append(ticker)
                row_run_ids.append(row.get("RunId", artifact_run_id or ""))
                row_rec_ids.append(row.get("RecRowId", pd.NA))
                row_profit_tiers.append(row.get("ProfitTier", pd.NA))

                cb = tk.Checkbutton(item_frame, variable=var, bg="#1e1e2e",
                                    activebackground="#1e1e2e", selectcolor="#313244",
                                    command=lambda: _update_impact())
                cb.pack(side=tk.LEFT)

                tk.Label(item_frame, text=f"{action:10s}",
                         font=("Menlo", 10, "bold"), fg=color, bg="#1e1e2e",
                         width=11, anchor=tk.W).pack(side=tk.LEFT)

                tk.Label(item_frame, text=f"{ticker:6s}",
                         font=("Menlo", 10), fg="#cdd6f4", bg="#1e1e2e",
                         width=7).pack(side=tk.LEFT)

                shares_val = int(row["Shares"]) if pd.notna(row.get("Shares")) else 0
                sh_var = tk.StringVar(value=str(shares_val))
                share_vars.append(sh_var)
                tk.Label(item_frame, text="sh:", font=("Helvetica", 9),
                         fg="#9399b2", bg="#1e1e2e").pack(side=tk.LEFT, padx=(4, 1))
                tk.Entry(item_frame, textvariable=sh_var, width=5,
                         font=("Menlo", 10), bg="#313244", fg="#cdd6f4",
                         insertbackground="#cdd6f4", bd=0).pack(side=tk.LEFT)

                price_val = float(row["Price"]) if pd.notna(row.get("Price")) else 0.0
                pr_var = tk.StringVar(value=f"{price_val:.2f}")
                price_vars.append(pr_var)
                tk.Label(item_frame, text="@$", font=("Helvetica", 9),
                         fg="#9399b2", bg="#1e1e2e").pack(side=tk.LEFT, padx=(4, 1))
                tk.Entry(item_frame, textvariable=pr_var, width=9,
                         font=("Menlo", 10), bg="#313244", fg="#cdd6f4",
                         insertbackground="#cdd6f4", bd=0).pack(side=tk.LEFT)

                cost_val = price_val * shares_val
                cost_lbl = tk.Label(item_frame, text=f"=${cost_val:>8,.0f}",
                                    font=("Menlo", 9), fg="#9399b2", bg="#1e1e2e",
                                    width=10)
                cost_lbl.pack(side=tk.LEFT, padx=(4, 0))

                ctx_parts = []
                if action in _SELL_ACTIONS and not current.empty:
                    mask = current["Ticker"] == ticker
                    if mask.any():
                        pnl_pct = float(current.loc[mask, "PnL_Pct"].iloc[0])
                        ctx_parts.append(f"PnL={pnl_pct:+.1f}%")
                if pd.notna(row.get("Score")) and float(row.get("Score", 0)) > 0:
                    ctx_parts.append(f"Sc={float(row['Score']):.0f}")
                if pd.notna(row.get("GapPct")) and float(row.get("GapPct", 0)) != 0:
                    ctx_parts.append(f"Gap={float(row['GapPct']):+.1f}%")
                if pd.notna(row.get("TargetPct")):
                    ctx_parts.append(f"Tgt={float(row['TargetPct']):.1f}%")
                # v2.1 — show profit_target tier so users can tell
                # "30% 익절 trim" apart from a rebalance trim.
                if action in ("TRIM_PROFIT", "SELL_PROFIT") and pd.notna(row.get("ProfitTier")):
                    ctx_parts.append(f"tier={float(row['ProfitTier']):.0f}%")

                if ctx_parts:
                    tk.Label(item_frame, text="  ".join(ctx_parts),
                             font=("Helvetica", 9), fg="#7f849c", bg="#1e1e2e"
                             ).pack(side=tk.LEFT, padx=6)
            else:
                tk.Label(item_frame, text="   ", bg="#1e1e2e", width=2).pack(side=tk.LEFT)
                tk.Label(item_frame, text=f"{action:10s}",
                         font=("Menlo", 10), fg=color, bg="#1e1e2e",
                         width=11, anchor=tk.W).pack(side=tk.LEFT)
                tk.Label(item_frame, text=f"{ticker:6s}",
                         font=("Menlo", 10), fg="#585b70", bg="#1e1e2e",
                         width=7).pack(side=tk.LEFT)

                info_parts = []
                if pd.notna(row.get("Score")) and float(row.get("Score", 0)) > 0:
                    info_parts.append(f"Sc={float(row['Score']):.0f}")
                if pd.notna(row.get("ActualPct")):
                    info_parts.append(f"w={float(row['ActualPct']):.1f}%")
                if pd.notna(row.get("TargetPct")):
                    info_parts.append(f"tgt={float(row['TargetPct']):.1f}%")
                if action == "SELL_GRACE" and pd.notna(row.get("GraceCount")):
                    gc = int(row["GraceCount"])
                    grace_max = _conf.get("strategy", {}).get("sell_grace_days", 60)
                    info_parts.append(f"grace {gc}/{grace_max} ({grace_max-gc}d left)")
                if action == "DEFERRED":
                    info_parts.append("budget exhausted")

                tk.Label(item_frame, text="  ".join(info_parts),
                         font=("Helvetica", 9), fg="#585b70", bg="#1e1e2e"
                         ).pack(side=tk.LEFT, padx=6)

        def _update_impact():
            total_buy = 0.0
            total_sell = 0.0
            for i in range(len(check_vars)):
                if check_vars[i].get():
                    try:
                        p = float(price_vars[i].get())
                        s = int(share_vars[i].get())
                    except (ValueError, IndexError):
                        continue
                    cost = p * s
                    act = row_actions[i]
                    if act in _BUY_ACTIONS:
                        total_buy += cost
                    elif act in _SELL_ACTIONS:
                        total_sell += cost
            net = total_sell - total_buy
            net_color = "#a6e3a1" if net >= 0 else "#f38ba8"
            impact_lbl.config(
                text=f"BUY: ${total_buy:,.0f}  |  SELL: ${total_sell:,.0f}  |  Net: ${net:+,.0f}",
                fg=net_color)

        _update_impact()

        # ── Bottom buttons ──
        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, padx=10, pady=(4, 10))

        def _select_group(actions, val):
            for i in range(len(check_vars)):
                if row_actions[i] in actions:
                    check_vars[i].set(val)
            _update_impact()

        tk.Button(btn_frame, text=" All Sells ",
                  font=("Helvetica", 10), bg="#45475a", fg="#cdd6f4",
                  activebackground="#585b70", bd=0, cursor="hand2",
                  command=lambda: _select_group(_SELL_ACTIONS, True)
                  ).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text=" All Buys ",
                  font=("Helvetica", 10), bg="#45475a", fg="#cdd6f4",
                  activebackground="#585b70", bd=0, cursor="hand2",
                  command=lambda: _select_group(_BUY_ACTIONS, True)
                  ).pack(side=tk.LEFT, padx=3)
        tk.Button(btn_frame, text=" Clear All ",
                  font=("Helvetica", 10), bg="#45475a", fg="#cdd6f4",
                  activebackground="#585b70", bd=0, cursor="hand2",
                  command=lambda: _select_group(list(_CHECKABLE), False)
                  ).pack(side=tk.LEFT, padx=3)

        def _apply():
            executed_rows = []
            for i in range(len(check_vars)):
                if check_vars[i].get():
                    try:
                        price = float(price_vars[i].get())
                        shares = int(share_vars[i].get())
                    except ValueError:
                        messagebox.showerror("Error",
                                             f"Invalid price/shares for {row_tickers[i]}")
                        return
                    if shares <= 0:
                        continue
                    executed_rows.append({
                        "RunId": row_run_ids[i],
                        "RecRowId": row_rec_ids[i],
                        "Ticker": row_tickers[i],
                        "Action": row_actions[i],
                        "ExecutedPrice": price,
                        "ExecutedShares": shares,
                        "ExecutionNote": "",
                        "ProfitTier": row_profit_tiers[i],
                    })

            if not executed_rows:
                messagebox.showwarning("Nothing selected",
                                       "Check at least one item to apply.")
                return

            total_buy_cost = sum(
                r["ExecutedPrice"] * r["ExecutedShares"] for r in executed_rows
                if r["Action"] in _BUY_ACTIONS)

            if total_buy_cost > cash_balance + 0.01:
                messagebox.showerror(
                    "Insufficient Cash",
                    f"Buy total ${total_buy_cost:,.2f} exceeds "
                    f"cash balance ${cash_balance:,.2f}")
                return

            summary = (
                f"Apply {len(executed_rows)} executions?\n\n"
                + "\n".join(
                    f"  {r['Action']:10s} {r['Ticker']:6s} "
                    f"{r['ExecutedShares']}sh @ ${r['ExecutedPrice']:.2f}"
                    for r in executed_rows)
            )
            if not messagebox.askyesno("Confirm Execution", summary):
                return

            executed_df = pd.DataFrame(executed_rows)
            applied_df = executed_df.rename(
                columns={"ExecutedPrice": "Price", "ExecutedShares": "Shares"}
            )
            hm.apply_partial_execution(applied_df, trigger_type="T10_MANUAL")

            for r in executed_rows:
                cost = round(r["ExecutedPrice"] * r["ExecutedShares"], 2)
                act = r["Action"]
                if act in _BUY_ACTIONS:
                    hm.record_cash_event(act, -cost,
                                         f"{r['Ticker']} {r['ExecutedShares']}sh")
                elif act in _SELL_ACTIONS:
                    hm.record_cash_event(act, cost,
                                         f"{r['Ticker']} {r['ExecutedShares']}sh")

            artifact_status = ""
            if artifact_run_dir is not None:
                try:
                    current_after = hm.load_current()
                    cash_after = hm.get_cash_balance()
                    total_after = hm.get_portfolio_value() + max(cash_after, 0.0)
                    total_checkable_count = int(recos["Action"].astype(str).isin(_CHECKABLE).sum())
                    exec_meta = record_execution_artifact(
                        artifact_run_dir,
                        executed_df,
                        source="T10",
                        total_checkable_count=total_checkable_count,
                        portfolio_after_execution_df=current_after,
                        cash_balance=cash_after,
                        total_capital=total_after,
                    )
                    artifact_status = str(exec_meta.get("execution_status", ""))
                except Exception as e:
                    self._log_write(f"  [Artifact][WARN] T10 artifact update failed: {type(e).__name__}: {e}")

            popup.destroy()

            self._log_write(f"\n  [T10] Applied {len(executed_rows)} executions:")
            for r in executed_rows:
                self._log_write(
                    f"    {r['Action']:10s}  {r['Ticker']:6s}  "
                    f"{r['ExecutedShares']} shares @ ${r['ExecutedPrice']:.2f}")
            cash_after = hm.get_cash_balance()
            pnl = hm.get_pnl_summary()
            self._log_write(
                f"  Portfolio: ${pnl['total_value']:,.2f} | "
                f"{pnl['holdings_count']} holdings | "
                f"Cash: ${cash_after:,.2f}")
            if artifact_run_id and artifact_status:
                self._log_write(
                    f"  Artifact: {artifact_run_id} -> status={artifact_status}"
                )

        def _undo_last_batch():
            if artifact_run_dir is None or not artifact_run_id:
                messagebox.showwarning("No Artifact Run", "No matching artifact run found for current recommendations.")
                return
            if last_exec_batch.empty:
                messagebox.showinfo("Nothing to Undo", "This run has no recorded T10 execution batch yet.")
                return

            current_now = hm.load_current()
            cash_now = hm.get_cash_balance()
            reverse_rows = []
            total_cash_out = 0.0
            for _, r in last_exec_batch.iterrows():
                orig_action = str(r.get("Action", ""))
                ticker = str(r.get("Ticker", ""))
                price = float(pd.to_numeric(r.get("ExecutedPrice"), errors="coerce"))
                shares = int(pd.to_numeric(r.get("ExecutedShares"), errors="coerce"))
                if shares <= 0 or price <= 0:
                    messagebox.showerror("Undo Error", f"Invalid execution row for {ticker}.")
                    return

                if orig_action in _BUY_ACTIONS:
                    mask = current_now["Ticker"] == ticker if not current_now.empty else pd.Series([], dtype=bool)
                    if current_now.empty or not mask.any():
                        messagebox.showerror("Undo Blocked", f"Cannot undo buy for {ticker}: holding not found.")
                        return
                    held = int(pd.to_numeric(current_now.loc[mask, "Shares"], errors="coerce").iloc[0])
                    if held < shares:
                        messagebox.showerror(
                            "Undo Blocked",
                            f"Cannot undo buy for {ticker}: current shares {held} < executed shares {shares}.",
                        )
                        return
                    reverse_action = "SELL" if held == shares else "TRIM"
                elif orig_action in _SELL_ACTIONS:
                    reverse_action = "BUY"
                    total_cash_out += price * shares
                else:
                    messagebox.showerror("Undo Blocked", f"Unsupported action for undo: {orig_action}")
                    return

                reverse_rows.append({
                    "Ticker": ticker,
                    "Action": reverse_action,
                    "Price": price,
                    "Shares": shares,
                    "OrigAction": orig_action,
                })

            if total_cash_out > cash_now + 0.01:
                messagebox.showerror(
                    "Insufficient Cash",
                    f"Undo requires ${total_cash_out:,.2f} but cash is ${cash_now:,.2f}.",
                )
                return

            summary = "Undo last T10 batch?\n\n" + "\n".join(
                f"  {row['OrigAction']:10s} -> {row['Action']:4s}  {row['Ticker']:6s}  {row['Shares']}sh @ ${row['Price']:.2f}"
                for row in reverse_rows
            )
            if not messagebox.askyesno("Confirm Undo", summary):
                return

            reverse_df = pd.DataFrame(reverse_rows)
            hm.apply_partial_execution(
                reverse_df[["Ticker", "Action", "Price", "Shares"]],
                trigger_type="UNDO",
            )

            for row in reverse_rows:
                cost = round(float(row["Price"]) * int(row["Shares"]), 2)
                if row["Action"] in _BUY_ACTIONS:
                    hm.record_cash_event(
                        f"UNDO_{row['OrigAction']}",
                        -cost,
                        f"{row['Ticker']} {row['Shares']}sh",
                    )
                else:
                    hm.record_cash_event(
                        f"UNDO_{row['OrigAction']}",
                        cost,
                        f"{row['Ticker']} {row['Shares']}sh",
                    )

            reversal_status = ""
            try:
                current_after = hm.load_current()
                cash_after = hm.get_cash_balance()
                total_after = hm.get_portfolio_value() + max(cash_after, 0.0)
                total_checkable_count = int(recos["Action"].astype(str).isin(_CHECKABLE).sum())
                reversal_meta = record_execution_reversal(
                    artifact_run_dir,
                    last_exec_batch,
                    reverse_df,
                    source="T10_UNDO",
                    total_checkable_count=total_checkable_count,
                    portfolio_after_execution_df=current_after,
                    cash_balance=cash_after,
                    total_capital=total_after,
                )
                reversal_status = str(reversal_meta.get("execution_status", ""))
            except Exception as e:
                self._log_write(f"  [Artifact][WARN] undo artifact update failed: {type(e).__name__}: {e}")

            popup.destroy()
            self._log_write(f"\n  [T10 Undo] Reverted {len(reverse_rows)} executions from last batch:")
            for row in reverse_rows:
                self._log_write(
                    f"    {row['OrigAction']:10s} -> {row['Action']:4s}  "
                    f"{row['Ticker']:6s}  {row['Shares']} shares @ ${row['Price']:.2f}"
                )
            cash_after = hm.get_cash_balance()
            pnl = hm.get_pnl_summary()
            self._log_write(
                f"  Portfolio: ${pnl['total_value']:,.2f} | "
                f"{pnl['holdings_count']} holdings | "
                f"Cash: ${cash_after:,.2f}"
            )
            if artifact_run_id and reversal_status:
                self._log_write(
                    f"  Artifact: {artifact_run_id} -> status={reversal_status}"
                )

        tk.Button(btn_frame, text="  Apply Checked  ", command=_apply,
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2").pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="  Undo Last Batch  ", command=_undo_last_batch,
                  font=("Helvetica", 11),
                  bg="#f9e2af", fg="#1e1e2e", activebackground="#fab387",
                  bd=0, cursor="hand2").pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="  Cancel  ", command=popup.destroy,
                  font=("Helvetica", 11),
                  bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
                  bd=0, cursor="hand2").pack(side=tk.RIGHT, padx=3)


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
        n_ts = len(getattr(phase3_lab, "SWEEP_TWOSTEP_ARMS", {}))
        n_bl = len(getattr(phase3_lab, "SWEEP_BLEND_ARMS", {}))
        n_ba = len(getattr(phase3_lab, "SWEEP_BLEND_ASYM_ARMS", {}))
        n_d2 = len(getattr(phase3_lab, "SWEEP_D2_EXIT_ARMS", {}))
        n_d2v2 = len(getattr(phase3_lab, "SWEEP_D2_EXIT_V2_ARMS", {}))
        n_d4 = len(getattr(phase3_lab, "SWEEP_D4_EXIT_ARMS", {}))
        n_d4v2 = len(getattr(phase3_lab, "SWEEP_D4_V2_ARMS", {}))
        n_d4v3 = len(getattr(phase3_lab, "SWEEP_D4_V3_MINI_ARMS", {}))
        n_d4v4 = len(getattr(phase3_lab, "SWEEP_D4_V4_COMBO_ARMS", {}))
        n_d4v5 = len(getattr(phase3_lab, "SWEEP_D4_V5_MICRO_ARMS", {}))

        for text, val in [
            (f"V3 ({n_v3})", "v3"),
            (f"V4 Final ({n_v4})", "v4"),
            (f"TwoStep ({n_ts})", "twostep"),
            (f"Blend ({n_bl})", "blend"),
            (f"BlendAsym ({n_ba})", "blend_asym"),
            (f"D2 Exit ({n_d2})", "d2_exit"),
            (f"D2 v2 ({n_d2v2})", "d2_exit_v2"),
            (f"D4 Exit ({n_d4})", "d4_exit"),
            (f"D4 v2 ({n_d4v2})", "d4_exit_v2"),
            (f"D4 v3 ({n_d4v3})", "d4_exit_v3"),
            (f"D4 v4 ({n_d4v4})", "d4_exit_v4"),
            (f"D4 v5 ({n_d4v5})", "d4_exit_v5"),
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
        all_arms.update(getattr(phase3_lab, "SWEEP_TWOSTEP_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_BLEND_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_BLEND_ASYM_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D2_EXIT_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D2_EXIT_V2_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D4_EXIT_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D4_V2_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D4_V3_MINI_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D4_V4_COMBO_ARMS", {}))
        all_arms.update(getattr(phase3_lab, "SWEEP_D4_V5_MICRO_ARMS", {}))

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
        _ts_arms = getattr(phase3_lab, "SWEEP_TWOSTEP_ARMS", {})
        _bl_arms = getattr(phase3_lab, "SWEEP_BLEND_ARMS", {})
        _ba_arms = getattr(phase3_lab, "SWEEP_BLEND_ASYM_ARMS", {})
        _d2_arms = getattr(phase3_lab, "SWEEP_D2_EXIT_ARMS", {})
        _d2v2_arms = getattr(phase3_lab, "SWEEP_D2_EXIT_V2_ARMS", {})
        _d4_arms = getattr(phase3_lab, "SWEEP_D4_EXIT_ARMS", {})
        _d4v2_arms = getattr(phase3_lab, "SWEEP_D4_V2_ARMS", {})
        _d4v3_arms = getattr(phase3_lab, "SWEEP_D4_V3_MINI_ARMS", {})
        _d4v4_arms = getattr(phase3_lab, "SWEEP_D4_V4_COMBO_ARMS", {})
        _d4v5_arms = getattr(phase3_lab, "SWEEP_D4_V5_MICRO_ARMS", {})

        def _set_arms(*_):
            s = arm_set_var.get()
            for n, v in arm_vars.items():
                if s == "v3":
                    v.set(n in _v3_arms)
                elif s == "v4":
                    v.set(n in _v4_arms)
                elif s == "twostep":
                    v.set(n in _ts_arms)
                elif s == "blend":
                    v.set(n in _bl_arms)
                elif s == "blend_asym":
                    v.set(n in _ba_arms)
                elif s == "d2_exit":
                    v.set(n in _d2_arms)
                elif s == "d2_exit_v2":
                    v.set(n in _d2v2_arms)
                elif s == "d4_exit":
                    v.set(n in _d4_arms)
                elif s == "d4_exit_v2":
                    v.set(n in _d4v2_arms)
                elif s == "d4_exit_v3":
                    v.set(n in _d4v3_arms)
                elif s == "d4_exit_v4":
                    v.set(n in _d4v4_arms)
                elif s == "d4_exit_v5":
                    v.set(n in _d4v5_arms)
                else:
                    v.set(True)
        arm_set_var.trace_add("write", _set_arms)
        _set_arms()

        tk.Label(popup, text="⚠ Pack is built once; each arm adds ~60-120s.",
                 font=("Helvetica", 9), bg="#1e1e2e", fg="#f9e2af"
                 ).pack(pady=4)

        dump_trades_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            popup,
            text="Dump D4 trade log (per-arm CSV of fired exit verdicts)",
            variable=dump_trades_var, bg="#1e1e2e", fg="#cdd6f4",
            selectcolor="#313244", activebackground="#1e1e2e",
            activeforeground="#cdd6f4", font=("Helvetica", 10),
            anchor=tk.W,
        ).pack(padx=20, pady=(0, 4), anchor=tk.W)

        def _start():
            start_d = fields["Start Date"].get().strip()
            end_d = fields["End Date"].get().strip()
            capital = float(fields["Initial Capital ($)"].get().strip())
            mode = mode_var.get()
            dump_trades = bool(dump_trades_var.get())
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
                if dump_trades:
                    print("  D4 trade-log dump: ENABLED")
                print()

                lab_result = phase3_lab.run_lab(
                    arms=selected,
                    start_date=start_d,
                    end_date=end_d,
                    initial_capital=capital,
                    daily_buy_limit=1000.0,
                    rebalance_mode=mode,
                    progress_fn=lambda m: print(m),
                    dump_trades=dump_trades,
                )

                import yaml
                conf_path = str(_THIS_DIR / "config.yaml")
                with open(conf_path) as f:
                    conf = yaml.safe_load(f)
                out_dir = conf["paths"]["output_dir"]
                comp_path = phase3_lab.save_lab_results(lab_result, out_dir)
                print(f"\n  Comparison saved: {comp_path}")

                if dump_trades:
                    written = phase3_lab.dump_trade_logs(
                        lab_result["results"], out_dir,
                    )
                    if written:
                        print(f"\n  D4 trade-log dumps ({len(written)} arm(s)):")
                        for arm, path in written.items():
                            print(f"    {arm:30s} → {path}")
                    else:
                        print("  No D4 events captured — no trade-log files written.")

                self._show_lab_chart(lab_result)

            self._run_task("T17: Lab Sweep", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="  Run Lab Sweep  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2", command=_start).pack()

    # ── T18: Live Performance Tracker ──
    # ── T19: End-to-End GA ──
    def _t19_e2e_ga(self):
        self._ensure_engine()
        if not _conf:
            return

        popup = tk.Toplevel(self)
        popup.title("T19: End-to-End GA Signal Optimization")
        popup.geometry("520x580")
        popup.configure(bg="#1e1e2e")

        tk.Label(popup, text="E2E GA — Walk-Forward Signal Search",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(12, 8))

        tk.Label(popup, text="Optimizes signal directly against Phase 3 simulator.\n"
                 "Walk-forward: train period → validate on unseen period.",
                 font=("Helvetica", 10), bg="#1e1e2e", fg="#9399b2",
                 justify=tk.CENTER).pack(pady=(0, 8))

        fields_frame = tk.Frame(popup, bg="#1e1e2e")
        fields_frame.pack(fill=tk.X, padx=20)

        field_defs = [
            ("Population", "30"),
            ("Generations", "32"),
            ("Train years", "4"),
            ("Val years", "2"),
            ("Step years", "2"),
            ("Start date", "2017-01-03"),
            ("Initial capital", "100000"),
            ("Daily buy limit", "1000"),
            ("Train weight", "0.3"),
            ("Val weight", "0.7"),
            ("Seed", "42"),
        ]
        entries = {}
        for i, (label, default) in enumerate(field_defs):
            tk.Label(fields_frame, text=label, font=("Helvetica", 10),
                     bg="#1e1e2e", fg="#9399b2", anchor=tk.W, width=16
                     ).grid(row=i, column=0, sticky=tk.W, pady=2)
            var = tk.StringVar(value=default)
            tk.Entry(fields_frame, textvariable=var, width=20,
                     font=("Menlo", 10), bg="#313244", fg="#cdd6f4",
                     insertbackground="#cdd6f4", bd=0
                     ).grid(row=i, column=1, pady=2, padx=4)
            entries[label] = var

        estimate_lbl = tk.Label(popup, text="",
                                font=("Helvetica", 10), bg="#1e1e2e", fg="#f9e2af")
        estimate_lbl.pack(pady=6)

        def _update_estimate(*_):
            try:
                p = int(entries["Population"].get())
                g = int(entries["Generations"].get())
                sec_per_eval = 30
                total_evals = p * g
                total_s = total_evals * sec_per_eval
                hours = total_s / 3600
                estimate_lbl.config(
                    text=f"Estimated: ~{hours:.1f} hours "
                    f"({p} pop × {g} gen = {total_evals:,} evals × ~{sec_per_eval}s)")
            except ValueError:
                estimate_lbl.config(text="")

        entries["Population"].trace_add("write", _update_estimate)
        entries["Generations"].trace_add("write", _update_estimate)
        _update_estimate()

        def _start():
            params = {}
            for label, var in entries.items():
                params[label] = var.get()
            popup.destroy()

            def _run():
                from e2e_ga import run_e2e_ga, save_e2e_signal

                result = run_e2e_ga(
                    population_size=int(params["Population"]),
                    generations=int(params["Generations"]),
                    train_years=int(params["Train years"]),
                    val_years=int(params["Val years"]),
                    step_years=int(params["Step years"]),
                    initial_capital=float(params["Initial capital"]),
                    daily_buy_limit=float(params["Daily buy limit"]),
                    train_weight=float(params["Train weight"]),
                    val_weight=float(params["Val weight"]),
                    seed=int(params["Seed"]),
                    start_date=params["Start date"],
                )

                output_dir = _conf["paths"]["output_dir"]
                saved_path = save_e2e_signal(result, output_dir, label="E2E")
                print(f"\n  Signal saved: {saved_path}")
                print(f"  To use: update config.yaml frozen_signal path")

                gen_log = result["generation_log"]
                if gen_log:
                    print(f"\n  Generation Log:")
                    print(f"  {'Gen':>4s}  {'Best':>8s}  {'Mean':>8s}  "
                          f"{'ValCAGR':>8s}  {'ValShp':>8s}  "
                          f"{'IC':>7s}  {'Spd':>7s}  "
                          f"{'K':>3s}  {'wPen':>6s}  {'Time':>6s}")
                    for g in gen_log:
                        print(f"  {g['gen']:4d}  {g['best_fitness']:+8.4f}  "
                              f"{g['mean_fitness']:+8.4f}  "
                              f"{g['best_val_cagr']*100:+7.2f}%  "
                              f"{g['best_val_sharpe']:8.3f}  "
                              f"{g.get('best_ic',0):7.4f}  "
                              f"{g.get('best_spread',0):7.4f}  "
                              f"{g['best_k']:3d}  "
                              f"{g.get('best_w_pen',0):+5.3f}  "
                              f"{g['elapsed_s']:5.0f}s")

            self._run_task("T19: E2E GA Optimization", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="  Start E2E GA  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#f9e2af", fg="#1e1e2e", activebackground="#fab387",
                  bd=0, cursor="hand2", command=_start).pack()

    # ── T25: Phase 5 Retrain (stability-only GA, OOS-safe) ──
    def _t25_p5_retrain(self):
        """Phase 5 retrain: stability-only GA on patched formula + T1 deployment penalty.

        Fixed plan (see phase3/docs/t1_phase2_deployment_tuning_plan.md, iter 2 = T1b):
          • train window : 2017-02-21 → 2024-05-31 (OOS = 2024-06-01 onward)
          • formula      : F1 + F4 + F11 + S1 patched defaults
          • T1           : deployment_penalty ON (w_turnover=0.3, w_cost=0.2)  [iter2, eased]
          • BULL biases  : ALL ON (Config default)
          • GA           : stability 100/300 × 8/12,  final 300 × 20  (reverted budget)
        Users only pick mode (Dry-run vs Full) and whether to force-rebuild pack.
        """
        popup = tk.Toplevel(self)
        popup.title("T25: Phase 5 Retrain (T1b — eased penalty)")
        popup.geometry("580x560")
        popup.configure(bg="#1e1e2e")

        tk.Label(
            popup, text="Phase 5 — T1 Deployment-Tuning (iter 2 = T1b)",
            font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4",
        ).pack(pady=(12, 4))
        tk.Label(
            popup,
            text=(
                "Patched formula (F1/F4/F11/S1) + eased T1 penalty (w=0.3/0.2).\n"
                "BULL biases ON. Budget reverted. Gate #5 relaxed to baseline × 0.7 (v1.1)."
            ),
            font=("Helvetica", 10), bg="#1e1e2e", fg="#9399b2", justify=tk.CENTER,
        ).pack(pady=(0, 8))

        # ── Fixed plan summary (read-only) ───────────────────────────
        plan_frame = tk.LabelFrame(
            popup, text=" Plan (fixed) ", font=("Helvetica", 10, "bold"),
            fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        plan_frame.pack(fill=tk.X, padx=20, pady=4)
        plan_lines = [
            ("Train window",     "2017-02-21  →  2024-05-31"),
            ("OOS holdout",      "2024-06-01  →  pack end (Step C)"),
            ("Formula patches",  "F1 entropy=0.04, F4 per-regime, F11 tradable mask, S1 cs_rank=True"),
            ("T1 deployment",    "ON  (w_turnover=0.3,  w_cost=0.2,  top_n=30,  15 bps)  [iter2, eased]"),
            ("BULL biases",      "ALL ON (Config default)"),
            ("Meta-search",      "OFF  (single template TPL_BALANCED)"),
            ("Stability layer",  "5 seeds  → top 4  → refine (pop 300 × gen 12)"),
            ("Final GA",         "pop 300 × gen 20   (budget reverted)"),
            ("GA seed",          "20260428 (deterministic)"),
            ("Run tag",          "P5_RETRAIN_T1b  (artifact: frozen_signal_P5_RETRAIN_T1b_<stamp>.npz)"),
            ("Gate #5",          "baseline × 0.7  =  ≤ 0.78% commission  (v1.1, relaxed)"),
        ]
        for i, (k, v) in enumerate(plan_lines):
            tk.Label(
                plan_frame, text=k, font=("Helvetica", 9, "bold"),
                bg="#1e1e2e", fg="#9399b2", anchor=tk.W, width=18,
            ).grid(row=i, column=0, sticky=tk.W, padx=6, pady=1)
            tk.Label(
                plan_frame, text=v, font=("Menlo", 9),
                bg="#1e1e2e", fg="#cdd6f4", anchor=tk.W, justify=tk.LEFT,
            ).grid(row=i, column=1, sticky=tk.W, padx=6, pady=1)

        # ── Options ──────────────────────────────────────────────────
        opt_frame = tk.LabelFrame(
            popup, text=" Options ", font=("Helvetica", 10, "bold"),
            fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        opt_frame.pack(fill=tk.X, padx=20, pady=8)

        dry_var   = tk.BooleanVar(value=False)
        force_var = tk.BooleanVar(value=False)
        run_step_c = tk.BooleanVar(value=True)

        tk.Checkbutton(
            opt_frame, text="Dry-run (tiny GA, ~1-2 min — validates wiring)",
            variable=dry_var, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)
        tk.Checkbutton(
            opt_frame,
            text="Force rebuild training pack (deletes cached .npz first)",
            variable=force_var, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)
        tk.Checkbutton(
            opt_frame,
            text="Auto-run Step C gate evaluation after GA completes",
            variable=run_step_c, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)

        estimate_lbl = tk.Label(
            popup, text="", font=("Helvetica", 10, "bold"),
            bg="#1e1e2e", fg="#f9e2af",
        )
        estimate_lbl.pack(pady=(4, 0))

        def _update_estimate(*_):
            if dry_var.get():
                estimate_lbl.config(text="Estimated runtime: ~1–2 min (dry-run)")
            else:
                estimate_lbl.config(text="Estimated runtime: ~80–90 min (P5_RETRAIN was 82 min; budget matches)")
        dry_var.trace_add("write", _update_estimate)
        _update_estimate()

        def _start():
            dry        = bool(dry_var.get())
            force_pack = bool(force_var.get())
            do_step_c  = bool(run_step_c.get())
            popup.destroy()

            def _run():
                from run_phase5_retrain import run_phase5_retrain
                result = run_phase5_retrain(
                    dry_run=dry,
                    force_rebuild_pack=force_pack,
                )
                fs = result.get("frozen_signal_path")
                print(f"\n  frozen signal → {fs}")
                print(f"  run log       → {result.get('run_log_path')}")
                qs = result.get("signal_quality") or {}
                if qs:
                    print(f"  signal quality (in-sample):")
                    for k, v in qs.items():
                        print(f"    {k:<14s}= {v}")

                if do_step_c and fs and os.path.exists(fs):
                    if dry:
                        print("\n  [skip] Step C auto-run disabled for dry-run (in-sample tiny GA; OOS gate not meaningful).")
                        return
                    print("\n" + "=" * 60)
                    print(f"  Chaining Step C gate evaluation  —  arm = {result.get('arm_name')}")
                    print("=" * 60)
                    import runpy as _runpy
                    step_c_path = str(_THIS_DIR / "tests" / "step_c_gate_evaluation.py")
                    sys.argv = [
                        step_c_path,
                        "--signal",   fs,
                        "--arm-name", result.get("arm_name", "P5_RETRAIN_T1b"),
                    ]
                    _runpy.run_path(step_c_path, run_name="__main__")
                else:
                    if do_step_c:
                        print("\n  [warn] frozen signal not found on disk — Step C skipped.")
                    print("\n  To run Step C manually later:")
                    print(f"    python3 -u phase3/tests/step_c_gate_evaluation.py \\")
                    print(f"        --signal {fs!s} \\")
                    print(f"        --arm-name {result.get('arm_name', 'P5_RETRAIN_T1b')}")

            self._run_task("T25: Phase 5 Retrain (stability GA)", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=12)
        tk.Button(
            btn_frame, text="  Start Phase 5 Retrain  ",
            font=("Helvetica", 12, "bold"),
            bg="#f9e2af", fg="#1e1e2e", activebackground="#fab387",
            bd=0, cursor="hand2", command=_start,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_frame, text="  Cancel  ",
            font=("Helvetica", 11),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", command=popup.destroy,
        ).pack(side=tk.LEFT, padx=6)

    # ── T26: T5 Walk-Forward (pack rebuild + 6-fold evaluation) ──
    def _t26_walk_forward(self):
        """T5 Phase A: Walk-forward evaluation across 6 temporal folds.

        Fixed plan (see phase3/docs/t5_walk_forward_plan.md v2):
          • Pack:    2011-01-03 → 2026-02-27 (rebuilt if absent)
          • Folds:   F0a/F0b (pre-train OOS) + F1-F3 (in-sample) + F4 (post-train OOS)
          • Signals: Baseline_V2, P5_RETRAIN, P5_RETRAIN_T1, P5_RETRAIN_T1b
          • Protocol identical to Step C (SIDE_DEF_p12, 10/5 bps, $1K daily limit)
          • Gates:   G6-A (CV≤0.5), G6-B (CV≤baseline), G6-C (worst≥baseline), G6-D (all>0)
        """
        popup = tk.Toplevel(self)
        popup.title("T26: T5 Walk-Forward (Phase A)")
        popup.geometry("600x620")
        popup.configure(bg="#1e1e2e")

        tk.Label(
            popup, text="T5 Walk-Forward — Phase A (6-fold)",
            font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4",
        ).pack(pady=(12, 4))
        tk.Label(
            popup,
            text=(
                "Evaluates 4 signals across 6 temporal folds on 14-year pack.\n"
                "Quantifies CAGR stability (CV) across regimes + pre/post-train OOS."
            ),
            font=("Helvetica", 10), bg="#1e1e2e", fg="#9399b2", justify=tk.CENTER,
        ).pack(pady=(0, 8))

        # ── Plan summary ───────────────────────────────────────────
        plan_frame = tk.LabelFrame(
            popup, text=" Plan (fixed) ", font=("Helvetica", 10, "bold"),
            fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        plan_frame.pack(fill=tk.X, padx=20, pady=4)
        plan_lines = [
            ("Pack window",    "2011-01-03  →  2026-02-27  (14.2 yr)"),
            ("Fold F0a",       "2012-01-01 → 2014-12-31  pre-train OOS (true OOS)"),
            ("Fold F0b",       "2015-01-01 → 2016-12-31  pre-train OOS (true OOS)"),
            ("Fold F1",        "2019-01-01 → 2020-12-31  in-sample (COVID regime)"),
            ("Fold F2",        "2021-01-01 → 2022-12-31  in-sample (meme bull + bear)"),
            ("Fold F3",        "2023-01-01 → 2024-05-31  in-sample (AI rally)"),
            ("Fold F4",        "2024-06-01 → 2026-02-27  post-train OOS  (≈ Step C)"),
            ("Signals",        "Baseline_V2 + P5_RETRAIN + T1 + T1b  (4 arms)"),
            ("Protocol",       "SIDE_DEF_p12,  10/5 bps,  $100K / $1K daily limit"),
            ("Gates",          "G6-A CV≤0.5 · G6-B CV≤base · G6-C worst≥base · G6-D all>0"),
            ("Sims",           "6 folds × 4 signals = 24 simulations"),
            ("Artifacts",      "t5_walk_forward_results_<stamp>.json + .md"),
        ]
        for i, (k, v) in enumerate(plan_lines):
            tk.Label(
                plan_frame, text=k, font=("Helvetica", 9, "bold"),
                bg="#1e1e2e", fg="#9399b2", anchor=tk.W, width=16,
            ).grid(row=i, column=0, sticky=tk.W, padx=6, pady=1)
            tk.Label(
                plan_frame, text=v, font=("Menlo", 9),
                bg="#1e1e2e", fg="#cdd6f4", anchor=tk.W, justify=tk.LEFT,
            ).grid(row=i, column=1, sticky=tk.W, padx=6, pady=1)

        # ── Options ────────────────────────────────────────────────
        opt_frame = tk.LabelFrame(
            popup, text=" Options ", font=("Helvetica", 10, "bold"),
            fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        opt_frame.pack(fill=tk.X, padx=20, pady=8)

        force_pack_var = tk.BooleanVar(value=False)
        skip_pack_var  = tk.BooleanVar(value=False)

        tk.Checkbutton(
            opt_frame,
            text="Force rebuild pack (deletes cached 2011-2026 .npz first, ~15 min)",
            variable=force_pack_var, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)
        tk.Checkbutton(
            opt_frame,
            text="Skip pack rebuild step (assumes pack already exists)",
            variable=skip_pack_var, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)

        estimate_lbl = tk.Label(
            popup, text="", font=("Helvetica", 10, "bold"),
            bg="#1e1e2e", fg="#f9e2af",
        )
        estimate_lbl.pack(pady=(4, 0))

        def _update_estimate(*_):
            if skip_pack_var.get():
                estimate_lbl.config(text="Estimated runtime: ~8–10 min (eval only)")
            elif force_pack_var.get():
                estimate_lbl.config(text="Estimated runtime: ~25 min (~15 min pack + ~10 min eval)")
            else:
                estimate_lbl.config(text="Estimated runtime: ~10 min (pack cached) — first run ~25 min")
        force_pack_var.trace_add("write", _update_estimate)
        skip_pack_var.trace_add("write",  _update_estimate)
        _update_estimate()

        def _start():
            force_pack = bool(force_pack_var.get())
            skip_pack  = bool(skip_pack_var.get())
            popup.destroy()

            def _run():
                import runpy as _runpy
                tests_dir = _THIS_DIR / "tests"

                # Step 1: pack rebuild (unless skipped)
                if not skip_pack:
                    print("=" * 60)
                    print("  Step 1 / 2  —  Pack rebuild (2011-01-03 → 2026-02-27)")
                    print("=" * 60)
                    from tests.rebuild_pack_walk_forward import rebuild_pack  # type: ignore
                    try:
                        rb = rebuild_pack(force=force_pack)
                        print(f"\n  pack ready : {rb.get('pack_path')}")
                        print(f"  tickers    : {rb.get('n_tickers')}  dates: {rb.get('n_dates')}")
                        print(f"  elapsed    : {rb.get('elapsed_sec')}s")
                    except Exception as exc:
                        print(f"\n[ERROR] pack rebuild failed: {exc}")
                        return
                else:
                    print("  [skip] pack rebuild (user requested)")

                # Step 2: run walk-forward evaluation
                print()
                print("=" * 60)
                print("  Step 2 / 2  —  Walk-forward evaluation (6 folds × 4 signals)")
                print("=" * 60)
                step_d_path = str(tests_dir / "step_d_walk_forward.py")
                sys.argv = [step_d_path]
                try:
                    _runpy.run_path(step_d_path, run_name="__main__")
                except SystemExit as se:
                    code = getattr(se, "code", 0) or 0
                    print(f"\n  step_d exit code: {code}")
                except Exception as exc:
                    print(f"\n[ERROR] walk-forward eval failed: {exc}")

            self._run_task("T26: T5 Walk-Forward (Phase A)", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=12)
        tk.Button(
            btn_frame, text="  Start Walk-Forward  ",
            font=("Helvetica", 12, "bold"),
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
            bd=0, cursor="hand2", command=_start,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_frame, text="  Cancel  ",
            font=("Helvetica", 11),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", command=popup.destroy,
        ).pack(side=tk.LEFT, padx=6)

    # ── T27: Phase B overnight batch orchestrator (Batches 1 / 2 / 3) ──
    def _t27_phase_b_batch(self):
        """Phase B — overnight batch (Batches 1 scalar profile / 2 scalar window / 3 regime-conditional).

        Batches 1-2 (scalar) reference: phase3/docs/phase_b_batch_plan.md
        Batch 3 (regime-conditional Option 3a): phase3/docs/phase_b2_regime_cond_plan.md
        """
        popup = tk.Toplevel(self)
        popup.title("T27: Phase B Batch")
        popup.geometry("720x760")
        popup.configure(bg="#1e1e2e")

        tk.Label(
            popup, text="Phase B — Overnight Batch (1 scalar / 2 scalar-win / 3 regime-cond)",
            font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4",
        ).pack(pady=(12, 4))
        tk.Label(
            popup,
            text=(
                "B1/B2 sweep scalar w_turnover × w_cost. B3 uses the Phase-B2\n"
                "Option-3a engine change (per-regime BULL/SIDE/DEF penalties)."
            ),
            font=("Helvetica", 10), bg="#1e1e2e", fg="#9399b2", justify=tk.CENTER,
        ).pack(pady=(0, 8))

        # ── Preset summary (read-only) ───────────────────────────────
        presets_frame = tk.LabelFrame(
            popup, text=" 12 presets (Batches 1-3) ", font=("Helvetica", 10, "bold"),
            fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        presets_frame.pack(fill=tk.X, padx=20, pady=4)
        preset_lines = [
            ("B1 consv",    "P5B_CONSV       scalar w_to=0.40 w_co=0.25          (1.33× T1b)"),
            ("B1 prop",     "P5B_PROP        scalar w_to=0.15 w_co=0.10          (0.50× T1b ← Opt-A mimic)"),
            ("B1 aggr",     "P5B_AGGR        scalar w_to=0.05 w_co=0.03          (0.17× T1b)"),
            ("B2 win_base", "P5B_WIN_BASE    base win + seed_B (PROP-profile)"),
            ("B2 win_fwd",  "P5B_WIN_FWD     +1y  win 2013→2025   seed_B"),
            ("B2 win_back", "P5B_WIN_BACK    −1y  win 2011→2023   seed_B"),
            ("B3 mild",     "P5C_MILD        to=(.10,.30,.40) co=(.05,.20,.25)   BULL mild"),
            ("B3 balanced", "P5C_BALANCED    to=(.15,.25,.35) co=(.10,.15,.20)   PROP→tier"),
            ("B3 deep",     "P5C_DEEP        to=(.00,.40,.50) co=(.00,.25,.30)   BULL free + heavy SIDE/DEF"),
            ("B3 bull_free","P5C_BULL_FREE   to=(.00,.30,.40) co=(.00,.20,.25)   BULL=0 × SIDE/DEF mid"),
            ("B3 def_heavy","P5C_DEF_HEAVY   to=(.05,.15,.60) co=(.02,.10,.35)   DEF isolated"),
            ("B3 side_heavy","P5C_SIDE_HEAVY to=(.05,.50,.30) co=(.02,.30,.15)   SIDE focused"),
        ]
        for i, (k, v) in enumerate(preset_lines):
            tk.Label(
                presets_frame, text=k, font=("Helvetica", 9, "bold"),
                bg="#1e1e2e", fg="#9399b2", anchor=tk.W, width=13,
            ).grid(row=i, column=0, sticky=tk.W, padx=6, pady=1)
            tk.Label(
                presets_frame, text=v, font=("Menlo", 9),
                bg="#1e1e2e", fg="#cdd6f4", anchor=tk.W, justify=tk.LEFT,
            ).grid(row=i, column=1, sticky=tk.W, padx=6, pady=1)

        # ── Target selection ─────────────────────────────────────────
        target_frame = tk.LabelFrame(
            popup, text=" Target ", font=("Helvetica", 10, "bold"),
            fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        target_frame.pack(fill=tk.X, padx=20, pady=8)

        target_var = tk.StringVar(value="3")
        for val, lbl in (
            ("3",   "Batch 3 only  (6× Phase B2 regime-cond)     — NEXT OVERNIGHT  (~12 h)"),
            ("1",   "Batch 1 only  (consv + prop + aggr)         — scalar profile sweep  (~6.5 h)"),
            ("2",   "Batch 2 only  (win_base + win_fwd + win_back) — scalar window sweep  (~6.5 h)"),
            ("all", "All batches back-to-back  (12 runs)         — single-shot long run  (~26 h)"),
            ("dry", "Dry-run smoke   (12 × ~2 min)               — wiring validation     (~25 min)"),
        ):
            tk.Radiobutton(
                target_frame, text=lbl, variable=target_var, value=val,
                font=("Menlo", 9), bg="#1e1e2e", fg="#cdd6f4",
                activebackground="#1e1e2e", activeforeground="#cdd6f4",
                selectcolor="#313244", anchor=tk.W, justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=8, pady=1)

        # ── Options ──────────────────────────────────────────────────
        opt_frame = tk.LabelFrame(
            popup, text=" Options ", font=("Helvetica", 10, "bold"),
            fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE,
        )
        opt_frame.pack(fill=tk.X, padx=20, pady=4)

        force_pack_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt_frame,
            text="Force rebuild training pack for every run (slower; use only on cache suspicion)",
            variable=force_pack_var, onvalue=True, offvalue=False,
            font=("Helvetica", 10), bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#1e1e2e", activeforeground="#cdd6f4",
            selectcolor="#313244",
        ).pack(anchor=tk.W, padx=8, pady=2)

        estimate_lbl = tk.Label(
            popup, text="", font=("Helvetica", 10, "bold"),
            bg="#1e1e2e", fg="#f9e2af",
        )
        estimate_lbl.pack(pady=(6, 0))

        def _update_estimate(*_):
            t = target_var.get()
            if t == "dry":
                estimate_lbl.config(text="Estimated runtime: ~25 min  (12 presets × ~2 min each)")
            elif t == "all":
                estimate_lbl.config(text="Estimated runtime: ~25–30 h  (12 runs back-to-back)")
            elif t == "3":
                estimate_lbl.config(text="Estimated runtime: ~12 h  (6 Phase-B2 regime-cond runs)")
            else:
                estimate_lbl.config(text="Estimated runtime: ~6.5 h  (3 scalar runs back-to-back)")
        target_var.trace_add("write", _update_estimate)
        _update_estimate()

        def _start():
            t          = target_var.get()
            force_pack = bool(force_pack_var.get())
            popup.destroy()

            def _run():
                from run_phase5_batch_b import run_batch
                kwargs = {"force_rebuild_pack": force_pack}
                if t == "1":
                    kwargs["batch"] = 1
                elif t == "2":
                    kwargs["batch"] = 2
                elif t == "3":
                    kwargs["batch"] = 3
                elif t == "dry":
                    kwargs["dry_run"] = True
                # "all" → no batch filter, full run
                result = run_batch(**kwargs)
                n_ok   = result.get("n_completed", 0)
                n_fail = result.get("n_failed", 0)
                print(f"\n[T27] batch finished — ok={n_ok}  failed={n_fail}  "
                      f"total={result.get('total_min', 0):.1f} min")
                print(f"       progress log: {result.get('progress_path')}")

            self._run_task(f"T27: Phase B Batch ({t})", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=14)
        tk.Button(
            btn_frame, text="  Start Batch  ",
            font=("Helvetica", 12, "bold"),
            bg="#cba6f7", fg="#1e1e2e", activebackground="#b4befe",
            bd=0, cursor="hand2", command=_start,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_frame, text="  Cancel  ",
            font=("Helvetica", 11),
            bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
            bd=0, cursor="hand2", command=popup.destroy,
        ).pack(side=tk.LEFT, padx=6)

    def _t18_performance(self):
        self._ensure_engine()
        if not _conf:
            return

        def _run():
            import pandas as _pd
            import numpy as _np
            from datetime import timedelta
            from holdings_manager import HoldingsManager
            from engine_loader import engine

            hm = HoldingsManager(_conf["paths"]["holdings_log"])
            log = hm.load_daily_log()

            if log.empty or len(log) < 2:
                print("[T18] Not enough DailyLog data (need at least 2 rows).")
                print("  Run T7 daily for a few days first.")
                return

            log["Date"] = _pd.to_datetime(log["Date"])
            log = log.sort_values("Date").drop_duplicates(subset="Date", keep="last").reset_index(drop=True)

            has_total = "TotalCapital" in log.columns and log["TotalCapital"].notna().any() and (log["TotalCapital"] > 0).any()
            if has_total:
                capital_col = "TotalCapital"
            else:
                capital_col = "PortfolioValue"
                print("  [INFO] TotalCapital not in log — using PortfolioValue only")

            capital = _np.array(log[capital_col].astype(float).values, copy=True)
            dates = _np.array(log["Date"].values, copy=True)

            initial_capital = _conf.get("portfolio", {}).get("total_capital", 100000.0)
            if capital[0] <= 0:
                capital[0] = initial_capital

            start_date = log["Date"].iloc[0] - timedelta(days=5)
            end_date = log["Date"].iloc[-1] + timedelta(days=1)

            print(f"  Loading SPY benchmark ({start_date.date()} → {end_date.date()})...")
            spy_df = engine.load_ohlcv_from_cache(_cfg, "SPY", start_date, end_date)
            spy_map = {}
            if not spy_df.empty:
                spy_df["date"] = _pd.to_datetime(spy_df["date"])
                spy_map = dict(zip(spy_df["date"].dt.date, spy_df["close"].astype(float)))
            else:
                print("  [WARN] SPY cache empty — downloading...")
                try:
                    engine.download_ohlcv_to_cache_chunked(
                        _cfg, "SPY", start_date, end_date, overwrite=True)
                    spy_df = engine.load_ohlcv_from_cache(_cfg, "SPY", start_date, end_date)
                    if not spy_df.empty:
                        spy_df["date"] = _pd.to_datetime(spy_df["date"])
                        spy_map = dict(zip(spy_df["date"].dt.date, spy_df["close"].astype(float)))
                except Exception as e:
                    print(f"  [WARN] SPY download failed: {e}")

            spy_prices = []
            for d in log["Date"]:
                d_date = _pd.Timestamp(d).date()
                price = spy_map.get(d_date)
                if price is None:
                    candidates = [v for k, v in spy_map.items() if k <= d_date]
                    price = candidates[-1] if candidates else _np.nan
                spy_prices.append(price)
            spy_arr = _np.array(spy_prices, dtype=float)

            port_base = capital[0] if capital[0] > 0 else initial_capital

            # -- Live snapshot from Current + CashLedger --
            live_holdings = hm.get_portfolio_value()
            live_cash = hm.get_cash_balance()
            live_total = live_holdings + max(live_cash, 0.0)
            total_deposited = hm.get_total_deposited()
            if total_deposited <= 0:
                total_deposited = initial_capital

            # Append live snapshot as today's data point if newer than last log
            from datetime import datetime as _dt
            today_dt = _pd.Timestamp(_dt.now().date())
            last_log_dt = _pd.Timestamp(log["Date"].iloc[-1])
            if live_total > 0 and today_dt > last_log_dt:
                capital = _np.append(capital, live_total)
                dates = _np.append(dates, today_dt)
                spy_today = spy_map.get(today_dt.date())
                if spy_today is None:
                    cands = [v for k, v in spy_map.items() if k <= today_dt.date()]
                    spy_today = cands[-1] if cands else _np.nan
                spy_arr = _np.append(spy_arr, spy_today if spy_today else _np.nan)
            elif live_total > 0:
                capital[-1] = live_total

            n_days = len(capital)
            total_ret = (capital[-1] / port_base - 1) * 100
            years = max((dates[-1] - dates[0]) / _np.timedelta64(1, 'D') / 365.25, 1/365.25)
            cagr = ((capital[-1] / port_base) ** (1 / years) - 1) * 100
            daily_rets = _np.diff(capital) / capital[:-1]
            sharpe = (daily_rets.mean() / daily_rets.std() * _np.sqrt(252)) if daily_rets.std() > 0 else 0
            mdd_vals = capital / _np.maximum.accumulate(capital) - 1
            mdd = mdd_vals.min() * 100
            win_rate = (_np.sum(daily_rets > 0) / len(daily_rets) * 100) if len(daily_rets) > 0 else 0

            port_norm = capital / port_base * 100.0
            port_cummax = _np.maximum.accumulate(capital)
            port_dd = (capital - port_cummax) / _np.where(port_cummax > 0, port_cummax, 1.0) * 100

            spy_norm = _np.full_like(spy_arr, _np.nan)
            first_valid = _np.where(~_np.isnan(spy_arr))[0]
            if len(first_valid) > 0:
                spy_base = spy_arr[first_valid[0]]
                spy_norm = spy_arr / spy_base * 100.0

            spy_cummax = _np.maximum.accumulate(
                _np.nan_to_num(spy_arr, nan=spy_arr[first_valid[0]] if len(first_valid) > 0 else 1.0))
            spy_dd = (spy_arr - spy_cummax) / _np.where(spy_cummax > 0, spy_cummax, 1.0) * 100

            spy_total_ret = _np.nan
            spy_cagr = _np.nan
            if len(first_valid) > 0 and not _np.isnan(spy_arr[-1]):
                spy_total_ret = (spy_arr[-1] / spy_arr[first_valid[0]] - 1) * 100
                spy_cagr = ((spy_arr[-1] / spy_arr[first_valid[0]]) ** (1 / years) - 1) * 100

            live_ret = (live_total / total_deposited - 1) * 100 if total_deposited > 0 else 0
            invest_pct = (live_holdings / live_total * 100) if live_total > 0 else 0
            pnl_summary = hm.get_pnl_summary()

            today_str = _dt.now().strftime("%Y-%m-%d")
            metrics = {
                "Period": f"{_pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} → {today_str}",
                "Trading Days": n_days,
                "Initial Capital": f"${total_deposited:,.0f}",
                "Current Value": f"${live_total:,.2f}",
                "Total Return": f"{live_ret:+.2f}%",
                "CAGR": f"{cagr:+.2f}%",
                "Sharpe": f"{sharpe:.2f}",
                "MDD": f"{mdd:.2f}%",
                "Win Rate": f"{win_rate:.1f}%",
                "SPY Return": f"{spy_total_ret:+.2f}%" if not _np.isnan(spy_total_ret) else "N/A",
                "SPY CAGR": f"{spy_cagr:+.2f}%" if not _np.isnan(spy_cagr) else "N/A",
                "Alpha (vs SPY)": f"{live_ret - spy_total_ret:+.2f}%" if not _np.isnan(spy_total_ret) else "N/A",
            }

            regimes = log["Regime"].values if "Regime" in log.columns else None

            report_lines = ["=" * 55, "  T18 LIVE PERFORMANCE REPORT", "=" * 55, ""]
            for k, v in metrics.items():
                report_lines.append(f"  {k:20s}: {v}")
            report_lines.append("")

            report_lines.append("  Portfolio Breakdown:")
            report_lines.append(f"    Holdings          : ${live_holdings:>12,.2f}  ({invest_pct:.1f}%)")
            report_lines.append(f"    Cash              : ${live_cash:>12,.2f}  ({100 - invest_pct:.1f}%)")
            report_lines.append(f"    Unrealized PnL    : ${pnl_summary['total_pnl']:>12,.2f}  ({pnl_summary['pnl_pct']:+.2f}%)")
            report_lines.append(f"    Positions         : {pnl_summary['holdings_count']}")
            report_lines.append("")

            if "Regime" in log.columns:
                report_lines.append("  Regime Distribution:")
                for rg in ["BULL", "SIDE", "DEFENSIVE", "CRASH"]:
                    cnt = (log["Regime"] == rg).sum()
                    if cnt > 0:
                        report_lines.append(f"    {rg:12s}: {cnt:3d} days ({cnt/n_days*100:.1f}%)")
                report_lines.append("")

            cur = hm.load_current()
            if not cur.empty:
                report_lines.append("  Current Holdings:")
                for _, h in cur.sort_values("MarketValue", ascending=False).iterrows():
                    pnl = h.get("PnL_Pct", 0)
                    report_lines.append(
                        f"    {h['Ticker']:6s}  ${h['MarketValue']:>10,.2f}  "
                        f"w={h.get('Weight', 0):5.1f}%  PnL={pnl:+.1f}%")
                report_lines.append("")

            report_text = "\n".join(report_lines)
            print(report_text)

            self.after(0, lambda: self._show_perf_chart(
                dates, capital, port_norm, spy_norm, port_dd, spy_dd,
                regimes, metrics, report_text))

        self._run_task("T18: Performance Tracker", _run)

    def _show_perf_chart(self, dates, capital, port_norm, spy_norm, port_dd, spy_dd,
                         regimes, metrics, report_text):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import pandas as _pd
        import numpy as _np

        popup = tk.Toplevel(self)
        popup.title("T18: Live Performance Tracker")
        popup.geometry("1300x900")
        popup.configure(bg="#1e1e2e")

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(13, 7), gridspec_kw={"height_ratios": [3, 1]})
        fig.patch.set_facecolor("#1e1e2e")

        pd_dates = _pd.to_datetime(dates)

        regime_colors = {"BULL": "#a6e3a120", "SIDE": "#f9e2af20",
                         "DEFENSIVE": "#f38ba820", "CRASH": "#eba0ac30"}
        if regimes is not None and len(regimes) == len(pd_dates):
            prev_regime = regimes[0]
            span_start = 0
            for i in range(1, len(regimes)):
                if regimes[i] != prev_regime or i == len(regimes) - 1:
                    end_i = i if regimes[i] != prev_regime else i + 1
                    clr = regime_colors.get(prev_regime, "#45475a10")
                    ax1.axvspan(pd_dates[span_start], pd_dates[min(end_i, len(pd_dates)-1)],
                                color=clr, alpha=0.3)
                    ax2.axvspan(pd_dates[span_start], pd_dates[min(end_i, len(pd_dates)-1)],
                                color=clr, alpha=0.3)
                    prev_regime = regimes[i]
                    span_start = i

        ax1.set_facecolor("#11111b")
        ax1.plot(pd_dates, port_norm, color="#89b4fa", linewidth=1.8, label="Portfolio")
        ax1.fill_between(pd_dates, 100, port_norm, alpha=0.08, color="#89b4fa")
        if not _np.all(_np.isnan(spy_norm)):
            ax1.plot(pd_dates, spy_norm, color="#9399b2", linewidth=1.2,
                     linestyle="--", label="SPY", alpha=0.8)

        ax1.axhline(100, color="#585b70", linewidth=0.6, linestyle=":")
        ax1.set_ylabel("Normalized Value (base=100)", color="#cdd6f4")
        ax1.set_title("Live Performance — Portfolio vs SPY", color="#cdd6f4", fontsize=13)
        ax1.legend(facecolor="#313244", edgecolor="#45475a", labelcolor="#cdd6f4",
                   fontsize=9, loc="upper left")
        ax1.tick_params(colors="#9399b2")
        ax1.grid(True, alpha=0.2, color="#45475a")

        ax2.set_facecolor("#11111b")
        ax2.fill_between(pd_dates, port_dd, color="#f38ba8", alpha=0.6, label="Portfolio DD")
        if not _np.all(_np.isnan(spy_dd)):
            ax2.plot(pd_dates, spy_dd, color="#9399b2", linewidth=0.9,
                     linestyle="--", alpha=0.7, label="SPY DD")
        ax2.set_ylabel("Drawdown (%)", color="#cdd6f4")
        ax2.set_xlabel("Date", color="#cdd6f4")
        ax2.legend(facecolor="#313244", edgecolor="#45475a", labelcolor="#cdd6f4",
                   fontsize=8, loc="lower left")
        ax2.tick_params(colors="#9399b2")
        ax2.grid(True, alpha=0.2, color="#45475a")

        for ax in (ax1, ax2):
            for spine in ax.spines.values():
                spine.set_color("#45475a")

        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=popup)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=False, pady=(5, 0))

        txt = tk.Text(popup, bg="#11111b", fg="#cdd6f4",
                      font=("Menlo", 9), wrap=tk.NONE,
                      insertbackground="#cdd6f4", relief=tk.FLAT, height=14)
        txt_sb_y = tk.Scrollbar(popup, orient=tk.VERTICAL, command=txt.yview)
        txt_sb_x = tk.Scrollbar(popup, orient=tk.HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=txt_sb_y.set, xscrollcommand=txt_sb_x.set)
        txt_sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        txt_sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        txt.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        txt.insert("1.0", report_text)
        txt.config(state=tk.DISABLED)

        btn_bar = tk.Frame(popup, bg="#1e1e2e")
        btn_bar.pack(fill=tk.X, padx=5, pady=(0, 5))

        def _copy_all():
            popup.clipboard_clear()
            popup.clipboard_append(report_text)
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


    # ── T20: Regime Blend Control Panel ──
    def _t20_regime_blend(self):
        import yaml

        popup = tk.Toplevel(self)
        popup.title("T20: Regime Blend Control Panel")
        popup.geometry("720x700")
        popup.configure(bg="#1e1e2e")

        conf_path = str(_THIS_DIR / "config.yaml")
        with open(conf_path) as f:
            conf = yaml.safe_load(f)
        rg = conf.get("regime", {})

        # ── Header ──
        tk.Label(popup, text="Regime Blend — Hysteresis + Soft Weight Interpolation",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(10, 4))

        # ── Current Status ──
        status_fr = tk.LabelFrame(popup, text=" Current Status ",
                                  font=("Helvetica", 11, "bold"),
                                  fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        status_fr.pack(fill=tk.X, padx=16, pady=6)

        status_text = tk.StringVar(value="Loading...")
        status_lbl = tk.Label(status_fr, textvariable=status_text,
                              font=("Menlo", 10), bg="#1e1e2e", fg="#cdd6f4",
                              justify=tk.LEFT, anchor=tk.W)
        status_lbl.pack(fill=tk.X, padx=10, pady=6)

        def _refresh_status():
            try:
                self._ensure_engine()
                from daily_runner import get_current_vix, _build_blend_params
                from regime_blend import compute_blend_alphas, apply_hysteresis
                from holdings_manager import HoldingsManager

                prev_regime = "SIDE"
                try:
                    hm = HoldingsManager(conf["paths"]["holdings_log"])
                    dl = hm.load_daily_log()
                    if not dl.empty and "regime" in dl.columns:
                        prev_regime = str(dl.iloc[-1]["regime"])
                except Exception:
                    pass

                bp = _build_blend_params(_cfg, rg)
                vix_close, regime_step, _ = get_current_vix(_cfg)
                regime_h = apply_hysteresis(prev_regime, vix_close, **bp)
                ab, as_, ad = compute_blend_alphas(vix_close, **bp)

                blend_on = bool(rg.get("regime_blend_enabled", False))
                bs_w = float(rg.get("bull_side_blend_width", 2.0))
                sd_w = float(rg.get("side_def_blend_width", 3.0))
                bull_t = float(rg.get("vix_bull_threshold", 18.0))
                def_t = float(rg.get("vix_defensive_threshold", 30.0))

                lines = []
                lines.append(f"VIX = {vix_close:.2f}  |  Blend {'ON' if blend_on else 'OFF'}")
                lines.append(f"Step regime   : {regime_step}  (hard threshold)")
                lines.append(f"Hysteresis    : {regime_h}  (prev={prev_regime})")
                lines.append(f"Blend alphas  : BULL={ab:.2f}  SIDE={as_:.2f}  DEF={ad:.2f}")
                lines.append(f"")
                lines.append(f"BULL zone     : VIX < {bull_t - bs_w:.0f}")
                lines.append(f"BULL↔SIDE     : VIX {bull_t - bs_w:.0f}~{bull_t + bs_w:.0f}  (center={bull_t:.0f}, width=±{bs_w:.0f})")
                lines.append(f"SIDE zone     : VIX {bull_t + bs_w:.0f}~{def_t - sd_w:.0f}")
                lines.append(f"SIDE↔DEF      : VIX {def_t - sd_w:.0f}~{def_t + sd_w:.0f}  (center={def_t:.0f}, width=±{sd_w:.0f})")
                lines.append(f"DEF zone      : VIX > {def_t + sd_w:.0f}")
                status_text.set("\n".join(lines))
            except Exception as e:
                status_text.set(f"Error: {e}")

        # ── Settings ──
        set_fr = tk.LabelFrame(popup, text=" Settings ",
                                font=("Helvetica", 11, "bold"),
                                fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        set_fr.pack(fill=tk.X, padx=16, pady=6)

        entry_style = dict(font=("Menlo", 11), bg="#313244", fg="#cdd6f4",
                           insertbackground="#cdd6f4", relief=tk.FLAT)
        lbl_style = dict(font=("Helvetica", 11), bg="#1e1e2e", fg="#bac2de", anchor=tk.W)

        blend_var = tk.BooleanVar(value=bool(rg.get("regime_blend_enabled", False)))
        toggle_fr = tk.Frame(set_fr, bg="#1e1e2e")
        toggle_fr.pack(fill=tk.X, padx=10, pady=6)
        tk.Label(toggle_fr, text="Regime Blend", width=24, **lbl_style).pack(side=tk.LEFT)
        tk.Checkbutton(toggle_fr, text="Enabled", variable=blend_var,
                       bg="#1e1e2e", fg="#a6e3a1", selectcolor="#313244",
                       activebackground="#1e1e2e", activeforeground="#a6e3a1",
                       font=("Helvetica", 11, "bold")).pack(side=tk.LEFT)

        fields = {}
        for label, key, default in [
            ("BULL↔SIDE blend width", "bull_side_blend_width", "2.0"),
            ("SIDE↔DEF blend width", "side_def_blend_width", "3.0"),
        ]:
            row = tk.Frame(set_fr, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=10, pady=3)
            tk.Label(row, text=label, width=24, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, width=8, **entry_style)
            e.insert(0, str(rg.get(key, default)))
            e.pack(side=tk.LEFT, ipady=3, padx=4)
            tk.Label(row, text="VIX points (each side of threshold)",
                     font=("Helvetica", 9), bg="#1e1e2e", fg="#6c7086").pack(side=tk.LEFT, padx=6)
            fields[key] = e

        # ── Action Buttons ──
        act_fr = tk.Frame(popup, bg="#1e1e2e")
        act_fr.pack(fill=tk.X, padx=16, pady=8)

        btn_s = dict(font=("Helvetica", 11, "bold"), bd=0, cursor="hand2", width=22)

        def _save_config():
            try:
                rg["regime_blend_enabled"] = blend_var.get()
                for key, entry in fields.items():
                    rg[key] = float(entry.get().strip())
                conf["regime"] = rg
                with open(conf_path, "w") as f:
                    yaml.safe_dump(conf, f, default_flow_style=False, sort_keys=False)
                print(f"[T20] Config saved: blend={'ON' if blend_var.get() else 'OFF'}"
                      f"  bs_width={rg.get('bull_side_blend_width')}"
                      f"  sd_width={rg.get('side_def_blend_width')}")
                _refresh_status()
            except Exception as e:
                print(f"[T20] Save error: {e}")

        def _preview_chart():
            try:
                self._ensure_engine()
                bs_w = float(fields["bull_side_blend_width"].get().strip())
                sd_w = float(fields["side_def_blend_width"].get().strip())
                bull_t = float(rg.get("vix_bull_threshold", 18.0))
                def_t = float(rg.get("vix_defensive_threshold", 30.0))
                self._show_blend_chart(bull_t, def_t, bs_w, sd_w)
            except Exception as e:
                print(f"[T20] Chart error: {e}")

        def _run_sweep():
            _save_config()
            popup.destroy()
            self._t17_lab_sweep()

        tk.Button(act_fr, text="Refresh Status", command=_refresh_status,
                  bg="#89b4fa", fg="#1e1e2e", activebackground="#74c7ec", **btn_s
                  ).pack(side=tk.LEFT, padx=4)
        tk.Button(act_fr, text="Preview Blend Zones", command=_preview_chart,
                  bg="#cba6f7", fg="#1e1e2e", activebackground="#b4befe", **btn_s
                  ).pack(side=tk.LEFT, padx=4)
        tk.Button(act_fr, text="Save to Config", command=_save_config,
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5", **btn_s
                  ).pack(side=tk.LEFT, padx=4)

        act_fr2 = tk.Frame(popup, bg="#1e1e2e")
        act_fr2.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Button(act_fr2, text="Run Blend Lab Sweep (T17)", command=_run_sweep,
                  bg="#f9e2af", fg="#1e1e2e", activebackground="#fab387",
                  font=("Helvetica", 11, "bold"), bd=0, cursor="hand2", width=30
                  ).pack(side=tk.LEFT, padx=4)

        # ── Info ──
        info = tk.Label(popup,
                        text=("Blend OFF = 기존 step-function regime (현재 live 설정)\n"
                              "Blend ON  = hysteresis + soft weight interpolation\n"
                              "검증: T17 Lab Sweep → Blend arm set 선택 → 기존 대비 비교"),
                        font=("Helvetica", 10), bg="#1e1e2e", fg="#6c7086",
                        justify=tk.LEFT, anchor=tk.W)
        info.pack(fill=tk.X, padx=20, pady=(0, 6))

        _refresh_status()

    def _show_blend_chart(self, bull_t, def_t, bs_w, sd_w):
        """Show a matplotlib chart of blend zones with current VIX position."""
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import numpy as _np
        from regime_blend import compute_blend_alphas

        vix_range = _np.linspace(10, 42, 500)
        a_bull, a_side, a_def = [], [], []
        for v in vix_range:
            ab, as_, ad = compute_blend_alphas(v, bull_t, def_t, bs_w, sd_w)
            a_bull.append(ab)
            a_side.append(as_)
            a_def.append(ad)

        current_vix = None
        try:
            from daily_runner import get_current_vix
            current_vix, _, _ = get_current_vix(_cfg)
        except Exception:
            pass

        chart_popup = tk.Toplevel(self)
        chart_popup.title("Regime Blend Zone Visualization")
        chart_popup.geometry("820x520")
        chart_popup.configure(bg="#1e1e2e")

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), height_ratios=[3, 1],
                                        facecolor="#1e1e2e")

        ax1.set_facecolor("#11111b")
        ax1.fill_between(vix_range, a_bull, alpha=0.7, color="#a6e3a1", label="BULL α")
        ax1.fill_between(vix_range, a_side, alpha=0.7, color="#89b4fa", label="SIDE α")
        ax1.fill_between(vix_range, a_def, alpha=0.7, color="#f38ba8", label="DEF α")
        ax1.set_ylabel("Blend Alpha", color="#cdd6f4")
        ax1.set_title("Regime Blend Zones", color="#cdd6f4", fontsize=13, fontweight="bold")
        ax1.legend(loc="upper right", facecolor="#313244", edgecolor="#45475a",
                   labelcolor="#cdd6f4")
        ax1.set_xlim(10, 42)
        ax1.set_ylim(0, 1.05)

        for t, c, lbl in [(bull_t, "#a6e3a1", f"BULL={bull_t:.0f}"),
                           (def_t, "#f38ba8", f"DEF={def_t:.0f}")]:
            ax1.axvline(t, color=c, linestyle="--", alpha=0.6, linewidth=1)
            ax1.text(t, 1.02, lbl, ha="center", fontsize=8, color=c)

        for lo, hi, c in [(bull_t - bs_w, bull_t + bs_w, "#f9e2af"),
                          (def_t - sd_w, def_t + sd_w, "#f9e2af")]:
            ax1.axvspan(lo, hi, alpha=0.08, color=c)

        if current_vix is not None:
            ax1.axvline(current_vix, color="#fab387", linewidth=2, linestyle="-")
            ax1.text(current_vix, 0.5, f"  VIX={current_vix:.1f}",
                     fontsize=9, color="#fab387", fontweight="bold",
                     transform=ax1.get_xaxis_transform())

        ax1.tick_params(colors="#9399b2")
        ax1.grid(True, alpha=0.15, color="#45475a")
        for spine in ax1.spines.values():
            spine.set_color("#45475a")

        # Bottom panel: step function comparison
        regime_step = []
        for v in vix_range:
            if v < bull_t:
                regime_step.append(1.0)
            elif v >= def_t:
                regime_step.append(-1.0)
            else:
                regime_step.append(0.0)

        ax2.set_facecolor("#11111b")
        ax2.plot(vix_range, regime_step, color="#6c7086", linewidth=1.5,
                 linestyle="--", label="Step (old)", alpha=0.8)
        dominant = [ab - ad for ab, ad in zip(a_bull, a_def)]
        ax2.plot(vix_range, dominant, color="#cba6f7", linewidth=2,
                 label="Blend (new)", alpha=0.9)
        if current_vix is not None:
            ax2.axvline(current_vix, color="#fab387", linewidth=2)
        ax2.set_xlabel("VIX", color="#cdd6f4")
        ax2.set_ylabel("BULL←  →DEF", color="#cdd6f4", fontsize=9)
        ax2.legend(loc="upper right", facecolor="#313244", edgecolor="#45475a",
                   labelcolor="#cdd6f4", fontsize=8)
        ax2.set_xlim(10, 42)
        ax2.set_ylim(-1.3, 1.3)
        ax2.tick_params(colors="#9399b2")
        ax2.grid(True, alpha=0.15, color="#45475a")
        for spine in ax2.spines.values():
            spine.set_color("#45475a")

        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=chart_popup)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        def _on_close():
            plt.close(fig)
            chart_popup.destroy()
        chart_popup.protocol("WM_DELETE_WINDOW", _on_close)


    # ── T23: Compose Backtest (Path C — regime-routed signals) ──
    def _t23_compose_backtest(self):
        popup = tk.Toplevel(self)
        popup.title("T23: Compose Backtest (Regime-Routed Signals)")
        popup.geometry("720x620")
        popup.configure(bg="#1e1e2e")

        tk.Label(popup, text="Compose Backtest — Path C",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(12, 4))
        tk.Label(popup,
                 text=("각 regime별로 다른 frozen signal을 사용해서 backtest.\n"
                       "Live 운영에는 영향 없음 (simulator only)."),
                 font=("Helvetica", 10), bg="#1e1e2e", fg="#6c7086",
                 justify=tk.LEFT).pack(pady=(0, 6))

        import yaml
        conf_path = str(_THIS_DIR / "config.yaml")
        with open(conf_path) as f:
            conf = yaml.safe_load(f)
        default_sig = conf["paths"]["frozen_signal"]
        rsp = (conf.get("paths", {}) or {}).get("regime_signal_paths", {}) or {}

        # ── Regime signal path fields ──
        sig_fr = tk.LabelFrame(popup, text=" Regime Signal Paths (blank = fallback to default) ",
                               font=("Helvetica", 11, "bold"),
                               fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        sig_fr.pack(fill=tk.X, padx=16, pady=6)

        entry_style = dict(font=("Menlo", 10), bg="#313244", fg="#cdd6f4",
                           insertbackground="#cdd6f4", relief=tk.FLAT)
        lbl_style = dict(font=("Helvetica", 11), bg="#1e1e2e", fg="#bac2de", anchor=tk.W)

        tk.Label(sig_fr, text=f"Default: {os.path.basename(default_sig)}",
                 font=("Menlo", 9), bg="#1e1e2e", fg="#6c7086",
                 anchor=tk.W).pack(fill=tk.X, padx=8, pady=(4, 2))

        regime_entries = {}
        for rg in ("BULL", "SIDE", "DEFENSIVE"):
            row = tk.Frame(sig_fr, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(row, text=rg, width=10, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, str(rsp.get(rg) or ""))
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

            def _browse(entry=e, regime=rg):
                from tkinter import filedialog
                init_dir = conf["paths"].get("output_dir", os.path.expanduser("~"))
                p = filedialog.askopenfilename(
                    title=f"Select {regime} signal",
                    initialdir=init_dir,
                    filetypes=[("NPZ", "*.npz"), ("All", "*.*")],
                )
                if p:
                    entry.delete(0, tk.END)
                    entry.insert(0, p)

            tk.Button(row, text="…", command=_browse,
                      bg="#45475a", fg="#cdd6f4", bd=0, cursor="hand2",
                      font=("Helvetica", 9), width=3).pack(side=tk.LEFT, padx=4)
            regime_entries[rg] = e

        # ── Backtest settings ──
        bt_fr = tk.LabelFrame(popup, text=" Backtest Settings ",
                              font=("Helvetica", 11, "bold"),
                              fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        bt_fr.pack(fill=tk.X, padx=16, pady=6)

        fields = {}
        for label, default in [
            ("Start Date", "2017-01-03"),
            ("End Date", datetime.now().strftime("%Y-%m-%d")),
            ("Initial Capital ($)", "100000"),
            ("Daily Buy Limit ($)", "1000"),
            ("Commission (bps)", "10"),
            ("Slippage (bps)", "5"),
        ]:
            row = tk.Frame(bt_fr, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(row, text=label, width=22, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, default)
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
            fields[label] = e

        mode_var = tk.StringVar(value="event_driven")
        mode_frame = tk.Frame(bt_fr, bg="#1e1e2e")
        mode_frame.pack(fill=tk.X, padx=8, pady=4)
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

            regime_paths = {
                rg: (regime_entries[rg].get().strip() or None)
                for rg in ("BULL", "SIDE", "DEFENSIVE")
            }
            popup.destroy()

            def _run():
                import importlib
                import simulator
                import daily_runner
                importlib.reload(simulator)
                importlib.reload(daily_runner)

                self._ensure_engine()
                with open(conf_path) as f:
                    conf_local = yaml.safe_load(f)
                conf_local.setdefault("regime_compose", {})["enabled"] = True
                conf_local.setdefault("paths", {})["regime_signal_paths"] = regime_paths

                print("[T23] Compose Backtest")
                print(f"  Period : {start_d} ~ {end_d}  Capital=${capital:,.0f}")
                print(f"  Mode   : {mode}  Commission={comm}bps  Slippage={slip}bps")
                for rg in ("BULL", "SIDE", "DEFENSIVE"):
                    p = regime_paths[rg]
                    tag = os.path.basename(p) if p else "(default)"
                    print(f"  {rg:10s} → {tag}")
                print()

                from engine_loader import engine
                import dataclasses

                signal = daily_runner.load_composed_signal(conf_local)
                print(f"  {daily_runner.describe_signal(signal)}")

                print("\n[Step 1/3] Building data pack...")
                cfg = engine.Config()
                for k, v in conf_local.get("regime", {}).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, type(getattr(cfg, k))(v))
                cfg.start_panel_date = datetime.strptime(start_d, "%Y-%m-%d")
                cfg.end_date = datetime.strptime(end_d, "%Y-%m-%d")
                cfg.enable_historical_universe = True
                cfg.historical_universe_expand_tickers = True
                cfg.enable_coverage_based_universe = True
                cfg.fmp_cache_root = conf_local["paths"]["fmp_cache_root"]

                prep = engine.prepare_inputs(cfg)
                pack = prep["pack"] if isinstance(prep, dict) and "pack" in prep else prep
                print(f"  Pack ready: {len(pack['tickers'])} tickers, {len(pack['dates'])} dates")

                print("\n[Step 2/3] Building VIX regime timeseries...")
                from datetime import timedelta as _td
                vix_df = engine.build_vix_regime_timeseries(
                    cfg,
                    datetime.strptime(start_d, "%Y-%m-%d") - _td(days=60),
                    datetime.strptime(end_d, "%Y-%m-%d"),
                )
                vix_close_map, vix_regime_map, vix_smooth_map = {}, {}, {}
                if vix_df is not None and not vix_df.empty:
                    for _, row in vix_df.iterrows():
                        d_str = str(row.get("date", row.name))[:10]
                        vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
                        vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
                        if "vix_smooth" in row.index:
                            vix_smooth_map[d_str] = float(row["vix_smooth"])
                print(f"  VIX data: {len(vix_close_map)} dates")

                print(f"\n[Step 3/3] Running simulation ({mode}, compose ON)...")
                res = simulator.run_simulation(
                    engine=engine,
                    cfg=cfg,
                    pack=pack,
                    signal=signal,
                    vix_close_by_date=vix_close_map,
                    vix_regime_by_date=vix_regime_map,
                    initial_capital=capital,
                    daily_buy_limit=buy_limit,
                    strategy_conf=conf_local.get("strategy", {}),
                    trigger_conf=conf_local.get("triggers", {}),
                    rebalance_mode=mode,
                    commission_bps=comm,
                    slippage_bps=slip,
                    start_date=start_d,
                    end_date=end_d,
                    progress_fn=lambda c, t, m: print(m),
                    blend_conf={"regime_blend_enabled": False},
                    vix_smooth_by_date=vix_smooth_map,
                )

                print()
                print(simulator.format_report(res))

                m = res["metrics"]
                rb = m.get("regime_breakdown", {})
                print("\n[Regime Breakdown]")
                print(f"  {'Regime':10s} {'Days':>5s} {'Ann%':>7s} {'Sharpe':>7s} "
                      f"{'MDD%':>6s} {'Calmar':>7s} {'Win%':>6s}")
                for rg in ("BULL", "SIDE", "DEF"):
                    r = rb.get(rg, {})
                    print(f"  {rg:10s} {r.get('Days',0):5d} "
                          f"{r.get('AnnRet',0)*100:+6.2f}% "
                          f"{r.get('Sharpe',0):7.3f} "
                          f"{r.get('MDD',0)*100:5.1f}% "
                          f"{r.get('Calmar',0):7.3f} "
                          f"{r.get('WinRate',0)*100:5.1f}%")

                out_dir = conf_local["paths"]["output_dir"]
                ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
                xlsx_path = os.path.join(out_dir, f"compose_backtest_{ts_tag}.xlsx")
                self._save_compose_excel(xlsx_path, res, signal, {
                    "start_date": start_d, "end_date": end_d,
                    "capital": capital, "buy_limit": buy_limit,
                    "mode": mode, "comm": comm, "slip": slip,
                })
                print(f"\n  [T23] Report saved: {xlsx_path}")

                self._show_sim_chart(res["daily_ts"])

            self._run_task("T23: Compose Backtest", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="  Run Compose Backtest  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2", command=_start).pack()

    # ── T24: Compose vs Baseline A/B ──
    def _t24_compose_vs_baseline(self):
        popup = tk.Toplevel(self)
        popup.title("T24: Compose vs Baseline A/B")
        popup.geometry("720x640")
        popup.configure(bg="#1e1e2e")

        tk.Label(popup, text="Compose vs Baseline — A/B",
                 font=("Helvetica", 14, "bold"), bg="#1e1e2e", fg="#cdd6f4"
                 ).pack(pady=(12, 4))
        tk.Label(popup,
                 text=("동일한 pack/VIX/strategy에서 단일 signal(baseline)과\n"
                       "regime-composed signal을 나란히 돌려 비교."),
                 font=("Helvetica", 10), bg="#1e1e2e", fg="#6c7086",
                 justify=tk.LEFT).pack(pady=(0, 6))

        import yaml
        conf_path = str(_THIS_DIR / "config.yaml")
        with open(conf_path) as f:
            conf = yaml.safe_load(f)
        default_sig = conf["paths"]["frozen_signal"]
        rsp = (conf.get("paths", {}) or {}).get("regime_signal_paths", {}) or {}

        sig_fr = tk.LabelFrame(popup, text=" Regime Signal Paths (blank = fallback to default) ",
                               font=("Helvetica", 11, "bold"),
                               fg="#89b4fa", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        sig_fr.pack(fill=tk.X, padx=16, pady=6)

        entry_style = dict(font=("Menlo", 10), bg="#313244", fg="#cdd6f4",
                           insertbackground="#cdd6f4", relief=tk.FLAT)
        lbl_style = dict(font=("Helvetica", 11), bg="#1e1e2e", fg="#bac2de", anchor=tk.W)

        tk.Label(sig_fr, text=f"Default: {os.path.basename(default_sig)}",
                 font=("Menlo", 9), bg="#1e1e2e", fg="#6c7086",
                 anchor=tk.W).pack(fill=tk.X, padx=8, pady=(4, 2))

        regime_entries = {}
        for rg in ("BULL", "SIDE", "DEFENSIVE"):
            row = tk.Frame(sig_fr, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(row, text=rg, width=10, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, str(rsp.get(rg) or ""))
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

            def _browse(entry=e, regime=rg):
                from tkinter import filedialog
                init_dir = conf["paths"].get("output_dir", os.path.expanduser("~"))
                p = filedialog.askopenfilename(
                    title=f"Select {regime} signal",
                    initialdir=init_dir,
                    filetypes=[("NPZ", "*.npz"), ("All", "*.*")],
                )
                if p:
                    entry.delete(0, tk.END)
                    entry.insert(0, p)

            tk.Button(row, text="…", command=_browse,
                      bg="#45475a", fg="#cdd6f4", bd=0, cursor="hand2",
                      font=("Helvetica", 9), width=3).pack(side=tk.LEFT, padx=4)
            regime_entries[rg] = e

        bt_fr = tk.LabelFrame(popup, text=" Backtest Settings ",
                              font=("Helvetica", 11, "bold"),
                              fg="#f9e2af", bg="#1e1e2e", bd=1, relief=tk.GROOVE)
        bt_fr.pack(fill=tk.X, padx=16, pady=6)

        fields = {}
        for label, default in [
            ("Start Date", "2017-01-03"),
            ("End Date", datetime.now().strftime("%Y-%m-%d")),
            ("Initial Capital ($)", "100000"),
            ("Daily Buy Limit ($)", "1000"),
            ("Commission (bps)", "10"),
            ("Slippage (bps)", "5"),
        ]:
            row = tk.Frame(bt_fr, bg="#1e1e2e")
            row.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(row, text=label, width=22, **lbl_style).pack(side=tk.LEFT)
            e = tk.Entry(row, **entry_style)
            e.insert(0, default)
            e.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
            fields[label] = e

        mode_var = tk.StringVar(value="event_driven")
        mode_frame = tk.Frame(bt_fr, bg="#1e1e2e")
        mode_frame.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(mode_frame, text="Rebalance Mode", width=22, **lbl_style
                 ).pack(side=tk.LEFT)
        for text, val in [("Event-Driven", "event_driven"), ("Daily", "daily")]:
            tk.Radiobutton(mode_frame, text=text, variable=mode_var, value=val,
                           bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                           activebackground="#1e1e2e", activeforeground="#cdd6f4",
                           font=("Helvetica", 10)).pack(side=tk.LEFT, padx=8)

        def _start():
            start_d = fields["Start Date"].get().strip()
            end_d = fields["End Date"].get().strip()
            capital = float(fields["Initial Capital ($)"].get().strip())
            buy_limit = float(fields["Daily Buy Limit ($)"].get().strip())
            comm = float(fields["Commission (bps)"].get().strip())
            slip = float(fields["Slippage (bps)"].get().strip())
            mode = mode_var.get()

            regime_paths = {
                rg: (regime_entries[rg].get().strip() or None)
                for rg in ("BULL", "SIDE", "DEFENSIVE")
            }
            popup.destroy()

            def _run():
                import importlib
                import simulator
                import daily_runner
                importlib.reload(simulator)
                importlib.reload(daily_runner)

                self._ensure_engine()
                with open(conf_path) as f:
                    conf_local = yaml.safe_load(f)

                print("[T24] Compose vs Baseline A/B")
                print(f"  Period : {start_d} ~ {end_d}  Capital=${capital:,.0f}")
                print(f"  Mode   : {mode}")
                print(f"  Default: {os.path.basename(default_sig)}")
                for rg in ("BULL", "SIDE", "DEFENSIVE"):
                    p = regime_paths[rg]
                    tag = os.path.basename(p) if p else "(default)"
                    print(f"  {rg:10s} → {tag}")
                print()

                from engine_loader import engine

                baseline_sig = daily_runner.load_frozen_signal(default_sig)

                conf_c = dict(conf_local)
                conf_c["regime_compose"] = {"enabled": True}
                conf_c.setdefault("paths", {})["regime_signal_paths"] = regime_paths
                compose_sig = daily_runner.load_composed_signal(conf_c)
                print(f"  {daily_runner.describe_signal(compose_sig)}")

                print("\n[Step 1/4] Building data pack...")
                cfg = engine.Config()
                for k, v in conf_local.get("regime", {}).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, type(getattr(cfg, k))(v))
                cfg.start_panel_date = datetime.strptime(start_d, "%Y-%m-%d")
                cfg.end_date = datetime.strptime(end_d, "%Y-%m-%d")
                cfg.enable_historical_universe = True
                cfg.historical_universe_expand_tickers = True
                cfg.enable_coverage_based_universe = True
                cfg.fmp_cache_root = conf_local["paths"]["fmp_cache_root"]

                prep = engine.prepare_inputs(cfg)
                pack = prep["pack"] if isinstance(prep, dict) and "pack" in prep else prep
                print(f"  Pack ready: {len(pack['tickers'])} tickers, {len(pack['dates'])} dates")

                print("\n[Step 2/4] Building VIX regime timeseries...")
                from datetime import timedelta as _td
                vix_df = engine.build_vix_regime_timeseries(
                    cfg,
                    datetime.strptime(start_d, "%Y-%m-%d") - _td(days=60),
                    datetime.strptime(end_d, "%Y-%m-%d"),
                )
                vix_close_map, vix_regime_map, vix_smooth_map = {}, {}, {}
                if vix_df is not None and not vix_df.empty:
                    for _, row in vix_df.iterrows():
                        d_str = str(row.get("date", row.name))[:10]
                        vix_close_map[d_str] = float(row.get("close", row.get("vix_close", 20)))
                        vix_regime_map[d_str] = str(row.get("regime", "SIDE"))
                        if "vix_smooth" in row.index:
                            vix_smooth_map[d_str] = float(row["vix_smooth"])

                common = dict(
                    engine=engine, cfg=cfg, pack=pack,
                    vix_close_by_date=vix_close_map,
                    vix_regime_by_date=vix_regime_map,
                    initial_capital=capital,
                    daily_buy_limit=buy_limit,
                    strategy_conf=conf_local.get("strategy", {}),
                    trigger_conf=conf_local.get("triggers", {}),
                    rebalance_mode=mode,
                    commission_bps=comm, slippage_bps=slip,
                    start_date=start_d, end_date=end_d,
                    progress_fn=lambda c, t, m: None,
                    blend_conf={"regime_blend_enabled": False},
                    vix_smooth_by_date=vix_smooth_map,
                )

                print(f"\n[Step 3/4] Running Baseline (single signal)...")
                import time as _time
                t0 = _time.time()
                res_base = simulator.run_simulation(signal=baseline_sig, **common)
                print(f"  done in {_time.time()-t0:.1f}s")

                print(f"\n[Step 4/4] Running Composed...")
                t0 = _time.time()
                res_comp = simulator.run_simulation(signal=compose_sig, **common)
                print(f"  done in {_time.time()-t0:.1f}s")

                self._print_ab_report(res_base, res_comp)

                out_dir = conf_local["paths"]["output_dir"]
                ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
                xlsx_path = os.path.join(out_dir, f"compose_ab_{ts_tag}.xlsx")
                self._save_ab_excel(xlsx_path, res_base, res_comp, compose_sig, {
                    "start_date": start_d, "end_date": end_d,
                    "capital": capital, "buy_limit": buy_limit,
                    "mode": mode, "comm": comm, "slip": slip,
                })
                print(f"\n  [T24] A/B report saved: {xlsx_path}")

            self._run_task("T24: Compose vs Baseline", _run)

        btn_frame = tk.Frame(popup, bg="#1e1e2e")
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="  Run A/B  ",
                  font=("Helvetica", 12, "bold"),
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  bd=0, cursor="hand2", command=_start).pack()

    # ── A/B helper: console table ──
    def _print_ab_report(self, res_base, res_comp):
        mb = res_base["metrics"]; mc = res_comp["metrics"]
        print()
        print("=" * 80)
        print(" Compose vs Baseline — Overall")
        print("=" * 80)
        print(f"  {'Metric':<22s} {'Baseline':>15s} {'Compose':>15s} {'Δ':>12s}")
        print(f"  {'-'*22} {'-'*15} {'-'*15} {'-'*12}")
        rows = [
            ("CAGR %",          mb.get("CAGR",0)*100,         mc.get("CAGR",0)*100,         "%"),
            ("Sharpe",          mb.get("Net_Sharpe",0),       mc.get("Net_Sharpe",0),       ""),
            ("Max DD %",        mb.get("Max_Drawdown",0)*100, mc.get("Max_Drawdown",0)*100, "%"),
            ("Calmar",          mb.get("Calmar_Ratio",0),     mc.get("Calmar_Ratio",0),     ""),
            ("Total Return %",  mb.get("Total_Return",0)*100, mc.get("Total_Return",0)*100, "%"),
            ("Final $",         mb.get("Final_Value",0),      mc.get("Final_Value",0),      "$"),
            ("Commission $",    mb.get("Total_Commission",0), mc.get("Total_Commission",0), "$"),
        ]
        for name, b, c, unit in rows:
            d = c - b
            sign = "+" if d >= 0 else ""
            if unit == "$":
                print(f"  {name:<22s} {b:>15,.0f} {c:>15,.0f} {sign}{d:>11,.0f}")
            elif unit == "%":
                print(f"  {name:<22s} {b:>14.2f}% {c:>14.2f}% {sign}{d:>10.2f}%")
            else:
                print(f"  {name:<22s} {b:>15.3f} {c:>15.3f} {sign}{d:>11.3f}")

        print()
        print("=" * 80)
        print(" Compose vs Baseline — Regime Breakdown")
        print("=" * 80)
        rbb = mb.get("regime_breakdown", {})
        rbc = mc.get("regime_breakdown", {})
        print(f"  {'Regime':<8s} {'Days':>5s}  "
              f"{'BaseAnn%':>10s} {'CompAnn%':>10s} {'ΔAnn%':>8s}  "
              f"{'BaseShp':>8s} {'CompShp':>8s} {'ΔShp':>7s}  "
              f"{'BaseMDD':>8s} {'CompMDD':>8s}")
        for rg in ("BULL", "SIDE", "DEF"):
            rb = rbb.get(rg, {}); rc = rbc.get(rg, {})
            da = (rc.get("AnnRet",0) - rb.get("AnnRet",0)) * 100
            ds = rc.get("Sharpe",0) - rb.get("Sharpe",0)
            print(f"  {rg:<8s} {rb.get('Days',0):>5d}  "
                  f"{rb.get('AnnRet',0)*100:>9.2f}% {rc.get('AnnRet',0)*100:>9.2f}% "
                  f"{'+' if da>=0 else ''}{da:>7.2f}%  "
                  f"{rb.get('Sharpe',0):>8.3f} {rc.get('Sharpe',0):>8.3f} "
                  f"{'+' if ds>=0 else ''}{ds:>6.3f}  "
                  f"{rb.get('MDD',0)*100:>7.2f}% {rc.get('MDD',0)*100:>7.2f}%")
        print("=" * 80)

    # ── Excel writers for T23/T24 ──
    def _save_compose_excel(self, path, result, signal, cfg_dict):
        import pandas as _pd
        m = result["metrics"]
        rb = m.get("regime_breakdown", {})
        with _pd.ExcelWriter(path, engine="openpyxl") as w:
            overall = _pd.DataFrame([{
                "CAGR %":           round(m.get("CAGR", 0) * 100, 2),
                "Net Sharpe":       round(m.get("Net_Sharpe", 0), 4),
                "Max DD %":         round(m.get("Max_Drawdown", 0) * 100, 2),
                "Calmar":           round(m.get("Calmar_Ratio", 0), 4),
                "Total Return %":   round(m.get("Total_Return", 0) * 100, 2),
                "Daily Win %":      round(m.get("Daily_Win_Rate", 0) * 100, 2),
                "Monthly Win %":    round(m.get("Monthly_Win_Rate", 0) * 100, 2),
                "Start":            m.get("Start_Date", ""),
                "End":              m.get("End_Date", ""),
                "Years":            m.get("Years", 0),
                "Initial $":        m.get("Initial_Capital", 0),
                "Final $":          m.get("Final_Value", 0),
                "Commission $":     round(m.get("Total_Commission", 0), 2),
                "Rebal Days":       m.get("Rebalance_Days", 0),
            }]).T.rename(columns={0: "Value"})
            overall.to_excel(w, sheet_name="Overall")

            rb_rows = []
            for rg in ("BULL", "SIDE", "DEF"):
                r = rb.get(rg, {})
                rb_rows.append({
                    "Regime":    rg,
                    "Days":      r.get("Days", 0),
                    "MaxStreak": r.get("MaxStreak", 0),
                    "Ann %":     round(r.get("AnnRet", 0) * 100, 2),
                    "Sharpe":    round(r.get("Sharpe", 0), 3),
                    "MDD %":     round(r.get("MDD", 0) * 100, 2),
                    "Calmar":    round(r.get("Calmar", 0), 3),
                    "Win %":     round(r.get("WinRate", 0) * 100, 2),
                })
            _pd.DataFrame(rb_rows).to_excel(w, sheet_name="RegimeBreakdown", index=False)

            meta_rows = [("run_ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]
            for k, v in cfg_dict.items():
                meta_rows.append((k, str(v)))
            cm = result.get("compose_meta") or {}
            if cm:
                meta_rows.append(("compose", "true"))
                meta_rows.append(("default_signal", cm.get("default_path", "")))
                for rg, p in (cm.get("regime_paths") or {}).items():
                    meta_rows.append((f"{rg}_signal", p))
                for rg, k in (cm.get("regime_k") or {}).items():
                    meta_rows.append((f"{rg}_k", k))
            else:
                meta_rows.append(("compose", "false"))
                import numpy as _np
                mask = signal.get("mask")
                if mask is not None:
                    meta_rows.append(("k", int(_np.asarray(mask, dtype=bool).sum())))
            _pd.DataFrame(meta_rows, columns=["Key", "Value"]).to_excel(
                w, sheet_name="Meta", index=False)

            ts = result.get("daily_ts", _pd.DataFrame())
            if not ts.empty:
                ts.to_excel(w, sheet_name="DailyTS", index=False)
            tr = result.get("trades", _pd.DataFrame())
            if not tr.empty:
                tr.to_excel(w, sheet_name="Trades", index=False)

    def _save_ab_excel(self, path, res_base, res_comp, compose_sig, cfg_dict):
        import pandas as _pd
        mb = res_base["metrics"]; mc = res_comp["metrics"]
        rbb = mb.get("regime_breakdown", {})
        rbc = mc.get("regime_breakdown", {})

        with _pd.ExcelWriter(path, engine="openpyxl") as w:
            def _row(label, b, c, pct=False):
                d = c - b
                return {
                    "Metric": label,
                    "Baseline": round(b * 100, 4) if pct else round(b, 4),
                    "Compose":  round(c * 100, 4) if pct else round(c, 4),
                    "Delta":    round(d * 100, 4) if pct else round(d, 4),
                }
            ov = _pd.DataFrame([
                _row("CAGR %",          mb.get("CAGR",0),         mc.get("CAGR",0),         pct=True),
                _row("Sharpe",          mb.get("Net_Sharpe",0),   mc.get("Net_Sharpe",0)),
                _row("Max DD %",        mb.get("Max_Drawdown",0), mc.get("Max_Drawdown",0), pct=True),
                _row("Calmar",          mb.get("Calmar_Ratio",0), mc.get("Calmar_Ratio",0)),
                _row("Total Return %",  mb.get("Total_Return",0), mc.get("Total_Return",0), pct=True),
                _row("Daily Win %",     mb.get("Daily_Win_Rate",0),  mc.get("Daily_Win_Rate",0),  pct=True),
                _row("Monthly Win %",   mb.get("Monthly_Win_Rate",0),mc.get("Monthly_Win_Rate",0),pct=True),
                _row("Final $",         mb.get("Final_Value",0),     mc.get("Final_Value",0)),
                _row("Commission $",    mb.get("Total_Commission",0),mc.get("Total_Commission",0)),
            ])
            ov.to_excel(w, sheet_name="Overall_AB", index=False)

            rb_rows = []
            for rg in ("BULL", "SIDE", "DEF"):
                rb = rbb.get(rg, {}); rc = rbc.get(rg, {})
                rb_rows.append({
                    "Regime": rg, "Days": rb.get("Days", 0),
                    "Base Ann %":  round(rb.get("AnnRet", 0) * 100, 2),
                    "Comp Ann %":  round(rc.get("AnnRet", 0) * 100, 2),
                    "Δ Ann %":     round((rc.get("AnnRet", 0) - rb.get("AnnRet", 0)) * 100, 2),
                    "Base Sharpe": round(rb.get("Sharpe", 0), 3),
                    "Comp Sharpe": round(rc.get("Sharpe", 0), 3),
                    "Δ Sharpe":    round(rc.get("Sharpe", 0) - rb.get("Sharpe", 0), 3),
                    "Base MDD %":  round(rb.get("MDD", 0) * 100, 2),
                    "Comp MDD %":  round(rc.get("MDD", 0) * 100, 2),
                    "Base Win %":  round(rb.get("WinRate", 0) * 100, 2),
                    "Comp Win %":  round(rc.get("WinRate", 0) * 100, 2),
                })
            _pd.DataFrame(rb_rows).to_excel(w, sheet_name="Regime_AB", index=False)

            meta = [("run_ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]
            for k, v in cfg_dict.items():
                meta.append((k, str(v)))
            cm = res_comp.get("compose_meta") or {}
            if cm:
                meta.append(("compose_default_signal", cm.get("default_path", "")))
                for rg, p in (cm.get("regime_paths") or {}).items():
                    meta.append((f"compose_{rg}_signal", p))
                for rg, kk in (cm.get("regime_k") or {}).items():
                    meta.append((f"compose_{rg}_k", kk))
            _pd.DataFrame(meta, columns=["Key", "Value"]).to_excel(
                w, sheet_name="Meta", index=False)

            tsb = res_base.get("daily_ts", _pd.DataFrame())
            tsc = res_comp.get("daily_ts", _pd.DataFrame())
            if not tsb.empty:
                tsb.to_excel(w, sheet_name="Baseline_TS", index=False)
            if not tsc.empty:
                tsc.to_excel(w, sheet_name="Compose_TS", index=False)


if __name__ == "__main__":
    app = App()
    app.mainloop()
