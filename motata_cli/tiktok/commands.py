from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from motata_cli.common.errors import CliError
from motata_cli.common.auth import resolve_auth
from motata_cli.common.utils import load_json_file, parse_json_option, write_json_file, validate_non_empty
from motata_cli.common.display import print_output
from motata_cli.product import scrape_product

from .client import TikTokClient
from .app_discovery import (
    build_tiktok_app_report,
    discover_recent_spend_advertisers,
)
from .activities import build_tiktok_activities_report
from .audience import build_tiktok_audience_breakdown
from .creative_retention import build_tiktok_creative_retention_report
from .landing_pages import (
    build_tiktok_landing_page_report,
    default_date_range,
    discover_advertiser_ids,
)
from .metrics import build_tiktok_metric_probe
from .user_type import build_tiktok_user_type_report
from .item_resolver import item_id_from_value, resolve_items

from types import SimpleNamespace
from . import payloads as _payloads
from .services import copy_payloads as _copy_payloads
from .services import normalization as _normalization
from .services import discovery as _discovery


# TikTok Call To Action (CTA) enum values
# Source: TikTok Business API SDK yml_files/smart_plus_ad_create.yml
TIKTOK_CTA_ACO_LIST = [
    "APPLY_NOW",
    "BOOK_NOW",
    "CALL_NOW",
    "CHECK_AVAILABILITY",
    "CONTACT_US",
    "DOWNLOAD_NOW",
    "EXPERIENCE_NOW",
    "GET_QUOTE",
    "GET_SHOWTIMES",
    "GET_TICKETS_NOW",
    "INSTALL_NOW",
    "INTERESTED",
    "JOIN_THIS_HASHTAG",
    "LEARN_MORE",
    "LISTEN_NOW",
    "ORDER_NOW",
    "PLAY_GAME",
    "PREORDER_NOW",
    "READ_MORE",
    "SEND_MESSAGE",
    "SHOOT_WITH_THIS_EFFECT",
    "SHOP_NOW",
    "SIGN_UP",
    "SUBSCRIBE",
    "VIEW_NOW",
    "VIEW_PROFILE",
    "VIEW_VIDEO_WITH_THIS_EFFECT",
    "VISIT_STORE",
    "WATCH_LIVE",
    "WATCH_NOW",
]

TIKTOK_CTA_SMART_PLUS_LIST = [
    "BOOK_NOW",
    "CONTACT_US",
    "LEARN_MORE",
    "ORDER_NOW",
    "READ_MORE",
    "SHOP_NOW",
    "SIGN_UP",
    "VIEW_MORE",
]

TIKTOK_CTA_ALL = set(TIKTOK_CTA_ACO_LIST + TIKTOK_CTA_SMART_PLUS_LIST)
TIKTOK_AIGC_VIDEO_TYPE_CHOICES = [
    "VOICEOVER",
    "AVATAR_PRODUCT",
    "TRYON",
]
TIKTOK_DIGITAL_AVATAR_IDENTITY_CHOICES = ["real", "aigc"]
TIKTOK_IMAGE_ANIMATION_PROVIDER_CHOICES = ["GOKU", "GEN_4_Turbo"]
TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES = ["0.7x", "0.8x", "0.9x", "1.0x", "1.1x", "1.2x"]
TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS = ["cost", "orders", "cost_per_order", "gross_revenue", "roi", "net_cost"]
TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS = [
    "roas_bid",
    "cost",
    "net_cost",
    "orders",
    "cost_per_order",
    "gross_revenue",
    "roi",
]
TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS = ["product_status", "orders", "gross_revenue"]
TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS = [
    "creative_delivery_status",
    "cost",
    "orders",
    "cost_per_order",
    "gross_revenue",
    "roi",
    "product_impressions",
    "product_clicks",
    "product_click_rate",
    "ad_click_rate",
    "ad_conversion_rate",
    "ad_video_view_rate_2s",
    "ad_video_view_rate_6s",
    "ad_video_view_rate_p25",
    "ad_video_view_rate_p50",
    "ad_video_view_rate_p75",
    "ad_video_view_rate_p100",
]
TIKTOK_GMV_MAX_DURATION_REPORT_METRICS = ["cost", "orders", "cost_per_order", "gross_revenue", "roi", "roas_bid"]
TIKTOK_GMV_MAX_REPORT_DIMENSION_CHOICES = [
    "advertiser_id",
    "campaign_id",
    "item_group_id",
    "item_id",
    "room_id",
    "duration",
    "stat_time_day",
    "stat_time_hour",
]
TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS = {
    "account": ["advertiser_id"],
    "campaign": ["campaign_id"],
    "product": ["item_group_id"],
    "creative": ["campaign_id", "item_group_id", "item_id"],
    "live": ["room_id"],
    "duration": ["duration"],
}
TIKTOK_GMV_MAX_REPORT_LEVEL_METRICS = {
    "account": TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS,
    "campaign": TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS,
    "product": TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS,
    "creative": TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS,
    "duration": TIKTOK_GMV_MAX_DURATION_REPORT_METRICS,
    "live": TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS,
}
TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS = {
    "none": [],
    "day": ["stat_time_day"],
    "hour": ["stat_time_hour"],
}
TIKTOK_GMV_MAX_ITEM_SCOPE_PRODUCT_CARD = "PRODUCT_CARD"
TIKTOK_GMV_MAX_ITEM_SCOPE_SPECIFIC_ITEM = "SPECIFIC_ITEM"


def add_tiktok_auth_arguments(parser: argparse.ArgumentParser, *, needs_advertiser: bool = True) -> None:
    if needs_advertiser:
        parser.add_argument("--advertiser-id", "--account-id", "--account", dest="advertiser_id", required=True)
    else:
        parser.add_argument("--advertiser-id", "--account-id", "--account", dest="advertiser_id")
    parser.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    parser.add_argument("--json", action="store_true", default=True)


def add_payload_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--payload-json")
    parser.add_argument("--payload-file")


def add_filtering_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--filtering-json", dest="payload_json")
    parser.add_argument("--filtering-file", dest="payload_file")


def load_payload(args: argparse.Namespace, *, label: str = "payload") -> dict[str, Any]:
    payload_json = getattr(args, "payload_json", None)
    payload_file = getattr(args, "payload_file", None)
    if payload_json and payload_file:
        raise CliError(f"Specify only one of --payload-json or --payload-file for {label}")
    if payload_file:
        payload = load_json_file(Path(payload_file))
        if not isinstance(payload, dict):
            raise CliError(f"Invalid {label}: expected JSON object in {payload_file}")
        return payload
    payload = parse_json_option(payload_json, label, dict)
    return payload or {}


def parse_json_arg(raw: str | None, label: str, expected_type: type | tuple[type, ...]) -> Any:
    return parse_json_option(raw, label, expected_type)


def resolve_tiktok_client(args: argparse.Namespace) -> tuple[str, TikTokClient]:
    advertiser_id = validate_non_empty(getattr(args, "advertiser_id", None), "advertiser_id")
    auth = resolve_auth(
        account_id=advertiser_id,
        media_code="tiktok",
        access_token=args.access_token,
    )
    return advertiser_id, TikTokClient(auth.access_token)


def assign_if_present(payload: dict[str, Any], key: str, value: Any) -> None:
    return _payloads.assign_if_present(payload, key, value, deps=_helper_dependencies())


def non_empty_list(values: list[str] | None) -> list[str]:
    return _payloads.non_empty_list(values, deps=_helper_dependencies())


def validate_max_list_size(values: list[str], label: str, *, max_size: int) -> list[str]:
    return _payloads.validate_max_list_size(values, label, max_size=max_size, deps=_helper_dependencies())


def require_non_empty_payload(payload: dict[str, Any], label: str = "payload") -> dict[str, Any]:
    return _payloads.require_non_empty_payload(payload, label, deps=_helper_dependencies())


def parse_bool_flags(*values: Any) -> Any:
    return _payloads.parse_bool_flags(*values, deps=_helper_dependencies())


def generate_tiktok_request_id() -> str:
    # TikTok create APIs accept a numeric request_id even though the SDK types it as str.
    return _payloads.generate_tiktok_request_id(deps=_helper_dependencies())


def slugify_token(value: Any, fallback: str) -> str:
    return _payloads.slugify_token(value, fallback, deps=_helper_dependencies())


def normalize_product_price(value: Any) -> str | None:
    return _payloads.normalize_product_price(value, deps=_helper_dependencies())


def derive_product_script(product: dict[str, Any]) -> str:
    return _payloads.derive_product_script(product, deps=_helper_dependencies())


def derive_product_prompt(product: dict[str, Any]) -> str:
    return _payloads.derive_product_prompt(product, deps=_helper_dependencies())


def infer_product_brand(product: dict[str, Any]) -> str | None:
    return _payloads.infer_product_brand(product, deps=_helper_dependencies())


def derive_product_description(product: dict[str, Any]) -> str:
    return _payloads.derive_product_description(product, deps=_helper_dependencies())


def derive_product_selling_points(product: dict[str, Any]) -> list[str]:
    return _payloads.derive_product_selling_points(product, deps=_helper_dependencies())


def normalize_product_price_value(value: Any) -> float | None:
    return _payloads.normalize_product_price_value(value, deps=_helper_dependencies())


def build_product_video_info_payload(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    context: dict[str, Any],
    video_type: str,
) -> dict[str, Any]:
    return _payloads.build_product_video_info_payload(
        payload,
        args,
        context=context,
        video_type=video_type,
        deps=_helper_dependencies(),
    )


def fetch_product_context(
    args: argparse.Namespace,
    *,
    client: TikTokClient,
    advertiser_id: str,
    upload_images: bool,
) -> dict[str, Any] | None:
    product_url = getattr(args, "product_url", None)
    if not product_url:
        return None

    product = scrape_product(product_url)
    if "error" in product:
        raise CliError(f"Product scrape failed for {product_url}: {product['error']}")

    image_urls = [url for url in (product.get("images") or []) if isinstance(url, str) and looks_like_url(url)]
    if upload_images and not image_urls:
        raise CliError(f"Product scrape returned no usable images for {product_url}")

    uploaded_image_ids: list[str] = []
    uploaded_assets: list[dict[str, Any]] = []

    if upload_images:
        image_limit = max(1, min(int(getattr(args, "product_image_limit", 3) or 3), 3))
        for index, image_url in enumerate(image_urls[:image_limit], start=1):
            raw_name = Path(urlparse(image_url).path).name or f"product-{index}.jpg"
            response = client.upload_image(
                advertiser_id,
                file_name=raw_name,
                image_url=image_url,
            )
            data = response.get("data") if isinstance(response, dict) else {}
            image_id = data.get("image_id") if isinstance(data, dict) else None
            if not image_id:
                raise CliError(f"TikTok image upload did not return image_id for product image: {image_url}")
            uploaded_image_ids.append(str(image_id))
            uploaded_assets.append({"source_url": image_url, "image_id": str(image_id)})

    return {
        "product": product,
        "image_urls": image_urls,
        "image_ids": uploaded_image_ids,
        "uploaded_assets": uploaded_assets,
    }


def apply_product_context_to_aigc_payload(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    context: dict[str, Any],
    mode: str,
    video_type: str | None = None,
) -> dict[str, Any]:
    return _payloads.apply_product_context_to_aigc_payload(
        payload,
        args,
        context=context,
        mode=mode,
        video_type=video_type,
        deps=_helper_dependencies(),
    )


def build_tiktok_image_animation_payload(
    args: argparse.Namespace,
    *,
    client: TikTokClient,
) -> dict[str, Any]:
    return _payloads.build_tiktok_image_animation_payload(args, client=client, deps=_helper_dependencies())


def build_tiktok_aigc_create_payload(
    args: argparse.Namespace,
    *,
    label: str,
    client: TikTokClient,
    mode: str,
    video_type: str | None = None,
) -> dict[str, Any]:
    return _payloads.build_tiktok_aigc_create_payload(
        args,
        label=label,
        client=client,
        mode=mode,
        video_type=video_type,
        deps=_helper_dependencies(),
    )


def add_tiktok_image_animation_create_arguments(parser: argparse.ArgumentParser) -> None:
    payload_group = parser.add_argument_group("Payload")
    payload_group.add_argument(
        "--payload-json",
        help="Raw JSON object for /creative/aigc/image_animation/task/create/.",
    )
    payload_group.add_argument(
        "--payload-file",
        help="Path to a JSON file for /creative/aigc/image_animation/task/create/.",
    )

    bootstrap_group = parser.add_argument_group("Bootstrap")
    bootstrap_group.add_argument(
        "--product-url",
        help="Scrape a product page and default image_url/video_name from it.",
    )

    fields_group = parser.add_argument_group("Image-to-Video Fields")
    fields_group.add_argument(
        "--image-url",
        help="Source image URL. Required unless provided by --product-url or the payload.",
    )
    fields_group.add_argument("--video-name", help="Optional output name for the generated clip.")
    fields_group.add_argument(
        "--change-background",
        dest="change_background",
        action="store_true",
        default=None,
        help="Ask TikTok to replace the image background.",
    )
    fields_group.add_argument(
        "--no-change-background",
        dest="change_background",
        action="store_false",
        default=None,
        help="Keep the original background.",
    )
    fields_group.add_argument(
        "--background-prompt",
        help="Background prompt used when --change-background is enabled.",
    )
    fields_group.add_argument(
        "--animation-prompt",
        help="Animation prompt that controls motion style for the image.",
    )
    fields_group.add_argument(
        "--provider-model",
        choices=TIKTOK_IMAGE_ANIMATION_PROVIDER_CHOICES,
        help="Video provider model. TikTok currently documents GOKU and GEN_4_Turbo.",
    )
    fields_group.add_argument(
        "--video-generation-count",
        type=int,
        help="Number of clips to generate. Valid range: 1-5.",
    )

    # Deprecated alias kept for compatibility with older local usage.
    parser.add_argument("--prompt", help=argparse.SUPPRESS)


def add_tiktok_product_video_create_arguments(parser: argparse.ArgumentParser) -> None:
    payload_group = parser.add_argument_group("Payload")
    payload_group.add_argument(
        "--payload-json",
        help="Raw JSON object for /creative/aigc/video/task/create/. Must use product_video_info, not material_packages.",
    )
    payload_group.add_argument(
        "--payload-file",
        help="Path to a JSON file for /creative/aigc/video/task/create/. Must use product_video_info.",
    )

    bootstrap_group = parser.add_argument_group("Bootstrap")
    bootstrap_group.add_argument(
        "--product-url",
        help="Scrape a product page and auto-fill product_info_list plus default input images.",
    )

    task_group = parser.add_argument_group("Task Type and Controls")
    task_group.add_argument(
        "--type",
        dest="aigc_video_type",
        choices=TIKTOK_AIGC_VIDEO_TYPE_CHOICES,
        help="Task type. Required unless already present in the payload.",
    )
    task_group.add_argument("--voice-id", help="Voice asset used by VOICEOVER.")
    task_group.add_argument("--avatar-id", help="Real avatar used by AVATAR_PRODUCT.")
    task_group.add_argument(
        "--video-generation-count",
        type=int,
        help="Generated video count. VOICEOVER/AVATAR_PRODUCT: 1-5. TRYON: 1-2.",
    )
    task_group.add_argument("--target-language", help="Target language for the generated video.")
    task_group.add_argument(
        "--source-language",
        help="Deprecated by TikTok for product_video_info, kept only for compatibility.",
    )
    task_group.add_argument(
        "--video-duration",
        choices=["RECOMMENDED", "15S", "30S"],
        help="Requested video duration when supported by TikTok.",
    )
    task_group.add_argument(
        "--subtitle-enabled",
        dest="subtitle_enabled",
        action="store_true",
        default=None,
        help="Request subtitles when supported by TikTok.",
    )
    task_group.add_argument(
        "--subtitle-disabled",
        dest="subtitle_enabled",
        action="store_false",
        default=None,
        help="Disable subtitles when supported by TikTok.",
    )

    product_group = parser.add_argument_group("Product Info")
    product_group.add_argument("--product-name", help="Product name in product_info_list.")
    product_group.add_argument("--title", help="Product title in product_info_list.")
    product_group.add_argument("--description", help="Product description in product_info_list.")
    product_group.add_argument("--brand", help="Product brand in product_info_list.")
    product_group.add_argument("--price", type=float, help="Numeric product price.")
    product_group.add_argument("--currency", help="Product price currency, for example USD.")
    product_group.add_argument(
        "--selling-point",
        dest="selling_points",
        action="append",
        help="Repeatable selling point for product_info_list.",
    )

    input_group = parser.add_argument_group("Input Assets")
    input_group.add_argument(
        "--input-video-id",
        dest="input_video_ids",
        action="append",
        help="Repeatable source video_id for input_video_list.video_id_list. Pass video_id, not task_id.",
    )
    input_group.add_argument(
        "--input-image-url",
        dest="input_image_urls",
        action="append",
        help="Repeatable source image URL for input_image_list.image_url_list.",
    )


