from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urljoin

from motata_cli import __version__

SKILLS_SOURCE_DEFAULT = "https://skill.motata.one"
NPM_PACKAGE_NAME = "motata"
PYPI_PACKAGE_NAME = "motata-cli"
COMPATIBILITY_FILE = Path(__file__).with_name("skills_compatibility.json")


@dataclass
class CommandResult:
    ok: bool
    command: list[str]
    code: int
    stdout: str
    stderr: str


def _http_headers() -> dict[str, str]:
    return {
        "User-Agent": f"motata-cli/{__version__}",
        "Accept": "application/json",
    }


def _run(command: list[str]) -> CommandResult:
    completed = subprocess.run(command, capture_output=True, text=True)
    return CommandResult(
        ok=completed.returncode == 0,
        command=command,
        code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def detect_install_method(*, root: Path | None = None, python_executable: str | None = None) -> str:
    root_path = (root or package_root()).resolve()
    python_path = Path(python_executable or sys.executable).resolve()
    npm_runtime = root_path / ".runtime" / "venv"
    if (root_path / "package.json").exists():
        if npm_runtime.exists() and _relative_to(python_path, npm_runtime):
            return "npm"
        return "source"
    return "pip"


def skills_stamp_path() -> Path:
    return Path.home() / ".motata" / "skills.stamp.json"


def load_skills_stamp() -> dict[str, Any] | None:
    path = skills_stamp_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def skills_stamp_is_current(stamp: dict[str, Any] | None, *, cli_version: str, skills_source: str) -> bool:
    if not isinstance(stamp, dict):
        return False
    return (
        str(stamp.get("cli_version") or "") == cli_version
        and str(stamp.get("skills_source") or "") == skills_source
    )


def load_compatibility_manifest() -> dict[str, Any]:
    try:
        return json.loads(COMPATIBILITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"default_skills_source": SKILLS_SOURCE_DEFAULT, "releases": []}


def compatibility_manifest_url(skills_source: str) -> str | None:
    source = str(skills_source or "").strip()
    if not source.startswith(("http://", "https://")):
        return None
    return urljoin(f"{source.rstrip('/')}/", ".well-known/motata/skills-compatibility.json")


def fetch_remote_compatibility_manifest(skills_source: str) -> dict[str, Any] | None:
    url = compatibility_manifest_url(skills_source)
    if not url:
        return None
    try:
        req = request.Request(url, headers=_http_headers())
        with request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_effective_compatibility_manifest(*, skills_source: str, prefer_remote: bool = True) -> tuple[dict[str, Any], str]:
    if prefer_remote:
        remote = fetch_remote_compatibility_manifest(skills_source)
        if remote is not None:
            return remote, "remote"
    return load_compatibility_manifest(), "local"


def _release_from_manifest(manifest: dict[str, Any], *, cli_version: str, skills_source: str) -> dict[str, Any] | None:
    releases = manifest.get("releases") or []
    for release in releases:
        if (
            str(release.get("cli_version") or "") == cli_version
            and str(release.get("skills_source") or "") == skills_source
        ):
            return release
    return None


def compatibility_release_for(
    cli_version: str,
    *,
    skills_source: str,
    prefer_remote: bool = True,
) -> dict[str, Any] | None:
    manifest, origin = load_effective_compatibility_manifest(
        skills_source=skills_source,
        prefer_remote=prefer_remote,
    )
    release = _release_from_manifest(
        manifest,
        cli_version=cli_version,
        skills_source=skills_source,
    )
    if release is not None:
        return release
    if prefer_remote and origin == "remote":
        return _release_from_manifest(
            load_compatibility_manifest(),
            cli_version=cli_version,
            skills_source=skills_source,
        )
    return None


def write_skills_stamp(*, cli_version: str, skills_source: str) -> Path:
    path = skills_stamp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    release = compatibility_release_for(cli_version, skills_source=skills_source) or {}
    payload = {
        "cli_version": cli_version,
        "skills_source": skills_source,
        "bundle_id": release.get("bundle_id"),
        "required_skills": release.get("required_skills") or [],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _trim_output(value: str, *, limit: int = 4000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def get_latest_npm_version(package_name: str = NPM_PACKAGE_NAME) -> str | None:
    if shutil.which("npm") is None:
        return None
    result = _run(["npm", "view", package_name, "version"])
    if not result.ok:
        return None
    version = (result.stdout or "").strip()
    return version or None


def get_latest_pypi_version(package_name: str = PYPI_PACKAGE_NAME) -> str | None:
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    info = payload.get("info") or {}
    version = str(info.get("version") or "").strip()
    return version or None


def _build_cli_update_command(*, install_method: str, force: bool) -> list[str] | None:
    if install_method == "npm":
        command = ["npm", "install", "-g", f"{NPM_PACKAGE_NAME}@latest"]
        if force:
            command.append("--force")
        return command
    if install_method == "pip":
        command = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if force:
            command.append("--force-reinstall")
        command.append(PYPI_PACKAGE_NAME)
        return command
    return None


def perform_cli_update(*, install_method: str, force: bool) -> dict[str, Any]:
    latest_version = get_latest_npm_version() if install_method == "npm" else get_latest_pypi_version()
    if install_method == "source":
        return {
            "attempted": False,
            "status": "skipped",
            "install_method": install_method,
            "current_version": __version__,
            "target_version": latest_version or __version__,
            "message": "Source/editable checkout detected; skipping package self-update.",
        }

    command = _build_cli_update_command(install_method=install_method, force=force)
    if command is None:
        return {
            "attempted": False,
            "status": "unsupported",
            "install_method": install_method,
            "current_version": __version__,
            "target_version": latest_version or __version__,
            "message": f"Unsupported install method: {install_method}",
        }

    result = _run(command)
    status = "updated" if result.ok else "failed"
    return {
        "attempted": True,
        "status": status,
        "install_method": install_method,
        "current_version": __version__,
        "target_version": latest_version or __version__,
        "command": command,
        "stdout": _trim_output(result.stdout),
        "stderr": _trim_output(result.stderr),
        "message": (
            f"CLI updated via {install_method}"
            if result.ok
            else f"CLI update failed via {install_method}"
        ),
    }


def build_skills_sync_command(*, skills_source: str) -> list[str]:
    return [
        "npx",
        "-y",
        "skills",
        "add",
        skills_source,
        "--skill",
        "*",
        "-g",
        "-y",
    ]


def perform_skills_sync(*, skills_source: str, cli_version: str) -> dict[str, Any]:
    if shutil.which("npx") is None:
        return {
            "attempted": False,
            "status": "failed",
            "skills_source": skills_source,
            "message": "npx is required to sync Motata skills, but it was not found on PATH.",
        }

    command = build_skills_sync_command(skills_source=skills_source)
    result = _run(command)
    payload: dict[str, Any] = {
        "attempted": True,
        "status": "synced" if result.ok else "failed",
        "skills_source": skills_source,
        "command": command,
        "stdout": _trim_output(result.stdout),
        "stderr": _trim_output(result.stderr),
    }
    if result.ok:
        payload["stamp_path"] = str(write_skills_stamp(cli_version=cli_version, skills_source=skills_source))
        payload["message"] = "Motata skills synced."
    else:
        payload["message"] = "Motata skills sync failed."
    return payload


def list_installed_global_skills() -> list[dict[str, Any]] | None:
    if shutil.which("npx") is None:
        return None
    result = _run(["npx", "-y", "skills", "ls", "-g", "--json"])
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def build_skills_registry_status(*, cli_version: str, skills_source: str) -> dict[str, Any]:
    release = compatibility_release_for(cli_version, skills_source=skills_source) or {}
    required_skills = [str(item) for item in (release.get("required_skills") or []) if str(item).strip()]
    installed = list_installed_global_skills() or []
    installed_names = {str(item.get("name") or "") for item in installed}
    installed_required = [name for name in required_skills if name in installed_names]
    missing_required = [name for name in required_skills if name not in installed_names]
    return {
        "bundle_id": release.get("bundle_id"),
        "required_skills": required_skills,
        "installed_required_skills": installed_required,
        "missing_required_skills": missing_required,
        "compatible": not missing_required if required_skills else None,
    }


def build_update_status(*, skills_source: str) -> dict[str, Any]:
    install_method = detect_install_method()
    stamp = load_skills_stamp()
    latest_version = get_latest_npm_version() if install_method in {"npm", "source"} else get_latest_pypi_version()
    manifest, manifest_origin = load_effective_compatibility_manifest(skills_source=skills_source)
    registry_status = build_skills_registry_status(cli_version=__version__, skills_source=skills_source)
    return {
        "ok": True,
        "command": "check",
        "action": "check",
        "current_version": __version__,
        "latest_version": latest_version or __version__,
        "install_method": install_method,
        "skills_source": skills_source,
        "skills_stamp": stamp,
        "skills_in_sync": skills_stamp_is_current(stamp, cli_version=__version__, skills_source=skills_source),
        "compatibility_manifest_origin": manifest_origin,
        "compatibility_manifest_url": compatibility_manifest_url(skills_source),
        "skills_registry_status": registry_status,
    }


def build_skills_drift_notice(
    *,
    cli_version: str,
    skills_source: str,
    stamp: dict[str, Any] | None,
    command_name: str | None = None,
) -> str | None:
    if command_name == "update":
        return None
    if skills_stamp_is_current(stamp, cli_version=cli_version, skills_source=skills_source):
        return None
    if not stamp:
        return (
            f"[motata] Motata skills have not been synced for CLI {cli_version}. "
            f"Run `motata update --skills-only` to install skills from {skills_source}."
        )
    synced_version = str(stamp.get("cli_version") or "unknown")
    synced_source = str(stamp.get("skills_source") or "unknown")
    return (
        f"[motata] Motata skills look out of sync "
        f"(cli={cli_version}, skills={synced_version}, source={synced_source}). "
        f"Run `motata update --skills-only` to refresh from {skills_source}."
    )


def maybe_emit_skills_drift_notice(
    *,
    command_name: str | None,
    cli_version: str = __version__,
    skills_source: str = SKILLS_SOURCE_DEFAULT,
    stream: Any = sys.stderr,
) -> None:
    if os.getenv("MOTATA_SUPPRESS_SKILLS_NOTICE") == "1":
        return
    if stream is None or not hasattr(stream, "write"):
        return
    if hasattr(stream, "isatty") and not stream.isatty():
        return
    notice = build_skills_drift_notice(
        cli_version=cli_version,
        skills_source=skills_source,
        stamp=load_skills_stamp(),
        command_name=command_name,
    )
    if notice:
        print(notice, file=stream)
        registry_status = build_skills_registry_status(cli_version=cli_version, skills_source=skills_source)
        missing = registry_status.get("missing_required_skills") or []
        if missing:
            print(
                f"[motata] Missing required Motata skills: {', '.join(missing)}",
                file=stream,
            )


def summarize_update_result(
    *,
    install_method: str,
    cli_result: dict[str, Any] | None,
    skills_result: dict[str, Any] | None,
    current_version: str,
    skills_source: str,
) -> dict[str, Any]:
    action = "noop"
    message = "No update work was performed."
    skills_action = "skipped" if skills_result is None else str(skills_result.get("status") or "unknown")

    if cli_result and cli_result.get("status") == "updated":
        action = "updated"
        message = f"Motata CLI updated via {install_method}."
    elif cli_result and cli_result.get("status") == "skipped":
        action = "skipped"
        message = str(cli_result.get("message") or "Motata CLI update skipped.")
    elif cli_result and cli_result.get("status") == "failed":
        action = "failed"
        message = str(cli_result.get("message") or "Motata CLI update failed.")

    if skills_result and skills_result.get("status") == "synced":
        if action in {"noop", "skipped"}:
            action = "skills_synced"
        message = "Motata skills synced."
        if cli_result and cli_result.get("status") == "updated":
            message = "Motata CLI updated and skills synced."
    elif skills_result and skills_result.get("status") == "failed":
        action = "failed"
        message = str(skills_result.get("message") or "Motata skills sync failed.")

    return {
        "action": action,
        "message": message,
        "skills_action": skills_action,
        "current_version": current_version,
        "skills_source": skills_source,
    }


def command_update(args: argparse.Namespace) -> None:
    skills_source = str(args.skills_source or SKILLS_SOURCE_DEFAULT).strip() or SKILLS_SOURCE_DEFAULT
    if args.check:
        print(json.dumps(build_update_status(skills_source=skills_source), ensure_ascii=False, indent=2))
        return

    install_method = detect_install_method()
    cli_result: dict[str, Any] | None = None
    skills_result: dict[str, Any] | None = None
    effective_version = __version__

    if not args.skills_only:
        cli_result = perform_cli_update(install_method=install_method, force=bool(args.force))
        target_version = str((cli_result or {}).get("target_version") or "").strip()
        if target_version:
            effective_version = target_version

    if not args.cli_only:
        skills_result = perform_skills_sync(skills_source=skills_source, cli_version=effective_version)

    ok = True
    if cli_result and cli_result.get("status") == "failed":
        ok = False
    if skills_result and skills_result.get("status") == "failed":
        ok = False

    summary = summarize_update_result(
        install_method=install_method,
        cli_result=cli_result,
        skills_result=skills_result,
        current_version=__version__,
        skills_source=skills_source,
    )

    print(
        json.dumps(
            {
                "ok": ok,
                "command": "update",
                "install_method": install_method,
                **summary,
                "cli_update": cli_result,
                "skills_sync": skills_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def register_update_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("update", help="Update Motata CLI and sync Motata skills")
    parser.add_argument("--check", action="store_true", help="Only check update and skills sync status")
    parser.add_argument("--force", action="store_true", help="Force reinstall the CLI package before syncing skills")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--cli-only", action="store_true", help="Update only the CLI package")
    scope.add_argument("--skills-only", action="store_true", help="Sync only Motata skills")
    parser.add_argument(
        "--skills-source",
        default=SKILLS_SOURCE_DEFAULT,
        help=f"Motata skills registry source (default: {SKILLS_SOURCE_DEFAULT})",
    )
    parser.set_defaults(func=command_update)
