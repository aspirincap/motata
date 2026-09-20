"""Asynchronous JWKS from one administrator-configured HTTPS URL.

No jku/x5u/jwk or issuer URL is learned from a token. A bounded cache, one refresh
lock and unknown-kid cooldown prevent request storms. Expired cache fails closed.
"""
from __future__ import annotations
import asyncio
import time
from urllib.parse import urlsplit
import httpx
import jwt
from .jwt_auth import JWTVerifier, ALGORITHMS, KEY_ID
from .errors import GatewayError
from .responses import strict_json


class RemoteJWTVerifier(JWTVerifier):
    def __init__(self, *, issuer, audience, jwks_url, algorithm='ES256', max_ttl=900,
                 leeway=60, cache_ttl=300, cooldown=5, http=None, clock=time.monotonic):
        if algorithm not in ALGORITHMS or not 0 < max_ttl <= 900 or not 0 <= leeway <= 60:
            raise ValueError('Invalid JWT policy')
        for value in (issuer, jwks_url):
            p = urlsplit(value)
            if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.query:
                raise ValueError('Issuer/JWKS must be fixed HTTPS URLs')
        if not audience or not 1 <= cache_ttl <= 300 or not 1 <= cooldown <= 60:
            raise ValueError('Invalid JWKS cache policy')
        self.issuer, self.audience, self.algorithm = issuer, audience, algorithm
        self.max_ttl, self.leeway = max_ttl, leeway
        self.jwks_url, self.cache_ttl, self.cooldown = jwks_url, cache_ttl, cooldown
        self._keys, self._expires, self._next_refresh = {}, 0, 0
        self._clock = clock
        self._lock = asyncio.Lock()
        self.http = http or httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        self.owns_http = http is None

    def _load(self):
        # Sync verify never makes network calls inside the ASGI event loop.
        if not self._keys or self._clock() >= self._expires:
            raise ValueError('JWKS cache is unavailable')

    async def verify_async(self, token):
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise ValueError('Invalid token length')
            h = jwt.get_unverified_header(token)
            if set(h) - {'typ', 'alg', 'kid'} or h.get('typ') != 'at+jwt' or h.get('alg') != self.algorithm:
                raise ValueError('Untrusted JOSE header')
            kid = h.get('kid')
            if not isinstance(kid, str) or not KEY_ID.fullmatch(kid):
                raise ValueError('Invalid key identifier')
            async with self._lock:
                now = self._clock()
                refresh = now >= self._expires or kid not in self._keys
                if refresh and now >= self._next_refresh:
                    self._next_refresh = now + self.cooldown
                    try:
                        async with asyncio.timeout(10):
                            async with self.http.stream('GET', self.jwks_url,
                                headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                                follow_redirects=False) as response:
                                if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                                    raise ValueError('JWKS fetch failed')
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    body.extend(chunk)
                                    if len(body) > 65536:
                                        raise ValueError('JWKS byte limit exceeded')
                        keys = self.parse_keys(strict_json(body))
                        self._keys, self._expires = keys, self._clock() + self.cache_ttl
                    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, jwt.PyJWTError):
                        # Do not extend old keys on a failed fetch. They are usable
                        # only until their ORIGINAL TTL, never indefinitely stale.
                        if now >= self._expires:
                            raise GatewayError('JWKS_UNAVAILABLE', 503, 'Signing-key verification is temporarily unavailable.') from None
                elif now >= self._expires:
                    raise GatewayError('JWKS_UNAVAILABLE', 503, 'Signing-key refresh is temporarily unavailable.')
            return self.verify(token)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise GatewayError('UNAUTHENTICATED', 401, 'Invalid gateway access token.') from None

    async def close(self):
        if self.owns_http:
            await self.http.aclose()
