from __future__ import annotations

from dataclasses import dataclass, field
from motata_cli.transport.gateway import GatewayAuthRef, auth_ref, gateway_enabled
from motata_cli.common.errors import CliError
from motata_cli.common.utils import normalize_account_id


@dataclass
class AuthContext:
    account_id: str
    media_code: str
    # Compatibility slot: gateway mode holds a typed non-secret reference, never a token.
    access_token: str | GatewayAuthRef = field(repr=False)
    expires_at: int | None
    source: str



def resolve_auth(
    *,
    account_id: str,
    media_code: str = "facebook",
    access_token: str | None = None,
    token_source: str | None = None,
    **auth_options,
) -> AuthContext:
    if media_code == "tiktok":
        if gateway_enabled():
            raise CliError("TikTok uses Direct HTTP; gateway mode is unsupported and will not fall back.", exit_code=2)
        from motata_cli.auth_center.provider import resolve_tiktok_credential
        credential, source = resolve_tiktok_credential(account_id=account_id or None, access_token=access_token,
                                                       token_source=token_source, **auth_options)
        return AuthContext(account_id=str(account_id or ""), media_code=media_code,
                           access_token=credential, expires_at=None, source=source)
    if gateway_enabled():
        if access_token is not None and not isinstance(access_token, GatewayAuthRef):
            raise CliError("Direct access tokens are disabled in gateway mode.", exit_code=2)
        reference = access_token or auth_ref(media_code, account_id)
        if reference.platform != ("meta" if media_code in ("meta", "facebook") else media_code):
            raise CliError("Gateway platform authorization mismatch.", exit_code=2)
        return AuthContext(account_id=reference.account_id or "", media_code=media_code,
                           access_token=reference, expires_at=None, source="gateway")
    if access_token:
        return AuthContext(
            account_id=normalize_account_id(account_id) if account_id else "",
            media_code=media_code,
            access_token=access_token,
            expires_at=None,
            source="direct",
        )
    raise CliError(
        "Missing access token. Fetch one with the motata token skill "
        "and pass it via --access-token."
    )
