from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._") or "item"


def report_table(headers: list[str], rows: list[list[str]], *, class_name: str = "") -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "\n".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    table_class = f' class="{esc(class_name)}"' if class_name else ""
    return f'<div class="table-wrap"><table{table_class}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def preview_cell(
    image_url: str = "",
    preview_url: str = "",
    *,
    alt: str = "preview",
    unavailable_text: str = "Unavailable",
    local_image_url: str = "",
) -> str:
    cover = str(local_image_url or image_url or "").strip()
    preview = str(preview_url or image_url or local_image_url or "").strip()
    if not cover and not preview:
        return (
            '<div class="preview-cell">'
            f'<div class="preview-img preview-placeholder">{esc(unavailable_text)}</div>'
            "</div>"
        )
    image = (
        f'<img class="preview-img" src="{esc(cover)}" alt="{esc(alt)}" loading="lazy" referrerpolicy="no-referrer" />'
        if cover
        else '<div class="preview-img preview-placeholder">Preview</div>'
    )
    action = (
        f'<a class="preview-action" href="{esc(preview)}" target="_blank" rel="noreferrer">打开预览 / Preview</a>'
        if preview
        else ""
    )
    return f'<div class="preview-cell">{image}{action}</div>'


def report_table_css() -> str:
    return """
    .table-wrap { overflow:auto; border:1px solid var(--line); border-radius:8px; background:white; max-width:100%; }
    table { width:100%; border-collapse:collapse; min-width:1040px; table-layout:auto; }
    th, td { padding:10px 11px; border-bottom:1px solid var(--line); vertical-align:top; font-size:13px; overflow-wrap:anywhere; word-break:break-word; }
    th { background:#f0f3f9; text-align:left; color:#39455f; position:sticky; top:0; z-index:1; }
    tr:last-child td { border-bottom:none; }
    .table-wrap a { overflow-wrap:anywhere; word-break:break-all; }
    .preview-cell { position:relative; width:92px; height:124px; }
    .preview-img { width:92px; height:124px; object-fit:cover; border-radius:6px; border:1px solid var(--line); background:#f2f4f8; display:block; }
    .preview-placeholder { display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:12px; }
    .preview-action { position:absolute; left:7px; right:7px; bottom:8px; display:flex; align-items:center; justify-content:center; min-height:30px; padding:5px 7px; border-radius:6px; background:rgba(15,31,61,.92); color:white; font-size:11px; font-weight:700; text-align:center; opacity:0; transform:translateY(4px); transition:opacity .16s ease, transform .16s ease; }
    .preview-cell:hover .preview-action, .preview-cell:focus-within .preview-action { opacity:1; transform:translateY(0); }
    .short { max-width:360px; overflow-wrap:anywhere; word-break:break-word; white-space:normal; }
    .short a { overflow-wrap:anywhere; word-break:break-all; }
    .campaign-table { min-width:1040px; }
    .product-table { min-width:1060px; }
    .creative-table { min-width:1980px; }
    .creative-table th:nth-child(1), .creative-table td:nth-child(1) { width:118px; min-width:118px; }
    .creative-table th:nth-child(2), .creative-table td:nth-child(2) { width:180px; min-width:180px; }
    .creative-table th:nth-child(3), .creative-table td:nth-child(3) { width:260px; min-width:260px; }
    .creative-table th:nth-child(n+4), .creative-table td:nth-child(n+4) { min-width:96px; white-space:nowrap; }
    """


def cache_remote_images(
    run_dir: Path,
    items: dict[str, str],
    *,
    relative_dir: str = "assets/previews",
    extension: str = "jpg",
    timeout: int = 12,
    max_bytes: int = 2_000_000,
) -> dict[str, str]:
    cache_dir = run_dir / relative_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached: dict[str, str] = {}
    for key, url in items.items():
        source_url = str(url or "").strip()
        if not source_url:
            continue
        out_path = cache_dir / f"{slug(key)}.{extension}"
        if not out_path.exists() or out_path.stat().st_size < 512:
            try:
                request = urllib.request.Request(source_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read(max_bytes)
                if data:
                    out_path.write_bytes(data)
            except (OSError, urllib.error.URLError, TimeoutError):
                continue
        if out_path.exists() and out_path.stat().st_size >= 512:
            cached[str(key)] = str(out_path.relative_to(run_dir))
    return cached
