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

from motata_cli.meta.commands import (
    CliError,
    load_json_file,
    parse_json_option,
    print_output,
    resolve_auth,
    write_json_file,
    validate_non_empty,
)
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
    if value is not None:
        payload[key] = value


def non_empty_list(values: list[str] | None) -> list[str]:
    return [str(value) for value in (values or []) if str(value).strip()]


def validate_max_list_size(values: list[str], label: str, *, max_size: int) -> list[str]:
    if len(values) > max_size:
        raise CliError(f"{label} supports at most {max_size} items")
    return values


def require_non_empty_payload(payload: dict[str, Any], label: str = "payload") -> dict[str, Any]:
    if not payload:
        raise CliError(f"Missing {label}: provide --payload-json or --payload-file")
    return payload


def parse_bool_flags(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def generate_tiktok_request_id() -> str:
    # TikTok create APIs accept a numeric request_id even though the SDK types it as str.
    return f"{time.time_ns() % 10**19:019d}"


def slugify_token(value: Any, fallback: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in str(value or ""):
        if ord(char) < 128 and char.isalnum():
            chars.append(char.lower())
            previous_dash = False
            continue
        if chars and not previous_dash:
            chars.append("-")
            previous_dash = True
    slug = "".join(chars).strip("-")
    return slug or fallback


def normalize_product_price(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"${value:.2f}"
    text = str(value).strip()
    return text or None


def derive_product_script(product: dict[str, Any]) -> str:
    name = str(product.get("name") or "this product").strip()
    price = normalize_product_price(product.get("price"))
    url_type = str(product.get("url_type") or "")

    if url_type in {"appstore", "googleplay"}:
        return f"Discover {name}, built to help people get started quickly and enjoy a smoother mobile experience."
    if price:
        return f"Discover {name}, available now for {price}. Explore the product details and see why shoppers are choosing it."
    return f"Discover {name}, designed to stand out with practical everyday value. Explore the product details and learn more."


def derive_product_prompt(product: dict[str, Any]) -> str:
    name = str(product.get("name") or "this product").strip()
    return f"Turn {name} into a short natural motion product showcase video with clean camera movement."


def infer_product_brand(product: dict[str, Any]) -> str | None:
    brand = product.get("brand")
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    product_url = str(product.get("url") or "").strip()
    hostname = urlparse(product_url).netloc.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if hostname:
        host_label = hostname.split(".")[0]
        if host_label and host_label != "us":
            return host_label.replace("-", " ").replace("_", " ").upper()
        parts = [part for part in hostname.split(".") if part not in {"www", "com", "net", "org", "co", "shop", "store", "us"}]
        if parts:
            return parts[0].replace("-", " ").replace("_", " ").upper()
    return None


def derive_product_description(product: dict[str, Any]) -> str:
    name = str(product.get("name") or "this product").strip()
    if "built in bra" in name.lower():
        return f"{name} combines a flattering silhouette with built-in support for workouts and everyday wear."
    if "tank" in name.lower():
        return f"{name} is designed for soft comfort, a polished fit, and easy everyday movement."
    return f"{name} is designed to balance comfort, style, and practical everyday use."


def derive_product_selling_points(product: dict[str, Any]) -> list[str]:
    name = str(product.get("name") or "").lower()
    selling_points: list[str] = []
    if "built in bra" in name:
        selling_points.append("Built-in bra support for an easy all-in-one look")
    if "butterluxe" in name or "soft" in name:
        selling_points.append("Ultra-soft fabric for all-day comfort")
    if "square neck" in name:
        selling_points.append("Flattering square neck silhouette")
    if "tank" in name and len(selling_points) < 2:
        selling_points.append("Lightweight tank shape for workouts and daily wear")
    if not selling_points:
        selling_points.extend(
            [
                "Designed for everyday comfort and easy styling",
                "Built to feel practical, polished, and easy to wear",
            ]
        )
    return selling_points[:3]


def normalize_product_price_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    for prefix in ("$", "USD", "usd"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    try:
        return float(text)
    except ValueError:
        return None


def build_product_video_info_payload(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    context: dict[str, Any],
    video_type: str,
) -> dict[str, Any]:
    if "material_packages" in payload:
        raise CliError(
            f"{video_type} create no longer accepts material_packages. "
            "Use product_video_info with product_info_list, input_video_list, and input_image_list."
        )
    product = context["product"]
    image_urls = [str(value).strip() for value in (context.get("image_urls") or []) if str(value).strip()]
    product_video_info = payload.get("product_video_info")
    if not isinstance(product_video_info, dict):
        product_video_info = {}

    input_video_list = product_video_info.get("input_video_list")
    if not isinstance(input_video_list, dict):
        input_video_list = {}
    video_id_list = [
        str(value).strip()
        for value in (
            input_video_list.get("video_id_list")
            or getattr(args, "input_video_ids", None)
            or []
        )
        if str(value).strip()
    ]
    if video_id_list:
        product_video_info["input_video_list"] = {"video_id_list": video_id_list}

    input_image_list = product_video_info.get("input_image_list")
    if not isinstance(input_image_list, dict):
        input_image_list = {}
    image_url_list = [
        str(value).strip()
        for value in (
            input_image_list.get("image_url_list")
            or getattr(args, "input_image_urls", None)
            or (image_urls if video_type in {"AVATAR_PRODUCT", "VOICEOVER", "TRYON"} else [])
        )
        if str(value).strip()
    ]
    if image_url_list:
        product_video_info["input_image_list"] = {"image_url_list": image_url_list}

    existing_product_info_list = product_video_info.get("product_info_list")
    first_product_info = first_dict(existing_product_info_list) if isinstance(existing_product_info_list, list) else None
    product_info = dict(first_product_info or {})
    source_language = getattr(args, "source_language", None) or product_info.get("source_language") or "en"
    target_language = getattr(args, "target_language", None) or product_video_info.get("target_language") or "en"
    product_name = getattr(args, "product_name", None) or product_info.get("product_name") or product.get("name")
    title = getattr(args, "title", None) or product_info.get("title") or product.get("name")
    description = getattr(args, "description", None) or product_info.get("description") or derive_product_description(product)
    brand = getattr(args, "brand", None) or product_info.get("brand") or infer_product_brand(product)
    price = getattr(args, "price", None)
    if price is None:
        price = product_info.get("price")
    if price is None:
        price = normalize_product_price_value(product.get("price"))
    currency = getattr(args, "currency", None) or product_info.get("currency") or "USD"
    selling_points = non_empty_list(getattr(args, "selling_points", None))
    if not selling_points:
        selling_points = [str(value).strip() for value in (product_info.get("selling_points") or []) if str(value).strip()]
    if not selling_points:
        selling_points = derive_product_selling_points(product)

    if not product_name:
        raise CliError(f"{video_type} create requires product_name or --product-url")
    product_info["source_language"] = source_language
    product_info["product_name"] = str(product_name)
    product_info["title"] = str(title or product_name)
    product_info["description"] = str(description)
    product_info["selling_points"] = selling_points
    if brand:
        product_info["brand"] = str(brand)
    if price is not None:
        product_info["price"] = float(price)
    product_info["currency"] = str(currency)
    product_video_info["product_info_list"] = [product_info]
    video_generation_count = int(
        getattr(args, "video_generation_count", None)
        or product_video_info.get("video_generation_count")
        or 1
    )
    if video_type in {"AVATAR_PRODUCT", "VOICEOVER"} and not 1 <= video_generation_count <= 5:
        raise CliError(f"{video_type} create requires video_generation_count between 1 and 5")
    if video_type == "TRYON" and not 1 <= video_generation_count <= 2:
        raise CliError("TRYON create requires video_generation_count between 1 and 2")
    product_video_info["video_generation_count"] = video_generation_count
    product_video_info["target_language"] = str(target_language)
    voice_id = getattr(args, "voice_id", None) or product_video_info.get("voice_id")
    if voice_id:
        product_video_info["voice_id"] = str(voice_id)
    video_duration = getattr(args, "video_duration", None) or product_video_info.get("video_duration")
    if video_duration:
        product_video_info["video_duration"] = str(video_duration)
    subtitle_enabled = parse_bool_flags(getattr(args, "subtitle_enabled", None), product_video_info.get("subtitle_enabled"))
    if subtitle_enabled is not None:
        product_video_info["subtitle_enabled"] = bool(subtitle_enabled)

    if not image_url_list and not video_id_list:
        raise CliError(f"{video_type} create requires at least one of input_image_list or input_video_list")
    if video_type in {"AVATAR_PRODUCT", "VOICEOVER"} and image_url_list and not 3 <= len(image_url_list) <= 30:
        raise CliError(f"{video_type} create requires input_image_list.image_url_list size between 3 and 30")
    if video_type == "TRYON" and not image_url_list:
        raise CliError("TRYON create requires input_image_list.image_url_list")
    if video_type == "TRYON" and image_url_list and not 1 <= len(image_url_list) <= 20:
        raise CliError("TRYON create requires input_image_list.image_url_list size between 1 and 20")
    if video_type == "TRYON" and video_id_list:
        raise CliError("TRYON create does not support input_video_list.video_id_list")
    if video_id_list and not 1 <= len(video_id_list) <= 20:
        raise CliError(f"{video_type} create requires input_video_list.video_id_list size between 1 and 20")

    if video_type == "AVATAR_PRODUCT":
        avatar_info = product_video_info.get("avatar_info")
        if not isinstance(avatar_info, dict):
            avatar_info = {}
        avatar_id = getattr(args, "avatar_id", None) or avatar_info.get("avatar_id")
        if avatar_id:
            product_video_info["avatar_info"] = {"avatar_id": str(avatar_id)}
    payload["product_video_info"] = product_video_info
    return payload


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
    product = context["product"]
    image_ids = context["image_ids"]
    product_url = str(product.get("url") or getattr(args, "product_url"))
    default_script = derive_product_script(product)
    default_prompt = derive_product_prompt(product)

    if mode == "video" and video_type in {"VOICEOVER", "AVATAR_PRODUCT", "TRYON"}:
        return build_product_video_info_payload(payload, args, context=context, video_type=video_type)

    packages = payload.get("material_packages")
    if not isinstance(packages, list):
        packages = []
    dict_packages = [item for item in packages if isinstance(item, dict)]
    if not dict_packages:
        dict_packages = [{}]
        payload["material_packages"] = dict_packages
    else:
        payload["material_packages"] = dict_packages

    primary = dict_packages[0]

    if not primary.get("package_id"):
        primary["package_id"] = getattr(args, "package_id", None) or f"{slugify_token(product.get('name'), 'product')}-{uuid.uuid4().hex[:8]}"
    if mode in {"image-animation", "video", "digital-avatar"} and not primary.get("video_name"):
        prefix = {"image-animation": "image-animation", "video": "aigc-video", "digital-avatar": "digital-avatar"}[mode]
        primary["video_name"] = getattr(args, "video_name", None) or f"{prefix}-{slugify_token(product.get('name'), 'demo')}"

    if mode == "image-animation" and not primary.get("prompt"):
        primary["prompt"] = getattr(args, "prompt", None) or default_prompt

    if mode == "video" and video_type in {"VOICEOVER", "AVATAR_PRODUCT"} and not primary.get("script"):
        primary["script"] = getattr(args, "script", None) or default_script

    if mode == "digital-avatar" and not primary.get("script"):
        primary["script"] = getattr(args, "script", None) or default_script

    if mode in {"image-animation", "video"} and not primary.get("image_ids"):
        primary["image_ids"] = image_ids

    if mode == "video" and video_type in {"VOICEOVER", "AVATAR_PRODUCT", "TRYON"} and not primary.get("landing_page_url"):
        primary["landing_page_url"] = product_url

    if getattr(args, "voice_id", None) and not primary.get("voice_id"):
        primary["voice_id"] = args.voice_id
    if getattr(args, "avatar_id", None) and not primary.get("avatar_id"):
        primary["avatar_id"] = args.avatar_id
    if getattr(args, "voice_volume", None) is not None and primary.get("voice_volume") is None:
        primary["voice_volume"] = args.voice_volume
    if getattr(args, "voice_speed", None) and not primary.get("voice_speed"):
        primary["voice_speed"] = args.voice_speed
    if getattr(args, "transparent_background_enabled", None) is not None and primary.get("transparent_background_enabled") is None:
        primary["transparent_background_enabled"] = args.transparent_background_enabled

    if mode == "digital-avatar" and not primary.get("avatar_id"):
        raise CliError("Digital avatar product bootstrap requires --avatar-id or payload material_packages[].avatar_id")
    return payload


def build_tiktok_image_animation_payload(
    args: argparse.Namespace,
    *,
    client: TikTokClient,
) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args) or {}
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", generate_tiktok_request_id())

    if "material_packages" in payload:
        raise CliError(
            "Image animation create no longer accepts material_packages. "
            "Use top-level image_url, background_prompt, animation_prompt, video_generation_count, and provider_model."
        )

    context = fetch_product_context(
        args,
        client=client,
        advertiser_id=advertiser_id,
        upload_images=False,
    )
    if context and not payload.get("image_url"):
        image_urls = context.get("image_urls") or []
        if image_urls:
            payload["image_url"] = image_urls[0]
    if context and not payload.get("video_name"):
        product = context["product"]
        payload["video_name"] = getattr(args, "video_name", None) or f"image-animation-{slugify_token(product.get('name'), 'demo')}"
    assign_if_present(payload, "image_url", getattr(args, "image_url", None))
    assign_if_present(payload, "video_name", getattr(args, "video_name", None))
    change_background = parse_bool_flags(getattr(args, "change_background", None), payload.get("change_background"))
    if change_background is None and getattr(args, "background_prompt", None):
        change_background = True
    if change_background is not None:
        payload["change_background"] = bool(change_background)
    assign_if_present(payload, "background_prompt", getattr(args, "background_prompt", None))
    assign_if_present(
        payload,
        "animation_prompt",
        getattr(args, "animation_prompt", None) or getattr(args, "prompt", None) or payload.get("prompt"),
    )
    assign_if_present(
        payload,
        "provider_model",
        getattr(args, "provider_model", None) or payload.get("provider_model"),
    )
    payload.pop("prompt", None)

    video_generation_count = getattr(args, "video_generation_count", None) or payload.get("video_generation_count")
    if video_generation_count is not None:
        video_generation_count = int(video_generation_count)
        if not 1 <= video_generation_count <= 5:
            raise CliError("Image animation create requires video_generation_count between 1 and 5")
        payload["video_generation_count"] = video_generation_count

    if payload.get("background_prompt") and not payload.get("change_background"):
        raise CliError("background_prompt is only valid when change_background is enabled")

    if not payload.get("image_url"):
        raise CliError("Image animation create requires --image-url, --product-url, or payload image_url")
    return payload


def build_tiktok_aigc_create_payload(
    args: argparse.Namespace,
    *,
    label: str,
    client: TikTokClient,
    mode: str,
    video_type: str | None = None,
) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    if not payload and not getattr(args, "product_url", None):
        raise CliError(f"Missing {label}: provide --payload-json, --payload-file, or --product-url")

    payload = payload or {}
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", generate_tiktok_request_id())
    resolved_video_type = video_type
    if mode == "video" and not resolved_video_type:
        payload_video_type = payload.get("aigc_video_type")
        if payload_video_type is not None:
            resolved_video_type = str(payload_video_type).strip() or None
    if mode == "video" and "material_packages" in payload:
        target_type = getattr(args, "aigc_video_type", None) or resolved_video_type or "AIGC video"
        raise CliError(
            f"{target_type} create no longer accepts material_packages. "
            "Use product_video_info with product_info_list, input_video_list, and input_image_list."
        )
    upload_images = mode == "image-animation" or (mode == "video" and resolved_video_type == "TRYON")
    context = fetch_product_context(
        args,
        client=client,
        advertiser_id=advertiser_id,
        upload_images=upload_images,
    )
    if context:
        payload = apply_product_context_to_aigc_payload(
            payload,
            args,
            context=context,
            mode=mode,
            video_type=resolved_video_type,
        )
    return payload


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
    filtering = load_payload(args, label="filtering")
    voice_ids = non_empty_list(getattr(args, "voice_ids", None))
    if voice_ids:
        filtering["voice_ids"] = voice_ids
    assign_if_present(filtering, "language", getattr(args, "language", None))
    assign_if_present(filtering, "gender", getattr(args, "gender", None))
    assign_if_present(filtering, "speaker_type", getattr(args, "speaker_type", None))
    assign_if_present(filtering, "tag_type", getattr(args, "tag_type", None))
    tag_names = non_empty_list(getattr(args, "tag_names", None))
    if tag_names:
        filtering["tag_names"] = tag_names
    return filtering or None


def build_tiktok_aigc_task_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    assign_if_present(filtering, "status", getattr(args, "status", None))
    return filtering or None


def build_tiktok_aigc_video_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    assign_if_present(filtering, "status", getattr(args, "status", None))
    return filtering or None


def build_tiktok_digital_avatar_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    avatar_ids = non_empty_list(getattr(args, "avatar_ids", None))
    if avatar_ids:
        filtering["avatar_ids"] = avatar_ids
    assign_if_present(filtering, "identity", getattr(args, "identity", None))
    assign_if_present(filtering, "keyword", getattr(args, "keyword", None))
    tag_groups = parse_json_arg(getattr(args, "tag_groups_json", None), "tag_groups", (dict, list))
    tag_type = getattr(args, "tag_type", None)
    tags = non_empty_list(getattr(args, "tags", None))
    if tag_type and tags:
        shorthand_group = {"tag_type": str(tag_type), "tags": tags}
        if tag_groups is None:
            tag_groups = [shorthand_group]
        elif isinstance(tag_groups, dict):
            tag_groups = [tag_groups, shorthand_group]
        else:
            tag_groups = [*tag_groups, shorthand_group]
    if tag_groups is not None:
        filtering["tag_groups"] = tag_groups
    return filtering or None


def build_tiktok_digital_avatar_video_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    assign_if_present(filtering, "status", getattr(args, "status", None))
    assign_if_present(filtering, "avatar_id", getattr(args, "avatar_id", None))
    assign_if_present(filtering, "start_date", getattr(args, "start_date", None))
    assign_if_present(filtering, "end_date", getattr(args, "end_date", None))
    return filtering or None


def avatar_identity_from_item(item: dict[str, Any]) -> str | None:
    for group in item.get("tag_groups") or []:
        if not isinstance(group, dict):
            continue
        if str(group.get("tag_type") or "").strip() != "identity":
            continue
        tags = [str(tag).strip() for tag in (group.get("tags") or []) if str(tag).strip()]
        if tags:
            return tags[0]
    return None


def validate_avatar_product_avatar_identity(
    *,
    client: TikTokClient,
    advertiser_id: str,
    payload: dict[str, Any],
) -> None:
    product_video_info = payload.get("product_video_info")
    if not isinstance(product_video_info, dict):
        return
    avatar_info = product_video_info.get("avatar_info")
    if not isinstance(avatar_info, dict):
        return
    avatar_id = str(avatar_info.get("avatar_id") or "").strip()
    if not avatar_id:
        return
    try:
        response = client.list_digital_avatars(
            advertiser_id,
            filtering={"avatar_ids": [avatar_id]},
            page=1,
            page_size=20,
        )
    except CliError:
        return
    avatar = first_dict(extract_response_list(response, "list")) or {}
    identity = avatar_identity_from_item(avatar)
    if identity and identity != "real":
        raise CliError(
            f"AVATAR_PRODUCT only supports real avatars per TikTok docs; avatar {avatar_id} has identity={identity!r}. "
            "Use a real avatar or omit --avatar-id to let TikTok auto-select one."
        )


def validate_digital_avatar_create_payload(payload: dict[str, Any]) -> None:
    packages = payload.get("material_packages")
    if not isinstance(packages, list):
        raise CliError("Digital avatar create requires material_packages")
    dict_packages = [item for item in packages if isinstance(item, dict)]
    if not dict_packages:
        raise CliError("Digital avatar create requires at least one material_packages item")
    if len(dict_packages) > 5:
        raise CliError("Digital avatar create supports at most 5 material_packages items")

    for index, package in enumerate(dict_packages, start=1):
        script = str(package.get("script") or "").strip()
        if not script:
            raise CliError(f"Digital avatar create requires material_packages[{index - 1}].script")
        if len(script) > 2000:
            raise CliError(f"Digital avatar create requires material_packages[{index - 1}].script <= 2000 chars")

        video_name = str(package.get("video_name") or "").strip()
        if video_name and len(video_name) > 50:
            raise CliError(f"Digital avatar create requires material_packages[{index - 1}].video_name <= 50 chars")

        voice_volume = package.get("voice_volume")
        if voice_volume is not None:
            try:
                numeric_volume = float(voice_volume)
            except (TypeError, ValueError) as exc:
                raise CliError(
                    f"Digital avatar create requires material_packages[{index - 1}].voice_volume to be numeric"
                ) from exc
            if not 0 <= numeric_volume <= 10:
                raise CliError(f"Digital avatar create requires material_packages[{index - 1}].voice_volume between 0 and 10")

        voice_speed = str(package.get("voice_speed") or "").strip()
        if voice_speed and voice_speed not in TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES:
            raise CliError(
                "Digital avatar create requires material_packages"
                f"[{index - 1}].voice_speed in {TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES}"
            )


def build_campaign_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    campaign_ids = non_empty_list(getattr(args, "campaign_ids", None))
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    assign_if_present(filtering, "campaign_name", getattr(args, "name", None))
    assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


def build_gmv_max_campaign_filtering(args: argparse.Namespace) -> dict[str, Any]:
    filtering = load_payload(args, label="filtering")
    promotion_types = non_empty_list(getattr(args, "gmv_max_promotion_types", None))
    if promotion_types:
        filtering["gmv_max_promotion_types"] = promotion_types
    else:
        filtering.setdefault("gmv_max_promotion_types", ["PRODUCT_GMV_MAX"])
    campaign_ids = non_empty_list(getattr(args, "campaign_ids", None))
    store_ids = non_empty_list(getattr(args, "store_ids", None))
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    if store_ids:
        filtering["store_ids"] = store_ids
    assign_if_present(filtering, "campaign_name", getattr(args, "name", None))
    assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    assign_if_present(filtering, "creation_filter_start_time", getattr(args, "creation_filter_start_time", None))
    assign_if_present(filtering, "creation_filter_end_time", getattr(args, "creation_filter_end_time", None))
    return filtering


def build_campaign_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    assign_if_present(payload, "campaign_name", args.name)
    assign_if_present(payload, "objective_type", args.objective_type)
    assign_if_present(payload, "budget", args.budget)
    assign_if_present(payload, "budget_mode", args.budget_mode)
    assign_if_present(payload, "operation_status", args.operation_status)
    assign_if_present(payload, "campaign_type", args.campaign_type)
    assign_if_present(payload, "sales_destination", args.sales_destination)
    assign_if_present(payload, "optimization_goal", args.optimization_goal)
    assign_if_present(payload, "app_id", getattr(args, "app_id", None))
    assign_if_present(payload, "app_promotion_type", getattr(args, "app_promotion_type", None))
    assign_if_present(payload, "bid_align_type", getattr(args, "bid_align_type", None))
    assign_if_present(payload, "campaign_app_profile_page_state", getattr(args, "campaign_app_profile_page_state", None))
    assign_if_present(payload, "page_id", getattr(args, "page_id", None))
    assign_if_present(payload, "disable_skan_campaign", getattr(args, "disable_skan_campaign", None))
    assign_if_present(payload, "budget_optimize_on", getattr(args, "budget_optimize_on", None))
    assign_if_present(payload, "postback_window_mode", getattr(args, "postback_window_mode", None))
    assign_if_present(payload, "is_advanced_dedicated_campaign", getattr(args, "is_advanced_dedicated_campaign", None))
    if args.special_industries:
        payload["special_industries"] = args.special_industries
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", generate_tiktok_request_id())
    validate_non_empty(payload.get("campaign_name"), "campaign_name")
    validate_non_empty(payload.get("objective_type"), "objective_type")
    return payload


def build_campaign_update_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["campaign_id"] = validate_non_empty(args.campaign_id, "campaign_id")
    assign_if_present(payload, "campaign_name", args.name)
    assign_if_present(payload, "budget", args.budget)
    assign_if_present(payload, "po_number", args.po_number)
    if args.special_industries:
        payload["special_industries"] = args.special_industries
    return payload


def build_campaign_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["campaign_ids"] = non_empty_list(args.campaign_ids)
    payload["operation_status"] = validate_non_empty(args.operation_status, "operation_status")
    assign_if_present(payload, "postback_window_mode", args.postback_window_mode)
    return payload


def build_adgroup_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    adgroup_ids = non_empty_list(getattr(args, "adgroup_ids", None))
    campaign_ids = non_empty_list(getattr(args, "campaign_ids", None))
    if adgroup_ids:
        filtering["adgroup_ids"] = adgroup_ids
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    assign_if_present(filtering, "adgroup_name", getattr(args, "name", None))
    assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    assign_if_present(filtering, "optimization_goal", getattr(args, "optimization_goal", None))
    assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


def build_adgroup_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", generate_tiktok_request_id())
    assign_if_present(payload, "campaign_id", args.campaign_id)
    assign_if_present(payload, "adgroup_name", args.name)
    assign_if_present(payload, "billing_event", args.billing_event)
    assign_if_present(payload, "optimization_goal", args.optimization_goal)
    assign_if_present(payload, "operation_status", args.operation_status)
    assign_if_present(payload, "budget", args.budget)
    assign_if_present(payload, "budget_mode", args.budget_mode)
    assign_if_present(payload, "bid_price", args.bid_price)
    assign_if_present(payload, "bid_type", args.bid_type)
    assign_if_present(payload, "promotion_type", args.promotion_type)
    assign_if_present(payload, "promotion_website_type", args.promotion_website_type)
    assign_if_present(payload, "pixel_id", args.pixel_id)
    assign_if_present(payload, "schedule_start_time", args.start_time)
    assign_if_present(payload, "schedule_end_time", args.end_time)
    assign_if_present(payload, "schedule_type", args.schedule_type)
    assign_if_present(payload, "placement_type", args.placement_type)
    if args.placements:
        payload["placements"] = args.placements
    targeting_spec = parse_json_arg(getattr(args, "targeting_spec_json", None), "targeting_spec", dict)
    if targeting_spec is not None:
        payload["targeting_spec"] = targeting_spec
    for key in ("campaign_id", "adgroup_name", "billing_event", "optimization_goal"):
        validate_non_empty(payload.get(key), key)
    return payload


def build_adgroup_update_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = validate_non_empty(args.adgroup_id, "adgroup_id")
    assign_if_present(payload, "adgroup_name", args.name)
    assign_if_present(payload, "budget", args.budget)
    assign_if_present(payload, "bid_price", args.bid_price)
    assign_if_present(payload, "roas_bid", args.roas_bid)
    assign_if_present(payload, "conversion_bid_price", args.conversion_bid_price)
    assign_if_present(payload, "comment_disabled", args.comment_disabled)
    assign_if_present(payload, "share_disabled", args.share_disabled)
    assign_if_present(payload, "pacing", args.pacing)
    assign_if_present(payload, "schedule_start_time", args.start_time)
    assign_if_present(payload, "schedule_end_time", args.end_time)
    assign_if_present(payload, "schedule_type", args.schedule_type)
    assign_if_present(payload, "targeting_optimization_mode", args.targeting_optimization_mode)
    targeting_spec = parse_json_arg(getattr(args, "targeting_spec_json", None), "targeting_spec", dict)
    if targeting_spec is not None:
        payload["targeting_spec"] = targeting_spec
    return payload


def build_adgroup_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_ids"] = non_empty_list(args.adgroup_ids)
    payload["operation_status"] = validate_non_empty(args.operation_status, "operation_status")
    if getattr(args, "allow_partial_success", None) is not None:
        payload["allow_partial_success"] = args.allow_partial_success
    return payload


def build_ad_filtering(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    ad_ids = non_empty_list(getattr(args, "ad_ids", None))
    adgroup_ids = non_empty_list(getattr(args, "adgroup_ids", None))
    campaign_ids = non_empty_list(getattr(args, "campaign_ids", None))
    if ad_ids:
        filtering["smart_plus_ad_ids" if smart_plus else "ad_ids"] = ad_ids
    if adgroup_ids:
        filtering["adgroup_ids"] = adgroup_ids
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    if not smart_plus:
        assign_if_present(filtering, "ad_name", getattr(args, "name", None))
    assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    assign_if_present(filtering, "optimization_goal", getattr(args, "optimization_goal", None))
    assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


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
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != [] and value != {}
    }


SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES = {"BUDGET_MODE_TOTAL", "BUDGET_MODE_DYNAMIC_DAILY_BUDGET"}


def parse_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def max_positive_float(values: list[Any]) -> float | None:
    parsed_values = [parsed for item in values if (parsed := parse_positive_float(item)) is not None]
    return max(parsed_values) if parsed_values else None


def merge_source_snapshot(summary: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged = dict(summary)
    merged.update(detail)
    return merged


def get_campaign_automation_type(source_campaign: dict[str, Any]) -> str | None:
    value = source_campaign.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_campaign_type(source_campaign: dict[str, Any]) -> bool:
    automation_type = get_campaign_automation_type(source_campaign)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    return bool(source_campaign.get("is_smart_performance_campaign"))


def get_adgroup_automation_type(source_adgroup: dict[str, Any]) -> str | None:
    value = source_adgroup.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_adgroup_type(source_adgroup: dict[str, Any], *, campaign_smart_plus: bool) -> bool:
    automation_type = get_adgroup_automation_type(source_adgroup)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    if source_adgroup.get("smart_plus_adgroup_id"):
        return True
    return campaign_smart_plus or bool(source_adgroup.get("is_smart_performance_campaign"))


def get_ad_automation_type(source_ad: dict[str, Any]) -> str | None:
    value = source_ad.get("campaign_automation_type")
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized or None
    return None


def is_smart_plus_ad_type(source_ad: dict[str, Any], *, adgroup_smart_plus: bool) -> bool:
    automation_type = get_ad_automation_type(source_ad)
    if automation_type == "MANUAL":
        return False
    if automation_type and "SMART_PLUS" in automation_type:
        return True
    if source_ad.get("smart_plus_ad_id"):
        return True
    if any(key in source_ad for key in SMART_PLUS_AD_COPY_FIELDS):
        return True
    return adgroup_smart_plus


def normalize_copy_schedule_fields(payload: dict[str, Any]) -> None:
    start_time = payload.get("schedule_start_time")
    parsed_start: datetime | None = None
    if isinstance(start_time, str) and start_time.strip():
        try:
            parsed_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            parsed_start = None
    if parsed_start is None or parsed_start <= datetime.now():
        payload["schedule_start_time"] = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")


def infer_campaign_copy_strategy(
    source_campaign: dict[str, Any],
    source_adgroups: list[dict[str, Any]],
    source_ads: list[dict[str, Any]],
    *,
    smart_plus: bool,
) -> dict[str, Any]:
    automation_type = get_campaign_automation_type(source_campaign)
    objective_type = source_campaign.get("objective_type") or source_campaign.get("objective")
    source_campaign_budget = parse_positive_float(source_campaign.get("budget"))
    source_adgroup_budget = max_positive_float([adgroup.get("budget") for adgroup in source_adgroups])
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
) -> dict[str, Any]:
    normalized = compact_mapping(dict(payload))
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
        budget_value = parse_positive_float(normalized.get("budget"))
        if budget_mode not in SMART_PLUS_APP_PROMOTION_CREATE_BUDGET_MODES:
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
                    raise CliError(
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

    return compact_mapping(normalized)


def infer_adgroup_copy_strategy(
    source_adgroup: dict[str, Any],
    *,
    campaign_strategy: dict[str, Any],
) -> dict[str, Any]:
    smart_plus = is_smart_plus_adgroup_type(
        source_adgroup,
        campaign_smart_plus=bool(campaign_strategy.get("smart_plus")),
    )
    automation_type = get_adgroup_automation_type(source_adgroup) or campaign_strategy.get("campaign_automation_type")
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
) -> dict[str, Any]:
    automation_type = get_ad_automation_type(source_ad) or adgroup_strategy.get("automation_type")
    smart_plus = is_smart_plus_ad_type(source_ad, adgroup_smart_plus=bool(adgroup_strategy.get("smart_plus")))
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


def sanitize_normal_ad_copy_creative(creative: dict[str, Any]) -> dict[str, Any]:
    sanitized = compact_mapping(creative)
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
) -> dict[str, Any]:
    copy_strategy = copy_strategy or {}
    payload = compact_mapping(
        {
            "advertiser_id": advertiser_id,
            "request_id": generate_tiktok_request_id(),
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
        assign_if_present(payload, "catalog_type", copy_strategy.get("catalog_type"))
        assign_if_present(payload, "smart_plus_adgroup_mode", copy_strategy.get("smart_plus_adgroup_mode"))
    special_industries = source_campaign.get("special_industries")
    if isinstance(special_industries, list) and special_industries:
        payload["special_industries"] = special_industries
    if copy_strategy.get("target_smart_plus"):
        return normalize_smartplus_app_campaign_payload(
            payload,
            fallback_budget=copy_strategy.get("campaign_budget"),
        )
    return compact_mapping(payload)


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
    adgroup_strategy = adgroup_strategy or {}
    payload = {
        "advertiser_id": advertiser_id,
        "request_id": generate_tiktok_request_id(),
        "campaign_id": campaign_id,
        "adgroup_name": source_adgroup.get("adgroup_name"),
        "operation_status": operation_status or source_adgroup.get("operation_status") or "DISABLE",
    }
    for key in ADGROUP_COPY_DIRECT_FIELDS:
        if key in source_adgroup:
            payload[key] = source_adgroup.get(key)
    targeting_spec = source_adgroup.get("targeting_spec")
    if isinstance(targeting_spec, dict) and targeting_spec:
        if adgroup_strategy.get("target_smart_plus"):
            payload["targeting_spec"] = targeting_spec
        else:
            payload.update(targeting_spec)
    else:
        for key in ADGROUP_COPY_FALLBACK_TARGETING_FIELDS:
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
        assign_if_present(payload, "catalog_id", source_adgroup.get("catalog_id"))
        assign_if_present(payload, "catalog_authorized_bc_id", source_adgroup.get("catalog_authorized_bc_id"))
        assign_if_present(payload, "product_source", source_adgroup.get("product_source"))
    normalize_copy_schedule_fields(payload)
    return compact_mapping(payload)


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
    if smart_plus:
        ad_id = ad.get("smart_plus_ad_id") or ad.get("ad_id")
    else:
        ad_id = ad.get("ad_id")
    return validate_non_empty(ad_id, "ad_id")


def should_override_landing_page(
    promotion_type: Any,
    existing_urls: list[str],
    override_url: str | None,
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
) -> dict[str, Any]:
    ad_strategy = ad_strategy or {}
    if ad_strategy.get("copy_mode") == "app_tiktok_item":
        creative = sanitize_normal_ad_copy_creative(
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
        creative_info = first_dict(source_ad.get("creative_list") or []) or {}
        creative_info = creative_info.get("creative_info") if isinstance(creative_info, dict) else {}
        creative_info = creative_info if isinstance(creative_info, dict) else {}
        ad_configuration = source_ad.get("ad_configuration") if isinstance(source_ad.get("ad_configuration"), dict) else {}
        tracking_info = ad_configuration.get("tracking_info") if isinstance(ad_configuration.get("tracking_info"), dict) else {}
        ad_text_list = source_ad.get("ad_text_list") if isinstance(source_ad.get("ad_text_list"), list) else []
        ad_text = None
        if ad_text_list:
            first_text = first_dict([item for item in ad_text_list if isinstance(item, dict)])
            if first_text:
                ad_text = first_text.get("ad_text")

        video_info = creative_info.get("video_info") if isinstance(creative_info.get("video_info"), dict) else {}
        video_ids = extract_video_ids(source_ad, smart_plus=True)
        image_ids = extract_image_ids(source_ad, smart_plus=True)
        if not image_ids and client and video_ids:
            video_id = video_ids[0]
            try:
                video_info_response = client.get_video_info(advertiser_id, [video_id])
                video_entries = video_info_response.get("data", {}).get("list", [])
                first_video = first_dict([item for item in video_entries if isinstance(item, dict)])
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
            except CliError:
                image_ids = []
        if not image_ids:
            raise CliError(
                "Unable to prepare a normal ad creative from SmartPlus source: missing image asset. "
                "The video cover upload did not return an image_id."
            )

        creative = sanitize_normal_ad_copy_creative(
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
            for key in NORMAL_AD_UPDATE_AUTOFILL_FIELDS
            if key in source_ad
        }
        if not creative.get("image_ids") and client:
            video_ids = extract_video_ids(source_ad, smart_plus=False)
            if video_ids:
                try:
                    video_info_response = client.get_video_info(advertiser_id, [video_ids[0]])
                    video_entries = video_info_response.get("data", {}).get("list", [])
                    first_video = first_dict([item for item in video_entries if isinstance(item, dict)])
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
                except CliError:
                    pass
    creative["operation_status"] = operation_status or source_ad.get("operation_status") or "ENABLE"
    if should_override_landing_page(
        promotion_type,
        extract_landing_page_urls(creative, smart_plus=False),
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
        "creatives": [sanitize_normal_ad_copy_creative(creative)],
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
) -> dict[str, Any]:
    ad_strategy = ad_strategy or {}
    payload = {
        "advertiser_id": advertiser_id,
        "adgroup_id": adgroup_id,
        "ad_name": source_ad.get("ad_name"),
        "operation_status": operation_status or source_ad.get("operation_status") or "ENABLE",
    }
    for key in SMART_PLUS_AD_COPY_FIELDS:
        if key in source_ad:
            payload[key] = source_ad.get(key)
    if should_override_landing_page(
        promotion_type,
        extract_landing_page_urls(payload, smart_plus=True),
        landing_page_url,
    ):
        payload["landing_page_url_list"] = [{"landing_page_url": landing_page_url}]
    return compact_mapping(payload)


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
    ad_id = validate_non_empty(existing_ad.get("ad_id"), "ad_id")
    creative = {"ad_id": ad_id}
    for key in NORMAL_AD_UPDATE_AUTOFILL_FIELDS:
        if key in existing_ad:
            creative[key] = existing_ad[key]
    return compact_mapping(creative)


def merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if value is not None:
            merged[key] = value
    return merged


def build_normal_ad_update_arg_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if getattr(args, "name", None):
        overrides["ad_name"] = args.name
    if getattr(args, "operation_status", None):
        overrides["operation_status"] = args.operation_status
    return overrides


def build_normal_ad_creatives(args: argparse.Namespace, *, for_update: bool) -> list[dict[str, Any]] | None:
    creatives = parse_json_arg(getattr(args, "creatives_json", None), "creatives", list)
    if creatives is None:
        return None
    if args.name:
        for creative in creatives:
            if isinstance(creative, dict):
                if for_update:
                    creative["ad_name"] = args.name
                elif not creative.get("ad_name"):
                    creative["ad_name"] = args.name
    if args.operation_status:
        for creative in creatives:
            if isinstance(creative, dict):
                if for_update:
                    creative["operation_status"] = args.operation_status
                elif not creative.get("operation_status"):
                    creative["operation_status"] = args.operation_status
    if for_update and args.ad_id:
        if len(creatives) == 1 and isinstance(creatives[0], dict) and not creatives[0].get("ad_id"):
            creatives[0]["ad_id"] = args.ad_id
    return creatives


def build_smart_plus_ad_json_fields(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    field_specs = [
        ("ad_configuration_json", "ad_configuration", dict),
        ("ad_text_list_json", "ad_text_list", list),
        ("auto_message_list_json", "auto_message_list", list),
        ("call_to_action_list_json", "call_to_action_list", list),
        ("creative_list_json", "creative_list", list),
        ("deeplink_list_json", "deeplink_list", list),
        ("interactive_add_on_list_json", "interactive_add_on_list", list),
        ("landing_page_url_list_json", "landing_page_url_list", list),
        ("page_list_json", "page_list", list),
    ]
    for arg_name, payload_key, expected_type in field_specs:
        value = parse_json_arg(getattr(args, arg_name, None), payload_key, expected_type)
        if value is not None:
            payload[payload_key] = value
    page_id = getattr(args, "page_id", None)
    if page_id:
        page_list = payload.get("page_list")
        if isinstance(page_list, list) and page_list:
            if all(isinstance(item, dict) and str(item.get("page_id") or "").strip() != str(page_id).strip() for item in page_list):
                page_list.append({"page_id": str(page_id).strip()})
        else:
            payload["page_list"] = [{"page_id": str(page_id).strip()}]


def build_ad_create_payload(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = validate_non_empty(args.adgroup_id, "adgroup_id")
    if smart_plus:
        assign_if_present(payload, "ad_name", args.name)
        assign_if_present(payload, "operation_status", args.operation_status)
        build_smart_plus_ad_json_fields(args, payload)
        validate_non_empty(payload.get("ad_name"), "ad_name")
    else:
        creatives = build_normal_ad_creatives(args, for_update=False)
        if creatives is not None:
            payload["creatives"] = creatives
        creatives_payload = payload.get("creatives")
        if not isinstance(creatives_payload, list) or not creatives_payload:
            raise CliError("Missing creatives: provide --creatives-json or payload.creatives")
    return payload


def build_ad_update_payload(
    args: argparse.Namespace,
    *,
    smart_plus: bool,
    existing_ad: dict[str, Any] | None = None,
) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    if smart_plus:
        payload["smart_plus_ad_id"] = validate_non_empty(args.ad_id, "ad_id")
        assign_if_present(payload, "ad_name", args.name)
        build_smart_plus_ad_json_fields(args, payload)
    else:
        existing_adgroup_id = existing_ad.get("adgroup_id") if isinstance(existing_ad, dict) else None
        payload["adgroup_id"] = validate_non_empty(
            payload.get("adgroup_id") or args.adgroup_id or existing_adgroup_id,
            "adgroup_id",
        )
        creatives = build_normal_ad_creatives(args, for_update=True)
        if creatives is not None:
            payload["creatives"] = creatives
        creatives_payload = payload.get("creatives")
        if existing_ad is not None and args.ad_id:
            base_creative = merge_mapping(
                build_normal_ad_update_base_creative(existing_ad),
                build_normal_ad_update_arg_overrides(args),
            )
            if not isinstance(creatives_payload, list) or not creatives_payload:
                creatives_payload = [base_creative]
            elif len(creatives_payload) == 1 and isinstance(creatives_payload[0], dict):
                creatives_payload = [merge_mapping(base_creative, creatives_payload[0])]
            payload["creatives"] = creatives_payload
        if not isinstance(creatives_payload, list) or not creatives_payload:
            raise CliError(
                "Missing creatives: provide --creatives-json or payload.creatives, "
                "or pass a single ad_id so the CLI can autofill from the current ad"
            )
        if args.patch_update is not None:
            payload["patch_update"] = args.patch_update
        has_ad_id = any(isinstance(item, dict) and item.get("ad_id") for item in creatives_payload)
        if not has_ad_id:
            raise CliError("Normal ad update requires ad_id inside payload.creatives or a single --ad-id injection target")
    return payload


def build_ad_status_payload(args: argparse.Namespace, *, smart_plus: bool) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["operation_status"] = validate_non_empty(args.operation_status, "operation_status")
    if smart_plus:
        payload["smart_plus_ad_ids"] = non_empty_list(args.ad_ids)
    else:
        payload["ad_ids"] = non_empty_list(args.ad_ids)
        aco_ad_ids = non_empty_list(getattr(args, "aco_ad_ids", None))
        if aco_ad_ids:
            payload["aco_ad_ids"] = aco_ad_ids
    return payload


def build_video_search_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    video_ids = non_empty_list(getattr(args, "video_ids", None))
    material_ids = non_empty_list(getattr(args, "material_ids", None))
    if video_ids:
        filtering["video_ids"] = video_ids
    if material_ids:
        filtering["material_ids"] = material_ids
    assign_if_present(filtering, "displayable", args.displayable)
    assign_if_present(filtering, "width", args.width)
    assign_if_present(filtering, "height", args.height)
    ratio = parse_json_arg(getattr(args, "ratio_json", None), "ratio", list)
    if ratio is not None:
        filtering["ratio"] = ratio
    return filtering or None


def extract_response_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def extract_response_strings(payload: dict[str, Any], *keys: str) -> list[str]:
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


def ensure_task_create_response(response: dict[str, Any], *, label: str) -> dict[str, Any]:
    task_ids = extract_response_strings(response, "task_ids")
    if not task_ids:
        task_ids = [
            str(item.get("task_id")).strip()
            for item in extract_response_list(response, "list")
            if isinstance(item, dict) and str(item.get("task_id") or "").strip()
        ]
    if task_ids:
        return response
    request_id = response.get("request_id")
    message = response.get("message") or response.get("msg") or "empty task_ids"
    raise CliError(f"{label} did not return any task_ids (request_id={request_id}, message={message})")


def filter_response_items_by_ids(
    response: dict[str, Any],
    *,
    list_key: str,
    item_key: str,
    allowed_ids: set[str],
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
    task_items = extract_response_list(task_response, "list")
    allowed_video_ids = {
        str(item.get("video_id")).strip()
        for item in task_items
        if isinstance(item, dict) and str(item.get("task_id") or "").strip() in normalized_task_ids and item.get("video_id")
    }
    return filter_response_items_by_ids(response, list_key="list", item_key="video_id", allowed_ids=allowed_video_ids)


def filter_digital_avatar_video_list_response(
    response: dict[str, Any],
    *,
    client: TikTokClient,
    advertiser_id: str,
    task_ids: list[str] | None,
) -> dict[str, Any]:
    normalized_task_ids = [str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()]
    if not normalized_task_ids:
        return response
    allowed_avatar_video_ids: set[str] = set()
    allowed_video_names: set[str] = set()
    for task_id in normalized_task_ids:
        task_response = client.get_digital_avatar_video_task(advertiser_id, task_id)
        task_items = extract_response_list(task_response, "list")
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
        return filter_response_items_by_ids(response, list_key="list", item_key="avatar_video_id", allowed_ids=allowed_avatar_video_ids)
    return filter_response_items_by_values(response, list_key="list", item_key="video_name", allowed_values=allowed_video_names)


def build_validation_result(kind: str, advertiser_id: str, *, smart_plus: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "kind": kind,
        "advertiser_id": advertiser_id,
        "smart_plus": smart_plus,
        "errors": [],
        "warnings": [],
        "checks": [],
    }


def add_validation_error(result: dict[str, Any], code: str, message: str, **context: Any) -> None:
    issue = {"code": code, "message": message}
    if context:
        issue["context"] = context
    result["errors"].append(issue)
    result["ok"] = False


def add_validation_warning(result: dict[str, Any], code: str, message: str, **context: Any) -> None:
    issue = {"code": code, "message": message}
    if context:
        issue["context"] = context
    result["warnings"].append(issue)


def add_validation_check(result: dict[str, Any], name: str, ok: bool, **details: Any) -> None:
    item = {"name": name, "ok": ok}
    if details:
        item["details"] = details
    result["checks"].append(item)
    if not ok:
        result["ok"] = False


def first_dict(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[0] if items else None


def collect_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, dict):
        results: list[str] = []
        for item in value.values():
            results.extend(collect_strings(item))
        return results
    if isinstance(value, list):
        results: list[str] = []
        for item in value:
            results.extend(collect_strings(item))
        return results
    return []


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    return str(value).strip().casefold() if value is not None else ""


def resolve_tiktok_app_promotion_type(platform: Any) -> str | None:
    platform_value = normalize_text(platform)
    if platform_value == "android":
        return "APP_ANDROID"
    if platform_value == "ios":
        return "APP_IOS"
    return None


def summarize_tiktok_template_campaign(campaign: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
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


def summarize_tiktok_template_adgroup(adgroup: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
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


def summarize_tiktok_template_ad(ad: dict[str, Any]) -> dict[str, Any]:
    tracking_info = ((ad.get("ad_configuration") or {}).get("tracking_info") or {}) if isinstance(ad, dict) else {}
    creative = first_dict(ad.get("creative_list") or []) or {}
    creative_info = creative.get("creative_info") or {}
    video_info = creative_info.get("video_info") or {}
    return compact_mapping(
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
) -> dict[str, Any] | None:
    if app_id:
        for app in apps:
            if str(app.get("app_id")) == str(app_id):
                return app
        return None
    if app_name:
        target = normalize_text(app_name)
        exact_matches = [app for app in apps if normalize_text(app.get("app_name")) == target]
        if exact_matches:
            return exact_matches[0]
        partial_matches = [app for app in apps if target in normalize_text(app.get("app_name"))]
        if partial_matches:
            return partial_matches[0]
        return None
    return None


def extract_tiktok_creative_portfolio_contents(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    contents = portfolio.get("portfolio_content")
    if isinstance(contents, list):
        return [item for item in contents if isinstance(item, dict)]
    if isinstance(contents, dict):
        return [contents]
    legacy_content = portfolio.get("creative_portfolio_content")
    if isinstance(legacy_content, dict):
        return [legacy_content]
    return []


def summarize_tiktok_creative_portfolio_content(content: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
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


def summarize_tiktok_creative_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    contents = extract_tiktok_creative_portfolio_contents(portfolio)
    first_content = first_dict(contents) or {}
    content_summary = summarize_tiktok_creative_portfolio_content(first_content) if first_content else None
    return compact_mapping(
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


def build_creative_portfolio_filtering(args: argparse.Namespace) -> dict[str, Any] | None:
    filtering = load_payload(args, label="filtering")
    portfolio_ids = non_empty_list(getattr(args, "creative_portfolio_ids", None))
    portfolio_types = non_empty_list(getattr(args, "creative_portfolio_types", None))
    if portfolio_ids:
        filtering["creative_portfolio_ids"] = portfolio_ids
    if portfolio_types:
        filtering["creative_portfolio_types"] = portfolio_types
    return filtering or None


def build_optional_object_from_json_args(
    args: argparse.Namespace,
    *,
    json_attr: str,
    file_attr: str,
    label: str,
) -> dict[str, Any] | None:
    json_value = getattr(args, json_attr, None)
    file_value = getattr(args, file_attr, None)
    if json_value and file_value:
        raise CliError(f"Specify only one of --{json_attr.replace('_', '-')} or --{file_attr.replace('_', '-')}")
    if file_value:
        payload = load_json_file(Path(file_value))
        if not isinstance(payload, dict):
            raise CliError(f"Invalid {label}: expected a JSON object in --{file_attr.replace('_', '-')}")
        return payload
    if json_value:
        payload = parse_json_arg(json_value, label, dict)
        if payload is not None:
            return payload
    return None


def build_creative_portfolio_content_from_args(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    content_json = getattr(args, "portfolio_content_json", None)
    content_file = getattr(args, "portfolio_content_file", None)
    if content_json and content_file:
        raise CliError("Specify only one of --portfolio-content-json or --portfolio-content-file")
    if content_file:
        portfolio_content = load_json_file(Path(content_file))
        if not isinstance(portfolio_content, list):
            raise CliError("Invalid portfolio_content: expected a JSON array in --portfolio-content-file")
        return [item for item in portfolio_content if isinstance(item, dict)]
    if content_json:
        portfolio_content = parse_json_arg(content_json, "portfolio_content", list)
        if portfolio_content is not None:
            if not all(isinstance(item, dict) for item in portfolio_content):
                raise CliError("Invalid portfolio_content: expected a JSON array of objects")
            return portfolio_content

    content = compact_mapping(
        {
            "title": getattr(args, "title", None),
            "ad_text": getattr(args, "ad_text", None),
            "asset_content": getattr(args, "asset_content", None),
            "primary_text": getattr(args, "primary_text", None),
            "secondary_text": getattr(args, "secondary_text", None),
            "description": getattr(args, "description", None),
            "content_url": getattr(args, "content_url", None),
            "card_type": getattr(args, "card_type", None),
            "app_id": getattr(args, "app_id", None),
            "origin_app_id": getattr(args, "origin_app_id", None),
            "image_id": getattr(args, "image_id", None),
            "jump_image_uri": getattr(args, "jump_image_uri", None),
            "thumbnail_id": getattr(args, "thumbnail_id", None),
            "video_id": getattr(args, "video_id", None),
            "identity_id": getattr(args, "identity_id", None),
            "identity_type": getattr(args, "identity_type", None),
            "identity_authorized_bc_id": getattr(args, "identity_authorized_bc_id", None),
            "call_to_action": getattr(args, "call_to_action", None),
            "call_to_action_text": getattr(args, "call_to_action_text", None),
            "product_source": getattr(args, "product_source", None),
            "product_set_id": getattr(args, "product_set_id", None),
            "product_platform_id": getattr(args, "product_platform_id", None),
            "product_specific_type": getattr(args, "product_specific_type", None),
            "catalog_id": getattr(args, "catalog_id", None),
            "catalog_authorized_bc_id": getattr(args, "catalog_authorized_bc_id", None),
            "store_id": getattr(args, "store_id", None),
            "store_authorized_bc_id": getattr(args, "store_authorized_bc_id", None),
            "category_label": getattr(args, "category_label", None),
            "card_show_price": getattr(args, "card_show_price", None),
            "card_image_index": getattr(args, "card_image_index", None),
            "display_price_enabled": getattr(args, "display_price_enabled", None),
            "image_optimization_enabled": getattr(args, "image_optimization_enabled", None),
            "gesture_type": getattr(args, "gesture_type", None),
            "interactive_music_id": getattr(args, "interactive_music_id", None),
            "vertical_creative_strategy": getattr(args, "vertical_creative_strategy", None),
            "vertical_video_strategy": getattr(args, "vertical_video_strategy", None),
            "slide_length": getattr(args, "slide_length", None),
            "advanced_show_time": getattr(args, "advanced_show_time", None),
            "advanced_interact_type": getattr(args, "advanced_interact_type", None),
            "advanced_interact_shape": getattr(args, "advanced_interact_shape", None),
            "content_url": getattr(args, "content_url", None),
        }
    )
    tags = non_empty_list(getattr(args, "tags", None))
    if tags:
        content["tags"] = tags
    card_tags = non_empty_list(getattr(args, "card_tags", None))
    if card_tags:
        content["card_tags"] = card_tags
    asset_ids = non_empty_list(getattr(args, "asset_ids", None))
    if asset_ids:
        content["asset_ids"] = asset_ids
    sku_ids = non_empty_list(getattr(args, "sku_ids", None))
    if sku_ids:
        content["sku_ids"] = sku_ids
    item_group_ids = non_empty_list(getattr(args, "item_group_ids", None))
    if item_group_ids:
        content["item_group_ids"] = item_group_ids
    selling_points = non_empty_list(getattr(args, "selling_points", None))
    if selling_points:
        content["selling_points"] = selling_points
    country_codes = non_empty_list(getattr(args, "country_codes", None))
    if country_codes:
        content["country_code"] = country_codes
    layouts = non_empty_list(getattr(args, "layouts", None))
    if layouts:
        content["layouts"] = layouts

    advanced_audio_info = build_optional_object_from_json_args(
        args,
        json_attr="advanced_audio_info_json",
        file_attr="advanced_audio_info_file",
        label="advanced_audio_info",
    ) or {}
    advanced_gesture_icon = build_optional_object_from_json_args(
        args,
        json_attr="advanced_gesture_icon_json",
        file_attr="advanced_gesture_icon_file",
        label="advanced_gesture_icon",
    ) or {}
    advanced_gesture_image = build_optional_object_from_json_args(
        args,
        json_attr="advanced_gesture_image_json",
        file_attr="advanced_gesture_image_file",
        label="advanced_gesture_image",
    ) or {}
    advanced_image_info = build_optional_object_from_json_args(
        args,
        json_attr="advanced_image_info_json",
        file_attr="advanced_image_info_file",
        label="advanced_image_info",
    ) or {}
    advanced_position = build_optional_object_from_json_args(
        args,
        json_attr="advanced_position_json",
        file_attr="advanced_position_file",
        label="advanced_position",
    ) or {}
    badge_image_info = build_optional_object_from_json_args(
        args,
        json_attr="badge_image_info_json",
        file_attr="badge_image_info_file",
        label="badge_image_info",
    ) or {}
    sticker_param = build_optional_object_from_json_args(
        args,
        json_attr="sticker_param_json",
        file_attr="sticker_param_file",
        label="sticker_param",
    ) or {}
    slide_dimension = build_optional_object_from_json_args(
        args,
        json_attr="slide_dimension_json",
        file_attr="slide_dimension_file",
        label="slide_dimension",
    ) or {}
    showcase_products = getattr(args, "showcase_products_json", None)
    showcase_products_file = getattr(args, "showcase_products_file", None)
    if showcase_products and showcase_products_file:
        raise CliError("Specify only one of --showcase-products-json or --showcase-products-file")
    showcase_products_value: list[dict[str, Any]] | None = None
    if showcase_products_file:
        loaded = load_json_file(Path(showcase_products_file))
        if not isinstance(loaded, list):
            raise CliError("Invalid showcase_products: expected a JSON array in --showcase-products-file")
        showcase_products_value = [item for item in loaded if isinstance(item, dict)]
    elif showcase_products:
        loaded = parse_json_arg(showcase_products, "showcase_products", list)
        if loaded is not None:
            if not all(isinstance(item, dict) for item in loaded):
                raise CliError("Invalid showcase_products: expected a JSON array of objects")
            showcase_products_value = loaded

    if getattr(args, "advanced_audio_video_id", None) and not advanced_audio_info:
        advanced_audio_info = {"video_id": getattr(args, "advanced_audio_video_id")}
    if getattr(args, "advanced_gesture_icon_image_id", None) and not advanced_gesture_icon:
        advanced_gesture_icon = {"image_id": getattr(args, "advanced_gesture_icon_image_id")}
    if getattr(args, "advanced_gesture_image_image_id", None) and not advanced_gesture_image:
        advanced_gesture_image = {"image_id": getattr(args, "advanced_gesture_image_image_id")}

    if advanced_audio_info:
        content["advanced_audio_info"] = advanced_audio_info
    if advanced_gesture_icon:
        content["advanced_gesture_icon"] = advanced_gesture_icon
    if advanced_gesture_image:
        content["advanced_gesture_image"] = advanced_gesture_image
    if advanced_image_info:
        content["advanced_image_info"] = advanced_image_info
    if advanced_position:
        content["advanced_position"] = advanced_position
    if badge_image_info:
        content["badge_image_info"] = badge_image_info
    if sticker_param:
        content["sticker_param"] = sticker_param
    if slide_dimension:
        content["slide_dimension"] = slide_dimension
    if showcase_products_value:
        content["showcase_products"] = showcase_products_value

    if not content:
        return None
    return [content]


def build_creative_portfolio_create_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    assign_if_present(payload, "creative_portfolio_type", getattr(args, "creative_portfolio_type", None))

    portfolio_content = build_creative_portfolio_content_from_args(args)
    if portfolio_content is not None:
        payload["portfolio_content"] = portfolio_content
    context_info = build_optional_object_from_json_args(
        args,
        json_attr="context_info_json",
        file_attr="context_info_file",
        label="context_info",
    ) or {}
    if getattr(args, "context_app_id", None) is not None:
        context_info["app_id"] = getattr(args, "context_app_id")
    if getattr(args, "context_core_user_id", None) is not None:
        context_info["core_user_id"] = getattr(args, "context_core_user_id")
    if getattr(args, "context_developer_id", None) is not None:
        context_info["developer_id"] = getattr(args, "context_developer_id")
    if getattr(args, "context_x_forwarded_for", None) is not None:
        context_info["x_forwarded_for"] = getattr(args, "context_x_forwarded_for")
    if getattr(args, "context_x_real_ip", None) is not None:
        context_info["x_real_ip"] = getattr(args, "context_x_real_ip")
    if getattr(args, "context_user_agent", None) is not None:
        context_info["user_agent"] = getattr(args, "context_user_agent")
    if getattr(args, "context_referer", None) is not None:
        context_info["referer"] = getattr(args, "context_referer")
    if context_info:
        payload["context_info"] = context_info

    if "creative_portfolio_type" not in payload:
        payload["creative_portfolio_type"] = "CTA"
    validate_non_empty(payload.get("creative_portfolio_type"), "creative_portfolio_type")
    if not isinstance(payload.get("portfolio_content"), list) or not payload.get("portfolio_content"):
        raise CliError("Missing portfolio_content: provide nested content flags or --portfolio-content-json/--portfolio-content-file")
    return payload


def build_creative_asset_delete_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    image_ids = non_empty_list(getattr(args, "image_ids", None))
    video_ids = non_empty_list(getattr(args, "video_ids", None))
    if image_ids:
        payload["image_ids"] = image_ids
    if video_ids:
        payload["video_ids"] = video_ids
    if not payload.get("image_ids") and not payload.get("video_ids"):
        raise CliError("Creative asset delete requires at least one image id or video id")
    return payload


def build_creative_asset_share_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    material_ids = non_empty_list(getattr(args, "material_ids", None))
    shared_advertiser_ids = non_empty_list(getattr(args, "shared_advertiser_ids", None))
    assign_if_present(payload, "asset_type", getattr(args, "asset_type", None))
    if material_ids:
        payload["material_ids"] = material_ids
    if shared_advertiser_ids:
        payload["shared_advertiser_ids"] = shared_advertiser_ids
    if not payload.get("material_ids") or not payload.get("shared_advertiser_ids"):
        raise CliError("Creative asset share requires material_ids and shared_advertiser_ids")
    return payload


def build_creative_shareable_link_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_payload(args)
    shared_assets_json = getattr(args, "shared_assets_json", None)
    shared_assets_file = getattr(args, "shared_assets_file", None)
    if shared_assets_json and shared_assets_file:
        raise CliError("Specify only one of --shared-assets-json or --shared-assets-file")
    if shared_assets_file:
        shared_assets = load_json_file(Path(shared_assets_file))
        if not isinstance(shared_assets, list):
            raise CliError("Invalid shared_assets: expected a JSON array in --shared-assets-file")
        payload["shared_assets"] = shared_assets
    elif shared_assets_json:
        shared_assets = parse_json_arg(shared_assets_json, "shared_assets", list)
        if shared_assets is not None:
            payload["shared_assets"] = shared_assets
    assign_if_present(payload, "sharer", getattr(args, "sharer", None))
    validate_non_empty(payload.get("shared_assets"), "shared_assets")
    validate_non_empty(payload.get("sharer"), "sharer")
    return payload


def build_creative_smart_text_payload(args: argparse.Namespace) -> dict[str, Any]:
    advertiser_id = validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = validate_non_empty(getattr(args, "adgroup_id", None), "adgroup_id")
    assign_if_present(payload, "industry_id", getattr(args, "industry_id", None))
    keywords = non_empty_list(getattr(args, "keywords", None))
    if keywords:
        payload["keywords"] = keywords
    assign_if_present(payload, "language", getattr(args, "language", None))
    assign_if_present(payload, "limit", getattr(args, "limit", None))
    assign_if_present(payload, "param_type", getattr(args, "param_type", None))
    validate_non_empty(payload.get("industry_id"), "industry_id")
    return payload


def select_tiktok_creative_portfolios(
    portfolios: list[dict[str, Any]],
    *,
    creative_portfolio_ids: list[str] | None = None,
    creative_portfolio_types: list[str] | None = None,
    title: str | None = None,
    query: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    requested = compact_mapping(
        {
            "creative_portfolio_ids": creative_portfolio_ids,
            "creative_portfolio_types": creative_portfolio_types,
            "title": title,
            "query": query,
            "limit": limit,
        }
    )
    title_filter = normalize_text(title)
    query_filter = normalize_text(query)
    type_filters = {normalize_text(value) for value in (creative_portfolio_types or []) if normalize_text(value)}
    portfolio_id_filters = {str(value).strip() for value in (creative_portfolio_ids or []) if str(value).strip()}
    selected: list[dict[str, Any]] = []

    for portfolio in portfolios:
        if not isinstance(portfolio, dict):
            continue
        portfolio_id = portfolio.get("creative_portfolio_id")
        if portfolio_id_filters and str(portfolio_id) not in portfolio_id_filters:
            continue
        if type_filters and normalize_text(portfolio.get("creative_portfolio_type")) not in type_filters:
            continue

        contents = extract_tiktok_creative_portfolio_contents(portfolio)
        summary = summarize_tiktok_creative_portfolio(portfolio)
        searchable = " ".join(
            collect_strings(
                [
                    summary,
                    contents,
                ]
            )
        )
        searchable_text = normalize_text(searchable)
        score = 0
        if portfolio_id_filters:
            score += 100
        if title_filter:
            portfolio_title = normalize_text(summary.get("title"))
            if portfolio_title == title_filter:
                score += 30
            elif title_filter in portfolio_title or title_filter in searchable_text:
                score += 15
        if query_filter:
            if query_filter in searchable_text:
                score += 10
        if not title_filter and not query_filter and not portfolio_id_filters and not type_filters:
            score += 1
        selected.append(
            {
                "score": score,
                "summary": summary,
                "raw": portfolio,
            }
        )

    selected.sort(
        key=lambda item: (
            item["score"],
            item["summary"].get("modify_time") or "",
            item["summary"].get("create_time") or "",
        ),
        reverse=True,
    )
    limited = selected[: max(limit, 0)] if limit else selected
    return {
        "requested": requested,
        "candidate_count": len(selected),
        "selected_count": len(limited),
        "selected": [item["summary"] for item in limited],
        "best_match": limited[0]["summary"] if limited else None,
    }


def validate_smartplus_app_eligibility(
    client: TikTokClient,
    *,
    advertiser_id: str,
    payload: dict[str, Any],
    context_label: str,
) -> dict[str, Any] | None:
    if str(payload.get("objective_type") or "").upper() != "APP_PROMOTION":
        return None
    app_id = str(payload.get("app_id") or "").strip()
    if not app_id:
        return None

    app_info = client.get_app_info(advertiser_id, app_id)
    app = app_info.get("data", {}).get("app") if isinstance(app_info, dict) else None
    if not isinstance(app, dict):
        return None

    if app.get("advanced_dedicated_campaign_allowed") is False:
        app_name = app.get("app_name") or app_id
        platform = app.get("platform") or "UNKNOWN"
        pages_response = client.list_pages(
            advertiser_id,
            business_types=["APP_PROFILE_PAGE"],
            app_id=app_id,
            page=1,
            page_size=20,
        )
        pages = extract_response_list(pages_response, "list")
        page_summary = [
            compact_mapping(
                {
                    "page_id": page.get("page_id"),
                    "title": page.get("title"),
                    "status": page.get("status"),
                    "preview_url": page.get("preview_url"),
                    "destination_urls": page.get("destination_urls"),
                }
            )
            for page in pages
        ]
        page_hint = (
            f"App Profile Page candidates found: {page_summary}"
            if page_summary
            else "No App Profile Page candidates were returned by /page/get for this app."
        )
        raise CliError(
            "SmartPlus iOS app promotion is not enabled for this app in the current advertiser.\n"
            f"Context: {context_label}\n"
            f"App: {app_name} ({app_id}, platform={platform})\n"
            "TikTok app_info reports advanced_dedicated_campaign_allowed=false, so the request will be rejected before payload shape becomes relevant.\n"
            f"{page_hint}\n"
            "Next step: enable Advanced Dedicated Campaign / App Profile Page for this app in TikTok Ads Manager or ask your TikTok rep to unlock the iOS Smart+ path."
        )

    return app


def find_tiktok_smartplus_app_template(
    client: TikTokClient,
    *,
    advertiser_id: str,
    app_id: str | None = None,
    app_name: str | None = None,
    app_promotion_type: str | None = None,
) -> dict[str, Any]:
    requested = compact_mapping(
        {
            "app_id": app_id,
            "app_name": app_name,
            "app_promotion_type": app_promotion_type,
        }
    )
    apps = extract_response_list(client.list_apps(advertiser_id), "apps")
    selected_app = select_tiktok_app(apps, app_id=app_id, app_name=app_name)
    requested_app_id = str(selected_app.get("app_id")) if isinstance(selected_app, dict) and selected_app.get("app_id") else None
    expected_promotion_type = resolve_tiktok_app_promotion_type(
        selected_app.get("platform") if isinstance(selected_app, dict) else None
    )

    campaigns = collect_paginated_entities(
        lambda page, page_size: client.list_campaigns(
            advertiser_id,
            page=page,
            page_size=page_size,
            smart_plus=True,
        ),
        "list",
    )
    campaign_by_id = {
        str(campaign.get("campaign_id")): campaign
        for campaign in campaigns
        if campaign.get("campaign_id") and campaign.get("objective_type") == "APP_PROMOTION"
    }

    adgroups = collect_paginated_entities(
        lambda page, page_size: client.list_adgroups(
            advertiser_id,
            page=page,
            page_size=page_size,
            smart_plus=True,
        ),
        "list",
    )

    candidates: list[dict[str, Any]] = []
    for adgroup in adgroups:
        campaign_id = str(adgroup.get("campaign_id") or "")
        campaign = campaign_by_id.get(campaign_id)
        if campaign is None:
            continue
        adgroup_app_id = adgroup.get("app_id")
        if not adgroup_app_id:
            continue
        if requested_app_id and str(adgroup_app_id) != requested_app_id:
            continue
        score = 100
        if app_promotion_type:
            if campaign.get("app_promotion_type") == app_promotion_type:
                score += 20
            elif campaign.get("app_promotion_type") not in {None, app_promotion_type}:
                continue
        if expected_promotion_type:
            if adgroup.get("promotion_type") == expected_promotion_type:
                score += 10
            else:
                continue
        placements = set(adgroup.get("placements") or [])
        if "PLACEMENT_TIKTOK" in placements:
            score += 8
        if "PLACEMENT_GLOBAL_APP_BUNDLE" in placements:
            score += 2
        if "PLACEMENT_PANGLE" in placements:
            score += 1
        if adgroup.get("optimization_goal") == "INSTALL":
            score += 3
        if campaign.get("operation_status") == "ENABLE":
            score += 2
        if adgroup.get("operation_status") == "ENABLE":
            score += 1
        candidates.append(
            {
                "score": score,
                "campaign": campaign,
                "adgroup": adgroup,
            }
        )

    if not candidates:
        return {
            "requested": requested,
            "app": summarize_tiktok_app(selected_app) if selected_app else None,
            "candidate_count": 0,
            "template_campaign": None,
            "template_adgroup": None,
            "template_ad": None,
        }

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["campaign"].get("modify_time") or "",
            item["campaign"].get("create_time") or "",
        ),
        reverse=True,
    )
    best = candidates[0]
    best_campaign = best["campaign"]
    best_adgroup_summary = best["adgroup"]
    best_adgroup_id = validate_non_empty(best_adgroup_summary.get("adgroup_id"), "adgroup_id")
    best_adgroup = merge_source_snapshot(
        best_adgroup_summary,
        client.get_adgroup(advertiser_id, best_adgroup_id, smart_plus=True),
    )

    ads_result = client.list_ads(
        advertiser_id,
        filtering={"adgroup_ids": [best_adgroup_id]},
        page=1,
        page_size=20,
        smart_plus=True,
    )
    best_ad: dict[str, Any] | None = None
    for ad_summary in ads_result.get("data", {}).get("list", []):
        source_ad_id = extract_source_ad_id(ad_summary, smart_plus=True)
        ad_detail = merge_source_snapshot(
            ad_summary,
            client.get_ad(advertiser_id, source_ad_id, smart_plus=True),
        )
        tracking_app_id = (((ad_detail.get("ad_configuration") or {}).get("tracking_info") or {}).get("tracking_app_id"))
        if requested_app_id is None or str(tracking_app_id or best_adgroup.get("app_id")) == str(best_adgroup.get("app_id")):
            best_ad = ad_detail
            break
    if best_ad is None:
        ad_list = ads_result.get("data", {}).get("list", [])
        if ad_list:
            source_ad_id = extract_source_ad_id(ad_list[0], smart_plus=True)
            best_ad = merge_source_snapshot(
                ad_list[0],
                client.get_ad(advertiser_id, source_ad_id, smart_plus=True),
            )

    if selected_app is None:
        selected_app = first_dict(
            [
                app
                for app in apps
                if str(app.get("app_id")) == str(best_adgroup.get("app_id"))
            ]
        )

    return {
        "requested": requested,
        "app": summarize_tiktok_app(selected_app) if selected_app else compact_mapping({"app_id": best_adgroup.get("app_id")}),
        "candidate_count": len(candidates),
        "template_campaign": summarize_tiktok_template_campaign(best_campaign),
        "template_adgroup": summarize_tiktok_template_adgroup(best_adgroup),
        "template_ad": summarize_tiktok_template_ad(best_ad) if best_ad else None,
    }


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def build_validate_creative_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_payload(args)
    if payload:
        return payload

    landing_page_urls = non_empty_list(getattr(args, "landing_page_urls", None))
    image_ids = non_empty_list(getattr(args, "image_ids", None))
    image_web_uris = non_empty_list(getattr(args, "image_web_uris", None))
    tracking_offline_event_set_ids = non_empty_list(getattr(args, "tracking_offline_event_set_ids", None))

    if getattr(args, "smart_plus", False):
        creative_info = compact_mapping(
            {
                "identity_id": getattr(args, "identity_id", None),
                "identity_type": getattr(args, "identity_type", None),
                "identity_authorized_bc_id": getattr(args, "identity_authorized_bc_id", None),
            }
        )
        if getattr(args, "video_id", None):
            creative_info["video_info"] = {"video_id": args.video_id}
        if image_web_uris:
            creative_info["image_info"] = [{"web_uri": web_uri} for web_uri in image_web_uris]
        elif image_ids:
            creative_info["image_info"] = [{"image_id": image_id} for image_id in image_ids]

        payload = {}
        if creative_info:
            payload["creative_list"] = [{"creative_info": creative_info}]
        if landing_page_urls:
            payload["landing_page_url_list"] = [{"landing_page_url": url} for url in landing_page_urls]

        tracking_info = compact_mapping(
            {
                "tracking_app_id": getattr(args, "tracking_app_id", None),
                "tracking_pixel_id": getattr(args, "tracking_pixel_id", None),
                "tracking_offline_event_set_ids": tracking_offline_event_set_ids,
            }
        )
        if tracking_info:
            payload["ad_configuration"] = {"tracking_info": tracking_info}
        return payload

    creative = compact_mapping(
        {
            "identity_id": getattr(args, "identity_id", None),
            "identity_type": getattr(args, "identity_type", None),
            "identity_authorized_bc_id": getattr(args, "identity_authorized_bc_id", None),
            "video_id": getattr(args, "video_id", None),
            "image_ids": image_ids,
            "tracking_app_id": getattr(args, "tracking_app_id", None),
            "tracking_pixel_id": getattr(args, "tracking_pixel_id", None),
            "tracking_offline_event_set_ids": tracking_offline_event_set_ids,
        }
    )
    if landing_page_urls:
        if len(landing_page_urls) == 1:
            creative["landing_page_url"] = landing_page_urls[0]
        else:
            creative["landing_page_urls"] = landing_page_urls
    if not creative:
        raise CliError("Missing payload: provide --payload-json/--payload-file or direct creative reference flags")
    return {"creatives": [creative]}


def normalize_validate_creatives(payload: dict[str, Any], *, smart_plus: bool) -> list[dict[str, Any]]:
    if smart_plus:
        return [payload] if payload else []
    creatives = payload.get("creatives")
    if isinstance(creatives, list):
        return [item for item in creatives if isinstance(item, dict)]
    if payload:
        return [payload]
    return []


def extract_identity_refs(creative: dict[str, Any], *, smart_plus: bool) -> dict[str, str | None]:
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


def extract_video_ids(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    if smart_plus:
        video_ids: list[str] = []
        for item in creative.get("creative_list") or []:
            creative_info = (item or {}).get("creative_info") or {}
            video_info = creative_info.get("video_info") or {}
            video_id = video_info.get("video_id")
            if isinstance(video_id, str) and video_id.strip():
                video_ids.append(video_id.strip())
        return dedupe_strings(video_ids)
    return non_empty_list([creative.get("video_id")])


def extract_image_ids(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    if smart_plus:
        image_ids: list[str] = []
        for item in creative.get("creative_list") or []:
            creative_info = (item or {}).get("creative_info") or {}
            for image_info in creative_info.get("image_info") or []:
                if isinstance(image_info, dict):
                    image_id = image_info.get("image_id")
                    if isinstance(image_id, str) and image_id.strip():
                        image_ids.append(image_id.strip())
        return dedupe_strings(image_ids)
    return non_empty_list(creative.get("image_ids"))


def extract_image_web_uris(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
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
    return dedupe_strings(image_web_uris)


def extract_landing_page_urls(creative: dict[str, Any], *, smart_plus: bool) -> list[str]:
    values: list[str] = []
    if smart_plus:
        for item in creative.get("landing_page_url_list") or []:
            if isinstance(item, dict):
                values.extend(collect_strings(item.get("landing_page_url")))
    else:
        values.extend(collect_strings(creative.get("landing_page_url")))
        values.extend(collect_strings(creative.get("landing_page_urls")))
    return dedupe_strings([value for value in values if looks_like_url(value)])


def extract_tracking_refs(creative: dict[str, Any], *, smart_plus: bool) -> dict[str, Any]:
    if smart_plus:
        tracking_info = ((creative.get("ad_configuration") or {}).get("tracking_info") or {})
        return {
            "tracking_app_id": tracking_info.get("tracking_app_id"),
            "tracking_pixel_id": tracking_info.get("tracking_pixel_id"),
            "tracking_offline_event_set_ids": non_empty_list(tracking_info.get("tracking_offline_event_set_ids")),
        }
    return {
        "tracking_app_id": creative.get("tracking_app_id"),
        "tracking_pixel_id": creative.get("tracking_pixel_id"),
        "tracking_offline_event_set_ids": non_empty_list(creative.get("tracking_offline_event_set_ids")),
    }


def derive_bc_ids_from_assets(identities: list[dict[str, Any]], stores: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for identity in identities:
        value = identity.get("identity_authorized_bc_id")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    for store in stores:
        value = store.get("store_authorized_bc_id")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return dedupe_strings(values)


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
    return compact_mapping(
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


def summarize_tiktok_pixel_event(event: dict[str, Any]) -> dict[str, Any]:
    rules = event.get("rules") or []
    return compact_mapping(
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
                compact_mapping(
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


def summarize_tiktok_pixel(pixel: dict[str, Any]) -> dict[str, Any]:
    ownership = pixel.get("asset_ownership") or {}
    events = pixel.get("events") or []
    return compact_mapping(
        {
            "pixel_id": pixel.get("pixel_id"),
            "pixel_name": pixel.get("pixel_name"),
            "pixel_code": pixel.get("pixel_code"),
            "activity_status": pixel.get("activity_status"),
            "pixel_setup_mode": pixel.get("pixel_setup_mode"),
            "partner_name": pixel.get("partner_name"),
            "pixel_category": pixel.get("pixel_category"),
            "event_count": len(events),
            "events": [summarize_tiktok_pixel_event(e) for e in events],
            "enable_first_party_cookies": pixel.get("enable_first_party_cookies"),
            "enable_expanded_data_sharing": pixel.get("enable_expanded_data_sharing"),
            "owner_bc_id": ownership.get("owner_bc_id"),
            "ownership_status": ownership.get("ownership_status"),
            "asset_relation_status": ownership.get("asset_relation_status"),
        }
    )


def summarize_tiktok_offline_event_set(event_set: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
        {
            "event_set_id": event_set.get("event_set_id"),
            "name": event_set.get("name"),
            "advertiser_id": event_set.get("advertiser_id"),
            "auto_tracking": event_set.get("auto_tracking"),
            "create_time": event_set.get("create_time"),
            "update_time": event_set.get("update_time"),
        }
    )


def summarize_tiktok_app(app: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
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


def summarize_tiktok_store(store: dict[str, Any]) -> dict[str, Any]:
    return compact_mapping(
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


def summarize_tiktok_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    catalog_conf = catalog.get("catalog_conf") or {}
    bc_info = catalog.get("bc_info") or {}
    return compact_mapping(
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


def load_tiktok_campaign_copy_source(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    skip_adgroups: bool,
    skip_ads: bool,
    verbose: bool,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    campaign_result = client.list_campaigns(
        advertiser_id,
        filtering={"campaign_ids": [campaign_id]},
        page_size=1,
        smart_plus=smart_plus,
    )
    campaign_list = campaign_result.get("data", {}).get("list", [])
    if not campaign_list:
        raise CliError(f"Source campaign {campaign_id} not found")
    source_campaign_data = campaign_list[0]
    effective_smart_plus = smart_plus or is_smart_plus_campaign_type(source_campaign_data)
    if effective_smart_plus:
        source_campaign_data = merge_source_snapshot(
            source_campaign_data,
            client.get_campaign(advertiser_id, campaign_id, smart_plus=True),
        )
    if effective_smart_plus and not smart_plus and verbose:
        print(
            "Detected SmartPlus source campaign from campaign_automation_type; evaluating SmartPlus vs normal copy target.",
            file=sys.stderr,
        )
    source_adgroups: list[dict[str, Any]] = []
    source_ads_by_adgroup: dict[str, list[dict[str, Any]]] = {}
    if not skip_adgroups:
        source_adgroups, source_ads_by_adgroup = load_source_adgroups_and_ads_for_copy(
            client,
            advertiser_id=advertiser_id,
            campaign_id=campaign_id,
            smart_plus=effective_smart_plus,
            include_ads=not skip_ads,
        )
    copy_strategy = infer_campaign_copy_strategy(
        source_campaign_data,
        source_adgroups,
        [ad for ads in source_ads_by_adgroup.values() for ad in ads],
        smart_plus=effective_smart_plus,
    )
    if copy_strategy.get("target_smart_plus") and source_campaign_data.get("objective_type") == "APP_PROMOTION":
        validate_smartplus_app_eligibility(
            client,
            advertiser_id=advertiser_id,
            payload=source_campaign_data,
            context_label=f"smartplus-campaigns copy source campaign {campaign_id}",
        )
    if verbose:
        print(f"Copy strategy: {copy_strategy}", file=sys.stderr)
    return source_campaign_data, effective_smart_plus, source_adgroups, source_ads_by_adgroup, copy_strategy


def execute_tiktok_campaign_copy(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    name: str,
    copies: int,
    operation_status: str,
    adgroup_status: str,
    ad_status: str,
    landing_page_url: str | None,
    page_id: str | None,
    skip_adgroups: bool,
    skip_ads: bool,
    smart_plus: bool,
    copy_route: str = "auto",
    verbose: bool,
) -> dict[str, Any]:
    """Copy a campaign with all its adgroups and ads."""
    (
        source_campaign_data,
        effective_smart_plus,
        source_adgroups,
        source_ads_by_adgroup,
        copy_strategy,
    ) = load_tiktok_campaign_copy_source(
        client,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        smart_plus=smart_plus,
        skip_adgroups=skip_adgroups,
        skip_ads=skip_ads,
        verbose=verbose,
    )
    if copy_route == "smartplus":
        copy_strategy["target_smart_plus"] = True
    elif copy_route == "normal":
        copy_strategy["target_smart_plus"] = False
    target_smart_plus = bool(copy_strategy.get("target_smart_plus", effective_smart_plus))

    # Create the campaign(s)
    created_campaigns = []
    failed_campaigns = []

    for i in range(copies):
        campaign_name = f"{name}_{i + 1}" if copies > 1 else name
        campaign_payload = build_campaign_copy_payload(
            source_campaign_data,
            advertiser_id=advertiser_id,
            campaign_name=campaign_name,
            operation_status=operation_status,
            page_id=page_id,
            copy_strategy=copy_strategy,
        )
        campaign_name = campaign_payload["campaign_name"]
        if verbose:
            print(f"Creating campaign {i+1}/{copies}: {campaign_name}...", file=sys.stderr)

        try:
            campaign_result = client.create_campaign(campaign_payload, smart_plus=target_smart_plus)
            campaign_data = campaign_result.get("data", {})
            new_campaign_id = campaign_data.get("campaign_id") or campaign_data.get("smart_plus_campaign_id")

            if not new_campaign_id:
                error_msg = campaign_result.get("message", "Unknown error")
                failed_campaigns.append({"name": campaign_name, "error": error_msg})
                if verbose:
                    print(f"  Failed: {error_msg}", file=sys.stderr)
                raise CliError(f"Failed to create campaign: {error_msg}")

            if verbose:
                print(f"  Created campaign ID: {new_campaign_id}", file=sys.stderr)

            campaign_info = {
                "campaign_id": new_campaign_id,
                "campaign_name": campaign_name,
                "copy_strategy": copy_strategy,
                "adgroups_created": 0,
                "ads_created": 0,
                "adgroups_failed": 0,
                "ads_failed": 0,
            }

            if not skip_adgroups:
                if verbose:
                    print(f"  Found {len(source_adgroups)} adgroup(s) to copy", file=sys.stderr)

                for adgroup_idx, source_adgroup in enumerate(source_adgroups, 1):
                    try:
                        source_adgroup_id = validate_non_empty(source_adgroup.get("adgroup_id"), "adgroup_id")
                        adgroup_name = source_adgroup.get("adgroup_name", f"Adgroup {adgroup_idx}")
                        adgroup_strategy = infer_adgroup_copy_strategy(
                            source_adgroup,
                            campaign_strategy=copy_strategy,
                        )
                        if verbose:
                            print(f"  Creating adgroup {adgroup_idx}/{len(source_adgroups)}: {adgroup_name}...", file=sys.stderr)
                            print(f"    Adgroup strategy: {adgroup_strategy}", file=sys.stderr)

                        adgroup_payload = build_adgroup_copy_payload(
                            source_adgroup,
                            advertiser_id=advertiser_id,
                            campaign_id=new_campaign_id,
                            operation_status=adgroup_status,
                            adgroup_strategy=adgroup_strategy,
                        )

                        adgroup_result = client.create_adgroup(adgroup_payload, smart_plus=target_smart_plus)
                        adgroup_data = adgroup_result.get("data", {})
                        new_adgroup_id = adgroup_data.get("adgroup_id") or adgroup_data.get("smart_plus_adgroup_id")

                        if not new_adgroup_id:
                            error_msg = adgroup_result.get("message", "Unknown error")
                            campaign_info["adgroups_failed"] += 1
                            if verbose:
                                print(f"    Failed: {error_msg}", file=sys.stderr)
                            continue

                        if verbose:
                            print(f"    Created adgroup ID: {new_adgroup_id}", file=sys.stderr)

                        campaign_info["adgroups_created"] += 1

                        if new_adgroup_id and not skip_ads:
                            source_ads = source_ads_by_adgroup.get(source_adgroup_id, [])

                            if verbose:
                                print(f"    Creating {len(source_ads)} ad(s)...", file=sys.stderr)

                            for source_ad in source_ads:
                                try:
                                    ad_strategy = infer_ad_copy_strategy(
                                        source_ad,
                                        adgroup_strategy=adgroup_strategy,
                                    )
                                    if verbose:
                                        ad_name = source_ad.get("ad_name") or source_ad.get("smart_plus_ad_id") or source_ad.get("ad_id")
                                        print(f"      Ad strategy for {ad_name}: {ad_strategy}", file=sys.stderr)
                                    if target_smart_plus and ad_strategy.get("use_smart_plus_payload"):
                                        ad_payload = build_smart_plus_ad_copy_payload(
                                            source_ad,
                                            advertiser_id=advertiser_id,
                                            adgroup_id=new_adgroup_id,
                                            operation_status=ad_status,
                                            landing_page_url=landing_page_url,
                                            promotion_type=source_adgroup.get("promotion_type"),
                                            ad_strategy=ad_strategy,
                                        )
                                    else:
                                        ad_payload = build_normal_ad_copy_payload(
                                            source_ad,
                                            advertiser_id=advertiser_id,
                                            adgroup_id=new_adgroup_id,
                                            operation_status=ad_status,
                                            landing_page_url=landing_page_url,
                                            promotion_type=source_adgroup.get("promotion_type"),
                                            client=client,
                                            source_smart_plus=effective_smart_plus,
                                            ad_strategy=ad_strategy,
                                        )

                                    ad_result = client.create_ad(ad_payload, smart_plus=target_smart_plus)
                                    ad_data = ad_result.get("data", {})
                                    ad_id = ad_data.get("smart_plus_ad_id") or ad_data.get("ad_id")
                                    if not ad_id:
                                        ad_ids = ad_data.get("ad_ids")
                                        if isinstance(ad_ids, list) and ad_ids:
                                            ad_id = ad_ids[0]

                                    if ad_id:
                                        campaign_info["ads_created"] += 1
                                    else:
                                        campaign_info["ads_failed"] += 1
                                        if verbose:
                                            error_msg = ad_result.get("message", "Unknown error")
                                            print(f"      Ad failed: {error_msg}", file=sys.stderr)
                                except CliError as exc:
                                    campaign_info["ads_failed"] += 1
                                    if verbose:
                                        print(f"      Ad failed: {exc}", file=sys.stderr)

                    except CliError as exc:
                        campaign_info["adgroups_failed"] += 1
                        if verbose:
                            print(f"    Adgroup failed: {exc}", file=sys.stderr)

            created_campaigns.append(campaign_info)
            if verbose:
                print(f"  Campaign complete: {campaign_info['adgroups_created']} adgroups, {campaign_info['ads_created']} ads created", file=sys.stderr)

        except CliError as exc:
            failed_campaigns.append({"name": campaign_name, "error": str(exc)})
            if verbose:
                print(f"  Campaign failed: {exc}", file=sys.stderr)

    result = {
        "source_campaign_id": campaign_id,
        "copies_requested": copies,
        "copies_created": len(created_campaigns),
        "copies_failed": len(failed_campaigns),
        "copy_route": copy_route,
        "campaigns": created_campaigns,
    }
    if failed_campaigns:
        result["failed"] = failed_campaigns
    if verbose and created_campaigns:
        total_adgroups = sum(c["adgroups_created"] for c in created_campaigns)
        total_ads = sum(c["ads_created"] for c in created_campaigns)
        print(f"Summary: {total_adgroups} adgroups, {total_ads} ads created across {len(created_campaigns)} campaign(s)", file=sys.stderr)
    return result


def command_tiktok_campaigns_copy(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    result = execute_tiktok_campaign_copy(
        client,
        advertiser_id=advertiser_id,
        campaign_id=args.campaign_id,
        name=args.name,
        copies=args.copies or 1,
        operation_status=args.operation_status,
        adgroup_status=args.adgroup_status,
        ad_status=args.ad_status,
        landing_page_url=args.landing_page_url,
        page_id=getattr(args, "page_id", None),
        skip_adgroups=args.skip_adgroups,
        skip_ads=args.skip_ads,
        smart_plus=args.smart_plus,
        copy_route=getattr(args, "copy_route", "auto"),
        verbose=args.verbose,
    )
    print_output(result, as_json=args.json)


def command_tiktok_smartplus_campaigns_bootstrap_app(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    selected_app = None
    template_match: dict[str, Any]
    requested = compact_mapping(
        {
            "name": args.name,
            "app_id": args.app_id,
            "app_name": args.app_name,
            "app_promotion_type": args.app_promotion_type,
            "template_campaign_id": args.template_campaign_id,
            "copies": args.copies,
        }
    )

    if args.template_campaign_id:
        (
            source_campaign_data,
            effective_smart_plus,
            source_adgroups,
            source_ads_by_adgroup,
            copy_strategy,
        ) = load_tiktok_campaign_copy_source(
            client,
            advertiser_id=advertiser_id,
            campaign_id=args.template_campaign_id,
            smart_plus=True,
            skip_adgroups=False,
            skip_ads=False,
            verbose=args.verbose,
        )
        if not effective_smart_plus:
            raise CliError(f"Template campaign {args.template_campaign_id} is not a SmartPlus campaign")
        if source_campaign_data.get("objective_type") != "APP_PROMOTION":
            raise CliError(f"Template campaign {args.template_campaign_id} is not an APP_PROMOTION SmartPlus campaign")
        source_adgroup = first_dict(source_adgroups) or {}
        source_ad = first_dict(source_ads_by_adgroup.get(str(source_adgroup.get('adgroup_id') or ''), []))
        if source_adgroup.get("app_id"):
            try:
                selected_app = client.get_app_info(advertiser_id, str(source_adgroup.get("app_id")))
            except CliError:
                selected_app = compact_mapping({"app_id": source_adgroup.get("app_id")})
        template_match = {
            "selection_mode": "template_campaign_id",
            "requested": compact_mapping({"template_campaign_id": args.template_campaign_id}),
            "app": summarize_tiktok_app(selected_app) if isinstance(selected_app, dict) and selected_app.get("app_id") else selected_app,
            "candidate_count": 1,
            "template_campaign": summarize_tiktok_template_campaign(source_campaign_data),
            "template_adgroup": summarize_tiktok_template_adgroup(source_adgroup),
            "template_ad": summarize_tiktok_template_ad(source_ad) if source_ad else None,
            "copy_strategy": copy_strategy,
        }
        template_campaign_id = validate_non_empty(source_campaign_data.get("campaign_id"), "campaign_id")
    else:
        template_match = find_tiktok_smartplus_app_template(
            client,
            advertiser_id=advertiser_id,
            app_id=args.app_id,
            app_name=args.app_name,
            app_promotion_type=args.app_promotion_type,
        )
        template_match["selection_mode"] = "auto_match"
        template_campaign_id = ((template_match.get("template_campaign") or {}).get("campaign_id"))
        if not template_campaign_id:
            raise CliError(
                f"No SmartPlus APP_PROMOTION template campaign found for {requested or {'scope': 'current advertiser'}}"
            )

    result = {
        "ok": True,
        "advertiser_id": advertiser_id,
        "mode": "dry_run" if args.dry_run else "create",
        "requested": requested,
        "matched": template_match,
    }
    if args.dry_run:
        print_output(result, as_json=args.json)
        return

    copy_result = execute_tiktok_campaign_copy(
        client,
        advertiser_id=advertiser_id,
        campaign_id=str(template_campaign_id),
        name=args.name,
        copies=args.copies or 1,
        operation_status=args.operation_status,
        adgroup_status=args.adgroup_status,
        ad_status=args.ad_status,
        landing_page_url=None,
        page_id=getattr(args, "page_id", None),
        skip_adgroups=False,
        skip_ads=False,
        smart_plus=True,
        verbose=args.verbose,
    )
    result["copy_result"] = copy_result
    print_output(result, as_json=args.json)


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
    filtering_json = getattr(args, "filtering_json", None)
    if not filtering_json:
        return None
    return parse_json_arg(filtering_json, "filtering", list)


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
    payload = load_payload(args, label="filtering")
    return payload or None


def build_gmv_max_report_filtering(args: argparse.Namespace, dimensions: list[str] | None = None) -> dict[str, Any] | None:
    payload = load_payload(args, label="filtering")
    assign_if_present(payload, "campaign_name", getattr(args, "campaign_name", None))
    for arg_name, field_name in (
        ("gmv_max_promotion_types", "gmv_max_promotion_types"),
        ("campaign_ids", "campaign_ids"),
        ("campaign_statuses", "campaign_statuses"),
        ("creative_delivery_statuses", "creative_delivery_statuses"),
        ("creative_types", "creative_types"),
        ("item_group_ids", "item_group_ids"),
        ("item_ids", "item_ids"),
        ("room_ids", "room_ids"),
    ):
        values = non_empty_list(getattr(args, arg_name, None))
        if values:
            payload[field_name] = values
    if dimensions and (
        payload.get("campaign_ids") or payload.get("item_group_ids") or payload.get("item_ids")
    ):
        payload.pop("gmv_max_promotion_types", None)
    assign_if_present(payload, "search_word", getattr(args, "search_word", None))
    return payload or None


def resolve_gmv_max_report_dimensions(args: argparse.Namespace) -> list[str]:
    dimensions = non_empty_list(getattr(args, "dimensions", None))
    if dimensions:
        return dimensions
    level = getattr(args, "level", None)
    if not level:
        raise CliError("GMV Max reports require --dimension or --level")
    resolved = list(TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS[level])
    for dimension in TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS[getattr(args, "time_grain", "none")]:
        if dimension not in resolved:
            resolved.append(dimension)
    return resolved


def default_gmv_max_report_metrics(args: argparse.Namespace, dimensions: list[str]) -> list[str]:
    level = getattr(args, "level", None)
    if level:
        return list(TIKTOK_GMV_MAX_REPORT_LEVEL_METRICS[level])
    if "item_id" in dimensions:
        return list(TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS)
    if "item_group_id" in dimensions:
        return list(TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS)
    if "duration" in dimensions:
        return list(TIKTOK_GMV_MAX_DURATION_REPORT_METRICS)
    if "campaign_id" in dimensions:
        return list(TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS)
    return list(TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS)


def classify_gmv_max_item_scope(item_id: Any) -> str:
    return (
        TIKTOK_GMV_MAX_ITEM_SCOPE_PRODUCT_CARD
        if str(item_id) == "-1"
        else TIKTOK_GMV_MAX_ITEM_SCOPE_SPECIFIC_ITEM
    )


def annotate_gmv_max_item_scope(response: dict[str, Any]) -> dict[str, Any]:
    rows = response.get("data", {}).get("list")
    if not isinstance(rows, list):
        return response
    for row in rows:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensions")
        if not isinstance(dimensions, dict) or "item_id" not in dimensions:
            continue
        dimensions.setdefault("item_scope", classify_gmv_max_item_scope(dimensions.get("item_id")))
    return response


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
    payload = load_payload(args, label="GMV Max custom anchor video payload")
    payload["advertiser_id"] = advertiser_id
    assign_if_present(payload, "store_id", getattr(args, "store_id", None))
    assign_if_present(payload, "store_authorized_bc_id", getattr(args, "store_authorized_bc_id", None))
    payload.setdefault("creative_source", getattr(args, "creative_source", None) or "CUSTOMIZED")
    assign_if_present(payload, "keyword", getattr(args, "keyword", None))
    assign_if_present(payload, "campaign_id", getattr(args, "campaign_id", None))
    assign_if_present(payload, "need_auth_code_video", getattr(args, "need_auth_code_video", None))
    assign_if_present(payload, "sort_field", getattr(args, "sort_field", None))
    assign_if_present(payload, "sort_type", getattr(args, "sort_type", None))
    spu_ids = non_empty_list(getattr(args, "spu_ids", None))
    if spu_ids:
        payload["spu_id_list"] = spu_ids
    identity_list = parse_json_arg(getattr(args, "identity_list_json", None), "identity_list", list)
    if identity_list:
        payload["identity_list"] = identity_list
    page = int(getattr(args, "page", 1) or 1)
    page_size = int(getattr(args, "page_size", 20) or 20)
    if page < 1:
        raise CliError("page must be >= 1")
    if not 1 <= page_size <= 50:
        raise CliError("page_size must be between 1 and 50 for GMV Max custom anchor videos")
    payload["page"] = page
    payload["page_size"] = page_size
    validate_non_empty(payload.get("store_id"), "store_id")
    validate_non_empty(payload.get("store_authorized_bc_id"), "store_authorized_bc_id")
    validate_non_empty(payload.get("creative_source"), "creative_source")
    return payload


def command_tiktok_gmv_max_custom_anchor_videos_list(args: argparse.Namespace) -> None:
    advertiser_id, client = resolve_tiktok_client(args)
    response = client.list_gmv_max_custom_anchor_videos(
        build_gmv_max_custom_anchor_video_payload(args, advertiser_id)
    )
    print_output(response, as_json=args.json)


def build_gmv_max_video_list_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_payload(args, label="GMV Max video list parameters")
    assign_if_present(payload, "store_id", getattr(args, "store_id", None))
    assign_if_present(payload, "store_authorized_bc_id", getattr(args, "store_authorized_bc_id", None))
    spu_ids = non_empty_list(getattr(args, "spu_ids", None))
    if spu_ids:
        payload["spu_id_list"] = spu_ids
    assign_if_present(payload, "custom_posts_eligible", getattr(args, "custom_posts_eligible", None))
    assign_if_present(payload, "sort_field", getattr(args, "sort_field", None))
    assign_if_present(payload, "sort_type", getattr(args, "sort_type", None))
    assign_if_present(payload, "keyword", getattr(args, "keyword", None))
    need_auth_code_video = parse_bool_flags(
        getattr(args, "need_auth_code_video", None),
        False if getattr(args, "no_need_auth_code_video", False) else None,
    )
    assign_if_present(payload, "need_auth_code_video", need_auth_code_video)
    identity_list = parse_json_arg(getattr(args, "identity_list_json", None), "identity_list", list)
    if identity_list:
        payload["identity_list"] = identity_list
    page = int(getattr(args, "page", 1) or 1)
    page_size = int(getattr(args, "page_size", 10) or 10)
    if page < 1:
        raise CliError("page must be >= 1")
    if not 1 <= page_size <= 50:
        raise CliError("page_size must be between 1 and 50 for GMV Max videos")
    payload["page"] = page
    payload["page_size"] = page_size
    validate_non_empty(payload.get("store_id"), "store_id")
    validate_non_empty(payload.get("store_authorized_bc_id"), "store_authorized_bc_id")
    return payload


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
    payload = load_payload(args, label="store product filtering")
    item_group_ids = validate_max_list_size(non_empty_list(getattr(args, "item_group_ids", None)), "item_group_ids", max_size=10)
    if item_group_ids:
        payload["item_group_ids"] = item_group_ids
    assign_if_present(payload, "product_name", getattr(args, "product_name", None))
    assign_if_present(payload, "ad_creation_eligible", getattr(args, "ad_creation_eligible", None))
    return payload or None


def normalize_store_product_row(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "store_id": product.get("store_id"),
        "item_group_id": product.get("item_group_id"),
        "title": product.get("title"),
        "product_image_url": product.get("product_image_url"),
        "min_price": product.get("min_price"),
        "max_price": product.get("max_price"),
        "currency": product.get("currency"),
        "historical_sales": product.get("historical_sales"),
        "category": product.get("category"),
        "quantity": product.get("quantity"),
        "status": product.get("status"),
        "gmv_max_ads_status": product.get("gmv_max_ads_status"),
        "is_running_custom_shop_ads": product.get("is_running_custom_shop_ads"),
        "raw": product,
    }


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


def command_tiktok_landing_pages_analyze(args: argparse.Namespace) -> None:
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
    portfolios = collect_paginated_entities(
        lambda page, current_page_size: client.list_creative_portfolios(
            advertiser_id,
            filtering=build_creative_portfolio_filtering(
                argparse.Namespace(
                    creative_portfolio_ids=creative_portfolio_ids,
                    creative_portfolio_types=creative_portfolio_types,
                    payload_json=None,
                    payload_file=None,
                )
            ),
            page=page,
            page_size=current_page_size,
        ),
        "creative_portfolios",
        page_size=page_size,
        max_pages=max_pages,
    )
    selection = select_tiktok_creative_portfolios(
        portfolios,
        creative_portfolio_ids=creative_portfolio_ids,
        creative_portfolio_types=creative_portfolio_types,
        title=title,
        query=query,
        limit=limit,
    )
    inspected: list[dict[str, Any]] = []
    for selected in selection.get("selected") or []:
        portfolio_id = selected.get("creative_portfolio_id")
        if not portfolio_id:
            continue
        try:
            detail_response = client.get_creative_portfolio(advertiser_id, str(portfolio_id))
            detail_data = detail_response.get("data") if isinstance(detail_response, dict) else detail_response
            detail_data = detail_data if isinstance(detail_data, dict) else {}
            inspected.append(
                compact_mapping(
                    {
                        "summary": selected,
                        "detail_summary": summarize_tiktok_creative_portfolio(detail_data),
                        "detail": detail_data if include_raw else None,
                    }
                )
            )
        except CliError as exc:
            inspected.append(
                {
                    "summary": selected,
                    "error": str(exc),
                }
            )
    return {
        "advertiser_id": advertiser_id,
        "requested": selection.get("requested"),
        "candidate_count": selection.get("candidate_count"),
        "selected_count": selection.get("selected_count"),
        "best_match": selection.get("best_match"),
        "inspected": inspected,
    }


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
    campaign = merge_source_snapshot({}, client.get_campaign(advertiser_id, campaign_id, smart_plus=smart_plus))
    adgroups = collect_paginated_entities(
        lambda page, current_page_size: client.list_adgroups(
            advertiser_id,
            filtering={"campaign_ids": [campaign_id]},
            page=page,
            page_size=current_page_size,
            smart_plus=smart_plus,
        ),
        "list",
        page_size=page_size,
        max_pages=max_pages,
    )
    adgroup_summaries: list[dict[str, Any]] = []
    ads: list[dict[str, Any]] = []
    identity_ids: list[str] = []
    image_ids: list[str] = []
    video_ids: list[str] = []
    landing_page_urls: list[str] = []
    text_tokens: list[str] = []
    app_ids: list[str] = []

    for adgroup in adgroups:
        adgroup_id = str(adgroup.get("adgroup_id") or "").strip()
        if not adgroup_id:
            continue
        adgroup_summaries.append(summarize_tiktok_template_adgroup(adgroup))
        adgroup_app_id = adgroup.get("app_id")
        if isinstance(adgroup_app_id, str) and adgroup_app_id.strip():
            app_ids.append(adgroup_app_id.strip())
        ad_list = collect_paginated_entities(
            lambda page, current_page_size: client.list_ads(
                advertiser_id,
                filtering={"adgroup_ids": [adgroup_id]},
                page=page,
                page_size=current_page_size,
                smart_plus=smart_plus,
            ),
            "list",
            page_size=page_size,
            max_pages=max_pages,
        )
        for ad_summary in ad_list:
            source_ad_id = extract_source_ad_id(ad_summary, smart_plus=smart_plus)
            try:
                ad_detail = merge_source_snapshot(
                    ad_summary,
                    client.get_ad(advertiser_id, source_ad_id, smart_plus=smart_plus),
                )
            except CliError:
                ad_detail = ad_summary
            ads.append(ad_detail)
            identity_refs = extract_identity_refs(ad_detail, smart_plus=smart_plus)
            if identity_refs.get("identity_id"):
                identity_ids.append(str(identity_refs["identity_id"]))
            video_ids.extend(extract_video_ids(ad_detail, smart_plus=smart_plus))
            image_ids.extend(extract_image_ids(ad_detail, smart_plus=smart_plus))
            landing_page_urls.extend(extract_landing_page_urls(ad_detail, smart_plus=smart_plus))
            text_tokens.extend(
                collect_strings(
                    [
                        ad_detail.get("ad_name"),
                        ad_detail.get("ad_text"),
                        ad_detail.get("ad_text_list"),
                        ad_detail.get("creative_list"),
                    ]
                )
            )

    hints = {
        "campaign": summarize_tiktok_template_campaign(campaign),
        "adgroups": adgroup_summaries,
        "ads": [summarize_tiktok_template_ad(ad) for ad in ads],
        "identity_ids": dedupe_strings([value for value in identity_ids if value]),
        "video_ids": dedupe_strings([value for value in video_ids if value]),
        "image_ids": dedupe_strings([value for value in image_ids if value]),
        "landing_page_urls": dedupe_strings([value for value in landing_page_urls if value]),
        "text_tokens": dedupe_strings([normalize_text(value) for value in text_tokens if normalize_text(value)]),
        "app_ids": dedupe_strings([value for value in app_ids if value]),
    }
    return hints


def score_tiktok_creative_portfolio_against_hints(
    portfolio: dict[str, Any],
    hints: dict[str, Any],
) -> dict[str, Any]:
    contents = extract_tiktok_creative_portfolio_contents(portfolio)
    summary = summarize_tiktok_creative_portfolio(portfolio)
    searchable_text = normalize_text(" ".join(collect_strings([summary, contents])))
    content_identity_ids = [
        str(content.get("identity_id")).strip()
        for content in contents
        if isinstance(content.get("identity_id"), str) and str(content.get("identity_id")).strip()
    ]
    content_app_ids = [
        str(content.get("app_id")).strip()
        for content in contents
        if isinstance(content.get("app_id"), str) and str(content.get("app_id")).strip()
    ]
    content_video_ids = [
        str((content.get("advanced_audio_info") or {}).get("video_id")).strip()
        for content in contents
        if isinstance((content.get("advanced_audio_info") or {}).get("video_id"), str)
        and str((content.get("advanced_audio_info") or {}).get("video_id")).strip()
    ]
    content_image_ids = [
        str(content.get("image_id")).strip()
        for content in contents
        if isinstance(content.get("image_id"), str) and str(content.get("image_id")).strip()
    ]
    reasons: list[str] = []
    score = 0

    campaign = hints.get("campaign") or {}
    campaign_name = normalize_text(campaign.get("campaign_name"))
    if campaign_name and campaign_name in searchable_text:
        score += 8
        reasons.append("campaign_name")

    app_ids = {str(value).strip() for value in hints.get("app_ids") or [] if str(value).strip()}
    if app_ids and any(value in app_ids for value in content_app_ids):
        score += 20
        reasons.append("app_id")

    identity_ids = {str(value).strip() for value in hints.get("identity_ids") or [] if str(value).strip()}
    if identity_ids and any(value in identity_ids for value in content_identity_ids):
        score += 18
        reasons.append("identity_id")

    video_ids = {str(value).strip() for value in hints.get("video_ids") or [] if str(value).strip()}
    if video_ids and any(value in video_ids for value in content_video_ids):
        score += 15
        reasons.append("video_id")

    image_ids = {str(value).strip() for value in hints.get("image_ids") or [] if str(value).strip()}
    if image_ids and any(value in image_ids for value in content_image_ids):
        score += 15
        reasons.append("image_id")

    for token in (normalize_text(value) for value in hints.get("text_tokens") or []):
        if token and token in searchable_text:
            score += 2
            reasons.append("text")
            break

    if not reasons:
        score += 1

    return {
        "score": score,
        "reasons": dedupe_strings(reasons),
        "summary": summary,
        "raw": portfolio,
    }


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
    hints = collect_tiktok_campaign_creative_hints(
        client,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        smart_plus=smart_plus,
        page_size=page_size,
        max_pages=max_pages,
    )
    portfolios = collect_paginated_entities(
        lambda page, current_page_size: client.list_creative_portfolios(
            advertiser_id,
            filtering=None,
            page=page,
            page_size=current_page_size,
        ),
        "creative_portfolios",
        page_size=page_size,
        max_pages=max_pages,
    )
    matches = [
        score_tiktok_creative_portfolio_against_hints(portfolio, hints)
        for portfolio in portfolios
        if isinstance(portfolio, dict)
    ]
    matches.sort(
        key=lambda item: (
            item["score"],
            item["summary"].get("modify_time") or "",
            item["summary"].get("create_time") or "",
        ),
        reverse=True,
    )
    selected = matches[: max(limit, 0)] if limit else matches
    return {
        "advertiser_id": advertiser_id,
        "campaign_id": campaign_id,
        "smart_plus": smart_plus,
        "candidate_count": len(matches),
        "selected_count": len(selected),
        "hints": hints,
        "best_match": selected[0]["summary"] if selected else None,
        "selected": [
            compact_mapping(
                {
                    "score": item["score"],
                    "reasons": item["reasons"],
                    "summary": item["summary"],
                    "raw": item["raw"],
                }
            )
            for item in selected
        ],
    }


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
    parser.add_argument("--name")
    parser.add_argument("--objective-type")
    parser.add_argument("--budget", type=float)
    parser.add_argument("--budget-mode")
    parser.add_argument("--operation-status")
    parser.add_argument("--campaign-type")
    parser.add_argument("--sales-destination")
    parser.add_argument("--optimization-goal")
    parser.add_argument("--special-industries", nargs="*")


def add_tiktok_campaign_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    campaigns = parent_subparsers.add_parser(name, help=help_text)
    campaigns_sub = campaigns.add_subparsers(dest=f"{name.replace('-', '_')}_command", required=True)

    p = campaigns_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--campaign-id", dest="campaign_ids", action="append")
    p.add_argument("--name")
    p.add_argument("--objective-type")
    p.add_argument("--primary-status")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--fields", nargs="*")
    p.add_argument("--exclude-field-types", nargs="*")
    p.set_defaults(func=command_tiktok_campaigns_list, smart_plus=smart_plus)

    p = campaigns_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("campaign_id")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_campaigns_get, smart_plus=smart_plus)

    p = campaigns_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    add_campaign_flags(p)
    if smart_plus:
        p.add_argument("--page-id", help="App Profile Page ID to use when campaign_app_profile_page_state is ON")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_campaigns_create, smart_plus=smart_plus)

    p = campaigns_sub.add_parser("update")
    add_tiktok_auth_arguments(p)
    p.add_argument("campaign_id")
    p.add_argument("--name")
    p.add_argument("--budget", type=float)
    p.add_argument("--po-number")
    p.add_argument("--special-industries", nargs="*")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_campaigns_update, smart_plus=smart_plus)

    p = campaigns_sub.add_parser("status")
    add_tiktok_auth_arguments(p)
    p.add_argument("campaign_ids", nargs="+")
    p.add_argument("--operation-status", required=True)
    p.add_argument("--postback-window-mode")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_campaigns_status, smart_plus=smart_plus)

    p = campaigns_sub.add_parser("copy")
    add_tiktok_auth_arguments(p)
    p.add_argument("campaign_id", help="Source campaign ID to copy")
    p.add_argument("--name", required=True, help="Name for the new campaign(s)")
    p.add_argument("--copies", type=int, default=1, help="Number of copies to create (default: 1)")
    p.add_argument("--operation-status", choices=["ENABLE", "DISABLE"], default="DISABLE",
                   help="Operation status for new campaigns (default: DISABLE)")
    p.add_argument("--adgroup-status", choices=["ENABLE", "DISABLE"], default="DISABLE",
                   help="Operation status for new adgroups (default: DISABLE)")
    p.add_argument("--ad-status", choices=["ENABLE", "DISABLE"], default="ENABLE",
                   help="Operation status for new ads (default: ENABLE)")
    p.add_argument("--page-id", help="App Profile Page ID to use when campaign_app_profile_page_state is ON")
    p.add_argument("--landing-page-url", help="Override landing page URL for website ads during copy")
    p.add_argument(
        "--copy-route",
        choices=["auto", "normal", "smartplus"],
        default="auto",
        help="Target API route for the copy flow (default: auto)",
    )
    p.add_argument("--skip-adgroups", action="store_true", help="Skip copying adgroups")
    p.add_argument("--skip-ads", action="store_true", help="Skip copying ads")
    p.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
    p.set_defaults(func=command_tiktok_campaigns_copy, smart_plus=smart_plus)

    if smart_plus:
        p = campaigns_sub.add_parser("bootstrap-app")
        add_tiktok_auth_arguments(p)
        p.add_argument("--name", required=True, help="Name for the new SmartPlus app campaign")
        p.add_argument("--app-id", help="Target TikTok app ID to match")
        p.add_argument("--app-name", help="Target TikTok app name to match when app_id is unknown")
        p.add_argument("--app-promotion-type", default="APP_INSTALL", help="App promotion type filter (default: APP_INSTALL)")
        p.add_argument("--template-campaign-id", help="Explicit SmartPlus APP_PROMOTION campaign to use as the template")
        p.add_argument("--copies", type=int, default=1, help="Number of campaign copies to create (default: 1)")
        p.add_argument("--operation-status", choices=["ENABLE", "DISABLE"], default="DISABLE",
                       help="Operation status for new campaigns (default: DISABLE)")
        p.add_argument("--adgroup-status", choices=["ENABLE", "DISABLE"], default="DISABLE",
                       help="Operation status for new adgroups (default: DISABLE)")
        p.add_argument("--ad-status", choices=["ENABLE", "DISABLE"], default="ENABLE",
                       help="Operation status for new ads (default: ENABLE)")
        p.add_argument("--page-id", help="App Profile Page ID to use when campaign_app_profile_page_state is ON")
        p.add_argument("--dry-run", action="store_true", help="Only resolve the best-matched template and assets")
        p.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
        p.set_defaults(func=command_tiktok_smartplus_campaigns_bootstrap_app, smart_plus=True)


def add_tiktok_gmv_max_campaign_group(parent_subparsers) -> None:
    campaigns = parent_subparsers.add_parser(
        "gmv-max-campaigns",
        aliases=["gmvmax-campaigns"],
        help="TikTok GMV Max campaigns",
    )
    campaigns_sub = campaigns.add_subparsers(dest="gmv_max_campaigns_command", required=True)

    p = campaigns_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument(
        "--gmv-max-promotion-type",
        dest="gmv_max_promotion_types",
        action="append",
        choices=["PRODUCT_GMV_MAX", "LIVE_GMV_MAX"],
        help="GMV Max campaign type. Defaults to PRODUCT_GMV_MAX.",
    )
    p.add_argument("--store-id", dest="store_ids", action="append")
    p.add_argument("--campaign-id", dest="campaign_ids", action="append")
    p.add_argument("--name")
    p.add_argument("--primary-status")
    p.add_argument("--creation-filter-start-time")
    p.add_argument("--creation-filter-end-time")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_gmv_max_campaigns_list)

    p = campaigns_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("campaign_id")
    p.set_defaults(func=command_tiktok_gmv_max_campaigns_get)

    p = campaigns_sub.add_parser("item-previews")
    add_tiktok_auth_arguments(p)
    p.add_argument(
        "--campaign-id",
        dest="campaign_ids",
        action="append",
        required=True,
        help="GMV Max campaign ID to read via /campaign/gmv_max/info/. Repeatable.",
    )
    p.add_argument(
        "--item-id",
        dest="item_ids",
        action="append",
        help="Optional target item_id to diagnose inside the returned campaign item_list. Repeatable.",
    )
    p.add_argument(
        "--no-identity-video-info",
        action="store_true",
        help="Skip /identity/video/info enrichment for item_list rows.",
    )
    p.add_argument(
        "--identity-video-info",
        action="store_true",
        help="Force /identity/video/info enrichment even when no --item-id target is provided.",
    )
    p.add_argument("--out", help="Optional JSON output path for the item_id -> preview map.")
    p.set_defaults(func=command_tiktok_gmv_max_campaigns_item_previews)


def add_tiktok_gmv_max_custom_anchor_video_group(parent_subparsers) -> None:
    videos = parent_subparsers.add_parser(
        "gmv-max-custom-anchor-videos",
        aliases=["gmvmax-custom-anchor-videos", "gmv-max-custom-videos", "gmvmax-custom-videos"],
        help="TikTok GMV Max customized TikTok post previews",
    )
    videos_sub = videos.add_subparsers(dest="gmv_max_custom_anchor_videos_command", required=True)

    p = videos_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.add_argument("--store-id", help="Required unless provided in --payload-json/--payload-file.")
    p.add_argument("--store-authorized-bc-id", help="Required unless provided in --payload-json/--payload-file.")
    p.add_argument("--creative-source", default="CUSTOMIZED")
    p.add_argument("--spu-id", dest="spu_ids", action="append")
    p.add_argument("--keyword", help="Keyword search. Numeric item_id can be used for exact preview enrichment probes.")
    p.add_argument("--campaign-id")
    p.add_argument("--need-auth-code-video", action="store_true", default=None)
    p.add_argument("--identity-list-json", help="JSON array for identity_list.")
    p.add_argument("--sort-field")
    p.add_argument("--sort-type")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_gmv_max_custom_anchor_videos_list)


def add_tiktok_gmv_max_video_group(parent_subparsers) -> None:
    videos = parent_subparsers.add_parser(
        "gmv-max-videos",
        aliases=["gmvmax-videos"],
        help="TikTok posts available for Product GMV Max campaigns",
    )
    videos_sub = videos.add_subparsers(dest="gmv_max_videos_command", required=True)

    p = videos_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.add_argument("--store-id", help="Required unless provided in --payload-json/--payload-file.")
    p.add_argument("--store-authorized-bc-id", help="Required unless provided in --payload-json/--payload-file.")
    p.add_argument("--spu-id", dest="spu_ids", action="append", help="Product SPU/item_group_id filter. Repeatable.")
    p.add_argument("--custom-posts-eligible", action="store_true", default=None)
    p.add_argument(
        "--sort-field",
        choices=["GMV", "POST_TIME", "VIDEO_VIEWS", "VIDEO_LIKES", "CLICK_THROUGH_RATE", "PRODUCT_CLICKS"],
    )
    p.add_argument("--sort-type", choices=["ASC", "DESC"])
    p.add_argument("--keyword", help="Post caption or exact numeric item_id/post ID.")
    p.add_argument(
        "--need-auth-code-video",
        action="store_true",
        default=None,
        help="Include AUTH_CODE identity posts when set.",
    )
    p.add_argument(
        "--no-need-auth-code-video",
        action="store_true",
        default=False,
        help="Pass need_auth_code_video=false explicitly.",
    )
    p.add_argument("--identity-list-json", help="JSON array for identity_list.")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=10)
    p.set_defaults(func=command_tiktok_gmv_max_videos_list)


def add_tiktok_gmv_max_product_group(parent_subparsers) -> None:
    products = parent_subparsers.add_parser(
        "gmv-max-products",
        aliases=["gmvmax-products", "store-products"],
        help="TikTok Shop products available for Product GMV Max campaigns",
    )
    products_sub = products.add_subparsers(dest="gmv_max_products_command", required=True)

    p = products_sub.add_parser("list", help="Get TikTok Shop products through /store/product/get/.")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.add_argument("--bc-id", required=True, help="Business Center ID.")
    p.add_argument("--store-id", required=True, help="TikTok Shop store ID.")
    p.add_argument("--item-group-id", dest="item_group_ids", action="append", help="SPU/item_group_id filter. Max 10.")
    p.add_argument("--product-name", help="Product name keyword filter.")
    p.add_argument("--ad-creation-eligible", choices=["GMV_MAX", "CUSTOM_SHOP_ADS"], default="GMV_MAX")
    p.add_argument("--sort-field", choices=["min_price", "historical_sales"])
    p.add_argument("--sort-type", choices=["ASC", "DESC"])
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--normalized", action="store_true", help="Print the normalized report-friendly payload.")
    p.add_argument("--out", help="Optional JSON output path for the normalized product map.")
    p.set_defaults(func=command_tiktok_gmv_max_products_list)


def add_tiktok_gmv_max_report_group(parent_subparsers) -> None:
    reports = parent_subparsers.add_parser(
        "gmv-max-reports",
        aliases=["gmvmax-reports"],
        help="TikTok GMV Max reports",
    )
    reports_sub = reports.add_subparsers(dest="gmv_max_reports_command", required=True)

    p = reports_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("--store-id", dest="store_ids", action="append", required=True)
    p.add_argument("--dimension", dest="dimensions", action="append", choices=TIKTOK_GMV_MAX_REPORT_DIMENSION_CHOICES)
    p.add_argument(
        "--level",
        choices=sorted(TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS),
        help="Shortcut for common dimension groupings. Ignored when --dimension is provided.",
    )
    p.add_argument(
        "--time-grain",
        choices=sorted(TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS),
        default="none",
        help="Append stat_time_day or stat_time_hour to the selected --level dimensions.",
    )
    p.add_argument(
        "--metric",
        dest="metrics",
        action="append",
        help="Defaults to GMV Max KPI metrics; item_id creative dimensions also include product click/video-view metrics.",
    )
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--gmv-max-promotion-type", dest="gmv_max_promotion_types", action="append")
    p.add_argument("--campaign-id", dest="campaign_ids", action="append")
    p.add_argument("--campaign-name")
    p.add_argument("--campaign-status", dest="campaign_statuses", action="append")
    p.add_argument("--creative-delivery-status", dest="creative_delivery_statuses", action="append")
    p.add_argument("--creative-type", dest="creative_types", action="append")
    p.add_argument("--item-group-id", dest="item_group_ids", action="append")
    p.add_argument("--item-id", dest="item_ids", action="append")
    p.add_argument("--room-id", dest="room_ids", action="append")
    p.add_argument("--search-word")
    add_filtering_arguments(p)
    p.add_argument("--sort-field")
    p.add_argument("--sort-type")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--enable-total-metrics", action="store_true", default=None)
    p.set_defaults(func=command_tiktok_gmv_max_reports_get)


def add_tiktok_adgroup_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    adgroups = parent_subparsers.add_parser(name, help=help_text)
    adgroups_sub = adgroups.add_subparsers(dest=f"{name.replace('-', '_')}_command", required=True)

    p = adgroups_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--adgroup-id", dest="adgroup_ids", action="append")
    p.add_argument("--campaign-id", dest="campaign_ids", action="append")
    p.add_argument("--name")
    p.add_argument("--objective-type")
    p.add_argument("--optimization-goal")
    p.add_argument("--primary-status")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--fields", nargs="*")
    p.add_argument("--exclude-field-types", nargs="*")
    p.set_defaults(func=command_tiktok_adgroups_list, smart_plus=smart_plus)

    p = adgroups_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("adgroup_id")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_adgroups_get, smart_plus=smart_plus)

    p = adgroups_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    p.add_argument("--campaign-id")
    p.add_argument("--name")
    p.add_argument("--billing-event")
    p.add_argument("--optimization-goal")
    p.add_argument("--operation-status")
    p.add_argument("--budget", type=float)
    p.add_argument("--budget-mode")
    p.add_argument("--bid-price", type=float)
    p.add_argument("--bid-type")
    p.add_argument("--promotion-type")
    p.add_argument("--promotion-website-type")
    p.add_argument("--pixel-id")
    p.add_argument("--placement-type")
    p.add_argument("--placements", nargs="*")
    p.add_argument("--start-time")
    p.add_argument("--end-time")
    p.add_argument("--schedule-type")
    p.add_argument("--targeting-spec-json")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_adgroups_create, smart_plus=smart_plus)

    p = adgroups_sub.add_parser("update")
    add_tiktok_auth_arguments(p)
    p.add_argument("adgroup_id")
    p.add_argument("--name")
    p.add_argument("--budget", type=float)
    p.add_argument("--bid-price", type=float)
    p.add_argument("--roas-bid", type=float)
    p.add_argument("--conversion-bid-price", type=float)
    p.add_argument("--comment-disabled", action="store_true", default=None)
    p.add_argument("--share-disabled", action="store_true", default=None)
    p.add_argument("--pacing")
    p.add_argument("--start-time")
    p.add_argument("--end-time")
    p.add_argument("--schedule-type")
    p.add_argument("--targeting-optimization-mode")
    p.add_argument("--targeting-spec-json")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_adgroups_update, smart_plus=smart_plus)

    p = adgroups_sub.add_parser("status")
    add_tiktok_auth_arguments(p)
    p.add_argument("adgroup_ids", nargs="+")
    p.add_argument("--operation-status", required=True)
    if not smart_plus:
        p.add_argument("--allow-partial-success", action="store_true", default=None)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_adgroups_status, smart_plus=smart_plus)


def add_tiktok_ad_group(parent_subparsers, *, name: str, smart_plus: bool, help_text: str) -> None:
    ads = parent_subparsers.add_parser(name, help=help_text)
    ads_sub = ads.add_subparsers(dest=f"{name.replace('-', '_')}_command", required=True)

    p = ads_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--ad-id", dest="ad_ids", action="append")
    p.add_argument("--campaign-id", dest="campaign_ids", action="append")
    p.add_argument("--adgroup-id", dest="adgroup_ids", action="append")
    p.add_argument("--name")
    p.add_argument("--objective-type")
    p.add_argument("--optimization-goal")
    p.add_argument("--primary-status")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--fields", nargs="*")
    p.add_argument("--exclude-field-types", nargs="*")
    p.set_defaults(func=command_tiktok_ads_list, smart_plus=smart_plus)

    p = ads_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("ad_id")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_ads_get, smart_plus=smart_plus)

    p = ads_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    p.add_argument("--adgroup-id", required=True)
    p.add_argument("--name")
    p.add_argument("--operation-status")
    p.add_argument("--creatives-json")
    p.add_argument("--ad-configuration-json")
    p.add_argument("--ad-text-list-json")
    p.add_argument("--auto-message-list-json")
    p.add_argument("--call-to-action-list-json")
    p.add_argument("--creative-list-json")
    p.add_argument("--deeplink-list-json")
    p.add_argument("--interactive-add-on-list-json")
    p.add_argument("--landing-page-url-list-json")
    p.add_argument("--page-list-json")
    if smart_plus:
        p.add_argument("--page-id", help="Convenience alias for SmartPlus page_list[0].page_id (App Profile Page ID)")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_ads_create, smart_plus=smart_plus)

    p = ads_sub.add_parser("update")
    add_tiktok_auth_arguments(p)
    p.add_argument("ad_id")
    if not smart_plus:
        p.add_argument("--adgroup-id")
        p.add_argument("--patch-update", action="store_true", default=None)
        p.add_argument("--creatives-json")
    p.add_argument("--name")
    p.add_argument("--ad-configuration-json")
    p.add_argument("--ad-text-list-json")
    p.add_argument("--call-to-action-list-json")
    p.add_argument("--creative-list-json")
    p.add_argument("--deeplink-list-json")
    p.add_argument("--interactive-add-on-list-json")
    p.add_argument("--landing-page-url-list-json")
    p.add_argument("--page-list-json")
    if smart_plus:
        p.add_argument("--page-id", help="Convenience alias for SmartPlus page_list[0].page_id (App Profile Page ID)")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_ads_update, smart_plus=smart_plus)

    p = ads_sub.add_parser("status")
    add_tiktok_auth_arguments(p)
    p.add_argument("ad_ids", nargs="+")
    p.add_argument("--operation-status", required=True)
    if not smart_plus:
        p.add_argument("--aco-ad-id", dest="aco_ad_ids", action="append")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_ads_status, smart_plus=smart_plus)


def add_tiktok_media_group(parent_subparsers) -> None:
    media = parent_subparsers.add_parser("media", help="TikTok media upload operations")
    media_sub = media.add_subparsers(dest="media_command", required=True)

    p = media_sub.add_parser("upload-image")
    add_tiktok_auth_arguments(p)
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.add_argument("--upload-type")
    p.add_argument("--file-id")
    p.add_argument("--image-signature")
    p.add_argument("--image-url")
    p.set_defaults(func=command_tiktok_media_upload_image)

    p = media_sub.add_parser("upload-video")
    add_tiktok_auth_arguments(p)
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.add_argument("--upload-type")
    p.add_argument("--file-id")
    p.add_argument("--video-id")
    p.add_argument("--video-signature")
    p.add_argument("--video-url")
    p.add_argument("--auto-bind-enabled", action="store_true", default=None)
    p.add_argument("--auto-fix-enabled", action="store_true", default=None)
    p.add_argument("--flaw-detect", action="store_true", default=None)
    p.add_argument("--is-third-party", action="store_true", default=None)
    p.set_defaults(func=command_tiktok_media_upload_video)


def add_tiktok_images_group(parent_subparsers) -> None:
    images = parent_subparsers.add_parser("images", help="TikTok image asset operations")
    images_sub = images.add_subparsers(dest="images_command", required=True)

    p = images_sub.add_parser("upload")
    add_tiktok_auth_arguments(p)
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.add_argument("--upload-type")
    p.add_argument("--file-id")
    p.add_argument("--image-signature")
    p.add_argument("--image-url")
    p.set_defaults(func=command_tiktok_media_upload_image)

    p = images_sub.add_parser("info")
    add_tiktok_auth_arguments(p)
    p.add_argument("image_ids", nargs="+")
    p.set_defaults(func=command_tiktok_images_info)


def add_tiktok_videos_group(parent_subparsers) -> None:
    videos = parent_subparsers.add_parser("videos", help="TikTok video asset operations")
    videos_sub = videos.add_subparsers(dest="videos_command", required=True)

    p = videos_sub.add_parser("upload")
    add_tiktok_auth_arguments(p)
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.add_argument("--upload-type")
    p.add_argument("--file-id")
    p.add_argument("--video-id")
    p.add_argument("--video-signature")
    p.add_argument("--video-url")
    p.add_argument("--auto-bind-enabled", action="store_true", default=None)
    p.add_argument("--auto-fix-enabled", action="store_true", default=None)
    p.add_argument("--flaw-detect", action="store_true", default=None)
    p.add_argument("--is-third-party", action="store_true", default=None)
    p.set_defaults(func=command_tiktok_media_upload_video)

    p = videos_sub.add_parser("info")
    add_tiktok_auth_arguments(p)
    p.add_argument("video_ids", nargs="+")
    p.set_defaults(func=command_tiktok_videos_info)

    p = videos_sub.add_parser("search")
    add_tiktok_auth_arguments(p)
    p.add_argument("--video-id", dest="video_ids", action="append")
    p.add_argument("--material-id", dest="material_ids", action="append")
    p.add_argument("--displayable", action="store_true", default=None)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--ratio-json")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_videos_search)


def add_tiktok_aigc_group(parent_subparsers) -> None:
    aigc = parent_subparsers.add_parser(
        "aigc",
        help="TikTok AIGC helpers: voices, image-to-video, product-video, and digital avatars",
        description=(
            "TikTok AIGC capability map:\n"
            "- voices: list reusable voice assets from /creative/aigc/voice/get/.\n"
            "- image-animation: image-to-video generation via /creative/aigc/image_animation/task/create/.\n"
            "- video: product-video tasks via /creative/aigc/video/task/create/ for VOICEOVER, AVATAR_PRODUCT, and TRYON.\n"
            "- digital-avatar: standalone avatar assets and avatar video tasks via /creative/digital_avatar/*.\n"
            "Use image-animation when you start from images, use video when you are composing product_video_info flows, "
            "and use digital-avatar when you need a talking avatar script-driven video."
        ),
    )
    aigc_sub = aigc.add_subparsers(dest="aigc_command", required=True)

    p = aigc_sub.add_parser(
        "voices",
        help="List reusable AIGC voice assets",
        description="List reusable voice assets from /creative/aigc/voice/get/. Use this before VOICEOVER or digital-avatar when you need to choose a voice_id.",
    )
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--voice-id", dest="voice_ids", action="append")
    p.add_argument("--language")
    p.add_argument("--gender")
    p.add_argument("--speaker-type")
    p.add_argument("--tag-type")
    p.add_argument("--tag-name", dest="tag_names", action="append")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_aigc_voices_list)

    image_animation = aigc_sub.add_parser(
        "image-animation",
        help="Image-to-video generation from /creative/aigc/image_animation/task/create/",
        description=(
            "Image-to-video flow backed by /creative/aigc/image_animation/task/create/ and the shared "
            "/creative/aigc/video/task/list/ + /creative/aigc/video/list/ polling endpoints. "
            "Use this when the source material is one image and you want short animated clips."
        ),
    )
    image_animation_sub = image_animation.add_subparsers(dest="image_animation_command", required=True)

    p = image_animation_sub.add_parser(
        "create",
        help="Create image-to-video tasks from one image URL",
        description="Create image-to-video tasks from one image using /creative/aigc/image_animation/task/create/.",
        epilog=(
            "Example:\n"
            "  motata tiktok aigc image-animation create \\\n"
            "    --advertiser-id 123 \\\n"
            "    --access-token \"$TOKEN\" \\\n"
            "    --image-url \"https://example.com/hero.jpg\" \\\n"
            "    --animation-prompt \"Product demo\" \\\n"
            "    --provider-model GOKU"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_tiktok_auth_arguments(p)
    add_tiktok_image_animation_create_arguments(p)
    p.set_defaults(func=command_tiktok_aigc_image_animation_create)

    p = image_animation_sub.add_parser(
        "tasks",
        help="Get image-animation task status by task_id",
        description="Check image-animation task status through /creative/aigc/video/task/list/ with aigc_video_type=IMAGE_ANIMATION.",
    )
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--task-id", dest="task_ids", action="append", required=True)
    p.add_argument("--status")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_aigc_image_animation_tasks)

    p = image_animation_sub.add_parser(
        "list",
        help="List generated image-animation videos",
        description="List generated image-animation videos through /creative/aigc/video/list/ with aigc_video_types=[IMAGE_ANIMATION].",
    )
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--task-id", dest="task_ids", action="append")
    p.add_argument("--video-id", dest="video_ids", action="append")
    p.add_argument("--status")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_aigc_image_animation_list)

    video = aigc_sub.add_parser(
        "video",
        help="Product-video AIGC tasks from /creative/aigc/video/task/create/",
        description=(
            "Product-video tasks backed by /creative/aigc/video/task/create/. "
            "Use this for VOICEOVER, AVATAR_PRODUCT, and TRYON, all of which use TikTok's product_video_info payload shape."
        ),
    )
    video_sub = video.add_subparsers(dest="aigc_video_command", required=True)

    p = video_sub.add_parser(
        "create",
        help="Create VOICEOVER, AVATAR_PRODUCT, or TRYON tasks",
        description=(
            "Create product-video tasks through /creative/aigc/video/task/create/. "
            "VOICEOVER adds narration to input videos or images, AVATAR_PRODUCT combines product inputs with a real avatar, "
            "and TRYON uses product images for try-on generation."
        ),
        epilog=(
            "Example:\n"
            "  motata tiktok aigc video create \\\n"
            "    --advertiser-id 123 \\\n"
            "    --access-token \"$TOKEN\" \\\n"
            "    --type VOICEOVER \\\n"
            "    --product-url \"https://shop.example.com/products/demo\" \\\n"
            "    --input-video-id v10033g50000demo123\n"
            "\n"
            "Note: --input-video-id expects a video_id, not a task_id."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_tiktok_auth_arguments(p)
    add_tiktok_product_video_create_arguments(p)
    p.set_defaults(func=command_tiktok_aigc_video_create)

    p = video_sub.add_parser(
        "tasks",
        help="Get product-video task status by type and task_id",
        description="Check VOICEOVER, AVATAR_PRODUCT, TRYON, or other AIGC video task status through /creative/aigc/video/task/list/.",
    )
    add_tiktok_auth_arguments(p)
    p.add_argument("--type", dest="aigc_video_type", choices=TIKTOK_AIGC_VIDEO_TYPE_CHOICES, required=True)
    add_filtering_arguments(p)
    p.add_argument("--task-id", dest="task_ids", action="append", required=True)
    p.add_argument("--status")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_aigc_video_tasks)

    p = video_sub.add_parser(
        "list",
        help="List generated AIGC videos by type",
        description="List generated product-video outputs through /creative/aigc/video/list/ filtered by aigc_video_type.",
    )
    add_tiktok_auth_arguments(p)
    p.add_argument("--type", dest="aigc_video_type", choices=TIKTOK_AIGC_VIDEO_TYPE_CHOICES, required=True)
    add_filtering_arguments(p)
    p.add_argument("--task-id", dest="task_ids", action="append")
    p.add_argument("--video-id", dest="video_ids", action="append")
    p.add_argument("--status")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_aigc_video_list)

    digital_avatar = aigc_sub.add_parser(
        "digital-avatar",
        help="Standalone talking-avatar assets and tasks from /creative/digital_avatar/*",
        description=(
            "Standalone digital-avatar flow backed by /creative/digital_avatar/get/, "
            "/creative/digital_avatar/video/task/create/, /creative/digital_avatar/video/task/get/, "
            "and /creative/digital_avatar/video/list/. Use this when you need a scripted talking avatar video, "
            "not the product_video_info flow."
        ),
    )
    digital_avatar_sub = digital_avatar.add_subparsers(dest="digital_avatar_command", required=True)

    p = digital_avatar_sub.add_parser(
        "list",
        help="List available digital avatar assets",
        description="List available avatar assets from /creative/digital_avatar/get/ before creating avatar videos.",
    )
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--avatar-id", dest="avatar_ids", action="append")
    p.add_argument("--identity", choices=TIKTOK_DIGITAL_AVATAR_IDENTITY_CHOICES)
    p.add_argument("--keyword")
    p.add_argument("--tag-groups-json")
    p.add_argument("--tag-type")
    p.add_argument("--tag", dest="tags", action="append")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_digital_avatars_list)

    p = digital_avatar_sub.add_parser(
        "task-create",
        help="Create a standalone digital avatar talking video",
        description=(
            "Create a standalone avatar talking video through /creative/digital_avatar/video/task/create/.\n"
            "CLI flags map into TikTok's material_packages[] shape."
        ),
        epilog=(
            "Example:\n"
            "  motata tiktok aigc digital-avatar task-create \\\n"
            "    --advertiser-id 123 \\\n"
            "    --access-token \"$TOKEN\" \\\n"
            "    --avatar-id 7301881390801371138 \\\n"
            "    --script \"Introduce the product in a short upbeat tone.\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_tiktok_auth_arguments(p)
    add_tiktok_digital_avatar_create_arguments(p)
    p.set_defaults(func=command_tiktok_digital_avatar_task_create)

    p = digital_avatar_sub.add_parser(
        "task-get",
        help="Get digital avatar task status",
        description="Get digital avatar task status from /creative/digital_avatar/video/task/get/ with fallback checking against video/list.",
    )
    add_tiktok_auth_arguments(p)
    p.add_argument("task_id")
    p.set_defaults(func=command_tiktok_digital_avatar_task_get)

    p = digital_avatar_sub.add_parser(
        "videos",
        help="List generated digital avatar videos",
        description="List generated digital avatar videos from /creative/digital_avatar/video/list/.",
    )
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--task-id", dest="task_ids", action="append")
    p.add_argument("--avatar-video-id", dest="avatar_video_ids", action="append")
    p.add_argument("--avatar-id")
    p.add_argument("--status")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_digital_avatar_video_list)

    p = digital_avatar_sub.add_parser(
        "rename-video",
        help="Rename a generated digital avatar video",
        description="Rename a generated avatar video through /file/video/ad/update/ using avatar_video_id and file_name.",
    )
    add_tiktok_auth_arguments(p)
    p.add_argument("--avatar-video-id", required=True)
    p.add_argument("--file-name", required=True)
    p.set_defaults(func=command_tiktok_digital_avatar_video_rename)


def add_tiktok_assets_group(parent_subparsers) -> None:
    assets = parent_subparsers.add_parser("assets", help="TikTok creative asset helpers")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)

    p = assets_sub.add_parser("image-info")
    add_tiktok_auth_arguments(p)
    p.add_argument("image_ids", nargs="+")
    p.set_defaults(func=command_tiktok_images_info)

    p = assets_sub.add_parser("video-info")
    add_tiktok_auth_arguments(p)
    p.add_argument("video_ids", nargs="+")
    p.set_defaults(func=command_tiktok_videos_info)

    p = assets_sub.add_parser("video-search")
    add_tiktok_auth_arguments(p)
    p.add_argument("--video-id", dest="video_ids", action="append")
    p.add_argument("--material-id", dest="material_ids", action="append")
    p.add_argument("--displayable", action="store_true", default=None)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--ratio-json")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_videos_search)

    p = assets_sub.add_parser("delete")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_assets_delete)

    p = assets_sub.add_parser("share")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_assets_share)

    p = assets_sub.add_parser("discover")
    add_tiktok_auth_arguments(p)
    p.add_argument("--bc-id", dest="bc_ids", action="append")
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--catalog-page-size", type=int, default=20)
    p.add_argument("--include-catalog-bindings", action="store_true")
    p.add_argument("--skip-catalogs", action="store_true")
    p.set_defaults(func=command_tiktok_assets_discover)

    p = assets_sub.add_parser("cta-list")
    p.add_argument("--type", choices=["aco", "smart-plus", "all"], default="all",
                   help="CTA type: aco (regular ads), smart-plus (Smart Plus ads), or all")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_cta_list)


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
    add_payload_arguments(parser)
    parser.add_argument("--smart-plus", action="store_true")
    parser.add_argument("--identity-id")
    parser.add_argument("--identity-type")
    parser.add_argument("--identity-authorized-bc-id")
    parser.add_argument("--video-id")
    parser.add_argument("--image-id", dest="image_ids", action="append")
    parser.add_argument("--image-web-uri", dest="image_web_uris", action="append")
    parser.add_argument("--landing-page-url", dest="landing_page_urls", action="append")
    parser.add_argument("--tracking-app-id")
    parser.add_argument("--tracking-pixel-id")
    parser.add_argument("--tracking-offline-event-set-id", dest="tracking_offline_event_set_ids", action="append")


def add_tiktok_validate_group(parent_subparsers) -> None:
    validate = parent_subparsers.add_parser("validate", help="TikTok preflight validation helpers")
    validate_sub = validate.add_subparsers(dest="validate_command", required=True)

    p = validate_sub.add_parser("creative")
    add_tiktok_auth_arguments(p)
    add_tiktok_validate_common_arguments(p)
    p.set_defaults(func=command_tiktok_validate_creative)

    p = validate_sub.add_parser("ad-link")
    add_tiktok_auth_arguments(p)
    p.add_argument("--adgroup-id", required=True)
    add_tiktok_validate_common_arguments(p)
    p.set_defaults(func=command_tiktok_validate_ad_link)

    p = validate_sub.add_parser("promoted-object")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.add_argument("--smart-plus", action="store_true")
    p.add_argument("--campaign-id")
    p.add_argument("--adgroup-id")
    p.add_argument("--objective-type")
    p.add_argument("--promotion-type")
    p.add_argument("--optimization-goal")
    p.add_argument("--optimization-event")
    p.add_argument("--placement", dest="placements", action="append")
    p.add_argument("--pixel-id")
    p.add_argument("--app-id")
    p.add_argument("--store-id")
    p.add_argument("--catalog-id")
    p.add_argument("--landing-page-url")
    p.add_argument("--app-promotion-type")
    p.add_argument("--campaign-type")
    p.add_argument("--bc-id", dest="bc_ids", action="append")
    p.set_defaults(func=command_tiktok_validate_promoted_object)


def add_tiktok_accounts_group(parent_subparsers) -> None:
    accounts = parent_subparsers.add_parser("accounts", help="TikTok advertiser account operations")
    accounts_sub = accounts.add_subparsers(dest="accounts_command", required=True)

    p = accounts_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("advertiser_ids", nargs="*")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_accounts_list)

    p = accounts_sub.add_parser("info")
    add_tiktok_auth_arguments(p)
    p.add_argument("advertiser_ids", nargs="*")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_accounts_info)

    p = accounts_sub.add_parser("inspect")
    add_tiktok_auth_arguments(p)
    p.add_argument("advertiser_ids", nargs="*")
    p.add_argument("--fields", nargs="*")
    p.set_defaults(func=command_tiktok_accounts_inspect)

    p = accounts_sub.add_parser("update")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_accounts_update)


def add_tiktok_activities_group(parent_subparsers) -> None:
    activities = parent_subparsers.add_parser("activities", aliases=["activity"], help="TikTok changelog activity operations")
    activities_sub = activities.add_subparsers(dest="activities_command", required=True)

    p = activities_sub.add_parser("get", help="Create and optionally poll a changelog task for advertiser activities")
    add_tiktok_auth_arguments(p)
    p.add_argument("--since", "--start-date", dest="start_date")
    p.add_argument("--until", "--end-date", dest="end_date")
    p.add_argument("--timezone")
    p.add_argument("--module", choices=["BIDDING_AND_OPTIMIZATION", "BUDGET", "STATUS", "TARGETING"])
    p.add_argument("--object-type", choices=["AD", "ADGROUP", "ADVERTISER", "CAMPAIGN", "INSTANT_FORM"])
    p.add_argument("--object-ids", nargs="+")
    p.add_argument(
        "--operation-types",
        nargs="+",
        help=(
            "Operation types. Accepts space-separated values or comma-separated strings: "
            "CREATE,AUDIT,STATUS,UPDATE,DELETE,DOWNLOAD_LEADS,SUBSCRIBE_FORM,UNSUBSCRIBE_FORM."
        ),
    )
    p.add_argument("--order-fields", nargs="+")
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--no-wait", action="store_true", help="Return after task creation without polling.")
    p.add_argument("--timeout-seconds", type=int, default=60)
    p.add_argument("--poll-seconds", type=float, default=5.0)
    p.set_defaults(func=command_tiktok_activities_get)


def add_tiktok_auth_group(parent_subparsers) -> None:
    auth = parent_subparsers.add_parser("auth", help="TikTok authentication helpers")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    p = auth_sub.add_parser("advertisers")
    p.add_argument("--app-id", required=True)
    p.add_argument("--secret", required=True)
    p.add_argument("--access-token", required=True)
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_auth_advertisers)


def add_tiktok_targeting_group(parent_subparsers) -> None:
    targeting = parent_subparsers.add_parser("targeting", help="TikTok targeting helpers")
    targeting_sub = targeting.add_subparsers(dest="targeting_command", required=True)

    p = targeting_sub.add_parser("regions")
    add_tiktok_auth_arguments(p)
    p.add_argument("--language")
    p.set_defaults(func=command_tiktok_targeting_regions)

    p = targeting_sub.add_parser("search")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_targeting_search)

    p = targeting_sub.add_parser("info")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_targeting_info)

    p = targeting_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--location-id", dest="location_ids", action="append", required=True)
    p.add_argument("--scene", required=True)
    p.set_defaults(func=command_tiktok_targeting_list)


