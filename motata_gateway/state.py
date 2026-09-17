"""Protected, durable authorization, encrypted credentials and write receipts.

The database and encryption key belong to the gateway OS identity, not the CLI.
No plaintext fallback; no credential-export route. SQLite transactions are short.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from .errors import GatewayError
from .jwt_auth import Principal


def check_private(path: Path, *, directory=False):
    st = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(st.st_mode) or path.is_symlink():
        raise ValueError('Private state must be a regular file/directory, never a symlink')
    if os.name != 'nt' and (st.st_mode & 0o077 or st.st_uid != os.geteuid()):
        raise ValueError('Private state must be owned by the gateway user and have no group/other access')


@dataclass(frozen=True)
class CredentialLease:
    reference: str
    platform: str
    value: str = field(repr=False)


class GatewayState:
    def __init__(self, directory: Path, *, create=False):
        self.directory = Path(directory)
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        check_private(self.directory, directory=True)
        self.key_path = self.directory / 'master.key'
        self.db_path = self.directory / 'state.sqlite3'
        if create and not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
        check_private(self.key_path)
        self.cipher = Fernet(self.key_path.read_bytes())
        if create and not self.db_path.exists():
            fd = os.open(self.db_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        check_private(self.db_path)
        if create:
            with self.connection() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                CREATE TABLE IF NOT EXISTS credentials (
                    ref TEXT PRIMARY KEY, platform TEXT NOT NULL, ciphertext BLOB NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (
                    subject TEXT NOT NULL, client TEXT NOT NULL, workspace TEXT NOT NULL,
                    platform TEXT NOT NULL, account TEXT NOT NULL, ref TEXT NOT NULL,
                    PRIMARY KEY(subject, client, workspace, platform, account));
                CREATE TABLE IF NOT EXISTS revoked (
                    kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,value));
                CREATE TABLE IF NOT EXISTS ownership (
                    platform TEXT NOT NULL, object_id TEXT NOT NULL, account TEXT NOT NULL,
                    object_type TEXT NOT NULL, PRIMARY KEY(platform,object_id));
                CREATE TABLE IF NOT EXISTS receipts (
                    owner TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(owner,idem));
                ''')
        # Check schema before accepting requests; a missing migration must fail startup.
        with self.connection() as db:
            db.execute('SELECT subject FROM grants LIMIT 1')
        self.resolve_count = 0  # Safe diagnostic counter; never a token label.

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def put_credential(self, reference: str, platform: str, token: str):
        # Trusted administration only. Binds ciphertext to metadata to prevent row swaps.
        if platform not in ('meta', 'tiktok') or not token or len(token) > 65536:
            raise ValueError('Invalid credential')
        raw = json.dumps({'ref': reference, 'platform': platform, 'token': token}).encode()
        encrypted = self.cipher.encrypt(raw)
        with self.connection() as db:
            db.execute('INSERT INTO credentials VALUES(?,?,?,1,?) ON CONFLICT(ref) DO UPDATE SET '
                       'platform=excluded.platform,ciphertext=excluded.ciphertext,active=1,updated_at=excluded.updated_at',
                       (reference, platform, encrypted, int(time.time())))

    def grant(self, *, subject: str, client: str, workspace: str, platform: str, account: str, reference: str):
        with self.connection() as db:
            row = db.execute('SELECT platform FROM credentials WHERE ref=? AND active=1', (reference,)).fetchone()
            if not row or row[0] != platform:
                raise ValueError('Credential reference does not match platform')
            db.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?,?,?,?)',
                       (subject, client, workspace, platform, account, reference))

    def remove_grant(self, *, subject: str, client: str, workspace: str, platform: str, account: str):
        with self.connection() as db:
            db.execute('DELETE FROM grants WHERE subject=? AND client=? AND workspace=? AND platform=? AND account=?',
                       (subject, client, workspace, platform, account))

    def revoke(self, kind: str, value: str):
        if kind not in ('sub', 'sid', 'jti', 'kid'):
            raise ValueError('Unknown revocation type')
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO revoked VALUES(?,?)', (kind, value))

    def disable_credential(self, reference: str):
        with self.connection() as db:
            db.execute('UPDATE credentials SET active=0 WHERE ref=?', (reference,))

    def check_principal(self, principal: Principal):
        with self.connection() as db:
            for kind, value in (('sub', principal.subject), ('sid', principal.session_id),
                                ('jti', principal.token_id), ('kid', principal.key_id)):
                if db.execute('SELECT 1 FROM revoked WHERE kind=? AND value=?', (kind, value)).fetchone():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway authorization has been revoked.')

    def authorize(self, principal: Principal, platform: str, account: str, reference: str | None) -> str:
        # Does not decrypt, touch a token provider, or contact the platform.
        with self.connection() as db:
            for kind, value in (('sub', principal.subject), ('sid', principal.session_id),
                                ('jti', principal.token_id), ('kid', principal.key_id)):
                if db.execute('SELECT 1 FROM revoked WHERE kind=? AND value=?', (kind, value)).fetchone():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway authorization has been revoked.')
            row = db.execute('SELECT g.ref FROM grants g JOIN credentials c ON c.ref=g.ref '
                             'WHERE g.subject=? AND g.client=? AND g.workspace=? AND g.platform=? '
                             'AND g.account=? AND c.active=1 AND c.platform=g.platform',
                             (principal.subject, principal.client_id, principal.workspace_id, platform, account)).fetchone()
        if not row or (reference is not None and reference != row[0]):
            raise GatewayError('FORBIDDEN', 403, 'Account access is not authorized.')
        return row[0]

    def resolve(self, reference: str, platform: str) -> CredentialLease:
        with self.connection() as db:
            row = db.execute('SELECT ciphertext FROM credentials WHERE ref=? AND platform=? AND active=1',
                             (reference, platform)).fetchone()
        if not row:
            raise GatewayError('AUTH_REQUIRED', 503, 'The administrator must configure platform authorization.')
        try:
            data = json.loads(self.cipher.decrypt(row[0]))
            if data['ref'] != reference or data['platform'] != platform or not isinstance(data['token'], str):
                raise ValueError('Credential binding mismatch')
            self.resolve_count += 1
            return CredentialLease(reference, platform, data['token'])
        except (ValueError, KeyError, InvalidToken):
            raise GatewayError('CREDENTIAL_UNAVAILABLE', 503, 'Platform authorization is unavailable.') from None

    def bind_object(self, platform: str, object_id: str, account: str, object_type: str):
        with self.connection() as db:
            existing = db.execute('SELECT account,object_type FROM ownership WHERE platform=? AND object_id=?',
                                  (platform, object_id)).fetchone()
            if existing and existing != (account, object_type):
                raise GatewayError('OBJECT_BINDING_CONFLICT', 409, 'Object ownership must be reviewed.')
            db.execute('INSERT OR IGNORE INTO ownership VALUES(?,?,?,?)', (platform, object_id, account, object_type))

    def require_object(self, platform: str, object_id: str, account: str, object_type: str | None = None):
        with self.connection() as db:
            row = db.execute('SELECT account,object_type FROM ownership WHERE platform=? AND object_id=?',
                             (platform, object_id)).fetchone()
        if not row or row[0] != account or (object_type and row[1] != object_type):
            raise GatewayError('OBJECT_NOT_AUTHORIZED', 403, 'Object ownership is not verified for this account.')

    @staticmethod
    def owner(principal: Principal, platform: str, account: str) -> str:
        return hashlib.sha256(json.dumps([principal.workspace_id, principal.subject, principal.client_id,
                                        platform, account], separators=(',', ':')).encode()).hexdigest()

    def begin_write(self, owner: str, key: str, fingerprint: str) -> dict | None:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,state,result FROM receipts WHERE owner=? AND idem=?', (owner, key)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise GatewayError('IDEMPOTENCY_CONFLICT', 409, 'Idempotency key was used for a different request.')
                if row[1] == 'complete':
                    return json.loads(row[2])
                raise GatewayError('WRITE_NEEDS_REVIEW', 409, 'Previous write outcome needs review; it was not replayed.', 'unknown')
            db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',
                       (owner, key, fingerprint, 'pending', None, int(time.time())))
        return None

    def finish_write(self, owner: str, key: str, result: dict):
        with self.connection() as db:
            db.execute('UPDATE receipts SET state=?,result=?,updated_at=? WHERE owner=? AND idem=?',
                       ('complete', json.dumps(result, allow_nan=False), int(time.time()), owner, key))

    def safe_status(self) -> list[dict]:
        with self.connection() as db:
            return [dict(zip(('credential_ref', 'platform', 'active', 'updated_at'), row))
                    for row in db.execute('SELECT ref,platform,active,updated_at FROM credentials ORDER BY ref')]
