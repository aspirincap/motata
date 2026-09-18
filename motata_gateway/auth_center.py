"""Server-only Auth Center adapter. No account/channel fallback or client token export.

Compatible with motata-auth-center 96fda94 GET /openapi/tokens/{channel}:{agent_id}.
Account linkage is a TRUSTED ADMIN assertion, not discovered by that endpoint.
Remote sessions/grants, OAuth refresh and Gateway JWT issuance remain separate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .errors import GatewayError
from .responses import strict_json
from .state import CredentialLease

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
ACCOUNT = re.compile(r'^[0-9]{1,32}$')
CHANNELS = {'meta': {'meta', 'facebook'}, 'tiktok': {'tiktok'}}


def unavailable(code='AUTH_CENTER_UNAVAILABLE', message='Auth Center authorization is unavailable.'):
    return GatewayError(code, 503, message, 'not_sent')


def private_bytes(path: Path, limit: int) -> bytes:
    """Open once; validate the actual file descriptor (no symlink/read race)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or Path(path).is_symlink():
            raise ValueError('Expected protected regular file')
        if os.name != 'nt' and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            raise ValueError('Private file ownership/permissions invalid')
        if st.st_size > limit:
            raise ValueError('Private file exceeds limit')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Private file exceeds limit')
        return data


@dataclass(frozen=True)
class AuthCenterBinding:
    profile: str
    tenant_id: str
    account_id: str
    platform: str
    channel: str
    oauth_agent_id: str

    def __post_init__(self):
        if (not isinstance(self.profile, str) or not NAME.fullmatch(self.profile)
                or not isinstance(self.oauth_agent_id, str) or not NAME.fullmatch(self.oauth_agent_id)
                or not isinstance(self.account_id, str) or not ACCOUNT.fullmatch(self.account_id)
                or self.platform not in CHANNELS or self.channel not in CHANNELS[self.platform]
                or str(UUID(self.tenant_id)) != self.tenant_id):
            raise ValueError('Invalid explicit Auth Center binding')

    def require_scope(self, workspace: str, platform: str, account: str):
        if (workspace, platform, account) != (self.tenant_id, self.platform, self.account_id):
            raise GatewayError('FORBIDDEN', 403, 'Credential binding is outside the authorized account.')


@dataclass(frozen=True)
class AuthCenterProfile:
    name: str
    tenant_id: str
    origin: str
    api_key_file: Path
    cache_ttl: float = 15.0
    timeout: float = 10.0
    expiry_buffer: float = 30.0

    def __post_init__(self):
        url = urlsplit(self.origin)
        if (not NAME.fullmatch(self.name) or str(UUID(self.tenant_id)) != self.tenant_id
                or url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment
                or not url.netloc.isascii() or any(c.isspace() for c in self.origin)
                or '\\' in self.origin or '%' in url.netloc or url.port not in (None, 443)
                or not self.api_key_file.is_absolute()):
            raise ValueError('Auth Center requires a fixed HTTPS origin and protected absolute key path')
        for value, lower, upper in ((self.cache_ttl, 0, 60), (self.timeout, 0.1, 30), (self.expiry_buffer, 0, 120)):
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError('Invalid bounded Auth Center timeout/cache policy')

    def read_key(self) -> str:
        # API key is service-side only. Its prefix is a misconfiguration check;
        # Auth Center must still authenticate it and enforce its actual tenant.
        key = private_bytes(self.api_key_file, 4096).decode('utf-8').strip()
        if not key.startswith('ak_' + self.tenant_id + '_') or not re.fullmatch(r'[A-Za-z0-9_-]{50,4096}', key):
            raise ValueError('Auth Center key does not match the configured tenant')
        return key


def load_profiles(path: Path) -> list[AuthCenterProfile]:
    """Protected server configuration only; never load from a CLI request."""
    raw = strict_json(private_bytes(path, 65536))
    if not isinstance(raw, dict) or set(raw) != {'profiles'} or not isinstance(raw['profiles'], list):
        raise ValueError('Invalid Auth Center profile configuration')
    if not 1 <= len(raw['profiles']) <= 128:
        raise ValueError('Profile count exceeds limit')
    profiles = []
    for item in raw['profiles']:
        if not isinstance(item, dict):
            raise ValueError('Invalid profile')
        values = dict(item)
        values['api_key_file'] = Path(values['api_key_file'])
        profile = AuthCenterProfile(**values)
        profile.read_key()  # Fail startup for missing/unsafe/incorrect credentials.
        profiles.append(profile)
    if len({p.name for p in profiles}) != len(profiles):
        raise ValueError('Duplicate profile')
    return profiles