def add_tiktok_identities_group(parent_subparsers) -> None:
    identities = parent_subparsers.add_parser("identities", help="TikTok identity asset operations")
    identities_sub = identities.add_subparsers(dest="identities_command", required=True)

    p = identities_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--identity-type")
    p.add_argument("--identity-authorized-bc-id")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_identities_list)

    p = identities_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_identities_create)

    p = identities_sub.add_parser("video-info")
    add_tiktok_auth_arguments(p)
    p.add_argument("--identity-type", required=True)
    p.add_argument("--identity-id", required=True)
    p.add_argument("--identity-authorized-bc-id")
    p.add_argument("--item-id")
    p.add_argument("--item-ids", dest="item_ids", action="append", help="Repeatable item_id. Max 20 per TikTok API request.")
    p.add_argument("--item-type", choices=["VIDEO", "CAROUSEL"], default="VIDEO")
    p.set_defaults(func=command_tiktok_identities_video_info)


def add_tiktok_insights_group(parent_subparsers) -> None:
    insights = parent_subparsers.add_parser("insights", help="TikTok reporting and insights")
    insights_sub = insights.add_subparsers(dest="insights_command", required=True)

    p = insights_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("--report-type", required=True)
    p.add_argument("--query-mode")
    p.add_argument("--advertiser", dest="advertiser_ids", action="append")
    p.add_argument("--bc-id")
    p.add_argument("--service-type")
    p.add_argument("--data-level")
    p.add_argument("--dimension", dest="dimensions", action="append")
    p.add_argument("--metric", dest="metrics", action="append")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--order-field")
    p.add_argument("--order-type")
    p.add_argument("--filtering-json")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--enable-total-metrics", action="store_true", default=None)
    p.add_argument("--multi-adv-report-in-utc-time", action="store_true", default=None)
    p.add_argument("--query-lifetime", action="store_true", default=None)
    p.set_defaults(func=command_tiktok_insights_get)

    p = insights_sub.add_parser("smartplus-overview")
    add_tiktok_auth_arguments(p)
    p.add_argument("--dimension", dest="dimensions", action="append", required=True)
    p.add_argument("--metric", dest="metrics", action="append")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--query-lifetime", action="store_true", default=None)
    p.add_argument("--sort-field")
    p.add_argument("--sort-type")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_insights_smartplus_overview)

    p = insights_sub.add_parser("smartplus-breakdown")
    add_tiktok_auth_arguments(p)
    p.add_argument("--dimension", dest="dimensions", action="append", required=True)
    p.add_argument("--metric", dest="metrics", action="append")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--sort-field")
    p.add_argument("--sort-type")
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_insights_smartplus_breakdown)


