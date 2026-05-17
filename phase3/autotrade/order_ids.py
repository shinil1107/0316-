"""R8-A — single source of truth for KIS overseas ODNO normalization.

Background (Round 6, 2026-05-15)
--------------------------------
KIS overseas APIs are inconsistent about ODNO format:

  - ``POST .../trading/order`` (place) returns ODNO **zero-padded** to 10
    chars, e.g. ``"0000041461"``.
  - ``GET  .../trading/inquire-ccnl``  returns the same ODNO **stripped**
    of leading zeros, e.g. ``"41461"``.
  - ``GET  .../trading/inquire-nccs``  also strips leading zeros (when it
    surfaces the row at all — paper does not, see R7-B).
  - ``POST .../trading/order-rvsecncl`` (R8 cancel) requires the original
    **zero-padded** form in ``ORGN_ODNO``.

A naive string compare therefore mis-classifies real fills, cancels,
and open orders as ``UNKNOWN`` / ``not_found``. R6 introduced
``echo._norm_odno`` to fix the echo-poll path; R7-A duplicated the same
helper into ``t10_applicator``; R8-A centralizes it here so that every
adapter / parser / manager / applicator goes through one function.

Contract
--------
``normalize_odno`` is for **matching ODNOs across responses**.

It is **not** for shaping outbound payloads:
  - ``ORGN_ODNO`` in the cancel/modify request body MUST be the exact
    surface form returned by ``place_order`` (zero-padded on paper). Do
    not normalize before serializing.
  - All adapter response-matching code SHOULD route through
    ``normalize_odno`` before comparing.
"""
from __future__ import annotations

from typing import Any


def normalize_odno(value: Any) -> str:
    """Return a comparison-safe form of a KIS overseas ODNO.

    Rules:
      - ``None`` / empty / whitespace-only → ``""``
      - strip surrounding whitespace
      - strip leading zeros, but preserve ``"0"`` itself (so an ODNO of
        literal ``"0000000000"`` does not collapse to the empty string)
      - idempotent: ``normalize_odno(normalize_odno(x)) == normalize_odno(x)``

    Examples:
      >>> normalize_odno("0000041461")
      '41461'
      >>> normalize_odno("41461")
      '41461'
      >>> normalize_odno("  0000041461  ")
      '41461'
      >>> normalize_odno("0000000000")
      '0'
      >>> normalize_odno("")
      ''
      >>> normalize_odno(None)
      ''
    """
    s = str(value or "").strip()
    if not s:
        return ""
    stripped = s.lstrip("0")
    return stripped or "0"


def odnos_match(a: Any, b: Any) -> bool:
    """Convenience predicate: True iff two ODNOs refer to the same order
    after normalization. Empty / blank ODNOs never match anything
    (including another empty ODNO), so callers cannot accidentally
    "match" two missing IDs.
    """
    na = normalize_odno(a)
    nb = normalize_odno(b)
    if not na or not nb:
        return False
    return na == nb
