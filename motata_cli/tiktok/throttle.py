"""Cross-process admission pacing and cooldowns for read-only TikTok endpoints.

State is deliberately shared across accounts/credentials on this machine: a
direct token does not identify its developer app. No credentials are persisted.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import sqlite3
import time

from motata_cli.common.config import CACHE_DIR
from motata_cli.common.errors import CliError


class ReadThrottle:
    # Conservative local defaults, not claims about TikTok's published quotas.
    DEFAULT_INTERVAL = 1.0
    REVIEW_INTERVAL = 2.0

    def __init__(self, path=None, *, clock=None):
        self.path = Path(path) if path is not None else CACHE_DIR / 'tiktok-read-limits.sqlite3'
        self.clock = clock or time.time
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS endpoint_limits ('
                           'endpoint TEXT PRIMARY KEY, next_start REAL NOT NULL, cooldown_until REAL NOT NULL)')
            if os.name != 'nt':
                self.path.chmod(0o600)
        except OSError:
            self._unavailable()

    @staticmethod
    def _unavailable():
        # Do not expose SQLite paths or replay reads when coordination fails.
        error = CliError('TikTok read limiter state is unavailable; request stopped before sending. '
                         'Check local state directory access and retry.')
        error.retry_managed = True
        error.retry_stop_reason = 'limiter_error'
        raise error from None

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=2)
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            if db is not None:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            self._unavailable()
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _key(endpoint):
        if endpoint.write:
            raise ValueError('The read limiter cannot schedule writes.')
        return 'business-api.tiktok.com/v1.3/' + endpoint.method + '/' + endpoint.path

    def _state(self, db, key):
        row = db.execute('SELECT next_start, cooldown_until FROM endpoint_limits WHERE endpoint=?', (key,)).fetchone()
        if row is None:
            return 0.0, 0.0
        if not all(isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 for value in row):
            raise ValueError('Invalid limiter state')
        return row

    def acquire_delay(self, endpoint):
        """Claim an admission slot only when ready; otherwise return time to wait.

        Call again after waiting: a concurrent 429 can extend the cooldown.
        Transactions never span a sleep, credential fetch, or platform request.
        """
        key = self._key(endpoint)
        with self._connect() as db:
            now = self.clock()
            next_start, cooldown = self._state(db, key)
            delay = max(next_start, cooldown) - now
            if delay > 0:
                return delay
            interval = self.REVIEW_INTERVAL if endpoint.path.endswith('/review_info/') else self.DEFAULT_INTERVAL
            db.execute('INSERT INTO endpoint_limits VALUES (?, ?, ?) ON CONFLICT(endpoint) '
                       'DO UPDATE SET next_start=excluded.next_start', (key, now + interval, cooldown))
            return 0.0

    def defer(self, endpoint, delay):
        """Publish a minimum next-read time, including after the final failed attempt."""
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('Cooldown must be finite and nonnegative.')
        key = self._key(endpoint)
        with self._connect() as db:
            next_start, cooldown = self._state(db, key)
            until = max(cooldown, self.clock() + delay)
            db.execute('INSERT INTO endpoint_limits VALUES (?, ?, ?) ON CONFLICT(endpoint) '
                       'DO UPDATE SET cooldown_until=excluded.cooldown_until', (key, next_start, until))


def default_read_throttle():
    return ReadThrottle()
