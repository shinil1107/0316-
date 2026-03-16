from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping


def build_runtime_context(namespace: Mapping[str, Any]) -> SimpleNamespace:
    payload = {key: value for key, value in namespace.items() if not str(key).startswith("__")}
    return SimpleNamespace(**payload)
