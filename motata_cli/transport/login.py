"""RFC 8628 public-client login; rotating Gateway refresh credentials, NOT ads tokens.

An external issuer must implement the device and token endpoints. This module
never issues JWTs itself and never treats unverified JWT claims as authorization.
Platform OAuth/App secrets are not accepted. Gateway enforces all JWT claims.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
import requests
from motata_cli.common.errors import CliError


def private_file(path, limit=65536):
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as file:
            s = os.fstat(file.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_size > limit:
                raise ValueError('Invalid file')
            if os.name != 'nt' and (s.st_mode & 0o077 or s.st_uid != os.geteuid()):
                raise ValueError('File must be private')
            data = file.read(limit + 1)
            if len(data) > limit:
                raise ValueError('File grew past limit')
            return data
    except (OSError, ValueError):
        raise CliError('Gateway session/config file is unavailable or not private.') from None


def https_url(value, *, origin=False):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ValueError('Invalid URL')
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.query:
        raise ValueError('Fixed HTTPS URL required')
    if origin and p.path not in ('', '/'):
        raise ValueError('Gateway origin required')
    return p


@dataclass(frozen=True)
class LoginConfig:
    issuer: str
    gateway: str
    client_id: str
    device_endpoint: str
    token_endpoint: str
    scope: str = 'gateway:use meta:read tiktok:read'

    def __post_init__(self):
        issuer = https_url(self.issuer)
        https_url(self.gateway, origin=True)
        for endpoint in (self.device_endpoint, self.token_endpoint):
            p = https_url(endpoint)
            if (p.scheme, p.netloc) != (issuer.scheme, issuer.netloc):
                raise ValueError('Issuer endpoints must share the configured origin')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', self.client_id):
            raise ValueError('Invalid client_id')
        scopes = self.scope.split(' ')
        if not 1 <= len(scopes) <= 32 or any(not re.fullmatch(r'[a-z][a-z0-9_:-]{0,63}', x) for x in scopes):
            raise ValueError('Invalid scope')

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path):
        try:
            return cls(**json.loads(private_file(path)))
        except (ValueError, TypeError):
            raise CliError('Invalid gateway login configuration.') from None


class SessionStore:
    """Private atomic file with an interprocess refresh lock.

    Same-user code can read this short-lived bearer capability; this does NOT
    claim to isolate it from the agent. No platform credential is stored here.
    """
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or parent.is_symlink() or (
                os.name != 'nt' and (st.st_mode & 0o077 or st.st_uid != os.geteuid())):
            raise CliError('Gateway session directory must be private and owned by the caller.')

    @contextmanager
    def lock(self, timeout=30):
        path = self.path.with_suffix('.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        locked = False
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (os.name != 'nt' and (st.st_mode & 0o077 or st.st_uid != os.geteuid())):
                raise CliError('Gateway session lock must be private.')
            if not st.st_size:
                os.write(fd, b'0')
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise CliError('Gateway session is busy; no refresh was sent.') from None
                    time.sleep(.05)
            yield
        finally:
            if locked:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(private_file(self.path))
            if not isinstance(value, dict):
                raise ValueError('Invalid session')
            return value
        except ValueError:
            raise CliError('Gateway session is invalid. Sign in again.') from None

    def write(self, value):
        fd, name = tempfile.mkstemp(prefix='.session-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as file:
                if hasattr(os, 'fchmod'):
                    os.fchmod(file.fileno(), 0o600)
                file.write(json.dumps(value, allow_nan=False).encode())
                file.flush(); os.fsync(file.fileno())
            os.replace(name, self.path)
            if os.name != 'nt':
                directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(directory)
                finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)

    def logout(self):
        with self.lock():
            self.write({'schema': 1, 'status': 'logged_out'})


class IssuerUnavailable(CliError):
    pass


class DeviceLogin:
    def __init__(self, config: LoginConfig, store: SessionStore, *, session=None,
                 clock=time.time, sleep=time.sleep):
        self.config, self.store, self.clock, self.sleep = config, store, clock, sleep
        self.http = session or requests.Session()
        self.http.trust_env = False
        self.owns_http = session is None

    def close(self):
        if self.owns_http:
            self.http.close()

    def post(self, endpoint, data):
        try:
            # Endpoints come solely from immutable config. No redirect, cookies,
            # inherited Authorization or platform credentials are forwarded.
            self.http.cookies.clear() if hasattr(self.http, 'cookies') else None
            with self.http.post(endpoint, data=data, timeout=(5, 15), allow_redirects=False,
                                headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'}, stream=True) as response:
                if 300 <= response.status_code < 400:
                    raise CliError('Issuer redirects are forbidden.')
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise CliError('Issuer response exceeds byte limit.')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('Invalid issuer response')
                return response.status_code, value
        except requests.RequestException:
            raise IssuerUnavailable('Gateway issuer request failed. No credentials were printed.') from None
        except ValueError:
            raise CliError('Gateway issuer response is invalid.') from None

    def save_tokens(self, data, *, old_refresh=None):
        access = data.get('access_token')
        refresh = data.get('refresh_token')
        lifetime = data.get('expires_in')
        if (data.get('token_type', '').lower() != 'bearer' or not isinstance(access, str)
                or not 1 <= len(access) <= 16384 or access.count('.') != 2
                or any(c.isspace() for c in access) or type(lifetime) is not int or not 1 <= lifetime <= 900):
            raise CliError('Issuer did not return a supported short-lived Gateway JWT.')
        if refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192 or any(c.isspace() for c in refresh)):
            raise CliError('Issuer refresh credential is invalid.')
        if old_refresh and (not refresh or refresh == old_refresh):
            raise CliError('Rotating refresh is required. Sign in again.')
        scope = data.get('scope', self.config.scope)
        if not isinstance(scope, str) or not set(scope.split()).issubset(self.config.scope.split()):
            raise CliError('Issuer returned unexpected scopes.')
        self.store.write({'schema': 1, 'status': 'active', 'binding': self.config.fingerprint,
                          'expires_at': self.clock() + lifetime, 'access_token': access,
                          'refresh_token': refresh, 'scope': scope})
        return access

    def login(self, display):
        with self.store.lock():
            code, data = self.post(self.config.device_endpoint,
                {'client_id': self.config.client_id, 'scope': self.config.scope})
            if code != 200:
                raise CliError('Device authorization was rejected by the issuer.')
            device, user, uri = data.get('device_code'), data.get('user_code'), data.get('verification_uri')
            lifetime, interval = data.get('expires_in'), data.get('interval', 5)
            try:
                parts = https_url(uri); issuer = https_url(self.config.issuer)
                if parts.netloc != issuer.netloc or not isinstance(device, str) or not 1 <= len(device) <= 8192:
                    raise ValueError('Invalid device response')
                if not isinstance(user, str) or not re.fullmatch(r'[A-Za-z0-9-]{4,64}', user):
                    raise ValueError('Invalid display code')
                if type(lifetime) is not int or not 1 <= lifetime <= 1800 or type(interval) is not int or not 1 <= interval <= 60:
                    raise ValueError('Invalid lifetime/interval')
            except (ValueError, TypeError):
                raise CliError('Issuer device authorization response is invalid.') from None
            # Device code is a secret: never display verification_uri_complete.
            display(uri, user)
            deadline = self.clock() + lifetime
            while self.clock() + interval < deadline:
                self.sleep(interval)
                try:
                    status, response = self.post(self.config.token_endpoint, {
                        'client_id': self.config.client_id,
                        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': device})
                except IssuerUnavailable:
                    interval = min(interval * 2, 60)
                    continue
                if status == 200 and 'error' not in response:
                    return self.save_tokens(response)
                error = response.get('error')
                if error == 'authorization_pending':
                    continue
                if error == 'slow_down':
                    interval += 5
                    continue
                raise CliError('Device authorization ended without a usable login.')
            raise CliError('Device authorization expired. Start a new login.')

    def access_token(self):
        with self.store.lock():
            state = self.store.read()
            if state.get('binding') != self.config.fingerprint or state.get('status') != 'active':
                raise CliError('AUTH_REQUIRED: sign in to the configured Gateway issuer.')
            if type(state.get('expires_at')) not in (int, float):
                raise CliError('Gateway session expiry is invalid.')
            if state['expires_at'] > self.clock() + 30:
                token = state.get('access_token')
                if not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(c.isspace() for c in token):
                    raise CliError('Gateway session token is invalid.')
                return token
            old = state.get('refresh_token')
            # Persist uncertainty BEFORE sending the rotating credential. A crash
            # or lost reply cannot cause another CLI process to reuse the old one.
            self.store.write({'schema': 1, 'status': 'refresh_pending', 'binding': self.config.fingerprint})
            if not old:
                raise CliError('AUTH_REQUIRED: Gateway session expired; sign in again.')
            try:
                status, response = self.post(self.config.token_endpoint,
                    {'client_id': self.config.client_id, 'grant_type': 'refresh_token', 'refresh_token': old})
                if status != 200 or 'error' in response:
                    raise CliError('Refresh was rejected.')
                return self.save_tokens(response, old_refresh=old)
            except Exception:
                # Keep pending state even if the issuer may have completed rotation.
                raise CliError('AUTH_REQUIRED: refresh outcome unavailable. Sign in again; old refresh will not be retried.') from None


def register_gateway_commands(subparsers):
    parser = subparsers.add_parser('gateway', help='Gateway login and local session status; never platform token export')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    children = parser.add_subparsers(dest='gateway_action', required=True)
    for action in ('login', 'status', 'logout'):
        sub = children.add_parser(action)
        sub.set_defaults(func=command_gateway)


def command_gateway(args):
    config = LoginConfig.load(args.config)
    store = SessionStore(args.session)
    if args.gateway_action == 'logout':
        store.logout()
        print(json.dumps({'status': 'logged_out_locally', 'remote_session_revoked': False}))
    elif args.gateway_action == 'status':
        with store.lock():
            data = store.read()
        print(json.dumps({'status': data.get('status', 'missing'), 'expires_at': data.get('expires_at'),
                          'configuration_matches': data.get('binding') == config.fingerprint}))
    else:
        client = DeviceLogin(config, store)
        try:
            # Only the user verification URI and user code are shown, never JWT/refresh/device_code.
            client.login(lambda uri, code: print(json.dumps({'verification_uri': uri, 'user_code': code})))
            print(json.dumps({'status': 'signed_in'}))
        finally:
            client.close()
    return 0