def add_tiktok_digital_avatar_create_arguments(parser: argparse.ArgumentParser) -> None:
    payload_group = parser.add_argument_group("Payload")
    payload_group.add_argument(
        "--payload-json",
        help="Raw JSON object for /creative/digital_avatar/video/task/create/. Uses material_packages[].",
    )
    payload_group.add_argument(
        "--payload-file",
        help="Path to a JSON file for /creative/digital_avatar/video/task/create/.",
    )

    bootstrap_group = parser.add_argument_group("Bootstrap")
    bootstrap_group.add_argument(
        "--product-url",
        help="Scrape a product page and auto-generate a default script and video name from it.",
    )

    fields_group = parser.add_argument_group("Digital Avatar Fields")
    fields_group.add_argument("--avatar-id", help="Avatar asset ID. Required unless already present in the payload.")
    fields_group.add_argument("--video-name", help="Output video name. TikTok limit: 50 chars.")
    fields_group.add_argument("--script", help="Spoken script for the avatar video. TikTok limit: 2000 chars.")
    fields_group.add_argument("--voice-id", help="Optional voice asset used by the avatar.")
    fields_group.add_argument("--voice-volume", type=float, help="Optional voice volume. Valid range: 0-10.")
    fields_group.add_argument(
        "--voice-speed",
        choices=TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES,
        help="Optional voice speed. TikTok currently supports 0.7x to 1.2x.",
    )
    fields_group.add_argument(
        "--transparent-background-enabled",
        action="store_true",
        default=None,
        help="Request transparent background output when supported.",
    )


def build_tiktok_aigc_voice_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_tiktok_aigc_voice_filtering(args, deps=_helper_dependencies())


def build_tiktok_aigc_task_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_tiktok_aigc_task_filtering(args, deps=_helper_dependencies())


def build_tiktok_aigc_video_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_tiktok_aigc_video_filtering(args, deps=_helper_dependencies())


def build_tiktok_digital_avatar_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_tiktok_digital_avatar_filtering(args, deps=_helper_dependencies())


def build_tiktok_digital_avatar_video_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_tiktok_digital_avatar_video_filtering(args, deps=_helper_dependencies())


def avatar_identity_from_item(item: dict[str, Any]) -> str | None:
    return _payloads.avatar_identity_from_item(item, deps=_helper_dependencies())


def validate_avatar_product_avatar_identity(
    *,
    client: TikTokClient,
    advertiser_id: str,
    payload: dict[str, Any],
) -> None:
    return _payloads.validate_avatar_product_avatar_identity(
        client=client,
        advertiser_id=advertiser_id,
        payload=payload,
        deps=_helper_dependencies(),
    )


def validate_digital_avatar_create_payload(payload: dict[str, Any]) -> None:
    return _payloads.validate_digital_avatar_create_payload(payload, deps=_helper_dependencies())


def build_campaign_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_campaign_filtering(args, deps=_helper_dependencies())


