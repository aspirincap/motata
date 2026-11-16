"""Durable non-secret usage evidence. Unknown POSTs are never replayed."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from motata_cli.common.config import CACHE_DIR


class UsageStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else CACHE_DIR / "auth-center-usage.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage_events (event_id TEXT PRIMARY KEY, origin TEXT, key_id TEXT, state TEXT, platform_outcome TEXT, payload TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def prepare(self, provider, lease, endpoint):
        event = {"channel": "tiktok", "units": 1, "endpoint": endpoint, "request_id": str(uuid.uuid4())}
        if lease.account_id:
            event["account_id"] = lease.account_id
        if lease.agent_id:
            event["agent_id"] = lease.agent_id
        with self.connect() as db:
            db.execute("INSERT INTO usage_events(event_id, origin, key_id, state, payload) VALUES (?, ?, ?, ?, ?)",
                       (event["request_id"], provider.base_url, provider.key_id, "platform_pending", json.dumps(event)))
        return event

    def update(self, event_id, state, outcome):
        with self.connect() as db:
            db.execute("UPDATE usage_events SET state=?, platform_outcome=? WHERE event_id=?", (state, outcome, event_id))
