"""Local configuration storage, independent of platform commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from motata_cli.common.utils import env_first, load_json_file, normalize_account_id, write_json_file

CACHE_DIR = Path(env_first("MOTATA_HOME", default=str(Path.home() / ".motata"))).expanduser()
CONFIG_PATH = CACHE_DIR / "config.json"
JOBS_DIR = CACHE_DIR / "jobs"


def ensure_dirs(*, cache_dir: Path | None = None, jobs_dir: Path | None = None) -> None:
    (cache_dir if cache_dir is not None else CACHE_DIR).mkdir(parents=True, exist_ok=True)
    (jobs_dir if jobs_dir is not None else JOBS_DIR).mkdir(parents=True, exist_ok=True)


def load_config(*, config_path: Path | None = None, cache_dir: Path | None = None, jobs_dir: Path | None = None) -> dict[str, Any]:
    ensure_dirs(cache_dir=cache_dir, jobs_dir=jobs_dir)
    return load_json_file(config_path if config_path is not None else CONFIG_PATH, default={"values": {}, "account_aliases": {}})


def save_config(payload: dict[str, Any], *, config_path: Path | None = None, cache_dir: Path | None = None, jobs_dir: Path | None = None) -> None:
    ensure_dirs(cache_dir=cache_dir, jobs_dir=jobs_dir)
    write_json_file(config_path if config_path is not None else CONFIG_PATH, payload)


def resolve_account_ref(account_ref: str | None, *, config_loader: Callable[[], dict[str, Any]] | None = None) -> str | None:
    loader = config_loader or load_config
    if not account_ref:
        config = loader()
        default_account = (config.get("values") or {}).get("default_account")
        if default_account:
            return normalize_account_id(default_account)
        return None
    raw = str(account_ref).strip()
    if raw.startswith("act_") or raw.isdigit():
        return normalize_account_id(raw)
    config = loader()
    aliases = config.get("account_aliases") or {}
    mapped = aliases.get(raw)
    if isinstance(mapped, dict):
        mapped = mapped.get("account_id")
    if mapped:
        return normalize_account_id(mapped)
    return normalize_account_id(raw)
