"""Command-local leases. Secrets are excluded from representations and disk."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from motata_cli.common.errors import CliError


@dataclass
class TokenLease:
    provider: object = field(repr=False)
    token: str = field(repr=False)
    account_id: str | None = None
    agent_id: str | None = None
    expires_at: float | None = None
    issued_at: float = field(default_factory=time.monotonic)
    verified: bool = False

    @property
    def stale(self):
        return (not self.token or time.monotonic() - self.issued_at >= 60 or
                (self.expires_at is not None and self.expires_at <= time.time() + 30))

    def get_token(self):
        if self.provider.blocked:
            raise CliError(self.provider.blocked)
        if self.stale:
            refreshed = self.provider.acquire(self.account_id)
            self.token, self.expires_at, self.agent_id = refreshed.token, refreshed.expires_at, refreshed.agent_id
            self.issued_at, self.verified = refreshed.issued_at, False
        return self.token

    def invalidate(self):
        self.token = ""
        self.verified = False

    def check_scope(self, parameters):
        if parameters.get("advertiser_id") and str(parameters["advertiser_id"]) != self.account_id:
            raise CliError("Token lease advertiser mismatch.")

    def before_use(self, endpoint):
        from .usage import UsageStore
        if self.provider.blocked:
            raise CliError(self.provider.blocked)
        if self.provider.usage_store is None:
            self.provider.usage_store = UsageStore()
        return self.provider.usage_store.prepare(self.provider, self, endpoint)

    def after_use(self, event, *, sent, outcome):
        store = self.provider.usage_store
        try:
            if not sent:
                store.update(event["request_id"], "not_used", outcome)
                return
            store.update(event["request_id"], "sending", outcome)
            result = self.provider.request("POST", "/billing/token-usage", body=event)["data"]
            if not isinstance(result.get("allowed"), bool) or not isinstance(result.get("blocked"), bool):
                raise ValueError("Invalid usage result")
            store.update(event["request_id"], "acknowledged", outcome)
            if result["blocked"] or not result["allowed"]:
                self.provider.blocked = "Auth Center billing is blocked; subsequent platform calls were stopped."
        except Exception:
            # v0.3.9 does not deduplicate request_id: never automatically repost.
            self.provider.blocked = "Auth Center usage acknowledgement is unknown; reconcile the usage event before continuing."
            try:
                store.update(event["request_id"], "unknown", outcome)
            except Exception:
                pass
        if self.provider.blocked:
            print(f"Warning: {self.provider.blocked} Event: {event['request_id']}", file=sys.stderr)
