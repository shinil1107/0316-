"""V1-H — per-ticker resilience in ``default_manage_loop_fn``.

5/29 post-mortem: HPE's reco close (38.21) gapped +10.5% to 42.23, so
the reprice ladder hit the slippage ceiling and the order was cancelled
unfilled. The pre-V1-H manage loop ``break``-ed on the first non-FILLED
outcome, so the SECOND intent (CSCO) never even reached the broker, and
t10_applicator then aborted the whole batch.

V1-H makes the loop CONTINUE past a cleanly-terminal miss
(CANCELLED / REJECTED — broker truth known, zero fill) so the next
ticker still gets its shot. It still STOPS on states that are ambiguous
or risk state-drift if we pile more orders on top (UNKNOWN /
OPEN_OR_PENDING / CANCEL_REQUESTED / PARTIALLY_FILLED).

These tests pin that contract by mocking the broker boundary so we can
drive ``manage_order`` to return arbitrary terminal states.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_ROOT = _PHASE3.parent
for _p in (_PHASE3, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade import daily_runner as dr
from phase3.autotrade.kis_broker_adapter import OrderIntent
from phase3.autotrade.order_manager import ManagedOrderOutcome
from phase3.autotrade.order_state import OrderState


def _intent(symbol: str, rid: int) -> OrderIntent:
    return OrderIntent(
        symbol=symbol, market="NASD", side="BUY", qty=2,
        order_type="LIMIT", limit_price=10.0,
        client_order_id=f"co-rid-{rid}-B-2-{symbol}",
        rec_row_id=rid,
    )


def _outcome(intent: OrderIntent, state: OrderState) -> ManagedOrderOutcome:
    return ManagedOrderOutcome(
        final_state=state,
        intent=intent,
        last_broker_order_id="0000000001",
        last_normalized_odno="0000000001",
        qty_filled=(float(intent.qty) if state == OrderState.FILLED else 0.0),
        qty_remaining=(0.0 if state == OrderState.FILLED else float(intent.qty)),
        avg_fill_price=(10.0 if state == OrderState.FILLED else None),
        last_limit_price=10.0,
        cancel_attempts=0,
        reprice_attempts=0,
        elapsed_sec=0.1,
        note=f"test {state.value}",
    )


class _Ctx:
    """Minimal DailyRunContext stand-in for the fields the manage loop
    reads (run_dir, autotrade_run_id, run_id)."""

    def __init__(self, tmp: Path):
        self.run_dir = tmp
        self.autotrade_run_id = "at-test"
        self.run_id = "rid"


class _ManageLoopHarness(unittest.TestCase):
    """Patches the broker boundary so default_manage_loop_fn runs in
    process. ``state_plan`` maps symbol -> OrderState that the mocked
    manage_order should return for that intent."""

    def _run(self, intents, state_plan, *, env=None):
        calls = {"symbols": []}

        def _fake_manage_order(intent, **kwargs):
            calls["symbols"].append(intent.symbol)
            return _outcome(intent, state_plan[intent.symbol])

        fake_cfg = mock.Mock(env_name="paper", is_paper=True)
        env = env or {}
        with mock.patch.object(dr, "manage_order", _fake_manage_order), \
                mock.patch.object(dr, "OrderStore", lambda *a, **k: mock.Mock()), \
                mock.patch(
                    "phase3.autotrade.kis_broker_adapter.load_env_config",
                    lambda: fake_cfg), \
                mock.patch(
                    "phase3.autotrade.kis_broker_adapter.KisBrokerAdapter",
                    lambda **k: mock.Mock()), \
                mock.patch(
                    "phase3.autotrade.kis_broker_adapter.SafetyState",
                    lambda **k: mock.Mock()), \
                mock.patch.dict("os.environ", env, clear=False):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                ctx = _Ctx(Path(td))
                outcomes = dr.default_manage_loop_fn(ctx, intents)
        return outcomes, calls["symbols"]


class TestContinueOnCleanMiss(_ManageLoopHarness):

    def test_cancelled_first_continues_to_next_ticker(self):
        """The 5/29 shape: ticker A cancels (unfilled), B fills. The
        loop must reach B and return BOTH outcomes."""
        intents = [_intent("MISS", 90), _intent("FILL", 91)]
        outcomes, seen = self._run(
            intents,
            {"MISS": OrderState.CANCELLED, "FILL": OrderState.FILLED},
        )
        self.assertEqual(seen, ["MISS", "FILL"])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0].final_state, OrderState.CANCELLED)
        self.assertEqual(outcomes[1].final_state, OrderState.FILLED)

    def test_rejected_also_continues(self):
        intents = [_intent("REJ", 1), _intent("OK", 2)]
        outcomes, seen = self._run(
            intents,
            {"REJ": OrderState.REJECTED, "OK": OrderState.FILLED},
        )
        self.assertEqual(seen, ["REJ", "OK"])
        self.assertEqual(len(outcomes), 2)

    def test_all_cancelled_visits_every_ticker(self):
        intents = [_intent("A", 1), _intent("B", 2), _intent("C", 3)]
        outcomes, seen = self._run(
            intents,
            {"A": OrderState.CANCELLED, "B": OrderState.CANCELLED,
             "C": OrderState.CANCELLED},
        )
        self.assertEqual(seen, ["A", "B", "C"])
        self.assertEqual(len(outcomes), 3)


class TestStopOnAmbiguous(_ManageLoopHarness):

    def test_unknown_stops_the_loop(self):
        """UNKNOWN = broker truth unclear. Never pile on more orders;
        stop so the runner-level evaluator hard-stops for review."""
        intents = [_intent("AMB", 1), _intent("NEVER", 2)]
        outcomes, seen = self._run(
            intents,
            {"AMB": OrderState.UNKNOWN, "NEVER": OrderState.FILLED},
        )
        self.assertEqual(seen, ["AMB"])
        self.assertEqual(len(outcomes), 1)

    def test_partial_fill_stops_the_loop(self):
        """A partial fill means real shares changed hands; t10 will
        whole-batch-abort without --allow-partial, so stop here."""
        intents = [_intent("PART", 1), _intent("NEVER", 2)]
        outcomes, seen = self._run(
            intents,
            {"PART": OrderState.PARTIALLY_FILLED,
             "NEVER": OrderState.FILLED},
        )
        self.assertEqual(seen, ["PART"])
        self.assertEqual(len(outcomes), 1)

    def test_open_or_pending_stops_the_loop(self):
        intents = [_intent("OPEN", 1), _intent("NEVER", 2)]
        outcomes, seen = self._run(
            intents,
            {"OPEN": OrderState.OPEN_OR_PENDING,
             "NEVER": OrderState.FILLED},
        )
        self.assertEqual(seen, ["OPEN"])
        self.assertEqual(len(outcomes), 1)


class TestEnvToggle(_ManageLoopHarness):

    def test_continue_disabled_restores_legacy_break(self):
        """``AUTOTRADE_CONTINUE_ON_UNFILLED=0`` reverts to the pre-V1-H
        behaviour: break on the first non-FILLED outcome."""
        intents = [_intent("MISS", 1), _intent("NEVER", 2)]
        outcomes, seen = self._run(
            intents,
            {"MISS": OrderState.CANCELLED, "NEVER": OrderState.FILLED},
            env={"AUTOTRADE_CONTINUE_ON_UNFILLED": "0"},
        )
        self.assertEqual(seen, ["MISS"])
        self.assertEqual(len(outcomes), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
