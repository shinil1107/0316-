"""R10F-Q2 — OrderManagementPolicy.from_env + dataclass default lift.

R10E acceptance (2026-05-20) showed CIEN gapped +1.5 % overnight and
our 10-bps step / 35-bps ceiling could not keep up: every reprice
landed below market and the order died unfilled. R10F-Q2

  1. lifts the defaults (step 30, cap 120, attempts 4, wait 60s) so
     a default install can chase a ~1 % open;
  2. adds ``OrderManagementPolicy.from_env(env)`` so the operator can
     tune per-session via four env vars without changing code.

Tests cover:

* New defaults match the R10F-Q2 spec.
* ``from_env`` parses each var into the correct field type.
* Unset / blank / unparseable cells fall back to the dataclass default
  silently (we'd rather run with defaults than abort because of a
  typo).
* CIEN-class scenario: with the new defaults, the reprice walker
  reaches the new ceiling instead of being stuck at the old 35 bps
  cap.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.order_manager import (
    OrderManagementPolicy,
    reprice_limit_buy,
)


class TestDefaultsAreLifted(unittest.TestCase):
    """R10F-Q2 dataclass-default surface."""

    def test_new_defaults(self):
        p = OrderManagementPolicy()
        self.assertEqual(p.reprice_step_bps, 30.0)
        self.assertEqual(p.max_total_slippage_bps, 120.0)
        self.assertEqual(p.max_reprice_attempts, 4)
        self.assertEqual(p.max_wait_sec, 60.0)
        self.assertEqual(p.poll_interval_sec, 5.0)
        self.assertEqual(p.ccnl_poll_retry_count, 2)


class TestFromEnvHappyPath(unittest.TestCase):

    def test_all_vars_applied(self):
        env = {
            "AUTOTRADE_POLL_INTERVAL_SEC": "3",
            "AUTOTRADE_MAX_WAIT_SEC": "45",
            "AUTOTRADE_MAX_REPRICE_ATTEMPTS": "6",
            "AUTOTRADE_REPRICE_STEP_BPS": "50",
            "AUTOTRADE_MAX_SLIPPAGE_BPS": "200",
            "AUTOTRADE_CCNL_RETRY_COUNT": "4",
            "AUTOTRADE_CCNL_RETRY_BACKOFF_SEC": "1.5",
        }
        p = OrderManagementPolicy.from_env(env)
        self.assertEqual(p.poll_interval_sec, 3.0)
        self.assertEqual(p.max_wait_sec, 45.0)
        self.assertEqual(p.max_reprice_attempts, 6)
        self.assertEqual(p.reprice_step_bps, 50.0)
        self.assertEqual(p.max_total_slippage_bps, 200.0)
        self.assertEqual(p.ccnl_poll_retry_count, 4)
        self.assertEqual(p.ccnl_poll_retry_backoff_sec, 1.5)

    def test_empty_env_returns_defaults(self):
        p = OrderManagementPolicy.from_env({})
        d = OrderManagementPolicy()
        self.assertEqual(p, d)


class TestFromEnvFallbacks(unittest.TestCase):

    def test_unparseable_value_falls_back(self):
        env = {
            "AUTOTRADE_REPRICE_STEP_BPS": "thirty",
            "AUTOTRADE_MAX_SLIPPAGE_BPS": "150",
        }
        p = OrderManagementPolicy.from_env(env)
        d = OrderManagementPolicy()
        self.assertEqual(p.reprice_step_bps, d.reprice_step_bps)
        self.assertEqual(p.max_total_slippage_bps, 150.0)

    def test_blank_string_falls_back(self):
        env = {"AUTOTRADE_REPRICE_STEP_BPS": ""}
        p = OrderManagementPolicy.from_env(env)
        d = OrderManagementPolicy()
        self.assertEqual(p.reprice_step_bps, d.reprice_step_bps)

    def test_int_field_accepts_float_string(self):
        env = {"AUTOTRADE_MAX_REPRICE_ATTEMPTS": "5.9"}
        p = OrderManagementPolicy.from_env(env)
        self.assertEqual(p.max_reprice_attempts, 5)

    def test_immutable_fields_remain_defaults(self):
        """``allow_market_order`` and ``cancel_before_reprice`` must NOT
        be overridable via env (safety invariants)."""
        env = {
            "AUTOTRADE_ALLOW_MARKET_ORDER": "true",
            "AUTOTRADE_CANCEL_BEFORE_REPRICE": "false",
        }
        p = OrderManagementPolicy.from_env(env)
        self.assertFalse(p.allow_market_order)
        self.assertTrue(p.cancel_before_reprice)


class TestCienScenarioWithNewDefaults(unittest.TestCase):
    """End-to-end reprice walker simulation that proves the new
    defaults chase a +1.5 % gap-up where the R8 defaults would not.

    CIEN intent: original limit 557.4419. Market trading ~564.84.
    """

    def test_r8_defaults_stuck_below_market(self):
        """Reproduce the failure mode: 10 bps step, 35 bps cap, 2
        attempts only — walker peaks at 558.5573 (matches the JSONL of
        2026-05-20's CIEN intent), well below the 564.84 market and
        also below the ceiling (559.39). Two attempts isn't even enough
        to reach the cap."""
        p = OrderManagementPolicy(
            reprice_step_bps=10.0,
            max_total_slippage_bps=35.0,
            max_reprice_attempts=2,
        )
        original = 557.4419
        cur = original
        seen = [cur]
        for _ in range(p.max_reprice_attempts):
            cur = reprice_limit_buy(
                original_limit=original, current_limit=cur, policy=p)
            seen.append(cur)
        market = 564.84
        ceiling = round(original * (1 + p.max_total_slippage_bps / 10_000.0), 4)
        # Real CIEN sequence: 557.4419 -> 557.9993 -> 558.5573
        self.assertAlmostEqual(seen[-1], 558.5573, places=4)
        # The walker never even reached the cap (attempts ran out first).
        self.assertLess(seen[-1], ceiling)
        # And of course nowhere near the live market.
        self.assertLess(seen[-1], market - 5.0)

    def test_new_defaults_walk_close_to_market(self):
        """With the new defaults the walker reaches within ~1 % of
        market — close enough that a quote retry (R10F-Q3) or a slight
        further uptick would fill."""
        p = OrderManagementPolicy()  # NEW defaults
        original = 557.4419
        cur = original
        seen = [cur]
        for _ in range(p.max_reprice_attempts):
            cur = reprice_limit_buy(
                original_limit=original, current_limit=cur, policy=p)
            seen.append(cur)
        # Ceiling = 557.4419 * 1.012 = 564.13.
        ceiling = round(original * (1 + p.max_total_slippage_bps / 10_000.0), 4)
        self.assertAlmostEqual(max(seen), ceiling, places=4)
        # Ceiling lifted to within $1 of the observed market price.
        self.assertGreater(max(seen), 564.84 - 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
