"""Endpoint responses cross one mandatory, bounded credential-safety boundary."""
from __future__ import annotations
import base64
import copy
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit
from .errors import GatewayError
from .protocol import PlatformRequest, SECRET_KEYS

SECRET_FIELD = re.compile(
    r'(?i)^(?:token|access[-_]?token|refresh[-_]?token|authorization|proxy[-_]?authorization|(?:x[-_]?)?api[-_]?key|appsecret_proof|(?:app|client)[-_]?secret|secret|password|cookie|set-cookie)$')
URL_KEYS = re.compile(r'(?i)^(?:token|access_token|refresh_token|api_key|key|signature|sig|x-amz-signature|x-goog-signature|appsecret_proof)$')


def strict_json(raw: bytes | str):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Non-finite JSON number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def sanitize(value, known_secrets: tuple[str, ...], depth=0):
    if depth > 32:
        raise GatewayError('RESPONSE_BLOCKED', 502, 'Platform response nesting exceeded the safety limit.')
    if isinstance(value, dict):
        return {sanitize(key, known_secrets, depth + 1): sanitize(item, known_secrets, depth + 1)
                for key, item in value.items() if not SECRET_FIELD.fullmatch(str(key))}
    if isinstance(value, list):
        return [sanitize(item, known_secrets, depth + 1) for item in value]
    if isinstance(value, str):
        variants = []
        for secret in known_secrets:
            if secret:
                variants.extend((secret, quote(secret, safe=''), quote_plus(secret),
                                 base64.b64encode(secret.encode()).decode(),
                                 base64.urlsafe_b64encode(secret.encode()).decode().rstrip('=')))
        decoded = value
        for _ in range(3):
            decoded = unquote(decoded)
        if any(v in value or v in decoded for v in variants):
            # Do not mutate arbitrary business string data and claim it is complete.
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Platform response contained credential material.')
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = strict_json(value)
            except (ValueError, RecursionError):
                return value
            return json.dumps(sanitize(parsed, known_secrets, depth + 1), ensure_ascii=False, allow_nan=False)
        for candidate in re.findall(r'https?://[^\s<>"\']+', value):
            parts = urlsplit(candidate)
            if parts.username or parts.password or any(URL_KEYS.fullmatch(k) for k, _ in parse_qsl(parts.query)):
                raise GatewayError('RESPONSE_BLOCKED', 502, 'Credential-bearing URL requires a reviewed download adapter.')
        return value
    return value


class PaginationStore:
    def __init__(self, ttl=300, capacity=4096):
        self.ttl, self.capacity = ttl, capacity
        self.entries = {}

    @staticmethod
    def owner(principal):
        return (principal.workspace_id, principal.subject, principal.client_id, principal.session_id)

    def rewrite(self, payload: dict, request: PlatformRequest, principal, version: str, known_secrets: tuple) -> dict:
        result = copy.deepcopy(payload)
        paging = result.get('paging')
        if not isinstance(paging, dict):
            return result
        # Previous URLs are never consumed by current client pagination.
        paging.pop('previous', None)
        url = paging.pop('next', None)
        if not url:
            return result
        if request.platform != 'meta' or request.method != 'GET' or not isinstance(url, str):
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Unexpected pagination response.')
        parts = urlsplit(url)
        prefix = '/' + version + '/'
        if parts.scheme != 'https' or parts.netloc != 'graph.facebook.com' or not parts.path.startswith(prefix) or parts.fragment:
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Platform pagination target is not trusted.')
        params = {}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if SECRET_FIELD.fullmatch(key):
                continue
            if key in params:
                raise GatewayError('RESPONSE_BLOCKED', 502, 'Ambiguous pagination parameters.')
            params[key] = value
        # Pagination may not switch path/account or smuggle a token into another field.
        path = parts.path[len(prefix):]
        if path != request.path:
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Pagination path changed.')
        sanitize(params, known_secrets)
        try:
            nxt = PlatformRequest.model_validate({**request.model_dump(), 'query': params})
        except ValueError:
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Unsafe pagination parameters.') from None
        now = time.monotonic()
        self.entries = {key: value for key, value in self.entries.items() if value[0] > now}
        if len(self.entries) >= self.capacity:
            raise GatewayError('GATEWAY_BUSY', 503, 'Pagination state capacity reached.')
        reference = secrets.token_urlsafe(24)
        self.entries[reference] = (now + self.ttl, self.owner(principal), nxt)
        paging['next'] = 'motata-page:' + reference
        return result

    def resolve(self, reference, principal):
        row = self.entries.get(reference)
        if not row or row[0] <= time.monotonic() or row[1] != self.owner(principal):
            raise GatewayError('PAGINATION_EXPIRED', 404, 'Pagination reference is unavailable.')
        # A page can be read repeatedly, but every read is reauthorized with live grants.
        return row[2]
