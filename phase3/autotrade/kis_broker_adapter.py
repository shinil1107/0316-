"""KIS Broker Adapter — Step 1 skeleton (env + token cache + audit + mock).

This file is intentionally a SINGLE FILE for the first iteration (Codex spec
§17.1). Internal sections below mark logical modules; they can be split out
later if the file grows past ~1500 lines.

SECTIONS
--------
    1. Constants / endpoints (paper vs live)
    2. EnvConfig — load + validate .env
    3. Mask helpers — for safe logging
    4. AuditLogger — per-day jsonl append
    5. TokenCache — load/save + expiry margin (real fetch wired in Step 2)
    6. SafetyGuard — dry_run + confirm_live + buy_only + global halt
    7. BrokerError — typed exceptions
    8. Quote / Position / Cash / Order dataclasses (broker types)
    9. KisBrokerAdapter — real adapter (Step 2 will wire REST calls)
   10. MockBrokerAdapter — for tests / skeleton verification (Step 1)
   11. _self_check() — sanity entry point: env load, mock round-trip, audit write
   12. CLI: `python -m phase3.autotrade.kis_broker_adapter --self-check`

STEP 1 ACCEPTANCE (this iteration)
----------------------------------
- .env is loaded with masking; missing required vars raise typed error
- TokenCache reads/writes ~/.kis_token_cache.json with expiry margin
- AuditLogger appends jsonl with secrets masked
- SafetyGuard correctly blocks real live orders unless all 3 conditions true
- MockBrokerAdapter exercises all interface methods without any network call
- `--self-check` runs end-to-end without touching KIS at all

STEP 2 (this iteration, secrets-bound, READ-ONLY)
--------------------------------------------------
- POST /oauth2/tokenP           → ensure_token()
- GET  /uapi/overseas-price/v1/quotations/price                 → get_quote()
- GET  /uapi/overseas-stock/v1/trading/inquire-balance          → get_positions()
- GET  /uapi/overseas-stock/v1/trading/inquire-psamount         → get_cash()
- GET  /uapi/overseas-stock/v1/trading/inquire-ccnl             → get_order_history()
- GET  /uapi/overseas-stock/v1/trading/inquire-nccs             → get_open_orders()  (R3 P1.A)
- All calls go through `_Http` which mirrors every request to the
  AuditLogger AND optionally to stdout (verbose mode) for live observation.

STEP 3 (next, write path)
-------------------------
- POST /uapi/overseas-stock/v1/trading/order (BUY first, paper LIMIT only)
- Paper TR_IDs: VTTT1002U (buy), VTTT1001U (sell). Paper requires ORD_DVSN=00.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent  # /Users/shin-il/PyCharmMiscProject/0316-
_DEFAULT_DOTENV = _REPO_ROOT / ".env"


# ──────────────────────────────────────────────────────────────────────
# 1. Constants — KIS endpoints per env
# ──────────────────────────────────────────────────────────────────────
KIS_HOSTS: Dict[str, str] = {
    # 모의투자 (paper). KIS Developers 공식 베이스 URL.
    "paper": "https://openapivts.koreainvestment.com:29443",
    # 실거래 (live).
    "live":  "https://openapi.koreainvestment.com:9443",
}

# Token TTL fallback if KIS response is missing `expires_in`. KIS access tokens
# typically last 24h; we re-issue with a healthy margin.
KIS_TOKEN_TTL_FALLBACK_SEC = 23 * 60 * 60  # 23h
# Refresh tokens earlier than expiry by this margin so we never use an expired
# one in flight.
KIS_TOKEN_REFRESH_MARGIN_SEC = 30 * 60  # 30 min

# Default paths (overrideable via env).
DEFAULT_TOKEN_CACHE = Path.home() / ".kis_token_cache.json"
DEFAULT_AUDIT_DIR   = Path.home() / ".kis_audit"

# KIS REST endpoint paths.
EP_TOKEN          = "/oauth2/tokenP"
EP_QUOTE_PRICE    = "/uapi/overseas-price/v1/quotations/price"
EP_INQ_BALANCE    = "/uapi/overseas-stock/v1/trading/inquire-balance"
EP_INQ_PSAMOUNT   = "/uapi/overseas-stock/v1/trading/inquire-psamount"
EP_INQ_CCNL       = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
EP_INQ_NCCS       = "/uapi/overseas-stock/v1/trading/inquire-nccs"   # Round 3 (P1.A)
EP_ORDER          = "/uapi/overseas-stock/v1/trading/order"
EP_ORDER_RVSECNCL = "/uapi/overseas-stock/v1/trading/order-rvsecncl"  # Round 8 (cancel path)

# TR_ID matrix. (paper, live). Paper is what we use during Step 2 / Step 3 v0.
# Source: KIS official docs + reference impl (geongi-im/kis-us-auto-trading).
TR: Dict[str, Dict[str, str]] = {
    "quote_price":      {"paper": "HHDFS00000300", "live": "HHDFS00000300"},
    "inquire_balance":  {"paper": "VTTS3012R",     "live": "TTTS3012R"},
    "inquire_psamount": {"paper": "VTTS3007R",     "live": "TTTS3007R"},
    "inquire_ccnl":     {"paper": "VTTS3035R",     "live": "TTTS3035R"},
    # Round 3: inquire-nccs (해외주식 미체결내역). Paper TR is the standard
    # V-prefix mirror of the live TR (TTTS3018R) per KIS naming convention; if
    # the paper server refuses it, the call will surface rt_cd!=0 + msg1 and
    # we'll patch the matrix from observed evidence rather than guessing twice.
    "inquire_nccs":     {"paper": "VTTS3018R",     "live": "TTTS3018R"},
    "order_buy":        {"paper": "VTTT1002U",     "live": "TTTT1002U"},
    "order_sell":       {"paper": "VTTT1001U",     "live": "TTTT1001U"},
    # Round 8 — overseas modify/cancel. The endpoint and TR_ID matrix is
    # confirmed against the geongi-im/kis-us-auto-trading reference impl
    # and the KIS Developers portal "해외주식 주문/계좌 → 정정취소" page.
    #         Modify (TTTT1003U / VTTT1003U) is deferred to a later round; today's
    # R8 slice only needs the cancel path.
    # Cancel payload contract (per KIS official LLM example
    # `examples_llm/overseas_stock/order_rvsecncl/order_rvsecncl.py`):
    #   CANO, ACNT_PRDT_CD, OVRS_EXCG_CD, PDNO,
    #   ORGN_ODNO, RVSE_CNCL_DVSN_CD ("02"=취소), ORD_QTY,
    #   OVRS_ORD_UNPR ("0" on cancel), MGCO_APTM_ODNO (""), ORD_SVR_DVSN_CD ("0")
    "order_cancel":     {"paper": "VTTT1004U",     "live": "TTTT1004U"},
}

# Exchange code mapping.
#   The QUOTE endpoint uses 3-char codes (NAS/NYS/AMS/HKS/SHS/SZS/TSE),
#   while the TRADING endpoints use 4-char codes (NASD/NYSE/AMEX/SEHK/SHAA/SZAA/TKSE).
#   We let callers pass the *trading* 4-char code as canonical and convert when needed.
EXCD_QUOTE_FROM_TRADE = {
    "NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
    "SEHK": "HKS", "SHAA": "SHS", "SZAA": "SZS",
    "TKSE": "TSE",
}
VALID_TRADE_MARKETS = frozenset(EXCD_QUOTE_FROM_TRADE)


# ──────────────────────────────────────────────────────────────────────
# 2. EnvConfig — load + validate .env
# ──────────────────────────────────────────────────────────────────────
class EnvConfigError(RuntimeError):
    """Raised when .env is missing or required vars are absent / malformed."""


@dataclass(frozen=True)
class EnvConfig:
    app_key: str
    app_secret: str
    account_no: str
    account_product_code: str
    env_name: str           # 'paper' | 'live'
    confirm_live: bool      # True only if env value is literally 'true' (live capital lock)
    # Paper-only second lock (Codex review P1-A). When env=paper and dry_run=False,
    # transmission to the paper broker still requires KIS_PAPER_SUBMIT_OK == 'true'.
    # Default False so a stray dry_run=False call cannot accidentally hit the paper
    # server. Live submissions remain governed by `confirm_live` (independent of this).
    paper_submit_ok: bool
    # R8 paper cancel gate. Mirrors paper_submit_ok: cancel transmission to
    # the paper broker requires KIS_PAPER_CANCEL_OK == 'true'. Independent
    # from submit_ok so that an operator can grant cancel permission to a
    # session that is *not* allowed to submit new orders (e.g. cleanup mode).
    paper_cancel_ok: bool = False
    token_cache_path: Path = DEFAULT_TOKEN_CACHE
    log_dir: Path = DEFAULT_AUDIT_DIR

    @property
    def base_url(self) -> str:
        return KIS_HOSTS[self.env_name]

    @property
    def is_paper(self) -> bool:
        return self.env_name == "paper"

    @property
    def is_live(self) -> bool:
        return self.env_name == "live"

    def masked(self) -> Dict[str, Any]:
        """Return a logging-safe dict with secrets masked."""
        return {
            "app_key": _mask_secret(self.app_key),
            "app_secret": _mask_secret(self.app_secret),
            "account_no": self.account_no,             # not secret
            "account_product_code": self.account_product_code,
            "env_name": self.env_name,
            "confirm_live": self.confirm_live,
            "paper_submit_ok": self.paper_submit_ok,
            "paper_cancel_ok": self.paper_cancel_ok,
            "base_url": self.base_url,
            "token_cache_path": str(self.token_cache_path),
            "log_dir": str(self.log_dir),
        }


def _read_dotenv(path: Path) -> Dict[str, str]:
    """Parse a minimal `.env`. Supports `KEY=VALUE`, comments with `#`, quoted
    values, and empty values. No shell expansion."""
    if not path.exists():
        raise EnvConfigError(f".env not found at {path}")
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def load_env_config(
    dotenv_path: Optional[Path] = None,
    *,
    overrides: Optional[Dict[str, str]] = None,
) -> EnvConfig:
    """Load .env + optional overrides and return an EnvConfig.

    Order of resolution per key:
        1. `overrides` dict (for tests)
        2. `os.environ` (so users can export at shell)
        3. dotenv file content
    """
    path = dotenv_path or _DEFAULT_DOTENV
    dotenv = _read_dotenv(path) if path.exists() else {}

    def _get(key: str, default: str = "") -> str:
        if overrides and key in overrides:
            return overrides[key]
        return os.environ.get(key) or dotenv.get(key) or default

    app_key = _get("KIS_APP_KEY")
    app_secret = _get("KIS_APP_SECRET")
    account_no = _get("KIS_ACCOUNT_NO")
    product_code = _get("KIS_ACCOUNT_PRODUCT_CODE", "01")
    env_name = _get("KIS_ENV", "paper").lower()
    confirm_live = _get("KIS_CONFIRM_LIVE", "false").strip().lower() == "true"
    paper_submit_ok = _get("KIS_PAPER_SUBMIT_OK", "false").strip().lower() == "true"
    paper_cancel_ok = _get("KIS_PAPER_CANCEL_OK", "false").strip().lower() == "true"
    token_cache_raw = _get("KIS_TOKEN_CACHE_PATH")
    log_dir_raw = _get("KIS_LOG_DIR")

    missing = [k for k, v in (
        ("KIS_APP_KEY", app_key),
        ("KIS_APP_SECRET", app_secret),
        ("KIS_ACCOUNT_NO", account_no),
    ) if not v]
    if missing:
        raise EnvConfigError(
            f"required .env vars missing: {missing}. Check {path}."
        )
    if env_name not in KIS_HOSTS:
        raise EnvConfigError(
            f"KIS_ENV must be one of {list(KIS_HOSTS)}, got {env_name!r}"
        )
    if not re.fullmatch(r"\d{8}", account_no):
        raise EnvConfigError(
            f"KIS_ACCOUNT_NO must be 8 digits (front portion only), got {account_no!r}"
        )
    if not re.fullmatch(r"\d{2}", product_code):
        raise EnvConfigError(
            f"KIS_ACCOUNT_PRODUCT_CODE must be 2 digits, got {product_code!r}"
        )

    token_cache = Path(os.path.expanduser(token_cache_raw)) if token_cache_raw else DEFAULT_TOKEN_CACHE
    log_dir = Path(os.path.expanduser(log_dir_raw)) if log_dir_raw else DEFAULT_AUDIT_DIR

    return EnvConfig(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        account_product_code=product_code,
        env_name=env_name,
        confirm_live=confirm_live,
        paper_submit_ok=paper_submit_ok,
        paper_cancel_ok=paper_cancel_ok,
        token_cache_path=token_cache,
        log_dir=log_dir,
    )


# ──────────────────────────────────────────────────────────────────────
# 3. Mask helpers
# ──────────────────────────────────────────────────────────────────────
# Sensitive substrings: ANY dict key containing one of these (case-insensitive)
# triggers masking. We use substring match rather than exact match so users who
# accidentally log a payload with a key like `sample_secret`, `app_key`,
# `bearer_token`, etc. still get protected.
_SENSITIVE_SUBSTRINGS = (
    "secret", "token", "password", "appkey", "app_key",
    "personalseckey", "personal_seckey", "hashkey", "hash_key",
    "authorization", "api_key", "apikey", "pin", "bearer",
)


def _is_sensitive_key(k: Any) -> bool:
    klower = str(k).lower()
    return any(s in klower for s in _SENSITIVE_SUBSTRINGS)


def _mask_secret(value: Optional[str]) -> str:
    """Return a length-preserving mask: 'PSaIz…GwGwl28akQ' → 'PSai***akQ'."""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}***{s[-3:]}"


def _mask_obj(o: Any) -> Any:
    """Recursively scan dict/list and mask values whose KEY name suggests they
    carry a secret (api keys, tokens, passwords, etc.).

    Substring match, case-insensitive. Conservative: false positives are
    cheap (mask shown instead of value), false negatives leak credentials.
    """
    if isinstance(o, dict):
        out: Dict[Any, Any] = {}
        for k, v in o.items():
            if _is_sensitive_key(k):
                if isinstance(v, (str, int, float)) or v is None:
                    out[k] = _mask_secret(v if isinstance(v, str) else str(v) if v is not None else None)
                else:
                    out[k] = "***"
            else:
                out[k] = _mask_obj(v)
        return out
    if isinstance(o, list):
        return [_mask_obj(x) for x in o]
    return o


# ──────────────────────────────────────────────────────────────────────
# 4. AuditLogger — per-day jsonl append
# ──────────────────────────────────────────────────────────────────────
class AuditLogger:
    """Append-only audit trail. One file per UTC date. JSONL.

    Fields (Codex §17.5):
        ts, env, endpoint, method, request_id, dry_run, http_status,
        latency_ms, request_summary, response_summary, error
    """

    def __init__(self, log_dir: Path, *, env_name: str):
        self.log_dir = log_dir
        self.env_name = env_name
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_today(self) -> Path:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"{d}.jsonl"

    def log(
        self,
        *,
        endpoint: str,
        method: str,
        request_id: str,
        dry_run: bool,
        http_status: Optional[int] = None,
        latency_ms: Optional[float] = None,
        request_summary: Optional[Dict[str, Any]] = None,
        response_summary: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "env": self.env_name,
            "endpoint": endpoint,
            "method": method,
            "request_id": request_id,
            "dry_run": bool(dry_run),
            "http_status": http_status,
            "latency_ms": latency_ms,
            "request_summary": _mask_obj(request_summary or {}),
            "response_summary": _mask_obj(response_summary or {}),
            "error": error,
        }
        if extra:
            record["extra"] = _mask_obj(extra)
        path = self._path_for_today()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ──────────────────────────────────────────────────────────────────────
# 5. TokenCache
# ──────────────────────────────────────────────────────────────────────
@dataclass
class TokenRecord:
    access_token: str
    token_type: str
    issued_at: str        # ISO-8601 UTC
    expires_at: str       # ISO-8601 UTC
    env: str

    def is_expired(self, *, margin_sec: int = KIS_TOKEN_REFRESH_MARGIN_SEC) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= (exp - timedelta(seconds=margin_sec))

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


class TokenCache:
    """Read/write a TokenRecord to/from a local json file. The file is created
    with mode 600 (owner-only) to keep the bearer token from leaking via FS."""

    def __init__(self, path: Path, *, env_name: str):
        self.path = path
        self.env_name = env_name

    def load(self) -> Optional[TokenRecord]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("env") != self.env_name:
            # Token issued for a different env (paper vs live) — never reuse.
            return None
        try:
            rec = TokenRecord(**{
                k: data[k] for k in ("access_token", "token_type",
                                     "issued_at", "expires_at", "env")
            })
        except (KeyError, TypeError):
            return None
        return rec

    def save(self, rec: TokenRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec.to_json(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ──────────────────────────────────────────────────────────────────────
# 6. SafetyGuard
# ──────────────────────────────────────────────────────────────────────
class SafetyError(RuntimeError):
    """Raised when an action would violate safety constraints (live-without-confirm,
    sell-in-buy-only-mode, halted broker, etc.)."""


@dataclass
class SafetyState:
    """Mutable runtime safety state. Default is the most conservative: no live
    orders, buy-only, not halted."""
    buy_only_mode: bool = True
    global_halt: bool = False
    cancel_all_pending: bool = False  # if true, no new orders accepted


class SafetyGuard:
    """Enforces the v0 invariants from Codex §17.6 and §7.4.

    Per Codex review P1-A, broker submission is split into TWO independent
    permission gates:

    `allow_paper_submit(dry_run)` — paper transmission
        ALL OF: cfg.env_name == 'paper', dry_run is False, cfg.paper_submit_ok

    `allow_live_capital(dry_run)` — live capital transmission
        ALL OF: cfg.env_name == 'live', dry_run is False, cfg.confirm_live

    Both gates additionally require: NOT global_halt and NOT cancel_all_pending.

    The legacy `allow_live_order` is kept as an alias for `allow_live_capital`
    so existing call sites continue to compile while we migrate Step 4.

    Anything failing both gates means: skip transmission, log a dry-run record.
    """

    def __init__(self, cfg: EnvConfig, *, state: Optional[SafetyState] = None):
        self.cfg = cfg
        self.state = state or SafetyState()

    def _state_blocks(self) -> bool:
        return self.state.global_halt or self.state.cancel_all_pending

    def allow_paper_submit(self, *, dry_run: bool) -> bool:
        return (
            self.cfg.is_paper
            and (not dry_run)
            and self.cfg.paper_submit_ok
            and (not self._state_blocks())
        )

    def allow_live_capital(self, *, dry_run: bool) -> bool:
        return (
            self.cfg.is_live
            and (not dry_run)
            and self.cfg.confirm_live
            and (not self._state_blocks())
        )

    # Legacy alias — kept so older callers (and the original probe path) keep
    # working without churn. New code should call `allow_live_capital`.
    def allow_live_order(self, *, dry_run: bool) -> bool:
        return self.allow_live_capital(dry_run=dry_run)

    def submit_mode(self, *, dry_run: bool) -> str:
        """Resolve to one of: 'dry_run' | 'paper_submit' | 'live_capital'.

        Used by `place_order` to pick the transmission branch. Always returns
        a value — never raises. Callers can still pre-empt with
        `assert_can_submit` which raises on buy_only / halt.
        """
        if self.allow_live_capital(dry_run=dry_run):
            return "live_capital"
        if self.allow_paper_submit(dry_run=dry_run):
            return "paper_submit"
        return "dry_run"

    def submit_decision(self, *, dry_run: bool) -> Tuple[str, str]:
        """Same as `submit_mode`, but also returns a human-readable reason
        the operator can log/print alongside the mode."""
        mode = self.submit_mode(dry_run=dry_run)
        if mode == "live_capital":
            return mode, "env=live, dry_run=False, confirm_live=true"
        if mode == "paper_submit":
            return mode, "env=paper, dry_run=False, paper_submit_ok=true"
        # dry_run — explain WHY each path was blocked.
        why: List[str] = []
        if dry_run:
            why.append("dry_run=True")
        if self.state.global_halt:
            why.append("global_halt=on")
        if self.state.cancel_all_pending:
            why.append("cancel_all_pending=on")
        if self.cfg.is_paper and not self.cfg.paper_submit_ok:
            why.append("env=paper but paper_submit_ok=false")
        if self.cfg.is_live and not self.cfg.confirm_live:
            why.append("env=live but confirm_live=false")
        return mode, ("; ".join(why) or "no submit gate satisfied")

    def assert_can_submit(self, *, side: str, dry_run: bool) -> None:
        if self.state.global_halt:
            raise SafetyError("global_halt is on — no orders allowed")
        if self.state.cancel_all_pending:
            raise SafetyError("cancel_all_pending — adapter is in panic-stop mode")
        if self.state.buy_only_mode and side.upper() != "BUY":
            raise SafetyError(
                f"buy_only_mode is on — side={side!r} blocked. Disable buy_only_mode to sell."
            )
        if self.cfg.is_live and (not dry_run) and (not self.cfg.confirm_live):
            raise SafetyError(
                "KIS_ENV=live but KIS_CONFIRM_LIVE != true — live orders blocked"
            )
        if self.cfg.is_paper and (not dry_run) and (not self.cfg.paper_submit_ok):
            raise SafetyError(
                "KIS_ENV=paper but KIS_PAPER_SUBMIT_OK != true — paper submit blocked. "
                "Set KIS_PAPER_SUBMIT_OK=true in .env to enable paper-broker transmission."
            )


# ──────────────────────────────────────────────────────────────────────
# 7. BrokerError — typed
# ──────────────────────────────────────────────────────────────────────
class BrokerError(RuntimeError):
    """Top-level error from any broker adapter."""


class BrokerAuthError(BrokerError):
    """Failed to issue/refresh access token."""


class BrokerNetworkError(BrokerError):
    """Network / HTTP / timeout error."""


class BrokerResponseError(BrokerError):
    """KIS API returned a logical error (rt_cd != '0')."""


# ──────────────────────────────────────────────────────────────────────
# 8. Broker types — minimal v0 schemas
# ──────────────────────────────────────────────────────────────────────
@dataclass
class Quote:
    symbol: str
    market: str           # e.g. 'NASD', 'NYSE', 'AMEX'
    last: float
    bid: Optional[float]
    ask: Optional[float]
    asof: str             # ISO-8601, broker's quote time


@dataclass
class Position:
    symbol: str
    market: str
    qty: float
    avg_price: float
    asof: str


@dataclass
class CashBalance:
    base_ccy: str         # 'USD' for overseas, 'KRW' for domestic
    total: float
    available: float      # buying power
    asof: str


@dataclass
class OrderIntent:
    symbol: str
    market: str           # 'NASD' | 'NYSE' | 'AMEX' | 'TSE'…
    side: str             # 'BUY' | 'SELL'
    qty: int
    order_type: str = "MARKET"   # v0: MARKET (later: 'LIMIT', etc.)
    limit_price: Optional[float] = None
    client_order_id: str = field(default_factory=lambda: f"co-{uuid.uuid4().hex[:12]}")
    note: str = ""
    # R10E — recommendations.csv RecRowId for the intent. The
    # autotrade pipeline must thread this through manage_order so
    # OrderStore.log_transition writes a real rid (not 0), otherwise
    # t10_applicator cannot match broker fills back to the
    # recommendation row. Default 0 stays compatible with the older
    # callers (probe scripts, tests) that don't care.
    rec_row_id: int = 0

    def validate(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"OrderIntent.side must be BUY/SELL, got {self.side!r}")
        if self.qty <= 0:
            raise ValueError(f"OrderIntent.qty must be positive, got {self.qty}")
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("LIMIT order requires limit_price")


@dataclass
class PlacedOrder:
    client_order_id: str
    broker_order_id: Optional[str]   # None if dry-run / not yet ack'd
    status: str                      # 'dry_run' | 'submitted' | 'rejected'
    intent: OrderIntent
    submitted_at: str
    raw_response_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CancelResult:
    """R8 — result of `KisBrokerAdapter.cancel_order(...)`.

    `broker_order_id` is the ORIGINAL order ODNO we asked KIS to cancel
    (i.e. the value that went into the `ORGN_ODNO` payload field), in
    the broker's surface form (typically zero-padded for paper).
    `cancel_order_id` is the *new* ODNO assigned by KIS to the cancel
    instruction itself, parsed from the `output.ODNO` of the cancel
    response. It is `None` for dry-runs and for rejected cancels.
    """
    broker_order_id: str
    cancel_order_id: Optional[str]
    accepted: bool
    dry_run: bool
    symbol: str
    market: str
    qty: int
    submitted_at: str
    payload: Dict[str, Any] = field(default_factory=dict)
    raw_response_summary: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class OpenOrder:
    """Round 3 (P1.A): normalized view of one row from `inquire-nccs`
    (해외주식 미체결내역).

    KIS returns Korean-named raw fields plus a few transport quirks
    (`sll_buy_dvsn_cd` is `01`=SELL / `02`=BUY, `ord_qty` vs `ft_ccld_qty3`
    for fills, etc.). We keep the raw dict on the side for diagnostics but
    surface a stable, codepath-friendly shape that the echo poller and
    future state-model can rely on.
    """
    broker_order_id: str         # ODNO
    ord_dt: str                  # YYYYMMDD as KIS returns it
    ord_tmd: str                 # HHMMSS as KIS returns it
    symbol: str                  # PDNO
    market: str                  # OVRS_EXCG_CD ('NASD' / 'NYSE' / 'AMEX' / …)
    side: str                    # 'BUY' | 'SELL' | 'UNKNOWN'
    qty_order: float             # ORD_QTY
    qty_filled: float            # cumulative fills
    qty_remaining: float         # NCCS_QTY (unfilled)
    limit_price: float           # OVRS_ORD_UNPR / FT_ORD_UNPR3
    status_text: str             # PRCS_STAT_NAME or similar; empty if unknown
    raw: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# 9a. _Http — internal REST helper with audit + verbose stdout mirroring
# ──────────────────────────────────────────────────────────────────────
def _safe_float(s: Any, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _describe_shape(data: Any) -> Dict[str, Any]:
    """Return a tiny shape descriptor for response logging — counts only,
    no values (so even if a future response carries sensitive data, the audit
    line stays small + safe)."""
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for k, v in data.items():
            if k in ("output", "output1", "output2", "output3"):
                if isinstance(v, list):
                    out[k] = f"list(n={len(v)})"
                elif isinstance(v, dict):
                    out[k] = f"dict(keys={len(v)})"
                else:
                    out[k] = type(v).__name__
        return out
    return {"type": type(data).__name__}


# Keywords that indicate a recoverable KIS rate-limit / throttling error.
# KIS returns these messages with HTTP 500 *and* a JSON body containing rt_cd != 0,
# so we treat them as application-level (not transport) errors.
_RATE_LIMIT_HINTS = ("초당", "거래건수", "rate limit", "exceeded", "too many")

# Transient paper-mode errors that come back as HTTP 200 + rt_cd=1 + a Korean
# "service-delayed / try again" message. Same retry treatment as rate limits.
_TRANSIENT_HINTS = ("지연되고", "잠시후", "잠시 후", "재시도", "일시", "사용량")


class _Http:
    """Thin wrapper around `requests.Session` that:
    - injects KIS-standard headers (content-type, authorization, appkey, appsecret, tr_id, custtype)
    - self-throttles to stay under KIS paper's ~2-3 TPS limit
    - audit-logs every call (request + response, secrets masked)
    - optionally prints a one-line summary to stdout for live observation
    - distinguishes BrokerNetworkError (transport) from BrokerResponseError (application)
    - one-shot retry on rate-limit responses
    """

    def __init__(
        self,
        cfg: EnvConfig,
        audit: AuditLogger,
        *,
        verbose: bool = False,
        timeout_sec: float = 10.0,
        min_interval_ms: int = 350,   # ≈ 2.8 TPS, well under KIS paper limit
        rate_limit_backoff_ms: int = 1100,
    ):
        self.cfg = cfg
        self.audit = audit
        self.verbose = verbose
        self.timeout_sec = timeout_sec
        self.min_interval_ms = min_interval_ms
        self.rate_limit_backoff_ms = rate_limit_backoff_ms
        self.session = requests.Session()
        self._last_call_perf: float = 0.0

    def _throttle(self) -> None:
        if self.min_interval_ms <= 0:
            return
        elapsed_ms = (time.perf_counter() - self._last_call_perf) * 1000.0
        wait_ms = self.min_interval_ms - elapsed_ms
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)

    def call(
        self,
        method: str,
        path: str,
        *,
        tr_id: Optional[str],
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth_headers: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        expect_rt_cd: bool = True,
        _retry_left: int = 2,  # one slot for rate-limit, one for transient delay
    ) -> Dict[str, Any]:
        self._throttle()

        url = self.cfg.base_url + path
        headers: Dict[str, str] = {
            "content-type": "application/json; charset=UTF-8",
            "custtype": "P",
        }
        if tr_id:
            headers["tr_id"] = tr_id
        if auth_headers:
            headers.update(auth_headers)
        if extra_headers:
            headers.update(extra_headers)

        request_id = f"req-{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        resp = None
        data: Dict[str, Any] = {}
        status: Optional[int] = None

        try:
            resp = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=body if method.upper() != "GET" else None,
                headers=headers,
                timeout=self.timeout_sec,
            )
            self._last_call_perf = time.perf_counter()
            status = resp.status_code
            try:
                data = resp.json() if resp.content else {}
            except json.JSONDecodeError:
                data = {"_raw": resp.text[:500]}
            t_ms = (time.perf_counter() - t0) * 1000.0
            # Surface paging signal from response headers (KIS uses `tr_cont`:
            #   "F"/"M" = more rows available, "D"/"E"/"" = last page).
            data["_tr_cont"] = (resp.headers.get("tr_cont") or "").strip()
        except requests.RequestException as e:
            t_ms = (time.perf_counter() - t0) * 1000.0
            err_str = f"{type(e).__name__}: {e}"
            if self.verbose:
                print(f"  [ERR ] {method} {path} tr={tr_id} -> {err_str}")
            self.audit.log(
                endpoint=path, method=method.upper(), request_id=request_id,
                dry_run=False, http_status=None, latency_ms=round(t_ms, 1),
                request_summary={"tr_id": tr_id, "params": params, "body": body},
                response_summary={},
                error=err_str,
            )
            raise BrokerNetworkError(err_str) from e

        rt_cd = data.get("rt_cd")
        msg1 = (data.get("msg1") or "").strip()

        if self.verbose:
            short = path.rsplit("/", 1)[-1]
            tag = "[paper]" if self.cfg.is_paper else "[LIVE ]"
            ts = datetime.now().strftime("%H:%M:%S")
            hint = ""
            if params:
                hint = " ".join(f"{k}={v}" for k, v in params.items()
                                if k in ("EXCD", "SYMB", "OVRS_EXCG_CD",
                                         "PDNO", "ITEM_CD"))[:80]
            print(f"  {tag} {ts}  {method:4s} {short:18s}  "
                  f"tr={tr_id or '-':14s}  status={status}  rt_cd={rt_cd}  "
                  f"{t_ms:6.0f}ms  {hint}  {msg1[:60]}")

        self.audit.log(
            endpoint=path, method=method.upper(), request_id=request_id,
            dry_run=False, http_status=status, latency_ms=round(t_ms, 1),
            request_summary={"tr_id": tr_id, "params": params, "body": body},
            response_summary={
                "rt_cd": rt_cd, "msg_cd": data.get("msg_cd"),
                "msg1": msg1[:200], "shape": _describe_shape(data),
            },
        )

        # Application-level error path (KIS returns valid JSON with rt_cd).
        if rt_cd not in (None, "0"):
            is_rate_limit = any(kw in msg1 for kw in _RATE_LIMIT_HINTS)
            is_transient = any(kw in msg1 for kw in _TRANSIENT_HINTS)
            if (is_rate_limit or is_transient) and _retry_left > 0:
                # Transient delays often resolve faster than rate-limit windows;
                # use the same backoff for simplicity (operator can see why in audit).
                cause = "rate-limit" if is_rate_limit else "transient-delay"
                if self.verbose:
                    print(f"  [retry] {cause}, backing off {self.rate_limit_backoff_ms}ms "
                          f"(retries_left={_retry_left})")
                time.sleep(self.rate_limit_backoff_ms / 1000.0)
                return self.call(
                    method, path, tr_id=tr_id, params=params, body=body,
                    auth_headers=auth_headers, extra_headers=extra_headers,
                    expect_rt_cd=expect_rt_cd, _retry_left=_retry_left - 1,
                )
            if expect_rt_cd:
                raise BrokerResponseError(
                    f"KIS rt_cd={rt_cd} (HTTP {status}) on {path}: {msg1}"
                )

        # Pure transport error path (no rt_cd, but bad HTTP).
        if resp is not None and not resp.ok and rt_cd is None:
            raise BrokerNetworkError(
                f"HTTP {status} on {method} {path}: {msg1 or resp.reason}"
            )

        return data


# ──────────────────────────────────────────────────────────────────────
# 9b. KisBrokerAdapter — Step 2: REST wired (read-only)
# ──────────────────────────────────────────────────────────────────────
class KisBrokerAdapter:
    """Real KIS adapter. Step 2 wires read-only REST: token, quote, positions,
    cash, order history. Step 3 will add write path (place_order live).
    """

    def __init__(
        self,
        cfg: Optional[EnvConfig] = None,
        *,
        safety_state: Optional[SafetyState] = None,
        logger: Optional[logging.Logger] = None,
        verbose: bool = False,
        timeout_sec: float = 10.0,
    ):
        self.cfg = cfg or load_env_config()
        self.token_cache = TokenCache(self.cfg.token_cache_path, env_name=self.cfg.env_name)
        self.audit = AuditLogger(self.cfg.log_dir, env_name=self.cfg.env_name)
        self.guard = SafetyGuard(self.cfg, state=safety_state)
        self.log = logger or logging.getLogger("kis_broker")
        self.http = _Http(self.cfg, self.audit, verbose=verbose, timeout_sec=timeout_sec)

    # -- helpers -----------------------------------------------------------
    def _tr(self, key: str) -> str:
        env_key = "paper" if self.cfg.is_paper else "live"
        return TR[key][env_key]

    def _auth_headers(self) -> Dict[str, str]:
        tok = self.ensure_token()
        return {
            "authorization": f"Bearer {tok.access_token}",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
        }

    @staticmethod
    def _normalize_market(market: str) -> str:
        m = (market or "NASD").upper()
        if m not in VALID_TRADE_MARKETS:
            raise BrokerError(
                f"market must be one of {sorted(VALID_TRADE_MARKETS)}, got {market!r}"
            )
        return m

    # -- session / token ---------------------------------------------------
    def ensure_token(self) -> TokenRecord:
        """Return a fresh access_token. Reuses cache if not within expiry margin.
        Issues a new token via POST /oauth2/tokenP otherwise.
        """
        cached = self.token_cache.load()
        if cached and not cached.is_expired():
            return cached

        body = {
            "grant_type":  "client_credentials",
            "appkey":      self.cfg.app_key,
            "appsecret":   self.cfg.app_secret,
        }
        # tokenP responds without rt_cd; use expect_rt_cd=False.
        data = self.http.call(
            "POST", EP_TOKEN, tr_id=None, body=body, expect_rt_cd=False,
        )
        access_token = data.get("access_token")
        if not access_token:
            raise BrokerAuthError(
                f"tokenP returned no access_token: {data}"
            )
        ttl = int(data.get("expires_in") or KIS_TOKEN_TTL_FALLBACK_SEC)
        issued = datetime.now(timezone.utc)
        rec = TokenRecord(
            access_token=access_token,
            token_type=str(data.get("token_type") or "Bearer"),
            issued_at=issued.isoformat(timespec="seconds"),
            expires_at=(issued + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
            env=self.cfg.env_name,
        )
        self.token_cache.save(rec)
        return rec

    # -- read endpoints ---------------------------------------------------
    def get_quote(self, symbol: str, *, market: str = "NASD") -> Quote:
        """현재가 조회. paper/live 공통 TR_ID HHDFS00000300.

        Returns a Quote with `last` as the broker's "last" field. Outside US
        market hours `last` may equal the prior close ("base") field.
        """
        market_t = self._normalize_market(market)
        excd = EXCD_QUOTE_FROM_TRADE[market_t]
        params = {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()}
        data = self.http.call(
            "GET", EP_QUOTE_PRICE,
            tr_id=self._tr("quote_price"),
            params=params,
            auth_headers=self._auth_headers(),
        )
        out = data.get("output") or {}
        last  = _safe_float(out.get("last"))
        if last == 0.0:
            # Fall back to prior close when after-hours.
            last = _safe_float(out.get("base"))
        return Quote(
            symbol=symbol.upper(),
            market=market_t,
            last=last,
            bid=_safe_float(out.get("pbid")) or None,
            ask=_safe_float(out.get("pask")) or None,
            asof=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def get_quote_with_exchange_fallback(
        self,
        symbol: str,
        *,
        preferred_market: str = "NASD",
        exchanges: Tuple[str, ...] = ("NASD", "NYSE", "AMEX"),
    ) -> Optional[Quote]:
        """R10E — try ``get_quote`` against multiple US exchanges and
        return the first one whose `ask` or `last` is positive.

        The autotrade pipeline used to hard-code ``market="NASD"`` for
        every BUY candidate (because recommendations.csv has no
        exchange column). That worked for NASDAQ tickers but for
        NYSE-listed names like JBL or DOW the KIS quote endpoint
        returned ``last=0, ask=0`` and the R10D-3 quote-refresh helper
        fell back to yesterday's close — exactly the failure that
        produced an overpriced limit in 20260519_220825_daily.

        We probe the preferred market first (so symbols that actually
        live on NASD are resolved in a single round trip), then walk
        the remaining exchanges. Anything that raises (auth, network,
        404, EXCD mismatch) is treated as "try the next one".

        Returns ``None`` only when EVERY exchange in the list either
        raised or returned a zero-priced Quote. The caller is then
        free to fall back to the recommendation price.
        """
        seen: List[str] = []
        ordered: List[str] = [self._normalize_market(preferred_market)]
        for ex in exchanges:
            ex_t = self._normalize_market(ex)
            if ex_t not in ordered:
                ordered.append(ex_t)
        for ex in ordered:
            seen.append(ex)
            try:
                q = self.get_quote(symbol, market=ex)
            except Exception:  # noqa: BLE001
                continue
            if q is None:
                continue
            # Treat a Quote as "good enough" if ASK > 0 OR LAST > 0.
            ask = q.ask if q.ask is not None else 0.0
            last = q.last if q.last is not None else 0.0
            if (ask and ask > 0) or (last and last > 0):
                return q
        return None

    def get_positions(
        self,
        *,
        market: str = "NASD",
        max_pages: int = 50,
    ) -> List[Position]:
        """해외주식 잔고 (paging-aware).

        KIS returns 30 rows/page. We follow continuation keys until `tr_cont`
        signals the last page (D/E). `max_pages` is a safety cap.
        """
        market_t = self._normalize_market(market)
        rows: List[Position] = []
        ctx_fk, ctx_nk = "", ""

        for page in range(1, max_pages + 1):
            params = {
                "CANO": self.cfg.account_no,
                "ACNT_PRDT_CD": self.cfg.account_product_code,
                "OVRS_EXCG_CD": market_t,
                "TR_CRCY_CD":   "USD",
                "CTX_AREA_FK200": ctx_fk,
                "CTX_AREA_NK200": ctx_nk,
            }
            extra_headers = {"tr_cont": "N"} if (ctx_fk or ctx_nk) else None
            data = self.http.call(
                "GET", EP_INQ_BALANCE,
                tr_id=self._tr("inquire_balance"),
                params=params,
                auth_headers=self._auth_headers(),
                extra_headers=extra_headers,
            )
            for row in (data.get("output1") or []):
                qty = _safe_float(row.get("ovrs_cblc_qty"))
                if qty <= 0:
                    continue
                rows.append(Position(
                    symbol=str(row.get("ovrs_pdno") or row.get("pdno") or "").upper(),
                    market=market_t,
                    qty=qty,
                    avg_price=_safe_float(row.get("pchs_avg_pric")),
                    asof=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ))
            tr_cont = data.get("_tr_cont", "")
            if tr_cont not in ("F", "M"):
                break
            ctx_fk = (data.get("ctx_area_fk200") or "").strip()
            ctx_nk = (data.get("ctx_area_nk200") or "").strip()
            if not ctx_nk:
                break
        else:
            self.log.warning("get_positions: hit max_pages=%d safety cap", max_pages)
        return rows

    def get_positions_all_us(self) -> List[Position]:
        """Aggregate US positions.

        Paper investing mirrors the same row-set across NASD/NYSE/AMEX query
        keys, so we issue a single NASD-paged query and de-duplicate by
        symbol. Live should also work with NASD only (KIS treats OVRS_EXCG_CD
        as a filter, not a partition).
        """
        seen: Dict[str, Position] = {}
        for p in self.get_positions(market="NASD"):
            key = p.symbol
            existing = seen.get(key)
            if existing is None:
                seen[key] = p
            else:
                # Same symbol returned twice — keep the larger qty as a
                # conservative reconcile choice and surface a warning.
                if p.qty > existing.qty:
                    seen[key] = p
        return list(seen.values())

    def get_cash(
        self,
        *,
        market: str = "NASD",
        ref_symbol: str = "AAPL",
        ref_price: Optional[float] = None,
    ) -> CashBalance:
        """매수가능금액 조회 (KIS는 단가/종목 기준으로 산정).
        ref_price 미지정 시 ref_symbol의 현재가를 한 번 더 조회하여 사용한다.
        반환값의 `available`은 외화(USD) 매수가능금액.
        """
        market_t = self._normalize_market(market)
        if ref_price is None:
            ref_price = self.get_quote(ref_symbol, market=market_t).last or 1.0
        # KIS uses string price with 8 decimals.
        ord_unpr_str = f"{float(ref_price):.4f}"
        params = {
            "CANO": self.cfg.account_no,
            "ACNT_PRDT_CD": self.cfg.account_product_code,
            "OVRS_EXCG_CD": market_t,
            "OVRS_ORD_UNPR": ord_unpr_str,
            "ITEM_CD": ref_symbol.upper(),
        }
        data = self.http.call(
            "GET", EP_INQ_PSAMOUNT,
            tr_id=self._tr("inquire_psamount"),
            params=params,
            auth_headers=self._auth_headers(),
        )
        out = data.get("output") or {}
        available = _safe_float(out.get("ovrs_ord_psbl_amt"))
        if available == 0.0:
            # Some paper accounts return frcr_ord_psbl_amt1 instead.
            available = _safe_float(out.get("frcr_ord_psbl_amt1"))
        return CashBalance(
            base_ccy="USD",
            total=available,        # KIS doesn't return "total" cleanly here
            available=available,
            asof=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def get_order_history(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """체결+미체결 내역 조회 (paging-aware).

        Paper has tight constraints on PDNO/SLL_BUY_DVSN/CCLD_NCCS_DVSN/
        OVRS_EXCG_CD/SORT_SQN: only wildcards are accepted. Reference:
        geongi-im/kis-us-auto-trading. Returns raw KIS rows.
        """
        today = datetime.now().strftime("%Y%m%d")
        start = start_date or (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        end = end_date or today
        paper = self.cfg.is_paper
        out: List[Dict[str, Any]] = []
        ctx_fk, ctx_nk = "", ""

        for page in range(1, max_pages + 1):
            params = {
                "CANO": self.cfg.account_no,
                "ACNT_PRDT_CD": self.cfg.account_product_code,
                "PDNO": "",                            # paper: only ""
                "ORD_STRT_DT": start,
                "ORD_END_DT":  end,
                "SLL_BUY_DVSN": "00",                  # paper: only "00"
                "CCLD_NCCS_DVSN": "00",                # paper: only "00"
                "OVRS_EXCG_CD": "%" if paper else "NASD",
                "SORT_SQN": "DS",                      # paper: only "DS"
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_FK200": ctx_fk,
                "CTX_AREA_NK200": ctx_nk,
            }
            extra_headers = {"tr_cont": "N"} if (ctx_fk or ctx_nk) else None
            data = self.http.call(
                "GET", EP_INQ_CCNL,
                tr_id=self._tr("inquire_ccnl"),
                params=params,
                auth_headers=self._auth_headers(),
                extra_headers=extra_headers,
            )
            out.extend(data.get("output") or [])
            tr_cont = data.get("_tr_cont", "")
            if tr_cont not in ("F", "M"):
                break
            ctx_fk = (data.get("ctx_area_fk200") or "").strip()
            ctx_nk = (data.get("ctx_area_nk200") or "").strip()
            if not ctx_nk:
                break
        else:
            self.log.warning("get_order_history: hit max_pages=%d safety cap", max_pages)
        return out

    def get_order(self, broker_order_id: str) -> Dict[str, Any]:
        """v0: scan recent ccnl history and match by ODNO.

        R8-A (2026-05-16): ODNO comparison goes through
        ``phase3.autotrade.order_ids.normalize_odno`` because KIS overseas
        returns padded ODNOs on ``place_order`` (``0000041461``) but
        stripped ODNOs on ``inquire-ccnl`` (``41461``). The previous raw
        ``str.strip() ==`` compare missed every paper fill where the
        caller passed the padded form from the place ack.
        """
        from phase3.autotrade.order_ids import normalize_odno  # local to avoid cyclic import at module load
        target = normalize_odno(broker_order_id)
        if not target:
            return {"status": "not_found", "broker_order_id": broker_order_id}
        for row in self.get_order_history():
            if normalize_odno(row.get("odno", "")) == target:
                return row
        return {"status": "not_found", "broker_order_id": broker_order_id}

    # -- Round 3 (P1.A): open / unfilled order visibility ------------------
    def get_open_orders(
        self,
        *,
        market: str = "NASD",
        sort_order: str = "DS",
        max_pages: int = 20,
        return_raw: bool = False,
    ) -> List[Any]:
        """해외주식 미체결내역 조회 (`inquire-nccs`).

        Codex Round 3 P1.A. Per KIS docs / official LLM sample:
          - GET /uapi/overseas-stock/v1/trading/inquire-nccs
          - paper tr_id = VTTS3018R, live tr_id = TTTS3018R
          - OVRS_EXCG_CD must NOT be blank (otherwise paging stops working).
            Passing 'NASD' returns the US aggregate (NASD+NYSE+AMEX); any
            other code returns only that single exchange.
          - SORT_SQN 'DS' = ascending; anything else = descending. Default
            'DS' matches the sample.
          - Paging via CTX_AREA_FK200/NK200 and tr_cont 'F'/'M', identical
            to `inquire-balance` and `inquire-ccnl`.

        Args:
            market: 'NASD' (US aggregate) | 'NYSE' | 'AMEX' | 'SEHK' | …
            sort_order: 'DS' (asc) or anything else (desc); KIS-defined.
            max_pages: safety cap; the typical paper account has < 30 open.
            return_raw: when True returns raw KIS row dicts; default False
                returns normalized `OpenOrder` instances + keeps raw on the side.

        Returns:
            List[OpenOrder] (default) or List[Dict[str, Any]] when `return_raw`.
            Empty list means "no open orders" *or* the endpoint returned an
            empty page — caller should check the audit log to distinguish.
        """
        out_raw: List[Dict[str, Any]] = []
        ctx_fk, ctx_nk = "", ""

        for page in range(1, max_pages + 1):
            params = {
                "CANO": self.cfg.account_no,
                "ACNT_PRDT_CD": self.cfg.account_product_code,
                "OVRS_EXCG_CD": market,        # MUST be non-empty
                "SORT_SQN": sort_order,
                "CTX_AREA_FK200": ctx_fk,
                "CTX_AREA_NK200": ctx_nk,
            }
            extra_headers = {"tr_cont": "N"} if (ctx_fk or ctx_nk) else None
            data = self.http.call(
                "GET", EP_INQ_NCCS,
                tr_id=self._tr("inquire_nccs"),
                params=params,
                auth_headers=self._auth_headers(),
                extra_headers=extra_headers,
            )
            out_raw.extend(data.get("output") or [])
            tr_cont = data.get("_tr_cont", "")
            if tr_cont not in ("F", "M"):
                break
            ctx_fk = (data.get("ctx_area_fk200") or "").strip()
            ctx_nk = (data.get("ctx_area_nk200") or "").strip()
            if not ctx_nk:
                break
        else:
            self.log.warning("get_open_orders: hit max_pages=%d safety cap", max_pages)

        if return_raw:
            return out_raw
        return [self._normalize_open_order(r) for r in out_raw]

    @staticmethod
    def _normalize_open_order(r: Dict[str, Any]) -> OpenOrder:
        """Map a raw KIS `inquire-nccs` row to our OpenOrder.

        KIS uses Korean-named keys with several aliases across endpoints.
        We pick the first non-empty among each known group, falling back
        to safe defaults; the raw row is preserved verbatim for audit.
        """
        def first(*keys: str) -> Any:
            for k in keys:
                v = r.get(k)
                if v not in (None, ""):
                    return v
            return ""

        odno = str(first("odno", "ord_no")).strip()
        ord_dt = str(first("ord_dt", "ord_dttm", "trad_dvsn_dt")).strip()
        ord_tmd = str(first("ord_tmd", "ord_tm", "ord_dttm")).strip()
        pdno = str(first("pdno", "ovrs_pdno")).strip().upper()
        excg = str(first("ovrs_excg_cd", "tr_mket_cd", "excg_cd")).strip().upper()
        sll_buy = str(first("sll_buy_dvsn_cd")).strip()
        if sll_buy == "01":
            side = "SELL"
        elif sll_buy == "02":
            side = "BUY"
        else:
            side = "UNKNOWN"
        qty_order = _safe_float(first("ft_ord_qty", "ord_qty"))
        qty_filled = _safe_float(first("ft_ccld_qty3", "ft_ccld_qty",
                                       "tot_ccld_qty", "ccld_qty"))
        qty_remaining = _safe_float(first("nccs_qty", "ord_psbl_qty",
                                          "rmn_qty"))
        # If KIS doesn't include explicit remaining, derive it.
        if qty_remaining == 0.0 and qty_order > 0.0:
            qty_remaining = max(qty_order - qty_filled, 0.0)
        limit_price = _safe_float(first("ft_ord_unpr3", "ovrs_ord_unpr",
                                        "ord_unpr"))
        status_text = str(first("prcs_stat_name", "ord_stts_name",
                                "rjct_rson_name", "ord_dvsn_name")).strip()

        return OpenOrder(
            broker_order_id=odno, ord_dt=ord_dt, ord_tmd=ord_tmd,
            symbol=pdno, market=excg, side=side,
            qty_order=qty_order, qty_filled=qty_filled,
            qty_remaining=qty_remaining,
            limit_price=limit_price, status_text=status_text,
            raw=r,
        )

    def find_open_order(self, broker_order_id: str, *,
                        market: str = "NASD") -> Optional[OpenOrder]:
        """Convenience: look up our ODNO among open orders. Returns None
        if not present (which means filled, cancelled, or never reached
        the open-list — caller should consult ccnl / position deltas).

        ODNO comparison goes through ``order_ids.normalize_odno`` (R8-A
        centralization of the R6 padded-vs-stripped fix).
        """
        from phase3.autotrade.order_ids import normalize_odno
        target = normalize_odno(broker_order_id)
        if not target:
            return None
        for oo in self.get_open_orders(market=market):
            if normalize_odno(oo.broker_order_id) == target:
                return oo
        return None

    # -- write endpoint (Step 3+ scaffold; Step 4 fills paper_submit branch) -
    def place_order(
        self,
        intent: OrderIntent,
        *,
        dry_run: bool = True,
    ) -> PlacedOrder:
        """Submit an order intent.

        Per Codex review P1-A the transmission branch is selected by
        `SafetyGuard.submit_mode`:
          - 'dry_run'      → log + return status='dry_run' (no network)
          - 'paper_submit' → POST to paper broker (wired in Step 4)
          - 'live_capital' → POST to live broker  (wired in a later step)

        Buy-only / global_halt / cancel_all_pending still raise SafetyError
        via `assert_can_submit` regardless of mode.
        """
        intent.validate()
        self.guard.assert_can_submit(side=intent.side, dry_run=dry_run)

        mode, reason = self.guard.submit_decision(dry_run=dry_run)
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        intent_summary = {
            "symbol": intent.symbol, "market": intent.market,
            "side": intent.side, "qty": intent.qty,
            "order_type": intent.order_type, "limit_price": intent.limit_price,
            "client_order_id": intent.client_order_id,
            "note": intent.note,
            "submit_mode": mode, "submit_reason": reason,
        }

        if mode == "dry_run":
            self.audit.log(
                endpoint="place_order",
                method="POST",
                request_id=request_id,
                dry_run=True,
                http_status=None,
                request_summary=intent_summary,
                response_summary={"reason": f"dry-run gated by SafetyGuard ({reason})"},
            )
            return PlacedOrder(
                client_order_id=intent.client_order_id,
                broker_order_id=None,
                status="dry_run",
                intent=intent,
                submitted_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                raw_response_summary={"gated": True, "mode": mode, "reason": reason},
            )

        if mode == "paper_submit":
            return self._submit_order_real(intent, request_id=request_id, mode=mode)

        # mode == 'live_capital'
        # Wired after Step 4 paper validation completes.
        raise NotImplementedError(
            "place_order(live_capital): live transmission is wired after Step 4 "
            "paper validation completes. Triple lock (env=live, dry_run=False, "
            "confirm_live=true) is satisfied here."
        )

    # -- internal: actual POST to /uapi/overseas-stock/v1/trading/order ------
    def _submit_order_real(
        self,
        intent: OrderIntent,
        *,
        request_id: str,
        mode: str,
    ) -> PlacedOrder:
        """Send `intent` to the broker via POST `/uapi/overseas-stock/v1/trading/order`.

        Body is built here (not from intents.py) so the adapter is the single
        owner of the wire format. Paper hardcodes ORD_DVSN=00 (LIMIT) since
        paper rejects market orders. The submitted_at timestamp is captured BEFORE
        the network call so audit timing is meaningful even if `http.call` raises.
        """
        market = self._normalize_market(intent.market)
        tr_id_key = "order_buy" if intent.side == "BUY" else "order_sell"
        tr_id = self._tr(tr_id_key)

        if intent.order_type not in ("LIMIT", "MARKET"):
            raise BrokerError(f"unsupported order_type={intent.order_type!r}")
        if intent.limit_price is None or intent.limit_price <= 0:
            raise BrokerError(
                f"paper submit requires a positive limit_price; got {intent.limit_price!r}"
            )

        body: Dict[str, Any] = {
            "CANO":            self.cfg.account_no,
            "ACNT_PRDT_CD":    self.cfg.account_product_code,
            "OVRS_EXCG_CD":    market,
            "PDNO":            intent.symbol.upper(),
            "ORD_QTY":         str(int(intent.qty)),
            "OVRS_ORD_UNPR":   f"{float(intent.limit_price):.4f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN":        "00",   # paper accepts LIMIT only
        }
        if intent.side == "SELL":
            body["SLL_TYPE"] = "00"    # 일반매도

        submitted_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        auth = self._auth_headers()

        try:
            resp = self.http.call(
                method="POST",
                path=EP_ORDER,
                tr_id=tr_id,
                body=body,
                auth_headers=auth,
            )
        except BrokerResponseError as e:
            # KIS rejected at the application level (rt_cd != 0). Keep the
            # client_order_id so the operator can correlate audit + retry.
            return PlacedOrder(
                client_order_id=intent.client_order_id,
                broker_order_id=None,
                status="rejected",
                intent=intent,
                submitted_at=submitted_at,
                raw_response_summary={
                    "mode": mode, "tr_id": tr_id, "error": str(e),
                },
            )

        output = (resp.get("output") or {}) if isinstance(resp.get("output"), dict) else {}
        broker_order_id = str(output.get("ODNO") or "").strip() or None
        krx_org = str(output.get("KRX_FWDG_ORD_ORGNO") or "").strip() or None
        ord_tmd = str(output.get("ORD_TMD") or "").strip() or None

        return PlacedOrder(
            client_order_id=intent.client_order_id,
            broker_order_id=broker_order_id,
            status="submitted" if broker_order_id else "ack_no_id",
            intent=intent,
            submitted_at=submitted_at,
            raw_response_summary={
                "mode": mode, "tr_id": tr_id,
                "ODNO": broker_order_id,
                "KRX_FWDG_ORD_ORGNO": krx_org,
                "ORD_TMD": ord_tmd,
                "msg_cd": resp.get("msg_cd"),
                "msg1": resp.get("msg1"),
            },
        )

    # =====================================================================
    # Round 8 — cancel path
    # =====================================================================
    def cancel_order(
        self,
        *,
        broker_order_id: str,
        symbol: str,
        market: str = "NASD",
        qty: int,
        dry_run: bool = True,
        note: str = "",
    ) -> CancelResult:
        """Cancel a previously-submitted overseas paper order.

        R8 today-slice contract:
          - paper-only (refuses live until live cancel is reviewed)
          - dry-run default; an actual broker POST requires both
            `dry_run=False` AND `cfg.paper_cancel_ok == True`
            (i.e. ``KIS_PAPER_CANCEL_OK=true`` in the env)
          - returns a structured ``CancelResult`` covering both dry-run
            and real paths; never raises on policy denial — always
            returns ``accepted=False`` with a reason in ``note``
          - empty / blank ``broker_order_id`` is refused outright

        The payload shape and TR_ID matrix are taken from KIS Developers
        "해외주식 정정취소" with the geongi-im/kis-us-auto-trading reference
        implementation as the field-name sanity check.

        Sends ``ORGN_ODNO`` with whatever surface form the caller passes
        (we do *not* zero-pad or strip here — KIS expects the form from
        the original ``place_order`` ack, which is already zero-padded
        on paper). ODNO normalization (R6) is for *matching* responses,
        not for serializing payloads.
        """
        submitted_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        def _result(*, accepted: bool, cancel_oid: Optional[str],
                    raw_summary: Dict[str, Any], reason: str = "",
                    payload: Optional[Dict[str, Any]] = None) -> CancelResult:
            return CancelResult(
                broker_order_id=str(broker_order_id or "").strip(),
                cancel_order_id=cancel_oid,
                accepted=accepted,
                dry_run=dry_run,
                symbol=str(symbol or "").upper(),
                market=str(market or "NASD").upper(),
                qty=int(qty),
                submitted_at=submitted_at,
                payload=payload or {},
                raw_response_summary=raw_summary,
                note=reason or note,
            )

        # ── policy & input guards ─────────────────────────────────────
        if not broker_order_id or not str(broker_order_id).strip():
            return _result(
                accepted=False, cancel_oid=None, raw_summary={},
                reason="empty broker_order_id refused",
            )
        if not symbol or not str(symbol).strip():
            return _result(
                accepted=False, cancel_oid=None, raw_summary={},
                reason="empty symbol refused",
            )
        if int(qty) <= 0:
            return _result(
                accepted=False, cancel_oid=None, raw_summary={},
                reason=f"qty must be positive, got {qty!r}",
            )
        if not self.cfg.is_paper:
            # R8 §2 hard rule #1: paper-only.
            return _result(
                accepted=False, cancel_oid=None, raw_summary={},
                reason=f"cancel_order is paper-only in R8 (env={self.cfg.env_name})",
            )
        if not dry_run and not self.cfg.paper_cancel_ok:
            return _result(
                accepted=False, cancel_oid=None, raw_summary={},
                reason="KIS_PAPER_CANCEL_OK=true required for real paper cancel",
            )

        market_canon = self._normalize_market(market)
        tr_id = self._tr("order_cancel")

        body: Dict[str, Any] = {
            "CANO":              self.cfg.account_no,
            "ACNT_PRDT_CD":      self.cfg.account_product_code,
            "OVRS_EXCG_CD":      market_canon,
            "PDNO":              str(symbol).upper(),
            "ORGN_ODNO":         str(broker_order_id).strip(),
            "RVSE_CNCL_DVSN_CD": "02",  # 01=modify, 02=cancel
            "ORD_QTY":           str(int(qty)),
            "OVRS_ORD_UNPR":     "0",   # KIS: 0 on cancel
            "MGCO_APTM_ODNO":    "",    # 운용사지정주문번호 (usually empty)
            "ORD_SVR_DVSN_CD":   "0",
        }

        if dry_run:
            request_id = f"req-{uuid.uuid4().hex[:12]}"
            self.audit.log(
                endpoint=EP_ORDER_RVSECNCL,
                method="POST",
                request_id=request_id,
                dry_run=True,
                request_summary={"tr_id": tr_id, "body": dict(body)},
                extra={"kind": "cancel_dry_run", "note": note},
            )
            return _result(
                accepted=True, cancel_oid=None,
                raw_summary={
                    "mode": "dry_run", "tr_id": tr_id,
                    "endpoint": EP_ORDER_RVSECNCL,
                    "request_id": request_id,
                },
                reason="dry-run — payload only, no network call",
                payload=dict(body),
            )

        # ── real paper cancel ─────────────────────────────────────────
        auth = self._auth_headers()
        try:
            resp = self.http.call(
                method="POST",
                path=EP_ORDER_RVSECNCL,
                tr_id=tr_id,
                body=body,
                auth_headers=auth,
            )
        except BrokerResponseError as e:
            return _result(
                accepted=False, cancel_oid=None,
                raw_summary={
                    "mode": "paper_cancel", "tr_id": tr_id,
                    "endpoint": EP_ORDER_RVSECNCL,
                    "error": str(e),
                },
                reason=f"KIS rejected cancel: {e}",
                payload=dict(body),
            )

        output = (resp.get("output") or {}) if isinstance(resp.get("output"), dict) else {}
        cancel_oid = str(output.get("ODNO") or "").strip() or None
        krx_org = str(output.get("KRX_FWDG_ORD_ORGNO") or "").strip() or None
        ord_tmd = str(output.get("ORD_TMD") or "").strip() or None
        return _result(
            accepted=True, cancel_oid=cancel_oid,
            raw_summary={
                "mode": "paper_cancel", "tr_id": tr_id,
                "endpoint": EP_ORDER_RVSECNCL,
                "ODNO": cancel_oid,
                "KRX_FWDG_ORD_ORGNO": krx_org,
                "ORD_TMD": ord_tmd,
                "msg_cd": resp.get("msg_cd"),
                "msg1": resp.get("msg1"),
            },
            payload=dict(body),
        )


# ──────────────────────────────────────────────────────────────────────
# 10. MockBrokerAdapter — for tests / skeleton verification
# ──────────────────────────────────────────────────────────────────────
class MockBrokerAdapter:
    """In-memory stub. Zero network calls. Used by tests and `--self-check`.

    Same public interface as KisBrokerAdapter, so the rest of the autotrade
    pipeline (intents → risk → place → reconcile) can be developed against
    this before live wiring.
    """

    def __init__(
        self,
        cfg: Optional[EnvConfig] = None,
        *,
        safety_state: Optional[SafetyState] = None,
        seed_positions: Optional[List[Position]] = None,
        seed_cash_usd: float = 10_000.0,
        seed_quotes: Optional[Dict[str, float]] = None,
    ):
        self.cfg = cfg or load_env_config()
        self.audit = AuditLogger(self.cfg.log_dir, env_name=self.cfg.env_name)
        self.guard = SafetyGuard(self.cfg, state=safety_state)
        self._positions: Dict[str, Position] = {
            p.symbol: p for p in (seed_positions or [])
        }
        self._cash = CashBalance(
            base_ccy="USD", total=seed_cash_usd, available=seed_cash_usd,
            asof=datetime.now(timezone.utc).isoformat(),
        )
        self._quotes: Dict[str, float] = seed_quotes or {
            "AAPL": 195.0, "MSFT": 420.0, "NVDA": 1100.0,
        }
        self._orders: Dict[str, PlacedOrder] = {}

    # No real token; this is intentional.
    def ensure_token(self) -> TokenRecord:
        return TokenRecord(
            access_token="MOCK_TOKEN",
            token_type="Bearer",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=23)).isoformat(),
            env=self.cfg.env_name,
        )

    def get_quote(self, symbol: str, *, market: str = "NASD") -> Quote:
        last = self._quotes.get(symbol.upper(), 100.0)
        q = Quote(symbol=symbol.upper(), market=market, last=last,
                  bid=last * 0.999, ask=last * 1.001,
                  asof=datetime.now(timezone.utc).isoformat())
        self.audit.log(endpoint="mock:get_quote", method="GET",
                       request_id=f"req-{uuid.uuid4().hex[:8]}",
                       dry_run=True, http_status=200,
                       request_summary={"symbol": symbol, "market": market},
                       response_summary={"last": last})
        return q

    def get_positions(self) -> List[Position]:
        out = list(self._positions.values())
        self.audit.log(endpoint="mock:get_positions", method="GET",
                       request_id=f"req-{uuid.uuid4().hex[:8]}",
                       dry_run=True, http_status=200,
                       request_summary={},
                       response_summary={"n": len(out)})
        return out

    def get_cash(self) -> CashBalance:
        self.audit.log(endpoint="mock:get_cash", method="GET",
                       request_id=f"req-{uuid.uuid4().hex[:8]}",
                       dry_run=True, http_status=200,
                       request_summary={},
                       response_summary={"available": self._cash.available})
        return self._cash

    def get_order(self, broker_order_id: str) -> Dict[str, Any]:
        po = self._orders.get(broker_order_id)
        return {"status": po.status if po else "unknown",
                "broker_order_id": broker_order_id}

    def get_open_orders(self, *, market: str = "NASD",
                        sort_order: str = "DS",
                        max_pages: int = 20,
                        return_raw: bool = False) -> List[Any]:
        """Mock parity for Round 3 P1.A. Mock fills are immediate, so no
        order is ever "open" — but we still log the call so the audit
        trail covers the new method and the verbose self-check passes.
        """
        self.audit.log(endpoint="mock:get_open_orders", method="GET",
                       request_id=f"req-{uuid.uuid4().hex[:8]}",
                       dry_run=True, http_status=200,
                       request_summary={"market": market, "sort_order": sort_order},
                       response_summary={"rows": 0})
        return []

    def find_open_order(self, broker_order_id: str, *,
                        market: str = "NASD") -> Optional[OpenOrder]:
        _ = self.get_open_orders(market=market)
        return None

    def place_order(
        self,
        intent: OrderIntent,
        *,
        dry_run: bool = True,
    ) -> PlacedOrder:
        intent.validate()
        self.guard.assert_can_submit(side=intent.side, dry_run=dry_run)

        request_id = f"req-{uuid.uuid4().hex[:12]}"
        # Mock always dry-runs the actual transmission; we just simulate a
        # broker_order_id so downstream code can be exercised.
        broker_order_id = f"mock-{uuid.uuid4().hex[:10]}"
        po = PlacedOrder(
            client_order_id=intent.client_order_id,
            broker_order_id=broker_order_id if not dry_run else None,
            status="submitted" if not dry_run else "dry_run",
            intent=intent,
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            raw_response_summary={"mock": True},
        )
        if not dry_run:
            self._orders[broker_order_id] = po
            # Immediately fill at last price (mock convenience).
            quote = self.get_quote(intent.symbol, market=intent.market)
            self._apply_mock_fill(intent, quote.last)

        self.audit.log(
            endpoint="mock:place_order", method="POST",
            request_id=request_id,
            dry_run=dry_run,
            http_status=200 if not dry_run else None,
            request_summary={
                "symbol": intent.symbol, "side": intent.side,
                "qty": intent.qty, "order_type": intent.order_type,
                "limit_price": intent.limit_price,
                "client_order_id": intent.client_order_id,
            },
            response_summary={
                "broker_order_id": po.broker_order_id, "status": po.status,
            },
        )
        return po

    def _apply_mock_fill(self, intent: OrderIntent, fill_price: float) -> None:
        sym = intent.symbol.upper()
        if intent.side == "BUY":
            cash_needed = fill_price * intent.qty
            self._cash = CashBalance(
                base_ccy=self._cash.base_ccy,
                total=self._cash.total - cash_needed,
                available=self._cash.available - cash_needed,
                asof=datetime.now(timezone.utc).isoformat(),
            )
            existing = self._positions.get(sym)
            if existing:
                new_qty = existing.qty + intent.qty
                new_avg = (existing.avg_price * existing.qty + fill_price * intent.qty) / new_qty
                self._positions[sym] = Position(
                    symbol=sym, market=intent.market, qty=new_qty,
                    avg_price=new_avg,
                    asof=datetime.now(timezone.utc).isoformat(),
                )
            else:
                self._positions[sym] = Position(
                    symbol=sym, market=intent.market, qty=intent.qty,
                    avg_price=fill_price,
                    asof=datetime.now(timezone.utc).isoformat(),
                )
        else:  # SELL
            existing = self._positions.get(sym)
            if not existing or existing.qty < intent.qty:
                raise BrokerError(f"mock sell exceeds position: {sym}")
            proceeds = fill_price * intent.qty
            self._cash = CashBalance(
                base_ccy=self._cash.base_ccy,
                total=self._cash.total + proceeds,
                available=self._cash.available + proceeds,
                asof=datetime.now(timezone.utc).isoformat(),
            )
            remaining_qty = existing.qty - intent.qty
            if remaining_qty <= 0:
                self._positions.pop(sym, None)
            else:
                self._positions[sym] = Position(
                    symbol=sym, market=existing.market, qty=remaining_qty,
                    avg_price=existing.avg_price,
                    asof=datetime.now(timezone.utc).isoformat(),
                )


# ──────────────────────────────────────────────────────────────────────
# 11. _self_check — sanity entry point
# ──────────────────────────────────────────────────────────────────────
def _self_check() -> int:
    print("=" * 60)
    print("KIS Broker Adapter — Step 1 self-check")
    print("=" * 60)
    try:
        cfg = load_env_config()
    except EnvConfigError as e:
        print(f"[FAIL] env: {e}")
        return 2
    print("[ok] env loaded (masked):")
    for k, v in cfg.masked().items():
        print(f"      {k:24s} = {v}")

    # Token cache plumbing (no real call)
    tc = TokenCache(cfg.token_cache_path, env_name=cfg.env_name)
    rec = tc.load()
    if rec is None:
        print(f"[ok] token cache empty at {cfg.token_cache_path} (Step 2 will populate)")
    else:
        print(f"[ok] token cache loaded, expired={rec.is_expired()}")

    # Audit logger smoke test — verifies that even ad-hoc keys whose name
    # *suggests* a secret get masked. We deliberately log the real app_key
    # under several misleading key names; ALL must come out masked.
    audit = AuditLogger(cfg.log_dir, env_name=cfg.env_name)
    rid = f"req-{uuid.uuid4().hex[:12]}"
    audit.log(endpoint="self_check", method="NOOP", request_id=rid,
              dry_run=True, http_status=None,
              request_summary={
                  "sample_secret": cfg.app_key,
                  "bearer_token": cfg.app_key,
                  "app_key": cfg.app_key,
                  "nested": {"my_password": cfg.app_key, "innocent": "ok"},
              },
              response_summary={"ok": True})
    # Verify by re-reading the line we just wrote: it must NOT contain the
    # raw secret anywhere.
    audit_file = audit._path_for_today()
    last_line = audit_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    if cfg.app_key in last_line or cfg.app_secret in last_line:
        print("[FAIL] secret leaked into audit log!")
        return 4
    print(f"[ok] audit jsonl appended to {audit_file} (secrets verified masked)")

    # Safety guard — legacy live-order alias
    guard = SafetyGuard(cfg)
    live_ok = guard.allow_live_order(dry_run=False)
    print(f"[ok] safety: allow_live_order(dry_run=False) = {live_ok} "
          f"(expected False unless env=live + confirm_live=true)")
    try:
        guard.assert_can_submit(side="SELL", dry_run=True)
        print("[FAIL] safety: SELL should be blocked in default buy_only_mode")
        return 3
    except SafetyError as e:
        print(f"[ok] safety: SELL blocked as expected → {e}")
    try:
        guard.assert_can_submit(side="BUY", dry_run=True)
        print("[ok] safety: BUY allowed (dry_run) in buy_only_mode")
    except SafetyError as e:
        print(f"[FAIL] safety: BUY should be allowed → {e}")
        return 3

    # P1-A submit-mode gate split: dry_run / paper_submit / live_capital
    import dataclasses as _dc
    mode_dry, reason_dry = guard.submit_decision(dry_run=True)
    if mode_dry != "dry_run":
        print(f"[FAIL] safety: submit_mode(dry_run=True) should be dry_run, got {mode_dry}")
        return 3
    print(f"[ok] safety: submit_mode(dry_run=True) = {mode_dry}  ({reason_dry})")

    mode_default, reason_default = guard.submit_decision(dry_run=False)
    if mode_default != "dry_run":
        # Either confirm_live or paper_submit_ok was true in the .env. Surface that
        # explicitly so the operator sees the implication, but don't fail.
        print(f"[warn] safety: submit_mode(dry_run=False) = {mode_default}  ({reason_default}) "
              f"— a real call could now hit the broker. Confirm this is intentional.")
    else:
        print(f"[ok] safety: submit_mode(dry_run=False, default env) = {mode_default}  ({reason_default})")

    if cfg.is_paper:
        # Verify that flipping KIS_PAPER_SUBMIT_OK alone opens the paper_submit path
        # WITHOUT touching the live capital path.
        paper_ok_cfg = _dc.replace(cfg, paper_submit_ok=True)
        paper_guard = SafetyGuard(paper_ok_cfg)
        mode_paper, reason_paper = paper_guard.submit_decision(dry_run=False)
        if mode_paper != "paper_submit":
            print(f"[FAIL] safety: paper_submit_ok=true should yield paper_submit, got {mode_paper}")
            return 3
        if paper_guard.allow_live_capital(dry_run=False):
            print(f"[FAIL] safety: paper_submit_ok must NOT unlock live capital")
            return 3
        print(f"[ok] safety: hypothetical paper_submit_ok=true → {mode_paper}  ({reason_paper})")
        print("[ok] safety: confirmed paper_submit_ok does NOT unlock live capital")

    # Mock adapter round-trip
    mock = MockBrokerAdapter(cfg=cfg)
    print(f"[ok] mock: positions n={len(mock.get_positions())}, "
          f"cash=${mock.get_cash().available:,.0f}, AAPL quote=${mock.get_quote('AAPL').last}")
    intent = OrderIntent(symbol="AAPL", market="NASD", side="BUY", qty=2)
    po = mock.place_order(intent, dry_run=True)
    print(f"[ok] mock: dry_run order → status={po.status}, broker_id={po.broker_order_id}")
    # Simulated fill must also pass the new gate. Use a hypothetical cfg with
    # paper_submit_ok=True so the mock's internal SafetyGuard accepts the call,
    # without touching the operator's real .env state.
    mock_submit_cfg = _dc.replace(cfg, paper_submit_ok=True) if cfg.is_paper else cfg
    mock2 = MockBrokerAdapter(cfg=mock_submit_cfg, seed_cash_usd=10_000.0)
    po2 = mock2.place_order(OrderIntent(symbol="AAPL", market="NASD", side="BUY", qty=3),
                            dry_run=False)
    print(f"[ok] mock: simulated fill → status={po2.status}, broker_id={po2.broker_order_id}")
    print(f"      after fill: positions={[p.symbol+':'+str(p.qty) for p in mock2.get_positions()]}, "
          f"cash=${mock2.get_cash().available:,.2f}")

    # Real adapter plumbing — do NOT call ensure_token here (network!).
    # Real-network probes live behind `probe token`/`probe quote`/etc.
    real = KisBrokerAdapter(cfg=cfg, verbose=False)
    po3 = real.place_order(intent, dry_run=True)
    print(f"[ok] real adapter: dry-run place_order short-circuits → status={po3.status}")
    print("      (network probes: run `--probe token` / `--probe quote` etc.)")

    print("\nAll Step 1 self-checks passed.")
    return 0


# ──────────────────────────────────────────────────────────────────────
# 12. CLI / probe runners
# ──────────────────────────────────────────────────────────────────────
def _print_section(title: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n{title}\n{bar}")


def _probe_token(adapter: KisBrokerAdapter) -> int:
    _print_section("PROBE: token  (POST /oauth2/tokenP)")
    rec = adapter.ensure_token()
    print(f"  access_token = {_mask_secret(rec.access_token)}")
    print(f"  token_type   = {rec.token_type}")
    print(f"  issued_at    = {rec.issued_at}")
    print(f"  expires_at   = {rec.expires_at}")
    print(f"  env          = {rec.env}")
    print(f"  cached at    = {adapter.token_cache.path}")
    return 0


def _probe_quote(adapter: KisBrokerAdapter, symbol: str, market: str) -> int:
    _print_section(f"PROBE: quote  {symbol} @ {market}  (GET overseas-price/.../price)")
    q = adapter.get_quote(symbol, market=market)
    print(f"  symbol = {q.symbol}    market = {q.market}")
    print(f"  last   = {q.last}")
    print(f"  bid    = {q.bid}")
    print(f"  ask    = {q.ask}")
    print(f"  asof   = {q.asof}")
    if q.last == 0.0:
        print("  NOTE: last=0 — KIS may return 0 for unsupported symbol or"
              " before market open. Check audit jsonl for full payload.")
    return 0


def _probe_positions(adapter: KisBrokerAdapter, market: Optional[str]) -> int:
    _print_section(f"PROBE: positions  market={market or 'US_ALL(dedup)'}  (GET inquire-balance)")
    if market is None:
        rows = adapter.get_positions_all_us()
    else:
        rows = adapter.get_positions(market=market)
    rows = sorted(rows, key=lambda p: p.symbol)
    if not rows:
        print("  (no positions held)")
    else:
        print(f"  {'symbol':8s} {'market':6s} {'qty':>10s} {'avg':>10s}")
        for p in rows:
            print(f"  {p.symbol:8s} {p.market:6s} {p.qty:>10.4f} {p.avg_price:>10.4f}")
        print(f"  ── total rows: {len(rows)}")
    return 0


def _probe_cash(adapter: KisBrokerAdapter, market: str, ref: str) -> int:
    _print_section(f"PROBE: cash  market={market}  ref_symbol={ref}  (GET inquire-psamount)")
    cash = adapter.get_cash(market=market, ref_symbol=ref)
    print(f"  base_ccy  = {cash.base_ccy}")
    print(f"  available = {cash.available:,.4f}")
    print(f"  total     = {cash.total:,.4f}")
    print(f"  asof      = {cash.asof}")
    return 0


def _probe_history(adapter: KisBrokerAdapter) -> int:
    _print_section("PROBE: history  last 7d  (GET inquire-ccnl)")
    rows = adapter.get_order_history()
    print(f"  rows = {len(rows)}")
    for r in rows[:10]:
        odno = r.get("odno") or r.get("ord_no") or "-"
        pdno = r.get("pdno") or r.get("ovrs_pdno") or "-"
        sll  = r.get("sll_buy_dvsn_cd") or r.get("sll_buy_dvsn_cd_name") or "-"
        qty  = r.get("ord_qty") or r.get("ft_ord_qty") or "-"
        prc  = r.get("ft_ord_unpr3") or r.get("ovrs_ord_unpr") or "-"
        print(f"    odno={odno}  {pdno}  side={sll}  qty={qty}  px={prc}")
    return 0


def _probe_open_orders(adapter: KisBrokerAdapter, market: str) -> int:
    """Round 3 P1.A probe: list current open/unfilled overseas orders.

    Returns 0 even if the list is empty (empty list is a valid result —
    just means no orders are currently sitting unfilled). Returns 1 only
    if the underlying call raises (which `_Http.call` reclassifies via
    Broker*Error and lets bubble out)."""
    _print_section(f"PROBE: open orders  market={market}  (GET inquire-nccs)")
    try:
        rows = adapter.get_open_orders(market=market)
    except Exception as e:                                       # noqa: BLE001
        print(f"  [ERROR] {type(e).__name__}: {e}")
        return 1
    print(f"  open orders = {len(rows)}")
    if not rows:
        print("    (none — paper may not surface very-recent ODNOs here either; "
              "submit a tiny test order in another terminal and re-run within seconds)")
    for oo in rows[:25]:
        print(f"    odno={oo.broker_order_id}  {oo.symbol:6s} {oo.market:4s} "
              f"{oo.side:4s} qty={oo.qty_order:>4.0f} filled={oo.qty_filled:>4.0f} "
              f"remain={oo.qty_remaining:>4.0f} @ {oo.limit_price:>8.2f}  "
              f"{oo.ord_dt} {oo.ord_tmd}  status={oo.status_text or '-'}")
    return 0


def _probe_all(adapter: KisBrokerAdapter) -> int:
    rc = _probe_token(adapter)
    if rc: return rc
    rc = _probe_quote(adapter, "AAPL", "NASD")
    if rc: return rc
    rc = _probe_positions(adapter, None)
    if rc: return rc
    rc = _probe_cash(adapter, "NASD", "AAPL")
    if rc: return rc
    rc = _probe_history(adapter)
    if rc: return rc
    rc = _probe_open_orders(adapter, "NASD")
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KIS Broker Adapter — Step 1/2")
    sub = ap.add_subparsers(dest="cmd")

    ap.add_argument("--self-check", action="store_true",
                    help="Step 1 skeleton sanity (no network calls)")

    p_probe = sub.add_parser("probe", help="Step 2 live read-only probes")
    p_probe.add_argument("target", choices=["token", "quote", "positions",
                                            "cash", "history", "open-orders",
                                            "all"])
    p_probe.add_argument("--symbol", default="AAPL")
    p_probe.add_argument("--market", default="NASD",
                         choices=sorted(VALID_TRADE_MARKETS))
    p_probe.add_argument("--all-markets", action="store_true",
                         help="positions: query NASD+NYSE+AMEX merged")
    p_probe.add_argument("--quiet", action="store_true",
                         help="suppress per-call verbose summary")

    args = ap.parse_args(argv)

    if args.self_check:
        return _self_check()

    if args.cmd == "probe":
        cfg = load_env_config()
        adapter = KisBrokerAdapter(cfg=cfg, verbose=not args.quiet)
        print(f"env={cfg.env_name}  account={cfg.account_no}-{cfg.account_product_code}  base_url={cfg.base_url}")
        print(f"audit log: {adapter.audit._path_for_today()}")
        if args.target == "token":
            return _probe_token(adapter)
        if args.target == "quote":
            return _probe_quote(adapter, args.symbol, args.market)
        if args.target == "positions":
            return _probe_positions(adapter, None if args.all_markets else args.market)
        if args.target == "cash":
            return _probe_cash(adapter, args.market, args.symbol)
        if args.target == "history":
            return _probe_history(adapter)
        if args.target == "open-orders":
            return _probe_open_orders(adapter, args.market)
        if args.target == "all":
            return _probe_all(adapter)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