def build_gmv_max_campaign_filtering(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_gmv_max_campaign_filtering(args, deps=_helper_dependencies())


def build_campaign_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_campaign_create_payload(args, deps=_helper_dependencies())


def build_campaign_update_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_campaign_update_payload(args, deps=_helper_dependencies())


def build_campaign_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_campaign_status_payload(args, deps=_helper_dependencies())


def build_adgroup_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_adgroup_filtering(args, deps=_helper_dependencies())


def build_adgroup_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_adgroup_create_payload(args, deps=_helper_dependencies())


def build_adgroup_update_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_adgroup_update_payload(args, deps=_helper_dependencies())


def build_adgroup_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_adgroup_status_payload(args, deps=_helper_dependencies())


def build_ad_filtering(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any] | None:
    return _payloads.build_ad_filtering(args, smart_plus=smart_plus, deps=_helper_dependencies())


NORMAL_AD_UPDATE_AUTOFILL_FIELDS = (
    "ad_format",
    "ad_name",
    "ad_text",
    "ad_texts",
    "app_name",
    "avatar_icon_web_uri",
    "call_to_action",
    "call_to_action_id",
    "card_id",
    "creative_authorized",
    "creative_type",
    "dark_post_status",
    "deeplink",
    "deeplink_type",
    "display_name",
    "identity_authorized_bc_id",
    "identity_id",
    "identity_type",
    "image_ids",
    "landing_page_url",
    "landing_page_urls",
    "music_id",
    "operation_status",
    "page_id",
    "playable_url",
    "promotional_music_disabled",
    "tracking_app_id",
    "tracking_message_event_set_id",
    "tracking_offline_event_set_ids",
    "tracking_pixel_id",
    "tiktok_item_id",
    "video_id",
    "viewability_postbid_partner",
)


def compact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return _copy_payloads.compact_mapping(values, deps=_helper_dependencies())


SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES = {"BUDGET_MODE_TOTAL", "BUDGET_MODE_DYNAMIC_DAILY_BUDGET"}


def parse_positive_float(value: Any) -> float | None:
    return _copy_payloads.parse_positive_float(value, deps=_helper_dependencies())


def max_positive_float(values: list[Any]) -> float | None:
    return _copy_payloads.max_positive_float(values, deps=_helper_dependencies())


def merge_source_snapshot(summary: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    return _copy_payloads.merge_source_snapshot(summary, detail, deps=_helper_dependencies())


def get_campaign_automation_type(source_campaign: dict[str, Any]) -> str | None:
    return _copy_payloads.get_campaign_automation_type(source_campaign, deps=_helper_dependencies())


def is_smart_plus_campaign_type(source_campaign: dict[str, Any]) -> bool:
    return _copy_payloads.is_smart_plus_campaign_type(source_campaign, deps=_helper_dependencies())


def get_adgroup_automation_type(source_adgroup: dict[str, Any]) -> str | None:
    return _copy_payloads.get_adgroup_automation_type(source_adgroup, deps=_helper_dependencies())


def is_smart_plus_adgroup_type(source_adgroup: dict[str, Any], *, campaign_smart_plus: bool) -> bool:
    return _copy_payloads.is_smart_plus_adgroup_type(
        source_adgroup,
        campaign_smart_plus=campaign_smart_plus,
        deps=_helper_dependencies(),
    )


def get_ad_automation_type(source_ad: dict[str, Any]) -> str | None:
    return _copy_payloads.get_ad_automation_type(source_ad, deps=_helper_dependencies())


def is_smart_plus_ad_type(source_ad: dict[str, Any], *, adgroup_smart_plus: bool) -> bool:
    return _copy_payloads.is_smart_plus_ad_type(
        source_ad,
        adgroup_smart_plus=adgroup_smart_plus,
        deps=_helper_dependencies(),
    )


def normalize_copy_schedule_fields(payload: dict[str, Any]) -> None:
    return _copy_payloads.normalize_copy_schedule_fields(payload, deps=_helper_dependencies())


def infer_campaign_copy_strategy(
    source_campaign: dict[str, Any],
    source_adgroups: list[dict[str, Any]],
    source_ads: list[dict[str, Any]],
    *,
    smart_plus: bool,
) -> dict[str, Any]:
    return _copy_payloads.infer_campaign_copy_strategy(
        source_campaign,
        source_adgroups,
        source_ads,
        smart_plus=smart_plus,
        deps=_helper_dependencies(),
    )


def normalize_smartplus_app_campaign_payload(
    payload: dict[str, Any],
    *,
    fallback_budget: float | None = None,
) -> dict[str, Any]:
    return _copy_payloads.normalize_smartplus_app_campaign_payload(
        payload,
        fallback_budget=fallback_budget,
        deps=_helper_dependencies(),
    )


def infer_adgroup_copy_strategy(
    source_adgroup: dict[str, Any],
    *,
    campaign_strategy: dict[str, Any],
) -> dict[str, Any]:
    return _copy_payloads.infer_adgroup_copy_strategy(
        source_adgroup,
        campaign_strategy=campaign_strategy,
        deps=_helper_dependencies(),
    )


def infer_ad_copy_strategy(
    source_ad: dict[str, Any],
    *,
    adgroup_strategy: dict[str, Any],
) -> dict[str, Any]:
    return _copy_payloads.infer_ad_copy_strategy(
        source_ad,
        adgroup_strategy=adgroup_strategy,
        deps=_helper_dependencies(),
    )


def sanitize_normal_ad_copy_creative(creative: dict[str, Any]) -> dict[str, Any]:
    return _copy_payloads.sanitize_normal_ad_copy_creative(creative, deps=_helper_dependencies())


def build_campaign_copy_payload(
    source_campaign: dict[str, Any],
    *,
    advertiser_id: str,
    campaign_name: str,
    operation_status: str | None,
    page_id: str | None = None,
    copy_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _copy_payloads.build_campaign_copy_payload(
        source_campaign,
        advertiser_id=advertiser_id,
        campaign_name=campaign_name,
        operation_status=operation_status,
        page_id=page_id,
        copy_strategy=copy_strategy,
        deps=_helper_dependencies(),
    )


ADGROUP_COPY_DIRECT_FIELDS = (
    "objective_type",
    "optimization_goal",
    "optimization_event",
    "optimization_event_type",
    "billing_event",
    "budget",
    "budget_mode",
    "bid_type",
    "bid_price",
    "conversion_bid_price",
    "roas_bid",
    "promotion_type",
    "promotion_website_type",
    "pixel_id",
    "app_id",
    "placement_type",
    "placements",
    "schedule_type",
    "schedule_start_time",
    "schedule_end_time",
    "click_attribution_window",
    "view_attribution_window",
    "attribution_event_count",
    "targeting_optimization_mode",
    "pacing",
    "comment_disabled",
    "share_disabled",
    "video_download_disabled",
    "creative_material_mode",
    "search_result_enabled",
    "brand_safety_type",
    "operating_systems",
    "min_android_version",
    "category_id",
)

ADGROUP_COPY_FALLBACK_TARGETING_FIELDS = (
    "location_ids",
    "age_groups",
    "gender",
    "spending_power",
)


def build_adgroup_copy_payload(
    source_adgroup: dict[str, Any],
    *,
    advertiser_id: str,
    campaign_id: str,
    operation_status: str | None,
    adgroup_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _copy_payloads.build_adgroup_copy_payload(
        source_adgroup,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        operation_status=operation_status,
        adgroup_strategy=adgroup_strategy,
        deps=_helper_dependencies(),
    )


SMART_PLUS_AD_COPY_FIELDS = (
    "ad_configuration",
    "ad_text_list",
    "auto_message_list",
    "call_to_action_list",
    "creative_list",
    "deeplink_list",
    "interactive_add_on_list",
    "landing_page_url_list",
    "page_list",
)


def extract_source_ad_id(ad: dict[str, Any], *, smart_plus: bool) -> str:
    return _copy_payloads.extract_source_ad_id(ad, smart_plus=smart_plus, deps=_helper_dependencies())


def should_override_landing_page(
    promotion_type: Any,
    existing_urls: list[str],
    override_url: str | None,
) -> bool:
    return _copy_payloads.should_override_landing_page(
        promotion_type,
        existing_urls,
        override_url,
        deps=_helper_dependencies(),
    )


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
) -> dict[str, Any]:
    return _copy_payloads.build_normal_ad_copy_payload(
        source_ad,
        advertiser_id=advertiser_id,
        adgroup_id=adgroup_id,
        operation_status=operation_status,
        landing_page_url=landing_page_url,
        promotion_type=promotion_type,
        client=client,
        source_smart_plus=source_smart_plus,
        ad_strategy=ad_strategy,
        deps=_helper_dependencies(),
    )


def build_smart_plus_ad_copy_payload(
    source_ad: dict[str, Any],
    *,
    advertiser_id: str,
    adgroup_id: str,
    operation_status: str | None,
    landing_page_url: str | None,
    promotion_type: Any,
    ad_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _copy_payloads.build_smart_plus_ad_copy_payload(
        source_ad,
        advertiser_id=advertiser_id,
        adgroup_id=adgroup_id,
        operation_status=operation_status,
        landing_page_url=landing_page_url,
        promotion_type=promotion_type,
        ad_strategy=ad_strategy,
        deps=_helper_dependencies(),
    )


def load_source_adgroups_and_ads_for_copy(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    include_ads: bool,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    source_adgroups: list[dict[str, Any]] = []
    source_ads_by_adgroup: dict[str, list[dict[str, Any]]] = {}
    adgroup_summaries = collect_paginated_entities(
        lambda page, page_size: client.list_adgroups(
            advertiser_id,
            filtering={"campaign_ids": [campaign_id]},
            page=page,
            page_size=page_size,
            smart_plus=smart_plus,
        ),
        "list",
    )
    for adgroup_summary in adgroup_summaries:
        source_adgroup_id = validate_non_empty(adgroup_summary.get("adgroup_id"), "adgroup_id")
        source_adgroup = merge_source_snapshot(
            adgroup_summary,
            client.get_adgroup(
                advertiser_id,
                source_adgroup_id,
                smart_plus=smart_plus,
            ),
        )
        source_adgroups.append(source_adgroup)
        if not include_ads:
            continue
        source_ads: list[dict[str, Any]] = []
        ad_summaries = collect_paginated_entities(
            lambda page, page_size: client.list_ads(
                advertiser_id,
                filtering={"adgroup_ids": [source_adgroup_id]},
                page=page,
                page_size=page_size,
                smart_plus=smart_plus,
            ),
            "list",
        )
        for ad_summary in ad_summaries:
            source_ad_id = extract_source_ad_id(ad_summary, smart_plus=smart_plus)
            source_ads.append(
                merge_source_snapshot(
                    ad_summary,
                    client.get_ad(
                        advertiser_id,
                        source_ad_id,
                        smart_plus=smart_plus,
                    ),
                )
            )
        source_ads_by_adgroup[source_adgroup_id] = source_ads
    return source_adgroups, source_ads_by_adgroup


def build_normal_ad_update_base_creative(existing_ad: dict[str, Any]) -> dict[str, Any]:
    return _payloads.build_normal_ad_update_base_creative(existing_ad, deps=_helper_dependencies())


def merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return _payloads.merge_mapping(base, override, deps=_helper_dependencies())


def build_normal_ad_update_arg_overrides(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_normal_ad_update_arg_overrides(args, deps=_helper_dependencies())


def build_normal_ad_creatives(args: argparse.Namespace, *, for_update: bool) -> list[dict[str, Any]] | None:
    return _payloads.build_normal_ad_creatives(args, for_update=for_update, deps=_helper_dependencies())


def build_smart_plus_ad_json_fields(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    return _payloads.build_smart_plus_ad_json_fields(args, payload, deps=_helper_dependencies())


def build_ad_create_payload(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any]:
    return _payloads.build_ad_create_payload(args, smart_plus=smart_plus, deps=_helper_dependencies())


def build_ad_update_payload(
    args: argparse.Namespace,
    *,
    smart_plus: bool,
    existing_ad: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _payloads.build_ad_update_payload(
        args,
        smart_plus=smart_plus,
        existing_ad=existing_ad,
        deps=_helper_dependencies(),
    )


def build_ad_status_payload(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any]:
    return _payloads.build_ad_status_payload(args, smart_plus=smart_plus, deps=_helper_dependencies())


def build_video_search_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_video_search_filtering(args, deps=_helper_dependencies())


def extract_response_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    return _normalization.extract_response_list(payload, *keys, deps=_helper_dependencies())


def extract_response_strings(payload: dict[str, Any], *keys: str) -> list[str]:
    return _normalization.extract_response_strings(payload, *keys, deps=_helper_dependencies())


def ensure_task_create_response(response: dict[str, Any], *, label: str) -> dict[str, Any]:
    return _normalization.ensure_task_create_response(response, label=label, deps=_helper_dependencies())


def filter_response_items_by_ids(
    response: dict[str, Any],
    *,
    list_key: str,
    item_key: str,
    allowed_ids: set[str],
) -> dict[str, Any]:
    return _normalization.filter_response_items_by_ids(
        response,
        list_key=list_key,
        item_key=item_key,
        allowed_ids=allowed_ids,
        deps=_helper_dependencies(),
    )


def filter_response_items_by_values(
    response: dict[str, Any],
    *,
    list_key: str,
    item_key: str,
    allowed_values: set[str],
) -> dict[str, Any]:
    return _normalization.filter_response_items_by_values(
        response,
        list_key=list_key,
        item_key=item_key,
        allowed_values=allowed_values,
        deps=_helper_dependencies(),
    )


def filter_aigc_video_list_response(
    response: dict[str, Any],
    *,
    client: TikTokClient,
    advertiser_id: str,
    aigc_video_type: str,
    task_ids: list[str] | None,
) -> dict[str, Any]:
    return _normalization.filter_aigc_video_list_response(
        response,
        client=client,
        advertiser_id=advertiser_id,
        aigc_video_type=aigc_video_type,
        task_ids=task_ids,
        deps=_helper_dependencies(),
    )


def filter_digital_avatar_video_list_response(
    response: dict[str, Any],
    *,
    client: TikTokClient,
    advertiser_id: str,
    task_ids: list[str] | None,
) -> dict[str, Any]:
    return _normalization.filter_digital_avatar_video_list_response(
        response,
        client=client,
        advertiser_id=advertiser_id,
        task_ids=task_ids,
        deps=_helper_dependencies(),
    )


def build_validation_result(kind: str, advertiser_id: str, *, smart_plus: bool = False) -> dict[str, Any]:
    return _normalization.build_validation_result(
        kind,
        advertiser_id,
        smart_plus=smart_plus,
        deps=_helper_dependencies(),
    )


def add_validation_error(result: dict[str, Any], code: str, message: str, **context: Any) -> None:
    return _normalization.add_validation_error(result, code, message, deps=_helper_dependencies(), **context)


def add_validation_warning(result: dict[str, Any], code: str, message: str, **context: Any) -> None:
    return _normalization.add_validation_warning(
        result,
        code,
        message,
        deps=_helper_dependencies(),
        **context,
    )


def add_validation_check(result: dict[str, Any], name: str, ok: bool, **details: Any) -> None:
    return _normalization.add_validation_check(result, name, ok, deps=_helper_dependencies(), **details)


def first_dict(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _normalization.first_dict(items, deps=_helper_dependencies())


def collect_strings(value: Any) -> list[str]:
    return _normalization.collect_strings(value, deps=_helper_dependencies())


def dedupe_strings(values: list[str]) -> list[str]:
    return _normalization.dedupe_strings(values, deps=_helper_dependencies())


def parse_int(value: Any) -> int | None:
    return _normalization.parse_int(value, deps=_helper_dependencies())


def collect_paginated_entities(
    fetch_page,
    *list_keys: str,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        response = fetch_page(page, page_size)
        items = extract_response_list(response, *list_keys)
        if not items:
            break
        results.extend(items)
        data = response.get("data")
        page_info = data.get("page_info") if isinstance(data, dict) else {}
        total_page = page_info.get("total_page") if isinstance(page_info, dict) else None
        parsed_total_page = parse_int(total_page)
        if parsed_total_page and page >= parsed_total_page:
            break
        if not parsed_total_page and len(items) < page_size:
            break
        page += 1
    return results


def normalize_text(value: Any) -> str:
    return _normalization.normalize_text(value, deps=_helper_dependencies())


def resolve_tiktok_app_promotion_type(platform: Any) -> str | None:
    return _normalization.resolve_tiktok_app_promotion_type(platform, deps=_helper_dependencies())


def summarize_tiktok_template_campaign(campaign: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_template_campaign(campaign, deps=_helper_dependencies())


def summarize_tiktok_template_adgroup(adgroup: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_template_adgroup(adgroup, deps=_helper_dependencies())


def summarize_tiktok_template_ad(ad: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_template_ad(ad, deps=_helper_dependencies())


def select_tiktok_app(
    apps: list[dict[str, Any]],
    *,
    app_id: str | None = None,
    app_name: str | None = None,
) -> dict[str, Any] | None:
    return _normalization.select_tiktok_app(
        apps,
        app_id=app_id,
        app_name=app_name,
        deps=_helper_dependencies(),
    )


def extract_tiktok_creative_portfolio_contents(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    return _normalization.extract_tiktok_creative_portfolio_contents(portfolio, deps=_helper_dependencies())


def summarize_tiktok_creative_portfolio_content(content: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_creative_portfolio_content(content, deps=_helper_dependencies())


def summarize_tiktok_creative_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_creative_portfolio(portfolio, deps=_helper_dependencies())


def build_creative_portfolio_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_creative_portfolio_filtering(args, deps=_helper_dependencies())


def build_optional_object_from_json_args(
    args: argparse.Namespace,
    *,
    json_attr: str,
    file_attr: str,
    label: str,
) -> dict[str, Any] | None:
    return _payloads.build_optional_object_from_json_args(
        args,
        json_attr=json_attr,
        file_attr=file_attr,
        label=label,
        deps=_helper_dependencies(),
    )


def build_creative_portfolio_content_from_args(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    return _payloads.build_creative_portfolio_content_from_args(args, deps=_helper_dependencies())


def build_creative_portfolio_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_creative_portfolio_create_payload(args, deps=_helper_dependencies())


def build_creative_asset_delete_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_creative_asset_delete_payload(args, deps=_helper_dependencies())


def build_creative_asset_share_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_creative_asset_share_payload(args, deps=_helper_dependencies())


def build_creative_shareable_link_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_creative_shareable_link_payload(args, deps=_helper_dependencies())


def build_creative_smart_text_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_creative_smart_text_payload(args, deps=_helper_dependencies())


def select_tiktok_creative_portfolios(
    portfolios: list[dict[str, Any]],
    *,
    creative_portfolio_ids: list[str] | None = None,
    creative_portfolio_types: list[str] | None = None,
    title: str | None = None,
    query: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    return _discovery.select_tiktok_creative_portfolios(
        portfolios,
        creative_portfolio_ids=creative_portfolio_ids,
        creative_portfolio_types=creative_portfolio_types,
        title=title,
        query=query,
        limit=limit,
        deps=_helper_dependencies(),
    )


def validate_smartplus_app_eligibility(
    client: TikTokClient,
    *,
    advertiser_id: str,
    payload: dict[str, Any],
    context_label: str,
) -> dict[str, Any] | None:
    return _discovery.validate_smartplus_app_eligibility(
        client,
        advertiser_id=advertiser_id,
        payload=payload,
        context_label=context_label,
        deps=_helper_dependencies(),
    )


def find_tiktok_smartplus_app_template(
    client: TikTokClient,
    *,
    advertiser_id: str,
    app_id: str | None = None,
    app_name: str | None = None,
    app_promotion_type: str | None = None,
) -> dict[str, Any]:
    return _discovery.find_tiktok_smartplus_app_template(
        client,
        advertiser_id=advertiser_id,
        app_id=app_id,
        app_name=app_name,
        app_promotion_type=app_promotion_type,
        deps=_helper_dependencies(),
    )


def looks_like_url(value: str) -> bool:
    return _payloads.looks_like_url(value, deps=_helper_dependencies())


def build_validate_creative_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_validate_creative_payload(args, deps=_helper_dependencies())


def normalize_validate_creatives(payload: dict[str, Any], *, smart_plus: bool) -> list[dict[str, Any]]:
    return _normalization.normalize_validate_creatives(
        payload,
        smart_plus=smart_plus,
        deps=_helper_dependencies(),
    )


def extract_identity_refs(creative: dict[str, Any], *, smart_plus: bool) -> dict[str, str | None]:
    return _normalization.extract_identity_refs(creative, smart_plus=smart_plus, deps=_helper_dependencies())


def extract_video_ids(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    return _normalization.extract_video_ids(creative, smart_plus=smart_plus, deps=_helper_dependencies())


def extract_image_ids(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    return _normalization.extract_image_ids(creative, smart_plus=smart_plus, deps=_helper_dependencies())


def extract_image_web_uris(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    return _normalization.extract_image_web_uris(creative, smart_plus=smart_plus, deps=_helper_dependencies())


def extract_landing_page_urls(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    return _normalization.extract_landing_page_urls(
        creative,
        smart_plus=smart_plus,
        deps=_helper_dependencies(),
    )


def extract_tracking_refs(creative: dict[str, Any], *, smart_plus: bool) -> dict[str, Any]:
    return _normalization.extract_tracking_refs(creative, smart_plus=smart_plus, deps=_helper_dependencies())


def derive_bc_ids_from_assets(identities: list[dict[str, Any]], stores: list[dict[str, Any]]) -> list[str]:
    return _normalization.derive_bc_ids_from_assets(identities, stores, deps=_helper_dependencies())


def safe_api_call(label: str, fn, result: dict[str, Any], **kwargs: Any) -> Any:
    try:
        payload = fn()
    except CliError as exc:
        add_validation_warning(result, f"{label}_request_failed", str(exc), **kwargs)
        return None
    add_validation_check(result, label, True, **kwargs)
    return payload


def validate_tiktok_creative_payload(
    advertiser_id: str,
    client: TikTokClient,
    payload: dict[str, Any],
    *,
    smart_plus: bool,
) -> dict[str, Any]:
    result = build_validation_result("creative", advertiser_id, smart_plus=smart_plus)
    creatives = normalize_validate_creatives(payload, smart_plus=smart_plus)
    result["creative_count"] = len(creatives)
    result["creatives"] = []
    if not creatives:
        add_validation_error(result, "creative_missing", "No creative payload was found to validate")
        return result

    identity_cache: dict[str, dict[str, Any]] | None = None
    video_cache: dict[str, bool] = {}
    image_cache: dict[str, bool] = {}
    url_cache: dict[str, dict[str, Any]] = {}

    def ensure_identity_cache() -> dict[str, dict[str, Any]]:
        nonlocal identity_cache
        if identity_cache is not None:
            return identity_cache
        identities_response = safe_api_call(
            "identity_list",
            lambda: client.list_identities(advertiser_id, page=1, page_size=100),
            result,
            page=1,
            page_size=100,
        )
        identity_cache = {}
        for item in extract_response_list(identities_response or {}, "identity_list"):
            identity_id = item.get("identity_id")
            if isinstance(identity_id, str) and identity_id.strip():
                identity_cache[identity_id] = item
        return identity_cache

    for index, creative in enumerate(creatives):
        creative_result = {
            "index": index,
            "errors": [],
            "warnings": [],
            "refs": {},
        }
        result["creatives"].append(creative_result)
        identity_refs = extract_identity_refs(creative, smart_plus=smart_plus)
        video_ids = extract_video_ids(creative, smart_plus=smart_plus)
        image_ids = extract_image_ids(creative, smart_plus=smart_plus)
        image_web_uris = extract_image_web_uris(creative, smart_plus=smart_plus)
        landing_page_urls = extract_landing_page_urls(creative, smart_plus=smart_plus)
        tracking_refs = extract_tracking_refs(creative, smart_plus=smart_plus)
        creative_result["refs"] = {
            "identity": identity_refs,
            "video_ids": video_ids,
            "image_ids": image_ids,
            "image_web_uris": image_web_uris,
            "landing_page_urls": landing_page_urls,
            "tracking": tracking_refs,
        }

        if not identity_refs.get("identity_id"):
            creative_result["errors"].append("Missing identity_id")
            add_validation_error(result, "identity_id_missing", "Creative is missing identity_id", index=index)
        if not identity_refs.get("identity_type"):
            creative_result["errors"].append("Missing identity_type")
            add_validation_error(result, "identity_type_missing", "Creative is missing identity_type", index=index)

        if not video_ids and not image_ids and not image_web_uris:
            creative_result["errors"].append("No media reference found")
            add_validation_error(result, "media_missing", "Creative must reference at least one video or image", index=index)

        identity_id = identity_refs.get("identity_id")
        if isinstance(identity_id, str) and identity_id.strip():
            identity = ensure_identity_cache().get(identity_id)
            if identity is None:
                creative_result["warnings"].append(f"Identity {identity_id} was not found in the first identity page")
                add_validation_warning(
                    result,
                    "identity_not_found",
                    "Identity was not found in the first fetched identity page",
                    index=index,
                    identity_id=identity_id,
                )
            else:
                add_validation_check(
                    result,
                    "identity_exists",
                    True,
                    index=index,
                    identity_id=identity_id,
                    available_status=identity.get("available_status"),
                )

        for video_id in video_ids:
            if video_id in video_cache:
                if video_cache[video_id]:
                    add_validation_check(result, "video_exists", True, index=index, video_id=video_id, cached=True)
                continue
            try:
                client.get_video_info(advertiser_id, [video_id])
                video_cache[video_id] = True
                add_validation_check(result, "video_exists", True, index=index, video_id=video_id)
            except CliError as exc:
                video_cache[video_id] = False
                creative_result["errors"].append(f"Video {video_id} is not readable")
                add_validation_error(result, "video_not_found", str(exc), index=index, video_id=video_id)

        for image_id in image_ids:
            if image_id in image_cache:
                if image_cache[image_id]:
                    add_validation_check(result, "image_exists", True, index=index, image_id=image_id, cached=True)
                continue
            try:
                client.get_image_info(advertiser_id, [image_id])
                image_cache[image_id] = True
                add_validation_check(result, "image_exists", True, index=index, image_id=image_id)
            except CliError as exc:
                image_cache[image_id] = False
                creative_result["errors"].append(f"Image {image_id} is not readable")
                add_validation_error(result, "image_not_found", str(exc), index=index, image_id=image_id)

        for image_web_uri in image_web_uris:
            add_validation_warning(
                result,
                "image_web_uri_unverified",
                "SmartPlus image web_uri cannot be verified with an asset-info endpoint",
                index=index,
                web_uri=image_web_uri,
            )

        for landing_page_url in landing_page_urls:
            cached = url_cache.get(landing_page_url)
            if cached is None:
                response = safe_api_call(
                    "url_validate",
                    lambda: client.validate_url(advertiser_id, landing_page_url),
                    result,
                    index=index,
                    url=landing_page_url,
                )
                cached = response or {}
                url_cache[landing_page_url] = cached
            validate_info = (((cached.get("data") or {}).get("url_info") or {}).get("validate_info") or {})
            is_valid_url = validate_info.get("is_valid_url")
            if is_valid_url is False:
                creative_result["errors"].append(f"Landing page URL is invalid: {landing_page_url}")
                add_validation_error(
                    result,
                    "landing_page_invalid",
                    "TikTok URL validation rejected the landing page URL",
                    index=index,
                    url=landing_page_url,
                    validate_info=validate_info,
                )
            else:
                add_validation_check(
                    result,
                    "landing_page_valid",
                    True,
                    index=index,
                    url=landing_page_url,
                    validate_info=validate_info,
                )

        # Validate call_to_action
        call_to_action = creative.get("call_to_action")
        if call_to_action:
            valid_cta_list = TIKTOK_CTA_SMART_PLUS_LIST if smart_plus else TIKTOK_CTA_ACO_LIST
            if call_to_action not in valid_cta_list:
                creative_result["warnings"].append(
                    f"Call to action '{call_to_action}' is not in the valid CTA list for {'Smart Plus' if smart_plus else 'ACO'} ads"
                )
                add_validation_warning(
                    result,
                    "call_to_action_invalid",
                    f"Call to action '{call_to_action}' is not a recognized value",
                    index=index,
                    call_to_action=call_to_action,
                    valid_values=valid_cta_list,
                    ad_type="smart_plus" if smart_plus else "aco",
                )
            else:
                add_validation_check(
                    result,
                    "call_to_action_valid",
                    True,
                    index=index,
                    call_to_action=call_to_action,
                    ad_type="smart_plus" if smart_plus else "aco",
                )

        # Validate call_to_action_id (CTA portfolio)
        call_to_action_id = creative.get("call_to_action_id")
        if call_to_action_id:
            add_validation_check(
                result,
                "call_to_action_id_present",
                True,
                index=index,
                call_to_action_id=call_to_action_id,
            )

    return result


def resolve_promoted_object_context(
    args: argparse.Namespace,
    advertiser_id: str,
    client: TikTokClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_payload(args)
    resolved = dict(payload)
    diagnostics = build_validation_result("promoted_object_context", advertiser_id, smart_plus=getattr(args, "smart_plus", False))

    adgroup_id = getattr(args, "adgroup_id", None)
    if adgroup_id:
        try:
            adgroup = client.get_adgroup(advertiser_id, adgroup_id, smart_plus=getattr(args, "smart_plus", False))
            diagnostics["adgroup"] = adgroup
            resolved.setdefault("adgroup_id", adgroup_id)
            for key in (
                "campaign_id",
                "promotion_type",
                "pixel_id",
                "app_id",
                "placements",
                "placement_type",
                "optimization_goal",
                "optimization_event",
                "schedule_type",
            ):
                if adgroup.get(key) is not None:
                    resolved.setdefault(key, adgroup.get(key))
        except CliError as exc:
            add_validation_warning(diagnostics, "adgroup_fetch_failed", str(exc), adgroup_id=adgroup_id)

    campaign_id = getattr(args, "campaign_id", None) or resolved.get("campaign_id")
    if campaign_id:
        try:
            campaign = client.get_campaign(advertiser_id, campaign_id, smart_plus=getattr(args, "smart_plus", False))
            diagnostics["campaign"] = campaign
            if campaign.get("objective_type") is not None:
                resolved.setdefault("objective_type", campaign.get("objective_type"))
            if campaign.get("campaign_type") is not None:
                resolved.setdefault("campaign_type", campaign.get("campaign_type"))
            if campaign.get("app_promotion_type") is not None:
                resolved.setdefault("app_promotion_type", campaign.get("app_promotion_type"))
            if campaign.get("budget_optimize_on") is not None:
                resolved.setdefault("budget_optimize_on", campaign.get("budget_optimize_on"))
            if campaign.get("campaign_app_profile_page_state") is not None:
                resolved.setdefault("campaign_app_profile_page_state", campaign.get("campaign_app_profile_page_state"))
        except CliError as exc:
            add_validation_warning(diagnostics, "campaign_fetch_failed", str(exc), campaign_id=campaign_id)

    direct_overrides = compact_mapping(
        {
            "objective_type": getattr(args, "objective_type", None),
            "promotion_type": getattr(args, "promotion_type", None),
            "optimization_goal": getattr(args, "optimization_goal", None),
            "optimization_event": getattr(args, "optimization_event", None),
            "pixel_id": getattr(args, "pixel_id", None),
            "app_id": getattr(args, "app_id", None),
            "store_id": getattr(args, "store_id", None),
            "catalog_id": getattr(args, "catalog_id", None),
            "campaign_type": getattr(args, "campaign_type", None),
            "app_promotion_type": getattr(args, "app_promotion_type", None),
            "landing_page_url": getattr(args, "landing_page_url", None),
        }
    )
    resolved.update(direct_overrides)
    placements = non_empty_list(getattr(args, "placements", None))
    if placements:
        resolved["placements"] = placements
    return resolved, diagnostics

def summarize_tiktok_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_identity(identity, deps=_helper_dependencies())


def summarize_tiktok_pixel_event(event: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_pixel_event(event, deps=_helper_dependencies())


def summarize_tiktok_pixel(pixel: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_pixel(pixel, deps=_helper_dependencies())


def summarize_tiktok_offline_event_set(event_set: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_offline_event_set(event_set, deps=_helper_dependencies())


def summarize_tiktok_app(app: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_app(app, deps=_helper_dependencies())


def summarize_tiktok_store(store: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_store(store, deps=_helper_dependencies())


def summarize_tiktok_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    return _normalization.summarize_tiktok_catalog(catalog, deps=_helper_dependencies())


def command_tiktok_assets_discover(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = {
        "ok": True,
        "advertiser_id": advertiser_id,
        "warnings": [],
        "summary": {},
        "assets": {},
    }

    def record_warning(code: str, message: str, **context: Any) -> None:
        issue = {"code": code, "message": message}
        if context:
            issue["context"] = context
        result["warnings"].append(issue)
        result["ok"] = False

    account_response = None
    try:
        account_response = client.get_account_info([advertiser_id])
    except CliError as exc:
        record_warning("account_info_failed", str(exc))
    account = first_dict(extract_response_list(account_response or {}, "list")) or {}
    result["account"] = compact_mapping(
        {
            "advertiser_id": account.get("advertiser_id") or advertiser_id,
            "name": account.get("name"),
            "company": account.get("company"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "timezone": account.get("timezone"),
            "country": account.get("country"),
            "advertiser_account_type": account.get("advertiser_account_type"),
            "role": account.get("role"),
        }
    )

    identities: list[dict[str, Any]] = []
    try:
        identities_response = client.list_identities(advertiser_id, page=1, page_size=args.page_size)
        identities = extract_response_list(identities_response, "identity_list")
    except CliError as exc:
        record_warning("identity_discover_failed", str(exc))
    result["assets"]["identities"] = [summarize_tiktok_identity(item) for item in identities]
    result["summary"]["identity_count"] = len(identities)

    pixels: list[dict[str, Any]] = []
    try:
        pixels_response = client.list_pixels(advertiser_id)
        pixels = extract_response_list(pixels_response, "pixels")
    except CliError as exc:
        record_warning("pixel_discover_failed", str(exc))
    result["assets"]["pixels"] = [summarize_tiktok_pixel(item) for item in pixels]
    result["summary"]["pixel_count"] = len(pixels)

    offline_event_sets: list[dict[str, Any]] = []
    try:
        offline_response = client.list_offline_event_sets(advertiser_id=advertiser_id)
        offline_event_sets = extract_response_list(offline_response, "event_set_list")
    except CliError as exc:
        record_warning("offline_event_set_discover_failed", str(exc))
    result["assets"]["offline_event_sets"] = [summarize_tiktok_offline_event_set(item) for item in offline_event_sets]
    result["summary"]["offline_event_set_count"] = len(offline_event_sets)

    apps: list[dict[str, Any]] = []
    try:
        apps_response = client.list_apps(advertiser_id)
        apps = extract_response_list(apps_response, "apps")
    except CliError as exc:
        record_warning("app_discover_failed", str(exc))
    result["assets"]["apps"] = [summarize_tiktok_app(item) for item in apps]
    result["summary"]["app_count"] = len(apps)

    stores: list[dict[str, Any]] = []
    try:
        stores_response = client.list_stores(advertiser_id)
        stores = extract_response_list(stores_response, "store_list")
    except CliError as exc:
        record_warning("store_discover_failed", str(exc))
    result["assets"]["stores"] = [summarize_tiktok_store(item) for item in stores]
    result["summary"]["store_count"] = len(stores)

    bc_ids = dedupe_strings(non_empty_list(getattr(args, "bc_ids", None)) + derive_bc_ids_from_assets(identities, stores))
    result["bc_ids"] = bc_ids

    catalogs_by_bc: list[dict[str, Any]] = []
    if bc_ids and not args.skip_catalogs:
        for bc_id in bc_ids:
            try:
                catalogs_response = client.list_catalogs(bc_id, page=1, page_size=args.catalog_page_size)
                catalogs = extract_response_list(catalogs_response, "list")
                catalog_payload = {
                    "bc_id": bc_id,
                    "catalogs": [summarize_tiktok_catalog(item) for item in catalogs],
                }
                if args.include_catalog_bindings:
                    bindings: list[dict[str, Any]] = []
                    for catalog in catalogs:
                        catalog_id = catalog.get("catalog_id")
                        if not isinstance(catalog_id, str) or not catalog_id.strip():
                            continue
                        try:
                            binding_response = client.get_catalog_eventsource_bindings(catalog_id, bc_id)
                            bindings.append(
                                {
                                    "catalog_id": catalog_id,
                                    "response": binding_response.get("data") if isinstance(binding_response, dict) else binding_response,
                                }
                            )
                        except CliError as exc:
                            bindings.append({"catalog_id": catalog_id, "error": str(exc)})
                    catalog_payload["eventsource_bindings"] = bindings
                catalogs_by_bc.append(catalog_payload)
            except CliError as exc:
                catalogs_by_bc.append({"bc_id": bc_id, "error": str(exc)})
                record_warning("catalog_discover_failed", str(exc), bc_id=bc_id)
    result["assets"]["catalogs"] = catalogs_by_bc
    result["summary"]["catalog_count"] = sum(len(item.get("catalogs") or []) for item in catalogs_by_bc)

    print_output(result, as_json=args.json)


def command_tiktok_validate_creative(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_validate_creative_payload(args)
    result = validate_tiktok_creative_payload(
        advertiser_id,
        client,
        payload,
        smart_plus=getattr(args, "smart_plus", False),
    )
    result["payload"] = payload
    print_output(result, as_json=args.json)


def command_tiktok_validate_ad_link(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_validate_creative_payload(args)
    smart_plus = getattr(args, "smart_plus", False)
    result = build_validation_result("ad_link", advertiser_id, smart_plus=smart_plus)
    result["payload"] = payload
    creative_validation = validate_tiktok_creative_payload(advertiser_id, client, payload, smart_plus=smart_plus)
    result["creative_validation"] = creative_validation
    if not creative_validation.get("ok", False):
        result["ok"] = False

    adgroup_id = validate_non_empty(args.adgroup_id, "adgroup_id")
    try:
        adgroup = client.get_adgroup(advertiser_id, adgroup_id, smart_plus=smart_plus)
        result["adgroup"] = adgroup
    except CliError as exc:
        add_validation_error(result, "adgroup_fetch_failed", str(exc), adgroup_id=adgroup_id)
        print_output(result, as_json=args.json)
        return

    creatives = normalize_validate_creatives(payload, smart_plus=smart_plus)
    creative = creatives[0] if creatives else {}
    tracking_refs = extract_tracking_refs(creative, smart_plus=smart_plus)
    landing_page_urls = extract_landing_page_urls(creative, smart_plus=smart_plus)

    promotion_type = str(adgroup.get("promotion_type") or "")
    if promotion_type == "WEBSITE":
        if not landing_page_urls:
            add_validation_error(result, "website_url_missing", "Website ad group requires a landing page URL", adgroup_id=adgroup_id)
        else:
            add_validation_check(result, "website_landing_page_present", True, adgroup_id=adgroup_id, landing_page_urls=landing_page_urls)
        adgroup_pixel_id = adgroup.get("pixel_id")
        creative_pixel_id = tracking_refs.get("tracking_pixel_id")
        if adgroup_pixel_id and creative_pixel_id and str(adgroup_pixel_id) != str(creative_pixel_id):
            add_validation_error(
                result,
                "pixel_mismatch",
                "Creative tracking_pixel_id does not match the ad group's pixel_id",
                adgroup_pixel_id=adgroup_pixel_id,
                creative_pixel_id=creative_pixel_id,
            )
        elif adgroup_pixel_id and not creative_pixel_id:
            add_validation_warning(
                result,
                "creative_pixel_missing",
                "Ad group has pixel_id but creative payload does not include tracking_pixel_id",
                adgroup_pixel_id=adgroup_pixel_id,
            )
        else:
            add_validation_check(
                result,
                "website_pixel_compatible",
                True,
                adgroup_pixel_id=adgroup_pixel_id,
                creative_pixel_id=creative_pixel_id,
            )
    elif promotion_type.startswith("APP"):
        creative_app_id = tracking_refs.get("tracking_app_id")
        adgroup_app_id = adgroup.get("app_id")
        if not creative_app_id:
            add_validation_error(
                result,
                "creative_app_missing",
                "App promotion ad group expects tracking_app_id in the creative payload",
                adgroup_id=adgroup_id,
                adgroup_app_id=adgroup_app_id,
            )
        elif adgroup_app_id and str(creative_app_id) != str(adgroup_app_id):
            add_validation_error(
                result,
                "app_mismatch",
                "Creative tracking_app_id does not match the ad group's app_id",
                adgroup_app_id=adgroup_app_id,
                creative_app_id=creative_app_id,
            )
        else:
            add_validation_check(
                result,
                "app_destination_compatible",
                True,
                adgroup_app_id=adgroup_app_id,
                creative_app_id=creative_app_id,
            )
        if landing_page_urls:
            add_validation_warning(
                result,
                "app_creative_has_landing_page",
                "App promotion creative includes landing page URLs; verify this is intentional",
                landing_page_urls=landing_page_urls,
            )
    else:
        add_validation_warning(
            result,
            "promotion_type_unhandled",
            "No specialized compatibility rule exists for this promotion_type yet",
            promotion_type=promotion_type,
        )

    print_output(result, as_json=args.json)


def command_tiktok_validate_promoted_object(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    smart_plus = getattr(args, "smart_plus", False)
    resolved, diagnostics = resolve_promoted_object_context(args, advertiser_id, client)
    result = build_validation_result("promoted_object", advertiser_id, smart_plus=smart_plus)
    result["resolved"] = resolved
    result["context_diagnostics"] = diagnostics

    objective_type = resolved.get("objective_type")
    promotion_type = resolved.get("promotion_type")
    placements = non_empty_list(resolved.get("placements"))
    pixel_id = resolved.get("pixel_id")
    app_id = resolved.get("app_id")
    store_id = resolved.get("store_id")
    catalog_id = resolved.get("catalog_id")
    optimization_goal = resolved.get("optimization_goal")
    optimization_event = resolved.get("optimization_event")
    landing_page_url = resolved.get("landing_page_url")
    app_promotion_type = resolved.get("app_promotion_type")
    campaign_type = resolved.get("campaign_type")
    budget_optimize_on = resolved.get("budget_optimize_on")
    campaign_app_profile_page_state = resolved.get("campaign_app_profile_page_state")

    if not objective_type:
        add_validation_error(result, "objective_type_missing", "Missing objective_type")
    if not promotion_type:
        add_validation_error(result, "promotion_type_missing", "Missing promotion_type")
    if not placements:
        add_validation_error(result, "placements_missing", "Missing placements")

    if objective_type == "APP_PROMOTION" and not app_id:
        add_validation_error(result, "app_id_missing", "APP_PROMOTION requires app_id")
    if objective_type == "WEB_CONVERSIONS" and promotion_type == "WEBSITE" and not pixel_id:
        add_validation_error(result, "pixel_id_missing", "WEB_CONVERSIONS website flow usually requires pixel_id")
    if pixel_id and not optimization_event:
        add_validation_warning(
            result,
            "optimization_event_missing",
            "pixel_id was provided without optimization_event; some validation and creation flows will reject this",
            pixel_id=pixel_id,
        )
    if objective_type == "PRODUCT_SALES" and promotion_type in {"LIVE_SHOPPING", "VIDEO_SHOPPING"} and (pixel_id or app_id):
        add_validation_error(
            result,
            "product_sales_pixel_or_app_forbidden",
            "LIVE_SHOPPING/VIDEO_SHOPPING should not send pixel_id or app_id for PRODUCT_SALES",
            pixel_id=pixel_id,
            app_id=app_id,
        )

    if pixel_id:
        try:
            pixels = extract_response_list(client.list_pixels(advertiser_id), "pixels")
            matched_pixel = first_dict([item for item in pixels if str(item.get("pixel_id")) == str(pixel_id)])
            if matched_pixel is None:
                add_validation_error(result, "pixel_not_found", "pixel_id was not found in advertiser assets", pixel_id=pixel_id)
            else:
                add_validation_check(
                    result,
                    "pixel_exists",
                    True,
                    pixel_id=pixel_id,
                    activity_status=matched_pixel.get("activity_status"),
                )
                # Add pixel events to result
                pixel_events = matched_pixel.get("events") or []
                result["pixel_events"] = [
                    compact_mapping({
                        "event_id": e.get("event_id"),
                        "event_code": e.get("event_code"),
                        "event_type": e.get("event_type"),
                        "optimization_event": e.get("optimization_event"),
                        "deprecated": e.get("deprecated"),
                    })
                    for e in pixel_events
                ]
                add_validation_check(
                    result,
                    "pixel_events_loaded",
                    len(pixel_events) > 0,
                    pixel_id=pixel_id,
                    event_count=len(pixel_events),
                )

                # Validate optimization_event against pixel events
                if optimization_event:
                    valid_events = [
                        e.get("optimization_event") or e.get("event_type")
                        for e in pixel_events
                        if e.get("optimization_event") or e.get("event_type")
                    ]
                    valid_events = [e for e in valid_events if e]

                    if optimization_event in valid_events:
                        add_validation_check(
                            result,
                            "optimization_event_valid",
                            True,
                            optimization_event=optimization_event,
                        )
                    else:
                        add_validation_error(
                            result,
                            "optimization_event_not_found_in_pixel",
                            f"optimization_event '{optimization_event}' not found in pixel {pixel_id}",
                            optimization_event=optimization_event,
                            pixel_id=pixel_id,
                            valid_events=valid_events,
                        )
        except CliError as exc:
            add_validation_warning(result, "pixel_lookup_failed", str(exc), pixel_id=pixel_id)

    if app_id:
        try:
            app_info = client.get_app_info(advertiser_id, app_id)
            result["app"] = app_info
            add_validation_check(result, "app_exists", True, app_id=app_id)
        except CliError as exc:
            add_validation_error(result, "app_lookup_failed", str(exc), app_id=app_id)
        if optimization_goal:
            try:
                app_events = client.list_app_optimization_events(
                    app_id,
                    advertiser_id,
                    optimization_goal,
                    placement=placements or None,
                    placement_type=resolved.get("placement_type"),
                    objective=objective_type,
                    available_only=True,
                    app_promotion_type=app_promotion_type,
                )
                result["app_optimization_events"] = app_events
                add_validation_check(
                    result,
                    "app_optimization_events_loaded",
                    True,
                    app_id=app_id,
                    optimization_goal=optimization_goal,
                )
            except CliError as exc:
                add_validation_warning(
                    result,
                    "app_optimization_events_failed",
                    str(exc),
                    app_id=app_id,
                    optimization_goal=optimization_goal,
                )

    if store_id:
        try:
            stores = extract_response_list(client.list_stores(advertiser_id), "store_list")
            matched_store = first_dict([item for item in stores if str(item.get("store_id")) == str(store_id)])
            if matched_store is None:
                add_validation_error(result, "store_not_found", "store_id was not found in advertiser assets", store_id=store_id)
            else:
                result["store"] = summarize_tiktok_store(matched_store)
                add_validation_check(result, "store_exists", True, store_id=store_id)
        except CliError as exc:
            add_validation_warning(result, "store_lookup_failed", str(exc), store_id=store_id)

    bc_ids = dedupe_strings(non_empty_list(getattr(args, "bc_ids", None)))
    if store_id and isinstance(result.get("store"), dict):
        store_bc = result["store"].get("store_authorized_bc_id")
        if isinstance(store_bc, str) and store_bc.strip():
            bc_ids = dedupe_strings(bc_ids + [store_bc])
    if catalog_id:
        if not bc_ids:
            try:
                identities = extract_response_list(client.list_identities(advertiser_id, page=1, page_size=100), "identity_list")
                stores = extract_response_list(client.list_stores(advertiser_id), "store_list")
                bc_ids = derive_bc_ids_from_assets(identities, stores)
            except CliError:
                bc_ids = []
        if not bc_ids:
            add_validation_warning(
                result,
                "catalog_lookup_skipped",
                "catalog_id was provided but no bc_id could be derived; pass --bc-id to enable catalog lookup",
                catalog_id=catalog_id,
            )
        else:
            matched_catalog = None
            for bc_id in bc_ids:
                try:
                    catalogs = extract_response_list(client.list_catalogs(bc_id, catalog_id=catalog_id, page=1, page_size=20), "list")
                except CliError as exc:
                    add_validation_warning(result, "catalog_lookup_failed", str(exc), catalog_id=catalog_id, bc_id=bc_id)
                    continue
                for catalog in catalogs:
                    if str(catalog.get("catalog_id")) == str(catalog_id):
                        matched_catalog = summarize_tiktok_catalog(catalog)
                        matched_catalog["bc_id"] = bc_id
                        break
                if matched_catalog is not None:
                    break
            if matched_catalog is None:
                add_validation_error(result, "catalog_not_found", "catalog_id was not found under discovered bc_ids", catalog_id=catalog_id, bc_ids=bc_ids)
            else:
                result["catalog"] = matched_catalog
                add_validation_check(result, "catalog_exists", True, catalog_id=catalog_id, bc_id=matched_catalog.get("bc_id"))

    if landing_page_url and looks_like_url(str(landing_page_url)):
        try:
            url_validation = client.validate_url(advertiser_id, str(landing_page_url))
            result["landing_page_validation"] = url_validation
            validate_info = (((url_validation.get("data") or {}).get("url_info") or {}).get("validate_info") or {})
            if validate_info.get("is_valid_url") is False:
                add_validation_error(
                    result,
                    "landing_page_invalid",
                    "TikTok URL validation rejected the landing_page_url",
                    landing_page_url=landing_page_url,
                    validate_info=validate_info,
                )
            else:
                add_validation_check(result, "landing_page_valid", True, landing_page_url=landing_page_url, validate_info=validate_info)
        except CliError as exc:
            add_validation_warning(result, "landing_page_validate_failed", str(exc), landing_page_url=landing_page_url)

    if objective_type and promotion_type and placements:
        try:
            vbo_status = client.get_vbo_status(
                advertiser_id,
                objective_type,
                promotion_type,
                placements,
                pixel_id=pixel_id,
                app_id=app_id,
                optimization_event=optimization_event,
                app_promotion_type=app_promotion_type,
                store_id=store_id,
                campaign_app_profile_page_state=campaign_app_profile_page_state,
                is_smart_performance_campaign=smart_plus,
                budget_optimize_on=budget_optimize_on,
                campaign_type=campaign_type,
            )
            result["vbo_status"] = vbo_status
            add_validation_check(
                result,
                "vbo_status_loaded",
                True,
                objective_type=objective_type,
                promotion_type=promotion_type,
                placements=placements,
            )
        except CliError as exc:
            add_validation_warning(
                result,
                "vbo_status_failed",
                str(exc),
                objective_type=objective_type,
                promotion_type=promotion_type,
                placements=placements,
            )

    print_output(result, as_json=args.json)


def command_tiktok_campaigns_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_campaigns(
        advertiser_id,
        filtering=build_campaign_filtering(args),
        page=args.page,
        page_size=args.page_size,
        fields=args.fields,
        exclude_field_types=args.exclude_field_types,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_campaigns_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_campaign(
        advertiser_id,
        args.campaign_id,
        fields=args.fields,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_gmv_max_campaigns_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_gmv_max_campaigns(
        advertiser_id,
        filtering=build_gmv_max_campaign_filtering(args),
        page=args.page,
        page_size=args.page_size,
        fields=args.fields,
    )
    print_output(response, as_json=args.json)


def command_tiktok_gmv_max_campaigns_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_gmv_max_campaign(
        advertiser_id,
        args.campaign_id,
    )
    print_output(response, as_json=args.json)


def _identity_video_details(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else {}
    if not isinstance(data, dict):
        return []
    details = data.get("video_details")
    if isinstance(details, list):
        return [item for item in details if isinstance(item, dict)]
    detail = data.get("video_detail")
    return [detail] if isinstance(detail, dict) else []


def _identity_video_author_info(detail: dict[str, Any]) -> dict[str, Any]:
    author: dict[str, Any] = {}
    candidate_keys = (
        "identity_info",
        "author_info",
        "user_info",
        "creator_info",
        "tt_user_info",
    )
    for key in candidate_keys:
        value = detail.get(key)
        if isinstance(value, dict):
            author.update({k: v for k, v in value.items() if v not in (None, "", [], {})})
    for source_key, target_key in (
        ("user_name", "user_name"),
        ("username", "user_name"),
        ("unique_id", "user_name"),
        ("display_name", "display_name"),
        ("nickname", "display_name"),
        ("profile_image", "profile_image"),
        ("profile_image_url", "profile_image"),
        ("avatar_url", "profile_image"),
        ("avatar", "profile_image"),
    ):
        value = detail.get(source_key)
        if value not in (None, "", [], {}):
            author[target_key] = value
    return author


def _apply_identity_video_detail(row: dict[str, Any], detail: dict[str, Any]) -> None:
    if detail.get("text") and not row.get("text"):
        row["text"] = detail.get("text")
    author = _identity_video_author_info(detail)
    if author:
        identity = row.get("identity_info") if isinstance(row.get("identity_info"), dict) else {}
        identity = dict(identity)
        for key, value in author.items():
            if key in {"profile_image", "avatar_url"}:
                identity[key] = value
            else:
                identity.setdefault(key, value)
        row["identity_info"] = identity
    video_info = row.get("video_info") if isinstance(row.get("video_info"), dict) else {}
    api_video = detail.get("video_info") if isinstance(detail.get("video_info"), dict) else {}
    if api_video:
        if api_video.get("poster_url"):
            video_info["video_cover_url"] = api_video.get("poster_url")
        if api_video.get("url"):
            video_info["preview_url"] = api_video.get("url")
        for source_key, target_key in (
            ("bit_rate", "bit_rate"),
            ("duration", "duration"),
            ("size", "size"),
            ("height", "height"),
            ("width", "width"),
            ("signature", "signature"),
        ):
            if api_video.get(source_key) is not None:
                video_info.setdefault(target_key, api_video.get(source_key))
        row["video_info"] = video_info
    row["identity_video_detail"] = detail


def enrich_gmv_max_item_identity_video_info(
    client: TikTokClient,
    *,
    advertiser_id: str,
    rows_by_item_id: dict[str, dict[str, Any]],
    target_item_ids: list[str] | None = None,
) -> dict[str, Any]:
    target_set = {str(value).strip() for value in (target_item_ids or []) if str(value).strip()}
    groups: dict[tuple[str, str, str], list[str]] = {}
    for item_id, row in rows_by_item_id.items():
        if target_set and item_id not in target_set:
            continue
        identity = row.get("identity_info") if isinstance(row.get("identity_info"), dict) else {}
        identity_type = str(identity.get("identity_type") or "").strip()
        identity_id = str(identity.get("identity_id") or "").strip()
        if identity_type not in {"AUTH_CODE", "TT_USER", "BC_AUTH_TT"} or not identity_id:
            continue
        identity_authorized_bc_id = str(identity.get("identity_authorized_bc_id") or "").strip()
        groups.setdefault((identity_type, identity_id, identity_authorized_bc_id), []).append(str(item_id))

    calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    enriched_count = 0
    for (identity_type, identity_id, identity_authorized_bc_id), item_ids in groups.items():
        for batch in [item_ids[index : index + 20] for index in range(0, len(item_ids), 20)]:
            try:
                response = client.get_identity_video_info(
                    advertiser_id,
                    identity_type=identity_type,
                    identity_id=identity_id,
                    item_id=None,
                    item_ids=batch,
                    identity_authorized_bc_id=identity_authorized_bc_id or None,
                    item_type="VIDEO",
                )
                details = _identity_video_details(response)
                for detail in details:
                    item_id = str(detail.get("item_id") or "").strip()
                    if item_id in rows_by_item_id:
                        _apply_identity_video_detail(rows_by_item_id[item_id], detail)
                        enriched_count += 1
                calls.append(
                    {
                        "identity_type": identity_type,
                        "identity_id": identity_id,
                        "identity_authorized_bc_id": identity_authorized_bc_id or None,
                        "item_count": len(batch),
                        "detail_count": len(details),
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "identity_type": identity_type,
                        "identity_id": identity_id,
                        "identity_authorized_bc_id": identity_authorized_bc_id or None,
                        "item_ids": batch,
                        "error": str(exc),
                    }
                )
    return {
        "enabled": True,
        "endpoint": "identity/video/info",
        "target_item_ids": sorted(target_set),
        "identity_group_count": len(groups),
        "call_count": len(calls),
        "enriched_item_count": enriched_count,
        "calls": calls,
        "errors": errors,
    }


def extract_gmv_max_campaign_item_previews(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_ids: list[str],
    target_item_ids: list[str] | None = None,
    enrich_identity_video_info: bool = True,
) -> dict[str, Any]:
    rows_by_item_id: dict[str, dict[str, Any]] = {}
    campaign_infos: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    target_item_ids = list(dict.fromkeys(str(value).strip() for value in (target_item_ids or []) if str(value).strip()))
    target_campaign_hits: dict[str, list[str]] = {item_id: [] for item_id in target_item_ids}
    for campaign_id in campaign_ids:
        try:
            response = client.get_gmv_max_campaign(advertiser_id, campaign_id)
            data = response.get("data") if isinstance(response.get("data"), dict) else response
            item_list = data.get("item_list") if isinstance(data, dict) else []
            item_list = item_list if isinstance(item_list, list) else []
            item_ids_in_campaign: list[str] = []
            campaign_infos.append(
                {
                    "campaign_id": str(campaign_id),
                    "campaign_name": data.get("campaign_name") if isinstance(data, dict) else None,
                    "item_list_count": len(item_list),
                    "data": data,
                }
            )
            for item in item_list:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("item_id") or "").strip()
                if item_id:
                    item_ids_in_campaign.append(item_id)
                    rows_by_item_id.setdefault(item_id, item)
            for item_id in target_item_ids:
                if item_id in item_ids_in_campaign:
                    target_campaign_hits.setdefault(item_id, []).append(str(campaign_id))
        except Exception as exc:
            errors.append({"campaign_id": str(campaign_id), "error": str(exc)})
    identity_enrichment = enrich_gmv_max_item_identity_video_info(
        client,
        advertiser_id=advertiser_id,
        rows_by_item_id=rows_by_item_id,
        target_item_ids=target_item_ids,
    ) if enrich_identity_video_info else {"enabled": False}
    missing_item_ids = [item_id for item_id in target_item_ids if item_id not in rows_by_item_id]
    campaigns_with_empty_item_list = [
        str(info.get("campaign_id"))
        for info in campaign_infos
        if int(info.get("item_list_count") or 0) == 0
    ]
    return {
        "source": "gmv_max_campaign_item_previews",
        "endpoint": "campaign/gmv_max/info",
        "advertiser_id": str(advertiser_id),
        "campaign_ids": campaign_ids,
        "campaign_count": len(campaign_ids),
        "item_preview_count": len(rows_by_item_id),
        "target_item_ids": target_item_ids,
        "missing_item_ids": missing_item_ids,
        "target_campaign_hits": target_campaign_hits,
        "campaigns_with_empty_item_list": campaigns_with_empty_item_list,
        "identity_video_info_enrichment": identity_enrichment,
        "diagnosis": (
            "campaign/gmv_max/info returned no item_list rows for the missing target item_ids. "
            "This endpoint reflects the campaign's current item/video configuration and may not contain historical or no-longer-delivering creative rows that still appear in GMV Max reports."
            if missing_item_ids
            else "All requested target item_ids were present in campaign/gmv_max/info item_list."
        ),
        "rows": list(rows_by_item_id.values()),
        "by_item_id": rows_by_item_id,
        "campaign_infos": campaign_infos,
        "errors": errors,
    }


def command_tiktok_gmv_max_campaigns_item_previews(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    campaign_ids = validate_max_list_size(non_empty_list(getattr(args, "campaign_ids", None)), "campaign_ids", max_size=100)
    if not campaign_ids:
        raise CliError("GMV Max campaign item previews require at least one --campaign-id")
    result = extract_gmv_max_campaign_item_previews(
        client,
        advertiser_id=advertiser_id,
        campaign_ids=campaign_ids,
        target_item_ids=non_empty_list(getattr(args, "item_ids", None)),
        enrich_identity_video_info=(
            bool(non_empty_list(getattr(args, "item_ids", None))) or bool(getattr(args, "identity_video_info", False))
        ) and not getattr(args, "no_identity_video_info", False),
    )
    if getattr(args, "out", None):
        write_json_file(Path(args.out), result)
    print_output(result, as_json=args.json)


def build_tiktok_smartplus_app_create_hint(
    client: TikTokClient,
    *,
    advertiser_id: str,
    payload: dict[str, Any],
    error_text: str | None = None,
) -> str | None:
    if payload.get("objective_type") != "APP_PROMOTION":
        return None
    if error_text and "40002" in error_text and "advanced dedicated campaign" in error_text.lower():
        state = str(payload.get("campaign_app_profile_page_state") or "").upper() or "UNSET"
        if state == "ON":
            return (
                "TikTok still rejected the advanced dedicated iOS app promotion payload even though App Profile Page is ON. "
                "That usually means this advertiser/app is not yet enabled for the required Smart+ iOS flow or the selected App Profile Page asset is invalid. "
                "If you are creating SmartPlus ads, pass the App Profile Page asset ID with `--page-id` (or `--page-list-json`)."
            )
        return (
            "TikTok still rejected the advanced dedicated iOS app promotion payload after Smart+ normalization. "
            "The remaining blocker is usually account-level ADC eligibility or a missing/invalid App Profile Page asset. "
            "If you have an App Profile Page, retry with `campaign_app_profile_page_state=ON`; then pass its asset ID to SmartPlus ads with `--page-id` (or `--page-list-json`). Otherwise create/select one in Ads Manager or ask your TikTok rep to enable the iOS Smart+ path."
        )
    template_match = find_tiktok_smartplus_app_template(
        client,
        advertiser_id=advertiser_id,
        app_id=payload.get("app_id"),
        app_promotion_type=payload.get("app_promotion_type"),
    )
    template_campaign_id = ((template_match.get("template_campaign") or {}).get("campaign_id"))
    if not template_campaign_id:
        app_name = payload.get("app_name") or "<APP_NAME>"
        return (
            "TikTok rejected direct SmartPlus APP_PROMOTION creation with No_ability_match_error. "
            "This usually means the account can run Smart App, but the direct create endpoint is stricter than template-copy flow. "
            "Try `motata tiktok smartplus-campaigns bootstrap-app --name <NEW_NAME> --app-id <APP_ID> "
            f'--app-name "{app_name}" --dry-run` first, then rerun with the returned template campaign if needed.'
        )
    app_id = payload.get("app_id") or "<APP_ID>"
    return (
        "TikTok rejected direct SmartPlus APP_PROMOTION creation with No_ability_match_error. "
        f"Use the safer template-based flow instead: "
        f"`motata tiktok smartplus-campaigns bootstrap-app --name <NEW_NAME> --app-id {app_id} --template-campaign-id {template_campaign_id}`."
    )


def command_tiktok_campaigns_create(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_campaign_create_payload(args)
    if args.smart_plus:
        payload = normalize_smartplus_app_campaign_payload(payload)
        validate_smartplus_app_eligibility(
            client,
            advertiser_id=advertiser_id,
            payload=payload,
            context_label="smartplus-campaigns create",
        )
    try:
        response = client.create_campaign(payload, smart_plus=args.smart_plus)
    except CliError as exc:
        error_text = str(exc)
        if args.smart_plus and ("No_ability_match_error" in error_text or "40002" in error_text):
            hint = build_tiktok_smartplus_app_create_hint(
                client,
                advertiser_id=advertiser_id,
                payload=payload,
                error_text=error_text,
            )
            if hint:
                raise CliError(f"{exc}\nHint: {hint}") from exc
        raise
    print_output(response, as_json=args.json)


def command_tiktok_campaigns_update(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_campaign(build_campaign_update_payload(args), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_campaigns_status(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_campaign_status(build_campaign_status_payload(args), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def load_tiktok_campaign_copy_source(client: TikTokClient, *, advertiser_id: str, campaign_id: str, smart_plus: bool, skip_adgroups: bool, skip_ads: bool, verbose: bool) -> tuple[dict[str, Any], bool, list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    return _campaign_copy.load_tiktok_campaign_copy_source(client, advertiser_id=advertiser_id, campaign_id=campaign_id, smart_plus=smart_plus, skip_adgroups=skip_adgroups, skip_ads=skip_ads, verbose=verbose, deps=_campaign_copy_dependencies())


def execute_tiktok_campaign_copy(client: TikTokClient, *, advertiser_id: str, campaign_id: str, name: str, copies: int, operation_status: str, adgroup_status: str, ad_status: str, landing_page_url: str | None, page_id: str | None, skip_adgroups: bool, skip_ads: bool, smart_plus: bool, copy_route: str='auto', verbose: bool) -> dict[str, Any]:
    return _campaign_copy.execute_tiktok_campaign_copy(client, advertiser_id=advertiser_id, campaign_id=campaign_id, name=name, copies=copies, operation_status=operation_status, adgroup_status=adgroup_status, ad_status=ad_status, landing_page_url=landing_page_url, page_id=page_id, skip_adgroups=skip_adgroups, skip_ads=skip_ads, smart_plus=smart_plus, copy_route=copy_route, verbose=verbose, deps=_campaign_copy_dependencies())


def command_tiktok_campaigns_copy(args: argparse.Namespace) -> None:
    return _campaign_copy.command_tiktok_campaigns_copy(args, deps=_campaign_copy_dependencies())


def command_tiktok_smartplus_campaigns_bootstrap_app(args: argparse.Namespace) -> None:
    return _campaign_copy.command_tiktok_smartplus_campaigns_bootstrap_app(args, deps=_campaign_copy_dependencies())


def command_tiktok_adgroups_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_adgroups(
        advertiser_id,
        filtering=build_adgroup_filtering(args),
        page=args.page,
        page_size=args.page_size,
        fields=args.fields,
        exclude_field_types=args.exclude_field_types,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_adgroups_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_adgroup(
        advertiser_id,
        args.adgroup_id,
        fields=args.fields,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_adgroups_create(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.create_adgroup(build_adgroup_create_payload(args), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_adgroups_update(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_adgroup(build_adgroup_update_payload(args), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_adgroups_status(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_adgroup_status(build_adgroup_status_payload(args), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_ads_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_ads(
        advertiser_id,
        filtering=build_ad_filtering(args, smart_plus=args.smart_plus),
        page=args.page,
        page_size=args.page_size,
        fields=args.fields,
        exclude_field_types=args.exclude_field_types,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_ads_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_ad(
        advertiser_id,
        args.ad_id,
        fields=args.fields,
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_ads_create(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.create_ad(build_ad_create_payload(args, smart_plus=args.smart_plus), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_ads_update(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    existing_ad = None
    if not args.smart_plus and getattr(args, "ad_id", None):
        existing_ad = client.get_ad(advertiser_id, args.ad_id, smart_plus=False)
    response = client.update_ad(
        build_ad_update_payload(args, smart_plus=args.smart_plus, existing_ad=existing_ad),
        smart_plus=args.smart_plus,
    )
    print_output(response, as_json=args.json)


def command_tiktok_ads_status(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_ad_status(build_ad_status_payload(args, smart_plus=args.smart_plus), smart_plus=args.smart_plus)
    print_output(response, as_json=args.json)


def command_tiktok_media_upload_image(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.upload_image(
        advertiser_id,
        args.file,
        file_name=args.name,
        upload_type=args.upload_type,
        image_signature=args.image_signature,
        image_url=args.image_url,
        file_id=args.file_id,
    )
    print_output(response, as_json=args.json)


def command_tiktok_media_upload_video(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.upload_video(
        advertiser_id,
        args.file,
        file_name=args.name,
        upload_type=args.upload_type,
        video_signature=args.video_signature,
        video_url=args.video_url,
        file_id=args.file_id,
        video_id=args.video_id,
        auto_bind_enabled=args.auto_bind_enabled,
        auto_fix_enabled=args.auto_fix_enabled,
        flaw_detect=args.flaw_detect,
        is_third_party=args.is_third_party,
    )
    print_output(response, as_json=args.json)


def command_tiktok_images_info(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_image_info(advertiser_id, non_empty_list(args.image_ids))
    print_output(response, as_json=args.json)


def command_tiktok_videos_info(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_video_info(advertiser_id, non_empty_list(args.video_ids))
    print_output(response, as_json=args.json)


def command_tiktok_videos_search(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.search_videos(
        advertiser_id,
        filtering=build_video_search_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_voices_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_aigc_voices(
        advertiser_id,
        filtering=build_tiktok_aigc_voice_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_image_animation_create(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    payload = build_tiktok_image_animation_payload(args, client=client)
    response = ensure_task_create_response(
        client.create_image_animation_task(payload),
        label="TikTok image-animation create",
    )
    print_output(response, as_json=args.json)


def command_tiktok_activities_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = build_tiktok_activities_report(
        client,
        advertiser_id=advertiser_id,
        start_date=getattr(args, "since", None) or getattr(args, "start_date", None),
        end_date=getattr(args, "until", None) or getattr(args, "end_date", None),
        timezone=getattr(args, "timezone", None),
        module=getattr(args, "module", None),
        object_type=getattr(args, "object_type", None),
        object_ids=getattr(args, "object_ids", None),
        operation_types=getattr(args, "operation_types", None),
        order_fields=getattr(args, "order_fields", None),
        page_size=getattr(args, "page_size", 100),
        wait=not getattr(args, "no_wait", False),
        timeout_seconds=getattr(args, "timeout_seconds", 60),
        poll_seconds=getattr(args, "poll_seconds", 5.0),
    )
    print_output(result, as_json=args.json)


def command_tiktok_aigc_image_animation_tasks(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_ids = validate_max_list_size(non_empty_list(getattr(args, "task_ids", None)), "task_ids", max_size=5)
    response = client.list_aigc_video_tasks(
        advertiser_id,
        aigc_video_type="IMAGE_ANIMATION",
        task_ids=task_ids,
        filtering=build_tiktok_aigc_task_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_image_animation_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_ids = non_empty_list(getattr(args, "task_ids", None))
    response = client.list_aigc_videos(
        advertiser_id,
        aigc_video_types=["IMAGE_ANIMATION"],
        task_ids=task_ids,
        video_ids=non_empty_list(getattr(args, "video_ids", None)),
        filtering=build_tiktok_aigc_video_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    response = filter_aigc_video_list_response(
        response,
        client=client,
        advertiser_id=advertiser_id,
        aigc_video_type="IMAGE_ANIMATION",
        task_ids=task_ids,
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_video_create(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_tiktok_aigc_create_payload(
        args,
        label="AIGC video payload",
        client=client,
        mode="video",
        video_type=getattr(args, "aigc_video_type", None),
    )
    payload["advertiser_id"] = advertiser_id
    assign_if_present(payload, "aigc_video_type", getattr(args, "aigc_video_type", None))
    aigc_video_type = validate_non_empty(payload.get("aigc_video_type"), "aigc_video_type")
    if aigc_video_type == "AVATAR_PRODUCT":
        validate_avatar_product_avatar_identity(
            client=client,
            advertiser_id=advertiser_id,
            payload=payload,
        )
    response = ensure_task_create_response(
        client.create_aigc_video_task(payload),
        label=f"TikTok {aigc_video_type} create",
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_video_tasks(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_ids = validate_max_list_size(non_empty_list(getattr(args, "task_ids", None)), "task_ids", max_size=5)
    response = client.list_aigc_video_tasks(
        advertiser_id,
        aigc_video_type=validate_non_empty(getattr(args, "aigc_video_type", None), "aigc_video_type"),
        task_ids=task_ids,
        filtering=build_tiktok_aigc_task_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_aigc_video_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_ids = non_empty_list(getattr(args, "task_ids", None))
    aigc_video_type = validate_non_empty(getattr(args, "aigc_video_type", None), "aigc_video_type")
    response = client.list_aigc_videos(
        advertiser_id,
        aigc_video_types=[aigc_video_type],
        task_ids=task_ids,
        video_ids=non_empty_list(getattr(args, "video_ids", None)),
        filtering=build_tiktok_aigc_video_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    response = filter_aigc_video_list_response(
        response,
        client=client,
        advertiser_id=advertiser_id,
        aigc_video_type=aigc_video_type,
        task_ids=task_ids,
    )
    print_output(response, as_json=args.json)


def command_tiktok_digital_avatars_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_digital_avatars(
        advertiser_id,
        filtering=build_tiktok_digital_avatar_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_digital_avatar_task_create(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_tiktok_aigc_create_payload(
        args,
        label="digital avatar payload",
        client=client,
        mode="digital-avatar",
    )
    payload["advertiser_id"] = advertiser_id
    validate_digital_avatar_create_payload(payload)
    response = ensure_task_create_response(
        client.create_digital_avatar_video_task(payload),
        label="TikTok digital-avatar create",
    )
    print_output(response, as_json=args.json)


def command_tiktok_digital_avatar_task_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_id = validate_non_empty(getattr(args, "task_id", None), "task_id")
    response = client.get_digital_avatar_video_task(advertiser_id, task_id)
    task_items = extract_response_list(response, "list")
    first_task = first_dict(task_items) or {}
    status = first_task.get("status")
    avatar_video_id = first_task.get("avatar_video_id")
    preview_url = first_task.get("preview_url")

    if status and avatar_video_id and preview_url:
        print_output(response, as_json=args.json)
        return

    fallback_response = client.list_digital_avatar_videos(
        advertiser_id,
        task_ids=[str(task_id)],
        page=1,
        page_size=20,
    )
    fallback_response = filter_digital_avatar_video_list_response(
        fallback_response,
        client=client,
        advertiser_id=advertiser_id,
        task_ids=[str(task_id)],
    )
    result = {
        "task": response,
        "fallback_video_list": fallback_response,
        "note": "digital avatar task/get may return null-state while processing; fallback_video_list is included for confirmation.",
    }
    print_output(result, as_json=args.json)


def command_tiktok_digital_avatar_video_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    task_ids = non_empty_list(getattr(args, "task_ids", None))
    response = client.list_digital_avatar_videos(
        advertiser_id,
        task_ids=task_ids,
        avatar_video_ids=non_empty_list(getattr(args, "avatar_video_ids", None)),
        filtering=build_tiktok_digital_avatar_video_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    response = filter_digital_avatar_video_list_response(
        response,
        client=client,
        advertiser_id=advertiser_id,
        task_ids=task_ids,
    )
    print_output(response, as_json=args.json)


def command_tiktok_digital_avatar_video_rename(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    file_name = validate_non_empty(getattr(args, "file_name", None), "file_name")
    if not 1 <= len(file_name) <= 100:
        raise CliError("Digital avatar rename requires file_name length between 1 and 100 chars")
    response = client.update_video_file_name(
        avatar_video_id=validate_non_empty(getattr(args, "avatar_video_id", None), "avatar_video_id"),
        file_name=file_name,
    )
    print_output(response, as_json=args.json)


def command_tiktok_assets_delete(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.delete_assets(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_assets_share(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.share_assets(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_auth_advertisers(args: argparse.Namespace) -> None:
    app_id = validate_non_empty(getattr(args, "app_id", None), "app_id")
    secret = validate_non_empty(getattr(args, "secret", None), "secret")
    access_token = validate_non_empty(getattr(args, "access_token", None), "access_token")
    client = TikTokClient(access_token)
    response = client.oauth2_advertiser_get(app_id, secret)
    print_output(response, as_json=args.json)


def resolve_account_info_ids(args: argparse.Namespace, fallback_advertiser_id: str) -> list[str]:
    advertiser_ids = non_empty_list(getattr(args, "advertiser_ids", None))
    return advertiser_ids or [fallback_advertiser_id]


def command_tiktok_accounts_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_account_info(
        resolve_account_info_ids(args, advertiser_id),
        fields=args.fields,
    )
    print_output(response, as_json=args.json)


def command_tiktok_accounts_info(args: argparse.Namespace) -> None:
    command_tiktok_accounts_list(args)


def command_tiktok_accounts_inspect(args: argparse.Namespace) -> None:
    command_tiktok_accounts_list(args)


def command_tiktok_accounts_update(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.update_account(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_targeting_regions(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.search_regions(advertiser_id, language=args.language)
    print_output(response, as_json=args.json)


def command_tiktok_targeting_search(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.targeting_search(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_targeting_info(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.targeting_info(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_targeting_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.targeting_list(
        advertiser_id,
        location_ids=non_empty_list(args.location_ids),
        scene=validate_non_empty(args.scene, "scene"),
    )
    print_output(response, as_json=args.json)


def command_tiktok_identities_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_identities(
        advertiser_id,
        identity_type=args.identity_type,
        identity_authorized_bc_id=args.identity_authorized_bc_id,
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_identities_create(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.create_identity(require_non_empty_payload(load_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_identities_video_info(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    item_ids = non_empty_list(getattr(args, "item_ids", None))
    if not item_ids and getattr(args, "item_id", None):
        item_ids = [str(args.item_id)]
    response = client.get_identity_video_info(
        advertiser_id,
        identity_type=validate_non_empty(args.identity_type, "identity_type"),
        identity_id=validate_non_empty(args.identity_id, "identity_id"),
        item_id=None if len(item_ids) != 1 else item_ids[0],
        item_ids=item_ids if len(item_ids) != 1 else None,
        identity_authorized_bc_id=getattr(args, "identity_authorized_bc_id", None),
        item_type=getattr(args, "item_type", None),
    )
    print_output(response, as_json=args.json)


def build_integrated_report_filtering(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    return _payloads.build_integrated_report_filtering(args, deps=_helper_dependencies())


def command_tiktok_insights_get(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.integrated_report(
        validate_non_empty(args.report_type, "report_type"),
        page=args.page,
        page_size=args.page_size,
        enable_total_metrics=args.enable_total_metrics,
        multi_adv_report_in_utc_time=args.multi_adv_report_in_utc_time,
        query_mode=args.query_mode,
        advertiser_id=args.advertiser_id,
        advertiser_ids=non_empty_list(args.advertiser_ids),
        bc_id=args.bc_id,
        service_type=args.service_type,
        data_level=args.data_level,
        dimensions=non_empty_list(args.dimensions),
        metrics=non_empty_list(args.metrics),
        start_date=args.start_date,
        end_date=args.end_date,
        query_lifetime=args.query_lifetime,
        order_field=args.order_field,
        order_type=args.order_type,
        filtering=build_integrated_report_filtering(args),
    )
    print_output(response, as_json=args.json)


def build_smartplus_material_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_smartplus_material_filtering(args, deps=_helper_dependencies())


def build_gmv_max_report_filtering(args: argparse.Namespace, dimensions: list[str] | None = None) -> dict[str, Any] | None:
    return _payloads.build_gmv_max_report_filtering(args, dimensions, deps=_helper_dependencies())


def resolve_gmv_max_report_dimensions(args: argparse.Namespace) -> list[str]:
    return _payloads.resolve_gmv_max_report_dimensions(args, deps=_helper_dependencies())


def default_gmv_max_report_metrics(args: argparse.Namespace, dimensions: list[str]) -> list[str]:
    return _payloads.default_gmv_max_report_metrics(args, dimensions, deps=_helper_dependencies())


def classify_gmv_max_item_scope(item_id: Any) -> str:
    return _payloads.classify_gmv_max_item_scope(item_id, deps=_helper_dependencies())


def annotate_gmv_max_item_scope(response: dict[str, Any]) -> dict[str, Any]:
    return _payloads.annotate_gmv_max_item_scope(response, deps=_helper_dependencies())


def command_tiktok_gmv_max_reports_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    dimensions = resolve_gmv_max_report_dimensions(args)
    metric_args = non_empty_list(args.metrics)
    metrics = metric_args or default_gmv_max_report_metrics(args, dimensions)
    response = client.gmv_max_report(
        advertiser_id,
        store_ids=non_empty_list(args.store_ids),
        dimensions=dimensions,
        metrics=metrics,
        start_date=validate_non_empty(args.start_date, "start_date"),
        end_date=validate_non_empty(args.end_date, "end_date"),
        filtering=build_gmv_max_report_filtering(args, dimensions),
        enable_total_metrics=args.enable_total_metrics,
        sort_field=args.sort_field,
        sort_type=args.sort_type,
        page=args.page,
        page_size=args.page_size,
    )
    response.setdefault("motata", {})["metrics_used"] = metrics
    annotate_gmv_max_item_scope(response)
    print_output(response, as_json=args.json)


def build_gmv_max_custom_anchor_video_payload(args: argparse.Namespace, advertiser_id: str) -> dict[str, Any]:
    return _payloads.build_gmv_max_custom_anchor_video_payload(
        args,
        advertiser_id,
        deps=_helper_dependencies(),
    )


def command_tiktok_gmv_max_custom_anchor_videos_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_gmv_max_custom_anchor_videos(
        build_gmv_max_custom_anchor_video_payload(args, advertiser_id)
    )
    print_output(response, as_json=args.json)


def build_gmv_max_video_list_payload(args: argparse.Namespace) -> dict[str, Any]:
    return _payloads.build_gmv_max_video_list_payload(args, deps=_helper_dependencies())


def command_tiktok_gmv_max_videos_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    payload = build_gmv_max_video_list_payload(args)
    response = client.list_gmv_max_videos(
        advertiser_id,
        store_id=str(payload["store_id"]),
        store_authorized_bc_id=str(payload["store_authorized_bc_id"]),
        spu_id_list=payload.get("spu_id_list"),
        custom_posts_eligible=payload.get("custom_posts_eligible"),
        sort_field=payload.get("sort_field"),
        sort_type=payload.get("sort_type"),
        keyword=payload.get("keyword"),
        need_auth_code_video=payload.get("need_auth_code_video"),
        identity_list=payload.get("identity_list"),
        page=payload.get("page"),
        page_size=payload.get("page_size"),
    )
    print_output(response, as_json=args.json)


def build_store_product_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    return _payloads.build_store_product_filtering(args, deps=_helper_dependencies())


def normalize_store_product_row(product: dict[str, Any]) -> dict[str, Any]:
    return _payloads.normalize_store_product_row(product, deps=_helper_dependencies())


def command_tiktok_gmv_max_products_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    page = int(getattr(args, "page", 1) or 1)
    page_size = int(getattr(args, "page_size", 100) or 100)
    if page < 1:
        raise CliError("page must be >= 1")
    if not 1 <= page_size <= 100:
        raise CliError("page_size must be between 1 and 100 for store/product/get")
    response = client.list_store_products(
        advertiser_id,
        bc_id=validate_non_empty(getattr(args, "bc_id", None), "bc_id"),
        store_id=validate_non_empty(getattr(args, "store_id", None), "store_id"),
        filtering=build_store_product_filtering(args),
        sort_field=getattr(args, "sort_field", None),
        sort_type=getattr(args, "sort_type", None),
        page=page,
        page_size=page_size,
    )
    product_rows = extract_response_list(response, "store_products")
    normalized_rows = [normalize_store_product_row(row) for row in product_rows]
    result = {
        "platform": "tiktok",
        "source": "store_product_get",
        "advertiser_id": advertiser_id,
        "bc_id": str(args.bc_id),
        "store_id": str(args.store_id),
        "filtering": build_store_product_filtering(args),
        "page_info": (response.get("data") or {}).get("page_info") if isinstance(response.get("data"), dict) else None,
        "rows": normalized_rows,
        "by_item_group_id": {
            str(row.get("item_group_id")): row
            for row in normalized_rows
            if row.get("item_group_id")
        },
        "raw_response": response,
    }
    out_path = getattr(args, "out", None)
    if out_path:
        write_json_file(Path(out_path), result)
    print_output(result if getattr(args, "normalized", False) else response, as_json=args.json)


def command_tiktok_insights_smartplus_overview(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.smart_plus_material_report_overview(
        advertiser_id,
        dimensions=non_empty_list(args.dimensions),
        metrics=non_empty_list(args.metrics),
        start_date=args.start_date,
        end_date=args.end_date,
        query_lifetime=args.query_lifetime,
        filtering=build_smartplus_material_filtering(args),
        sort_field=args.sort_field,
        sort_type=args.sort_type,
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_landing_pages_analyze(args: argparse.Namespace) -> int:
    advertiser_ids = non_empty_list(getattr(args, "advertiser_ids", None))
    access_token = getattr(args, "access_token", None)
    if not access_token:
        if len(advertiser_ids) != 1:
            raise CliError("TikTok landing-pages analyze requires --access-token when querying multiple advertisers")
        auth = resolve_auth(
            account_id=advertiser_ids[0],
            media_code="tiktok",
            access_token=access_token,
        )
        access_token = auth.access_token

    client = TikTokClient(access_token)
    start_date = getattr(args, "start_date", None)
    end_date = getattr(args, "end_date", None)
    if not start_date or not end_date:
        default_start, default_end = default_date_range(14)
        start_date = start_date or default_start
        end_date = end_date or default_end

    if not advertiser_ids:
        advertiser_assets, _active_discovery_errors = discover_recent_spend_advertisers(
            client,
            advertiser_limit=getattr(args, "advertiser_limit", None),
            start_date=start_date,
            end_date=end_date,
        )
        advertiser_ids = [item["advertiser_id"] for item in advertiser_assets if item.get("advertiser_id")]
    if not advertiser_ids:
        advertiser_ids = discover_advertiser_ids(
            client,
            app_id=getattr(args, "app_id", None),
            secret=getattr(args, "secret", None),
        )
    if not advertiser_ids:
        raise CliError(
            "No TikTok advertiser IDs found. Provide --advertiser-id, use a token with bc/asset/get access, "
            "or provide --app-id and --secret for discovery."
        )

    result = build_tiktok_landing_page_report(
        client,
        advertiser_ids=advertiser_ids,
        start_date=start_date,
        end_date=end_date,
        top=getattr(args, "top", None),
        report_page_size=getattr(args, "page_size", 1000),
        max_pages=getattr(args, "max_pages", 50),
        enrich_product=not getattr(args, "no_product", False),
        product_limit=getattr(args, "product_limit", 50),
        include_ads=getattr(args, "include_ads", False),
        smart_plus=getattr(args, "smart_plus", False),
        ad_limit=getattr(args, "ad_limit", None),
    )
    print_output(result, as_json=True)
    return result.get("completeness", {}).get("exit_code", 0)


def command_tiktok_apps_analyze(args: argparse.Namespace) -> None:
    advertiser_ids = non_empty_list(getattr(args, "advertiser_ids", None))
    access_token = getattr(args, "access_token", None)
    if not access_token:
        if len(advertiser_ids) != 1:
            raise CliError("TikTok apps analyze requires --access-token when querying multiple or auto-discovered advertisers")
        auth = resolve_auth(
            account_id=advertiser_ids[0],
            media_code="tiktok",
            access_token=access_token,
        )
        access_token = auth.access_token

    client = TikTokClient(access_token)
    start_date = getattr(args, "start_date", None)
    end_date = getattr(args, "end_date", None)
    if not start_date or not end_date:
        default_start, default_end = default_date_range(14)
        start_date = start_date or default_start
        end_date = end_date or default_end
    result = build_tiktok_app_report(
        client,
        advertiser_ids=advertiser_ids,
        start_date=start_date,
        end_date=end_date,
        advertiser_limit=getattr(args, "advertiser_limit", 2),
        campaign_limit=getattr(args, "campaign_limit", 20),
        include_campaigns=getattr(args, "include_campaigns", False),
    )
    print_output(result, as_json=True)


def command_tiktok_user_type_analyze(args: argparse.Namespace) -> None:
    advertiser_ids = non_empty_list(getattr(args, "advertiser_ids", None))
    access_token = getattr(args, "access_token", None)
    if not access_token:
        if len(advertiser_ids) != 1:
            raise CliError("TikTok user-type analyze requires --access-token when querying multiple or auto-discovered advertisers")
        auth = resolve_auth(
            account_id=advertiser_ids[0],
            media_code="tiktok",
            access_token=access_token,
        )
        access_token = auth.access_token

    client = TikTokClient(access_token)
    result = build_tiktok_user_type_report(
        client,
        advertiser_ids=advertiser_ids,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        advertiser_limit=getattr(args, "advertiser_limit", 10),
        campaign_limit=getattr(args, "campaign_limit", 10),
        ad_limit=getattr(args, "ad_limit", 5),
        content_limit=getattr(args, "content_limit", 60),
        page_size=getattr(args, "page_size", 1000),
        max_pages=getattr(args, "max_pages", 50),
        smart_plus=getattr(args, "smart_plus", False),
        include_evidence=getattr(args, "include_evidence", False),
    )
    print_output(result, as_json=True)


def command_tiktok_metrics_probe(args: argparse.Namespace) -> None:
    advertiser_ids = non_empty_list(getattr(args, "advertiser_ids", None))
    access_token = getattr(args, "access_token", None)
    if not access_token:
        if len(advertiser_ids) != 1:
            raise CliError("TikTok metrics probe requires --access-token when querying multiple or auto-discovered advertisers")
        auth = resolve_auth(
            account_id=advertiser_ids[0],
            media_code="tiktok",
            access_token=access_token,
        )
        access_token = auth.access_token

    client = TikTokClient(access_token)
    result = build_tiktok_metric_probe(
        client,
        advertiser_ids=advertiser_ids or None,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        advertiser_limit=getattr(args, "advertiser_limit", 10),
        page_size=getattr(args, "page_size", 5),
        group_names=getattr(args, "groups", None),
        profile=getattr(args, "profile", "full"),
    )
    print_output(result, as_json=args.json)


def command_tiktok_audience_breakdown(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = build_tiktok_audience_breakdown(
        client,
        advertiser_id=advertiser_id,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        page_size=getattr(args, "page_size", 500),
        top=getattr(args, "top", 20),
        breakdowns=getattr(args, "breakdowns", None),
        data_level=getattr(args, "data_level", "AUCTION_ADVERTISER"),
    )
    print_output(result, as_json=args.json)


def command_tiktok_creative_retention_report(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    target_ad_ids = non_empty_list(getattr(args, "ad_ids", None))
    if getattr(args, "ad_ids_csv", None):
        target_ad_ids.extend(
            [value.strip() for value in str(args.ad_ids_csv).replace("\n", ",").split(",") if value.strip()]
        )
    target_ad_ids = list(dict.fromkeys(target_ad_ids))
    result = build_tiktok_creative_retention_report(
        client,
        advertiser_id=advertiser_id,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        top=getattr(args, "top", 20),
        page_size=getattr(args, "page_size", 200),
        max_pages=getattr(args, "max_pages", 3),
        include_attributes=not getattr(args, "no_attributes", False),
        include_previews=not getattr(args, "no_previews", False),
        probe_on_missing_core=not getattr(args, "no_probe_on_missing_core", False),
        target_ad_ids=target_ad_ids,
    )
    print_output(result, as_json=args.json)


def command_tiktok_insights_smartplus_breakdown(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.smart_plus_material_report_breakdown(
        advertiser_id,
        dimensions=non_empty_list(args.dimensions),
        start_date=validate_non_empty(args.start_date, "start_date"),
        end_date=validate_non_empty(args.end_date, "end_date"),
        metrics=non_empty_list(args.metrics),
        filtering=build_smartplus_material_filtering(args),
        sort_field=args.sort_field,
        sort_type=args.sort_type,
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_portfolio_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_creative_portfolios(
        advertiser_id,
        filtering=build_creative_portfolio_filtering(args),
        page=args.page,
        page_size=args.page_size,
    )
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_portfolio_get(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.get_creative_portfolio(advertiser_id, args.creative_portfolio_id)
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_portfolio_create(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.create_creative_portfolio(require_non_empty_payload(build_creative_portfolio_create_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_portfolio_select(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    creative_portfolio_ids = non_empty_list(getattr(args, "creative_portfolio_ids", None))
    creative_portfolio_types = non_empty_list(getattr(args, "creative_portfolio_types", None))
    portfolios = collect_paginated_entities(
        lambda page, page_size: client.list_creative_portfolios(
            advertiser_id,
            filtering=build_creative_portfolio_filtering(args),
            page=page,
            page_size=page_size,
        ),
        "creative_portfolios",
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    response = select_tiktok_creative_portfolios(
        portfolios,
        creative_portfolio_ids=creative_portfolio_ids,
        creative_portfolio_types=creative_portfolio_types,
        title=getattr(args, "title", None),
        query=getattr(args, "query", None),
        limit=args.limit,
    )
    response["advertiser_id"] = advertiser_id
    print_output(response, as_json=args.json)


def inspect_tiktok_creative_portfolios(
    client: TikTokClient,
    *,
    advertiser_id: str,
    creative_portfolio_ids: list[str] | None = None,
    creative_portfolio_types: list[str] | None = None,
    title: str | None = None,
    query: str | None = None,
    limit: int = 10,
    page_size: int = 100,
    max_pages: int = 20,
    include_raw: bool = False,
) -> dict[str, Any]:
    return _discovery.inspect_tiktok_creative_portfolios(
        client,
        advertiser_id=advertiser_id,
        creative_portfolio_ids=creative_portfolio_ids,
        creative_portfolio_types=creative_portfolio_types,
        title=title,
        query=query,
        limit=limit,
        page_size=page_size,
        max_pages=max_pages,
        include_raw=include_raw,
        deps=_helper_dependencies(),
    )


def command_tiktok_creative_assets_portfolio_inspect(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = inspect_tiktok_creative_portfolios(
        client,
        advertiser_id=advertiser_id,
        creative_portfolio_ids=non_empty_list(getattr(args, "creative_portfolio_ids", None)),
        creative_portfolio_types=non_empty_list(getattr(args, "creative_portfolio_types", None)),
        title=getattr(args, "title", None),
        query=getattr(args, "query", None),
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
        include_raw=getattr(args, "include_raw", False),
    )
    output_file = getattr(args, "output_file", None)
    if output_file:
        write_json_file(Path(output_file).expanduser(), result)
    print_output(result, as_json=args.json)


def command_tiktok_creative_assets_portfolio_export(args: argparse.Namespace) -> None:
    output_file = validate_non_empty(getattr(args, "output_file", None), "output_file")
    advertiser_id, client = resolve_tiktok_client(args)
    result = inspect_tiktok_creative_portfolios(
        client,
        advertiser_id=advertiser_id,
        creative_portfolio_ids=non_empty_list(getattr(args, "creative_portfolio_ids", None)),
        creative_portfolio_types=non_empty_list(getattr(args, "creative_portfolio_types", None)),
        title=getattr(args, "title", None),
        query=getattr(args, "query", None),
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
        include_raw=True,
    )
    export_path = Path(output_file).expanduser()
    write_json_file(export_path, result)
    print_output(
        {
            "ok": True,
            "advertiser_id": advertiser_id,
            "exported_to": str(export_path),
            "candidate_count": result.get("candidate_count"),
            "selected_count": result.get("selected_count"),
        },
        as_json=args.json,
    )


def collect_tiktok_campaign_creative_hints(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    page_size: int = 100,
    max_pages: int = 20,
) -> dict[str, Any]:
    return _discovery.collect_tiktok_campaign_creative_hints(
        client,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        smart_plus=smart_plus,
        page_size=page_size,
        max_pages=max_pages,
        deps=_helper_dependencies(),
    )


def score_tiktok_creative_portfolio_against_hints(
    portfolio: dict[str, Any],
    hints: dict[str, Any],
) -> dict[str, Any]:
    return _discovery.score_tiktok_creative_portfolio_against_hints(
        portfolio,
        hints,
        deps=_helper_dependencies(),
    )


def match_tiktok_creative_portfolios_from_campaign(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    limit: int = 10,
    page_size: int = 100,
    max_pages: int = 20,
) -> dict[str, Any]:
    return _discovery.match_tiktok_creative_portfolios_from_campaign(
        client,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        smart_plus=smart_plus,
        limit=limit,
        page_size=page_size,
        max_pages=max_pages,
        deps=_helper_dependencies(),
    )


def command_tiktok_creative_assets_portfolio_match_campaign(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = match_tiktok_creative_portfolios_from_campaign(
        client,
        advertiser_id=advertiser_id,
        campaign_id=validate_non_empty(args.campaign_id, "campaign_id"),
        smart_plus=getattr(args, "smart_plus", False),
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    output_file = getattr(args, "output_file", None)
    if output_file:
        write_json_file(Path(output_file).expanduser(), result)
    print_output(result, as_json=args.json)


def command_tiktok_creative_assets_share_link(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.create_shareable_link(require_non_empty_payload(build_creative_shareable_link_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_smart_text(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.generate_smart_text(require_non_empty_payload(build_creative_smart_text_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_delete(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.delete_assets(require_non_empty_payload(build_creative_asset_delete_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_creative_assets_share(args: argparse.Namespace) -> None:
    _, client = resolve_tiktok_client(args)
    response = client.share_assets(require_non_empty_payload(build_creative_asset_share_payload(args)))
    print_output(response, as_json=args.json)


def command_tiktok_creatives_list(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_portfolio_list(args)


def command_tiktok_creatives_get(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_portfolio_get(args)


def command_tiktok_creatives_create(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_portfolio_create(args)


def command_tiktok_creatives_share_link(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_share_link(args)


def command_tiktok_creatives_smart_text(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_smart_text(args)


def command_tiktok_items_resolve(args: argparse.Namespace) -> None:
    result = resolve_items(
        args.item_ids,
        timeout=args.timeout,
        save_html_dir=Path(args.save_html_dir).expanduser() if args.save_html_dir else None,
        download_dir=Path(args.download_dir).expanduser() if args.download_dir else None,
        workers=args.workers,
        include_timing=args.include_timing,
    )
    payload: Any = result[0] if len(result) == 1 else result
    if args.out:
        write_json_file(Path(args.out).expanduser(), payload)
    print_output(payload, as_json=args.json)


def command_tiktok_assets_delete(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_delete(args)


def command_tiktok_assets_share(args: argparse.Namespace) -> None:
    command_tiktok_creative_assets_share(args)


def add_campaign_flags(parser: argparse.ArgumentParser) -> None:
    return _registration.add_campaign_flags(parser, deps=_registration_dependencies())


def add_tiktok_campaign_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    return _registration.add_tiktok_campaign_group(parent_subparsers, name=name, smart_plus=smart_plus, help_text=help_text, deps=_registration_dependencies())


def add_tiktok_gmv_max_campaign_group(parent_subparsers) -> None:
    return _registration.add_tiktok_gmv_max_campaign_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_gmv_max_custom_anchor_video_group(parent_subparsers) -> None:
    return _registration.add_tiktok_gmv_max_custom_anchor_video_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_gmv_max_video_group(parent_subparsers) -> None:
    return _registration.add_tiktok_gmv_max_video_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_gmv_max_product_group(parent_subparsers) -> None:
    return _registration.add_tiktok_gmv_max_product_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_gmv_max_report_group(parent_subparsers) -> None:
    return _registration.add_tiktok_gmv_max_report_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_adgroup_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    return _registration.add_tiktok_adgroup_group(parent_subparsers, name=name, smart_plus=smart_plus, help_text=help_text, deps=_registration_dependencies())


def add_tiktok_ad_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    return _registration.add_tiktok_ad_group(parent_subparsers, name=name, smart_plus=smart_plus, help_text=help_text, deps=_registration_dependencies())


def add_tiktok_media_group(parent_subparsers) -> None:
    return _registration.add_tiktok_media_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_images_group(parent_subparsers) -> None:
    return _registration.add_tiktok_images_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_videos_group(parent_subparsers) -> None:
    return _registration.add_tiktok_videos_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_aigc_group(parent_subparsers) -> None:
    return _registration.add_tiktok_aigc_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_assets_group(parent_subparsers) -> None:
    return _registration.add_tiktok_assets_group(parent_subparsers, deps=_registration_dependencies())


def command_tiktok_cta_list(args: argparse.Namespace) -> None:
    """List available TikTok Call To Action (CTA) values."""
    cta_type = getattr(args, "type", "all")

    result = {
        "ok": True,
        "cta_type": cta_type,
    }

    if cta_type in ("aco", "all"):
        result["aco_cta"] = {
            "description": "Call to action values for ACO (Ad Create Optimization) ads",
            "count": len(TIKTOK_CTA_ACO_LIST),
            "values": TIKTOK_CTA_ACO_LIST,
        }

    if cta_type in ("smart-plus", "all"):
        result["smart_plus_cta"] = {
            "description": "Call to action values for Smart Plus ads (common_material)",
            "count": len(TIKTOK_CTA_SMART_PLUS_LIST),
            "values": TIKTOK_CTA_SMART_PLUS_LIST,
        }

    if cta_type == "all":
        result["all_unique"] = {
            "count": len(TIKTOK_CTA_ALL),
            "values": sorted(TIKTOK_CTA_ALL),
        }

    print_output(result, as_json=args.json)


def add_tiktok_validate_common_arguments(parser: argparse.ArgumentParser) -> None:
    return _registration.add_tiktok_validate_common_arguments(parser, deps=_registration_dependencies())


def add_tiktok_validate_group(parent_subparsers) -> None:
    return _registration.add_tiktok_validate_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_accounts_group(parent_subparsers) -> None:
    return _registration.add_tiktok_accounts_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_activities_group(parent_subparsers) -> None:
    return _registration.add_tiktok_activities_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_auth_group(parent_subparsers) -> None:
    return _registration.add_tiktok_auth_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_targeting_group(parent_subparsers) -> None:
    return _registration.add_tiktok_targeting_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_identities_group(parent_subparsers) -> None:
    return _registration.add_tiktok_identities_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_insights_group(parent_subparsers) -> None:
    return _registration.add_tiktok_insights_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_landing_pages_group(parent_subparsers) -> None:
    return _registration.add_tiktok_landing_pages_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_apps_group(parent_subparsers) -> None:
    return _registration.add_tiktok_apps_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_user_type_group(parent_subparsers) -> None:
    return _registration.add_tiktok_user_type_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_metrics_group(parent_subparsers) -> None:
    return _registration.add_tiktok_metrics_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_audience_group(parent_subparsers) -> None:
    return _registration.add_tiktok_audience_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_creative_retention_report_arguments(parser: argparse.ArgumentParser) -> None:
    return _registration.add_tiktok_creative_retention_report_arguments(parser, deps=_registration_dependencies())


def add_tiktok_creatives_group(parent_subparsers) -> None:
    return _registration.add_tiktok_creatives_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_items_group(parent_subparsers) -> None:
    return _registration.add_tiktok_items_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_creative_retention_group(parent_subparsers) -> None:
    return _registration.add_tiktok_creative_retention_group(parent_subparsers, deps=_registration_dependencies())


def add_tiktok_creative_assets_group(parent_subparsers) -> None:
    return _registration.add_tiktok_creative_assets_group(parent_subparsers, deps=_registration_dependencies())


def register_tiktok_commands(subparsers) -> None:
    return _registration.register_tiktok_commands(subparsers, deps=_registration_dependencies())


# Explicit adapters keep the historical commands API stable without reverse imports.
from types import SimpleNamespace

from .command_groups import registration as _registration

def _registration_dependencies():
    """Resolve legacy public collaborators at call time (including monkey patches)."""
    return SimpleNamespace(
        TIKTOK_AIGC_VIDEO_TYPE_CHOICES=TIKTOK_AIGC_VIDEO_TYPE_CHOICES,
        TIKTOK_DIGITAL_AVATAR_IDENTITY_CHOICES=TIKTOK_DIGITAL_AVATAR_IDENTITY_CHOICES,
        TIKTOK_GMV_MAX_REPORT_DIMENSION_CHOICES=TIKTOK_GMV_MAX_REPORT_DIMENSION_CHOICES,
        TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS=TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS,
        TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS=TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS,
        add_filtering_arguments=add_filtering_arguments,
        add_payload_arguments=add_payload_arguments,
        add_tiktok_auth_arguments=add_tiktok_auth_arguments,
        add_tiktok_digital_avatar_create_arguments=add_tiktok_digital_avatar_create_arguments,
        add_tiktok_image_animation_create_arguments=add_tiktok_image_animation_create_arguments,
        add_tiktok_product_video_create_arguments=add_tiktok_product_video_create_arguments,
        command_tiktok_accounts_info=command_tiktok_accounts_info,
        command_tiktok_accounts_inspect=command_tiktok_accounts_inspect,
        command_tiktok_accounts_list=command_tiktok_accounts_list,
        command_tiktok_accounts_update=command_tiktok_accounts_update,
        command_tiktok_activities_get=command_tiktok_activities_get,
        command_tiktok_adgroups_create=command_tiktok_adgroups_create,
        command_tiktok_adgroups_get=command_tiktok_adgroups_get,
        command_tiktok_adgroups_list=command_tiktok_adgroups_list,
        command_tiktok_adgroups_status=command_tiktok_adgroups_status,
        command_tiktok_adgroups_update=command_tiktok_adgroups_update,
        command_tiktok_ads_create=command_tiktok_ads_create,
        command_tiktok_ads_get=command_tiktok_ads_get,
        command_tiktok_ads_list=command_tiktok_ads_list,
        command_tiktok_ads_status=command_tiktok_ads_status,
        command_tiktok_ads_update=command_tiktok_ads_update,
        command_tiktok_aigc_image_animation_create=command_tiktok_aigc_image_animation_create,
        command_tiktok_aigc_image_animation_list=command_tiktok_aigc_image_animation_list,
        command_tiktok_aigc_image_animation_tasks=command_tiktok_aigc_image_animation_tasks,
        command_tiktok_aigc_video_create=command_tiktok_aigc_video_create,
        command_tiktok_aigc_video_list=command_tiktok_aigc_video_list,
        command_tiktok_aigc_video_tasks=command_tiktok_aigc_video_tasks,
        command_tiktok_aigc_voices_list=command_tiktok_aigc_voices_list,
        command_tiktok_apps_analyze=command_tiktok_apps_analyze,
        command_tiktok_assets_delete=command_tiktok_assets_delete,
        command_tiktok_assets_discover=command_tiktok_assets_discover,
        command_tiktok_assets_share=command_tiktok_assets_share,
        command_tiktok_audience_breakdown=command_tiktok_audience_breakdown,
        command_tiktok_auth_advertisers=command_tiktok_auth_advertisers,
        command_tiktok_campaigns_copy=command_tiktok_campaigns_copy,
        command_tiktok_campaigns_create=command_tiktok_campaigns_create,
        command_tiktok_campaigns_get=command_tiktok_campaigns_get,
        command_tiktok_campaigns_list=command_tiktok_campaigns_list,
        command_tiktok_campaigns_status=command_tiktok_campaigns_status,
        command_tiktok_campaigns_update=command_tiktok_campaigns_update,
        command_tiktok_creative_assets_delete=command_tiktok_creative_assets_delete,
        command_tiktok_creative_assets_portfolio_create=command_tiktok_creative_assets_portfolio_create,
        command_tiktok_creative_assets_portfolio_export=command_tiktok_creative_assets_portfolio_export,
        command_tiktok_creative_assets_portfolio_get=command_tiktok_creative_assets_portfolio_get,
        command_tiktok_creative_assets_portfolio_inspect=command_tiktok_creative_assets_portfolio_inspect,
        command_tiktok_creative_assets_portfolio_list=command_tiktok_creative_assets_portfolio_list,
        command_tiktok_creative_assets_portfolio_match_campaign=command_tiktok_creative_assets_portfolio_match_campaign,
        command_tiktok_creative_assets_portfolio_select=command_tiktok_creative_assets_portfolio_select,
        command_tiktok_creative_assets_share=command_tiktok_creative_assets_share,
        command_tiktok_creative_assets_share_link=command_tiktok_creative_assets_share_link,
        command_tiktok_creative_assets_smart_text=command_tiktok_creative_assets_smart_text,
        command_tiktok_creative_retention_report=command_tiktok_creative_retention_report,
        command_tiktok_creatives_create=command_tiktok_creatives_create,
        command_tiktok_creatives_get=command_tiktok_creatives_get,
        command_tiktok_creatives_list=command_tiktok_creatives_list,
        command_tiktok_creatives_share_link=command_tiktok_creatives_share_link,
        command_tiktok_creatives_smart_text=command_tiktok_creatives_smart_text,
        command_tiktok_cta_list=command_tiktok_cta_list,
        command_tiktok_digital_avatar_task_create=command_tiktok_digital_avatar_task_create,
        command_tiktok_digital_avatar_task_get=command_tiktok_digital_avatar_task_get,
        command_tiktok_digital_avatar_video_list=command_tiktok_digital_avatar_video_list,
        command_tiktok_digital_avatar_video_rename=command_tiktok_digital_avatar_video_rename,
        command_tiktok_digital_avatars_list=command_tiktok_digital_avatars_list,
        command_tiktok_gmv_max_campaigns_get=command_tiktok_gmv_max_campaigns_get,
        command_tiktok_gmv_max_campaigns_item_previews=command_tiktok_gmv_max_campaigns_item_previews,
        command_tiktok_gmv_max_campaigns_list=command_tiktok_gmv_max_campaigns_list,
        command_tiktok_gmv_max_custom_anchor_videos_list=command_tiktok_gmv_max_custom_anchor_videos_list,
        command_tiktok_gmv_max_products_list=command_tiktok_gmv_max_products_list,
        command_tiktok_gmv_max_reports_get=command_tiktok_gmv_max_reports_get,
        command_tiktok_gmv_max_videos_list=command_tiktok_gmv_max_videos_list,
        command_tiktok_identities_create=command_tiktok_identities_create,
        command_tiktok_identities_list=command_tiktok_identities_list,
        command_tiktok_identities_video_info=command_tiktok_identities_video_info,
        command_tiktok_images_info=command_tiktok_images_info,
        command_tiktok_insights_get=command_tiktok_insights_get,
        command_tiktok_insights_smartplus_breakdown=command_tiktok_insights_smartplus_breakdown,
        command_tiktok_insights_smartplus_overview=command_tiktok_insights_smartplus_overview,
        command_tiktok_items_resolve=command_tiktok_items_resolve,
        command_tiktok_landing_pages_analyze=command_tiktok_landing_pages_analyze,
        command_tiktok_media_upload_image=command_tiktok_media_upload_image,
        command_tiktok_media_upload_video=command_tiktok_media_upload_video,
        command_tiktok_metrics_probe=command_tiktok_metrics_probe,
        command_tiktok_smartplus_campaigns_bootstrap_app=command_tiktok_smartplus_campaigns_bootstrap_app,
        command_tiktok_targeting_info=command_tiktok_targeting_info,
        command_tiktok_targeting_list=command_tiktok_targeting_list,
        command_tiktok_targeting_regions=command_tiktok_targeting_regions,
        command_tiktok_targeting_search=command_tiktok_targeting_search,
        command_tiktok_user_type_analyze=command_tiktok_user_type_analyze,
        command_tiktok_validate_ad_link=command_tiktok_validate_ad_link,
        command_tiktok_validate_creative=command_tiktok_validate_creative,
        command_tiktok_validate_promoted_object=command_tiktok_validate_promoted_object,
        command_tiktok_videos_info=command_tiktok_videos_info,
        command_tiktok_videos_search=command_tiktok_videos_search,
        item_id_from_value=item_id_from_value,
        os=os,
    )


from .services import campaign_copy as _campaign_copy

def _campaign_copy_dependencies():
    """Resolve legacy public collaborators at call time (including monkey patches)."""
    return SimpleNamespace(
        CliError=CliError,
        build_adgroup_copy_payload=build_adgroup_copy_payload,
        build_campaign_copy_payload=build_campaign_copy_payload,
        build_normal_ad_copy_payload=build_normal_ad_copy_payload,
        build_smart_plus_ad_copy_payload=build_smart_plus_ad_copy_payload,
        compact_mapping=compact_mapping,
        execute_tiktok_campaign_copy=execute_tiktok_campaign_copy,
        find_tiktok_smartplus_app_template=find_tiktok_smartplus_app_template,
        first_dict=first_dict,
        infer_ad_copy_strategy=infer_ad_copy_strategy,
        infer_adgroup_copy_strategy=infer_adgroup_copy_strategy,
        infer_campaign_copy_strategy=infer_campaign_copy_strategy,
        is_smart_plus_campaign_type=is_smart_plus_campaign_type,
        load_source_adgroups_and_ads_for_copy=load_source_adgroups_and_ads_for_copy,
        load_tiktok_campaign_copy_source=load_tiktok_campaign_copy_source,
        merge_source_snapshot=merge_source_snapshot,
        print_output=print_output,
        resolve_tiktok_client=resolve_tiktok_client,
        summarize_tiktok_app=summarize_tiktok_app,
        summarize_tiktok_template_ad=summarize_tiktok_template_ad,
        summarize_tiktok_template_adgroup=summarize_tiktok_template_adgroup,
        summarize_tiktok_template_campaign=summarize_tiktok_template_campaign,
        validate_non_empty=validate_non_empty,
        validate_smartplus_app_eligibility=validate_smartplus_app_eligibility,
    )


def _helper_dependencies() -> SimpleNamespace:
    """Resolve current facade bindings so downstream monkeypatches remain effective."""
    return SimpleNamespace(
        ADGROUP_COPY_DIRECT_FIELDS=ADGROUP_COPY_DIRECT_FIELDS,
        ADGROUP_COPY_FALLBACK_TARGETING_FIELDS=ADGROUP_COPY_FALLBACK_TARGETING_FIELDS,
        CliError=CliError,
        NORMAL_AD_UPDATE_AUTOFILL_FIELDS=NORMAL_AD_UPDATE_AUTOFILL_FIELDS,
        Path=Path,
        SMART_PLUS_AD_COPY_FIELDS=SMART_PLUS_AD_COPY_FIELDS,
        SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES=SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES,
        TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES=TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES,
        TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS=TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS,
        TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS=TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS,
        TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS=TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS,
        TIKTOK_GMV_MAX_DURATION_REPORT_METRICS=TIKTOK_GMV_MAX_DURATION_REPORT_METRICS,
        TIKTOK_GMV_MAX_ITEM_SCOPE_PRODUCT_CARD=TIKTOK_GMV_MAX_ITEM_SCOPE_PRODUCT_CARD,
        TIKTOK_GMV_MAX_ITEM_SCOPE_SPECIFIC_ITEM=TIKTOK_GMV_MAX_ITEM_SCOPE_SPECIFIC_ITEM,
        TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS=TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS,
        TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS=TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS,
        TIKTOK_GMV_MAX_REPORT_LEVEL_METRICS=TIKTOK_GMV_MAX_REPORT_LEVEL_METRICS,
        TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS=TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS,
        apply_product_context_to_aigc_payload=apply_product_context_to_aigc_payload,
        argparse=argparse,
        assign_if_present=assign_if_present,
        avatar_identity_from_item=avatar_identity_from_item,
        build_creative_portfolio_content_from_args=build_creative_portfolio_content_from_args,
        build_creative_portfolio_filtering=build_creative_portfolio_filtering,
        build_normal_ad_creatives=build_normal_ad_creatives,
        build_normal_ad_update_arg_overrides=build_normal_ad_update_arg_overrides,
        build_normal_ad_update_base_creative=build_normal_ad_update_base_creative,
        build_optional_object_from_json_args=build_optional_object_from_json_args,
        build_product_video_info_payload=build_product_video_info_payload,
        build_smart_plus_ad_json_fields=build_smart_plus_ad_json_fields,
        classify_gmv_max_item_scope=classify_gmv_max_item_scope,
        collect_paginated_entities=collect_paginated_entities,
        collect_strings=collect_strings,
        collect_tiktok_campaign_creative_hints=collect_tiktok_campaign_creative_hints,
        compact_mapping=compact_mapping,
        datetime=datetime,
        dedupe_strings=dedupe_strings,
        derive_product_description=derive_product_description,
        derive_product_prompt=derive_product_prompt,
        derive_product_script=derive_product_script,
        derive_product_selling_points=derive_product_selling_points,
        extract_identity_refs=extract_identity_refs,
        extract_image_ids=extract_image_ids,
        extract_landing_page_urls=extract_landing_page_urls,
        extract_response_list=extract_response_list,
        extract_response_strings=extract_response_strings,
        extract_source_ad_id=extract_source_ad_id,
        extract_tiktok_creative_portfolio_contents=extract_tiktok_creative_portfolio_contents,
        extract_video_ids=extract_video_ids,
        fetch_product_context=fetch_product_context,
        filter_response_items_by_ids=filter_response_items_by_ids,
        filter_response_items_by_values=filter_response_items_by_values,
        first_dict=first_dict,
        generate_tiktok_request_id=generate_tiktok_request_id,
        get_ad_automation_type=get_ad_automation_type,
        get_adgroup_automation_type=get_adgroup_automation_type,
        get_campaign_automation_type=get_campaign_automation_type,
        infer_product_brand=infer_product_brand,
        is_smart_plus_ad_type=is_smart_plus_ad_type,
        is_smart_plus_adgroup_type=is_smart_plus_adgroup_type,
        load_json_file=load_json_file,
        load_payload=load_payload,
        looks_like_url=looks_like_url,
        max_positive_float=max_positive_float,
        merge_mapping=merge_mapping,
        merge_source_snapshot=merge_source_snapshot,
        non_empty_list=non_empty_list,
        normalize_copy_schedule_fields=normalize_copy_schedule_fields,
        normalize_product_price=normalize_product_price,
        normalize_product_price_value=normalize_product_price_value,
        normalize_smartplus_app_campaign_payload=normalize_smartplus_app_campaign_payload,
        normalize_text=normalize_text,
        parse_bool_flags=parse_bool_flags,
        parse_json_arg=parse_json_arg,
        parse_positive_float=parse_positive_float,
        resolve_tiktok_app_promotion_type=resolve_tiktok_app_promotion_type,
        sanitize_normal_ad_copy_creative=sanitize_normal_ad_copy_creative,
        score_tiktok_creative_portfolio_against_hints=score_tiktok_creative_portfolio_against_hints,
        select_tiktok_app=select_tiktok_app,
        select_tiktok_creative_portfolios=select_tiktok_creative_portfolios,
        should_override_landing_page=should_override_landing_page,
        slugify_token=slugify_token,
        summarize_tiktok_app=summarize_tiktok_app,
        summarize_tiktok_creative_portfolio=summarize_tiktok_creative_portfolio,
        summarize_tiktok_creative_portfolio_content=summarize_tiktok_creative_portfolio_content,
        summarize_tiktok_pixel_event=summarize_tiktok_pixel_event,
        summarize_tiktok_template_ad=summarize_tiktok_template_ad,
        summarize_tiktok_template_adgroup=summarize_tiktok_template_adgroup,
        summarize_tiktok_template_campaign=summarize_tiktok_template_campaign,
        time=time,
        timedelta=timedelta,
        urlparse=urlparse,
        uuid=uuid,
        validate_max_list_size=validate_max_list_size,
        validate_non_empty=validate_non_empty,
    )
