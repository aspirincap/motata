"""TikTok copy payloads helpers.

Dependencies are explicit and supplied by the CLI compatibility wrappers; this
module never imports the command facade. Source bodies retain their original
formatting and behavior.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..client import TikTokClient


def compact_mapping(values: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != [] and value != {}
    }


def parse_positive_float(value: Any, *, deps: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def max_positive_float(values: list[Any], *, deps: Any) -> float | None:
    parsed_values = [parsed for item in values if (parsed := deps.parse_positive_float(item)) is not None]
    return max(parsed_values) if parsed_values else None


def merge_source_snapshot(summary: dict[str, Any], detail: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    merged = dict(summary)
    merged.update(detail)
    return merged


def get_campaign_automation_type(source_campaign: dict[str, Any], *, deps: Any) -> str | None:
    value = source_campaign.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_campaign_type(source_campaign: dict[str, Any], *, deps: Any) -> bool:
    automation_type = deps.get_campaign_automation_type(source_campaign)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    return bool(source_campaign.get("is_smart_performance_campaign"))


def get_adgroup_automation_type(source_adgroup: dict[str, Any], *, deps: Any) -> str | None:
    value = source_adgroup.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_adgroup_type(
    source_adgroup: dict[str, Any],
    *,
    campaign_smart_plus: bool,
    deps: Any,
) -> bool:
    automation_type = deps.get_adgroup_automation_type(source_adgroup)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    if source_adgroup.get("smart_plus_adgroup_id"):
        return True
    return campaign_smart_plus or bool(source_adgroup.get("is_smart_performance_campaign"))


def get_ad_automation_type(source_ad: dict[str, Any], *, deps: Any) -> str | None:
    value = source_ad.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_ad_type(source_ad: dict[str, Any], *, adgroup_smart_plus: bool, deps: Any) -> bool:
    automation_type = deps.get_ad_automation_type(source_ad)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    if source_ad.get("smart_plus_ad_id"):
        return True
    if any(key in source_ad for key in deps.SMART_PLUS_AD_COPY_FIELDS):
        return True
    return adgroup_smart_plus


def normalize_copy_schedule_fields(payload: dict[str, Any], *, deps: Any) -> None:
    start_time = payload.get("schedule_start_time")
    parsed_start: deps.datetime | None = None
    if isinstance(start_time, str) and start_time.strip():
        try:
            parsed_start = deps.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            parsed_start = None
    if parsed_start is None or parsed_start <= deps.datetime.now():
        payload["schedule_start_time"] = (deps.datetime.now() + deps.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")


def infer_campaign_copy_strategy(
    source_campaign: dict[str, Any],
    source_adgroups: list[dict[str, Any]],
    source_ads: list[dict[str, Any]],
    *,
    smart_plus: bool,
    deps: Any,
) -> dict[str, Any]:
    automation_type = deps.get_campaign_automation_type(source_campaign)
    objective_type = source_campaign.get("objective_type") or source_campaign.get("objective")
    source_campaign_budget = deps.parse_positive_float(source_campaign.get("budget"))
    source_adgroup_budget = deps.max_positive_float([adgroup.get("budget") for adgroup in source_adgroups])
    requested_budget_mode = source_campaign.get("budget_mode")
    effective_budget_mode = requested_budget_mode
    effective_budget = source_campaign_budget

    has_catalog_assets = bool(source_campaign.get("catalog_enabled")) or any(
        adgroup.get("product_source") == "CATALOG"
        or adgroup.get("catalog_id")
        or adgroup.get("catalog_authorized_bc_id")
        for adgroup in source_adgroups
    ) or any(((ad.get("ad_configuration") or {}).get("product_ids") or []) for ad in source_ads)

    used_fallback_campaign_budget = False
    if smart_plus and (
        effective_budget is None
        or str(requested_budget_mode or "").upper() == "BUDGET_MODE_INFINITE"
    ):
        effective_budget_mode = "BUDGET_MODE_TOTAL"
        fallback_budget = source_adgroup_budget or 20.0
        effective_budget = max(fallback_budget, 20.0)
        used_fallback_campaign_budget = True

    force_explicit_ad_schedule = smart_plus and effective_budget_mode == "BUDGET_MODE_TOTAL"
    drop_adgroup_budget_controls = smart_plus and (
        force_explicit_ad_schedule
        or any(str(adgroup.get("budget_mode") or "").startswith("BUDGET_MODE_DYNAMIC") for adgroup in source_adgroups)
    )
    source_is_advanced_dedicated = bool(source_campaign.get("is_advanced_dedicated_campaign"))
    target_smart_plus = smart_plus and source_is_advanced_dedicated

    return {
        "smart_plus": smart_plus,
        "target_smart_plus": target_smart_plus,
        "campaign_automation_type": automation_type,
        "has_catalog_assets": has_catalog_assets,
        "catalog_type": source_campaign.get("catalog_type"),
        "smart_plus_adgroup_mode": source_campaign.get("smart_plus_adgroup_mode"),
        "campaign_budget": effective_budget,
        "campaign_budget_mode": effective_budget_mode,
        "used_fallback_campaign_budget": used_fallback_campaign_budget,
        "force_explicit_ad_schedule": force_explicit_ad_schedule,
        "drop_adgroup_budget_controls": drop_adgroup_budget_controls,
    }


def normalize_smartplus_app_campaign_payload(
    payload: dict[str, Any],
    *,
    fallback_budget: float | None = None,
    deps: Any,
) -> dict[str, Any]:
    normalized = deps.compact_mapping(dict(payload))
    objective_type = str(normalized.get("objective_type") or "").upper()
    app_promotion_type = str(normalized.get("app_promotion_type") or "").upper()

    if objective_type == "APP_PROMOTION":
        # The upgraded Smart+ create endpoint expects iOS app promotion campaigns to be created
        # as advanced dedicated campaigns, and it rejects the generic REGULAR_CAMPAIGN mapping.
        normalized["is_advanced_dedicated_campaign"] = True
        if normalized.get("page_id"):
            normalized["campaign_app_profile_page_state"] = "ON"
        if app_promotion_type in {"", "APP_INSTALL"}:
            normalized["campaign_type"] = "IOS14_CAMPAIGN"

        budget_mode = str(normalized.get("budget_mode") or "").upper()
        budget_value = deps.parse_positive_float(normalized.get("budget"))
        if budget_mode not in deps.SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES:
            normalized["budget_mode"] = "BUDGET_MODE_TOTAL"
        else:
            normalized["budget_mode"] = budget_mode
        if budget_value is None or budget_value <= 0:
            normalized["budget"] = max(fallback_budget or 0.0, 20.0)

        campaign_app_profile_page_state = normalized.get("campaign_app_profile_page_state")
        if isinstance(campaign_app_profile_page_state, str):
            state = campaign_app_profile_page_state.strip().upper()
            if state in {"ON", "OFF"}:
                normalized["campaign_app_profile_page_state"] = state
                if state == "ON" and not normalized.get("page_id"):
                    raise deps.CliError(
                        "campaign_app_profile_page_state=ON requires an App Profile Page ID. "
                        "Pass it with --page-id or page_id in the payload before creating or copying SmartPlus iOS campaigns."
                    )
            else:
                normalized.pop("campaign_app_profile_page_state", None)
        elif campaign_app_profile_page_state is not None:
            normalized.pop("campaign_app_profile_page_state", None)
        normalized.pop("sales_destination", None)
        normalized.pop("optimization_goal", None)

    budget = normalized.get("budget")
    if isinstance(budget, (int, float)) and budget <= 0 and str(normalized.get("budget_mode") or "").upper() != "BUDGET_MODE_INFINITE":
        normalized.pop("budget", None)

    return deps.compact_mapping(normalized)


def infer_adgroup_copy_strategy(
    source_adgroup: dict[str, Any],
    *,
    campaign_strategy: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    smart_plus = deps.is_smart_plus_adgroup_type(
        source_adgroup,
        campaign_smart_plus=bool(campaign_strategy.get("smart_plus")),
    )
    automation_type = deps.get_adgroup_automation_type(source_adgroup) or campaign_strategy.get("campaign_automation_type")
    budget_mode = str(source_adgroup.get("budget_mode") or "").upper()
    has_catalog_assets = bool(campaign_strategy.get("has_catalog_assets")) or source_adgroup.get("product_source") == "CATALOG" or bool(
        source_adgroup.get("catalog_id") or source_adgroup.get("catalog_authorized_bc_id")
    )
    force_explicit_ad_schedule = bool(campaign_strategy.get("force_explicit_ad_schedule"))
    drop_budget_controls = smart_plus and (
        bool(campaign_strategy.get("drop_adgroup_budget_controls"))
        or budget_mode.startswith("BUDGET_MODE_DYNAMIC")
    )
    return {
        "smart_plus": smart_plus,
        "automation_type": automation_type,
        "has_catalog_assets": has_catalog_assets,
        "force_explicit_ad_schedule": force_explicit_ad_schedule,
        "drop_budget_controls": drop_budget_controls,
        "product_source": source_adgroup.get("product_source"),
        "schedule_type": source_adgroup.get("schedule_type"),
    }


def infer_ad_copy_strategy(
    source_ad: dict[str, Any],
    *,
    adgroup_strategy: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    automation_type = deps.get_ad_automation_type(source_ad) or adgroup_strategy.get("automation_type")
    smart_plus = deps.is_smart_plus_ad_type(source_ad, adgroup_smart_plus=bool(adgroup_strategy.get("smart_plus")))
    has_catalog_assets = bool(adgroup_strategy.get("has_catalog_assets")) or bool(
        ((source_ad.get("ad_configuration") or {}).get("product_ids") or [])
    )
    has_tiktok_item = bool(source_ad.get("tiktok_item_id"))
    has_direct_media = bool(source_ad.get("video_id")) or bool(source_ad.get("image_ids"))
    return {
        "smart_plus": smart_plus,
        "automation_type": automation_type,
        "has_catalog_assets": has_catalog_assets,
        "has_product_ids": bool(((source_ad.get("ad_configuration") or {}).get("product_ids") or [])),
        "has_tiktok_item_id": has_tiktok_item,
        "copy_mode": "app_tiktok_item" if (not smart_plus and has_tiktok_item and not has_direct_media) else "default",
        "use_smart_plus_payload": smart_plus,
    }


def sanitize_normal_ad_copy_creative(creative: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    sanitized = deps.compact_mapping(creative)
    for key, value in list(sanitized.items()):
        if isinstance(value, str) and not value.strip():
            sanitized.pop(key, None)
    if sanitized.get("tracking_pixel_id") in (0, "0"):
        sanitized.pop("tracking_pixel_id", None)
    if sanitized.get("viewability_postbid_partner") == "UNSET":
        sanitized.pop("viewability_postbid_partner", None)
    if not sanitized.get("deeplink"):
        sanitized.pop("deeplink", None)
        sanitized.pop("deeplink_type", None)
    if not sanitized.get("landing_page_url"):
        sanitized.pop("landing_page_url", None)
    if not sanitized.get("landing_page_urls"):
        sanitized.pop("landing_page_urls", None)
    if not sanitized.get("playable_url"):
        sanitized.pop("playable_url", None)
    return sanitized


def build_campaign_copy_payload(
    source_campaign: dict[str, Any],
    *,
    advertiser_id: str,
    campaign_name: str,
    operation_status: str | None,
    page_id: str | None = None,
    copy_strategy: dict[str, Any] | None = None,
    deps: Any,
) -> dict[str, Any]:
    copy_strategy = copy_strategy or {}
    payload = deps.compact_mapping(
        {
            "advertiser_id": advertiser_id,
            "request_id": deps.generate_tiktok_request_id(),
            "campaign_name": campaign_name,
            "objective_type": source_campaign.get("objective_type") or source_campaign.get("objective"),
            "app_id": source_campaign.get("app_id"),
            "bid_align_type": source_campaign.get("bid_align_type"),
            "budget": copy_strategy.get("campaign_budget", source_campaign.get("budget")),
            "budget_mode": copy_strategy.get("campaign_budget_mode", source_campaign.get("budget_mode")),
            "budget_optimize_on": source_campaign.get("budget_optimize_on"),
            "operation_status": operation_status or source_campaign.get("operation_status") or "DISABLE",
            "campaign_type": source_campaign.get("campaign_type"),
            "postback_window_mode": source_campaign.get("postback_window_mode"),
            "app_promotion_type": source_campaign.get("app_promotion_type"),
            "disable_skan_campaign": source_campaign.get("disable_skan_campaign"),
            "campaign_app_profile_page_state": source_campaign.get("campaign_app_profile_page_state"),
            "page_id": page_id,
        }
    )
    if copy_strategy.get("target_smart_plus") and copy_strategy.get("has_catalog_assets"):
        payload["catalog_enabled"] = True
        deps.assign_if_present(payload, "catalog_type", copy_strategy.get("catalog_type"))
        deps.assign_if_present(payload, "smart_plus_adgroup_mode", copy_strategy.get("smart_plus_adgroup_mode"))
    special_industries = source_campaign.get("special_industries")
    if isinstance(special_industries, list) and special_industries:
        payload["special_industries"] = special_industries
    if copy_strategy.get("target_smart_plus"):
        return deps.normalize_smartplus_app_campaign_payload(
            payload,
            fallback_budget=copy_strategy.get("campaign_budget"),
        )
    return deps.compact_mapping(payload)


def build_adgroup_copy_payload(
    source_adgroup: dict[str, Any],
    *,
    advertiser_id: str,
    campaign_id: str,
    operation_status: str | None,
    adgroup_strategy: dict[str, Any] | None = None,
    deps: Any,
) -> dict[str, Any]:
    adgroup_strategy = adgroup_strategy or {}
    payload = {
        "advertiser_id": advertiser_id,
        "request_id": deps.generate_tiktok_request_id(),
        "campaign_id": campaign_id,
        "adgroup_name": source_adgroup.get("adgroup_name"),
        "operation_status": operation_status or source_adgroup.get("operation_status") or "DISABLE",
    }
    for key in deps.ADGROUP_COPY_DIRECT_FIELDS:
        if key in source_adgroup:
            payload[key] = source_adgroup.get(key)
    targeting_spec = source_adgroup.get("targeting_spec")
    if isinstance(targeting_spec, dict) and targeting_spec:
        if adgroup_strategy.get("target_smart_plus"):
            payload["targeting_spec"] = targeting_spec
        else:
            payload.update(targeting_spec)
    else:
        for key in deps.ADGROUP_COPY_FALLBACK_TARGETING_FIELDS:
            if key in source_adgroup:
                payload[key] = source_adgroup.get(key)
    if adgroup_strategy.get("target_smart_plus") and adgroup_strategy.get("drop_budget_controls"):
        payload.pop("budget", None)
        payload.pop("budget_mode", None)
    if adgroup_strategy.get("target_smart_plus") and adgroup_strategy.get("force_explicit_ad_schedule"):
        payload["schedule_type"] = "SCHEDULE_START_END"
    if adgroup_strategy.get("smart_plus") and (
        adgroup_strategy.get("has_catalog_assets") or source_adgroup.get("product_source") == "CATALOG"
    ):
        deps.assign_if_present(payload, "catalog_id", source_adgroup.get("catalog_id"))
        deps.assign_if_present(payload, "catalog_authorized_bc_id", source_adgroup.get("catalog_authorized_bc_id"))
        deps.assign_if_present(payload, "product_source", source_adgroup.get("product_source"))
    deps.normalize_copy_schedule_fields(payload)
    return deps.compact_mapping(payload)


def extract_source_ad_id(ad: dict[str, Any], *, smart_plus: bool, deps: Any) -> str:
    if smart_plus:
        ad_id = ad.get("smart_plus_ad_id") or ad.get("ad_id")
    else:
        ad_id = ad.get("ad_id")
    return deps.validate_non_empty(ad_id, "ad_id")


def should_override_landing_page(
    promotion_type: Any,
    existing_urls: list[str],
    override_url: str | None,
    *,
    deps: Any,
) -> bool:
    if not override_url:
        return False
    return bool(existing_urls) or str(promotion_type or "").upper() == "WEBSITE"


def build_normal_ad_copy_payload(
    source_ad: dict[str, Any],
    *,
    advertiser_id: str,
    adgroup_id: str,
    operation_status: str | None,
    landing_page_url: str | None,
    promotion_type: Any,
    client: TikTokClient | None = None,
    source_smart_plus: bool = False,
    ad_strategy: dict[str, Any] | None = None,
    deps: Any,
) -> dict[str, Any]:
    ad_strategy = ad_strategy or {}
    if ad_strategy.get("copy_mode") == "app_tiktok_item":
        creative = deps.sanitize_normal_ad_copy_creative(
            {
                "ad_name": source_ad.get("ad_name"),
                "ad_format": source_ad.get("ad_format"),
                "ad_text": source_ad.get("ad_text"),
                "call_to_action": source_ad.get("call_to_action"),
                "identity_type": source_ad.get("identity_type"),
                "identity_id": source_ad.get("identity_id"),
                "identity_authorized_bc_id": source_ad.get("identity_authorized_bc_id"),
                "tracking_app_id": source_ad.get("tracking_app_id"),
                "tracking_offline_event_set_ids": source_ad.get("tracking_offline_event_set_ids"),
                "operation_status": operation_status or source_ad.get("operation_status") or "ENABLE",
                "promotional_music_disabled": source_ad.get("promotional_music_disabled"),
                "tiktok_item_id": source_ad.get("tiktok_item_id"),
                "display_name": source_ad.get("display_name"),
                "app_name": source_ad.get("app_name"),
            }
        )
        return {
            "advertiser_id": advertiser_id,
            "adgroup_id": adgroup_id,
            "creatives": [creative],
        }

    if source_smart_plus:
        creative_info = deps.first_dict(source_ad.get("creative_list") or []) or {}
        creative_info = creative_info.get("creative_info") if isinstance(creative_info, dict) else {}
        creative_info = creative_info if isinstance(creative_info, dict) else {}
        ad_configuration = source_ad.get("ad_configuration") if isinstance(source_ad.get("ad_configuration"), dict) else {}
        tracking_info = ad_configuration.get("tracking_info") if isinstance(ad_configuration.get("tracking_info"), dict) else {}
        ad_text_list = source_ad.get("ad_text_list") if isinstance(source_ad.get("ad_text_list"), list) else []
        ad_text = None
        if ad_text_list:
            first_text = deps.first_dict([item for item in ad_text_list if isinstance(item, dict)])
            if first_text:
                ad_text = first_text.get("ad_text")

        video_info = creative_info.get("video_info") if isinstance(creative_info.get("video_info"), dict) else {}
        video_ids = deps.extract_video_ids(source_ad, smart_plus=True)
        image_ids = deps.extract_image_ids(source_ad, smart_plus=True)
        if not image_ids and client and video_ids:
            video_id = video_ids[0]
            try:
                video_info_response = client.get_video_info(advertiser_id, [video_id])
                video_entries = video_info_response.get("data", {}).get("list", [])
                first_video = deps.first_dict([item for item in video_entries if isinstance(item, dict)])
                cover_url = first_video.get("video_cover_url") if first_video else None
                if isinstance(cover_url, str) and cover_url.strip():
                    uploaded = client.upload_image(
                        advertiser_id,
                        file_path=None,
                        file_name=f"{video_id}.jpg",
                        image_url=cover_url,
                        upload_type="UPLOAD_BY_URL",
                    )
                    uploaded_image_id = uploaded.get("data", {}).get("image_id")
                    if isinstance(uploaded_image_id, str) and uploaded_image_id.strip():
                        image_ids = [uploaded_image_id.strip()]
            except deps.CliError:
                image_ids = []
        if not image_ids:
            raise deps.CliError(
                "Unable to prepare a normal ad creative from SmartPlus source: missing image asset. "
                "The video cover upload did not return an image_id."
            )

        creative = deps.sanitize_normal_ad_copy_creative(
            {
                "ad_name": source_ad.get("ad_name"),
                "ad_format": creative_info.get("ad_format") or source_ad.get("ad_format"),
                "ad_text": ad_text or source_ad.get("ad_text"),
                "call_to_action_id": ad_configuration.get("call_to_action_id"),
                "dark_post_status": ad_configuration.get("dark_post_status") or source_ad.get("dark_post_status"),
                "identity_type": creative_info.get("identity_type") or source_ad.get("identity_type"),
                "identity_id": creative_info.get("identity_id") or source_ad.get("identity_id"),
                "identity_authorized_bc_id": creative_info.get("identity_authorized_bc_id")
                or source_ad.get("identity_authorized_bc_id"),
                "tracking_app_id": tracking_info.get("tracking_app_id") or source_ad.get("tracking_app_id"),
                "tracking_offline_event_set_ids": tracking_info.get("tracking_offline_event_set_ids")
                or source_ad.get("tracking_offline_event_set_ids"),
                "tracking_pixel_id": tracking_info.get("tracking_pixel_id") or source_ad.get("tracking_pixel_id"),
                "video_id": video_ids[0] if video_ids else video_info.get("video_id"),
                "image_ids": image_ids,
                "operation_status": operation_status or source_ad.get("operation_status") or "ENABLE",
                "promotional_music_disabled": source_ad.get("promotional_music_disabled"),
                "display_name": source_ad.get("display_name"),
                "app_name": source_ad.get("app_name"),
            }
        )
        creative["image_ids"] = image_ids
    else:
        creative = {
            key: source_ad.get(key)
            for key in deps.NORMAL_AD_UPDATE_AUTOFILL_FIELDS
            if key in source_ad
        }
        if not creative.get("image_ids") and client:
            video_ids = deps.extract_video_ids(source_ad, smart_plus=False)
            if video_ids:
                try:
                    video_info_response = client.get_video_info(advertiser_id, [video_ids[0]])
                    video_entries = video_info_response.get("data", {}).get("list", [])
                    first_video = deps.first_dict([item for item in video_entries if isinstance(item, dict)])
                    cover_url = first_video.get("video_cover_url") if first_video else None
                    if isinstance(cover_url, str) and cover_url.strip():
                        uploaded = client.upload_image(
                            advertiser_id,
                            file_path=None,
                            file_name=f"{video_ids[0]}.jpg",
                            image_url=cover_url,
                            upload_type="UPLOAD_BY_URL",
                        )
                        uploaded_image_id = uploaded.get("data", {}).get("image_id")
                        if isinstance(uploaded_image_id, str) and uploaded_image_id.strip():
                            creative["image_ids"] = [uploaded_image_id.strip()]
                except deps.CliError:
                    pass
    creative["operation_status"] = operation_status or source_ad.get("operation_status") or "ENABLE"
    if deps.should_override_landing_page(
        promotion_type,
        deps.extract_landing_page_urls(creative, smart_plus=False),
        landing_page_url,
    ):
        if creative.get("landing_page_urls"):
            creative["landing_page_urls"] = [landing_page_url]
            creative.pop("landing_page_url", None)
        else:
            creative["landing_page_url"] = landing_page_url
            creative.pop("landing_page_urls", None)
    payload = {
        "advertiser_id": advertiser_id,
        "adgroup_id": adgroup_id,
        "creatives": [deps.sanitize_normal_ad_copy_creative(creative)],
    }
    return payload


def build_smart_plus_ad_copy_payload(
    source_ad: dict[str, Any],
    *,
    advertiser_id: str,
    adgroup_id: str,
    operation_status: str | None,
    landing_page_url: str | None,
    promotion_type: Any,
    ad_strategy: dict[str, Any] | None = None,
    deps: Any,
) -> dict[str, Any]:
    ad_strategy = ad_strategy or {}
    payload = {
        "advertiser_id": advertiser_id,
        "adgroup_id": adgroup_id,
        "ad_name": source_ad.get("ad_name"),
        "operation_status": operation_status or source_ad.get("operation_status") or "ENABLE",
    }
    for key in deps.SMART_PLUS_AD_COPY_FIELDS:
        if key in source_ad:
            payload[key] = source_ad.get(key)
    if deps.should_override_landing_page(
        promotion_type,
        deps.extract_landing_page_urls(payload, smart_plus=True),
        landing_page_url,
    ):
        payload["landing_page_url_list"] = [{"landing_page_url": landing_page_url}]
    return deps.compact_mapping(payload)
