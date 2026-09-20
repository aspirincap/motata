"""Creative media helpers independent of the CLI command layer."""
from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import requests

from motata_cli.common.errors import CliError
from motata_cli.common.utils import env_first, filter_empty
from motata_cli.meta.client import MetaClient
from motata_cli.meta.utils import ad_account_path, parse_meta_error_payload


def pick_link(creative: dict[str, Any]) -> str | None:
    oss = creative.get("object_story_spec") or {}
    video_data = oss.get("video_data") or {}
    cta = (video_data.get("call_to_action") or {}).get("value") or {}
    return (
        cta.get("link")
        or (oss.get("link_data") or {}).get("link")
        or (oss.get("template_data") or {}).get("link")
        or (((oss.get("photo_data") or {}).get("call_to_action") or {}).get("value") or {}).get("link")
        or creative.get("link_url")
        or (((creative.get("asset_feed_spec") or {}).get("link_urls") or [{}])[0].get("website_url"))
    )


def pick_creative_thumbnail_url(creative: dict[str, Any]) -> str | None:
    oss = creative.get("object_story_spec") or {}
    video_data = oss.get("video_data") or {}
    link_data = oss.get("link_data") or {}
    photo_data = oss.get("photo_data") or {}
    template_data = oss.get("template_data") or {}
    return (
        video_data.get("image_url")
        or photo_data.get("image_url")
        or (((photo_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))
        or creative.get("thumbnail_url")
        or creative.get("image_url")
        or (((template_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))
        or (((link_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))
    )


def pick_creative_text_parts(creative: dict[str, Any]) -> dict[str, str | None]:
    oss = creative.get("object_story_spec") or {}
    video_data = oss.get("video_data") or {}
    link_data = oss.get("link_data") or {}
    photo_data = oss.get("photo_data") or {}
    template_data = oss.get("template_data") or {}
    return {
        "message": video_data.get("message") or link_data.get("message") or photo_data.get("message") or template_data.get("message") or creative.get("body"),
        "headline": video_data.get("title") or link_data.get("name") or photo_data.get("name") or template_data.get("name") or creative.get("title"),
        "description": video_data.get("link_description") or link_data.get("description") or photo_data.get("caption") or template_data.get("description"),
        "call_to_action": (video_data.get("call_to_action") or {}).get("type") or (link_data.get("call_to_action") or {}).get("type") or (photo_data.get("call_to_action") or {}).get("type") or (template_data.get("call_to_action") or {}).get("type") or creative.get("call_to_action_type") or "SHOP_NOW",
    }


def detect_creative_migration_mode(
    creative: dict[str, Any], *, link_picker: Callable[[dict[str, Any]], str | None] | None = None,
) -> str:
    oss = creative.get("object_story_spec") or {}
    if (oss.get("video_data") or {}).get("video_id"):
        return "video"
    if creative.get("object_story_id"):
        return "existing_post"
    if (link_picker or pick_link)(creative):
        return "link"
    raise CliError(
        f"Unsupported creative format for migration: creative {creative.get('id')} "
        "has neither video_data.video_id, object_story_id, nor a resolvable link."
    )


def local_media_extension_from_url(url: str | None, default: str = ".jpg") -> str:
    if not url:
        return default
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"} else default


def download_file(url: str, dest: Path, *, get: Callable | None = None) -> None:
    from motata_cli.transport.gateway import gateway_enabled, RemoteGatewayTransport
    if gateway_enabled():
        transport=RemoteGatewayTransport.from_environment()
        try: transport.download(url,dest)
        finally: transport.close()
        return
    with (get or requests.get)(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


META_VERSION = env_first("MOTATA_META_VERSION", default="v23.0")


META_BASE_URL = f"https://graph.facebook.com/{META_VERSION}"


META_VIDEO_BASE_URL = f"https://graph-video.facebook.com/{META_VERSION}"


VIDEO_CHUNKED_THRESHOLD = 20 * 1024 * 1024


VIDEO_MAX_CHUNK_WINDOW_SIZE = 5 * 1024 * 1024


VIDEO_MAX_FILE_SIZE = 4_000_000_000


VIDEO_MAX_RETRIES = 5


VIDEO_RETRY_BASE_DELAY_MS = 1000


VIDEO_RETRY_MAX_DELAY_MS = 60_000


VIDEO_RETRYABLE_META_CODES = {1, 2, 4, 17, 32, 80, 613}


def is_retryable_video_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    payload = parse_meta_error_payload(exc)
    code = payload.get("code") if payload else None
    return code in VIDEO_RETRYABLE_META_CODES


def retry_delay_seconds(attempt: int) -> float:
    base = VIDEO_RETRY_BASE_DELAY_MS * (2 ** (attempt - 1))
    capped = min(base, VIDEO_RETRY_MAX_DELAY_MS)
    return (capped * (0.8 + (0.4 * (time.time() % 1)))) / 1000.0


def parse_upload_offset(value: Any, label: str) -> int:
    if value in (None, ""):
        raise CliError(f"Invalid {label} returned by video upload API: {value}")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CliError(f"Invalid {label} returned by video upload API: {value}") from exc
    if parsed < 0:
        raise CliError(f"Invalid {label} returned by video upload API: {value}")
    return parsed


def file_tuple(path: Path, field_name: str) -> tuple[str, Any, str]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return (path.name, path.open("rb"), mime)


def upload_image(meta: MetaClient, account_id: str, file_path: str, name: str | None = None) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise CliError(f"Image file not found: {path}")
    with path.open("rb") as handle:
        payload = meta.post(
            f"{ad_account_path(account_id)}/adimages",
            data={"name": name or path.name},
            files={"bytes": (path.name, handle, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
        )
    images = payload.get("images") or {}
    if images:
        first = next(iter(images.values()))
        return {"id": first.get("hash"), "type": "image", **first, "raw": payload}
    return {"id": payload.get("hash"), "type": "image", "raw": payload}


def build_video_client(meta: MetaClient) -> MetaClient:
    if getattr(meta, 'gateway', None) is not None:
        return meta  # Gateway policy selects the fixed video origin; preserve account ref.
    return MetaClient(meta.access_token, version=META_VERSION, base_url=META_VIDEO_BASE_URL, error_factory=meta.error_factory)


def video_post_with_retry(
    video_meta: MetaClient,
    path: str,
    *,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    attempt: int = 1,
    context: str,
) -> dict[str, Any]:
    # Historical name/signature retained. These are writes, not retryable reads:
    # start/single/finish may have committed before a timeout or disconnect. Even
    # transfer's session ID and requested offset do not prove the server's current
    # offset. Until a strict server-side session/offset verifier exists, stop and
    # let the migration ledger mark the result unknown rather than replay bytes.
    try:
        return video_meta.post(path, data=data, files=files)
    except Exception as exc:
        raise CliError(
            f"{context} failed; upload outcome unknown, not automatically retried: {exc}"
        ) from exc


def single_upload_video(
    video_meta: MetaClient,
    account_id: str,
    file_path: Path,
    *,
    name: str | None,
    title: str | None,
) -> dict[str, Any]:
    mime = mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    with file_path.open("rb") as source:
        payload = video_post_with_retry(
            video_meta,
            f"{ad_account_path(account_id)}/advideos",
            data=filter_empty({"name": name or file_path.name, "title": title}),
            files={"source": (file_path.name, source, mime)},
            context=f"Single video upload for {file_path.name}",
        )
    return {"id": payload.get("id") or payload.get("video_id"), "type": "video", **payload, "raw": payload}


def chunked_upload_video(
    video_meta: MetaClient,
    account_id: str,
    file_path: Path,
    *,
    name: str | None,
    title: str | None,
) -> dict[str, Any]:
    file_size = file_path.stat().st_size
    endpoint = f"{ad_account_path(account_id)}/advideos"
    start_result = video_post_with_retry(
        video_meta,
        endpoint,
        data={"upload_phase": "start", "file_size": str(file_size)},
        context=f"Video upload start for {file_path.name}",
    )
    upload_session_id = start_result.get("upload_session_id")
    if not upload_session_id:
        raise CliError(f"Video upload start did not return upload_session_id: {json.dumps(start_result, ensure_ascii=False)}")
    video_id = start_result.get("video_id")
    start_offset = parse_upload_offset(start_result.get("start_offset"), "start_offset")
    end_offset = parse_upload_offset(start_result.get("end_offset"), "end_offset")
    if start_offset != 0:
        raise CliError("New video upload did not start at offset 0; upload outcome unknown")
    with file_path.open("rb") as handle:
        while start_offset != end_offset:
            if end_offset < start_offset or end_offset > file_size:
                raise CliError(f"Invalid upload window returned by video upload API: {start_offset}-{end_offset}")
            chunk_size = end_offset - start_offset
            if chunk_size > VIDEO_MAX_CHUNK_WINDOW_SIZE:
                raise CliError(
                    f"Upload chunk window {chunk_size} exceeds supported maximum {VIDEO_MAX_CHUNK_WINDOW_SIZE} bytes"
                )
            handle.seek(start_offset)
            buffer = handle.read(chunk_size)
            if len(buffer) != chunk_size:
                raise CliError(
                    f"Failed to read {chunk_size} bytes for upload chunk at offset {start_offset}; read {len(buffer)} bytes"
                )
            transfer_result = video_post_with_retry(
                video_meta,
                endpoint,
                data={
                    "upload_phase": "transfer",
                    "upload_session_id": str(upload_session_id),
                    "start_offset": str(start_offset),
                },
                files={"video_file_chunk": ("chunk", buffer, "application/octet-stream")},
                context=f"Video chunk upload at offset {start_offset}",
            )
            next_start = parse_upload_offset(transfer_result.get("start_offset"), "start_offset")
            next_end = parse_upload_offset(transfer_result.get("end_offset"), "end_offset")
            if next_start != end_offset:
                raise CliError("Video upload did not confirm the submitted chunk; upload outcome unknown, no bytes replayed")
            start_offset, end_offset = next_start, next_end
    if start_offset != file_size:
        raise CliError("Video upload ended before confirming the whole file; upload outcome unknown")
    finish_result = video_post_with_retry(
        video_meta,
        endpoint,
        data=filter_empty(
            {
                "upload_phase": "finish",
                "upload_session_id": str(upload_session_id),
                "title": title,
                "name": name or file_path.name,
            }
        ),
        context=f"Video upload finish for {file_path.name}",
    )
    if finish_result.get("success") is not True:
        raise CliError("Video upload finish did not confirm success; upload outcome unknown")
    payload = {"id": finish_result.get("video_id") or video_id, "type": "video", **finish_result, "raw": finish_result}
    if payload.get("id") is None and video_id:
        payload["id"] = video_id
    return payload


def upload_video(
    meta: MetaClient,
    account_id: str,
    file_path: str,
    name: str | None = None,
    thumbnail_path: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise CliError(f"Video file not found: {path}")
    file_size = path.stat().st_size
    if file_size > VIDEO_MAX_FILE_SIZE:
        raise CliError(f"File exceeds 4 GB maximum ({file_size} bytes).")
    video_meta = build_video_client(meta)
    if file_size <= VIDEO_CHUNKED_THRESHOLD:
        payload = single_upload_video(video_meta, account_id, path, name=name, title=title)
    else:
        payload = chunked_upload_video(video_meta, account_id, path, name=name, title=title)
    if thumbnail_path:
        thumb = Path(thumbnail_path)
        if not thumb.exists():
            raise CliError(f"Thumbnail file not found: {thumb}")
    return payload


def wait_for_video_thumbnail(
    meta: MetaClient,
    account_id: str,
    video_id: str,
    *,
    timeout_seconds: int = 180,
    poll_seconds: int = 5,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        payload = meta.get(
            video_id,
            params={"fields": "id,status,thumbnails{uri,is_preferred}"},
        )
        last_payload = payload
        thumbnails = (payload.get("thumbnails") or {}).get("data") or []
        if thumbnails:
            preferred = next((row for row in thumbnails if row.get("is_preferred")), thumbnails[0])
            return {"thumbnail_url": preferred.get("uri"), "video_status": (payload.get("status") or {}).get("video_status")}
        status = payload.get("status") or {}
        if status.get("video_status") in {"error", "failed"}:
            break
        time.sleep(poll_seconds)
    raise CliError(
        f"Could not auto-resolve thumbnail for video {video_id}. "
        f"Last status: {json.dumps(last_payload or {}, ensure_ascii=False)}"
    )
