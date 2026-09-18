"""Trusted request pipeline: JWT -> scope/grants/ownership -> credentials -> upstream."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import time
from typing import Any
import httpx
from .errors import GatewayError
from .jwt_auth import JWTVerifier, Principal
from .limits import Limits, Scheduler
from .policy import select, validate_scope, check_objects
from .protocol import PlatformRequest
from .responses import PaginationStore, sanitize, strict_json
from .state import GatewayState
from .credentials import CredentialResolver


class GatewayService:
    def __init__(self, *, verifier: JWTVerifier, state: GatewayState,
                 meta_version: str, limits: Limits | None = None,
                 http: httpx.AsyncClient | None = None,
                 credentials: CredentialResolver | None = None):
        if not re.fullmatch(r'v[0-9]{1,2}\.0', meta_version):
            raise ValueError('Explicit reviewed Meta API version required')
        self.verifier, self.state, self.meta_version = verifier, state, meta_version
        self.credentials = credentials or CredentialResolver(state)
        self.limits = limits or Limits()
        self.scheduler = Scheduler(self.limits)
        self.pages = PaginationStore()
        self.http = http or httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(120, connect=10, pool=10),
            limits=httpx.Limits(max_connections=self.limits.upstream, max_keepalive_connections=self.limits.upstream))
        self.owns_http = http is None

    async def close(self):
        await self.credentials.close()
        if self.owns_http:
            await self.http.aclose()

    def authenticate(self, authorization: str) -> Principal:
        if not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
            raise GatewayError('UNAUTHENTICATED', 401, 'Gateway bearer authentication is required.')
        principal = self.verifier.verify(authorization[7:])
        self.state.check_principal(principal)
        return principal

    async def dispatch(self, request: PlatformRequest, principal: Principal) -> dict:
        endpoint = select(request)
        validate_scope(principal, endpoint)
        reference = self.state.authorize(principal, request.platform, request.account_id, request.credential_ref)
        check_objects(request, self.state)
        if endpoint.write and not request.idempotency_key:
            raise GatewayError('IDEMPOTENCY_REQUIRED', 400, 'A stable idempotency key is required for writes.')
        key = (request.platform, request.account_id)
        async with self.scheduler.execution(key):
            # Re-check revocation and grants after any queue wait, before resolving a token.
            if principal.expires_at <= time.time():
                raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while queued.')
            reference = self.state.authorize(principal, request.platform, request.account_id, reference)
            check_objects(request, self.state)
            owner = self.state.owner(principal, request.platform, request.account_id)
            fingerprint = hashlib.sha256(json.dumps(
                {k: v for k, v in request.model_dump().items() if k not in ('request_id', 'idempotency_key')},
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if endpoint.write:
                previous = self.state.lookup_write(owner, request.idempotency_key, fingerprint)
                if previous is not None:
                    return {**previous, 'request_id': request.request_id, 'replayed': True}
            lease = await self.credentials.resolve(reference, request.platform, principal.workspace_id, request.account_id)
            # External fetch may have waited: recheck identity, grants and ownership
            # immediately before a write receipt or any requested platform operation.
            if principal.expires_at <= time.time():
                raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while resolving credentials.')
            self.state.authorize(principal, request.platform, request.account_id, reference)
            check_objects(request, self.state)
            if endpoint.write:
                previous = self.state.begin_write(owner, request.idempotency_key, fingerprint)
                if previous is not None:
                    return {**previous, 'request_id': request.request_id, 'replayed': True}
            secrets = (lease.value, *lease.sensitive_values)
            try:
                status, data = await self.upstream(request, lease.value)
                error = data.get('error')
                if status == 401 or (request.platform == 'meta' and isinstance(error, dict) and error.get('code') == 190):
                    self.credentials.invalidate(lease)  # NEXT request refetches; never replay this write.
                # No upstream status is interpreted as safe-to-retry mutation by default.
                data = self.pages.rewrite(data, request, principal, self.meta_version, secrets)
                safe = sanitize(data, secrets)
                result = {'protocol': 'motata-gateway/v1', 'request_id': request.request_id,
                          'ok': True, 'status': status, 'data': safe,
                          'write_outcome': 'not_applicable' if not endpoint.write else 'unknown'}
                upstream_error = status >= 400 or 'error' in safe or (
                    request.platform == 'tiktok' and safe.get('code') not in (None, 0, '0'))
                if endpoint.write:
                    if upstream_error and 400 <= status < 500 and (safe.get('error') or {}).get('code') in (100, 190):
                        result['write_outcome'] = 'confirmed_failed'
                    elif upstream_error:
                        raise GatewayError('WRITE_NEEDS_REVIEW', 502, 'Platform failure did not establish a safe write outcome.', 'unknown')
                    elif request.platform == 'meta' and re.fullmatch(r'[0-9]+', str(safe.get('id', ''))):
                        self.state.bind_object('meta', str(safe['id']), request.account_id, endpoint.object_type)
                        result['write_outcome'] = 'confirmed_succeeded'
                    else:
                        raise GatewayError('WRITE_NEEDS_REVIEW', 502, 'Write returned no authoritative object ID.', 'unknown')
                    self.state.finish_write(owner, request.idempotency_key, result)
                elif not upstream_error:
                    # Collection membership provides account-bound ownership evidence.
                    rows = safe.get('data', [])
                    if isinstance(rows, dict):
                        rows = rows.get('list', [])
                    if endpoint.object_type and isinstance(rows, list):
                        id_field = 'id' if request.platform == 'meta' else endpoint.object_type + '_id'
                        for item in rows:
                            if isinstance(item, dict) and re.fullmatch(r'[0-9]+', str(item.get(id_field, ''))):
                                self.state.bind_object(request.platform, str(item[id_field]), request.account_id, endpoint.object_type)
                return result
            except GatewayError as error:
                if endpoint.write:
                    # A pending durable record remains quarantined until human reconciliation.
                    raise GatewayError(error.code, error.status, error.message, 'unknown') from None
                raise

    async def upstream(self, request: PlatformRequest, token: str) -> tuple[int, dict]:
        if request.platform == 'meta':
            url = f'https://graph.facebook.com/{self.meta_version}/{request.path}'
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        else:
            url = f'https://business-api.tiktok.com/open_api/v1.3/{request.path}'
            headers = {'Access-Token': token, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        query = {}
        for key, value in request.query.items():
            query[key] = json.dumps(value, separators=(',', ':')) if isinstance(value, (dict, list)) else value
        kwargs: dict[str, Any] = {'params': query, 'headers': headers}
        if request.body is not None:
            # Meta form params that contain objects retain the existing JSON-string convention.
            if request.body_encoding == 'form':
                kwargs['data'] = {k: json.dumps(v, separators=(',', ':')) if isinstance(v, (dict, list)) else v
                                  for k, v in request.body.items()}
            else:
                kwargs['json'] = request.body
        try:
            async with asyncio.timeout(180):
                async with self.http.stream(request.method, url, **kwargs) as response:
                    if 300 <= response.status_code < 400:
                        raise GatewayError('UPSTREAM_REDIRECT_BLOCKED', 502, 'Upstream redirects require a reviewed adapter.')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise GatewayError('RESPONSE_ENCODING_BLOCKED', 502, 'Compressed responses require a bounded decoder.')
                    # No credentials are sent to an origin chosen by the caller or a redirect.
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.limits.response_bytes:
                            raise GatewayError('RESPONSE_TOO_LARGE', 502, 'Platform response exceeded the byte limit.')
                    payload = strict_json(data)
                    if not isinstance(payload, dict):
                        raise ValueError('Expected JSON object')
                    return response.status_code, payload
        except GatewayError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError, UnicodeError):
            raise GatewayError('UPSTREAM_UNAVAILABLE', 502, 'Platform request failed; inspect operation state before retrying.',
                               'not_applicable' if request.method == 'GET' else 'unknown') from None