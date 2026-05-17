"""Round 8 — `KisBrokerAdapter.cancel_order` acceptance tests.

Scope (per R8 §11 "Today: Cancel-Path-Only Order"):

  1. dry-run never hits the network and never raises
  2. real cancel without `KIS_PAPER_CANCEL_OK=true` is refused with
     ``accepted=False`` (no network call)
  3. real cancel with the env gate sends the documented payload
     (``CANO`` / ``ACNT_PRDT_CD`` / ``OVRS_EXCG_CD`` / ``PDNO`` /
     ``ORGN_ODNO`` / ``ORD_QTY`` / ``OVRS_ORD_UNPR='0'`` /
     ``ORD_SVR_DVSN_CD='0'``) to the right endpoint with the right TR_ID
     and parses ``output.ODNO`` as the cancel order id
  4. empty / blank ``broker_order_id`` is refused outright
  5. non-paper env (``env_name='live'``) is refused outright
  6. broker-level rejection (KIS returns rt_cd!=0) is surfaced as
     ``accepted=False`` with the message preserved in ``note``

No real KIS calls; we fake the HTTP layer and auth headers.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo paths are importable (mirror orchestrator.py / t10_applicator).
_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.kis_broker_adapter import (
    CancelResult,
    EnvConfig,
    EP_ORDER_RVSECNCL,
    KisBrokerAdapter,
    SafetyState,
    BrokerResponseError,
    TR,
)


# ──────────────────────────────────────────────────────────────────────
# Fake KIS HTTP + auth layer
# ──────────────────────────────────────────────────────────────────────
class _FakeHttp:
    """Stand-in for ``KisBrokerAdapter.http`` that records calls and
    returns a scripted response. ``raise_with`` lets us simulate a
    KIS-level rejection."""

    def __init__(self, *, response: Dict[str, Any] | None = None,
                 raise_with: Exception | None = None):
        self.response = response or {
            "rt_cd": "0",
            "msg_cd": "OK",
            "msg1": "OK",
            "output": {
                "ODNO": "0000099999",
                "KRX_FWDG_ORD_ORGNO": "00950",
                "ORD_TMD": "000000",
            },
        }
        self.raise_with = raise_with
        self.calls: List[Dict[str, Any]] = []

    def call(self, *, method, path, tr_id, body, auth_headers):
        self.calls.append({
            "method": method, "path": path, "tr_id": tr_id,
            "body": dict(body), "auth_headers": dict(auth_headers),
        })
        if self.raise_with is not None:
            raise self.raise_with
        return self.response


def _build_adapter(*, env: str = "paper", paper_cancel_ok: bool = False,
                   paper_submit_ok: bool = False, http: _FakeHttp | None = None):
    tmp = tempfile.mkdtemp(prefix="r8_cancel_")
    cfg = EnvConfig(
        app_key="FAKE_APP_KEY_xxxxxxxxxx",
        app_secret="FAKE_APP_SECRET_xxxxxxxx",
        account_no="50182047",
        account_product_code="01",
        env_name=env,
        confirm_live=False,
        paper_submit_ok=paper_submit_ok,
        paper_cancel_ok=paper_cancel_ok,
        token_cache_path=Path(tmp) / "tok.json",
        log_dir=Path(tmp) / "audit",
    )
    adapter = KisBrokerAdapter(
        cfg=cfg, safety_state=SafetyState(buy_only_mode=True),
        verbose=False,
    )
    if http is not None:
        adapter.http = http  # bypass real HTTP layer entirely
    # Never let the test reach real auth/token logic.
    adapter._auth_headers = lambda: {"authorization": "Bearer FAKE"}
    return adapter, cfg


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────
class TestDryRunNoNetwork(unittest.TestCase):
    def test_dry_run_records_payload_without_calling_http(self) -> None:
        http = _FakeHttp()
        adapter, _ = _build_adapter(http=http)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=1, dry_run=True,
            note="r8 acceptance test",
        )
        self.assertIsInstance(res, CancelResult)
        self.assertTrue(res.accepted)
        self.assertTrue(res.dry_run)
        self.assertIsNone(res.cancel_order_id)
        self.assertEqual(http.calls, [])
        # Payload shape — exact field-by-field comparison against the
        # KIS official examples_llm/overseas_stock/order_rvsecncl sample.
        self.assertEqual(res.payload, {
            "CANO": "50182047",
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "PDNO": "APA",
            "ORGN_ODNO": "0000042031",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "1",
            "OVRS_ORD_UNPR": "0",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
        })
        # Raw response summary should announce dry-run mode + endpoint.
        self.assertEqual(res.raw_response_summary["mode"], "dry_run")
        self.assertEqual(res.raw_response_summary["endpoint"], EP_ORDER_RVSECNCL)


class TestRealRequiresEnvGate(unittest.TestCase):
    def test_real_cancel_without_gate_is_refused(self) -> None:
        http = _FakeHttp()
        adapter, _ = _build_adapter(http=http, paper_cancel_ok=False)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=1, dry_run=False,
        )
        self.assertFalse(res.accepted)
        self.assertFalse(res.dry_run)
        self.assertEqual(http.calls, [])
        self.assertIn("KIS_PAPER_CANCEL_OK", res.note)


class TestRealSendsPayloadAndTrId(unittest.TestCase):
    def test_real_cancel_with_gate_sends_expected_payload(self) -> None:
        http = _FakeHttp(response={
            "rt_cd": "0", "msg_cd": "0000", "msg1": "OK",
            "output": {
                "ODNO": "0000099999",
                "KRX_FWDG_ORD_ORGNO": "00950",
                "ORD_TMD": "010101",
            },
        })
        adapter, _ = _build_adapter(http=http, paper_cancel_ok=True)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=1, dry_run=False,
        )
        self.assertTrue(res.accepted)
        self.assertFalse(res.dry_run)
        self.assertEqual(res.cancel_order_id, "0000099999")
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], EP_ORDER_RVSECNCL)
        self.assertEqual(call["tr_id"], TR["order_cancel"]["paper"])
        self.assertEqual(call["body"]["ORGN_ODNO"], "0000042031")
        self.assertEqual(call["body"]["RVSE_CNCL_DVSN_CD"], "02")
        self.assertEqual(call["body"]["OVRS_ORD_UNPR"], "0")
        self.assertEqual(call["body"]["MGCO_APTM_ODNO"], "")
        self.assertEqual(call["body"]["ORD_QTY"], "1")
        self.assertEqual(call["body"]["OVRS_EXCG_CD"], "NASD")
        self.assertEqual(call["body"]["PDNO"], "APA")
        # Raw summary surfaces the broker ack + identifiers.
        self.assertEqual(res.raw_response_summary["ODNO"], "0000099999")


class TestEmptyOdnoRefused(unittest.TestCase):
    def test_blank_broker_order_id_is_refused(self) -> None:
        http = _FakeHttp()
        adapter, _ = _build_adapter(http=http, paper_cancel_ok=True)
        for bad in ("", "   ", None):
            res = adapter.cancel_order(
                broker_order_id=bad,  # type: ignore[arg-type]
                symbol="APA", market="NASD",
                qty=1, dry_run=False,
            )
            self.assertFalse(res.accepted)
            self.assertEqual(http.calls, [])
            self.assertIn("empty", res.note.lower())


class TestNonPaperEnvRefused(unittest.TestCase):
    def test_live_env_is_refused_outright(self) -> None:
        http = _FakeHttp()
        adapter, _ = _build_adapter(env="live", http=http, paper_cancel_ok=True)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=1, dry_run=False,
        )
        self.assertFalse(res.accepted)
        self.assertEqual(http.calls, [])
        self.assertIn("paper-only", res.note)


class TestBrokerRejectionSurfaced(unittest.TestCase):
    def test_kis_rt_cd_nonzero_returns_accepted_false_with_note(self) -> None:
        # KIS application-level rejection surfaces as BrokerResponseError;
        # cancel_order catches it and returns a structured failure result.
        http = _FakeHttp(raise_with=BrokerResponseError("rt_cd=1 (이미 취소된 주문)"))
        adapter, _ = _build_adapter(http=http, paper_cancel_ok=True)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=1, dry_run=False,
        )
        self.assertFalse(res.accepted)
        self.assertIsNone(res.cancel_order_id)
        self.assertEqual(len(http.calls), 1)  # the call did happen
        self.assertIn("rt_cd=1", res.note)


class TestQtyValidation(unittest.TestCase):
    def test_zero_qty_refused(self) -> None:
        http = _FakeHttp()
        adapter, _ = _build_adapter(http=http, paper_cancel_ok=True)
        res = adapter.cancel_order(
            broker_order_id="0000042031",
            symbol="APA", market="NASD",
            qty=0, dry_run=True,
        )
        self.assertFalse(res.accepted)
        self.assertEqual(http.calls, [])
        self.assertIn("qty", res.note.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
