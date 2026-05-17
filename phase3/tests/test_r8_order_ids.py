"""R8-A — `phase3.autotrade.order_ids.normalize_odno` acceptance tests.

Covers the R8 §3 acceptance list plus a contract check on
``KisBrokerAdapter.get_order()`` (mandatory fix per R8 §1.3 / §3
"Mandatory fix"). The R6 ``echo._norm_odno`` alias must remain
callable so the R6 regression suite keeps passing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PHASE3 = _HERE.parent
_REPO_ROOT = _PHASE3.parent
for _p in (_PHASE3, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from phase3.autotrade.order_ids import normalize_odno, odnos_match
from phase3.autotrade.echo import _norm_odno as echo_norm_odno  # R6 alias
from phase3.autotrade.t10_applicator import _norm_odno as t10_norm_odno


class TestNormalizeOdnoSpec(unittest.TestCase):
    """R8 §3 acceptance list."""

    def test_padded_strips_to_raw(self) -> None:
        self.assertEqual(normalize_odno("0000041467"), "41467")

    def test_raw_is_idempotent(self) -> None:
        self.assertEqual(normalize_odno("41467"), "41467")

    def test_all_zero_preserves_zero(self) -> None:
        self.assertEqual(normalize_odno("0000000000"), "0")

    def test_empty_string(self) -> None:
        self.assertEqual(normalize_odno(""), "")

    def test_none(self) -> None:
        self.assertEqual(normalize_odno(None), "")

    def test_whitespace_only(self) -> None:
        self.assertEqual(normalize_odno("   "), "")

    def test_surrounding_whitespace_stripped(self) -> None:
        self.assertEqual(normalize_odno("  0000041467  "), "41467")

    def test_idempotent(self) -> None:
        once = normalize_odno("0000041467")
        twice = normalize_odno(once)
        thrice = normalize_odno(twice)
        self.assertEqual(once, twice)
        self.assertEqual(twice, thrice)

    def test_numeric_input_accepted(self) -> None:
        # KIS only ever returns strings, but we should not blow up if a
        # caller hands in a parsed int by accident.
        self.assertEqual(normalize_odno(41467), "41467")


class TestOdnosMatch(unittest.TestCase):
    def test_padded_matches_raw(self) -> None:
        self.assertTrue(odnos_match("0000041467", "41467"))

    def test_raw_matches_raw(self) -> None:
        self.assertTrue(odnos_match("41467", "41467"))

    def test_different_orders_do_not_match(self) -> None:
        self.assertFalse(odnos_match("0000041467", "0000041468"))

    def test_empty_never_matches_empty(self) -> None:
        # Two missing ODNOs must NOT be reported as the same order.
        self.assertFalse(odnos_match("", ""))
        self.assertFalse(odnos_match(None, None))
        self.assertFalse(odnos_match("0000041467", ""))


class TestBackwardCompatAliases(unittest.TestCase):
    """R6 / R7-A internal `_norm_odno` callers must keep working."""

    def test_echo_alias_is_normalize_odno(self) -> None:
        self.assertIs(echo_norm_odno, normalize_odno)

    def test_t10_applicator_alias_is_normalize_odno(self) -> None:
        self.assertIs(t10_norm_odno, normalize_odno)

    def test_aliases_behave_identically(self) -> None:
        for v in ("0000041467", "41467", "0000000000", "", None, "  41467 "):
            self.assertEqual(echo_norm_odno(v), normalize_odno(v))
            self.assertEqual(t10_norm_odno(v), normalize_odno(v))


# ──────────────────────────────────────────────────────────────────────
# get_order() padded-vs-raw match
# ──────────────────────────────────────────────────────────────────────
class _FakeAdapterForGetOrder:
    """Mimics enough of ``KisBrokerAdapter`` for the get_order ODNO
    normalization contract test. We bind the real method below so the
    test exercises the actual production code path."""

    def __init__(self, rows):
        self._rows = rows

    def get_order_history(self):
        return list(self._rows)


def _bound_get_order(rows, query):
    """Invoke the real ``KisBrokerAdapter.get_order`` against the fake
    history. In Python 3 the unbound method is a plain function, so we
    just pass our fake instance as the first positional ``self``."""
    from phase3.autotrade.kis_broker_adapter import KisBrokerAdapter
    fake = _FakeAdapterForGetOrder(rows)
    return KisBrokerAdapter.get_order(fake, query)  # type: ignore[arg-type]


class TestKisAdapterGetOrderNormalization(unittest.TestCase):
    """R8 §3 mandatory fix — `get_order()` must match padded against raw."""

    def test_query_padded_matches_raw_row(self) -> None:
        row = {"odno": "41467", "pdno": "AMD"}
        result = _bound_get_order([row], "0000041467")
        self.assertEqual(result, row)

    def test_query_raw_matches_padded_row(self) -> None:
        row = {"odno": "0000041467", "pdno": "AMD"}
        result = _bound_get_order([row], "41467")
        self.assertEqual(result, row)

    def test_no_match_returns_not_found(self) -> None:
        rows = [{"odno": "41467"}, {"odno": "41468"}]
        result = _bound_get_order(rows, "0000099999")
        self.assertEqual(result["status"], "not_found")

    def test_empty_query_returns_not_found(self) -> None:
        rows = [{"odno": "41467"}]
        for q in ("", None, "   "):
            result = _bound_get_order(rows, q)
            self.assertEqual(result["status"], "not_found")

    def test_first_match_wins_when_history_has_padded_and_raw(self) -> None:
        # Pathological-but-possible case: KIS occasionally re-emits the
        # same order in different padding forms across pages. The first
        # normalized match wins, which is the documented behaviour.
        rows = [
            {"odno": "0000041467", "pdno": "AMD", "marker": "first"},
            {"odno": "41467",      "pdno": "AMD", "marker": "second"},
        ]
        result = _bound_get_order(rows, "41467")
        self.assertEqual(result["marker"], "first")


if __name__ == "__main__":
    unittest.main(verbosity=2)
