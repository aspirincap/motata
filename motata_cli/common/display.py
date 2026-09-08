"""Platform-independent console rendering shared by all CLI namespaces."""
from __future__ import annotations

import json
from typing import Any

_OUTPUT_MODE = "json"
_OUTPUT_EXPLICIT = False


def configure_output(mode: str | None) -> None:
    global _OUTPUT_MODE, _OUTPUT_EXPLICIT
    _OUTPUT_MODE = mode or "json"
    _OUTPUT_EXPLICIT = mode is not None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _print_table(payload: Any) -> None:
    if isinstance(payload, dict):
        rows = [{"key": key, "value": _stringify(value)} for key, value in payload.items()]
    elif isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        rows = [{key: _stringify(value) for key, value in item.items()} for item in payload]
    else:
        print(_stringify(payload))
        return
    if not rows:
        print("")
        return
    columns = list(rows[0].keys())
    for row in rows[1:]:
        for key in row:
            if key not in columns:
                columns.append(key)
    widths = {column: max(len(column), *(len(_stringify(row.get(column, ""))) for row in rows)) for column in columns}
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(_stringify(row.get(column, "")).ljust(widths[column]) for column in columns))


def print_output(payload: Any, *, as_json: bool = False) -> None:
    mode = "json" if not _OUTPUT_EXPLICIT and as_json else _OUTPUT_MODE
    if mode == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, (dict, list)) else _stringify(payload))
    elif mode == "plain":
        print(_stringify(payload))
    elif mode == "table":
        _print_table(payload)
    else:
        raise ValueError(f"Unsupported output mode: {mode}")
