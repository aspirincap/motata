#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from motata_cli import __version__
from motata_cli.meta.app_discovery import build_meta_app_report
from motata_cli.meta.activities import build_meta_activities_report
from motata_cli.meta.audience import build_meta_audience_breakdown
from motata_cli.meta.client import MetaClient
from motata_cli.meta.landing_pages import build_landing_page_report
from motata_cli.meta.metrics import build_meta_metric_probe
from motata_cli.meta.output import print_output
from motata_cli.meta.payloads import (
    build_ad_payload,
    build_adset_payload,
    build_campaign_payload,
    build_creative_payload,
    build_story_spec,
    normalize_promoted_object,
)
from motata_cli.meta.preflight import (
    adset_uses_app_promoted_object,
    build_validation_adset_args,
    build_validation_campaign_args,
    describe_validation_error,
    remap_promoted_object_for_target,
    validate_ad_link_probe,
    validate_migration_preflight,
    validate_promoted_object_probe,
    validate_target_app_ad_links,
    validate_target_promoted_objects,
)
from motata_cli.meta.user_type import build_user_type_report
from motata_cli.meta.services import (
    build_auth_from_args,
    build_migration_plan_summary,
    cleanup_object,
    create_ad,
    create_adset,
    create_campaign,
    create_creative,
    export_migration_bundle,
    get_entity,
    list_entities,
    run_migration_flow,
    update_entity,
)


def env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default

META_VERSION = env_first("MOTATA_META_VERSION", default="v23.0")
META_BASE_URL = f"https://graph.facebook.com/{META_VERSION}"
META_VIDEO_BASE_URL = f"https://graph-video.facebook.com/{META_VERSION}"
CACHE_DIR = Path(env_first("MOTATA_HOME", default=str(Path.home() / ".motata"))).expanduser()
CONFIG_PATH = CACHE_DIR / "config.json"
JOBS_DIR = CACHE_DIR / "jobs"
VIDEO_CHUNKED_THRESHOLD = 20 * 1024 * 1024
VIDEO_MAX_CHUNK_WINDOW_SIZE = 5 * 1024 * 1024
VIDEO_MAX_FILE_SIZE = 4_000_000_000
VIDEO_MAX_RETRIES = 5
VIDEO_RETRY_BASE_DELAY_MS = 1000
VIDEO_RETRY_MAX_DELAY_MS = 60_000
VIDEO_RETRYABLE_META_CODES = {1, 2, 4, 17, 32, 80, 613}
BID_STRATEGIES_REQUIRING_BID_CAP = {"LOWEST_COST_WITH_BID_CAP", "COST_CAP"}
BID_STRATEGY_MIN_ROAS = "LOWEST_COST_WITH_MIN_ROAS"


class CliError(RuntimeError):
    pass


@dataclass
class AuthContext:
    account_id: str
    media_code: str
    access_token: str
    expires_at: int | None
    source: str


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def now_ts() -> int:
    return int(time.time())


def normalize_account_id(account_id: str) -> str:
    account_id = str(account_id).strip()
    return account_id[4:] if account_id.startswith("act_") else account_id


def ad_account_path(account_id: str) -> str:
    account_id = normalize_account_id(account_id)
    return f"act_{account_id}"