def add_tiktok_landing_pages_group(parent_subparsers) -> None:
    landing_pages = parent_subparsers.add_parser(
        "landing-pages",
        aliases=["landing-page", "landings"],
        help="TikTok landing page spend analysis",
    )
    landing_pages_sub = landing_pages.add_subparsers(dest="landing_pages_command", required=True)

    p = landing_pages_sub.add_parser("analyze")
    p.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_ids",
        action="append",
        help="TikTok advertiser ID. Repeat for multiple advertisers.",
    )
    p.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    p.add_argument("--app-id", default=os.environ.get("TIKTOK_APP_ID"))
    p.add_argument("--secret", default=os.environ.get("TIKTOK_APP_SECRET"))
    p.add_argument("--start-date", "--since", dest="start_date")
    p.add_argument("--end-date", "--until", dest="end_date")
    p.add_argument("--top", type=int)
    p.add_argument("--ad-limit", type=int, help="Analyze only the top spend ads returned by the TikTok report.")
    p.add_argument("--advertiser-limit", type=int, default=10, help="Top spend auto-discovered advertisers to analyze.")
    p.add_argument("--page-size", type=int, default=1000, help="TikTok ad report page size.")
    p.add_argument("--max-pages", type=int, default=50, help="Maximum TikTok report pages per advertiser.")
    p.add_argument(
        "--no-product",
        dest="no_product",
        action="store_true",
        default=True,
        help="Skip product page scraping/enrichment. This is the default.",
    )
    p.add_argument(
        "--product",
        dest="no_product",
        action="store_false",
        help="Enable product page scraping/enrichment.",
    )
    p.add_argument("--product-limit", type=int, default=50, help="Maximum ranked landing pages to product-enrich.")
    p.add_argument("--include-ads", action="store_true", help="Include all ads under each landing page group.")
    p.add_argument("--smart-plus", action="store_true", help="Resolve SmartPlus ad details instead of normal ads.")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_landing_pages_analyze)


