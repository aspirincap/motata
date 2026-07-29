from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


AUTO_START = "<!-- HANDOFF:AUTO:START -->"
AUTO_END = "<!-- HANDOFF:AUTO:END -->"
DEFAULT_HANDOFF_NAME = "RECENT_REFACTOR_HANDOFF.md"
DEFAULT_MAX_FILES = 12
TRACKED_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".sh",
}
IGNORED_DIR_NAMES = {
    ".git",
    ".runtime",
    ".venv",
    ".vendor",
    "__pycache__",
    "build",
    "dist",
    "tmp",
    "meta_exports",
    "motata_cli.egg-info",
}


def is_ignored_part(part: str) -> bool:
    return part in IGNORED_DIR_NAMES or part.startswith(".venv")


@dataclass(frozen=True)
class TrackedFile:
    relative_path: str
    modified_at: datetime


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parent.parent


def should_track_file(path: Path, *, root: Path, handoff_path: Path) -> bool:
    if path == handoff_path:
        return False
    if not path.is_file():
        return False
    if path.suffix.lower() not in TRACKED_SUFFIXES:
        return False
    relative = path.relative_to(root)
    if any(is_ignored_part(part) for part in relative.parts):
        return False
    return True


def iter_tracked_files(root: Path, *, handoff_path: Path) -> Iterable[TrackedFile]:
    for path in root.rglob("*"):
        if not should_track_file(path, root=root, handoff_path=handoff_path):
            continue
        stat = path.stat()
        yield TrackedFile(
            relative_path=path.relative_to(root).as_posix(),
            modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
        )


def collect_recent_files(root: Path, *, handoff_path: Path, limit: int) -> list[TrackedFile]:
    files = sorted(
        iter_tracked_files(root, handoff_path=handoff_path),
        key=lambda item: (item.modified_at.timestamp(), item.relative_path),
        reverse=True,
    )
    return files[:limit]


def build_auto_section(
    *,
    root: Path,
    handoff_path: Path,
    note: str | None,
    max_files: int,
    now: datetime | None = None,
) -> str:
    current_time = (now or datetime.now().astimezone()).astimezone()
    recent_files = collect_recent_files(root, handoff_path=handoff_path, limit=max_files)

    lines = [
        AUTO_START,
        "## 自动维护区",
        "",
        "这一段由脚本自动维护。",
        f"最近同步时间：{current_time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "",
        "每次仓库发生实际变更后，请立即运行：",
        "",
        "```bash",
        'motata-handoff --note "本次变更摘要"',
        "```",
        "",
        "最近触达文件：",
    ]

    if recent_files:
        lines.append("")
        for item in recent_files:
            lines.append(f"- `{item.relative_path}`")
            lines.append(f"  - 修改时间：{item.modified_at.strftime('%Y-%m-%d %H:%M:%S %z')}")
    else:
        lines.extend(["", "- 暂无可跟踪文件"])

    lines.extend(["", f"本次变更备注：{note.strip() if note and note.strip() else '未提供'}", AUTO_END])
    return "\n".join(lines)


def sync_handoff_document(
    *,
    root: Path,
    handoff_path: Path,
    note: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    now: datetime | None = None,
) -> str:
    auto_section = build_auto_section(
        root=root,
        handoff_path=handoff_path,
        note=note,
        max_files=max_files,
        now=now,
    )

    original = handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else ""
    if AUTO_START in original and AUTO_END in original:
        start_index = original.index(AUTO_START)
        end_index = original.index(AUTO_END) + len(AUTO_END)
        updated = original[:start_index].rstrip()
        if updated:
            updated += "\n\n"
        updated += auto_section
        tail = original[end_index:].lstrip()
        if tail:
            updated += "\n\n" + tail
    else:
        body = original.lstrip()
        updated = auto_section if not body else f"{auto_section}\n\n{body}"

    handoff_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return updated


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="motata-handoff",
        description="Sync the repo handoff markdown with recent changed files and a short note.",
    )
    parser.add_argument("--root", default=str(repo_root_from_module()))
    parser.add_argument("--handoff-file", default=DEFAULT_HANDOFF_NAME)
    parser.add_argument("--note", help="Short summary of the latest important change.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    handoff_arg = Path(args.handoff_file)
    handoff_path = handoff_arg if handoff_arg.is_absolute() else root / handoff_arg
    sync_handoff_document(
        root=root,
        handoff_path=handoff_path,
        note=args.note,
        max_files=max(1, int(args.max_files)),
    )
    print(handoff_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
