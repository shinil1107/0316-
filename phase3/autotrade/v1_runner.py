"""V1-E.2 / V1-F — Headless V1 runner.

What this is
------------

A ``python -m phase3.autotrade.v1_runner`` CLI that drives the same
pipeline the panel's "Full Paper Run" button drives, but with **no
Tk import and no operator interaction**. It is the target of two
``launchd`` agents:

* ``com.autotrade.v1.t7``    fires at 09:00 KST → ``t7-prefetch`` cmd
* ``com.autotrade.v1.daily`` fires at 22:35 KST → ``trade`` cmd

V1-F split rationale
--------------------

In V1-E we ran T7 inline with the 22:35 fire. 22:35 KST = 09:35 EDT
which is **5 minutes after US market open** — some prior-day closes
were still settling on FMP at that moment, so a manual T7 minutes
later produced *different recommendations*. Non-determinism in the
recommendation source is unacceptable. V1-F separates:

  09:00 KST T7 prefetch    — FMP closes stable for 5+ hours
                              by then. T7 sends its own email so the
                              operator can preview the day's picks
                              before market open and decide to arm.
  22:35 KST trade fire     — discovers that morning's T7 run_id on
                              disk, ``--skip-t7``s past T7, and runs
                              generate_intents + paper_submit + apply
                              + R11B trading digest. NEVER re-runs T7.

If the morning T7 fire failed (no run_dir for today) the trade fire
hard-fails with rc=2 and an explicit halt_reason — better than
silently using yesterday's stale recommendations.

Pipeline (trade subcommand)::

  0. require_armed_for_today          (V1-E gate; skip if --no-arm)
  1. env-gate validation              (KIS_ENV / SUBMIT / CANCEL / APPLY)
  2. discover today's T7 run_id       (V1-F; rc=2 + mail if absent)
  3. generate_intents (in-process)    → submitted_intents.json
  4. paper_submit + t10_apply         (one subprocess to
                                       phase3.autotrade.daily_runner)
  5. R11B EOD digest                  (trading-only; t7_payload=None)

Pipeline (t7-prefetch subcommand)::

  1. env-gate validation              (KIS_ENV only — no broker)
  2. T7 generate                      (own email via existing mailer)
  3. status.json write                (panel reads for "Last fire")

Subcommands
-----------

* ``arm-today``    — write today's KST arm token; the daily green-light
* ``status``       — show last arm token + last run dir + env-gate state
* ``t7-prefetch``  — V1-F: 09:00 fire; T7 only, sends own email
* ``trade``        — V1-F: 22:35 fire; reuses morning T7 run_id
* ``run``          — legacy entry point (T7 + trade in one); kept for
                     operator manual use and the panel test-fire

Safety pins
-----------

* **Default-safe**: `run` without `--no-arm` REQUIRES today's arm
  token to exist. Forgetting to arm = no-op exit rc=0 with a
  one-line log. This is what makes the unattended path safe by
  default; missing token → skipped trading day.
* **No Tk import**: this module deliberately does not touch
  control_panel; importing Tk would refuse to come up on a headless
  Mac running launchd without a window server session.
* **No silent env arming**: this module reads env gates but never
  sets them. The launchd plist is where the gates get exported,
  so the audit trail is one file (the plist) rather than a hidden
  ``os.environ[...] = 'true'`` somewhere in code.
* **rc=0 on safe-skip**: when the arm gate fails, exit rc=0. launchd
  retries only on non-zero exits; we want "no token = skip the day"
  to be a quiet success from the daemon's perspective.
* **rc != 0 only on actionable failures**: T7 crash, env gate off,
  generate_intents producing zero rows, subprocess crash. Each of
  these gets surfaced via stdout AND, when possible, the R11B mail.

CLI examples
------------

::

    # Operator's morning: green-light tonight's trading session.
    python -m phase3.autotrade.v1_runner arm-today --note "ok"

    # What launchd actually invokes at 22:25 KST.
    python -m phase3.autotrade.v1_runner run

    # Ad-hoc dry-run with operator-typed run_id (no T7).
    python -m phase3.autotrade.v1_runner run \\
        --skip-t7 --run-id 20260526_223714_daily --no-arm

    # Status check from a terminal.
    python -m phase3.autotrade.v1_runner status
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Mapping, Optional, Sequence,
)


_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import auto_halt        # noqa: E402
from phase3.autotrade import global_halt      # noqa: E402
from phase3.autotrade import intents_io       # noqa: E402
from phase3.autotrade import smtp_mailer      # noqa: E402
from phase3.autotrade import t7_runner        # noqa: E402
from phase3.autotrade import trading_calendar  # noqa: E402
from phase3.autotrade import v1_arm           # noqa: E402
from phase3.autotrade import v1_status        # noqa: E402


_DOTENV_PATH = _REPO_ROOT / ".env"

# V1-F.2 — conventional launchd StandardOutPath per fire. The panel
# tails one of these files while a fire is in_progress so the operator
# can watch cache-download / T7 progress live, just like the in-process
# launcher.py T7 button does via sys.stdout capture. Must stay in sync
# with the ``StandardOutPath`` keys in the plist templates.
_RUNTIME_DIR = _HERE / "runtime"
LAUNCHD_LOG_BY_FIRE = {
    "t7_prefetch": _RUNTIME_DIR / "v1_t7_launchd.out.log",
    "trade":       _RUNTIME_DIR / "v1_launchd.out.log",
}


def _launchd_log_for(fire_label: str) -> Optional[Path]:
    """Return the launchd StandardOutPath the panel should tail for
    a given fire_label, or ``None`` for fires whose stdout the panel
    already streams (e.g. ``test-fire`` / legacy ``run`` are launched
    by the panel itself via Popen — no log file involved).
    """
    return LAUNCHD_LOG_BY_FIRE.get(fire_label)


def _hydrate_env_from_dotenv(
    *,
    dotenv_path: Path = _DOTENV_PATH,
    target: Optional[Any] = None,
) -> List[str]:
    """Merge ``.env`` keys into ``os.environ`` (or ``target``).

    Rationale
    ---------
    launchd's ``gui/$UID`` session does NOT inherit env vars from the
    operator's ``~/.zshrc``. After ``launchctl bootstrap``, the agent
    runs with only what the plist's ``EnvironmentVariables`` dict
    explicitly declares. That dict intentionally **does not** carry
    secrets (``FMP_API_KEY``, ``GMAIL_APP_PASSWORD``, KIS creds) so
    the plist remains safe to read via ``launchctl print``.

    The contract we honour instead:

      * KIS credentials live in ``./.env`` (already enforced by
        ``kis_broker_adapter._read_dotenv``).
      * Every other launchd-required secret SHOULD also live in
        ``./.env``. This loader merges those into ``os.environ`` at
        the **start** of ``v1_runner.main`` so downstream modules
        that only read ``os.environ`` (``benchmarks.py``,
        ``backfill_legacy_financials.py``, T7 inside the
        ``daily_runner`` subprocess via its inherited env, etc.)
        see the keys uniformly whether the run was started by
        launchd or by an operator from their shell.

    Precedence: ``os.environ`` wins over ``.env``. Operators who
    export a key in their shell to test an override won't have it
    silently overwritten by a stale ``.env``.

    Returns
    -------
    List of keys that were ADDED (i.e. not already present in the
    target). Useful for the bootstrap log.
    """
    env_target = target if target is not None else os.environ
    if not dotenv_path.exists():
        return []
    added: List[str] = []
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and key not in env_target:
            env_target[key] = value
            added.append(key)
    return added


# Env-gate constants — must match what
# ``phase3.autotrade.daily_runner.main`` enforces. We re-declare them
# here so the headless CLI does not have to import the heavy daily
# runner module just to read three string constants.
KIS_ENV_VAR = "KIS_ENV"
# NOTE: these constants MUST match ``phase3.autotrade.daily_runner``'s
# ``SUBMIT_ENV_GATE`` / ``CANCEL_ENV_GATE`` / ``APPLY_ENV_GATE`` —
# the actual gate names ``daily_runner.main`` enforces on
# ``os.environ``. Importing daily_runner just for three strings
# would drag in pandas + the engine; we redeclare and lock the
# parity via ``test_v1e_env_gate_names_match_daily_runner``.
#
# Historical trap: V1-E v0 typed ``KIS_T10_APPLY_OK`` (plausible
# but wrong) → the panel's check_button_gates lit green (because the
# panel reads from the same wrong constant!), the test-fire passed
# all UI gates, T7 ran, generate_intents ran, then daily_runner
# halted with rc=2 ``--apply-t10 requires AUTOTRADE_T10_APPLY_OK=true``.
SUBMIT_ENV_GATE = "KIS_PAPER_SUBMIT_OK"
CANCEL_ENV_GATE = "KIS_PAPER_CANCEL_OK"
APPLY_ENV_GATE  = "AUTOTRADE_T10_APPLY_OK"
# V1-G — lifts the broker-layer ``buy_only_mode`` safety pin in
# autotrade/daily_runner.default_manage_loop_fn so STOP_LOSS / SELL /
# TRIM intents can reach the KIS paper broker. Treated the same way
# as the SUBMIT/CANCEL/APPLY gates above: explicit env value
# documents intent, default-off keeps legacy / manual flows
# (paper_buy.py, single-intent execute) conservative.
ALLOW_SELL_ENV_GATE = "KIS_ALLOW_SELL"

# V1-I — live-quote pricing + gap filter env keys (read in ``main`` for
# the ``run`` / ``trade`` subcommands so the plist EnvironmentVariables
# drive the unattended fire, matching the V1-H AUTOTRADE_* pattern).
USE_QUOTE_ENV = "AUTOTRADE_USE_QUOTE"
QUOTE_ONLY_ENV = "AUTOTRADE_QUOTE_ONLY"
BUY_QUOTE_PAD_ENV = "AUTOTRADE_BUY_QUOTE_PAD_PCT"
GAP_FILTER_ENV = "AUTOTRADE_GAP_FILTER_MAX_PCT"


# ``LAUNCHD_ENV`` is the SINGLE source of truth for the env vars the
# unattended launchd fire injects on top of the operator's shell env.
# Three downstream contracts must stay in sync with this dict:
#
#   1. ``phase3/launchd/com.autotrade.v1.daily.plist.template``
#      hand-edits the same keys → ``test_v1e_plist`` asserts equality.
#   2. ``control_panel._on_v1e_test_fire`` injects this dict so the
#      panel "Test-fire" button reproduces the launchd env exactly,
#      independent of whatever the panel-launching shell exported.
#   3. ``v1_runner.run_v1_pipeline`` `check_env_gates` reads three
#      of these keys at the start of every run.
#
# Anything you add here MUST also be added to the plist template,
# or the launchd fire and the test-fire will diverge.
LAUNCHD_ENV: Dict[str, str] = {
    "PYTHONUNBUFFERED": "1",
    "KIS_ENV": "paper",
    SUBMIT_ENV_GATE: "true",
    CANCEL_ENV_GATE: "true",
    APPLY_ENV_GATE:  "true",
    ALLOW_SELL_ENV_GATE: "true",
    "AUTOTRADE_V1_SUPPRESS_T7_MAIL": "true",
    # V1-I — live-quote BUY pricing + gap filter. Read in ``main`` for
    # the ``trade`` fire and threaded into ``run_generate_intents``.
    #   * USE_QUOTE / QUOTE_ONLY — price the initial BUY limit off the
    #     live KIS ask (not yesterday's stale close), so the order
    #     starts AT the live market. SELL already prices off the live
    #     bid (sell_candidates_to_intent_rows quote_only default).
    #   * BUY_QUOTE_PAD_PCT = -0.2 — start the BUY limit 0.2% BELOW the
    #     ask so it rests passively for one cycle (chance of a slightly
    #     better fill); the reprice ladder then chases up toward/past
    #     the ask. Negative is intentional and BUY-only (SELL keeps the
    #     shared positive quote_pad which lowers the limit below the bid).
    #   * GAP_FILTER_MAX_PCT = 15 — refuse to BUY a name that gapped UP
    #     >15% vs its reco close. Buying a double-digit gap is not a
    #     behaviour the backtest validated; we drop (don't chase) it.
    USE_QUOTE_ENV: "true",
    QUOTE_ONLY_ENV: "true",
    BUY_QUOTE_PAD_ENV: "-0.2",
    GAP_FILTER_ENV: "15",
    # V1-H — reprice chase + per-ticker resilience tuning for the
    # unattended fire. Read by ``OrderManagementPolicy.from_env``
    # (reprice) and ``default_manage_loop_fn`` (continue-on-unfilled)
    # inside the paper_submit subprocess, which inherits this env.
    #
    # V1-I re-tune: with USE_QUOTE on, the FIRST limit already sits at
    # the live ask, so the slippage ceiling (measured from the FIRST
    # limit — see order_manager.reprice_limit_buy) no longer needs the
    # ~12% of headroom V1-H added to climb from the stale close. It now
    # only needs to cover intra-fill drift between quote and rest, so
    # we shrink it back:
    #   * MAX_SLIPPAGE_BPS 1200 -> 300 (3% above the live-ask start).
    #   * MAX_REPRICE_ATTEMPTS 12 -> 6 (the ladder + quote-chase only
    #     has to cover the start-below-ask gap + small drift now).
    #   * CONTINUE_ON_UNFILLED stays — a ticker that still cannot fill
    #     within 3% of the live ask is a clean skip, not a batch abort.
    # The gap that V1-H tried to absorb with a wide ceiling is now
    # handled UPSTREAM by the gap filter (drop) + live-ask pricing.
    "AUTOTRADE_MAX_REPRICE_ATTEMPTS": "6",
    "AUTOTRADE_MAX_SLIPPAGE_BPS": "300",
    "AUTOTRADE_CONTINUE_ON_UNFILLED": "true",
}


# V1-F: the T7 prefetch fire at 09:00 KST does NOT touch the broker
# and does NOT need the submit/cancel/apply gates. Keeping the env
# block minimal here means a misconfigured T7 fire cannot accidentally
# submit live orders (defence in depth on top of the ``KIS_ENV=paper``
# pin). The corresponding plist is
# ``phase3/launchd/com.autotrade.v1.t7.plist.template``; parity is
# locked by ``test_v1f_plist`` (see F-4 in the V1-F plan).
T7_LAUNCHD_ENV: Dict[str, str] = {
    "PYTHONUNBUFFERED": "1",
    "KIS_ENV": "paper",
}


# Default V1 pricing for the unattended launchd path. The operator
# can override on the CLI. These match what the panel ships as the
# "safe" V1 defaults (matches the values typed during the
# 20260526 live verification run).
DEFAULT_BATCH_PAD_PCT = 0.0
DEFAULT_QUOTE_PAD_PCT = 0.1


# ──────────────────────────────────────────────────────────────────────
# Logging helper — single source of stdout formatting
# ──────────────────────────────────────────────────────────────────────
def _stamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _log(msg: str, *, stream=None) -> None:
    s = stream if stream is not None else sys.stdout
    s.write(f"[v1] {_stamp()} {msg}\n")
    s.flush()


# ──────────────────────────────────────────────────────────────────────
# Outcome model
# ──────────────────────────────────────────────────────────────────────
@dataclass
class V1RunResult:
    """End-to-end outcome of a single ``v1_runner run`` invocation."""
    rc: int = 0
    halt_reason: Optional[str] = None
    run_id: str = ""
    run_dir: Optional[Path] = None
    started_at: str = field(default_factory=_stamp)
    ended_at: str = ""
    duration_sec: float = 0.0
    stages: List[Dict[str, Any]] = field(default_factory=list)
    t7_payload: Optional[Dict[str, Any]] = None
    arm_ok: bool = True
    # V2-A — True when the fire returned early on a calendar / halt /
    # arm gate (a clean no-trade skip). Lets ``main`` decide NOT to
    # record this fire into the auto-lockout history (skips are neither
    # successes nor failures).
    skipped_by_gate: bool = False


# ──────────────────────────────────────────────────────────────────────
# Env-gate validator
# ──────────────────────────────────────────────────────────────────────
def _gate_truthy(env: Mapping[str, str], key: str) -> bool:
    return str(env.get(key, "")).strip().lower() == "true"


def _env_float(env: Mapping[str, str], key: str) -> Optional[float]:
    """Parse an optional float env var. Returns ``None`` when unset or
    blank so callers can distinguish "operator did not configure this"
    from "configured to 0". A malformed value is treated as unset (the
    safe default) rather than crashing the unattended fire."""
    raw = str(env.get(key, "")).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        _log(f"WARN: {key}={raw!r} is not a float — ignoring")
        return None


def check_env_gates(env: Mapping[str, str]) -> Optional[str]:
    """Return ``None`` if all four env gates are set as V1 expects, or
    a human-readable reason string if not.

    V1 is paper-only; ``KIS_ENV`` MUST be exactly ``'paper'`` (an empty
    or accidentally 'live' value blocks the run, never an upgrade)."""
    if env.get(KIS_ENV_VAR, "").strip() != "paper":
        return (f"{KIS_ENV_VAR}={env.get(KIS_ENV_VAR, '')!r} "
                f"(need 'paper'); refusing")
    for k in (SUBMIT_ENV_GATE, CANCEL_ENV_GATE, APPLY_ENV_GATE):
        if not _gate_truthy(env, k):
            return f"{k} not set to true; refusing"
    return None


def check_t7_env_gates(env: Mapping[str, str]) -> Optional[str]:
    """V1-F: the 09:00 T7 prefetch only needs ``KIS_ENV=paper``.

    Why not require SUBMIT/CANCEL/APPLY too?
    ----------------------------------------
    The T7 prefetch never calls the broker — it generates
    recommendations and writes them to disk. If the prefetch happened
    to inherit ``KIS_PAPER_SUBMIT_OK=true`` (because the operator
    exported it in their shell) the fire still does nothing
    submit-shaped, but requiring SUBMIT/CANCEL/APPLY to be UNSET would
    cause spurious halts on operators who keep them on globally. We
    take the narrower contract: ``KIS_ENV=paper`` is the only must.
    """
    if env.get(KIS_ENV_VAR, "").strip() != "paper":
        return (f"{KIS_ENV_VAR}={env.get(KIS_ENV_VAR, '')!r} "
                f"(need 'paper'); refusing")
    return None


# ──────────────────────────────────────────────────────────────────────
# generate_intents (in-process, mirrors panel's batch path)
# ──────────────────────────────────────────────────────────────────────
@dataclass
class GenerateIntentsResult:
    rc: int
    rows_written: int
    warnings: List[Any] = field(default_factory=list)
    error: str = ""
    # V1-G — per-side breakdown so the panel/log/mail can show how
    # many BUYs vs SELLs were generated and how many of those SELLs
    # were STOP_LOSS vs SELL vs TRIM. rows_written stays the sum so
    # existing callers / tests that only look at the total are still
    # correct.
    buy_count: int = 0
    sell_count: int = 0
    sell_action_breakdown: Dict[str, int] = field(default_factory=dict)
    # V1-I — BUY candidates dropped by the gap filter (gapped up beyond
    # the cap vs reco close). These never became intent rows. Surfaced
    # in the launchd log + R11B mail so the operator can see WHY a
    # recommended BUY did not trade.
    dropped_by_gap: int = 0
    dropped_gap_tickers: List[str] = field(default_factory=list)


def run_generate_intents(
    *,
    run_dir: Path,
    run_id: str,
    batch_pad_pct: float = DEFAULT_BATCH_PAD_PCT,
    quote_pad_pct: float = DEFAULT_QUOTE_PAD_PCT,
    use_quote: bool = False,
    quote_only: bool = False,
    buy_quote_pad_pct: Optional[float] = None,
    gap_filter_max_pct: Optional[float] = None,
    overwrite: bool = True,
    include_sells: bool = True,
) -> GenerateIntentsResult:
    """Headless equivalent of the panel's
    ``_run_generate_all_intents_blocking``.

    V1-G: BOTH BUY and SELL candidates are read from
    ``recommendations.csv`` and merged into a single
    ``submitted_intents.json``. SELL_GRACE is excluded by
    ``intents_io.load_sell_candidates`` (warning state only).
    STOP_LOSS / SELL / TRIM are submitted.

    The function returns rc=2 only when **both** the BUY and SELL
    pools are empty. A run with zero BUYs but one STOP_LOSS is a
    perfectly valid trading day (forced exits with no new entries),
    so we don't treat it as failure.

    ``include_sells=False`` is a kill-switch — useful for the legacy
    "buys only" debug path and for the very first cutover so the
    operator can A/B compare against the V1-F behavior.

    Quote integration is OFF by default in the unattended path —
    bringing up the live KIS adapter inside a launchd worker process
    is more failure modes than upside, and the reco close + 0% pad
    has been the safe V1 default in live runs so far. When quotes
    ARE on (``use_quote=True``), the SELL side uses bid-based
    limits (see ``sell_candidates_to_intent_rows``) while BUY uses
    ask-based limits.
    """
    try:
        buy_cands = intents_io.load_buy_candidates(run_dir)
    except Exception as e:  # noqa: BLE001
        return GenerateIntentsResult(
            rc=2, rows_written=0,
            error=f"load_buy_candidates: {type(e).__name__}: {e}")
    sell_cands: List[Any] = []
    if include_sells:
        try:
            sell_cands = intents_io.load_sell_candidates(run_dir)
        except Exception as e:  # noqa: BLE001
            return GenerateIntentsResult(
                rc=2, rows_written=0,
                error=f"load_sell_candidates: {type(e).__name__}: {e}")
    if not buy_cands and not sell_cands:
        return GenerateIntentsResult(
            rc=2, rows_written=0,
            error="no BUY or SELL candidates in recommendations.csv")

    quote_fn = None
    if use_quote:
        try:
            from phase3.autotrade.kis_broker_adapter import (
                KisBrokerAdapter, load_env_config,
            )
            env_cfg = load_env_config()
            if env_cfg.env_name != "paper":
                return GenerateIntentsResult(
                    rc=2, rows_written=0,
                    error=f"KIS_ENV={env_cfg.env_name!r} not paper")
            qa = KisBrokerAdapter(cfg=env_cfg, verbose=False)

            def quote_fn(symbol: str, market: str):  # noqa: E306
                return qa.get_quote_with_exchange_fallback(
                    symbol, preferred_market=market)
        except Exception as e:  # noqa: BLE001
            return GenerateIntentsResult(
                rc=2, rows_written=0,
                error=f"quote adapter init: {type(e).__name__}: {e}")

    warnings: List[Any] = []
    buy_rows: List[Dict[str, Any]] = []
    sell_rows: List[Dict[str, Any]] = []
    if buy_cands:
        try:
            # V1-I — BUY may start *below* the live ask (negative
            # ``buy_quote_pad_pct``) and is gap-filtered. SELL keeps the
            # shared ``quote_pad_pct`` (which pads DOWN from the bid, so
            # a negative BUY pad must not leak onto the SELL side).
            buy_pad = (buy_quote_pad_pct if buy_quote_pad_pct is not None
                       else quote_pad_pct)
            buy_rows = intents_io.candidates_to_intent_rows(
                buy_cands,
                limit_pad_pct=batch_pad_pct,
                quote_fn=quote_fn,
                quote_pad_pct=buy_pad,
                quote_only=quote_only,
                gap_filter_max_pct=gap_filter_max_pct,
                warnings_out=warnings,
            )
        except Exception as e:  # noqa: BLE001
            return GenerateIntentsResult(
                rc=2, rows_written=0,
                error=f"candidates_to_intent_rows (BUY): "
                      f"{type(e).__name__}: {e}",
            )
    if sell_cands:
        try:
            # SELL is quote-only by default — using reco close as a
            # floor on SELL means refusing to sell after a gap-down,
            # which is exactly the failure stop-loss exists to
            # prevent. See sell_candidates_to_intent_rows docstring.
            sell_rows = intents_io.sell_candidates_to_intent_rows(
                sell_cands,
                limit_pad_pct=batch_pad_pct,
                quote_fn=quote_fn,
                quote_pad_pct=quote_pad_pct,
                quote_only=True,
                warnings_out=warnings,
            )
        except Exception as e:  # noqa: BLE001
            return GenerateIntentsResult(
                rc=2, rows_written=0,
                error=f"sell_candidates_to_intent_rows: "
                      f"{type(e).__name__}: {e}",
            )

    # V1-I — tally gap-filter drops from the warning stream so the
    # caller can report "recommended N, dropped M for gapping up".
    dropped_gap_tickers = [
        w.ticker for w in warnings
        if isinstance(w, intents_io.IntentBuildWarning)
        and str(w.reason).startswith(intents_io._GAP_FILTER_WARN_PREFIX)
    ]
    dropped_by_gap = len(dropped_gap_tickers)

    sell_breakdown: Dict[str, int] = {}
    for c in sell_cands:
        sell_breakdown[c.action] = sell_breakdown.get(c.action, 0) + 1

    rows: List[Dict[str, Any]] = list(buy_rows) + list(sell_rows)
    if not rows:
        # V1-I — distinguish "every recommended BUY gapped past the cap"
        # (a legitimate flat day → rc=0, write an empty batch so the
        # downstream submit/apply is a clean no-op) from a genuine
        # builder failure (rc=2). Only the gap-filter path is benign;
        # if there were candidates but no drops, something is wrong.
        if dropped_by_gap > 0 and not sell_cands:
            try:
                intents_io.write_submitted_intents(
                    run_dir, [], run_id=run_id, overwrite=overwrite)
            except Exception as e:  # noqa: BLE001
                return GenerateIntentsResult(
                    rc=2, rows_written=0,
                    error=f"write_submitted_intents (empty): "
                          f"{type(e).__name__}: {e}")
            return GenerateIntentsResult(
                rc=0, rows_written=0, warnings=warnings,
                buy_count=0, sell_count=0,
                dropped_by_gap=dropped_by_gap,
                dropped_gap_tickers=dropped_gap_tickers,
            )
        return GenerateIntentsResult(
            rc=2, rows_written=0,
            error="candidates_to_intent_rows produced 0 rows")

    try:
        intents_io.write_submitted_intents(
            run_dir, rows, run_id=run_id, overwrite=overwrite)
    except Exception as e:  # noqa: BLE001
        return GenerateIntentsResult(
            rc=2, rows_written=0,
            error=f"write_submitted_intents: "
                  f"{type(e).__name__}: {e}",
        )
    return GenerateIntentsResult(
        rc=0, rows_written=len(rows), warnings=warnings,
        buy_count=len(buy_rows), sell_count=len(sell_rows),
        sell_action_breakdown=sell_breakdown,
        # V1-I reporting fix: the gap-drop tally MUST ride the normal
        # (non-flat) success path too, otherwise a partial drop (e.g.
        # 2 BUYs recommended, 1 gapped past the cap) silently vanishes
        # from the log + R11B mail and the operator sees a ticker
        # "disappear" with no explanation. (Discovered 2026-06-02: HPE
        # gapped >15% and was correctly dropped, but nothing reported it.)
        dropped_by_gap=dropped_by_gap,
        dropped_gap_tickers=dropped_gap_tickers,
    )


# ──────────────────────────────────────────────────────────────────────
# paper_submit + apply_t10 subprocess
# ──────────────────────────────────────────────────────────────────────
SubprocessRunFn = Callable[..., subprocess.CompletedProcess]


def _default_subprocess_run(
    argv: Sequence[str], *,
    env: Mapping[str, str],
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        env=dict(env),
        capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def run_paper_submit_and_apply(
    *,
    run_id: str,
    profile: str = "paper",
    env: Optional[Mapping[str, str]] = None,
    timeout_sec: Optional[float] = 60 * 30,
    subprocess_run: Optional[SubprocessRunFn] = None,
    apply_t10: bool = True,
) -> Dict[str, Any]:
    """Single subprocess call: paper_submit (+ optionally apply_t10).

    ``daily_runner.main`` already supports both flags in a single
    invocation (it journals submit then runs t10_apply on the
    survivor batch). Keeping them in one process means we never end
    up with a clean submit followed by a launchd crash before apply
    — the journal would otherwise show ``started`` without ``applied``
    and the next manual T7 would refuse to proceed.

    ``apply_t10=False`` is the safe choice for **panel test-fire
    BEFORE US market open**: orders submitted minutes pre-open sit
    OPEN in the paper broker, daily_runner's manage_loop bails on
    its time budget, and t10_applicator policy-aborts on the
    unfilled orders (rc=2). Test-fire should still verify the
    submit plumbing without touching the holdings_log; the operator
    can then T10-apply manually after fills, or simply let the
    22:35 launchd fire do the real apply.
    """
    runner = subprocess_run or _default_subprocess_run
    base_env = dict(env if env is not None else os.environ)
    argv = [
        sys.executable, "-m", "phase3.autotrade.daily_runner",
        "--profile", profile,
        "--run-id", run_id,
        "--paper-submit",
    ]
    if apply_t10:
        argv.append("--apply-t10")
    t0 = time.monotonic()
    try:
        cp = runner(argv, env=base_env, timeout=timeout_sec)
        rc = int(cp.returncode)
        stdout = getattr(cp, "stdout", "") or ""
        stderr = getattr(cp, "stderr", "") or ""
        err = ""
    except subprocess.TimeoutExpired:
        rc, stdout, stderr = 124, "", ""
        err = f"paper_submit/apply timed out after {timeout_sec}s"
    except Exception as e:  # noqa: BLE001
        rc, stdout, stderr = 255, "", ""
        err = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0
    return {
        "rc": rc, "duration_sec": elapsed,
        "argv": argv,
        "stdout_tail": _tail_lines(stdout, 60),
        "stderr_tail": _tail_lines(stderr, 60),
        "error": err,
    }


def _tail_lines(text: str, n: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# ──────────────────────────────────────────────────────────────────────
# Pipeline orchestrator
# ──────────────────────────────────────────────────────────────────────
def run_v1_pipeline(
    *,
    config_path: Optional[Path] = None,
    skip_t7: bool = False,
    run_id_override: str = "",
    profile: str = "paper",
    batch_pad_pct: float = DEFAULT_BATCH_PAD_PCT,
    quote_pad_pct: float = DEFAULT_QUOTE_PAD_PCT,
    use_quote: bool = False,
    quote_only: bool = False,
    buy_quote_pad_pct: Optional[float] = None,
    gap_filter_max_pct: Optional[float] = None,
    require_arm_token: bool = True,
    output_dir_override: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    runtime_dir: Optional[Path] = None,
    arm_now: Optional[datetime] = None,
    subprocess_run: Optional[SubprocessRunFn] = None,
    t7_subprocess_run: Optional[t7_runner.SubprocessRunFn] = None,
    log: Callable[[str], None] = _log,
    send_mail: bool = True,
    apply_t10: bool = True,
    discover_today_t7: bool = False,
    fire_label: str = "run",
    status_path: Optional[Path] = None,
) -> V1RunResult:
    """Run the full V1 pipeline. NEVER raises; failures populate
    ``rc`` + ``halt_reason``.

    Parameters new in V1-F
    ----------------------
    discover_today_t7
        When True (the ``trade`` subcommand), the pipeline does NOT
        run T7 inline. Instead it scans ``daily_runs/`` for a
        prefetch run produced earlier today (KST) and reuses its
        ``run_id``. If none is found the run hard-fails with rc=2
        and a halt_reason — using yesterday's recommendations is the
        exact bug V1-F exists to prevent. Mutually exclusive with
        ``skip_t7`` (which is the operator-supplied-run_id mode).
    fire_label
        Free-form tag for the status.json snapshot — ``"trade"``,
        ``"run"``, ``"test-fire"``. Surfaces in the panel "Last
        fire" row.
    status_path
        Override the default ``runtime/v1_status.json`` location.
        Used by tests; production callers leave it at the default.
    """
    t0 = time.monotonic()
    result = V1RunResult()
    env_map = dict(env if env is not None else os.environ)
    # V1-F.4 — when the caller passes a custom ``runtime_dir`` (the
    # signature already supports this for the arm-token file) but
    # leaves ``status_path`` at its default, derive the status file
    # under that runtime_dir too. Pre-V1-F.4 the tests overrode
    # runtime_dir but the pipeline still clobbered the real
    # ``phase3/autotrade/runtime/v1_status.json`` on every unit-test
    # run, which broke V1-E.6's "last fire KST date" lookup and any
    # other code that trusts the on-disk snapshot.
    if status_path is None and runtime_dir is not None:
        status_path = Path(runtime_dir) / "v1_status.json"
    sp = v1_status.Writer(path=status_path)
    sp.start(fire_label=fire_label,
             started_at_utc=result.started_at,
             log_path=_launchd_log_for(fire_label))

    # V2-A — gates that run BEFORE the arm gate on a real (gated) fire.
    # Precedence: trading-calendar → global_halt → arm. All three are a
    # clean no-trade SKIP (rc=0) so launchd does not retry. ``require_arm
    # _token`` is the existing "this is a real launchd fire" switch, so we
    # piggyback on it: ad-hoc CLI / panel test-fires (--no-arm) bypass all
    # three, exactly as they bypass the arm gate today.
    if require_arm_token:
        # -1) Trading-calendar gate — skip weekends + NYSE holidays so the
        # standing-arm trader doesn't fire into a closed market.
        sp.set_stage("calendar_gate")
        session_d = v1_arm.today_kst_date(now=arm_now)
        if not trading_calendar.is_trading_day(session_d):
            result.skipped_by_gate = True
            result.rc = 0
            result.halt_reason = (
                f"{session_d.isoformat()} is not a NYSE trading day "
                f"(weekend/holiday)")
            log(f"calendar gate: {result.halt_reason}")
            log("SKIP — exit rc=0 (launchd retries only on non-zero)")
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=0, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        sp.complete_stage("calendar_gate", rc=0)

        # 0a) global_halt gate — manual STOP or a latched auto-lockout.
        sp.set_stage("halt_gate")
        hs = global_halt.read_halt()
        if hs.halted:
            result.skipped_by_gate = True
            result.rc = 0
            result.halt_reason = (
                f"global_halt is set: {hs.reason or '(no reason)'} "
                f"— investigate, then Clear in panel to resume")
            log(f"halt gate: {result.halt_reason}")
            log("SKIP — exit rc=0 (launchd retries only on non-zero)")
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=0, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        sp.complete_stage("halt_gate", rc=0)

    # 0b) Arm-token gate (V2-A: standing OR daily token).
    sp.set_stage("arm_token")
    if require_arm_token:
        ac = v1_arm.require_armed_for_today(
            runtime_dir=runtime_dir, now=arm_now)
        if not ac.ok:
            result.arm_ok = False
            result.skipped_by_gate = True
            result.rc = 0   # safe-skip — launchd retries on non-zero
            result.halt_reason = ac.reason
            log(f"arm gate failed: {ac.reason}")
            log("SKIP — exit rc=0 (launchd retries only on non-zero)")
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        log(f"arm OK for {ac.date_kst} (mode={ac.mode}; {ac.reason})")
    sp.complete_stage("arm_token", rc=0)

    # 1) Env gates.
    sp.set_stage("env_gates")
    gate_err = check_env_gates(env_map)
    if gate_err:
        result.rc = 2
        result.halt_reason = gate_err
        log(f"env-gate halt: {gate_err}")
        result.ended_at = _stamp()
        result.duration_sec = time.monotonic() - t0
        sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                  ended_at_utc=result.ended_at)
        return result
    log("env gates OK (KIS_ENV=paper, SUBMIT/CANCEL/APPLY=true)")
    sp.complete_stage("env_gates", rc=0)

    # 1b) V1-F: discover this morning's T7 prefetch run BEFORE doing
    # any T7 work. This is mutually exclusive with skip_t7 (which is
    # the operator-supplied-run_id mode for ad-hoc CLI runs) and
    # with the inline-T7 path (the legacy ``run`` subcommand kept
    # for the panel test-fire).
    rid = run_id_override.strip()
    output_dir_for_discover = (
        Path(output_dir_override) if output_dir_override
        else _resolve_output_dir(config_path))
    if discover_today_t7 and not skip_t7:
        sp.set_stage("discover_t7_run")
        if output_dir_for_discover is None:
            result.rc = 2
            result.halt_reason = (
                "could not resolve output_dir from config; pass "
                "--output-dir explicitly")
            log(result.halt_reason)
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        disc = _find_today_t7_run(
            output_dir_for_discover / "daily_runs",
            now_kst=arm_now,
        )
        result.stages.append({
            "key": "discover_t7_run",
            "rc": 0 if disc.run_dir is not None else 2,
            "duration_sec": 0.0,
            "halt_reason": disc.reason if disc.run_dir is None else None,
            "skipped": False,
        })
        if disc.run_dir is None:
            result.rc = 2
            result.halt_reason = f"T7 prefetch missing: {disc.reason}"
            log(f"discover_t7_run FAILED: {disc.reason}")
            sp.complete_stage("discover_t7_run", rc=2,
                              halt_reason=result.halt_reason)
            # R11B mail still goes out so the operator gets the
            # hard-fail signal without watching launchd logs.
            _maybe_mail(result, output_dir_for_discover, log,
                        send_mail=send_mail, profile=profile,
                        fire_label=fire_label)
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        rid = disc.run_id
        result.run_id = rid
        log(f"discover_t7_run OK — using {rid} "
            f"(recs={disc.recommendations_count})")
        sp.complete_stage("discover_t7_run", rc=0, run_id=rid)
        # Skip the inline T7 path below; we already have a run_id.
        skip_t7 = True

    # 2) T7.
    t7_started = time.monotonic()
    if not skip_t7:
        sp.set_stage("t7_generate")
        # paper-only pipeline → config.yaml (output/) is the single
        # source of truth; see ``t7_runner._DEFAULT_T7_CONFIG``
        # comment for the parity rationale.
        cfg = config_path or (_PHASE3 / "config.yaml")
        if not Path(cfg).exists():
            cfg = _PHASE3 / "config_real.yaml"
        log(f"T7: starting (config={cfg})")
        t7_res = t7_runner.run_t7_generate(
            config_path=Path(cfg),
            suppress_mail=True,
            on_progress=lambda m: log(m),
            subprocess_run=t7_subprocess_run,
            base_env=env_map,
        )
        result.t7_payload = {
            "ok": bool(t7_res.ok), "rc": int(t7_res.rc),
            "run_id": t7_res.run_id,
            "recommendations_count":
                int(t7_res.recommendations_count),
            "duration_sec": float(t7_res.duration_sec),
            "error": t7_res.error,
            "stdout_tail": t7_res.stdout_tail,
            "stderr_tail": t7_res.stderr_tail,
            "suppressed_mail": True,
        }
        result.stages.append({
            "key": "t7_generate", "rc": t7_res.rc,
            "duration_sec": t7_res.duration_sec,
            "halt_reason": t7_res.error if not t7_res.ok else None,
            "skipped": False,
        })
        if not t7_res.ok:
            result.rc = 2
            result.halt_reason = f"T7: {t7_res.error}"
            log(f"T7 FAILED: {t7_res.error}")
            sp.complete_stage("t7_generate", rc=t7_res.rc,
                              halt_reason=t7_res.error)
            _maybe_mail(result, output_dir_override, log,
                        send_mail=send_mail, profile=profile,
                        fire_label=fire_label)
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        rid = t7_res.run_id
        result.run_id = rid
        log(f"T7 OK — run_id={rid} recs={t7_res.recommendations_count} "
            f"duration={t7_res.duration_sec:.1f}s")
        sp.complete_stage("t7_generate", rc=0, run_id=rid)
    else:
        if not rid:
            result.rc = 2
            result.halt_reason = "--skip-t7 requires --run-id"
            log(result.halt_reason)
            result.ended_at = _stamp()
            result.duration_sec = time.monotonic() - t0
            sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                      ended_at_utc=result.ended_at)
            return result
        result.run_id = rid
        result.stages.append({
            "key": "t7_generate", "rc": 0, "duration_sec": 0.0,
            "halt_reason": None, "skipped": True,
        })
        log(f"T7 skipped (operator-supplied run_id={rid})")

    # Resolve run_dir from config OR from override.
    output_dir = (output_dir_for_discover if output_dir_for_discover
                  else _resolve_output_dir(config_path))
    if output_dir is None:
        result.rc = 2
        result.halt_reason = (
            "could not resolve output_dir from config; pass "
            "--output-dir explicitly")
        log(result.halt_reason)
        result.ended_at = _stamp()
        result.duration_sec = time.monotonic() - t0
        sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                  ended_at_utc=result.ended_at)
        return result
    run_dir = output_dir / "daily_runs" / rid
    result.run_dir = run_dir

    # 3) generate_intents.
    sp.set_stage("generate_intents")
    log(f"generate_intents: starting (pad={batch_pad_pct}%, "
        f"quote_pad={quote_pad_pct}%, use_quote={use_quote})")
    gi_t0 = time.monotonic()
    gi = run_generate_intents(
        run_dir=run_dir, run_id=rid,
        batch_pad_pct=batch_pad_pct,
        quote_pad_pct=quote_pad_pct,
        use_quote=use_quote, quote_only=quote_only,
        buy_quote_pad_pct=buy_quote_pad_pct,
        gap_filter_max_pct=gap_filter_max_pct,
    )
    gi_dt = time.monotonic() - gi_t0
    result.stages.append({
        "key": "generate_intents", "rc": gi.rc,
        "duration_sec": gi_dt,
        "halt_reason": gi.error if gi.rc != 0 else None,
        "skipped": False,
    })
    if gi.rc != 0:
        result.rc = gi.rc
        result.halt_reason = f"generate_intents: {gi.error}"
        log(f"generate_intents FAILED: {gi.error}")
        sp.complete_stage("generate_intents", rc=gi.rc,
                          halt_reason=gi.error)
        _maybe_mail(result, output_dir, log,
                    send_mail=send_mail, profile=profile,
                    fire_label=fire_label)
        result.ended_at = _stamp()
        result.duration_sec = time.monotonic() - t0
        sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                  ended_at_utc=result.ended_at)
        return result
    # V1-G: surface BUY/SELL split + STOP_LOSS count so the operator
    # can see at a glance from the log whether today's run includes
    # forced exits. The R11B mail body separately enumerates each
    # row with its T7 action.
    sell_bits = ""
    if gi.sell_count:
        bits = [f"{a}={n}" for a, n in
                sorted(gi.sell_action_breakdown.items())]
        sell_bits = f" sells={gi.sell_count} ({', '.join(bits)})"
    gap_bits = ""
    if gi.dropped_by_gap:
        gap_bits = (f" gap_dropped={gi.dropped_by_gap} "
                    f"({', '.join(gi.dropped_gap_tickers)})")
    log(f"generate_intents OK — rows={gi.rows_written} "
        f"buys={gi.buy_count}{sell_bits}{gap_bits}")
    sp.complete_stage("generate_intents", rc=0,
                      rows_written=gi.rows_written)

    # V1-I — flat day: every recommended BUY gapped past the cap (and
    # no SELLs). ``run_generate_intents`` already wrote an empty intent
    # file and returned rc=0; there is nothing for the broker to do, so
    # short-circuit to a clean finish instead of spinning up a
    # paper_submit subprocess on zero orders (which would have nothing
    # to fill and could trip the t10 "nothing to apply" path).
    if gi.rows_written == 0:
        result.rc = 0
        result.halt_reason = None
        log(f"no tradable intents today — "
            f"{gi.dropped_by_gap} BUY(s) dropped by gap filter, "
            f"0 SELL(s). Clean flat day, skipping paper_submit.")
        _maybe_mail(result, output_dir, log,
                    send_mail=send_mail, profile=profile,
                    fire_label=fire_label)
        result.ended_at = _stamp()
        result.duration_sec = time.monotonic() - t0
        sp.finish(final_rc=0, halt_reason=None,
                  ended_at_utc=result.ended_at)
        return result

    # 4) paper_submit (+ apply_t10 unless caller opts out).
    stage_name = ("paper_submit_and_apply" if apply_t10
                  else "paper_submit_only")
    sp.set_stage(stage_name)
    log(f"{stage_name}: starting subprocess "
        f"(apply_t10={apply_t10})")
    sub = run_paper_submit_and_apply(
        run_id=rid, profile=profile, env=env_map,
        subprocess_run=subprocess_run, apply_t10=apply_t10,
    )
    result.stages.append({
        "key": stage_name, "rc": sub["rc"],
        "duration_sec": sub["duration_sec"],
        "halt_reason": (sub["error"] if sub["rc"] != 0
                        else None),
        "skipped": False,
    })
    if sub["rc"] != 0:
        result.rc = sub["rc"]
        result.halt_reason = (f"{stage_name} rc={sub['rc']}"
                              + (f": {sub['error']}"
                                 if sub["error"] else ""))
        log(f"{stage_name} FAILED rc={sub['rc']}: "
            f"{sub['error']}")
        if sub["stderr_tail"]:
            log("stderr tail:\n" + sub["stderr_tail"])
        sp.complete_stage(stage_name, rc=sub["rc"],
                          halt_reason=result.halt_reason)
    else:
        log(f"{stage_name} OK "
            f"(duration={sub['duration_sec']:.1f}s)")
        sp.complete_stage(stage_name, rc=0)

    # 5) R11B EOD digest.
    _maybe_mail(result, output_dir, log,
                send_mail=send_mail, profile=profile,
                fire_label=fire_label)
    result.ended_at = _stamp()
    result.duration_sec = time.monotonic() - t0
    log(f"V1 pipeline FINISHED — rc={result.rc} "
        f"halt_reason={result.halt_reason!r} "
        f"duration={result.duration_sec:.1f}s")
    sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
              ended_at_utc=result.ended_at)
    return result


def _read_json_field(path: Path, key: str):
    """Best-effort read of a single top-level field from a JSON file.
    Returns None on any error (missing file, parse error, missing key)."""
    try:
        import json as _json
        data = _json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get(key)
    except Exception:  # noqa: BLE001
        pass
    return None


def record_fire_and_evaluate_lockout(
    result: V1RunResult,
    *,
    runtime_dir: Optional[Path] = None,
    arm_now: Optional[datetime] = None,
    log: Callable[[str], None] = _log,
) -> Optional[auto_halt.LockoutDecision]:
    """V2-B — append this trade fire to the lockout history and evaluate
    the auto-lockout rules. Latches ``global_halt`` if any rule trips.

    Call ONLY for real trade fires that passed the gates (``not result.
    skipped_by_gate``); skips are neither success nor failure. Best-effort
    and never raises — a logging/evaluation hiccup must not change the
    fire's own rc."""
    try:
        session_d = v1_arm.today_kst_date(now=arm_now)
        scoring_date = None
        total_after = None
        if result.run_dir is not None:
            rd = Path(result.run_dir)
            scoring_date = _read_json_field(rd / "run_meta.json",
                                            "scoring_date")
            total_after = _read_json_field(
                rd / "autotrade_t10_apply_report.json", "total_after")
        auto_halt.record_fire(
            run_id=result.run_id or "(unknown)",
            session_date_kst=session_d.isoformat(),
            kind="trade",
            rc=result.rc,
            total_after=(float(total_after)
                         if isinstance(total_after, (int, float)) else None),
            scoring_date=(scoring_date if isinstance(scoring_date, str)
                          else None),
            note=(result.halt_reason or ""),
            runtime_dir=runtime_dir,
        )
        history = auto_halt.read_history(runtime_dir=runtime_dir)
        decision = auto_halt.evaluate_lockout(history, session_date=session_d)
        log(f"auto-lockout check: {decision.detail}")
        if decision.tripped:
            applied = auto_halt.apply_lockout(decision)
            if applied is not None:
                log(f"AUTO-LOCKOUT TRIGGERED — global_halt latched at "
                    f"{applied}: {decision.message}. Trading is now PAUSED "
                    f"until an operator investigates and Clears the halt.")
            else:
                log(f"auto-lockout condition met ({decision.message}) but "
                    f"global_halt was already set — leaving as-is.")
        return decision
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: auto-lockout bookkeeping failed (non-fatal): {exc}")
        return None