def add_tiktok_apps_group(parent_subparsers) -> None:
    apps = parent_subparsers.add_parser(
        "apps",
        aliases=["app-discovery"],
        help="TikTok active app discovery from campaign, adgroup, and ad attributes",
    )
    apps_sub = apps.add_subparsers(dest="apps_command", required=True)
    p = apps_sub.add_parser("analyze")
    p.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_ids",
        action="append",
        help="TikTok advertiser ID. Repeat for multiple advertisers.",
    )
    p.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    p.add_argument("--start-date", "--since", dest="start_date")
    p.add_argument("--end-date", "--until", dest="end_date")
    p.add_argument("--advertiser-limit", type=int, default=2, help="Auto-discovered advertiser limit.")
    p.add_argument("--campaign-limit", type=int, default=20, help="Top spend campaigns to probe per advertiser.")
    p.add_argument("--include-campaigns", action="store_true", help="Include campaign-level app evidence under each app group.")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_apps_analyze)


def add_tiktok_user_type_group(parent_subparsers) -> None:
    user_type = parent_subparsers.add_parser(
        "user-type",
        aliases=["customer-type", "account-type", "classify-user"],
        help="Classify TikTok advertiser vertical from top-spend campaigns, landing pages, and app evidence",
    )
    user_type_sub = user_type.add_subparsers(dest="user_type_command", required=True)
    p = user_type_sub.add_parser("analyze")
    p.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_ids",
        action="append",
        help="TikTok advertiser ID. Repeat for multiple advertisers.",
    )
    p.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    p.add_argument("--start-date", "--since", dest="start_date")
    p.add_argument("--end-date", "--until", dest="end_date")
    p.add_argument("--advertiser-limit", type=int, default=10, help="Auto-discovered advertiser limit.")
    p.add_argument("--campaign-limit", type=int, default=10, help="Top spend campaigns to analyze per advertiser.")
    p.add_argument("--ad-limit", type=int, default=5, help="Top spend ads to inspect for landing URLs.")
    p.add_argument("--content-limit", type=int, default=60, help="Maximum landing/app-store URLs to scrape for classification.")
    p.add_argument("--page-size", type=int, default=1000, help="TikTok ad report page size.")
    p.add_argument("--max-pages", type=int, default=50, help="Maximum TikTok report pages per advertiser.")
    p.add_argument("--smart-plus", action="store_true", help="Resolve SmartPlus ad details for landing URLs.")
    p.add_argument("--include-evidence", action="store_true", help="Include matched text snippets and scraped content details.")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_user_type_analyze)


