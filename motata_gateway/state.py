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
from dataclasses import dataclass, field, asdict
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
    expires_at: float | None = None
    token_version: int | None = None
    sensitive_values: tuple[str, ...] = field(default=(), repr=False)
    cache_key: tuple | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CredentialDescriptor:
    reference: str
    platform: str
    provider: str
    binding_json: str
    revision: int


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
                CREATE TABLE IF NOT EXISTS asset_membership (
                    platform TEXT NOT NULL, object_id TEXT NOT NULL, account TEXT NOT NULL,
                    object_type TEXT NOT NULL, PRIMARY KEY(platform,object_id,account,object_type));
                CREATE TABLE IF NOT EXISTS receipts (
                    owner TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(owner,idem));
                ''')
                # Explicit admin init is also the non-destructive v2 schema migration.
                columns = {row[1] for row in db.execute('PRAGMA table_info(credentials)')}
                for name, definition in (
                    ('provider', "TEXT NOT NULL DEFAULT 'direct'"),
                    ('binding', "TEXT NOT NULL DEFAULT '{}'"),
                    ('revision', 'INTEGER NOT NULL DEFAULT 1'),
                ):
                    if name not in columns:
                        db.execute(f'ALTER TABLE credentials ADD COLUMN {name} {definition}')
        # Check schema before accepting requests; a missing migration must fail startup.
        with self.connection() as db:
            try:
                db.execute('SELECT provider,binding,revision FROM credentials LIMIT 1')
                db.execute('SELECT subject FROM grants LIMIT 1')
                db.execute('SELECT account FROM asset_membership LIMIT 1')
            except sqlite3.DatabaseError:
                raise ValueError('Protected state requires offline admin init/migration') from None
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
            db.execute("INSERT INTO credentials(ref,platform,ciphertext,active,updated_at,provider,binding,revision) "
                       "VALUES(?,?,?,1,?,'direct','{}',1) ON CONFLICT(ref) DO UPDATE SET "
                       "platform=excluded.platform,ciphertext=excluded.ciphertext,active=1,updated_at=excluded.updated_at,"
                       "provider='direct',binding='{}',revision=credentials.revision+1",
                       (reference, platform, encrypted, int(time.time())))

    def put_auth_center_binding(self, reference: str, binding):
        """Trusted offline declaration; never call fallback account-token APIs.

        The administrator must verify the account/OAuth identity association.
        This binding is NOT evidence automatically discovered from Auth Center.
        """
        from .auth_center import AuthCenterBinding, NAME
        if not isinstance(binding, AuthCenterBinding) or not NAME.fullmatch(reference):
            raise ValueError('Invalid Auth Center binding')
        with self.connection() as db:
            db.execute("INSERT INTO credentials(ref,platform,ciphertext,active,updated_at,provider,binding,revision) "
                       "VALUES(?,?,?,1,?,'auth_center',?,1) ON CONFLICT(ref) DO UPDATE SET "
                       "platform=excluded.platform,ciphertext=excluded.ciphertext,active=1,updated_at=excluded.updated_at,"
                       "provider='auth_center',binding=excluded.binding,revision=credentials.revision+1",
                       (reference, binding.platform, b'', int(time.time()),
                        json.dumps(asdict(binding), sort_keys=True)))

    def describe(self, reference: str, platform: str) -> CredentialDescriptor:
        with self.connection() as db:
            row = db.execute('SELECT provider,binding,revision FROM credentials WHERE ref=? AND platform=? AND active=1',
                             (reference, platform)).fetchone()
        if not row:
            raise GatewayError('AUTH_REQUIRED', 503, 'Platform authorization is unavailable.')
        return CredentialDescriptor(reference, platform, *row)

    @staticmethod
    def require_binding_scope(provider: str, binding_json: str, workspace: str, platform: str, account: str):
        if provider == 'direct':
            return
        try:
            from .auth_center import AuthCenterBinding
            if provider != 'auth_center':
                raise ValueError('Unknown provider')
            AuthCenterBinding(**json.loads(binding_json)).require_scope(workspace, platform, account)
        except (ValueError, TypeError, KeyError):
            raise GatewayError('CREDENTIAL_UNAVAILABLE', 503, 'Credential binding is invalid.') from None

    def grant(self, *, subject: str, client: str, workspace: str, platform: str, account: str, reference: str):
        with self.connection() as db:
            row = db.execute('SELECT platform,provider,binding FROM credentials WHERE ref=? AND active=1', (reference,)).fetchone()
            if not row or row[0] != platform:
                raise ValueError('Credential reference does not match platform')
            self.require_binding_scope(row[1], row[2], workspace, platform, account)
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
            row = db.execute('SELECT g.ref,c.provider,c.binding FROM grants g JOIN credentials c ON c.ref=g.ref '
                             'WHERE g.subject=? AND g.client=? AND g.workspace=? AND g.platform=? '
                             'AND g.account=? AND c.active=1 AND c.platform=g.platform',
                             (principal.subject, principal.client_id, principal.workspace_id, platform, account)).fetchone()
        if not row or (reference is not None and reference != row[0]):
            raise GatewayError('FORBIDDEN', 403, 'Account access is not authorized.')
        self.require_binding_scope(row[1], row[2], principal.workspace_id, platform, account)
        return row[0]

    def resolve(self, reference: str, platform: str) -> CredentialLease:
        with self.connection() as db:
            row = db.execute("SELECT ciphertext FROM credentials WHERE ref=? AND platform=? AND active=1 AND provider='direct'",
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

    SHARED_TYPES = frozenset({'page', 'pixel', 'app', 'instagram', 'image', 'video',
                              'bc', 'catalog', 'store', 'identity', 'portfolio',
                              'audience', 'event_set', 'avatar', 'task', 'post'})

    def bind_object(self, platform: str, object_id: str, account: str, object_type: str):
        import re
        if not re.fullmatch(r'[0-9]{1,32}', account) or not isinstance(object_id, str) or not object_id or len(object_id) > 512 or any(ord(c) < 32 for c in object_id):
            raise GatewayError('OBJECT_BINDING_CONFLICT', 409, 'Invalid resource identity.')
        if object_type in self.SHARED_TYPES:
            with self.connection() as db:
                db.execute('INSERT OR IGNORE INTO asset_membership VALUES(?,?,?,?)',
                           (platform, object_id, account, object_type))
            return
        with self.connection() as db:
            existing = db.execute('SELECT account,object_type FROM ownership WHERE platform=? AND object_id=?',
                                  (platform, object_id)).fetchone()
            if existing and existing[0] == account and (existing[1] == 'adobject' or object_type == 'adobject'):
                if existing[1] == 'adobject' and object_type != 'adobject':
                    db.execute('UPDATE ownership SET object_type=? WHERE platform=? AND object_id=?', (object_type, platform, object_id))
                return
            if existing and existing != (account, object_type):
                raise GatewayError('OBJECT_BINDING_CONFLICT', 409, 'Object ownership must be reviewed.')
            db.execute('INSERT OR IGNORE INTO ownership VALUES(?,?,?,?)', (platform, object_id, account, object_type))

    def require_object(self, platform: str, object_id: str, account: str, object_type: str | None = None):
        with self.connection() as db:
            row = db.execute('SELECT account,object_type FROM ownership WHERE platform=? AND object_id=?',
                             (platform, object_id)).fetchone()
            shared = db.execute('SELECT object_type FROM asset_membership WHERE platform=? AND object_id=? AND account=?',
                                (platform, object_id, account)).fetchall()
        if any(object_type is None or r[0] == object_type for r in shared):
            return
        if not row or row[0] != account or (object_type and row[1] != object_type and not (row[1] == 'adobject' and object_type in ('campaign', 'adset', 'ad', 'creative'))):
            raise GatewayError('OBJECT_NOT_AUTHORIZED', 403, 'Object ownership is not verified for this account.')

    def objects_for(self, platform, account, object_type):
        with self.connection() as db:
            return [row[0] for row in db.execute('SELECT object_id FROM asset_membership WHERE platform=? AND account=? AND object_type=? UNION SELECT object_id FROM ownership WHERE platform=? AND account=? AND object_type=?',
                (platform, account, object_type, platform, account, object_type))]

    def remove_asset(self, platform, account, object_id, object_type):
        with self.connection() as db:
            db.execute('DELETE FROM asset_membership WHERE platform=? AND account=? AND object_id=? AND object_type=?',
                       (platform, account, object_id, object_type))

    @staticmethod
    def owner(principal: Principal, platform: str, account: str) -> str:
        return hashlib.sha256(json.dumps([principal.workspace_id, principal.subject, principal.client_id,
                                        platform, account], separators=(',', ':')).encode()).hexdigest()

    def lookup_write(self, owner: str, key: str, fingerprint: str) -> dict | None:
        """Read a receipt before external credential IO; never create pending yet."""
        with self.connection() as db:
            row = db.execute('SELECT fingerprint,state,result FROM receipts WHERE owner=? AND idem=?', (owner, key)).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise GatewayError('IDEMPOTENCY_CONFLICT', 409, 'Idempotency key was used for a different request.')
        if row[1] != 'complete':
            raise GatewayError('WRITE_NEEDS_REVIEW', 409, 'Previous write needs review; it was not replayed.', 'unknown')
        return json.loads(row[2])

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

    def accounts_for(self, principal, platform):
        self.check_principal(principal)
        with self.connection() as db:
            rows = db.execute('SELECT g.account FROM grants g JOIN credentials c ON c.ref=g.ref '
                'WHERE g.subject=? AND g.client=? AND g.workspace=? AND g.platform=? AND c.active=1 '
                'ORDER BY g.account', (principal.subject, principal.client_id, principal.workspace_id, platform)).fetchall()
        # Return only configured grants, never all accounts available to a broad platform token.
        return [row[0] for row in rows]

    def safe_status(self) -> list[dict]:
        with self.connection() as db:
            return [dict(zip(('credential_ref', 'platform', 'active', 'updated_at', 'provider', 'revision'), row))
                    for row in db.execute('SELECT ref,platform,active,updated_at,provider,revision FROM credentials ORDER BY ref')]
