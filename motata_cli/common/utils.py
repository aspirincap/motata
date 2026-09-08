from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from motata_cli.common.errors import CliError


def env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default



def now_ts() -> int:
    return int(time.time())



def normalize_account_id(account_id: str) -> str:
    account_id = str(account_id).strip()
    return account_id[4:] if account_id.startswith("act_") else account_id



def load_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))



def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").replace("\ufffd", " ")
    text = "".join(" " if unicodedata.category(ch) == "So" else ch for ch in text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text



def json_or_none(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)



def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))



def parse_json_option(raw: Any, label: str, expected_type: type | tuple[type, ...] | None = None) -> Any:
    if raw in (None, ""):
        return None
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(f"Invalid {label} JSON: {exc.msg}") from exc
    if expected_type and not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            names = ", ".join(t.__name__ for t in expected_type)
        else:
            names = expected_type.__name__
        raise CliError(f"Invalid {label}: expected {names}")
    return value



def parse_positive_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CliError(f"Invalid {label}: expected a positive integer") from exc
    if parsed <= 0:
        raise CliError(f"Invalid {label}: expected a positive integer")
    return parsed



def parse_positive_int_str(value: Any, label: str) -> str | None:
    parsed = parse_positive_int(value, label)
    return str(parsed) if parsed is not None else None



def validate_name(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        raise CliError(f"{label} cannot be empty")
    if len(normalized) > 400:
        raise CliError(f"{label} exceeds 400 characters")
    return normalized



def validate_non_empty(value: str | None, label: str) -> str:
    normalized = validate_name(value, label)
    if normalized is None:
        raise CliError(f"Missing {label}")
    return normalized



def validate_iso_datetime(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CliError(f"Invalid {label}: expected ISO 8601 datetime") from exc
    return normalized



def validate_domain(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", normalized):
        raise CliError(f"Invalid {label}: expected a domain like example.com")
    return normalized



def parse_fields(fields: list[str] | None, default: list[str]) -> str:
    return ",".join(fields or default)



def filter_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
