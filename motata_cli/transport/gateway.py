"""HTTPS gateway transport. Carries only a short-lived gateway JWT, never platform tokens."""
from __future__ import annotations
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import requests
from motata_cli.common.errors import CliError

PLATFORM_SECRET_ENV = ('META_ACCESS_TOKEN', 'MOTATA_META_ACCESS_TOKEN', 'TIKTOK_ACCESS_TOKEN',
                       'MOTATA_TIKTOK_ACCESS_TOKEN', 'AUTH_CENTER_API_KEY')


def gateway_enabled() -> bool:
    value = os.getenv('MOTATA_AUTH_MODE', 'direct')
    if value not in ('direct', 'gateway'):
        raise CliError('MOTATA_AUTH_MODE must be direct or gateway.')
    return value == 'gateway'


def check_agent_environment():
    if any(os.environ.get(name) for name in PLATFORM_SECRET_ENV):
        raise CliError('Remove platform/Auth Center credential variables before starting gateway-mode CLI.')


@dataclass(frozen=True)
class GatewayAuthRef:
    platform: str
    account_id: str | None = None
    credential_ref: str | None = None


def auth_ref(platform: str, account_id: str | None = None) -> GatewayAuthRef:
    check_agent_environment()
    platform = 'meta' if platform in ('meta', 'facebook') else platform
    ref = os.getenv('MOTATA_GATEWAY_' + platform.upper() + '_CREDENTIAL_REF')
    account = account_id or os.getenv('MOTATA_GATEWAY_' + platform.upper() + '_ACCOUNT_ID')
    return GatewayAuthRef(platform, str(account).removeprefix('act_') if account else None, ref)


class RemoteGatewayTransport:
    def __init__(self, endpoint: str, *, token_file: str | None = None,
                 allow_loopback_http: bool = False, session=None):
        parts = urlsplit(endpoint)
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise CliError('Gateway endpoint must be a trusted HTTPS origin, without URL credentials or parameters.')
        if parts.scheme != 'https':
            if not allow_loopback_http or parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', '::1'):
                raise CliError('Remote gateway requires HTTPS.')
        if not parts.hostname:
            raise CliError('Gateway endpoint is required.')
        self.endpoint = endpoint.rstrip('/')
        self.token_file = token_file
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.owns_session = session is None

    @classmethod
    def from_environment(cls):
        check_agent_environment()
        return cls(os.getenv('MOTATA_GATEWAY_URL', ''), token_file=os.getenv('MOTATA_GATEWAY_JWT_FILE'),
                   allow_loopback_http=os.getenv('MOTATA_GATEWAY_ALLOW_LOOPBACK_HTTP') == '1')

    def close(self):
        if self.owns_session:
            self.session.close()

    def _jwt(self):
        if self.token_file:
            path = Path(self.token_file).expanduser()
            try:
                st = path.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_size > 16384:
                    raise ValueError('Invalid access-token file')
                if os.name != 'nt' and (st.st_mode & 0o077 or st.st_uid != os.geteuid()):
                    raise ValueError('Access-token file must be private')
                token = path.read_text().strip()
            except (OSError, ValueError):
                raise CliError('Gateway access-token file is unavailable or not private.') from None
        else:
            token = os.getenv('MOTATA_GATEWAY_JWT', '')
        if not token or len(token) > 16384 or any(c.isspace() for c in token):
            raise CliError('Gateway JWT is required. Sign in through your trusted issuer; do not fetch a platform token.')
        return token

    def request(self, *, auth: GatewayAuthRef, method: str, path: str,
                query: dict | None = None, body: dict | None = None, body_encoding='json',
                idempotency_key: str | None = None) -> dict:
        if not isinstance(auth, GatewayAuthRef):
            raise CliError('Gateway transport requires an authorization reference, not an access token.')
        account = auth.account_id
        match = re.match(r'^act_([0-9]+)(?:/|$)', path)
        if match:
            if account and account != match[1]:
                raise CliError('Account context does not match the requested path.')
            account = match[1]
        if auth.platform == 'tiktok' and not account:
            account = str((query or {}).get('advertiser_id') or (body or {}).get('advertiser_id') or '')
        if path.startswith('motata-page:'):
            page = path[len('motata-page:'):]
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', page):
                raise CliError('Invalid gateway pagination reference.')
            route, payload = '/v1/pages/' + page, {}
        else:
            if not account or not re.fullmatch(r'[0-9]{1,32}', account):
                raise CliError('An explicit or configured gateway account is required for this operation.')
            route = '/v1/platform/request'
            payload = {'protocol': 'motata-gateway/v1', 'request_id': str(uuid.uuid4()),
                       'platform': auth.platform, 'account_id': account, 'credential_ref': auth.credential_ref,
                       'method': method.upper(), 'path': path.lstrip('/'), 'query': query or {}, 'body': body,
                       'body_encoding': body_encoding, 'idempotency_key': idempotency_key}
        try:
            response = self.session.post(self.endpoint + route, json=payload,
                                         headers={'Authorization': 'Bearer ' + self._jwt()},
                                         timeout=(10, 210), allow_redirects=False, stream=True)
            with response:
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 9 * 1024 * 1024:
                        raise CliError('Gateway response exceeded the byte limit.')
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('protocol') != 'motata-gateway/v1':
                    raise ValueError('Invalid gateway envelope')
                if response.status_code != 200 or result.get('ok') is not True:
                    code = (result.get('error') or {}).get('code', 'GATEWAY_ERROR')
                    code = code if isinstance(code, str) and re.fullmatch(r'[A-Z_]{1,64}', code) else 'GATEWAY_ERROR'
                    raise CliError(f'{code}: gateway request rejected. No direct-token fallback was attempted.')
                if not isinstance(result.get('data'), dict):
                    raise ValueError('Invalid gateway payload')
                return result
        except (requests.RequestException, ValueError, UnicodeError):
            raise CliError('Gateway transport failed. Write outcome may be unknown; do not blindly retry.') from None


# Experimental migration boundary. Direct mode retains the complete existing CLI.
# This is explicit coverage, not a permanent business capability restriction.
REVIEWED_HANDLERS = {
    'command_accounts_inspect',
    'command_campaigns_list', 'command_campaigns_get', 'command_campaigns_create',
    'command_adsets_list', 'command_adsets_get', 'command_ads_list', 'command_ads_get',
    'command_creatives_list', 'command_creatives_get',
    'command_tiktok_campaigns_list', 'command_tiktok_campaigns_get',
    'command_tiktok_adgroups_list', 'command_tiktok_adgroups_get',
    'command_tiktok_ads_list', 'command_tiktok_ads_get',
}


def guard_command(args):
    if not gateway_enabled():
        return
    check_agent_environment()
    for name, value in vars(args).items():
        if (name.endswith('access_token') or name == 'api_key') and value is not None:
            raise CliError('Direct platform credentials are disabled in gateway mode.', exit_code=2)
    if args.command in ('product', 'metrics', 'update'):
        return
    if getattr(args.func, '__name__', '') not in REVIEWED_HANDLERS or getattr(args, 'smart_plus', False):
        raise CliError('GATEWAY_OPERATION_UNAVAILABLE: this command is not migrated yet; no direct-token fallback was attempted.')
