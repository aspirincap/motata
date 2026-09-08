"""Shared result completeness and conservative read-only retry policy."""
from __future__ import annotations

import time
from typing import Any, Callable


def error_kind(exc: Exception) -> str:
    text = str(exc).lower()
    if any(x in text for x in ('429', '40108', '40109', 'rate limit', 'too many requests', 'throttl')):
        return 'rate_limit'
    if any(x in text for x in ('401', '403', '40001', '40002', '40100', 'unauthorized', 'forbidden', 'access token', 'access_token', 'permission', 'authentication')):
        return 'auth'
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(x in text for x in ('timeout', 'timed out', 'connection', '502', '503', '504')):
        return 'network'
    if ('metric' in text or 'dimension' in text) and any(x in text for x in ('invalid', 'unsupported', 'not support', 'incompatible', 'not allowed')):
        return 'metrics'
    return 'other'


def request_with_retry(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Use only for reads: retry identical parameters at most three times."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if error_kind(exc) not in {'network', 'rate_limit'} or attempt == 2:
                raise
            try:
                wait = float(getattr(exc, 'retry_after', None))
            except (TypeError, ValueError):
                wait = 2 ** attempt
            time.sleep(min(60.0, max(0.0, wait)))


def unsupported_metrics(exc: Exception) -> bool:
    text = str(exc).lower()
    return error_kind(exc) == 'metrics' and 'metric' in text and any(
        marker in text for marker in ('unsupported', 'not support', 'not allowed', 'invalid metric', 'metrics invalid', 'incompatible')
    )


def pagination(page: int, page_size: int, count: int, total_pages: int = 0, *, row_limit: bool = False) -> dict[str, Any]:
    exhausted = page >= total_pages if total_pages else count < page_size
    truncated = row_limit or not exhausted
    return {'truncated': truncated, 'stop_reason': 'max_rows' if row_limit else ('exhausted' if exhausted else 'max_pages'),
            'pages_fetched': page, 'total_pages': total_pages or None}


def completeness(payload: Any) -> dict[str, Any]:
    reasons: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ('error', '_error', 'errors', 'account_errors', 'warnings', 'account_warnings', 'truncated'):
                if value.get(key):
                    reasons.add(key)
            if value.get('status') in {'failed', 'degraded', 'partial_success'}:
                reasons.add(str(value['status']))
            if value is not payload and isinstance(value.get('completeness'), dict):
                nested_status = value['completeness'].get('status')
                if nested_status in {'failed', 'partial_success', 'degraded'}:
                    reasons.add(nested_status)
            for key, child in value.items():
                if key != 'completeness':
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    status = 'partial_success' if reasons else 'success'
    if isinstance(payload, dict):
        children = payload.get('sources') or payload.get('results') or payload.get('runs')
        if isinstance(children, list) and children:
            states = [completeness(child)['status'] for child in children
                      if isinstance(child, dict) and child.get('status') not in {'skipped', 'planned'}]
            if states and all(state == 'failed' for state in states):
                status = 'failed'
        elif payload.get('status') == 'failed' or (payload.get('error') and not payload.get('rows')):
            status = 'failed'
        elif payload.get('account_errors') and not payload.get('rows') and not payload.get('ad_count'):
            status = 'failed'
    return {'schema_version': 1, 'status': status, 'complete': status == 'success',
            'exit_code': {'success': 0, 'partial_success': 3, 'failed': 1}[status],
            'reasons': sorted(reasons)}


def finalize(payload: dict[str, Any]) -> dict[str, Any]:
    payload['completeness'] = completeness(payload)
    return payload
