"""Structured, redacted platform errors; no automatic write retries."""
from __future__ import annotations

from motata_cli.common.errors import CliError
from motata_cli.common.security import redact

RATE_LIMIT_CODES = frozenset({'40100', '40108', '40109'})


class TikTokError(CliError):
    def __init__(self, message, *, http_status=None, platform_code=None,
                 platform_request_id=None, category="other", retry_after=None,
                 write_outcome="not_submitted"):
        super().__init__(message)
        self.http_status = http_status
        self.platform_code = platform_code
        self.platform_request_id = platform_request_id
        self.category = category
        self.retry_after = retry_after
        self.write_outcome = write_outcome
        self.retry_managed = False
        self.attempts = 1
        self.retry_stop_reason = None


def check_response(data, *, token=None, status=200, write=False, retry_after=None, secrets=()):
    unknown = "unknown" if write else "not_submitted"
    suffix = "; verify remote state before retrying" if write else ""
    if not isinstance(data, dict) or "code" not in data or isinstance(data.get("code"), bool):
        category = 'rate_limit' if status == 429 else 'auth' if status in (401, 403) else 'network' if status >= 500 else 'protocol'
        raise TikTokError("TikTok API returned an invalid response object" + suffix,
                          http_status=status, category=category, retry_after=retry_after, write_outcome=unknown)
    code = data["code"]
    if 200 <= status < 300 and code in (0, "0"):
        return data
    secrets = tuple(secrets) + ((token,) if token else ())
    safe = redact(data, secrets)
    text = str(safe.get("message", "Request rejected"))
    category = "other"
    # 40002 also covers invalid parameters/tokens: match the QPS sentinel only.
    qps_limited = (str(code) == '40002' and isinstance(safe.get('message'), str)
                   and text.strip().rstrip('.').strip().upper() == 'OVER_QPS_LIMIT')
    if status == 429 or str(code) in RATE_LIMIT_CODES or qps_limited or any(
            word in text.lower() for word in ('too many requests', 'rate limit', 'rate_limit', 'throttl')):
        category = "rate_limit"
    elif status in (401, 403) or str(code) == "40105" or any(
            word in text.lower() for word in ("access token", "access_token", "permission", "unauthorized", "authentication")):
        category = "auth"
    elif status >= 500:
        category = "network"
    elif any(word in text.lower() for word in ("metric", "dimension")) and any(
            word in text.lower() for word in ("invalid", "unsupported", "not support", "incompatible")):
        category = "metrics"
    outcome = "unknown" if write and (status >= 500 or code in (0, "0")) else "failed"
    message = redact(f"TikTok API request failed: HTTP {status}, Error Code: {code}, "
                     f"message: {text}, request_id: {safe.get('request_id')}", secrets)
    if outcome == "unknown":
        message += suffix
    raise TikTokError(message, http_status=status, platform_code=code,
                      platform_request_id=safe.get("request_id"), category=category,
                      retry_after=retry_after, write_outcome=outcome)
