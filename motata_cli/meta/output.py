from __future__ import annotations

import json
from typing import Any

_OUTPUT_MODE = "json"
_OUTPUT_EXPLICIT = False


def configure_output(mode: str | None) -> None:
    global _OUTPUT_MODE, _OUTPUT_EXPLICIT
    if mode is None:
        _OUTPUT_MODE = "json"
        _OUTPUT_EXPLICIT = False
        return
    _OUTPUT_MODE = mode
    _OUTPUT_EXPLICIT = True


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
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    widths = {
        column: max(len(column), *(len(_stringify(row.get(column, ""))) for row in rows))
        for column in columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    divider = "  ".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in rows:
        line = "  ".join(_stringify(row.get(column, "")).ljust(widths[column]) for column in columns)
        print(line)


def print_output(payload: Any, *, as_json: bool = False) -> None:
    mode = _OUTPUT_MODE
    if not _OUTPUT_EXPLICIT and as_json:
        mode = "json"

    if mode == "json":
        if isinstance(payload, (dict, list)):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(_stringify(payload))
        return

    if mode == "plain":
        if isinstance(payload, (dict, list)):
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return
        print(_stringify(payload))
        return

    if mode == "table":
        _print_table(payload)
        return

    raise ValueError(f"Unsupported output mode: {mode}")
