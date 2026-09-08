from __future__ import annotations

from dataclasses import dataclass, field
from motata_cli.common.errors import CliError
from motata_cli.common.utils import normalize_account_id


@dataclass
class AuthContext:
    account_id: str
    media_code: str
    access_token: str = field(repr=False)
    expires_at: int | None
    source: str



def resolve_auth(
    *,
    account_id: str,
    media_code: str = "facebook",
    access_token: str | None = None,
) -> AuthContext:
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
