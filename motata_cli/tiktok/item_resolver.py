from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
VIDEO_URL_TEMPLATE = "https://www.tiktok.com/@motata/video/{item_id}"


@dataclass
class TikTokItem:
    item_id: str
    input_url: str
    final_url: str
    title: str | None = None
    handle_name: str | None = None
    user_name: str | None = None
    avatar_url: str | None = None
    preview_url: str | None = None
    avatar_path: str | None = None
    preview_path: str | None = None
    asset_errors: list[str] = field(default_factory=list)
    real_video_url: str | None = None
    author_id: str | None = None
    sec_uid: str | None = None
    upload_timestamp: int | None = None
    upload_datetime: str | None = None
    modify_timestamp: int | None = None
    modify_datetime: str | None = None
    description: str | None = None
    source: str | None = None
    error: str | None = None


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self._script: dict[str, str] | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            self.meta.append(values)
        elif tag.lower() == "script":
            self._script = values
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script is not None:
            script = dict(self._script)
            script["text"] = "".join(self._script_chunks)
            self.scripts.append(script)
            self._script = None
            self._script_chunks = []


def item_id_from_value(value: str) -> str:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        match = re.search(r"/video/(\d+)", urlparse(value).path)
        if not match:
            raise ValueError(f"cannot find /video/{{item_id}} in {value}")
        return match.group(1)
    if not re.fullmatch(r"\d{6,}", value):
        raise ValueError(f"invalid TikTok item id: {value}")
    return value