def add_tiktok_metrics_group(parent_subparsers) -> None:
    metrics = parent_subparsers.add_parser("metrics", help="TikTok metric probe helpers")
    metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    p = metrics_sub.add_parser("probe")
    p.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_ids",
        action="append",
        help="TikTok advertiser ID. Repeat for multiple advertisers.",
    )
    p.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    p.add_argument("--start-date", "--since", dest="start_date")
    p.add_argument("--end-date", "--until", dest="end_date")
    p.add_argument("--advertiser-limit", type=int, default=10, help="Top spend auto-discovered advertisers to probe.")
    p.add_argument("--page-size", type=int, default=5, help="Per metric-group report page size.")
    p.add_argument("--group", dest="groups", action="append", help="Probe only this metric group. Repeat for multiple groups.")
    p.add_argument(
        "--profile",
        choices=["light", "batch", "vertical", "full"],
        default="batch",
        help="Probe profile. batch avoids high-risk gated groups such as TikTok web_events; full is for connector QA.",
    )
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_metrics_probe)


def add_tiktok_audience_group(parent_subparsers) -> None:
    audience = parent_subparsers.add_parser(
        "audience",
        aliases=["audience-breakdown"],
        help="TikTok audience, geo, placement, and device breakdown diagnostics",
    )
    audience_sub = audience.add_subparsers(dest="audience_command", required=True)
    p = audience_sub.add_parser("breakdown")
    p.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_id",
        required=True,
    )
    p.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    p.add_argument("--json", action="store_true", default=True)
    p.add_argument("--start-date", "--since", dest="start_date")
    p.add_argument("--end-date", "--until", dest="end_date")
    p.add_argument(
        "--breakdown",
        dest="breakdowns",
        action="append",
        choices=["country", "age_gender", "placement", "device"],
        help="Audience lens to fetch. Repeat to limit the run; defaults to all key lenses.",
    )
    p.add_argument("--data-level", default="AUCTION_ADVERTISER")
    p.add_argument("--top", type=int, default=20, help="Maximum segments kept per breakdown.")
    p.add_argument("--page-size", type=int, default=500, help="Per-breakdown report page size.")
    p.set_defaults(func=command_tiktok_audience_breakdown)


