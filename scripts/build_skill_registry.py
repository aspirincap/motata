#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = REPO_ROOT / "cloudflare" / "motata-skills-registry"
PUBLIC_ROOT = REGISTRY_ROOT / "public"
AGENT_SKILLS_ROOT = PUBLIC_ROOT / ".well-known" / "agent-skills"
MOTATA_WELL_KNOWN_ROOT = PUBLIC_ROOT / ".well-known" / "motata"
SKILLS_SOURCE = "https://skill.motata.one"
CANONICAL_SKILLS_ROOT = REPO_ROOT / "registry" / "skills"
ALLOWED_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml"}
FORBIDDEN_NAMES = {"token.txt", ".env", ".npmrc", "credentials.json", "auth.json"}
FORBIDDEN_DIRECTORIES = {"runtime", "outputs", "node_modules"}
SKILL_NAMES = [
    "motata-starter",
    "motata-report",
    "motata-ad-ops",
    "motata-token",
    "motata-vertical-analyst",
]


def _read_frontmatter_text(skill_dir: Path) -> str:
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return ""
    end = content.find("\n---\n", 4)
    if end == -1:
        return ""
    return content[4:end]


def _frontmatter_value(frontmatter: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.*)$", frontmatter, flags=re.M)
    if not match:
        return ""
    value = match.group(1).strip()
    if value in {"|", ">"}:
        lines = frontmatter.splitlines()
        start_index = match.string[: match.start()].count("\n") + 1
        block_lines: list[str] = []
        for line in lines[start_index:]:
            if not line.startswith((" ", "\t")):
                break
            block_lines.append(line)
        return textwrap.dedent("\n".join(block_lines)).strip()
    return value.strip('"').strip("'")


def resolve_skill_source(skill_name: str) -> Path:
    if skill_name not in SKILL_NAMES:
        raise ValueError(f"Unknown registry skill: {skill_name}")
    source = CANONICAL_SKILLS_ROOT / skill_name
    if source.is_symlink() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"Missing canonical skill source: {source}")
    if not source.resolve().is_relative_to(CANONICAL_SKILLS_ROOT.resolve()):
        raise ValueError("Skill source escapes canonical registry root")
    return source


def should_ignore(path: Path) -> bool:
    if path.name in {".DS_Store"}:
        return True
    if path.name == "__pycache__":
        return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    return False


def publishable_files(src: Path) -> list[Path]:
    """Allow only reviewed source formats; never follow symlinks or publish secrets."""
    files = []
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(should_ignore(part) for part in rel.parents) or should_ignore(item):
            continue
        if item.is_symlink():
            raise ValueError(f"Symlinks are not publishable: {rel}")
        if any(part.lower() in FORBIDDEN_NAMES or part.startswith(".") for part in rel.parts):
            raise ValueError(f"Unreviewed registry file: {rel}")
        if any(part.lower() in FORBIDDEN_DIRECTORIES for part in rel.parts[:-1]) or (
            item.is_dir() and item.name.lower() in FORBIDDEN_DIRECTORIES
        ):
            raise ValueError(f"Unreviewed registry directory: {rel}")
        if item.is_dir():
            continue
        if item.suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"Unreviewed registry file: {rel}")
        text = item.read_text(encoding="utf-8")
        if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)", text):
            raise ValueError(f"Potential secret in registry file: {rel}")
        files.append(item)
    return files


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in publishable_files(src):
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def list_skill_files(skill_dir: Path) -> list[str]:
    return [item.relative_to(skill_dir).as_posix() for item in publishable_files(skill_dir)]


def build_agent_skills_index() -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    for name in SKILL_NAMES:
        source = resolve_skill_source(name)
        frontmatter = _read_frontmatter_text(source)
        skills.append(
            {
                "name": name,
                "description": _frontmatter_value(frontmatter, "description"),
                "files": list_skill_files(source),
                "sha256": {
                    item.relative_to(source).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                    for item in publishable_files(source)
                },
            }
        )
    return {"skills": skills}


def load_cli_compatibility() -> dict[str, Any]:
    path = REPO_ROOT / "motata_cli" / "skills_compatibility.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_remote_compatibility_manifest() -> dict[str, Any]:
    compatibility = load_cli_compatibility()
    return {
        "registry": {
            "name": "motata-skills",
            "skills_source": SKILLS_SOURCE,
            "agent_skills_index_url": urljoin(f"{SKILLS_SOURCE}/", ".well-known/agent-skills/index.json"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        **compatibility,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_headers(path: Path) -> None:
    path.write_text(
        (
            "/.well-known/agent-skills/*\n"
            "  Cache-Control: public, max-age=300\n"
            "  Access-Control-Allow-Origin: *\n\n"
            "/.well-known/motata/*.json\n"
            "  Cache-Control: public, max-age=300\n"
            "  Access-Control-Allow-Origin: *\n"
            "  Content-Type: application/json; charset=utf-8\n"
        ),
        encoding="utf-8",
    )


def build_registry() -> dict[str, Any]:
    # Validate every source before mutating the build output.
    index_payload = build_agent_skills_index()
    compatibility_payload = build_remote_compatibility_manifest()
    if AGENT_SKILLS_ROOT.exists():
        shutil.rmtree(AGENT_SKILLS_ROOT)
    AGENT_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    MOTATA_WELL_KNOWN_ROOT.mkdir(parents=True, exist_ok=True)

    for name in SKILL_NAMES:
        copy_tree(resolve_skill_source(name), AGENT_SKILLS_ROOT / name)

    write_json(AGENT_SKILLS_ROOT / "index.json", index_payload)

    write_json(MOTATA_WELL_KNOWN_ROOT / "skills-compatibility.json", compatibility_payload)

    (PUBLIC_ROOT / ".nojekyll").write_text("", encoding="utf-8")
    write_headers(PUBLIC_ROOT / "_headers")
    return {
        "public_root": str(PUBLIC_ROOT),
        "skills_index": str(AGENT_SKILLS_ROOT / "index.json"),
        "compatibility_manifest": str(MOTATA_WELL_KNOWN_ROOT / "skills-compatibility.json"),
        "skill_count": len(SKILL_NAMES),
    }


if __name__ == "__main__":
    print(json.dumps(build_registry(), ensure_ascii=False, indent=2))
