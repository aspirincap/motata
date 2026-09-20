"""Async credential routing without changing business CLI or JWT verification."""
from __future__ import annotations

import json
from .auth_center import AuthCenterBinding, AuthCenterProvider
from .errors import GatewayError
from .state import CredentialLease, GatewayState


class CredentialResolver:
    def __init__(self, state: GatewayState, auth_center: AuthCenterProvider | None = None):
        self.state, self.auth_center = state, auth_center

    async def resolve(self, reference: str, platform: str, workspace: str, account: str) -> CredentialLease:
        descriptor = self.state.describe(reference, platform)
        if descriptor.provider == 'direct':
            return self.state.resolve(reference, platform)
        if descriptor.provider != 'auth_center' or self.auth_center is None:
            raise GatewayError('AUTH_CENTER_NOT_CONFIGURED', 503, 'Credential provider is not configured.')
        try:
            binding = AuthCenterBinding(**json.loads(descriptor.binding_json))
        except (ValueError, TypeError, KeyError):
            raise GatewayError('CREDENTIAL_UNAVAILABLE', 503, 'Credential binding is invalid.') from None
        binding.require_scope(workspace, platform, account)
        lease = await self.auth_center.resolve(binding, reference)
        # A trusted admin may change/disable the binding during the network await.
        if self.state.describe(reference, platform) != descriptor:
            raise GatewayError('CREDENTIAL_CHANGED', 503, 'Credential binding changed while waiting.')
        self.state.resolve_count += 1
        return lease

    def invalidate(self, lease: CredentialLease):
        if self.auth_center is not None:
            self.auth_center.invalidate(lease)

    async def close(self):
        if self.auth_center is not None:
            await self.auth_center.close()
