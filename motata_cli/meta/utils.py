from __future__ import annotations

import json
from typing import Any
from motata_cli.common.errors import CliError
from motata_cli.common.utils import normalize_account_id


def ad_account_path(account_id: str) -> str:
    account_id = normalize_account_id(account_id)
    return f"act_{account_id}"



def parse_meta_error_payload(exc: Exception) -> dict[str, Any] | None:
    if not isinstance(exc, CliError):
        return None
    try:
        payload = json.loads(str(exc))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
