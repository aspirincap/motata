"""Durable, fail-closed migration checkpoints (no remote cleanup or name matching)."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_WINDOWS = sys.platform == "win32"
if _WINDOWS:
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = 1


class MigrationStateError(RuntimeError):
    pass


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def job_path(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise MigrationStateError("Invalid migration job ID")
    return root / f"{job_id}.json"


@contextmanager
def job_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock file: unlinking it would allow competing locks on different files.
    with path.with_suffix(".lock").open("a+b") as handle:
        try:
            if _WINDOWS:
                # Windows permits locking beyond EOF, so even an empty file is safe.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise MigrationStateError("Migration job is already running") from exc
            raise
        try:
            yield
        finally:
            if _WINDOWS:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        # Windows cannot open/fsync directories through these POSIX APIs.
        # The temporary file is still flushed and replaced atomically above.
        if not _WINDOWS:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_job(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise MigrationStateError("Legacy/unsupported job: unsafe resume refused; reconcile remote objects manually")
        if data.get("type") != "migration" or data.get("job_id") != path.stem:
            raise ValueError("identity")
        if data.get("status") not in {"running", "failed", "needs_review", "completed"}:
            raise ValueError("status")
        for key in ("config", "ledger", "stages"):
            if not isinstance(data[key], dict):
                raise ValueError(key)
        config = data["config"]
        for field in ("source_account_id", "target_account_id", "export_dir"):
            if not isinstance(config.get(field), str) or not config[field]:
                raise ValueError("config")
        if not isinstance(data["export_fingerprint"], str) or (data["export_fingerprint"] and not re.fullmatch(r"[0-9a-f]{64}", data["export_fingerprint"])):
            raise ValueError("export fingerprint")
        for key, entry in data["ledger"].items():
            if not isinstance(entry, dict):
                raise ValueError("ledger entry")
            if entry.get("kind") not in {"video", "image", "creative", "campaign", "adset", "ad"}:
                raise ValueError("ledger kind")
            if not isinstance(entry.get("source_id"), str) or not entry["source_id"]:
                raise ValueError("source ID")
            if key != f"{entry['kind']}:{entry['source_id']}" or not isinstance(entry.get("request_fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["request_fingerprint"]):
                raise ValueError("ledger identity")
            if entry["status"] not in {"pending", "confirmed", "needs_review"}:
                raise ValueError("ledger status")
            if entry["status"] == "confirmed" and (not isinstance(entry.get("target_id"), str) or not entry["target_id"]):
                raise ValueError("missing target ID")
        if data["status"] == "completed" and (not isinstance(data.get("result"), dict) or any(e["status"] != "confirmed" for e in data["ledger"].values())):
            raise ValueError("incomplete result")
        if any(e["status"] != "confirmed" for e in data["ledger"].values()):
            data["status"] = "needs_review"
        return data
    except MigrationStateError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MigrationStateError("Missing/corrupt migration checkpoint; unsafe resume refused") from exc


class MigrationState:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path, self.data = path, data

    def save(self):
        self.data["cleanup_plan"] = {
            "mode": "plan_only",
            "objects": [dict(e) for e in self.data["ledger"].values()],
            "recommendation": "Keep confirmed objects PAUSED. Reconcile pending writes using request receipts/remote IDs and target-account ownership; names are not proof. Do not rerun or delete until reviewed.",
        }
        atomic_json(self.path, self.data)

    def confirmed(self, kind: str, source_id: str) -> str | None:
        entry = self.data["ledger"].get(f"{kind}:{source_id}")
        if not entry:
            return None
        if entry["status"] != "confirmed":
            raise MigrationStateError("Uncertain write; needs_review, not retried")
        return entry["target_id"]

    def stage(self, name: str):
        previous = self.data.get("stage")
        if previous and previous != name:
            self.data["stages"][previous] = "completed"
        self.data["stage"] = name
        self.data["stages"][name] = "running"
        self.save()

    def write(self, kind: str, source_id: str, request: Any, operation, *, result_key="id"):
        key = f"{kind}:{source_id}"
        digest = fingerprint(request)
        entry = self.data["ledger"].get(key)
        if entry:
            if entry["request_fingerprint"] != digest:
                raise MigrationStateError(f"Request changed for {key}; needs manual review")
            if entry["status"] == "confirmed":
                return {result_key: entry["target_id"]}
            raise MigrationStateError(f"Uncertain write {key}; needs_review, no safe remote correlation ID; not retried")
        entry = {"kind": kind, "source_id": str(source_id), "status": "pending", "request_fingerprint": digest, "target_id": None}
        self.data["ledger"][key] = entry
        self.save()  # Must succeed before the remote write.
        try:
            result = operation()
            target_id = result.get(result_key)
            if not target_id or str(target_id) == "None":
                raise MigrationStateError("Write returned no authoritative target identifier")
            entry.update(status="confirmed", target_id=str(target_id))
            self.save()
            return result
        except BaseException:
            entry["status"] = "needs_review"
            self.data["status"] = "needs_review"
            self.save()
            raise
