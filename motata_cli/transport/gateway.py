"""HTTPS gateway transport. Carries only a short-lived gateway JWT, never platform tokens."""
from __future__ import annotations
import json
import hashlib
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


@dataclass(frozen=True)
class GatewayPageRef:
    reference: str
    account_id: str
    page_id: str


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
        self._write_root = os.getenv('MOTATA_GATEWAY_RUN_ID') or uuid.uuid4().hex
        self._write_counts = {}
        self._objects = set()

    @classmethod
    def from_environment(cls):
        check_agent_environment()
        return cls(os.getenv('MOTATA_GATEWAY_URL', ''), token_file=os.getenv('MOTATA_GATEWAY_JWT_FILE'),
                   allow_loopback_http=os.getenv('MOTATA_GATEWAY_ALLOW_LOOPBACK_HTTP') == '1')

    def close(self):
        if self.owns_session:
            self.session.close()

    def _jwt(self):
        session_file = os.getenv('MOTATA_GATEWAY_SESSION_FILE')
        if session_file:
            from .login import LoginConfig, SessionStore, DeviceLogin
            if self.token_file or os.getenv('MOTATA_GATEWAY_JWT'):
                raise CliError('Configure exactly one gateway access-token source.')
            config = LoginConfig.load(os.getenv('MOTATA_GATEWAY_LOGIN_CONFIG', ''))
            if config.gateway.rstrip('/') != self.endpoint:
                raise CliError('Gateway session is bound to a different gateway origin.')
            login = DeviceLogin(config, SessionStore(session_file))
            try:
                return login.access_token()
            finally:
                login.close()
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
                idempotency_key: str | None = None, files: dict | None = None,
                page_credential_ref: str | None = None) -> dict:
        if not isinstance(auth, GatewayAuthRef):
            raise CliError('Gateway transport requires an authorization reference, not an access token.')
        if auth.platform == 'meta' and path.lstrip('/') == 'me/adaccounts':
            if method.upper() != 'GET':
                raise CliError('Account discovery is read only.')
            return self._send('/v1/accounts', payload={'platform': 'meta', 'query': query or {}})
        if path.startswith('motata-accounts:'):
            return self._send('/v1/accounts',payload={'platform':auth.platform,'cursor':path.split(':',1)[1]})
        account = auth.account_id
        match = re.match(r'^act_([0-9]+)(?:/|$)', path)
        if match:
            if account and account != match[1]:
                raise CliError('Account context does not match the requested path.')
            account = match[1]
        if auth.platform == 'tiktok' and not account:
            params=(query or {}) if method.upper()=='GET' else (body or {})
            ids=params.get('advertiser_ids') or []
            if isinstance(ids,str):
                try: ids=json.loads(ids)
                except ValueError: ids=[]
            account = str(params.get('advertiser_id') or params.get('source_advertiser_id') or (ids[0] if isinstance(ids,list) and ids else '') or '')
        if not account and not path.startswith('motata-page:'):
            discovery=self._send('/v1/accounts',payload={'platform':auth.platform})['data']['data']
            if len(discovery)==1:
                row=discovery[0]; account=str(row.get('account_id') or row.get('advertiser_id') or '')
        path = path.lstrip('/')
        if auth.platform == 'meta' and path == 'me/accounts':
            if not account:
                raise CliError('An account is required for authorized Page discovery.')
            # Account-specific pages only, never all pages reachable by a broad user token.
            path = 'act_' + account + '/promote_pages'
        root_id = path.split('/')[0]
        if auth.platform == 'meta' and account and not page_credential_ref and re.fullmatch(r'[0-9]{1,32}', root_id):
            object_key = (account, root_id)
            if object_key not in self._objects:
                self._send('/v1/objects/resolve', payload={
                    'protocol': 'motata-gateway/v1', 'request_id': str(uuid.uuid4()),
                    'platform': 'meta', 'account_id': account, 'credential_ref': auth.credential_ref,
                    'method': 'GET', 'path': root_id,
                    'query': {'object_type':'video'} if 'source' in str((query or {}).get('fields','')).split(',') else {}})
                self._objects.add(object_key)
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
                       'method': method.upper(), 'path': path, 'query': {k: v for k, v in (query or {}).items() if v is not None}, 'body': body,
                       'body_encoding': body_encoding, 'idempotency_key': idempotency_key}
            if page_credential_ref: payload['page_credential_ref']=page_credential_ref
        from contextlib import nullcontext
        from .uploads import UploadBody
        with UploadBody(files) if files else nullcontext(None) as upload:
            if upload:
                if route != '/v1/platform/request':
                    raise CliError('Cannot upload to a pagination reference.')
                payload['uploads'] = upload.specs
            if method.upper() != 'GET' and not payload.get('idempotency_key'):
                # One invocation namespace, distinct keys for identical intentional
                # writes. No automatic write retry. A durable workflow must retain
                # its own run/step identity (migration already has a ledger).
                raw = json.dumps({k: v for k, v in payload.items() if k not in ('request_id', 'idempotency_key')},
                                 sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                fingerprint = hashlib.sha256(raw).hexdigest()
                index = self._write_counts.get(fingerprint, 0)
                self._write_counts[fingerprint] = index + 1
                payload['idempotency_key'] = hashlib.sha256(
                    f'{self._write_root}:{fingerprint}:{index}'.encode()).hexdigest()
            if upload:
                return self._send('/v1/platform/upload', data=upload.encode(payload),
                                  content_type='application/vnd.motata.upload-v1')
            return self._send(route, payload=payload)

    def page_credentials(self, auth: GatewayAuthRef):
        accounts=[auth.account_id] if auth.account_id else [r['account_id'] for r in self._send('/v1/accounts',payload={'platform':'meta'})['data']['data']]
        result=[]
        for account in accounts:
            response=self._send('/v1/meta/page-credentials',payload={
                'protocol':'motata-gateway/v1','request_id':str(uuid.uuid4()),'platform':'meta',
                'account_id':account,'credential_ref':auth.credential_ref,'method':'GET','path':'me/accounts',
                'query':{'fields':'id,name,tasks'}})
            result.extend(response['data']['data'])
        return result

    def download(self, reference: str, destination: Path, *, max_bytes=4_000_000_000):
        import tempfile
        if not isinstance(reference,str) or not re.fullmatch(r'motata-download:[A-Za-z0-9_-]{16,64}',reference):
            raise CliError('Only gateway-issued download references can use the media channel.')
        destination=Path(destination); temporary=None
        try:
            # Destination belongs to CLI OS identity, not the gateway service.
            fd,name=tempfile.mkstemp(prefix=destination.name+'.',suffix='.part',dir=destination.parent)
            temporary=Path(name)
            with os.fdopen(fd,'wb') as output:
                response=self.session.post(self.endpoint+'/v1/downloads/'+reference.split(':',1)[1],
                    json={},headers={'Authorization':'Bearer '+self._jwt()},
                    timeout=(10,660),allow_redirects=False,stream=True)
                with response:
                    if response.status_code!=200:
                        raise CliError('Gateway media unavailable; refetch source metadata. No direct URL fallback.')
                    declared=response.headers.get('Content-Length','')
                    expected=response.headers.get('X-Motata-Sha256','')
                    if not declared.isdigit() or int(declared)>max_bytes or not re.fullmatch(r'[0-9a-f]{64}',expected):
                        raise CliError('Invalid gateway download integrity headers.')
                    size=0; digest=hashlib.sha256()
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>max_bytes or size>int(declared): raise CliError('Gateway download exceeds limit.')
                        digest.update(chunk);output.write(chunk)
                    if size!=int(declared) or digest.hexdigest()!=expected:
                        raise CliError('Gateway download integrity check failed.')
                    output.flush();os.fsync(output.fileno())
            temporary.replace(destination)
            return {'size':size,'sha256':expected}
        except (requests.RequestException,OSError,ValueError):
            raise CliError('Gateway media transfer failed; no partial destination was committed.') from None
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    def _send(self, route, *, payload=None, data=None, content_type=None):
        headers = {'Authorization': 'Bearer ' + self._jwt()}
        if content_type:
            headers['Content-Type'] = content_type
        kwargs = {'json': payload} if data is None else {'data': data}
        try:
            response = self.session.post(self.endpoint + route, **kwargs, headers=headers,
                                         timeout=(10, 330), allow_redirects=False, stream=True)
            with response:
                if 300 <= response.status_code < 400:
                    raise CliError('Gateway redirects are forbidden.')
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 9 * 1024 * 1024:
                        raise CliError('Gateway response exceeded the byte limit.')
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('protocol') != 'motata-gateway/v1':
                    raise ValueError('Invalid gateway envelope')
                if response.status_code != 200 or result.get('ok') is not True:
                    error = result.get('error') or {}
                    code = error.get('code', 'GATEWAY_ERROR')
                    code = code if isinstance(code, str) and re.fullmatch(r'[A-Z_]{1,64}', code) else 'GATEWAY_ERROR'
                    exc = CliError(f'{code}: gateway request rejected. No direct-token fallback was attempted.')
                    exc.gateway_code = code
                    exc.write_outcome = error.get('write_outcome', 'unknown')
                    raise exc
                if not isinstance(result.get('data'), dict):
                    raise ValueError('Invalid gateway payload')
                return result
        except (requests.RequestException, ValueError, UnicodeError):
            raise CliError('Gateway transport failed. Write outcome may be unknown; do not blindly retry.') from None


# Transport coverage, not an endpoint allow-all. The server remains authoritative.
# Credential administration and unadapted interactive onboarding are separate.
def guard_command(args):
    if not gateway_enabled():
        return
    check_agent_environment()
    for name, value in vars(args).items():
        if (name.endswith('access_token') or name in ('api_key', 'secret', 'app_secret', 'client_secret')) and value is not None:
            raise CliError('Direct platform credentials are disabled in gateway mode.', exit_code=2)
    if args.command in ('product', 'metrics', 'update', 'meta', 'tiktok', 'report', 'gateway'):
        if getattr(args.func, '__name__', '') == 'command_tiktok_auth_advertisers':
            raise CliError('Credential administration is disabled in gateway mode.')
        return
    raise CliError('GATEWAY_OPERATION_UNAVAILABLE: use the trusted onboarding/admin entry for this command.')
