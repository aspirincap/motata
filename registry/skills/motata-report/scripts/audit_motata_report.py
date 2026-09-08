#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"access_token", re.I),
    re.compile(r"authorization\s*[:=]", re.I),
    re.compile(r"x-api-key\s*[:=]", re.I),
    re.compile(r"\bEAAG[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bxmp[_-]?token[_-]?info[_-]?key\s*[:=]"),
]


def load_json(path: Path) -> tuple[bool, Any | str]:
    try:
        return True, json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - diagnostic script
        return False, str(exc)


def count_rows(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 1 if data else 0
    if isinstance(data.get("rows"), list):
        return len(data["rows"])
    if isinstance(data.get("data"), dict):
        rows = data["data"].get("list") or data["data"].get("rows") or []
        return len(rows) if isinstance(rows, list) else 0
    if isinstance(data.get("sections"), dict):
        total = 0
        for section in data["sections"].values():
            if isinstance(section, dict):
                rows = section.get("segments") or section.get("rows") or []
            else:
                rows = section or []
            if isinstance(rows, list):
                total += len(rows)
        return total
    if isinstance(data.get("accounts"), list):
        return len(data["accounts"])
    return 1 if data else 0


def scan_secrets(text: str) -> list[str]:
    return [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)]


def audit(run_dir: Path | None, html_path: Path) -> dict[str, Any]:
    html = html_path.read_text(encoding="utf-8")
    json_files: list[dict[str, Any]] = []
    if run_dir and run_dir.exists():
        for path in sorted(run_dir.glob("*.json")):
            ok, payload = load_json(path)
            json_files.append(
                {
                    "file": str(path),
                    "valid_json": ok,
                    "row_count": count_rows(payload) if ok else 0,
                    "error": None if ok else payload,
                }
            )

    checks = {
        "html_exists": html_path.exists(),
        "json_files_valid": all(item["valid_json"] for item in json_files),
        "has_preview_columns": "<th>Preview</th>" in html and "preview-action" in html,
        "has_preview_images_or_unavailable": "preview-img" in html or "Unavailable" in html,
        "has_url_wrapping_css": "overflow-wrap:anywhere" in html and "word-break:break-word" in html,
        "has_quality_section": "数据质量" in html or "Data quality" in html or "Data Quality" in html,
        "has_scope": "Scope:" in html,
        "secret_absent": not scan_secrets(html),
    }
    required = [
        "html_exists",
        "json_files_valid",
        "has_preview_columns",
        "has_url_wrapping_css",
        "has_scope",
        "secret_absent",
    ]
    return {
        "html": str(html_path),
        "run_dir": str(run_dir) if run_dir else None,
        "checks": checks,
        "required_passed": all(checks[name] for name in required),
        "json_files": json_files,
        "secret_patterns_found": scan_secrets(html),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Motata HTML report.")
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    result = audit(args.run_dir, args.html)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["required_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