def run_t7_prefetch(
    *,
    config_path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    t7_subprocess_run: Optional[t7_runner.SubprocessRunFn] = None,
    log: Callable[[str], None] = _log,
    status_path: Optional[Path] = None,
    dry_run: bool = False,
) -> V1RunResult:
    """V1-F — 09:00 KST T7 prefetch fire.

    What this does, and why it is separate from ``run_v1_pipeline``:

    * NO arm-token gate. T7 prefetch happens whether or not the
      operator intends to trade tonight; the arm token is the
      operator's "trade tonight" green-light, not a "preview the
      picks" green-light. Worst case: T7 runs, sends an email, and
      no trade fire follows because the operator didn't arm.
    * NO SUBMIT / CANCEL / APPLY env-gate. T7 never touches the
      broker — requiring those would surface as a hard-fail on
      operators who do not export those vars during the day.
      ``check_t7_env_gates`` enforces ``KIS_ENV=paper`` only.
    * ``suppress_mail=False``. The T7 process is told to send its
      own recommendation email via the existing ``phase3/mailer.py``
      pipeline, so the operator sees today's picks BEFORE arming.
      This was the entire point of V1-F: separate the "what shall
      we trade" mail (T7) from the "what we did trade" mail (R11B).

    On failure, status.json captures the diagnostic so the panel can
    surface it (the launchd subprocess has no stdout the panel can
    stream). The function still returns rc=2 so launchd marks the
    last_exit accordingly.
    """
    t0 = time.monotonic()
    result = V1RunResult(fire_label="t7_prefetch") if False else V1RunResult()
    env_map = dict(env if env is not None else os.environ)
    sp = v1_status.Writer(path=status_path)
    sp.start(fire_label="t7_prefetch", started_at_utc=result.started_at,
             log_path=_launchd_log_for("t7_prefetch"))

    # 1) Env-gate (T7-only: KIS_ENV=paper).
    sp.set_stage("env_gates")
    gate_err = check_t7_env_gates(env_map)
    if gate_err:
        result.rc = 2
        result.halt_reason = gate_err
        log(f"env-gate halt: {gate_err}")
        result.ended_at = _stamp()
        result.duration_sec = time.monotonic() - t0
        sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
                  ended_at_utc=result.ended_at)
        return result
    log("env gates OK (KIS_ENV=paper)")
    sp.complete_stage("env_gates", rc=0)

    # 2) T7 generate — same path as the inline V1 run, but with
    # ``suppress_mail=False`` so the existing T7 mailer fires.
    sp.set_stage("t7_generate")
    cfg = config_path or (_PHASE3 / "config.yaml")
    if not Path(cfg).exists():
        cfg = _PHASE3 / "config_real.yaml"
    log(f"T7 prefetch: starting (config={cfg}"
        f"{', dry-run PREVIEW' if dry_run else ''})")
    t7_res = t7_runner.run_t7_generate(
        config_path=Path(cfg),
        suppress_mail=False,
        dry_run=dry_run,
        on_progress=lambda m: log(m),
        subprocess_run=t7_subprocess_run,
        base_env=env_map,
    )
    result.t7_payload = {
        "ok": bool(t7_res.ok), "rc": int(t7_res.rc),
        "run_id": t7_res.run_id,
        "recommendations_count":
            int(t7_res.recommendations_count),
        "duration_sec": float(t7_res.duration_sec),
        "error": t7_res.error,
        "stdout_tail": t7_res.stdout_tail,
        "stderr_tail": t7_res.stderr_tail,
        "suppressed_mail": False,
        "dry_run": dry_run,
    }
    result.stages.append({
        "key": "t7_generate", "rc": t7_res.rc,
        "duration_sec": t7_res.duration_sec,
        "halt_reason": t7_res.error if not t7_res.ok else None,
        "skipped": False,
    })
    if not t7_res.ok:
        result.rc = 2
        result.halt_reason = f"T7 prefetch: {t7_res.error}"
        log(f"T7 prefetch FAILED: {t7_res.error}")
        sp.complete_stage("t7_generate", rc=t7_res.rc,
                          halt_reason=t7_res.error)
    else:
        result.run_id = t7_res.run_id
        log(f"T7 prefetch OK — run_id={t7_res.run_id} "
            f"recs={t7_res.recommendations_count} "
            f"duration={t7_res.duration_sec:.1f}s")
        sp.complete_stage("t7_generate", rc=0, run_id=t7_res.run_id,
                          recommendations_count=int(
                              t7_res.recommendations_count))
    result.ended_at = _stamp()
    result.duration_sec = time.monotonic() - t0
    log(f"T7 prefetch FINISHED — rc={result.rc} "
        f"duration={result.duration_sec:.1f}s")
    sp.finish(final_rc=result.rc, halt_reason=result.halt_reason,
              ended_at_utc=result.ended_at)
    return result


