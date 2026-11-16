"""Auth Center v0.3.9 API Key credentials; no JWT or advertising gateway."""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests

from motata_cli.common.errors import CliError
from motata_cli.common.http import ExplicitAuth, bounded_json
from motata_cli.common.security import network_error

DEFAULT_BASE_URL = "https://motata.one"
PREFIX = "/api/v1/openapi"


def add_auth_arguments(parser):
    parser.add_argument("--token-source", choices=("direct", "auth-center"))
    parser.add_argument("--auth-center-base-url")
    parser.add_argument("--auth-center-header", choices=("x-api-key", "bearer"))
    parser.add_argument("--auth-center-agent-id", help="Optional explicit TikTok OAuth identity")


def options_from_args(args):
    return {"token_source": getattr(args, "token_source", None),
            "base_url": getattr(args, "auth_center_base_url", None),
            "header_mode": getattr(args, "auth_center_header", None),
            "agent_id": getattr(args, "auth_center_agent_id", None)}


class AuthCenterError(CliError):
    def __init__(self, message, *, http_status=None, request_id=None):
        super().__init__(message)
        self.http_status, self.request_id = http_status, request_id
        self.category = "billing" if http_status == 402 else "auth" if http_status in (401, 403) else "other"


class AuthCenterProvider:
    def __init__(self, api_key, *, base_url=DEFAULT_BASE_URL, header_mode="x-api-key", session=None,
                 agent_id=None, usage_store=None):
        parts = urlsplit(base_url)
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ("", "/") or (parts.scheme != "https" and not
                (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")))):
            raise CliError("Auth Center requires an HTTPS origin (loopback HTTP is allowed for development).")
        if not isinstance(api_key, str) or not api_key.strip():
            raise CliError("Missing AUTH_CENTER_API_KEY for the selected auth-center token source.")
        if header_mode not in ("x-api-key", "bearer"):
            raise CliError("Auth Center header must be x-api-key or bearer.")
        self._api_key = api_key
        self.key_id = hashlib.sha256(api_key.encode()).hexdigest()[:24]
        self.base_url, self.header_mode = base_url.rstrip("/"), header_mode
        self.session = session if session is not None else requests.Session()
        self.owns_session = session is None
        self.agent_id = agent_id
        self.usage_store = usage_store
        self.blocked = None

    def close(self):
        if self.owns_session:
            self.session.close()

    def __del__(self):
        if hasattr(self, "owns_session"):
            self.close()

    def request(self, method, path, *, params=None, body=None):
        if not path.startswith("/") or ".." in path or "://" in path:
            raise CliError("Invalid Auth Center path.")
        headers = {"Accept": "application/json"}
        headers.update({"Authorization": f"Bearer {self._api_key}"} if self.header_mode == "bearer"
                       else {"X-API-Key": self._api_key})
        response = None
        try:
            response = self.session.request(method, self.base_url + PREFIX + path, headers=headers,
                                            params=params, json=body, timeout=(10, 30),
                                            auth=ExplicitAuth(), allow_redirects=False, stream=True)
            status = response.status_code
            if not 200 <= status < 300:
                raise AuthCenterError(f"Auth Center HTTP {status}: credential, account or billing request rejected.",
                                      http_status=status, request_id=response.headers.get("X-Request-ID"))
            payload = bounded_json(response)
            if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), dict):
                raise AuthCenterError("Auth Center returned an invalid v0.3.9 response.")
            return payload
        except (requests.RequestException, ValueError) as exc:
            raise AuthCenterError(network_error("Auth Center", exc)) from None
        finally:
            if response is not None:
                response.close()

    def account(self, account_id):
        data = self.request("GET", "/ad-accounts/" + quote(str(account_id), safe=""))["data"]
        if str(data.get("account_id")) != str(account_id) or data.get("channel") != "tiktok":
            raise AuthCenterError("Auth Center account/channel mismatch.")
        return data

    def acquire(self, account_id=None):
        from .models import TokenLease
        if self.blocked:
            raise AuthCenterError(self.blocked)
        if account_id:
            self.account(account_id)
        if self.agent_id:
            agent = str(self.agent_id).removeprefix("tiktok:")
            data = self.request("GET", "/tokens/tiktok:" + quote(agent, safe="") + "/access-token")["data"]
            token_data = data
        else:
            path = "/ad-accounts/" + quote(str(account_id), safe="") + "/token" if account_id else "/agents/channel/tiktok/valid-token"
            data = self.request("GET", path)["data"]
            if data.get("channel") != "tiktok" or (account_id and str(data.get("account_id")) != str(account_id)):
                raise AuthCenterError("Auth Center token account/channel mismatch.")
            token_data = data.get("token")
            if not isinstance(token_data, dict):
                raise AuthCenterError("Auth Center has no valid TikTok token.")
            agent = (data.get("agent") or {}).get("agent_id") or token_data.get("agent_id")
        token = token_data.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthCenterError("Auth Center did not return a valid access token.")
        expires = token_data.get("expires_at")
        if expires is not None:
            try:
                expires = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    raise ValueError("Timezone required")
                expires = expires.timestamp()
            except (TypeError, ValueError):
                raise AuthCenterError("Invalid Auth Center token expiry.") from None
            if expires <= time.time() + 30:
                raise AuthCenterError("Auth Center token is expired or too close to expiry; refresh it in Auth Center.")
        return TokenLease(provider=self, token=token, account_id=str(account_id) if account_id else None,
                          agent_id=str(agent) if agent else None, expires_at=expires)


