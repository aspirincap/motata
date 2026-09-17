"""Asymmetric JWT access-token verification against administrator-managed JWKS.

No token-controlled URLs, private signing keys, HMAC, or decoded-only identity.
Source: RFC 8725; RFC 9068; PyJWT API documentation.
"""
from __future__ import annotations
import json
import os
import stat
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import jwt
from .errors import GatewayError

ALGORITHMS = {'ES256': ('EC', 'P-256'), 'RS256': ('RSA', None), 'EdDSA': ('OKP', 'Ed25519')}
PRIVATE = {'d', 'p', 'q', 'dp', 'dq', 'qi', 'oth', 'k'}
KEY_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')

@dataclass(frozen=True)
class Principal:
    subject: str
    client_id: str
    workspace_id: str
    session_id: str
    token_id: str
    key_id: str
    scopes: frozenset[str]
    expires_at: int


class JWTVerifier:
    def __init__(self, *, issuer: str, audience: str, jwks_path: Path,
                 algorithm: str = 'ES256', max_ttl: int = 900, leeway: int = 60):
        if algorithm not in ALGORITHMS or not 0 < max_ttl <= 900 or not 0 <= leeway <= 60:
            raise ValueError('Invalid JWT verification policy')
        if not issuer.startswith('https://') or not audience:
            raise ValueError('A configured HTTPS issuer and audience are required')
        self.issuer, self.audience, self.path = issuer, audience, jwks_path
        self.algorithm, self.max_ttl, self.leeway = algorithm, max_ttl, leeway
        self._stamp = None
        self._keys: dict[str, Any] = {}
        self._load()

    def _load(self):
        # Bounded trusted local JWKS is deliberately the v1 key-distribution boundary.
        # Re-read on replacement; no network key discovery controlled by JWT headers.
        st = self.path.lstat()
        if not stat.S_ISREG(st.st_mode) or (os.name != "nt" and st.st_mode & 0o022):
            raise ValueError("JWKS must be a regular, non-group/world-writable trusted file")
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        if stamp == self._stamp:
            return
        if st.st_size > 65536:
            raise ValueError('JWKS too large')
        data = json.loads(self.path.read_text())
        items = data.get('keys') if isinstance(data, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ValueError('Invalid JWKS')
        parsed = {}
        for item in items:
            if not isinstance(item, dict) or PRIVATE.intersection(item):
                raise ValueError('Only public asymmetric keys may be installed')
            kid = item.get('kid', '')
            kty, curve = ALGORITHMS[self.algorithm]
            if not isinstance(kid, str) or not KEY_ID.fullmatch(kid) or kid in parsed:
                raise ValueError('Invalid or duplicate kid')
            if item.get('kty') != kty or item.get('alg') != self.algorithm:
                raise ValueError('Key algorithm does not match configured policy')
            if curve and item.get('crv') != curve:
                raise ValueError('Unexpected curve')
            if item.get('use', 'sig') != 'sig' or item.get('key_ops', ['verify']) != ['verify']:
                raise ValueError('Key is not restricted to signature verification')
            key = jwt.PyJWK.from_dict(item, algorithm=self.algorithm).key
            if self.algorithm == 'RS256' and key.key_size < 2048:
                raise ValueError('RSA key must be at least 2048 bits')
            parsed[kid] = key
        self._keys, self._stamp = parsed, stamp

    def verify(self, token: str) -> Principal:
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise ValueError('Invalid token size')
            header = jwt.get_unverified_header(token)
            # Explicitly reject unknown key-discovery/critical/header extensions.
            if set(header) - {'typ', 'alg', 'kid'}:
                raise ValueError('Unsupported JOSE header')
            if header.get('typ') != 'at+jwt' or header.get('alg') != self.algorithm:
                raise ValueError('Wrong token type or algorithm')
            kid = header.get('kid')
            if not isinstance(kid, str) or not KEY_ID.fullmatch(kid):
                raise ValueError('Invalid kid')
            self._load()
            key = self._keys.get(kid)
            if key is None:
                raise ValueError('Unknown key')
            claims = jwt.decode(token, key, algorithms=[self.algorithm], issuer=self.issuer,
                                audience=self.audience, leeway=self.leeway,
                                options={'require': ['iss', 'aud', 'sub', 'exp', 'iat', 'jti',
                                                     'client_id', 'sid', 'workspace_id', 'scope'],
                                         'strict_aud': True})
            for name in ('exp', 'iat', 'nbf'):
                if name in claims and type(claims[name]) is not int:
                    raise ValueError('Time claims must be integer NumericDates')
            if not 0 < claims['exp'] - claims['iat'] <= self.max_ttl:
                raise ValueError('Invalid access-token lifetime')
            if 'nbf' in claims and claims['nbf'] >= claims['exp']:
                raise ValueError('Invalid not-before')
            for name in ('sub', 'jti', 'client_id', 'sid', 'workspace_id', 'scope'):
                if not isinstance(claims[name], str) or not 1 <= len(claims[name]) <= 1024:
                    raise ValueError('Invalid identity/scope claim')
            scopes = claims['scope'].split(' ')
            if any(not re.fullmatch(r'[a-z][a-z0-9_:-]{0,63}', s) for s in scopes):
                raise ValueError('Invalid scope format')
            return Principal(claims['sub'], claims['client_id'], claims['workspace_id'],
                             claims['sid'], claims['jti'], kid, frozenset(scopes), claims['exp'])
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError, RecursionError):
            # Never echo the JWT, header, claims, PEM/JWK, or exception details.
            raise GatewayError('UNAUTHENTICATED', 401, 'Invalid gateway access token.') from None
