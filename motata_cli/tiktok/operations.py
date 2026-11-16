"""Durable step claims: a crash or ambiguous write is never silently replayed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from motata_cli.common.config import JOBS_DIR
from motata_cli.common.errors import CliError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class OperationStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else JOBS_DIR / "tiktok-operations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS steps(job_id TEXT, step_id TEXT, payload_hash TEXT, state TEXT, object_id TEXT, request_id TEXT, PRIMARY KEY(job_id,step_id))")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def bind(self, job_id, plan):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?)", (job_id, digest(plan)))
            if db.execute("SELECT plan_hash FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0] != digest(plan):
                raise CliError("Job ID already belongs to a different TikTok plan.")

    @contextmanager
    def lock(self, job_id):
        from motata_cli.meta.services.migration_state import job_lock
        with job_lock(self.path.parent / (digest([str(self.path.resolve()), job_id]) + ".json")):
            # Holding the job lock proves no current writer owns a stale claim.
            with self.connect() as db:
                db.execute("UPDATE steps SET state='unknown' WHERE job_id=? AND state='submitting'", (job_id,))
            yield

    def step(self, job_id, step_id, payload, submit, id_keys, *, retry_failed=False):
        fingerprint = digest(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload_hash,state,object_id FROM steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise CliError(f"Payload changed for existing step {step_id}.")
                if row[1] == "succeeded":
                    return row[2]
                if row[1] == "failed" and retry_failed:
                    db.execute("DELETE FROM steps WHERE job_id=? AND step_id=?", (job_id, step_id))
                else:
                    raise CliError(f"Step {step_id} is {row[1]}; inspect/reconcile remote state before resuming. No write was replayed.")
            db.execute("INSERT INTO steps VALUES (?, ?, ?, 'submitting', NULL, NULL)", (job_id, step_id, fingerprint))
        try:
            result = submit(payload)
            data = result.get("data")
            object_id = next((data.get(key) for key in id_keys if isinstance(data, dict) and data.get(key)), None)
            if isinstance(object_id, list):
                object_id = object_id[0] if len(object_id) == 1 else None
            if not isinstance(object_id, (str, int)) or isinstance(object_id, bool):
                raise CliError(f"Step {step_id} returned no unambiguous object ID; verify remote state.")
            self.update(job_id, step_id, "succeeded", str(object_id), result.get("request_id"))
            return str(object_id)
        except Exception as exc:
            state = "failed" if getattr(exc, "write_outcome", None) in ("failed", "not_submitted") else "unknown"
            self.update(job_id, step_id, state, request_id=getattr(exc, "platform_request_id", None))
            raise

    def update(self, job_id, step_id, state, object_id=None, request_id=None):
        with self.connect() as db:
            db.execute("UPDATE steps SET state=?, object_id=?, request_id=? WHERE job_id=? AND step_id=?",
                       (state, object_id, request_id, job_id, step_id))

    def rows(self, job_id):
        with self.connect() as db:
            return [{"step": row[0], "state": row[1], "object_id": row[2], "request_id": row[3]}
                    for row in db.execute("SELECT step_id,state,object_id,request_id FROM steps WHERE job_id=? ORDER BY rowid", (job_id,))]