class AuthCenterCredential:
    """Resolve independently per advertiser, including multi-account reports."""
    def __init__(self, provider, account_id=None):
        self.provider, self.account_id = provider, account_id
        self.leases = {}

    def for_request(self, parameters):
        account = parameters.get("advertiser_id") or self.account_id
        ids = parameters.get("advertiser_ids") or []
        if ids:
            if not isinstance(ids, list):
                raise CliError("advertiser_ids must be a list.")
            account = str(ids[0])
            for other in ids[1:]:
                self.provider.account(str(other))
        account = str(account) if account else None
        lease = self.leases.get(account)
        if lease is None or lease.stale:
            lease = self.provider.acquire(account)
            self.leases[account] = lease
        return lease

    def close(self):
        self.leases.clear()
        self.provider.close()


def resolve_tiktok_credential(*, account_id=None, access_token=None, token_source=None,
                              base_url=None, api_key=None, header_mode=None, agent_id=None):
    if isinstance(access_token, AuthCenterCredential):
        return access_token, "auth-center"
    if access_token:
        if token_source == "auth-center":
            raise CliError("--access-token conflicts with --token-source auth-center.")
        return access_token, "direct"
    from motata_cli.init_profile import load_init_profile
    profile = load_init_profile("tiktok")
    key = api_key or os.getenv("AUTH_CENTER_API_KEY")
    source = token_source or profile.get("token_source") or ("auth-center" if key else "direct")
    if source == "auth-center":
        provider = AuthCenterProvider(key, base_url=base_url or os.getenv("AUTH_CENTER_BASE_URL") or
                                      profile.get("auth_center_base_url") or DEFAULT_BASE_URL,
                                      header_mode=header_mode or profile.get("auth_center_header") or "x-api-key",
                                      agent_id=agent_id or profile.get("auth_center_agent_id"))
        return AuthCenterCredential(provider, account_id), source
    token = os.getenv("TIKTOK_ACCESS_TOKEN") or os.getenv("MOTATA_TIKTOK_ACCESS_TOKEN")
    if not token:
        raise CliError("Missing TikTok access token. Configure AUTH_CENTER_API_KEY or TIKTOK_ACCESS_TOKEN, or pass --access-token.")
    return token, "environment"