def _resolve_output_dir(
    config_path: Optional[Path],
) -> Optional[Path]:
    """Read ``paths.output_dir`` from T7 yaml. Same pure-python
    fallback shape as ``t7_runner._daily_runs_dir_from_config``
    but returning the BASE output_dir, not the daily_runs subdir."""
    cfg = config_path or (_PHASE3 / "config.yaml")
    if not Path(cfg).exists():
        cfg = _PHASE3 / "config_real.yaml"
    if not Path(cfg).exists():
        return None
    try:
        import yaml  # type: ignore
        with open(cfg, "r", encoding="utf-8") as f:
            conf = yaml.safe_load(f) or {}
        out = (conf.get("paths") or {}).get("output_dir", "")
        if out:
            return Path(out).expanduser()
    except ImportError:
        in_paths = False
        with open(cfg, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("paths:"):
                    in_paths = True
                    continue
                if in_paths and not (line.startswith(" ")
                                     or line.startswith("\t")):
                    in_paths = False
                if in_paths and "output_dir" in line:
                    _, _, v = line.partition(":")
                    v = v.strip().strip("\"'")
                    if v:
                        return Path(v).expanduser()
    return None


@dataclass(frozen=True)
class T7RunDiscovery:
    """Outcome of scanning ``daily_runs/`` for today's prefetch run.

    ``run_dir`` populated → trade fire proceeds with this run_id.
    ``run_dir is None`` → trade fire must hard-fail (rc=2) because
    using yesterday's recommendations is the entire bug V1-F exists
    to prevent.
    """
    run_dir: Optional[Path]
    run_id: str
    reason: str       # populated when run_dir is None
    candidates_seen: List[str] = field(default_factory=list)
    # Recommendations count when available — cheap diagnostic for
    # the panel + mail body (so operator can see "9 candidates from
    # the 09:00 fire" without opening the file).
    recommendations_count: int = 0


def _find_today_t7_run(
    daily_runs_dir: Path,
    *,
    today_kst_str: Optional[str] = None,
    now_kst: Optional[datetime] = None,
) -> T7RunDiscovery:
    """Find today's T7 prefetch run.

    A ``*_daily`` directory counts as today's T7 prefetch when:

    * its name starts with ``YYYYMMDD_`` matching today's KST date
    * it is NOT a ``*_shadow`` / ``*_dryrun`` sibling
    * ``recommendations.csv`` exists, is non-empty, and parses

    If multiple match (e.g. 09:00 fire + a manual T7 click later),
    we pick the most recent by mtime — the later run is what the
    operator saw most recently in the email, so trading on it is the
    least-surprise choice.

    Returns
    -------
    ``T7RunDiscovery``. NEVER raises; missing dirs / parse errors
    populate ``reason`` so the caller can mail the operator the
    exact diagnostic.
    """
    if not daily_runs_dir.exists() or not daily_runs_dir.is_dir():
        return T7RunDiscovery(
            run_dir=None, run_id="",
            reason=(f"daily_runs_dir does not exist: "
                    f"{daily_runs_dir}"),
        )
    today_str = today_kst_str or v1_arm.today_kst(now=now_kst)
    date_prefix = today_str.replace("-", "")   # 2026-05-28 → 20260528

    candidates: List[Tuple[Path, float, int]] = []
    seen: List[str] = []
    for child in daily_runs_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        seen.append(name)
        if not name.endswith("_daily"):
            continue
        # Skip the shadow/dryrun siblings that t7_runner ignores too;
        # they share a timestamp prefix but never carry a real
        # recommendations.csv.
        if "_shadow" in name or "_dryrun" in name:
            continue
        if not name.startswith(date_prefix + "_"):
            continue
        rec = child / "recommendations.csv"
        if not rec.exists() or rec.stat().st_size == 0:
            continue
        # Sanity-parse: at least one data line beyond the header.
        try:
            with open(rec, "r", encoding="utf-8") as f:
                header = f.readline()
                first_data = f.readline()
            if not header.strip() or not first_data.strip():
                continue
            # Cheap line count for diagnostic surface (the panel /
            # mail show "from the 09:00 fire — 91 recs").
            with open(rec, "r", encoding="utf-8") as f:
                lines = sum(1 for _ in f)
            data_rows = max(0, lines - 1)
        except OSError:
            continue
        candidates.append(
            (child, child.stat().st_mtime, data_rows))

    if not candidates:
        return T7RunDiscovery(
            run_dir=None, run_id="",
            reason=(f"no T7 prefetch run for {today_str} in "
                    f"{daily_runs_dir} (the 09:00 fire either did "
                    f"not run, halted, or produced an empty "
                    f"recommendations.csv; check "
                    f"v1_t7_launchd.err.log)"),
            candidates_seen=seen,
        )
    # Newest mtime wins (manual T7 later in the day overrides the
    # 09:00 fire — operator likely re-ran for a reason).
    candidates.sort(key=lambda c: c[1], reverse=True)
    chosen, _mt, recs = candidates[0]
    return T7RunDiscovery(
        run_dir=chosen, run_id=chosen.name,
        reason="", candidates_seen=seen,
        recommendations_count=recs,
    )


def _maybe_mail(
    result: V1RunResult,
    output_dir: Optional[Path],
    log: Callable[[str], None],
    *, send_mail: bool, profile: str,
    fire_label: str = "",
) -> None:
    """Send the R11B EOD digest. Failures here NEVER change rc — the
    pipeline outcome is whatever the stages decided. Mail problems
    are logged for triage but don't escalate.

    ``run_dir is None`` case (V1-F)
    -------------------------------
    The trade fire's discover-T7 stage can hard-fail BEFORE any
    run_dir is resolved (e.g. no morning T7 prefetch ran today).
    We still want the operator to get an alert email — otherwise
    they'd only see the failure by opening the panel after the
    fact. We pass a placeholder run_dir (the output_dir itself);
    ``compose_run_summary_mail`` reads attachments best-effort so
    missing files just yield a body without attachments, never a
    crash."""
    if not send_mail:
        log("mail dispatch: skipped (--no-mail)")
        return
    run_dir_for_mail = result.run_dir
    if run_dir_for_mail is None:
        if output_dir is None:
            log("mail dispatch: skipped (no run_dir and no output_dir)")
            return
        # Use the output_dir as the placeholder; attachments are
        # best-effort and the body still carries rc + halt_reason
        # which is the actionable signal.
        run_dir_for_mail = Path(output_dir)
    try:
        mail_cfg = smtp_mailer.resolve_smtp_config()
        mr = smtp_mailer.send_run_summary_mail(
            run_dir=run_dir_for_mail,
            run_id=result.run_id or "<v1-no-run-id>",
            profile=profile,
            overall_rc=result.rc,
            halt_reason=result.halt_reason,
            duration_sec=result.duration_sec,
            stage_outcomes=result.stages,
            t7_payload=result.t7_payload,
            fire_label=fire_label,
        )
        src = mail_cfg.source
        if mr.ok:
            if mr.dry_run:
                log(f"R11B mail dry-run via {src} "
                    f"({mr.bytes_sent} bytes composed)")
            else:
                log(f"R11B mail sent via {src} "
                    f"to {', '.join(mail_cfg.to_addrs)} "
                    f"({mr.bytes_sent} bytes)")
        else:
            log(f"R11B mail SKIPPED ({src}) — {mr.reason}")
    except Exception as e:  # noqa: BLE001
        log(f"R11B mail crashed (ignored): "
            f"{type(e).__name__}: {e}")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="v1_runner",
        description="V1 — Headless T7 → R11A → R11B pipeline.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser(
        "arm-today",
        help="green-light tonight's V1 launchd run "
             "(writes today's KST arm token)",
    )
    a.add_argument("--note", default="",
                   help="optional audit-trail note recorded in the token")
    a.add_argument("--runtime-dir", type=Path, default=None,
                   help="override default runtime/ dir (tests)")
    a.add_argument("--date-kst", default=None,
                   help="arm a specific KST date (default: today)")

    s = sub.add_parser("status",
                       help="show arm-token + env-gate diagnostics")
    s.add_argument("--runtime-dir", type=Path, default=None)

    # V2-A — standing (continuous) arm. One arm, runs every trading day
    # until disarmed; replaces the daily arm-today for the hands-off
    # trader.
    asd = sub.add_parser(
        "arm-standing",
        help="V2: arm the CONTINUOUS trader — every trading-day fire "
             "runs until you 'disarm-standing'. No daily arming needed.")
    asd.add_argument("--note", default="",
                     help="optional audit-trail note")
    asd.add_argument("--runtime-dir", type=Path, default=None)

    dsd = sub.add_parser(
        "disarm-standing",
        help="V2: stop the continuous trader (remove the standing arm). "
             "Does NOT touch global_halt.")
    dsd.add_argument("--runtime-dir", type=Path, default=None)

    ssd = sub.add_parser(
        "standing-status",
        help="V2: show standing-arm + calendar + halt + lockout state")
    ssd.add_argument("--runtime-dir", type=Path, default=None)

    def _add_pipeline_args(parser: argparse.ArgumentParser) -> None:
        """Shared arg surface for ``run`` (legacy/test-fire) and
        ``trade`` (V1-F 22:35 fire). The discovery vs inline-T7
        difference is hard-wired by which subcommand was used; the
        operator does not pass a flag for it."""
        parser.add_argument("--config", type=Path, default=None,
                             help="phase3 config yaml")
        parser.add_argument("--profile", default="paper",
                             choices=("paper",))
        parser.add_argument("--skip-t7", action="store_true",
                             help="skip T7; --run-id required "
                                  "(legacy; trade subcommand uses "
                                  "auto-discovery instead)")
        parser.add_argument("--run-id", default="",
                             help="operator-supplied run_id when "
                                  "--skip-t7")
        parser.add_argument("--batch-pad-pct", type=float,
                             default=DEFAULT_BATCH_PAD_PCT)
        parser.add_argument("--quote-pad-pct", type=float,
                             default=DEFAULT_QUOTE_PAD_PCT)
        parser.add_argument("--use-quote", action="store_true",
                             help="pull live quotes during "
                                  "generate_intents")
        parser.add_argument("--quote-only", action="store_true",
                             help="R10F-Q1 quote-only mode "
                                  "(implies --use-quote)")
        parser.add_argument("--no-arm", action="store_true",
                             help="bypass the daily arm-token gate "
                                  "(operator override; NOT for launchd)")
        parser.add_argument("--no-mail", action="store_true",
                             help="suppress R11B EOD digest "
                                  "(for ad-hoc tests)")
        parser.add_argument(
            "--no-apply", action="store_true",
            help="paper-submit only; skip T10 apply (use for "
                 "pre-market test-fire — orders submitted before "
                 "US open sit OPEN and t10 policy-aborts on "
                 "unfilled orders)")
        parser.add_argument("--runtime-dir", type=Path, default=None)
        parser.add_argument("--output-dir", type=Path, default=None,
                             help="override paths.output_dir from "
                                  "config")

    r = sub.add_parser(
        "run", help="legacy V1 pipeline (T7 + trade in one process). "
                    "Used by panel test-fire and ad-hoc CLI runs")
    _add_pipeline_args(r)

    t = sub.add_parser(
        "trade",
        help="V1-F 22:35 trade fire — discovers today's T7 prefetch "
             "run on disk and runs intents + paper_submit + apply. "
             "Hard-fails (rc=2) if no T7 prefetch ran today.",
    )
    _add_pipeline_args(t)

    tp = sub.add_parser(
        "t7-prefetch",
        help="V1-F 09:00 fire — generates recommendations + sends "
             "T7 own email. No broker contact.",
    )
    tp.add_argument("--config", type=Path, default=None,
                    help="phase3 config yaml (default: config.yaml)")
    tp.add_argument("--no-arm", action="store_true",
                    help="bypass the V2 trading-calendar / halt gate "
                         "(ad-hoc CLI override; NOT for launchd)")

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv else None)

    # Hydrate ``os.environ`` from ``.env`` BEFORE any subcommand runs.
    # See ``_hydrate_env_from_dotenv``'s docstring for the launchd
    # contract — without this, the 22:35 fire halts at T7 with
    # ``FMP_API_KEY not found`` because launchd's gui session does
    # NOT inherit ``~/.zshrc`` exports.
    added = _hydrate_env_from_dotenv()
    if added:
        # NOTE: don't echo values (these are secrets). Keys only.
        _log(f"hydrated {len(added)} key(s) from .env: "
             f"{','.join(sorted(added))}")

    if args.cmd == "arm-today":
        tok = v1_arm.write_arm_token(
            date_kst=args.date_kst, runtime_dir=args.runtime_dir,
            note=args.note,
        )
        _log(f"armed {tok.date_kst} by {tok.armed_by} on "
             f"{tok.hostname}"
             + (f" — {tok.note}" if tok.note else ""))
        _log(f"token: "
             f"{v1_arm.token_path(date_kst=tok.date_kst, runtime_dir=args.runtime_dir)}")
        return 0

    if args.cmd == "arm-standing":
        p = v1_arm.write_standing_arm(
            runtime_dir=args.runtime_dir, note=args.note)
        _log(f"STANDING ARM set: the continuous trader will run EVERY "
             f"NYSE trading day until you run 'disarm-standing'.")
        _log(f"token: {p}")
        if global_halt.is_halted():
            _log("WARNING: global_halt is currently SET — fires will skip "
                 "until you Clear it in the panel.")
        return 0

    if args.cmd == "disarm-standing":
        removed = v1_arm.clear_standing_arm(runtime_dir=args.runtime_dir)
        if removed:
            _log("STANDING ARM cleared — continuous trader stopped. "
                 "(global_halt untouched.)")
        else:
            _log("no standing arm was set (nothing to do).")
        return 0

    if args.cmd == "standing-status":
        rt = args.runtime_dir
        standing = v1_arm.read_standing_arm(runtime_dir=rt)
        session_d = v1_arm.today_kst_date()
        is_td = trading_calendar.is_trading_day(session_d)
        hs = global_halt.read_halt()
        _log(f"today (KST/ET session): {session_d.isoformat()} "
             f"— {'TRADING DAY' if is_td else 'CLOSED (weekend/holiday)'}")
        if standing is not None:
            _log(f"standing arm: ACTIVE "
                 f"(armed_by={standing.get('armed_by', '?')} "
                 f"at {standing.get('armed_at', '?')})")
        else:
            _log("standing arm: OFF — run 'arm-standing' to enable "
                 "continuous trading")
        _log(f"global_halt: {'SET — ' + (hs.reason or '(no reason)') if hs.halted else 'clear'}")
        hist = auto_halt.read_history(runtime_dir=rt, tail=5)
        if hist:
            _log("recent fires (last 5):")
            for rec in hist:
                _log(f"  {rec.get('session_date_kst','?')} "
                     f"{rec.get('kind','?'):5s} rc={rec.get('rc','?')} "
                     f"total={rec.get('total_after')}")
            decision = auto_halt.evaluate_lockout(
                auto_halt.read_history(runtime_dir=rt),
                session_date=session_d)
            _log(f"lockout eval: {'WOULD TRIP — ' + decision.message if decision.tripped else 'OK'}")
            _log(f"  detail: {decision.detail}")
        else:
            _log("no fire history yet")
        armed_ok = (standing is not None) and is_td and not hs.halted
        return 0 if armed_ok else 1

    if args.cmd == "status":
        ac = v1_arm.require_armed_for_today(
            runtime_dir=args.runtime_dir)
        _log(f"today (KST): {ac.date_kst}")
        _log(f"armed: {'YES' if ac.ok else 'NO'} — {ac.reason or 'OK'}")
        if ac.token:
            _log(f"  armed_at: {ac.token.armed_at}")
            _log(f"  armed_by: {ac.token.armed_by}@{ac.token.hostname}")
        gate_err = check_env_gates(os.environ)
        _log(f"env gates: {'OK' if not gate_err else gate_err}")
        return 0 if ac.ok and not gate_err else 1

    if args.cmd in ("run", "trade"):
        # V1-I — env-driven pricing config. The launchd plist sets
        # these (mirroring LAUNCHD_ENV); a CLI flag can still force
        # ``--use-quote`` / ``--quote-only`` on. ``os.environ`` was
        # already hydrated from .env above. buy-pad / gap-filter have
        # no CLI flag (env-only) to keep the operator surface small.
        use_quote = args.use_quote or _gate_truthy(os.environ, USE_QUOTE_ENV)
        quote_only = args.quote_only or _gate_truthy(os.environ, QUOTE_ONLY_ENV)
        buy_quote_pad_pct = _env_float(os.environ, BUY_QUOTE_PAD_ENV)
        gap_filter_max_pct = _env_float(os.environ, GAP_FILTER_ENV)
        if quote_only and not use_quote:
            _log("quote_only implies use_quote (auto-enabling)")
            use_quote = True
        if use_quote:
            _log(f"V1-I pricing: use_quote=True quote_only={quote_only} "
                 f"buy_quote_pad_pct={buy_quote_pad_pct} "
                 f"gap_filter_max_pct={gap_filter_max_pct}")
        # The ``trade`` subcommand is the V1-F 22:35 fire entry
        # point: it discovers this morning's T7 prefetch run and
        # never re-runs T7 inline. The legacy ``run`` subcommand
        # keeps the old (T7 + trade in one process) behaviour for
        # the panel test-fire and ad-hoc operator CLI use.
        is_trade = (args.cmd == "trade")
        r = run_v1_pipeline(
            config_path=args.config,
            skip_t7=args.skip_t7,
            run_id_override=args.run_id,
            profile=args.profile,
            batch_pad_pct=args.batch_pad_pct,
            quote_pad_pct=args.quote_pad_pct,
            use_quote=use_quote,
            quote_only=quote_only,
            buy_quote_pad_pct=buy_quote_pad_pct,
            gap_filter_max_pct=gap_filter_max_pct,
            require_arm_token=(not args.no_arm),
            output_dir_override=args.output_dir,
            runtime_dir=args.runtime_dir,
            send_mail=(not args.no_mail),
            apply_t10=(not args.no_apply),
            discover_today_t7=is_trade,
            fire_label=("trade" if is_trade else "run"),
        )
        # V2-B — record real trade fires + evaluate auto-lockout. Only for
        # the gated 22:35 ``trade`` fire that actually attempted to trade
        # (calendar/halt/arm skips set skipped_by_gate and are ignored).
        if is_trade and not args.no_arm and not r.skipped_by_gate:
            record_fire_and_evaluate_lockout(
                r, runtime_dir=args.runtime_dir)
        return r.rc

    if args.cmd == "t7-prefetch":
        # V2-A — on non-trading days we DEMOTE the morning prefetch to a
        # dry-run preview instead of skipping it outright: the operator still
        # gets the recommendation email, but daily_runner mutates NO persistent
        # state (no recommendations archive / grace counters / daily log), so
        # weekend/holiday reruns cannot inflate grace parity. ``global_halt``
        # remains a hard skip (the operator explicitly stopped everything).
        # ``--no-arm`` (ad-hoc CLI) bypasses the calendar gate entirely.
        dry_run_preview = False
        if not args.no_arm:
            if global_halt.is_halted():
                _log("t7-prefetch SKIP — global_halt is set; exit rc=0")
                return 0
            session_d = v1_arm.today_kst_date()
            if not trading_calendar.is_trading_day(session_d):
                _log(f"t7-prefetch PREVIEW — {session_d.isoformat()} is not a "
                     f"NYSE trading day (weekend/holiday); running dry-run "
                     f"(mail only, no state mutation); exit rc=0")
                dry_run_preview = True
        r = run_t7_prefetch(config_path=args.config, dry_run=dry_run_preview)
        # A non-trading-day preview is never an actionable failure for the
        # daemon: keep launchd quiet (it retries only on non-zero exits).
        if dry_run_preview:
            return 0
        return r.rc

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