def fetch_html(url: str, timeout: int) -> tuple[str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace"), response.geturl()


def fetch_bytes(url: str, timeout: int) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "User-Agent": DESKTOP_UA,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Referer": "https://www.tiktok.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def parse_head(document: str) -> HeadParser:
    parser = HeadParser()
    parser.feed(document)
    return parser


def meta_content(parser: HeadParser, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for item in parser.meta:
        key = (item.get("property") or item.get("name") or "").lower()
        if key in wanted and item.get("content"):
            return html.unescape(item["content"]).strip()
    return None


def parse_json_script(parser: HeadParser, script_id: str) -> Any:
    for script in parser.scripts:
        if script.get("id") == script_id and script.get("text"):
            return json.loads(script["text"])
    return None


def find_item_struct(universal_data: Any, item_id: str) -> dict[str, Any] | None:
    if not isinstance(universal_data, dict):
        return None
    scope = universal_data.get("__DEFAULT_SCOPE__")
    if not isinstance(scope, dict):
        return None
    detail = scope.get("webapp.video-detail")
    if isinstance(detail, dict):
        item = detail.get("itemInfo", {}).get("itemStruct")
        if isinstance(item, dict) and str(item.get("id") or "") == item_id:
            return item

    stack: list[Any] = [universal_data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if str(value.get("id") or value.get("awemeId") or "") == item_id:
                author = value.get("author")
                video = value.get("video")
                if isinstance(author, dict) and isinstance(video, dict):
                    return value
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None


def first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return html.unescape(value)
    if isinstance(value, list):
        for item in value:
            found = first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "uri", "UrlList", "urlList"):
            found = first_url(value.get(key))
            if found:
                return found
    return None


def all_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return [html.unescape(value)]
    if isinstance(value, list):
        for item in value:
            urls.extend(all_urls(item))
    elif isinstance(value, dict):
        for key in ("url", "uri", "UrlList", "urlList"):
            urls.extend(all_urls(value.get(key)))
    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def signed_cdn_fallback_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    hostname = parsed.netloc
    variants = [url]
    if hostname.startswith("p16-"):
        variants.append(parsed._replace(netloc="p19-" + hostname[4:]).geturl())
    elif hostname.startswith("p19-"):
        variants.append(parsed._replace(netloc="p16-" + hostname[4:]).geturl())
    elif hostname.startswith("p16-") is False and "p16-" in hostname:
        variants.append(parsed._replace(netloc=hostname.replace("p16-", "p19-", 1)).geturl())
    elif hostname.startswith("p19-") is False and "p19-" in hostname:
        variants.append(parsed._replace(netloc=hostname.replace("p19-", "p16-", 1)).geturl())
    deduped: list[str] = []
    for variant in variants:
        if variant not in deduped:
            deduped.append(variant)
    return deduped


def item_preview_url(item: dict[str, Any]) -> str | None:
    video = item.get("video")
    if not isinstance(video, dict):
        return None
    for key in ("originCover", "cover", "dynamicCover", "reflowCover"):
        url = first_url(video.get(key))
        if url:
            return url
    zoom_cover = video.get("zoomCover")
    if isinstance(zoom_cover, dict):
        for key in ("960", "720", "480", "240"):
            url = first_url(zoom_cover.get(key))
            if url:
                return url
    return None


def item_preview_urls(item: dict[str, Any]) -> list[str]:
    video = item.get("video")
    if not isinstance(video, dict):
        return []
    urls: list[str] = []
    for key in ("originCover", "cover", "dynamicCover", "reflowCover"):
        urls.extend(all_urls(video.get(key)))
    zoom_cover = video.get("zoomCover")
    if isinstance(zoom_cover, dict):
        for key in ("960", "720", "480", "240"):
            urls.extend(all_urls(zoom_cover.get(key)))
    expanded: list[str] = []
    for url in urls:
        expanded.extend(signed_cdn_fallback_urls(url))
    return list(dict.fromkeys(expanded))


def item_avatar_url(author: dict[str, Any]) -> str | None:
    for key in ("avatarLarger", "avatarMedium", "avatarThumb"):
        url = first_url(author.get(key))
        if url:
            return url
    return None


def item_avatar_urls(author: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("avatarLarger", "avatarMedium", "avatarThumb"):
        urls.extend(all_urls(author.get(key)))
    expanded: list[str] = []
    for url in urls:
        expanded.extend(signed_cdn_fallback_urls(url))
    return list(dict.fromkeys(expanded))


def int_timestamp(value: Any) -> int | None:
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def timestamp_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def first_post_modify_timestamp(item: dict[str, Any]) -> int | None:
    # Public TikTok SSR commonly exposes createTime, but not a post edit/update timestamp.
    for key in (
        "modifyTime",
        "modifiedTime",
        "updateTime",
        "updatedTime",
        "editTime",
        "lastModifyTime",
        "lastModifiedTime",
    ):
        timestamp = int_timestamp(item.get(key))
        if timestamp:
            return timestamp
    return None


def clean_post_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def item_post_text(item: dict[str, Any]) -> str | None:
    for key in ("desc", "description", "title", "caption"):
        text = clean_post_text(item.get(key))
        if text:
            return text

    contents = item.get("contents")
    if isinstance(contents, list):
        for content in contents:
            if not isinstance(content, dict):
                continue
            for key in ("desc", "description", "text", "title"):
                text = clean_post_text(content.get(key))
                if text:
                    return text

    stickers = item.get("stickersOnItem")
    if isinstance(stickers, list):
        sticker_texts: list[str] = []
        for sticker in stickers:
            if not isinstance(sticker, dict):
                continue
            values = sticker.get("stickerText")
            values = values if isinstance(values, list) else [values]
            for value in values:
                text = clean_post_text(value)
                if text:
                    sticker_texts.append(text)
        if sticker_texts:
            return " / ".join(dict.fromkeys(sticker_texts))

    share_meta = item.get("shareMeta")
    if isinstance(share_meta, dict):
        text = clean_post_text(share_meta.get("desc"))
        if text and " short video with " not in text:
            return text
    return None


def item_post_title(item: dict[str, Any], parser: HeadParser) -> str | None:
    for key in ("title", "desc"):
        text = clean_post_text(item.get(key))
        if text:
            return text
    share_meta = item.get("shareMeta")
    if isinstance(share_meta, dict):
        text = clean_post_text(share_meta.get("title"))
        if text and text.lower() not in {"tiktok", "tiktok - make your day"}:
            return text
    text = meta_content(parser, "og:title", "twitter:title")
    if text and text.lower() not in {"tiktok", "tiktok - make your day"}:
        return re.sub(r"\s+(?:on|·)\s+TikTok$", "", text).strip() or text
    return None


def extension_for_url(url: str, content_type: str) -> str:
    content_type_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }
    if content_type in content_type_map:
        return content_type_map[content_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".bin"


def download_asset(url: str, path_prefix: Path, timeout: int, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for index in range(attempts):
        try:
            content, content_type = fetch_bytes(url, timeout)
            path = path_prefix.with_suffix(extension_for_url(url, content_type))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return str(path)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if index + 1 < attempts:
                time.sleep(0.8 * (index + 1))
    raise RuntimeError(str(last_error))


def download_first_available(urls: list[str], path_prefix: Path, timeout: int) -> str:
    errors: list[str] = []
    for url in urls:
        try:
            return download_asset(url, path_prefix, timeout)
        except RuntimeError as exc:
            errors.append(f"{urlparse(url).netloc}: {exc}")
    raise RuntimeError("; ".join(errors))


def download_item_assets(item: TikTokItem, download_dir: Path, timeout: int) -> None:
    item_dir = download_dir / item.item_id
    if item.avatar_url:
        try:
            avatar_urls = item_avatar_urls(item._raw_author) if hasattr(item, "_raw_author") else [item.avatar_url]
            item.avatar_path = download_first_available(avatar_urls or [item.avatar_url], item_dir / "avatar", timeout)
        except RuntimeError as exc:
            item.asset_errors.append(f"avatar: {exc}")
    if item.preview_url:
        try:
            preview_urls = item_preview_urls(item._raw_item) if hasattr(item, "_raw_item") else [item.preview_url]
            item.preview_path = download_first_available(preview_urls or [item.preview_url], item_dir / "preview", timeout)
        except RuntimeError as exc:
            item.asset_errors.append(f"preview: {exc}")


def infer_from_meta(parser: HeadParser, item_id: str, input_url: str, final_url: str) -> TikTokItem:
    title = meta_content(parser, "og:title", "twitter:title")
    description = meta_content(parser, "description", "og:description", "twitter:description")
    image = meta_content(parser, "og:image", "twitter:image")
    og_url = meta_content(parser, "og:url") or final_url
    handle_name = None
    user_name = None

    if title:
        handle_name = re.sub(r"\s+(?:on|·)\s+TikTok$", "", title).strip() or None
        if handle_name and handle_name.lower() == "tiktok":
            handle_name = None

    if description:
        match = re.search(r"from\s+(.+?)\s+\(@([^)]+)\)", description)
        if not match:
            match = re.search(r"de\s+(.+?)\s+\(@([^)]+)\)", description)
        if match:
            handle_name = html.unescape(match.group(1)).strip()
            user_name = "@" + match.group(2).strip().lstrip("@")

    if not user_name:
        match = re.search(r"/@([^/]+)/video/", og_url)
        if match and match.group(1) != "motata":
            user_name = "@" + match.group(1)

    return TikTokItem(
        item_id=item_id,
        input_url=input_url,
        final_url=final_url,
        title=title,
        handle_name=handle_name,
        user_name=user_name,
        preview_url=image,
        real_video_url=og_url,
        description=description,
        source="meta",
    )


def resolve_item(
    item_id: str,
    *,
    timeout: int = 30,
    save_html_dir: Path | None = None,
    download_dir: Path | None = None,
) -> TikTokItem:
    input_url = VIDEO_URL_TEMPLATE.format(item_id=item_id)
    try:
        document, final_url = fetch_html(input_url, timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return TikTokItem(item_id=item_id, input_url=input_url, final_url=input_url, error=str(exc))

    if save_html_dir:
        save_html_dir.mkdir(parents=True, exist_ok=True)
        (save_html_dir / f"{item_id}.html").write_text(document, encoding="utf-8")

    parser = parse_head(document)
    try:
        universal = parse_json_script(parser, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
        item = find_item_struct(universal, item_id)
    except Exception:
        item = None

    if isinstance(item, dict):
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        unique_id = str(author.get("uniqueId") or "").strip()
        upload_timestamp = int_timestamp(item.get("createTime"))
        modify_timestamp = first_post_modify_timestamp(item)
        result = TikTokItem(
            item_id=item_id,
            input_url=input_url,
            final_url=final_url,
            title=item_post_title(item, parser),
            handle_name=str(author.get("nickname") or "").strip() or None,
            user_name=f"@{unique_id}" if unique_id else None,
            avatar_url=item_avatar_url(author),
            preview_url=item_preview_url(item) or meta_content(parser, "og:image", "twitter:image"),
            real_video_url=f"https://www.tiktok.com/@{unique_id}/video/{item_id}" if unique_id else final_url,
            author_id=str(author.get("id") or "").strip() or None,
            sec_uid=str(author.get("secUid") or "").strip() or None,
            upload_timestamp=upload_timestamp,
            upload_datetime=timestamp_iso(upload_timestamp),
            modify_timestamp=modify_timestamp,
            modify_datetime=timestamp_iso(modify_timestamp),
            description=item_post_text(item) or meta_content(parser, "description"),
            source="__UNIVERSAL_DATA_FOR_REHYDRATION__",
        )
        result._raw_author = author
        result._raw_item = item
    else:
        result = infer_from_meta(parser, item_id, input_url, final_url)

    if download_dir:
        download_item_assets(result, download_dir, timeout)
    for attr in ("_raw_author", "_raw_item"):
        if hasattr(result, attr):
            delattr(result, attr)
    return result


def resolve_items(
    item_ids: list[str],
    *,
    timeout: int = 30,
    save_html_dir: Path | None = None,
    download_dir: Path | None = None,
    workers: int = 1,
    include_timing: bool = False,
) -> list[dict[str, Any]]:
    normalized_item_ids = [item_id_from_value(item_id) for item_id in item_ids]

    def run(item_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        result = asdict(resolve_item(item_id, timeout=timeout, save_html_dir=save_html_dir, download_dir=download_dir))
        if include_timing:
            result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return result

    if workers <= 1 or len(normalized_item_ids) <= 1:
        return [run(item_id) for item_id in normalized_item_ids]

    by_item_id: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_item_id = {executor.submit(run, item_id): item_id for item_id in normalized_item_ids}
        for future in as_completed(future_to_item_id):
            by_item_id[future_to_item_id[future]] = future.result()
    return [by_item_id[item_id] for item_id in normalized_item_ids]