def add_tiktok_creative_retention_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--advertiser-id",
        "--account-id",
        "--account",
        "--advertiser",
        dest="advertiser_id",
        required=True,
    )
    parser.add_argument("--access-token", default=os.environ.get("TIKTOK_ACCESS_TOKEN") or os.environ.get("MOTATA_TIKTOK_ACCESS_TOKEN"))
    parser.add_argument("--json", action="store_true", default=True)
    parser.add_argument("--start-date", "--since", dest="start_date")
    parser.add_argument("--end-date", "--until", dest="end_date")
    parser.add_argument("--top", type=int, default=20, help="Number of creatives kept in each ranking section.")
    parser.add_argument("--page-size", type=int, default=200, help="Ad-level report page size.")
    parser.add_argument("--max-pages", type=int, default=3, help="Maximum report pages to scan.")
    parser.add_argument("--ad-id", dest="ad_ids", action="append", help="Limit retention and preview enrichment to one final report ad ID. Repeatable.")
    parser.add_argument("--ad-ids", dest="ad_ids_csv", help="Comma-separated final report ad IDs for targeted retention and preview enrichment.")
    parser.add_argument("--no-attributes", action="store_true", default=False, help="Skip TikTok attribute metrics such as ad_name.")
    parser.add_argument("--no-previews", action="store_true", default=False, help="Skip ad/creative preview image and URL enrichment.")
    parser.add_argument(
        "--no-probe-on-missing-core",
        action="store_true",
        default=False,
        help="Do not run a full metric probe when core metrics, especially revenue, are missing.",
    )
    parser.set_defaults(func=command_tiktok_creative_retention_report)


