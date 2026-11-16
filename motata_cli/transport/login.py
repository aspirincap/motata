"""RFC 8628 device login and fail-closed rotating refresh sessions.

Only Gateway credentials are stored here. Platform/Auth Center secrets never
enter this process. The issuer must actually implement this OAuth contract.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import requests
from motata_cli.common.errors import CliError


def private_json(path):
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'r') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError()
            if os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                raise ValueError()
            data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError()
            return data
    except (OSError, ValueError):
        raise CliError('Gateway session/configuration file is unavailable or not private.') from None


def save_private(path, data):
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.geteuid())):
        raise CliError('Gateway session directory must be private and owned by the current user.')
    if path.is_symlink():
        raise CliError('Gateway session symlinks are forbidden.')
    descriptor, temporary = tempfile.mkstemp(prefix='.session-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def session_lock(path):
    """Cross-process lock; OS releases it if login/refresh crashes."""
    path = Path(path).expanduser()
    save_private(path.parent / '.lock-policy', {'version': 1})
    descriptor = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and (info.st_mode & 0o077 or info.st_uid != os.geteuid())):
            raise CliError('Gateway session lock must be private.')
        if os.name == 'nt':
            import msvcrt
            stream.write(b'0'); stream.flush(); stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise CliError('Gateway session is in use; retry later.') from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise CliError('Gateway session is in use; retry later.') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def https_url(value):
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
        raise CliError('OAuth endpoints must be fixed HTTPS URLs without credentials or query parameters.')
    return value


class GatewayLogin:
    def __init__(self, config, session_file, *, http=None, clock=time.time, sleep=time.sleep):
        self.config = config
        if set(config) != {'issuer', 'client_id', 'audience', 'scope', 'device_authorization_endpoint', 'token_endpoint'}:
            raise CliError('Invalid Gateway OAuth configuration.')
        origin = urlsplit(https_url(config['issuer'])).netloc
        for field in ('device_authorization_endpoint', 'token_endpoint'):
            if urlsplit(https_url(config[field])).netloc != origin:
                raise CliError('OAuth endpoints must use the configured issuer origin.')
        for field in ('client_id', 'audience', 'scope'):
            if not isinstance(config[field], str) or not 1 <= len(config[field]) <= 1024:
                raise CliError('Invalid Gateway OAuth identity/scope.')
        self.path = Path(session_file).expanduser()
        self.http = http or requests.Session()
        self.http.trust_env = False
        self.owns_http = http is None
        self.clock, self.sleep = clock, sleep

    def close(self):
        if self.owns_http:
            self.http.close()

    def post(self, endpoint, data):
        try:
            with self.http.post(endpoint, data=data, timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code not in (200, 400):
                    raise ValueError()
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except (requests.RequestException, ValueError, UnicodeError):
            raise CliError('Gateway issuer request failed; credential details were suppressed.') from None

    def accept(self, reply):
        token, lifetime = reply.get('access_token'), reply.get('expires_in')
        if reply.get('token_type', '').lower() != 'bearer' or not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(c.isspace() for c in token):
            raise CliError('Issuer returned an invalid Gateway access token.')
        if type(lifetime) is not int or not 0 < lifetime <= 900:
            raise CliError('Issuer access-token lifetime must be at most 15 minutes.')
        refresh = reply.get('refresh_token')
        if refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 16384):
            raise CliError('Issuer returned an invalid refresh token.')
        return {'config': self.config, 'access_token': token, 'refresh_token': refresh,
                'expires_at': self.clock() + lifetime, 'state': 'ready'}

    def login(self, display=print):
        with session_lock(self.path):
            data = self.post(self.config['device_authorization_endpoint'],
                             {key: self.config[key] for key in ('client_id', 'scope', 'audience')})
            try:
                verification = https_url(data['verification_uri'])
                if urlsplit(verification).netloc != urlsplit(self.config['issuer']).netloc:
                    raise ValueError()
                code, device = data['user_code'], data['device_code']
                if not all(isinstance(v, str) and 1 <= len(v) <= limit for v, limit in ((code, 64), (device, 4096))):
                    raise ValueError()
                duration, interval = data['expires_in'], data.get('interval', 5)
                if type(duration) is not int or not 1 <= duration <= 1800 or type(interval) is not int or not 1 <= interval <= 60:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise CliError('Issuer returned an invalid device authorization response.') from None
            display(f'Open {verification} and enter code {code} to authorize Gateway access.')
            deadline = self.clock() + duration
            while self.clock() + interval < deadline:
                self.sleep(interval)
                reply = self.post(self.config['token_endpoint'], {'client_id': self.config['client_id'],
                                  'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': device})
                error = reply.get('error')
                if error == 'authorization_pending':
                    continue
                if error == 'slow_down':
                    interval += 5
                    continue
                if error:
                    raise CliError('Gateway login was denied or expired. Start login again.')
                save_private(self.path, self.accept(reply))
                return
            raise CliError('Gateway device authorization expired.')

    def access_token(self):
        with session_lock(self.path):
            saved = private_json(self.path)
            if saved.get('config') != self.config or saved.get('state') != 'ready':
                raise CliError('Gateway session requires login. An uncertain refresh is never replayed.')
            if saved.get('expires_at', 0) > self.clock() + 60:
                return saved['access_token']
            old = saved.get('refresh_token')
            if not old:
                raise CliError('Gateway session expired. Run gateway login.')
            # Durable tombstone precedes IO: even a process crash cannot reuse a
            # refresh token that the issuer may have already consumed.
            save_private(self.path, {'config': self.config, 'state': 'refresh_pending'})
            reply = self.post(self.config['token_endpoint'], {'client_id': self.config['client_id'],
                              'grant_type': 'refresh_token', 'refresh_token': old})
            if reply.get('error') or not reply.get('refresh_token') or reply.get('refresh_token') == old:
                raise CliError('Gateway refresh requires login; refresh-token rotation was not confirmed.')
            updated = self.accept(reply)
            save_private(self.path, updated)
            return updated['access_token']


def command_gateway(args):
    path = Path(args.session_file).expanduser()
    if args.action == 'status':
        saved = private_json(path) if path.exists() else {}
        print(json.dumps({'configured': bool(saved), 'state': saved.get('state', 'missing'),
                          'expires_at': saved.get('expires_at')}))
        return
    if args.action == 'logout':
        with session_lock(path):
            save_private(path, {'state': 'logged_out'})
        print('Gateway local session removed. Revoke the issuer session to invalidate existing access tokens.')
        return
    client = GatewayLogin(private_json(Path(args.config).expanduser()), path)
    try:
        client.login()
        print('Gateway login complete. No credential was printed.')
    finally:
        client.close()


def register_gateway_commands(subparsers):
    parser = subparsers.add_parser('gateway', help='Gateway device login and safe session status')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('login', 'status', 'logout'):
        child = actions.add_parser(action)
        child.add_argument('--session-file', default='~/.motata/gateway/session.json')
        if action == 'login':
            child.add_argument('--config', required=True)
        child.set_defaults(func=command_gateway)
