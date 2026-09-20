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
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = strict_json(value)
            except (ValueError, RecursionError):
                pass
            else:
                return json.dumps(sanitize(parsed, known_secrets, depth + 1), ensure_ascii=False, allow_nan=False)
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


def credential_values(value, depth=0) -> tuple[str, ...]:
    """Collect NEW secrets returned by upstream before removing their fields.

    A derived secret can also be echoed in a business string. Only scanning the
    credential used for the request would miss that second copy.
    """
    if depth > 32:
        raise GatewayError('RESPONSE_BLOCKED', 502, 'Response nesting exceeded safety limit.')
    values = []
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_FIELD.fullmatch(str(key)) and isinstance(item, str) and item:
                values.append(item)
            else:
                values.extend(credential_values(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            values.extend(credential_values(item, depth + 1))
    elif isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            parsed = strict_json(value)
        except (ValueError, RecursionError):
            return ()
        values.extend(credential_values(parsed, depth + 1))
    return tuple(set(values))


def write_evidence(request, endpoint, payload):
    """Return (confirmed, bindings) only for explicit reviewed success contracts.

    HTTP 2xx alone is NOT evidence. A Meta partial video phase is complete only
    for that phase, not the entire upload. No evidence -> uncertain receipt.
    """
    bindings = []
    typ = endpoint.object_type
    def numeric(value):
        return re.fullmatch(r'[0-9]{1,64}', str(value or '')) is not None
    if request.platform == 'meta':
        if endpoint.confirmation == 'meta_success':
            return payload.get('success') is True, bindings
        if endpoint.confirmation == 'report':
            ident = payload.get('report_run_id')
            if numeric(ident):
                return True, [('report', str(ident))]
            return False, bindings
        if endpoint.confirmation == 'upload':
            phase = (request.body or {}).get('upload_phase')
            if phase == 'start':
                ok = numeric(payload.get('upload_session_id')) and numeric(payload.get('video_id'))
                if ok:
                    return True, [('upload_session', str(payload['upload_session_id'])), ('video', str(payload['video_id']))]
                return False, []
            if phase == 'transfer':
                start, end = str(payload.get('start_offset', '')), str(payload.get('end_offset', ''))
                return start.isdigit() and end.isdigit() and int(start) <= int(end), []
            if phase == 'finish':
                return payload.get('success') is True, []
            if typ == 'image':
                images = payload.get('images')
                return bool(isinstance(images, dict) and images and
                            all(isinstance(x, dict) and isinstance(x.get('hash'), str) and x['hash']
                                for x in images.values())), []
        if numeric(payload.get('id')):
            return True, [(typ, str(payload['id']))] if typ else []
        return False, []
    if payload.get('code') not in (0, '0'):
        return False, []
    if endpoint.confirmation == 'tiktok_code':
        return True, []
    if endpoint.confirmation == 'tiktok_link':
        data=payload.get('data') or {}
        return isinstance(data,dict) and any(isinstance(data.get(k),str) and data[k] for k in ('shareable_link','preview_url','url','shareable_url')), []
    data = payload.get('data')
    candidates = data if isinstance(data, list) else [data]
    key = {'portfolio':'creative_portfolio_id'}.get(typ, (typ or '') + '_id')
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # Different platform upload/create responses have singular or plural IDs.
        identities = [item.get(key),item.get('smart_plus_'+key),item.get('avatar_video_id') if typ=='video' else None]
        for plural in (key + 's', 'smart_plus_' + key + 's'):
            if isinstance(item.get(plural), list):
                identities += item[plural]
        from .resource_evidence import identifier
        bindings += [(typ, str(x)) for x in identities if identifier(x)]
    return bool(bindings), bindings