def add_tiktok_creatives_group(parent_subparsers) -> None:
    creatives = parent_subparsers.add_parser("creatives", help="TikTok creative portfolio and text helpers")
    creatives_sub = creatives.add_subparsers(dest="creatives_command", required=True)

    p = creatives_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    add_filtering_arguments(p)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_creatives_list)

    p = creatives_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("creative_portfolio_id")
    p.set_defaults(func=command_tiktok_creatives_get)

    p = creatives_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creatives_create)

    p = creatives_sub.add_parser("share-link")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creatives_share_link)

    p = creatives_sub.add_parser("smart-text")
    add_tiktok_auth_arguments(p)
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creatives_smart_text)

    p = creatives_sub.add_parser("retention-report", aliases=["creative-retention"], help="Short-drama creative retention curve ranking")
    add_tiktok_creative_retention_report_arguments(p)


def add_tiktok_items_group(parent_subparsers) -> None:
    items = parent_subparsers.add_parser(
        "items",
        aliases=["item", "posts", "post"],
        help="Public TikTok post/item metadata resolver",
    )
    items_sub = items.add_subparsers(dest="items_command", required=True)
    p = items_sub.add_parser(
        "resolve",
        aliases=["get"],
        help="Resolve item IDs to real handle, username, preview, avatar, URL, and upload time",
    )
    p.add_argument("item_ids", nargs="+", type=item_id_from_value, help="TikTok item IDs or video URLs")
    p.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    p.add_argument("--workers", type=int, default=1, help="Concurrent workers for multiple item IDs")
    p.add_argument("--download-dir", help="Optional directory for avatar and preview images")
    p.add_argument("--save-html-dir", help="Optional directory for raw HTML debugging")
    p.add_argument("--include-timing", action="store_true", help="Include per-item elapsed_seconds")
    p.add_argument("--out", help="Optional JSON output path")
    p.add_argument("--json", action="store_true", default=True)
    p.set_defaults(func=command_tiktok_items_resolve)