@dataclass
class _Flight:
    task: asyncio.Task | None = None
    invalidated: bool = False


@dataclass
class _Cached:
    until: float
    lease: CredentialLease | None = field(default=None, repr=False)
    error: tuple[str, int, str, str] | None = None


class LeaseCache:
    """Bounded positive/negative cache; shared fetch survives one waiter cancelling.

    Flight tasks have bounded IO deadlines. Invalidated flights cannot repopulate
    cache or return a lease. This is in-process, not distributed coordination.
    """
    def __init__(self, *, capacity=256, max_fetches=16, negative_ttl=1.0,
                 monotonic: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time):
        if not 1 <= capacity <= 4096 or not 1 <= max_fetches <= 256 or not 0 <= negative_ttl <= 5:
            raise ValueError('Invalid credential cache limits')
        self.capacity, self.max_fetches, self.negative_ttl = capacity, max_fetches, negative_ttl
        self.monotonic, self.wall_clock = monotonic, wall_clock
        self.entries: OrderedDict[tuple, _Cached] = OrderedDict()
        self.flights: dict[tuple, _Flight] = {}
        self.closed = False

    def _remember(self, key: tuple, entry: _Cached):
        self.entries[key] = entry
        self.entries.move_to_end(key)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)

    async def get(self, key: tuple, fetch: Callable[[], Awaitable[CredentialLease]], *, ttl: float, buffer: float):
        if self.closed:
            raise unavailable()
        now = self.monotonic()
        cached = self.entries.get(key)
        if cached and cached.until > now:
            self.entries.move_to_end(key)
            if cached.error:
                raise GatewayError(*cached.error) from None
            lease = cached.lease
            if lease and (lease.expires_at is None or lease.expires_at > self.wall_clock() + buffer):
                return lease
        self.entries.pop(key, None)
        flight = self.flights.get(key)
        if flight is None:
            if len(self.flights) >= self.max_fetches:
                raise unavailable('AUTH_CENTER_BUSY', 'Credential fetch capacity reached.')
            flight = _Flight()
            self.flights[key] = flight
            async def run():
                try:
                    lease = await fetch()
                    if flight.invalidated or self.closed:
                        raise unavailable('CREDENTIAL_CHANGED', 'Credential state changed; retry the read or inspect the write.')
                    remaining = math.inf if lease.expires_at is None else lease.expires_at - self.wall_clock() - buffer
                    if remaining <= 0:
                        raise unavailable('AUTH_REQUIRED', 'Platform authorization requires renewal.')
                    # Bound staleness from request START, not completion.
                    until = min(now + ttl, self.monotonic() + remaining)
                    if until > self.monotonic():
                        self._remember(key, _Cached(until, lease=lease))
                    return lease
                except GatewayError as exc:
                    if not flight.invalidated and not self.closed:
                        # Do not retain exception objects/tracebacks containing secrets.
                        self._remember(key, _Cached(self.monotonic() + self.negative_ttl,
                            error=(exc.code, exc.status, exc.message, exc.write_outcome)))
                    raise
                finally:
                    if self.flights.get(key) is flight:
                        self.flights.pop(key, None)
            flight.task = asyncio.create_task(run())
            # Observe errors even when all waiters cancel; never log the exception.
            flight.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(flight.task)

    def invalidate(self, key: tuple):
        self.entries.pop(key, None)
        if key in self.flights:
            self.flights[key].invalidated = True

    async def close(self):
        self.closed = True
        tasks = [flight.task for flight in self.flights.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.entries.clear()
        self.flights.clear()


class AuthCenterProvider:
    """Fetch exact OAuth identities, never account-token/channel fallback endpoints."""
    def __init__(self, profiles: list[AuthCenterProfile], *, http: httpx.AsyncClient | None = None,
                 cache: LeaseCache | None = None):
        if len({p.name for p in profiles}) != len(profiles):
            raise ValueError('Duplicate profile')
        self.profiles = {p.name: p for p in profiles}
        self.cache = cache or LeaseCache()
        self.http = http or httpx.AsyncClient(trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(10, connect=3, pool=3),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
        self.owns_http = http is None
        self.fetch_count = 0

    async def resolve(self, binding: AuthCenterBinding, reference: str) -> CredentialLease:
        profile = self.profiles.get(binding.profile)
        if profile is None or profile.tenant_id != binding.tenant_id:
            raise unavailable('AUTH_CENTER_NOT_CONFIGURED', 'The configured credential provider is unavailable.')
        try:
            key = profile.read_key()  # rotation / file removal / permissions checked on every call
        except (OSError, ValueError, UnicodeError):
            raise unavailable('AUTH_CENTER_NOT_CONFIGURED', 'The configured credential provider is unavailable.') from None
        # Cached lease is per OAuth identity and service key generation, not per
        # account. Account/grant checks happen independently before EVERY use.
        cache_key = (profile.name, profile.origin, profile.tenant_id, binding.platform, binding.channel,
                     binding.oauth_agent_id, hashlib.sha256(key.encode()).digest())
        lease = await self.cache.get(cache_key, lambda: self._fetch(profile, binding, key, cache_key),
                                     ttl=profile.cache_ttl, buffer=profile.expiry_buffer)
        return replace(lease, reference=reference)

    async def _fetch(self, profile: AuthCenterProfile, binding: AuthCenterBinding,
                     key: str, cache_key: tuple) -> CredentialLease:
        url = profile.origin.rstrip('/') + '/api/v1/openapi/tokens/' + binding.channel + ':' + binding.oauth_agent_id
        # Request constructed explicitly: no caller JWT, inherited client headers,
        # cookies, proxy variables, or token-bearing query parameters.
        request = httpx.Request('GET', url, headers={'X-API-Key': key, 'Accept': 'application/json',
                                                   'Accept-Encoding': 'identity'},
                               extensions={'timeout': {'connect': 3, 'read': profile.timeout,
                                                        'write': 3, 'pool': 3}})
        self.fetch_count += 1
        try:
            async with asyncio.timeout(profile.timeout):
                response = await self.http.send(request, stream=True, follow_redirects=False)
                try:
                    if response.status_code != 200:
                        codes = {401: 'AUTH_CENTER_DENIED', 403: 'AUTH_CENTER_DENIED',
                                 402: 'AUTH_CENTER_ACCESS_BLOCKED', 404: 'AUTH_REQUIRED', 429: 'AUTH_CENTER_BUSY'}
                        raise unavailable(codes.get(response.status_code, 'AUTH_CENTER_UNAVAILABLE'))
                    if (response.headers.get('content-type', '').split(';')[0].lower() != 'application/json'
                            or response.headers.get('content-encoding', 'identity').lower() != 'identity'):
                        raise ValueError('Invalid response format')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 131072:
                            raise ValueError('Credential response too large')
                        data.extend(chunk)
                    payload = strict_json(data)
                finally:
                    await response.aclose()
            if not isinstance(payload, dict) or payload.get('success') is not True:
                raise ValueError('Unsuccessful response')
            value = payload.get('data')
            if not isinstance(value, dict):
                raise ValueError('Invalid data')
            if (value.get('channel'), value.get('agent_id')) != (binding.channel, binding.oauth_agent_id):
                raise ValueError('Credential identity mismatch')
            if value.get('auth_status') != 'active':
                raise unavailable('AUTH_REQUIRED', 'Platform authorization requires renewal.')
            token = value.get('access_token')
            if not isinstance(token, str) or not 1 <= len(token) <= 32768 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError('Invalid credential value')
            if 'expires_at' not in value:
                raise ValueError('Missing expiration metadata')
            expires = value['expires_at']
            if expires is not None:
                if not isinstance(expires, str):
                    raise ValueError('Invalid expiry')
                dt = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    raise ValueError('Expiry must be timezone aware')
                expires = dt.timestamp()
            version = value.get('token_version')  # current OpenAPI does not supply this
            if version is not None and (type(version) is not int or version < 1):
                raise ValueError('Invalid token version')
            # Nothing from the original OAuth payload is retained in public results.
            return CredentialLease('', binding.platform, token, expires_at=expires,
                                   token_version=version, sensitive_values=(key,), cache_key=cache_key)
        except GatewayError as exc:
            if exc.code.startswith('AUTH_CENTER_') or exc.code == 'AUTH_REQUIRED':
                raise
            # A malformed source JSON is a provider failure, not a client 400.
            raise unavailable() from None
        except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, TimeoutError, RecursionError, UnicodeError):
            raise unavailable() from None

    def invalidate(self, lease: CredentialLease):
        if lease.cache_key is not None:
            self.cache.invalidate(lease.cache_key)

    async def close(self):
        await self.cache.close()
        if self.owns_http:
            await self.http.aclose()
