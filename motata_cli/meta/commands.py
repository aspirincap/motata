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

from motata_cli.common import config as common_config
from motata_cli.common.config import CACHE_DIR, CONFIG_PATH, JOBS_DIR
from motata_cli.common.errors import CliError
from motata_cli.common.utils import (
    env_first,
    now_ts,
    normalize_account_id,
    load_json_file,
    write_json_file,
    normalize_name,
    json_or_none,
    json_compact,
    parse_json_option,
    parse_positive_int,
    parse_positive_int_str,
    validate_name,
    validate_non_empty,
    validate_iso_datetime,
    validate_domain,
    parse_fields,
    filter_empty,
)
from motata_cli.common.auth import (
    AuthContext,
    resolve_auth,
)
from motata_cli.meta.utils import (
    ad_account_path,
    parse_meta_error_payload,
)

from motata_cli import __version__
from motata_cli.meta.app_discovery import build_meta_app_report
from motata_cli.meta.activities import build_meta_activities_report
from motata_cli.meta.audience import build_meta_audience_breakdown
from motata_cli.meta.client import MetaClient
from motata_cli.meta.landing_pages import build_landing_page_report
from motata_cli.meta.metrics import build_meta_metric_probe
from motata_cli.meta.output import print_output
from motata_cli.meta.payloads import (
    BID_STRATEGIES_REQUIRING_BID_CAP,
    BID_STRATEGY_MIN_ROAS,
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
from motata_cli.meta.services import media as media_service
from motata_cli.meta.user_type import build_user_type_report
from motata_cli.meta.services import (
    build_auth_from_args,
    build_migration_plan_summary as _build_migration_plan_summary,
    cleanup_object,
    create_ad,
    create_adset,
    create_campaign,
    create_creative,
    export_migration_bundle as _export_migration_bundle,
    get_entity,
    list_entities,
    run_migration_flow,
    update_entity,
)


# Compatibility re-exports; implementations live below the CLI layer.
from motata_cli.meta.services.media import (
    META_VERSION, META_BASE_URL, META_VIDEO_BASE_URL,
    VIDEO_CHUNKED_THRESHOLD, VIDEO_MAX_CHUNK_WINDOW_SIZE, VIDEO_MAX_FILE_SIZE,
    VIDEO_MAX_RETRIES, VIDEO_RETRY_BASE_DELAY_MS, VIDEO_RETRY_MAX_DELAY_MS,
    VIDEO_RETRYABLE_META_CODES, is_retryable_video_error, retry_delay_seconds,
    parse_upload_offset, file_tuple, upload_image, build_video_client,
    video_post_with_retry, single_upload_video, chunked_upload_video, upload_video,
    wait_for_video_thumbnail,
)
from motata_cli.meta.services.migration_assets import (
    load_required_migration_export, referenced_creatives_from_asset_tree,
    first_by_name, first_creative_by_prefix,
    MIGRATION_EXPORT_CAMPAIGN_FIELDS, MIGRATION_EXPORT_ADSET_FIELDS,
    MIGRATION_EXPORT_AD_FIELDS, MIGRATION_EXPORT_CREATIVE_FIELDS,
)
from motata_cli.meta.services.discovery import infer_promotable_pages, discover_pixels


def export_migration_bundle(meta: MetaClient, *, source_account_id: str,
                            campaign_ids: list[str], export_dir: Path,
                            commands_module: Any = None) -> dict[str, Any]:
    return _export_migration_bundle(
        meta, source_account_id=source_account_id, campaign_ids=campaign_ids,
        export_dir=export_dir,
        commands_module=commands_module if commands_module is not None else sys.modules[__name__],
    )


def build_migration_plan_summary(*, export_dir: Path, source_account_id: str,
                                 target_account_id: str, target_meta: MetaClient,
                                 commands_module: Any = None) -> dict[str, Any]:
    return _build_migration_plan_summary(
        export_dir=export_dir, source_account_id=source_account_id,
        target_account_id=target_account_id, target_meta=target_meta,
        commands_module=commands_module if commands_module is not None else sys.modules[__name__],
    )


def ensure_dirs() -> None:
    common_config.ensure_dirs(cache_dir=CACHE_DIR, jobs_dir=JOBS_DIR)


def load_config() -> dict[str, Any]:
    return common_config.load_config(config_path=CONFIG_PATH, cache_dir=CACHE_DIR, jobs_dir=JOBS_DIR)


def save_config(payload: dict[str, Any]) -> None:
    common_config.save_config(payload, config_path=CONFIG_PATH, cache_dir=CACHE_DIR, jobs_dir=JOBS_DIR)


def resolve_account_ref(account_ref: str | None) -> str | None:
    return common_config.resolve_account_ref(account_ref, config_loader=load_config)


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


pick_link = media_service.pick_link


pick_creative_thumbnail_url = media_service.pick_creative_thumbnail_url


pick_creative_text_parts = media_service.pick_creative_text_parts


def detect_creative_migration_mode(creative: dict[str, Any]) -> str:
    # Preserve the historical commands.pick_link patch seam without reverse imports.
    return media_service.detect_creative_migration_mode(creative, link_picker=pick_link)


local_media_extension_from_url = media_service.local_media_extension_from_url


def download_file(url: str, dest: Path) -> None:
    return media_service.download_file(url, dest, get=requests.get)


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
    from motata_cli.meta.services.migration_state import MigrationStateError

    try:
        result = run_migration_flow(args, commands_module=sys.modules[__name__])
    except MigrationStateError as exc:
        raise CliError(str(exc)) from exc
    print_output(result, as_json=True)


def command_migrate_status(args: argparse.Namespace) -> None:
    from motata_cli.meta.services.migration_state import job_path, load_job, MigrationStateError

    try:
        job = load_job(job_path(JOBS_DIR, args.job_id))
    except MigrationStateError as exc:
        raise CliError(str(exc)) from exc
    print_output(job, as_json=True)


def command_migrate_resume(args: argparse.Namespace) -> None:
    from motata_cli.meta.services.migration_state import job_path, load_job, MigrationStateError

    try:
        job = load_job(job_path(JOBS_DIR, args.job_id))
    except MigrationStateError as exc:
        raise CliError(str(exc)) from exc
    config = job["config"]
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
        _resume=True,
    )
    command_migrate_run(rerun)


def register_meta_commands(subparsers) -> None:
    from motata_cli.meta.cli import register_meta_commands as register_meta_cli_commands

    register_meta_cli_commands(subparsers)