def add_tiktok_creative_retention_group(parent_subparsers) -> None:
    creative_retention = parent_subparsers.add_parser(
        "creative-retention",
        aliases=["retention"],
        help="TikTok short-drama creative retention reports",
    )
    creative_retention_sub = creative_retention.add_subparsers(dest="creative_retention_command", required=True)
    p = creative_retention_sub.add_parser("report")
    add_tiktok_creative_retention_report_arguments(p)


def add_tiktok_creative_assets_group(parent_subparsers) -> None:
    creative_assets = parent_subparsers.add_parser(
        "creative-assets",
        aliases=["creative-asset"],
        help="Unified TikTok CreativeManagementApi helpers",
    )
    creative_assets_sub = creative_assets.add_subparsers(dest="creative_assets_command", required=True)

    portfolio = creative_assets_sub.add_parser("portfolio", help="Creative portfolio operations")
    portfolio_sub = portfolio.add_subparsers(dest="portfolio_command", required=True)

    p = portfolio_sub.add_parser("list")
    add_tiktok_auth_arguments(p)
    p.add_argument("--creative-portfolio-id", dest="creative_portfolio_ids", action="append")
    p.add_argument("--creative-portfolio-type", dest="creative_portfolio_types", action="append")
    p.add_argument("--title")
    p.add_argument("--query")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_list)

    p = portfolio_sub.add_parser("get")
    add_tiktok_auth_arguments(p)
    p.add_argument("creative_portfolio_id")
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_get)

    p = portfolio_sub.add_parser("create")
    add_tiktok_auth_arguments(p)
    p.add_argument("--creative-portfolio-type")
    p.add_argument("--portfolio-content-json")
    p.add_argument("--portfolio-content-file")
    p.add_argument("--title")
    p.add_argument("--ad-text")
    p.add_argument("--asset-content")
    p.add_argument("--primary-text")
    p.add_argument("--secondary-text")
    p.add_argument("--description")
    p.add_argument("--content-url")
    p.add_argument("--card-type")
    p.add_argument("--app-id")
    p.add_argument("--origin-app-id")
    p.add_argument("--image-id")
    p.add_argument("--jump-image-uri")
    p.add_argument("--thumbnail-id")
    p.add_argument("--video-id")
    p.add_argument("--identity-id")
    p.add_argument("--identity-type")
    p.add_argument("--identity-authorized-bc-id")
    p.add_argument("--call-to-action")
    p.add_argument("--call-to-action-text")
    p.add_argument("--product-source")
    p.add_argument("--product-set-id")
    p.add_argument("--catalog-id")
    p.add_argument("--catalog-authorized-bc-id")
    p.add_argument("--store-id")
    p.add_argument("--store-authorized-bc-id")
    p.add_argument("--category-label")
    p.add_argument("--product-platform-id")
    p.add_argument("--product-specific-type")
    p.add_argument("--card-show-price", action="store_true", default=None)
    p.add_argument("--card-image-index", type=int)
    p.add_argument("--display-price-enabled", action="store_true", default=None)
    p.add_argument("--enable-image-optimization", action="store_true", default=None)
    p.add_argument("--gesture-type", type=int)
    p.add_argument("--interactive-music-id")
    p.add_argument("--vertical-creative-strategy", type=int)
    p.add_argument("--vertical-video-strategy", type=int)
    p.add_argument("--slide-length", type=int)
    p.add_argument("--advanced-show-time", type=int)
    p.add_argument("--advanced-interact-type", type=int)
    p.add_argument("--advanced-interact-shape", type=int)
    p.add_argument("--asset-id", dest="asset_ids", action="append")
    p.add_argument("--sku-id", dest="sku_ids", action="append")
    p.add_argument("--item-group-id", dest="item_group_ids", action="append")
    p.add_argument("--tag", dest="tags", action="append")
    p.add_argument("--card-tag", dest="card_tags", action="append")
    p.add_argument("--selling-point", dest="selling_points", action="append")
    p.add_argument("--country-code", dest="country_codes", action="append")
    p.add_argument("--layout", dest="layouts", action="append")
    p.add_argument("--advanced-audio-video-id")
    p.add_argument("--advanced-gesture-icon-image-id")
    p.add_argument("--advanced-gesture-image-image-id")
    p.add_argument("--advanced-audio-info-json")
    p.add_argument("--advanced-audio-info-file")
    p.add_argument("--advanced-gesture-icon-json")
    p.add_argument("--advanced-gesture-icon-file")
    p.add_argument("--advanced-gesture-image-json")
    p.add_argument("--advanced-gesture-image-file")
    p.add_argument("--advanced-image-info-json")
    p.add_argument("--advanced-image-info-file")
    p.add_argument("--advanced-position-json")
    p.add_argument("--advanced-position-file")
    p.add_argument("--badge-image-info-json")
    p.add_argument("--badge-image-info-file")
    p.add_argument("--sticker-param-json")
    p.add_argument("--sticker-param-file")
    p.add_argument("--slide-dimension-json")
    p.add_argument("--slide-dimension-file")
    p.add_argument("--showcase-products-json")
    p.add_argument("--showcase-products-file")
    p.add_argument("--context-info-json")
    p.add_argument("--context-info-file")
    p.add_argument("--context-app-id")
    p.add_argument("--context-core-user-id")
    p.add_argument("--context-developer-id")
    p.add_argument("--context-x-forwarded-for")
    p.add_argument("--context-x-real-ip")
    p.add_argument("--context-user-agent")
    p.add_argument("--context-referer")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_create)

    p = portfolio_sub.add_parser("select")
    add_tiktok_auth_arguments(p)
    p.add_argument("--creative-portfolio-id", dest="creative_portfolio_ids", action="append")
    p.add_argument("--creative-portfolio-type", dest="creative_portfolio_types", action="append")
    p.add_argument("--title")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--max-pages", type=int, default=20)
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_select)

    p = portfolio_sub.add_parser("inspect")
    add_tiktok_auth_arguments(p)
    p.add_argument("--creative-portfolio-id", dest="creative_portfolio_ids", action="append")
    p.add_argument("--creative-portfolio-type", dest="creative_portfolio_types", action="append")
    p.add_argument("--title")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--include-raw", action="store_true", default=False)
    p.add_argument("--output-file")
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_inspect)

    p = portfolio_sub.add_parser("export")
    add_tiktok_auth_arguments(p)
    p.add_argument("--output-file", required=True)
    p.add_argument("--creative-portfolio-id", dest="creative_portfolio_ids", action="append")
    p.add_argument("--creative-portfolio-type", dest="creative_portfolio_types", action="append")
    p.add_argument("--title")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--max-pages", type=int, default=20)
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_export)

    p = portfolio_sub.add_parser("match-campaign")
    add_tiktok_auth_arguments(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--smart-plus", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--output-file")
    p.set_defaults(func=command_tiktok_creative_assets_portfolio_match_campaign)

    asset = creative_assets_sub.add_parser("asset", help="Creative asset delete/share helpers")
    asset_sub = asset.add_subparsers(dest="asset_command", required=True)

    p = asset_sub.add_parser("delete")
    add_tiktok_auth_arguments(p)
    p.add_argument("--image-id", dest="image_ids", action="append")
    p.add_argument("--video-id", dest="video_ids", action="append")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creative_assets_delete)

    p = asset_sub.add_parser("share")
    add_tiktok_auth_arguments(p)
    p.add_argument("--material-id", dest="material_ids", action="append")
    p.add_argument("--shared-advertiser-id", dest="shared_advertiser_ids", action="append")
    p.add_argument("--asset-type", choices=["VIDEO", "IMAGE", "MUSIC"])
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creative_assets_share)

    p = creative_assets_sub.add_parser("share-link", help="Create shareable links for creative assets")
    add_tiktok_auth_arguments(p)
    p.add_argument("--shared-assets-json")
    p.add_argument("--shared-assets-file")
    p.add_argument("--sharer")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creative_assets_share_link)

    p = creative_assets_sub.add_parser("smart-text", help="Generate smart text suggestions")
    add_tiktok_auth_arguments(p)
    p.add_argument("--adgroup-id")
    p.add_argument("--industry-id")
    p.add_argument("--keyword", dest="keywords", action="append")
    p.add_argument("--language")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--param-type", default="CUSTOMIZED")
    add_payload_arguments(p)
    p.set_defaults(func=command_tiktok_creative_assets_smart_text)


def register_tiktok_commands(subparsers) -> None:
    tiktok = subparsers.add_parser("tiktok", help="TikTok Business API operations")
    tiktok_sub = tiktok.add_subparsers(dest="tiktok_command", required=True)
    add_tiktok_auth_group(tiktok_sub)
    add_tiktok_accounts_group(tiktok_sub)
    add_tiktok_activities_group(tiktok_sub)
    add_tiktok_campaign_group(tiktok_sub, name="campaigns", smart_plus=False, help_text="Normal TikTok campaigns")
    add_tiktok_campaign_group(
        tiktok_sub,
        name="smartplus-campaigns",
        smart_plus=True,
        help_text="TikTok SmartPlus campaigns",
    )
    add_tiktok_gmv_max_campaign_group(tiktok_sub)
    add_tiktok_gmv_max_video_group(tiktok_sub)
    add_tiktok_gmv_max_custom_anchor_video_group(tiktok_sub)
    add_tiktok_gmv_max_product_group(tiktok_sub)
    add_tiktok_gmv_max_report_group(tiktok_sub)
    add_tiktok_adgroup_group(tiktok_sub, name="adgroups", smart_plus=False, help_text="Normal TikTok ad groups")
    add_tiktok_adgroup_group(
        tiktok_sub,
        name="smartplus-adgroups",
        smart_plus=True,
        help_text="TikTok SmartPlus ad groups",
    )
    add_tiktok_ad_group(tiktok_sub, name="ads", smart_plus=False, help_text="Normal TikTok ads")
    add_tiktok_ad_group(
        tiktok_sub,
        name="smartplus-ads",
        smart_plus=True,
        help_text="TikTok SmartPlus ads",
    )
    add_tiktok_creatives_group(tiktok_sub)
    add_tiktok_items_group(tiktok_sub)
    add_tiktok_creative_retention_group(tiktok_sub)
    add_tiktok_identities_group(tiktok_sub)
    add_tiktok_targeting_group(tiktok_sub)
    add_tiktok_insights_group(tiktok_sub)
    add_tiktok_landing_pages_group(tiktok_sub)
    add_tiktok_apps_group(tiktok_sub)
    add_tiktok_user_type_group(tiktok_sub)
    add_tiktok_metrics_group(tiktok_sub)
    add_tiktok_audience_group(tiktok_sub)
    add_tiktok_media_group(tiktok_sub)
    add_tiktok_images_group(tiktok_sub)
    add_tiktok_videos_group(tiktok_sub)
    add_tiktok_aigc_group(tiktok_sub)
    add_tiktok_creative_assets_group(tiktok_sub)
    add_tiktok_assets_group(tiktok_sub)
    add_tiktok_validate_group(tiktok_sub)
