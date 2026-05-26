"""R10F-T — integration-test escape hatch for forcing a reprice
quote-chase in live paper.

These tests pin the env-var gate that allows ``_on_generate_all_intents``
(and the R11A in-process generator) to accept negative batch / quote
pads. Negative pad means the limit price will be BELOW the live ask,
which deterministically triggers the reprice quote-chase path on the
first poll — that is exactly what we need to verify B2 and to create a
stuck CID for B3.

We test only the gate helper here because exercising the full Tk click
handler requires a display and real event loop. The behaviour at the
call sites is covered indirectly: with the gate False (default), the
negative-pad branches return early; with the gate True, the
``messagebox`` / log banner branches execute.

NOTE: This is intentionally a hidden flag. The panel surfaces a
modal warning ("TEST MODE — negative pad allowed") on every click
that uses it, and the panel log prints a [TEST MODE] banner that the
operator (and the post-trade SMTP report) will see."""

from __future__ import annotations

import unittest

from phase3.autotrade import control_panel as cp


class TestAllowNegativePadEnabled(unittest.TestCase):

    def test_unset_is_false(self):
        self.assertFalse(cp._allow_negative_pad_enabled({}))

    def test_empty_string_is_false(self):
        self.assertFalse(cp._allow_negative_pad_enabled(
            {"AUTOTRADE_ALLOW_NEGATIVE_PAD": ""}))

    def test_explicit_false_is_false(self):
        for val in ("false", "False", "FALSE", "0", "no", "off", "n"):
            with self.subTest(val=val):
                self.assertFalse(cp._allow_negative_pad_enabled(
                    {"AUTOTRADE_ALLOW_NEGATIVE_PAD": val}),
                    f"{val!r} must be treated as false")

    def test_truthy_values_are_true(self):
        for val in ("true", "True", "TRUE", "1", "yes", "y", "on", "ON"):
            with self.subTest(val=val):
                self.assertTrue(cp._allow_negative_pad_enabled(
                    {"AUTOTRADE_ALLOW_NEGATIVE_PAD": val}),
                    f"{val!r} must be treated as true")

    def test_whitespace_is_stripped(self):
        self.assertTrue(cp._allow_negative_pad_enabled(
            {"AUTOTRADE_ALLOW_NEGATIVE_PAD": "  true  "}))
        self.assertFalse(cp._allow_negative_pad_enabled(
            {"AUTOTRADE_ALLOW_NEGATIVE_PAD": "   "}))

    def test_garbage_is_false(self):
        """Anything that is neither a recognised truthy nor falsy
        token defaults to False. We want the safety guard to stay
        ARMED if the operator typos the value."""
        for val in ("maybe", "TRUEISH", "0.9", "enabled-please"):
            with self.subTest(val=val):
                self.assertFalse(cp._allow_negative_pad_enabled(
                    {"AUTOTRADE_ALLOW_NEGATIVE_PAD": val}))

    def test_uses_os_environ_when_env_arg_omitted(self):
        """Production call path: panel just calls
        ``_allow_negative_pad_enabled()`` with no args."""
        import os
        prev = os.environ.pop("AUTOTRADE_ALLOW_NEGATIVE_PAD", None)
        try:
            self.assertFalse(cp._allow_negative_pad_enabled())
            os.environ["AUTOTRADE_ALLOW_NEGATIVE_PAD"] = "true"
            self.assertTrue(cp._allow_negative_pad_enabled())
        finally:
            os.environ.pop("AUTOTRADE_ALLOW_NEGATIVE_PAD", None)
            if prev is not None:
                os.environ["AUTOTRADE_ALLOW_NEGATIVE_PAD"] = prev


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
