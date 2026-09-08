"""TikTok normalization helpers.

Dependencies are explicit and supplied by the CLI compatibility wrappers; this
module never imports the command facade. Source bodies retain their original
formatting and behavior.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..client import TikTokClient


def extract_response_list(payload: dict[str, Any], *keys: str, deps: Any) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def extract_response_strings(payload: dict[str, Any], *keys: str, deps: Any) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is not None:
            text = str(value).strip()
            if text:
                return [text]
    return []


def ensure_task_create_response(response: dict[str, Any], *, label: str, deps: Any) -> dict[str, Any]:
    task_ids = deps.extract_response_strings(response, "task_ids")
    if not task_ids:
        task_ids = [
            str(item.get("task_id")).strip()
            for item in deps.extract_response_list(response, "list")
            if isinstance(item, dict) and str(item.get("task_id") or "").strip()
        ]
    if task_ids:
        return response
    request_id = response.get("request_id")
    message = response.get("message") or response.get("msg") or "empty task_ids"
    raise deps.CliError(f"{label} did not return any task_ids (request_id={request_id}, message={message})")


def filter_response_items_by_ids(
    response: dict[str, Any],
    *,
    list_key: str,
    item_key: str,
    allowed_ids: set[str],
    deps: Any,
) -> dict[str, Any]:
    if not allowed_ids:
        return response
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    items = data.get(list_key)
    if not isinstance(items, list):
        return response
    filtered = [item for item in items if isinstance(item, dict) and str(item.get(item_key) or "").strip() in allowed_ids]
    if len(filtered) == len(items):
        return response
    page_info = data.get("page_info")
    if isinstance(page_info, dict):
        page_info = dict(page_info)
        page_info["total_number"] = len(filtered)
        page_info["total_page"] = 1 if filtered else 0
    new_data = dict(data)
    new_data[list_key] = filtered
    if page_info is not None:
        new_data["page_info"] = page_info
    result = dict(response)
    result["data"] = new_data
    return result


def filter_response_items_by_values(
    response: dict[str, Any],
    *,
    list_key: str,
    item_key: str,
    allowed_values: set[str],
    deps: Any,
) -> dict[str, Any]:
    normalized_values = {value for value in allowed_values if value}
    if not normalized_values:
        return response
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    items = data.get(list_key)
    if not isinstance(items, list):
        return response
    filtered = [item for item in items if isinstance(item, dict) and str(item.get(item_key) or "").strip() in normalized_values]
    if len(filtered) == len(items):
        return response
    page_info = data.get("page_info")
    if isinstance(page_info, dict):
        page_info = dict(page_info)
        page_info["total_number"] = len(filtered)
        page_info["total_page"] = 1 if filtered else 0
    new_data = dict(data)
    new_data[list_key] = filtered
    if page_info is not None:
        new_data["page_info"] = page_info
    result = dict(response)
    result["data"] = new_data
    return result


def filter_aigc_video_list_response(
    response: dict[str, Any],
    *,
    client: TikTokClient,
    advertiser_id: str,
    aigc_video_type: str,
    task_ids: list[str] | None,
    deps: Any,
) -> dict[str, Any]:
    normalized_task_ids = {str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()}
    if not normalized_task_ids:
        return response
    task_response = client.list_aigc_video_tasks(
        advertiser_id,
        aigc_video_type=aigc_video_type,
        task_ids=sorted(normalized_task_ids),
        page=1,
        page_size=max(len(normalized_task_ids), 20),
    )
    task_items = deps.extract_response_list(task_response, "list")
    allowed_video_ids = {
        str(item.get("video_id")).strip()
        for item in task_items
        if isinstance(item, dict) and str(item.get("task_id") or "").strip() in normalized_task_ids and item.get("video_id")
    }
    return deps.filter_response_items_by_ids(response, list_key="list", item_key="video_id", allowed_ids=allowed_video_ids)


def filter_digital_avatar_video_list_response(
    response: dict[str, Any],
    *,
    client: TikTokClient,
    advertiser_id: str,
    task_ids: list[str] | None,
    deps: Any,
) -> dict[str, Any]:
    normalized_task_ids = [str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()]
    if not normalized_task_ids:
        return response
    allowed_avatar_video_ids: set[str] = set()
    allowed_video_names: set[str] = set()
    for task_id in normalized_task_ids:
        task_response = client.get_digital_avatar_video_task(advertiser_id, task_id)
        task_items = deps.extract_response_list(task_response, "list")
        for item in task_items:
            if not isinstance(item, dict):
                continue
            avatar_video_id = str(item.get("avatar_video_id") or "").strip()
            video_name = str(item.get("video_name") or "").strip()
            if avatar_video_id:
                allowed_avatar_video_ids.add(avatar_video_id)
            if video_name:
                allowed_video_names.add(video_name)
    if allowed_avatar_video_ids:
        return deps.filter_response_items_by_ids(response, list_key="list", item_key="avatar_video_id", allowed_ids=allowed_avatar_video_ids)
    return deps.filter_response_items_by_values(response, list_key="list", item_key="video_name", allowed_values=allowed_video_names)


def build_validation_result(
    kind: str,
    advertiser_id: str,
    *,
    smart_plus: bool = False,
    deps: Any,
) -> dict[str, Any]:
    return {
        "ok": True,
        "kind": kind,
        "advertiser_id": advertiser_id,
        "smart_plus": smart_plus,
        "errors": [],
        "warnings": [],
        "checks": [],
    }


def add_validation_error(
    result: dict[str, Any],
    code: str,
    message: str,
    *,
    deps: Any,
    **context: Any,
) -> None:
    issue = {"code": code, "message": message}
    if context:
        issue["context"] = context
    result["errors"].append(issue)
    result["ok"] = False


def add_validation_warning(
    result: dict[str, Any],
    code: str,
    message: str,
    *,
    deps: Any,
    **context: Any,
) -> None:
    issue = {"code": code, "message": message}
    if context:
        issue["context"] = context
    result["warnings"].append(issue)


def add_validation_check(result: dict[str, Any], name: str, ok: bool, *, deps: Any, **details: Any) -> None:
    item = {"name": name, "ok": ok}
    if details:
        item["details"] = details
    result["checks"].append(item)
    if not ok:
        result["ok"] = False


def first_dict(items: list[dict[str, Any]], *, deps: Any) -> dict[str, Any] | None:
    return items[0] if items else None


def collect_strings(value: Any, *, deps: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, dict):
        results: list[str] = []
        for item in value.values():
            results.extend(deps.collect_strings(item))
        return results
    if isinstance(value, list):
        results: list[str] = []
        for item in value:
            results.extend(deps.collect_strings(item))
        return results
    return []


def dedupe_strings(values: list[str], *, deps: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_int(value: Any, *, deps: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_text(value: Any, *, deps: Any) -> str:
    return str(value).strip().casefold() if value is not None else ""


def resolve_tiktok_app_promotion_type(platform: Any, *, deps: Any) -> str | None:
    platform_value = deps.normalize_text(platform)
    if platform_value == "android":
        return "APP_ANDROID"
    if platform_value == "ios":
        return "APP_IOS"
    return None


def summarize_tiktok_template_campaign(campaign: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "campaign_id": campaign.get("campaign_id"),
            "campaign_name": campaign.get("campaign_name"),
            "objective_type": campaign.get("objective_type"),
            "app_promotion_type": campaign.get("app_promotion_type"),
            "budget": campaign.get("budget"),
            "budget_mode": campaign.get("budget_mode"),
            "operation_status": campaign.get("operation_status"),
            "smart_plus_adgroup_mode": campaign.get("smart_plus_adgroup_mode"),
            "create_time": campaign.get("create_time"),
            "modify_time": campaign.get("modify_time"),
        }
    )


def summarize_tiktok_template_adgroup(adgroup: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "adgroup_id": adgroup.get("adgroup_id"),
            "adgroup_name": adgroup.get("adgroup_name"),
            "campaign_id": adgroup.get("campaign_id"),
            "app_id": adgroup.get("app_id"),
            "promotion_type": adgroup.get("promotion_type"),
            "optimization_goal": adgroup.get("optimization_goal"),
            "optimization_event": adgroup.get("optimization_event"),
            "budget": adgroup.get("budget"),
            "budget_mode": adgroup.get("budget_mode"),
            "placements": adgroup.get("placements"),
            "operation_status": adgroup.get("operation_status"),
            "create_time": adgroup.get("create_time"),
            "modify_time": adgroup.get("modify_time"),
        }
    )


def summarize_tiktok_template_ad(ad: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    tracking_info = ((ad.get("ad_configuration") or {}).get("tracking_info") or {}) if isinstance(ad, dict) else {}
    creative = deps.first_dict(ad.get("creative_list") or []) or {}
    creative_info = creative.get("creative_info") or {}
    video_info = creative_info.get("video_info") or {}
    return deps.compact_mapping(
        {
            "smart_plus_ad_id": ad.get("smart_plus_ad_id"),
            "ad_name": ad.get("ad_name"),
            "tracking_app_id": tracking_info.get("tracking_app_id"),
            "tracking_offline_event_set_ids": tracking_info.get("tracking_offline_event_set_ids"),
            "call_to_action_id": (ad.get("ad_configuration") or {}).get("call_to_action_id"),
            "identity_id": creative_info.get("identity_id"),
            "identity_type": creative_info.get("identity_type"),
            "identity_authorized_bc_id": creative_info.get("identity_authorized_bc_id"),
            "video_id": video_info.get("video_id"),
            "material_name": creative_info.get("material_name"),
            "operation_status": ad.get("operation_status"),
            "create_time": ad.get("create_time"),
            "modify_time": ad.get("modify_time"),
        }
    )


def select_tiktok_app(
    apps: list[dict[str, Any]],
    *,
    app_id: str | None = None,
    app_name: str | None = None,
    deps: Any,
) -> dict[str, Any] | None:
    if app_id:
        for app in apps:
            if str(app.get("app_id")) == str(app_id):
                return app
        return None
    if app_name:
        target = deps.normalize_text(app_name)
        exact_matches = [app for app in apps if deps.normalize_text(app.get("app_name")) == target]
        if exact_matches:
            return exact_matches[0]
        partial_matches = [app for app in apps if target in deps.normalize_text(app.get("app_name"))]
        if partial_matches:
            return partial_matches[0]
        return None
    return None


def extract_tiktok_creative_portfolio_contents(
    portfolio: dict[str, Any],
    *,
    deps: Any,
) -> list[dict[str, Any]]:
    contents = portfolio.get("portfolio_content")
    if isinstance(contents, list):
        return [item for item in contents if isinstance(item, dict)]
    if isinstance(contents, dict):
        return [contents]
    legacy_content = portfolio.get("creative_portfolio_content")
    if isinstance(legacy_content, dict):
        return [legacy_content]
    return []


def summarize_tiktok_creative_portfolio_content(content: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "title": content.get("title") or content.get("ad_text"),
            "card_title": content.get("card_title"),
            "primary_text": content.get("primary_text"),
            "secondary_text": content.get("secondary_text"),
            "description": content.get("description"),
            "asset_content": content.get("asset_content"),
            "identity_id": content.get("identity_id"),
            "identity_type": content.get("identity_type"),
            "identity_authorized_bc_id": content.get("identity_authorized_bc_id"),
            "app_id": content.get("app_id"),
            "origin_app_id": content.get("origin_app_id"),
            "image_id": content.get("image_id"),
            "jump_image_uri": content.get("jump_image_uri"),
            "thumbnail_id": content.get("thumbnail_id"),
            "video_id": (content.get("advanced_audio_info") or {}).get("video_id") if isinstance(content.get("advanced_audio_info"), dict) else None,
            "call_to_action": content.get("call_to_action"),
            "advanced_guide_text_bottom": content.get("advanced_guide_text_bottom"),
            "card_type": content.get("card_type"),
            "content_url": content.get("content_url"),
            "product_source": content.get("product_source"),
            "product_set_id": content.get("product_set_id"),
            "product_platform_id": content.get("product_platform_id"),
            "product_specific_type": content.get("product_specific_type"),
            "catalog_authorized_bc_id": content.get("catalog_authorized_bc_id"),
            "store_id": content.get("store_id"),
            "shop_id": content.get("shop_id"),
            "shop_authorized_bc_id": content.get("shop_authorized_bc_id"),
            "catalog_id": content.get("catalog_id"),
            "category_label": content.get("category_label"),
            "card_show_price": content.get("card_show_price"),
            "card_image_index": content.get("card_image_index"),
            "display_price_enabled": content.get("display_price_enabled"),
            "image_optimization_enabled": content.get("image_optimization_enabled"),
            "gesture_type": content.get("gesture_type"),
            "interactive_music_id": content.get("interactive_music_id"),
            "vertical_creative_strategy": content.get("vertical_creative_strategy"),
            "vertical_video_strategy": content.get("vertical_video_strategy"),
            "slide_length": content.get("slide_length"),
            "slide_dimension": content.get("slide_dimension"),
            "advanced_show_time": content.get("advanced_show_time"),
            "advanced_interact_type": content.get("advanced_interact_type"),
            "advanced_interact_shape": content.get("advanced_interact_shape"),
            "advanced_position": content.get("advanced_position"),
            "advanced_image_info": content.get("advanced_image_info"),
            "advanced_gesture_icon": content.get("advanced_gesture_icon"),
            "advanced_gesture_image": content.get("advanced_gesture_image"),
            "advanced_audio_info": content.get("advanced_audio_info"),
            "sticker_param": content.get("sticker_param"),
            "showcase_spu_list": content.get("showcase_spu_list"),
            "layouts": content.get("layouts"),
            "country_code": content.get("country_code"),
            "display_price_info": content.get("display_price_info"),
            "enable_image_optimization": content.get("enable_image_optimization"),
            "tags": content.get("tags"),
            "card_tags": content.get("card_tags"),
            "selling_points": content.get("selling_points"),
        }
    )


def summarize_tiktok_creative_portfolio(portfolio: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    contents = deps.extract_tiktok_creative_portfolio_contents(portfolio)
    first_content = deps.first_dict(contents) or {}
    content_summary = deps.summarize_tiktok_creative_portfolio_content(first_content) if first_content else None
    return deps.compact_mapping(
        {
            "creative_portfolio_id": portfolio.get("creative_portfolio_id"),
            "creative_portfolio_type": portfolio.get("creative_portfolio_type"),
            "creative_portfolio_preview_url": portfolio.get("creative_portfolio_preview_url"),
            "title": portfolio.get("title") or first_content.get("title") or first_content.get("ad_text"),
            "primary_text": portfolio.get("primary_text") or first_content.get("primary_text"),
            "secondary_text": portfolio.get("secondary_text") or first_content.get("secondary_text"),
            "content_count": len(contents),
            "content_summary": content_summary,
            "modify_time": portfolio.get("modify_time"),
            "create_time": portfolio.get("create_time"),
        }
    )


def normalize_validate_creatives(
    payload: dict[str, Any],
    *,
    smart_plus: bool,
    deps: Any,
) -> list[dict[str, Any]]:
    if smart_plus:
        return [payload] if payload else []
    creatives = payload.get("creatives")
    if isinstance(creatives, list):
        return [item for item in creatives if isinstance(item, dict)]
    if payload:
        return [payload]
    return []


def extract_identity_refs(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> dict[str, str | None]:
    source = creative
    if smart_plus:
        creative_list = creative.get("creative_list")
        if isinstance(creative_list, list) and creative_list:
            creative_info = (creative_list[0] or {}).get("creative_info")
            if isinstance(creative_info, dict):
                source = creative_info
    return {
        "identity_id": source.get("identity_id"),
        "identity_type": source.get("identity_type"),
        "identity_authorized_bc_id": source.get("identity_authorized_bc_id"),
    }


def extract_video_ids(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> list[str]:
    if smart_plus:
        video_ids: list[str] = []
        for item in creative.get("creative_list") or []:
            creative_info = (item or {}).get("creative_info") or {}
            video_info = creative_info.get("video_info") or {}
            video_id = video_info.get("video_id")
            if isinstance(video_id, str) and video_id.strip():
                video_ids.append(video_id.strip())
        return deps.dedupe_strings(video_ids)
    return deps.non_empty_list([creative.get("video_id")])


def extract_image_ids(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> list[str]:
    if smart_plus:
        image_ids: list[str] = []
        for item in creative.get("creative_list") or []:
            creative_info = (item or {}).get("creative_info") or {}
            for image_info in creative_info.get("image_info") or []:
                if isinstance(image_info, dict):
                    image_id = image_info.get("image_id")
                    if isinstance(image_id, str) and image_id.strip():
                        image_ids.append(image_id.strip())
        return deps.dedupe_strings(image_ids)
    return deps.non_empty_list(creative.get("image_ids"))


def extract_image_web_uris(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> list[str]:
    if not smart_plus:
        return []
    image_web_uris: list[str] = []
    for item in creative.get("creative_list") or []:
        creative_info = (item or {}).get("creative_info") or {}
        for image_info in creative_info.get("image_info") or []:
            if isinstance(image_info, dict):
                web_uri = image_info.get("web_uri")
                if isinstance(web_uri, str) and web_uri.strip():
                    image_web_uris.append(web_uri.strip())
    return deps.dedupe_strings(image_web_uris)


def extract_landing_page_urls(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> list[str]:
    values: list[str] = []
    if smart_plus:
        for item in creative.get("landing_page_url_list") or []:
            if isinstance(item, dict):
                values.extend(deps.collect_strings(item.get("landing_page_url")))
    else:
        values.extend(deps.collect_strings(creative.get("landing_page_url")))
        values.extend(deps.collect_strings(creative.get("landing_page_urls")))
    return deps.dedupe_strings([value for value in values if deps.looks_like_url(value)])


def extract_tracking_refs(creative: dict[str, Any], *, smart_plus: bool, deps: Any) -> dict[str, Any]:
    if smart_plus:
        tracking_info = ((creative.get("ad_configuration") or {}).get("tracking_info") or {})
        return {
            "tracking_app_id": tracking_info.get("tracking_app_id"),
            "tracking_pixel_id": tracking_info.get("tracking_pixel_id"),
            "tracking_offline_event_set_ids": deps.non_empty_list(tracking_info.get("tracking_offline_event_set_ids")),
        }
    return {
        "tracking_app_id": creative.get("tracking_app_id"),
        "tracking_pixel_id": creative.get("tracking_pixel_id"),
        "tracking_offline_event_set_ids": deps.non_empty_list(creative.get("tracking_offline_event_set_ids")),
    }


def derive_bc_ids_from_assets(
    identities: list[dict[str, Any]],
    stores: list[dict[str, Any]],
    *,
    deps: Any,
) -> list[str]:
    values: list[str] = []
    for identity in identities:
        value = identity.get("identity_authorized_bc_id")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    for store in stores:
        value = store.get("store_authorized_bc_id")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return deps.dedupe_strings(values)


def summarize_tiktok_identity(identity: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "identity_id": identity.get("identity_id"),
            "identity_type": identity.get("identity_type"),
            "display_name": identity.get("display_name"),
            "username": identity.get("username"),
            "available_status": identity.get("available_status"),
            "identity_authorized_bc_id": identity.get("identity_authorized_bc_id"),
            "can_pull_video": identity.get("can_pull_video"),
            "can_push_video": identity.get("can_push_video"),
            "can_manage_message": identity.get("can_manage_message"),
            "ads_only_mode": identity.get("ads_only_mode"),
        }
    )


def summarize_tiktok_pixel_event(event: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    rules = event.get("rules") or []
    return deps.compact_mapping(
        {
            "event_id": event.get("event_id"),
            "event_code": event.get("event_code"),
            "event_type": event.get("event_type"),
            "optimization_event": event.get("optimization_event"),
            "statistic_type": event.get("statistic_type"),
            "currency": event.get("currency"),
            "currency_value": event.get("currency_value"),
            "deprecated": event.get("deprecated"),
            "rule_count": len(rules),
            "rules": [
                deps.compact_mapping(
                    {
                        "trigger": rule.get("trigger"),
                        "operator": rule.get("operator"),
                        "value": rule.get("value"),
                        "variable": rule.get("variable"),
                    }
                )
                for rule in rules
            ]
            if rules
            else None,
        }
    )


def summarize_tiktok_pixel(pixel: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    ownership = pixel.get("asset_ownership") or {}
    events = pixel.get("events") or []
    return deps.compact_mapping(
        {
            "pixel_id": pixel.get("pixel_id"),
            "pixel_name": pixel.get("pixel_name"),
            "pixel_code": pixel.get("pixel_code"),
            "activity_status": pixel.get("activity_status"),
            "pixel_setup_mode": pixel.get("pixel_setup_mode"),
            "partner_name": pixel.get("partner_name"),
            "pixel_category": pixel.get("pixel_category"),
            "event_count": len(events),
            "events": [deps.summarize_tiktok_pixel_event(e) for e in events],
            "enable_first_party_cookies": pixel.get("enable_first_party_cookies"),
            "enable_expanded_data_sharing": pixel.get("enable_expanded_data_sharing"),
            "owner_bc_id": ownership.get("owner_bc_id"),
            "ownership_status": ownership.get("ownership_status"),
            "asset_relation_status": ownership.get("asset_relation_status"),
        }
    )


def summarize_tiktok_offline_event_set(event_set: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "event_set_id": event_set.get("event_set_id"),
            "name": event_set.get("name"),
            "advertiser_id": event_set.get("advertiser_id"),
            "auto_tracking": event_set.get("auto_tracking"),
            "create_time": event_set.get("create_time"),
            "update_time": event_set.get("update_time"),
        }
    )


def summarize_tiktok_app(app: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "app_id": app.get("app_id"),
            "app_name": app.get("app_name"),
            "platform": app.get("platform"),
            "package_name": app.get("package_name"),
            "app_platform_id": app.get("app_platform_id"),
            "download_url": app.get("download_url"),
            "enable_retargeting": app.get("enable_retargeting"),
            "skan_allowed": app.get("skan_allowed"),
            "advanced_dedicated_campaign_allowed": app.get("advanced_dedicated_campaign_allowed"),
        }
    )


def summarize_tiktok_store(store: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return deps.compact_mapping(
        {
            "store_id": store.get("store_id"),
            "store_name": store.get("store_name"),
            "store_status": store.get("store_status"),
            "store_role": store.get("store_role"),
            "store_code": store.get("store_code"),
            "store_authorized_bc_id": store.get("store_authorized_bc_id"),
            "is_gmv_max_available": store.get("is_gmv_max_available"),
            "is_owner_bc": store.get("is_owner_bc"),
            "targeting_region_codes": store.get("targeting_region_codes"),
        }
    )


def summarize_tiktok_catalog(catalog: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    catalog_conf = catalog.get("catalog_conf") or {}
    bc_info = catalog.get("bc_info") or {}
    return deps.compact_mapping(
        {
            "catalog_id": catalog.get("catalog_id"),
            "catalog_name": catalog.get("catalog_name"),
            "catalog_type": catalog.get("catalog_type"),
            "ad_creation_eligible": catalog.get("ad_creation_eligible"),
            "country": catalog_conf.get("country"),
            "currency": catalog_conf.get("currency"),
            "bc_id": bc_info.get("bc_id"),
            "bc_name": bc_info.get("bc_name"),
            "create_time": catalog.get("create_time"),
            "update_time": catalog.get("update_time"),
        }
    )
