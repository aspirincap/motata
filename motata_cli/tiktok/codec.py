"""TikTok wire encoding: preserve explicit empty values and unknown fields."""
from __future__ import annotations

import json
from typing import Any


def compact(values: dict[str, Any]) -> dict[str, Any]:
    # None means omitted for keyword query/form parameters. JSON bodies are not
    # compacted: an explicit JSON null can have different update semantics.
    return {key: value for key, value in values.items() if value is not None}


def scalar(value: Any) -> str:
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def query(values: dict[str, Any] | None) -> dict[str, str]:
    return {key: scalar(value) for key, value in compact(values or {}).items()}


def form(values: dict[str, Any] | None) -> dict[str, str]:
    return query(values)
