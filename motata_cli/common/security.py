"""Redaction for diagnostics only; never alter request or successful response data."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, quote_plus, unquote

REDACTED = "[REDACTED]"
_SECRET = r"(?:access[-_]?token|refresh[-_]?token|token|authorization|proxy[-_]?authorization|(?:x[-_]?)?api[-_]?key|appsecret_proof|(?:app|client)[-_]?secret|secret|password|cookie|set-cookie)"
_PAIR = re.compile(r"(?i)(\b" + _SECRET + r"\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&,;}]+)")
_KEY = re.compile(r"(?i)^" + _SECRET + r"$")
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]*")


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact credentials in headers, payloads, URLs and error text."""
    if isinstance(value, dict):
        return {key: REDACTED if _KEY.fullmatch(str(key)) else redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    # Serialized JSON may contain escaped quotes or further JSON strings. Parse
    # before regex redaction so a quote inside a secret cannot expose its suffix.
    if value.lstrip().startswith(("{", "[", '"')):
        try:
            parsed = json.loads(value)
        except ValueError:
            pass
        else:
            return json.dumps(redact(parsed, secrets), ensure_ascii=False)
    # Decode URL escapes as diagnostics need not preserve a request's wire format.
    text = unquote(value)
    for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        for variant in (secret, quote(secret, safe=""), quote_plus(secret)):
            text = text.replace(variant, REDACTED)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;}]+", r"\1 " + REDACTED, text)
    # Semicolons separate cookies, not diagnostic fields. Redact the whole
    # header line before the generic key/value rule can expose later cookies.
    text = _COOKIE_HEADER.sub(lambda match: match.group(1) + REDACTED, text)
    text = _PAIR.sub(lambda match: match.group(1) + REDACTED, text)
    return re.sub(r"(https?://)[^/\s@]+@", r"\1" + REDACTED + "@", text)


def network_error(source: str, exc: Exception, *, write: bool = False) -> str:
    """Do not echo transport exception strings: they may contain arbitrary bodies."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    detail = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
    message = f"{source} request failed ({detail})."
    if write:
        message += " Write outcome may be unknown; verify remote state before retrying."
    return message