def load_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def load_required_migration_export(export_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    asset_tree_path = export_dir / "asset-tree.json"
    creatives_path = export_dir / "creatives.raw.json"
    missing = [str(path) for path in (asset_tree_path, creatives_path) if not path.exists()]
    if missing:
        raise CliError(
            "Migration export is incomplete. Expected files:\n"
            f"- {asset_tree_path}\n"
            f"- {creatives_path}\n"
            f"Missing: {', '.join(missing)}\n"
            "Generate them with `motata meta migrate export`, then rerun the migration command."
        )

    asset_tree = load_json_file(asset_tree_path)
    creatives = load_json_file(creatives_path)
    if not isinstance(asset_tree, dict) or not isinstance(asset_tree.get("tree"), list):
        raise CliError(f"Invalid migration export: {asset_tree_path} must contain a top-level 'tree' list.")
    if not isinstance(creatives, dict):
        raise CliError(f"Invalid migration export: {creatives_path} must contain a JSON object keyed by creative ID.")
    return asset_tree, creatives


MIGRATION_EXPORT_CAMPAIGN_FIELDS = [
    "id",
    "name",
    "objective",
    "status",
    "daily_budget",
    "lifetime_budget",
    "bid_strategy",
    "special_ad_categories",
]
MIGRATION_EXPORT_ADSET_FIELDS = [
    "id",
    "name",
    "campaign_id",
    "status",
    "effective_status",
    "optimization_goal",
    "billing_event",
    "promoted_object",
    "targeting",
    "daily_budget",
    "lifetime_budget",
    "bid_amount",
    "bid_strategy",
    "bid_constraints",
    "start_time",
    "end_time",
    "destination_type",
]
MIGRATION_EXPORT_AD_FIELDS = [
    "id",
    "name",
    "campaign_id",
    "adset_id",
    "status",
    "effective_status",
    "bid_amount",
    "creative",
]
MIGRATION_EXPORT_CREATIVE_FIELDS = [
    "id",
    "name",
    "object_story_spec",
    "object_story_id",
    "asset_feed_spec",
    "media_sourcing_spec",
    "url_tags",
    "thumbnail_url",
    "image_url",
    "link_url",
    "body",
    "title",
    "call_to_action_type",
    "object_type",
    "actor_id",
]


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").replace("\ufffd", " ")
    text = "".join(" " if unicodedata.category(ch) == "So" else ch for ch in text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def json_or_none(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse_json_option(raw: Any, label: str, expected_type: type | tuple[type, ...] | None = None) -> Any:
    if raw in (None, ""):
        return None
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(f"Invalid {label} JSON: {exc.msg}") from exc
    if expected_type and not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            names = ", ".join(t.__name__ for t in expected_type)
        else:
            names = expected_type.__name__
        raise CliError(f"Invalid {label}: expected {names}")
    return value


def parse_positive_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CliError(f"Invalid {label}: expected a positive integer") from exc
    if parsed <= 0:
        raise CliError(f"Invalid {label}: expected a positive integer")
    return parsed


def parse_positive_int_str(value: Any, label: str) -> str | None:
    parsed = parse_positive_int(value, label)
    return str(parsed) if parsed is not None else None


def validate_name(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        raise CliError(f"{label} cannot be empty")
    if len(normalized) > 400:
        raise CliError(f"{label} exceeds 400 characters")
    return normalized


def validate_non_empty(value: str | None, label: str) -> str:
    normalized = validate_name(value, label)
    if normalized is None:
        raise CliError(f"Missing {label}")
    return normalized


def validate_iso_datetime(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CliError(f"Invalid {label}: expected ISO 8601 datetime") from exc
    return normalized


def validate_domain(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", normalized):
        raise CliError(f"Invalid {label}: expected a domain like example.com")
    return normalized


def parse_meta_error_payload(exc: Exception) -> dict[str, Any] | None:
    if not isinstance(exc, CliError):
        return None
    try:
        payload = json.loads(str(exc))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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


def load_config() -> dict[str, Any]:
    ensure_dirs()
    return load_json_file(CONFIG_PATH, default={"values": {}, "account_aliases": {}})


def save_config(payload: dict[str, Any]) -> None:
    ensure_dirs()
    write_json_file(CONFIG_PATH, payload)


def resolve_account_ref(account_ref: str | None) -> str | None:
    if not account_ref:
        config = load_config()
        default_account = (config.get("values") or {}).get("default_account")
        if default_account:
            return normalize_account_id(default_account)
        return None
    raw = str(account_ref).strip()
    if raw.startswith("act_") or raw.isdigit():
        return normalize_account_id(raw)
    config = load_config()
    aliases = config.get("account_aliases") or {}
    mapped = aliases.get(raw)
    if isinstance(mapped, dict):
        mapped = mapped.get("account_id")
    if mapped:
        return normalize_account_id(mapped)
    return normalize_account_id(raw)


def resolve_auth(
    *,
    account_id: str,
    media_code: str = "facebook",
    access_token: str | None = None,
) -> AuthContext:
    if access_token:
        return AuthContext(
            account_id=normalize_account_id(account_id) if account_id else "",
            media_code=media_code,
            access_token=access_token,
            expires_at=None,
            source="direct",
        )
    raise CliError(
        "Missing access token. Fetch one with the motata token skill "
        "and pass it via --access-token."
    )


def first_by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = normalize_name(name)
    for item in items:
        current = normalize_name(item.get("name"))
        if current == target or current.startswith(target):
            return item
    return None


def first_creative_by_prefix(items: list[dict[str, Any]], name: str, page_id: str | None) -> dict[str, Any] | None:
    target = normalize_name(name)
    for item in items:
        item_page = str(((item.get("object_story_spec") or {}).get("page_id")) or "")
        if page_id and item_page and item_page != str(page_id):
            continue
        if normalize_name(item.get("name")).startswith(target):
            return item
    return None


def infer_promotable_pages(meta: MetaClient, account_id: str) -> dict[str, Any]:
    account = ad_account_path(account_id)
    visible_pages = meta.paginate(
        "me/accounts",
        params={"fields": "id,name,tasks,instagram_business_account{id,username},connected_instagram_account{id,username}", "limit": 200},
    )
    creatives = meta.paginate(
        f"{account}/adcreatives",
        params={"fields": "id,name,object_story_spec{page_id,instagram_user_id}", "limit": 50},
    )
    ads = meta.paginate(
        f"{account}/ads",
        params={"fields": "id,name,creative{id,name,object_story_spec{page_id,instagram_user_id}}", "limit": 50},
    )

    visible_map = {str(page["id"]): page for page in visible_pages}
    creative_pages: dict[str, dict[str, Any]] = {}
    ad_pages: dict[str, dict[str, Any]] = {}
    ig_ids: set[str] = set()

    for creative in creatives:
        oss = creative.get("object_story_spec") or {}
        page_id = str(oss.get("page_id") or "")
        if not page_id:
            continue
        creative_pages.setdefault(page_id, {"count": 0, "samples": []})
        creative_pages[page_id]["count"] += 1
        if len(creative_pages[page_id]["samples"]) < 5:
            creative_pages[page_id]["samples"].append({"id": creative["id"], "name": creative.get("name")})
        ig = oss.get("instagram_user_id")
        if ig:
            ig_ids.add(str(ig))

    for ad in ads:
        creative = ad.get("creative") or {}
        oss = creative.get("object_story_spec") or {}
        page_id = str(oss.get("page_id") or "")
        if not page_id:
            continue
        ad_pages.setdefault(page_id, {"count": 0, "samples": []})
        ad_pages[page_id]["count"] += 1
        if len(ad_pages[page_id]["samples"]) < 5:
            ad_pages[page_id]["samples"].append(
                {
                    "ad_id": ad["id"],
                    "ad_name": ad.get("name"),
                    "creative_id": creative.get("id"),
                    "creative_name": creative.get("name"),
                }
            )
        ig = oss.get("instagram_user_id")
        if ig:
            ig_ids.add(str(ig))

    all_page_ids = sorted(set(visible_map) | set(creative_pages) | set(ad_pages))
    pages: list[dict[str, Any]] = []
    for page_id in all_page_ids:
        visible = visible_map.get(page_id)
        pages.append(
            {
                "page_id": page_id,
                "name": (visible or {}).get("name"),
                "token_visible": page_id in visible_map,
                "creative_seen": page_id in creative_pages,
                "ad_attach_usable": page_id in ad_pages,
                "creative_count": (creative_pages.get(page_id) or {}).get("count", 0),
                "ad_count": (ad_pages.get(page_id) or {}).get("count", 0),
                "tasks": (visible or {}).get("tasks", []),
                "instagram_business_account": (visible or {}).get("instagram_business_account"),
                "connected_instagram_account": (visible or {}).get("connected_instagram_account"),
                "creative_samples": (creative_pages.get(page_id) or {}).get("samples", []),
                "ad_samples": (ad_pages.get(page_id) or {}).get("samples", []),
            }
        )

    return {
        "account_id": account,
        "token_visible_pages": visible_pages,
        "pages": pages,
        "instagram_user_ids_seen": sorted(ig_ids),
    }


def discover_pixels(meta: MetaClient, account_id: str) -> list[dict[str, Any]]:
    return meta.paginate(
        f"{ad_account_path(account_id)}/adspixels",
        params={"fields": "id,name,owner_ad_account,creation_time,last_fired_time,is_created_by_business", "limit": 200},
    )


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
    try:
        return video_meta.post(path, data=data, files=files)
    except Exception as exc:
        if attempt >= VIDEO_MAX_RETRIES or not is_retryable_video_error(exc):
            raise CliError(f"{context} failed: {exc}") from exc
        time.sleep(retry_delay_seconds(attempt))
        return video_post_with_retry(
            video_meta,
            path,
            data=data,
            files=files,
            attempt=attempt + 1,
            context=context,
        )


def single_upload_video(
    video_meta: MetaClient,
    account_id: str,
    file_path: Path,
    *,
    name: str | None,
    title: str | None,
) -> dict[str, Any]:
    file_bytes = file_path.read_bytes()
    mime = mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    payload = video_post_with_retry(
        video_meta,
        f"{ad_account_path(account_id)}/advideos",
        data=filter_empty({"name": name or file_path.name, "title": title}),
        files={"source": (file_path.name, file_bytes, mime)},
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
    with file_path.open("rb") as handle:
        while start_offset != end_offset:
            if end_offset < start_offset:
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
            start_offset = parse_upload_offset(transfer_result.get("start_offset"), "start_offset")
            end_offset = parse_upload_offset(transfer_result.get("end_offset"), "end_offset")
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


def parse_fields(fields: list[str] | None, default: list[str]) -> str:
    return ",".join(fields or default)


def filter_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}



def build_meta(args: argparse.Namespace) -> MetaClient:
    auth = resolve_auth(
        account_id=getattr(args, "account_id", None),
        access_token=getattr(args, "access_token", None),
        media_code=getattr(args, "media_code", "facebook"),
    )
    return MetaClient(auth.access_token, version=META_VERSION, error_factory=CliError)


def command_accounts_inspect(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        ad_account_path(args.account_id),
        params={"fields": "id,name,account_status,currency,timezone_name,business,owner,capabilities"},
    )
    print_output(payload, as_json=True)


def command_accounts_list(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = {
        "fields": parse_fields(
            getattr(args, "fields", None),
            ["id", "name", "account_status", "currency", "timezone_name", "business"],
        ),
        "limit": args.limit,
    }
    if args.after:
        params["after"] = args.after
    if args.all:
        payload = meta.paginate("me/adaccounts", params=params)
        print_output(payload, as_json=True)
        return
    payload = meta.get("me/adaccounts", params=params)
    print_output(payload.get("data") or payload, as_json=True)


def command_accounts_info(args: argparse.Namespace) -> None:
    if getattr(args, "account_ref", None) and not getattr(args, "account_id", None):
        args.account_id = args.account_ref
    meta = build_meta(args)
    payload = meta.get(
        ad_account_path(args.account_id),
        params={"fields": "id,name,account_status,currency,timezone_name,business,owner,capabilities"},
    )
    print_output(payload, as_json=True)


def command_assets_pages(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = {
        "fields": parse_fields(
            getattr(args, "fields", None),
            ["id", "name", "tasks", "instagram_business_account{id,username}", "connected_instagram_account{id,username}"],
        ),
        "limit": getattr(args, "limit", 25),
    }
    if getattr(args, "after", None):
        params["after"] = args.after
    if getattr(args, "all", False):
        payload = meta.paginate("me/accounts", params=params)
        print_output(payload, as_json=True)
        return
    payload = meta.get("me/accounts", params=params)
    print_output(payload.get("data") or payload, as_json=True)


def command_assets_pixels(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    print_output(discover_pixels(meta, args.account_id), as_json=True)


def command_assets_instagram(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = infer_promotable_pages(meta, args.account_id)
    ig_rows = []
    seen = set()
    for page in payload["token_visible_pages"]:
        for key in ("instagram_business_account", "connected_instagram_account"):
            ig = page.get(key)
            if ig and str(ig.get("id")) not in seen:
                seen.add(str(ig["id"]))
                ig_rows.append({"source": key, **ig})
    for ig_id in payload["instagram_user_ids_seen"]:
        if ig_id not in seen:
            ig_rows.append({"id": ig_id, "source": "seen_in_ads_or_creatives"})
    print_output(ig_rows, as_json=True)


def command_assets_promotable_pages(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = infer_promotable_pages(meta, args.account_id)
    print_output(payload["pages"], as_json=True)


def command_assets_discover(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = infer_promotable_pages(meta, args.account_id)
    payload["pixels"] = discover_pixels(meta, args.account_id)
    print_output(payload, as_json=True)


def command_pages_list(args: argparse.Namespace) -> None:
    command_assets_pages(args)


def command_pages_instagram(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        args.page_id,
        params={"fields": "id,name,instagram_business_account{id,username},connected_instagram_account{id,username}"},
    )
    instagram = payload.get("instagram_business_account") or payload.get("connected_instagram_account")
    if instagram:
        print_output(instagram, as_json=True)
        return
    print_output(
        {
            "page_id": args.page_id,
            "status": "not_found",
            "hint": "No Instagram account found. The page may have no linked Instagram account, or the token may be missing instagram_basic permission.",
        },
        as_json=True,
    )


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


def command_media_upload_image(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = upload_image(meta, args.account_id, args.file, args.name)
    print_output(payload, as_json=True)


def command_media_upload_video(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    if args.thumbnail and args.auto_thumbnail:
        raise CliError("Cannot use both --thumbnail and --auto-thumbnail.")
    payload = upload_video(meta, args.account_id, args.file, args.name, args.thumbnail, args.title)
    result = dict(payload)
    image_hash = None
    if args.thumbnail:
        uploaded_thumb = upload_image(meta, args.account_id, args.thumbnail, Path(args.thumbnail).name)
        image_hash = uploaded_thumb.get("hash") or uploaded_thumb.get("id")
    elif args.auto_thumbnail:
        resolved = wait_for_video_thumbnail(meta, args.account_id, str(payload.get("id") or payload.get("video_id")))
        thumbnail_url = resolved.get("thumbnail_url")
        if not thumbnail_url:
            raise CliError("Auto thumbnail requested, but Meta returned no thumbnail URL.")
        temp_path = CACHE_DIR / "tmp" / f"video-thumb-{payload.get('id') or payload.get('video_id')}.jpg"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        download_file(thumbnail_url, temp_path)
        try:
            uploaded_thumb = upload_image(meta, args.account_id, str(temp_path), temp_path.name)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        image_hash = uploaded_thumb.get("hash") or uploaded_thumb.get("id")
        if resolved.get("video_status"):
            result["status"] = resolved["video_status"]
    if image_hash:
        result["image_hash"] = image_hash
    print_output(result, as_json=True)


def command_creatives_create(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = create_creative(meta, args.account_id, args)
    print_output(payload, as_json=True)


def command_campaigns_list(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = filter_empty(
        {
            "effective_status": ",".join(args.status) if args.status else None,
            "objective": args.objective,
            "after": args.after,
        }
    )
    payload = list_entities(
        meta,
        ad_account_path(args.account_id),
        "campaigns",
        fields=args.fields or ["id", "name", "objective", "status", "effective_status"],
        extra_params=params,
        limit=args.limit,
        fetch_all=args.all,
    )
    print_output(payload, as_json=True)


def command_campaigns_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = get_entity(meta, args.campaign_id, args.fields or ["id", "name", "objective", "status", "effective_status", "daily_budget", "lifetime_budget", "bid_strategy"])
    print_output(payload, as_json=True)


def command_campaigns_update(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = build_campaign_payload(args, update=True)
    result = update_entity(meta, args.campaign_id, payload)
    print_output(result, as_json=True)


def command_adsets_list(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = filter_empty(
        {
            "effective_status": ",".join(args.status) if args.status else None,
            "after": args.after,
        }
    )
    parent_path = args.campaign or ad_account_path(args.account_id)
    payload = list_entities(
        meta,
        parent_path,
        "adsets",
        fields=args.fields or ["id", "name", "campaign_id", "status", "effective_status", "optimization_goal"],
        extra_params=params,
        limit=args.limit,
        fetch_all=args.all,
    )
    print_output(payload, as_json=True)


def command_adsets_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = get_entity(meta, args.adset_id, args.fields or ["id", "name", "campaign_id", "status", "effective_status", "optimization_goal", "billing_event", "promoted_object", "targeting"])
    print_output(payload, as_json=True)


def command_adsets_update(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = build_adset_payload(args, update=True)
    result = update_entity(meta, args.adset_id, payload)
    print_output(result, as_json=True)


def command_ads_list(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = filter_empty(
        {
            "effective_status": ",".join(args.status) if args.status else None,
            "after": args.after,
        }
    )
    parent_path = args.adset or args.campaign or ad_account_path(args.account_id)
    payload = list_entities(
        meta,
        parent_path,
        "ads",
        fields=args.fields or ["id", "name", "campaign_id", "adset_id", "status", "effective_status"],
        extra_params=params,
        limit=args.limit,
        fetch_all=args.all,
    )
    print_output(payload, as_json=True)


def command_ads_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = get_entity(meta, args.ad_id, args.fields or ["id", "name", "campaign_id", "adset_id", "status", "effective_status", "creative"])
    print_output(payload, as_json=True)


def command_ads_update(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = build_ad_payload(args, update=True)
    result = update_entity(meta, args.ad_id, payload)
    print_output(result, as_json=True)


def command_creatives_list(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    if args.ad:
        params = filter_empty({"after": args.after})
        payload = list_entities(
            meta,
            args.ad,
            "adcreatives",
            fields=args.fields or ["id", "name", "object_story_spec"],
            extra_params=params,
            limit=args.limit,
            fetch_all=args.all,
        )
    else:
        params = filter_empty({"after": args.after})
        payload = list_entities(
            meta,
            ad_account_path(args.account_id),
            "adcreatives",
            fields=args.fields or ["id", "name", "object_story_spec"],
            extra_params=params,
            limit=args.limit,
            fetch_all=args.all,
        )
    print_output(payload, as_json=True)


def command_creatives_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = get_entity(
        meta,
        args.creative_id,
        args.fields or ["id", "name", "object_story_spec", "asset_feed_spec", "media_sourcing_spec"],
    )
    print_output(payload, as_json=True)


def command_creatives_update(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = update_entity(meta, args.creative_id, build_creative_payload(args, update=True))
    print_output(payload, as_json=True)


def command_campaigns_create(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = create_campaign(meta, args.account_id, args)
    print_output(payload, as_json=True)


def command_adsets_create(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = create_adset(meta, args.account_id, args)
    print_output(payload, as_json=True)


def command_ads_create(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = create_ad(meta, args.account_id, args)
    print_output(payload, as_json=True)


def command_validate_creative(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    temp_name = args.name or f"motata-validate-creative-{uuid.uuid4().hex[:8]}"
    args.name = temp_name
    created = create_creative(meta, args.account_id, args)
    result = {"ok": True, "creative": created}
    if args.cleanup and created.get("id"):
        result["cleanup"] = cleanup_object(meta, created["id"])
    print_output(result, as_json=True)


def command_validate_ad_link(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    name = args.name or f"motata-validate-ad-{uuid.uuid4().hex[:8]}"
    payload = meta.post(
        f"{ad_account_path(args.account_id)}/ads",
        data={
            "name": name,
            "adset_id": args.adset_id,
            "creative": json.dumps({"creative_id": args.creative_id}, separators=(",", ":")),
            "status": "PAUSED",
        },
    )
    result = {"ok": True, "ad": payload}
    if args.cleanup and payload.get("id"):
        result["cleanup"] = cleanup_object(meta, payload["id"])
    print_output(result, as_json=True)


def command_validate_promoted_object(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = validate_promoted_object_probe(
        meta,
        args.account_id,
        source_campaign={
            "objective": args.campaign_objective,
            "special_ad_categories": [],
        },
        source_adset={
            "optimization_goal": args.optimization_goal,
            "billing_event": args.billing_event,
            "targeting": parse_json_option(args.targeting_json, "targeting", dict) or {},
        },
        promoted_object=parse_json_option(args.promoted_object_json, "promoted_object", dict) or {},
        cleanup=args.cleanup,
    )
    print_output(result, as_json=True)


def command_insights_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    if args.ad:
        path = f"{args.ad}/insights"
    elif args.adset:
        path = f"{args.adset}/insights"
    elif args.campaign:
        path = f"{args.campaign}/insights"
    else:
        path = f"{ad_account_path(args.account_id)}/insights"
    params = filter_empty(
        {
            "level": args.level,
            "date_preset": args.date_preset,
            "breakdowns": ",".join(args.breakdowns) if args.breakdowns else None,
            "fields": parse_fields(args.fields, ["impressions", "reach", "clicks", "spend", "cpm", "cpc", "ctr"]),
            "time_increment": args.time_increment,
            "action_report_time": args.action_report_time,
            "action_attribution_windows": json.dumps(args.action_attribution_windows, separators=(",", ":"))
            if args.action_attribution_windows
            else None,
            "use_unified_attribution_setting": "true" if args.use_unified_attribution_setting else None,
            "summary": ",".join(args.summary) if args.summary else None,
            "default_summary": "true" if args.default_summary else None,
            "sort": ",".join(args.sort) if args.sort else None,
            "limit": args.limit,
        }
    )
    if args.since or args.until:
        params["time_range"] = json.dumps(filter_empty({"since": args.since, "until": args.until}), separators=(",", ":"))
    if args.summary or args.default_summary:
        payload = meta.get(path, params=params)
        print_output(payload, as_json=True)
        return
    paginate_insights = getattr(meta, "paginate_insights", None)
    if callable(paginate_insights):
        payload = paginate_insights(path, params=params, prefer_async=args.async_report, auto_async=args.auto_async)
    else:
        payload = meta.paginate(path, params=params)
    print_output(payload, as_json=True)


def command_landing_pages_analyze(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_landing_page_report(
        meta,
        account_id=getattr(args, "account_id", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        date_preset=getattr(args, "date_preset", None),
        account_limit=getattr(args, "account_limit", 10),
        top=getattr(args, "top", None),
        insight_limit=getattr(args, "limit", 5000),
        enrich_product=not getattr(args, "no_product", False),
        product_limit=getattr(args, "product_limit", 50),
        include_ads=getattr(args, "include_ads", False),
        include_previews=not getattr(args, "no_previews", False),
        async_insights=getattr(args, "async_report", False),
        auto_async_insights=getattr(args, "auto_async", True),
        profile=getattr(args, "profile", "full"),
        max_ad_context_fetches=getattr(args, "max_ad_context_fetches", None),
    )
    print_output(result, as_json=True)


def command_apps_analyze(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_meta_app_report(
        meta,
        account_id=getattr(args, "account_id", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        date_preset=getattr(args, "date_preset", None),
        account_limit=getattr(args, "account_limit", 2),
        campaign_limit=getattr(args, "campaign_limit", 20),
        include_campaigns=getattr(args, "include_campaigns", False),
        profile=getattr(args, "profile", "full"),
    )
    print_output(result, as_json=True)


def command_user_type_analyze(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_user_type_report(
        meta,
        account_id=getattr(args, "account_id", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        date_preset=getattr(args, "date_preset", None),
        account_limit=getattr(args, "account_limit", 10),
        campaign_limit=getattr(args, "campaign_limit", 10),
        ad_limit=getattr(args, "ad_limit", 5),
        content_limit=getattr(args, "content_limit", 60),
        include_evidence=getattr(args, "include_evidence", False),
        profile=getattr(args, "profile", "full"),
    )
    print_output(result, as_json=True)


def command_metrics_probe(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_meta_metric_probe(
        meta,
        account_id=getattr(args, "account_id", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        date_preset=getattr(args, "date_preset", None),
        account_limit=getattr(args, "account_limit", 10),
        insight_limit=getattr(args, "limit", 25),
        async_insights=getattr(args, "async_report", False),
        auto_async_insights=getattr(args, "auto_async", True),
        group_names=getattr(args, "groups", None),
        profile=getattr(args, "profile", "full"),
    )
    print_output(result, as_json=True)


def command_audience_breakdown(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_meta_audience_breakdown(
        meta,
        account_id=getattr(args, "account_id"),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        date_preset=getattr(args, "date_preset", None),
        insight_limit=getattr(args, "limit", 500),
        top=getattr(args, "top", 20),
        breakdowns=getattr(args, "breakdowns", None),
        async_insights=getattr(args, "async_report", False),
        auto_async_insights=getattr(args, "auto_async", True),
    )
    print_output(result, as_json=True)


def command_activities_get(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    result = build_meta_activities_report(
        meta,
        account_id=getattr(args, "account_id"),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        fields=getattr(args, "fields", None),
        limit=getattr(args, "limit", 100),
        max_pages=getattr(args, "max_pages", 1),
    )
    print_output(result, as_json=True)


def command_targeting_interests(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        "search",
        params={"type": "adinterest", "q": args.query, "limit": args.limit},
    )
    print_output(payload.get("data") or payload, as_json=True)


def command_targeting_locations(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params: dict[str, Any] = {"type": "adgeolocation", "q": args.query, "limit": args.limit}
    if getattr(args, "location_types", None):
        params["location_types"] = args.location_types
    payload = meta.get("search", params=params)
    print_output(payload.get("data") or payload, as_json=True)


def command_targeting_suggestions(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    interests = getattr(args, "interests", None)
    interest_list_value = args.interest_list_json if getattr(args, "interest_list_json", None) else json.dumps(interests or [])
    payload = meta.get(
        "search",
        params={"type": "adinterestsuggestion", "interest_list": interest_list_value, "limit": args.limit},
    )
    print_output(payload.get("data") or payload, as_json=True)


def command_targeting_behaviors(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        "search",
        params={"type": "adTargetingCategory", "class": "behaviors", "limit": args.limit},
    )
    print_output(payload.get("data") or payload, as_json=True)


def command_targeting_demographics(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        "search",
        params={"type": "adTargetingCategory", "class": args.category_class or "demographics", "limit": args.limit},
    )
    print_output(payload.get("data") or payload, as_json=True)


def command_targeting_estimate(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    payload = meta.get(
        f"{ad_account_path(args.account_id)}/reachestimate",
        params={"targeting_spec": args.targeting_json},
    )
    print_output(payload.get("data") or payload, as_json=True)


def command_config_get(args: argparse.Namespace) -> None:
    config = load_config()
    value = (config.get("values") or {}).get(args.key)
    print_output({"key": args.key, "value": value}, as_json=True)


def command_config_list(args: argparse.Namespace) -> None:
    print_output(load_config(), as_json=True)


def command_config_set(args: argparse.Namespace) -> None:
    config = load_config()
    updates = 0
    if getattr(args, "account", None):
        config.setdefault("values", {})["default_account"] = normalize_account_id(args.account)
        updates += 1
    if getattr(args, "key", None):
        if args.value is None:
            raise CliError("--value is required when using --key")
        config.setdefault("values", {})[args.key] = args.value
        updates += 1
    if updates == 0:
        raise CliError("No config values provided. Use --account or --key --value.")
    save_config(config)
    print_output(config, as_json=True)


def command_config_unset(args: argparse.Namespace) -> None:
    config = load_config()
    (config.get("values") or {}).pop(args.key, None)
    save_config(config)
    print_output({"removed": args.key}, as_json=True)


def command_config_accounts_list(args: argparse.Namespace) -> None:
    config = load_config()
    aliases = config.get("account_aliases") or {}
    rows = []
    for alias, value in sorted(aliases.items()):
        if isinstance(value, dict):
            rows.append({"alias": alias, "account_id": value.get("account_id"), "label": value.get("label")})
        else:
            rows.append({"alias": alias, "account_id": value, "label": None})
    print_output(rows, as_json=True)


def command_config_accounts_add(args: argparse.Namespace) -> None:
    alias = args.alias.strip()
    if not alias:
        raise CliError("Alias cannot be empty.")
    if alias.startswith("act_") or alias.isdigit():
        raise CliError('Alias cannot look like an account ID ("act_..." or numeric). Use a descriptive name.')
    config = load_config()
    config.setdefault("account_aliases", {})[alias] = {
        "account_id": normalize_account_id(args.account_id),
        "label": args.label,
    }
    save_config(config)
    print_output({"alias": alias, "account_id": normalize_account_id(args.account_id), "label": args.label}, as_json=True)


def command_config_accounts_remove(args: argparse.Namespace) -> None:
    config = load_config()
    aliases = config.get("account_aliases") or {}
    if args.alias not in aliases:
        raise CliError(f'Alias "{args.alias}" not found.')
    aliases.pop(args.alias, None)
    save_config(config)
    print_output({"removed": args.alias}, as_json=True)


def command_debug_graph(args: argparse.Namespace) -> None:
    meta = build_meta(args)
    params = json_or_none(args.params_json) or {}
    data = json_or_none(args.data_json) or {}
    if args.data_file:
        data = json.loads(Path(args.data_file).read_text())
    method = args.method.upper()
    if method == "GET":
        payload = meta.get(args.path, params=params)
    elif method == "POST":
        payload = meta.post(args.path, data=data)
    elif method == "DELETE":
        payload = meta.delete(args.path)
    else:
        raise CliError(f"Unsupported method: {args.method}")
    print_output(payload, as_json=True)


def referenced_creatives_from_asset_tree(asset_tree: dict[str, Any], creatives: dict[str, Any]) -> dict[str, Any]:
    referenced_ids: set[str] = set()
    for campaign_node in asset_tree["tree"]:
        for adset_node in campaign_node["adsets"]:
            for ad_node in adset_node["ads"]:
                creative = ad_node["creative"]
                referenced_ids.add(str(creative["id"]))
    return {cid: creatives[cid] for cid in referenced_ids if cid in creatives}


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
        "message": (
            video_data.get("message")
            or link_data.get("message")
            or photo_data.get("message")
            or template_data.get("message")
            or creative.get("body")
        ),
        "headline": (
            video_data.get("title")
            or link_data.get("name")
            or photo_data.get("name")
            or template_data.get("name")
            or creative.get("title")
        ),
        "description": (
            video_data.get("link_description")
            or link_data.get("description")
            or photo_data.get("caption")
            or template_data.get("description")
        ),
        "call_to_action": (
            ((video_data.get("call_to_action") or {}).get("type"))
            or ((link_data.get("call_to_action") or {}).get("type"))
            or ((photo_data.get("call_to_action") or {}).get("type"))
            or ((template_data.get("call_to_action") or {}).get("type"))
            or creative.get("call_to_action_type")
            or "SHOP_NOW"
        ),
    }


def detect_creative_migration_mode(creative: dict[str, Any]) -> str:
    oss = creative.get("object_story_spec") or {}
    if ((oss.get("video_data") or {}).get("video_id")):
        return "video"
    if creative.get("object_story_id"):
        return "existing_post"
    if pick_link(creative):
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


def download_file(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def save_job(job_id: str, payload: dict[str, Any]) -> Path:
    ensure_dirs()
    path = JOBS_DIR / f"{job_id}.json"
    write_json_file(path, payload)
    return path


def command_migrate_export(args: argparse.Namespace) -> None:
    export_dir = Path(args.export_dir).expanduser().resolve()
    source_auth = build_auth_from_args(args.source_account_id, args.source_access_token)
    source_meta = MetaClient(source_auth.access_token, version=META_VERSION, error_factory=CliError)
    summary = export_migration_bundle(
        source_meta,
        source_account_id=args.source_account_id,
        campaign_ids=[str(campaign_id) for campaign_id in args.campaign_id],
        export_dir=export_dir,
    )
    print_output(summary, as_json=True)


def command_migrate_plan(args: argparse.Namespace) -> None:
    export_dir = Path(args.export_dir)
    target_auth = build_auth_from_args(args.target_account_id, args.target_access_token)
    target_meta = MetaClient(target_auth.access_token, version=META_VERSION, error_factory=CliError)
    summary = build_migration_plan_summary(
        export_dir=export_dir,
        source_account_id=args.source_account_id,
        target_account_id=args.target_account_id,
        target_meta=target_meta,
    )
    print_output(summary, as_json=True)


def command_migrate_run(args: argparse.Namespace) -> None:
    result = run_migration_flow(args, commands_module=sys.modules[__name__])
    print_output(result, as_json=True)


def command_migrate_status(args: argparse.Namespace) -> None:
    job_path = JOBS_DIR / f"{args.job_id}.json"
    if not job_path.exists():
        raise CliError(f"Job not found: {args.job_id}")
    print_output(load_json_file(job_path), as_json=True)


def command_migrate_resume(args: argparse.Namespace) -> None:
    job_path = JOBS_DIR / f"{args.job_id}.json"
    if not job_path.exists():
        raise CliError(f"Job not found: {args.job_id}")
    job = load_json_file(job_path)
    config = job.get("config") or {}
    rerun = argparse.Namespace(
        export_dir=config["export_dir"],
        source_account_id=config["source_account_id"],
        target_account_id=config["target_account_id"],
        page_id=config.get("page_id"),
        instagram_user_id=config.get("instagram_user_id"),
        pixel_id=config.get("pixel_id"),
        target_application_id=config.get("target_application_id"),
        target_object_store_url=config.get("target_object_store_url"),
        promoted_object_overrides_json=config.get("promoted_object_overrides_json"),
        reuse_existing_by_name=config.get("reuse_existing_by_name", True),
        source_access_token=args.source_access_token,
        target_access_token=args.target_access_token,
        job_id=args.job_id,
    )
    command_migrate_run(rerun)


def register_meta_commands(subparsers) -> None:
    from motata_cli.meta.cli import register_meta_commands as register_meta_cli_commands

    register_meta_cli_commands(subparsers)
