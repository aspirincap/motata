"""Short-lived Page credentials never leave the server; opaque refs are session-bound."""
from __future__ import annotations
from dataclasses import dataclass, field
import secrets
import time
from .errors import GatewayError

@dataclass
class PageLease:
    owner: tuple
    account: str
    parent_ref: str
    parent_revision: int
    parent_digest: str
    page: str
    expires: float
    value: str = field(repr=False)

class PageCredentialStore:
    def __init__(self, ttl=300, capacity=4096):
        self.ttl, self.capacity = ttl, capacity
        self.entries: dict[str, PageLease] = {}

    @staticmethod
    def owner(p):
        return (p.workspace_id, p.subject, p.client_id, p.session_id)

    def put(self, *, principal, account, parent_ref, parent_revision, parent_digest, page, token, expires_at=None):
        now = time.time()
        self.entries = {k:v for k,v in self.entries.items() if v.expires > now}
        if len(self.entries) >= self.capacity:
            raise GatewayError('GATEWAY_BUSY', 503, 'Page credential reference capacity reached.')
        expiry = min(now+self.ttl, expires_at or now+self.ttl)
        if expiry <= now or not isinstance(token, str) or not token or len(token)>65536:
            raise GatewayError('PAGE_AUTH_REQUIRED', 403, 'Page authorization is unavailable.')
        key = secrets.token_urlsafe(24)
        self.entries[key] = PageLease(self.owner(principal), account, parent_ref, parent_revision, parent_digest, page, expiry, token)
        return key

    def resolve(self, key, request, principal, state):
        row = self.entries.get(key)
        if not row or row.owner != self.owner(principal) or row.account != request.account_id or row.expires <= time.time():
            raise GatewayError('PAGE_AUTH_REQUIRED', 403, 'Page credential reference is expired or unavailable.')
        current = state.authorize(principal, 'meta', request.account_id, request.credential_ref)
        if current != row.parent_ref or state.describe(current, 'meta').revision != row.parent_revision:
            self.entries.pop(key, None)
            raise GatewayError('PAGE_AUTH_REQUIRED', 403, 'Parent authorization changed.')
        root = request.path.split('/')[0]
        # The Page itself or its compound post IDs, never another Graph object.
        if root != row.page and not (root.startswith(row.page+'_') and root[len(row.page)+1:].isdigit()):
            raise GatewayError('FORBIDDEN', 403, 'Page credential cannot be applied to this object.')
        if request.method != 'GET':
            raise GatewayError('FORBIDDEN', 403, 'Page references currently serve reviewed read workflows only.')
        state.require_object('meta', row.page, request.account_id, 'page')
        return row

    def close(self):
        self.entries.clear()
