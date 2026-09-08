"""TikTok payloads helpers.

Dependencies are explicit and supplied by the CLI compatibility wrappers; this
module never imports the command facade. Source bodies retain their original
formatting and behavior.
"""

from __future__ import annotations

import argparse
from typing import Any

from .client import TikTokClient


def assign_if_present(payload: dict[str, Any], key: str, value: Any, *, deps: Any) -> None:
    if value is not None:
        payload[key] = value


def non_empty_list(values: list[str] | None, *, deps: Any) -> list[str]:
    return [str(value) for value in (values or []) if str(value).strip()]


def validate_max_list_size(values: list[str], label: str, *, max_size: int, deps: Any) -> list[str]:
    if len(values) > max_size:
        raise deps.CliError(f"{label} supports at most {max_size} items")
    return values


def require_non_empty_payload(
    payload: dict[str, Any],
    label: str = 'payload',
    *,
    deps: Any,
) -> dict[str, Any]:
    if not payload:
        raise deps.CliError(f"Missing {label}: provide --payload-json or --payload-file")
    return payload


def parse_bool_flags(*values: Any, deps: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def generate_tiktok_request_id(*, deps: Any) -> str:
    # TikTok create APIs accept a numeric request_id even though the SDK types it as str.
    return f"{deps.time.time_ns() % 10**19:019d}"


def slugify_token(value: Any, fallback: str, *, deps: Any) -> str:
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


def normalize_product_price(value: Any, *, deps: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"${value:.2f}"
    text = str(value).strip()
    return text or None


def derive_product_script(product: dict[str, Any], *, deps: Any) -> str:
    name = str(product.get("name") or "this product").strip()
    price = deps.normalize_product_price(product.get("price"))
    url_type = str(product.get("url_type") or "")

    if url_type in {"appstore", "googleplay"}:
        return f"Discover {name}, built to help people get started quickly and enjoy a smoother mobile experience."
    if price:
        return f"Discover {name}, available now for {price}. Explore the product details and see why shoppers are choosing it."
    return f"Discover {name}, designed to stand out with practical everyday value. Explore the product details and learn more."


def derive_product_prompt(product: dict[str, Any], *, deps: Any) -> str:
    name = str(product.get("name") or "this product").strip()
    return f"Turn {name} into a short natural motion product showcase video with clean camera movement."


def infer_product_brand(product: dict[str, Any], *, deps: Any) -> str | None:
    brand = product.get("brand")
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    product_url = str(product.get("url") or "").strip()
    hostname = deps.urlparse(product_url).netloc.lower()
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


def derive_product_description(product: dict[str, Any], *, deps: Any) -> str:
    name = str(product.get("name") or "this product").strip()
    if "built in bra" in name.lower():
        return f"{name} combines a flattering silhouette with built-in support for workouts and everyday wear."
    if "tank" in name.lower():
        return f"{name} is designed for soft comfort, a polished fit, and easy everyday movement."
    return f"{name} is designed to balance comfort, style, and practical everyday use."


def derive_product_selling_points(product: dict[str, Any], *, deps: Any) -> list[str]:
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


def normalize_product_price_value(value: Any, *, deps: Any) -> float | None:
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
    deps: Any,
) -> dict[str, Any]:
    if "material_packages" in payload:
        raise deps.CliError(
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
    first_product_info = deps.first_dict(existing_product_info_list) if isinstance(existing_product_info_list, list) else None
    product_info = dict(first_product_info or {})
    source_language = getattr(args, "source_language", None) or product_info.get("source_language") or "en"
    target_language = getattr(args, "target_language", None) or product_video_info.get("target_language") or "en"
    product_name = getattr(args, "product_name", None) or product_info.get("product_name") or product.get("name")
    title = getattr(args, "title", None) or product_info.get("title") or product.get("name")
    description = getattr(args, "description", None) or product_info.get("description") or deps.derive_product_description(product)
    brand = getattr(args, "brand", None) or product_info.get("brand") or deps.infer_product_brand(product)
    price = getattr(args, "price", None)
    if price is None:
        price = product_info.get("price")
    if price is None:
        price = deps.normalize_product_price_value(product.get("price"))
    currency = getattr(args, "currency", None) or product_info.get("currency") or "USD"
    selling_points = deps.non_empty_list(getattr(args, "selling_points", None))
    if not selling_points:
        selling_points = [str(value).strip() for value in (product_info.get("selling_points") or []) if str(value).strip()]
    if not selling_points:
        selling_points = deps.derive_product_selling_points(product)

    if not product_name:
        raise deps.CliError(f"{video_type} create requires product_name or --product-url")
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
        raise deps.CliError(f"{video_type} create requires video_generation_count between 1 and 5")
    if video_type == "TRYON" and not 1 <= video_generation_count <= 2:
        raise deps.CliError("TRYON create requires video_generation_count between 1 and 2")
    product_video_info["video_generation_count"] = video_generation_count
    product_video_info["target_language"] = str(target_language)
    voice_id = getattr(args, "voice_id", None) or product_video_info.get("voice_id")
    if voice_id:
        product_video_info["voice_id"] = str(voice_id)
    video_duration = getattr(args, "video_duration", None) or product_video_info.get("video_duration")
    if video_duration:
        product_video_info["video_duration"] = str(video_duration)
    subtitle_enabled = deps.parse_bool_flags(getattr(args, "subtitle_enabled", None), product_video_info.get("subtitle_enabled"))
    if subtitle_enabled is not None:
        product_video_info["subtitle_enabled"] = bool(subtitle_enabled)

    if not image_url_list and not video_id_list:
        raise deps.CliError(f"{video_type} create requires at least one of input_image_list or input_video_list")
    if video_type in {"AVATAR_PRODUCT", "VOICEOVER"} and image_url_list and not 3 <= len(image_url_list) <= 30:
        raise deps.CliError(f"{video_type} create requires input_image_list.image_url_list size between 3 and 30")
    if video_type == "TRYON" and not image_url_list:
        raise deps.CliError("TRYON create requires input_image_list.image_url_list")
    if video_type == "TRYON" and image_url_list and not 1 <= len(image_url_list) <= 20:
        raise deps.CliError("TRYON create requires input_image_list.image_url_list size between 1 and 20")
    if video_type == "TRYON" and video_id_list:
        raise deps.CliError("TRYON create does not support input_video_list.video_id_list")
    if video_id_list and not 1 <= len(video_id_list) <= 20:
        raise deps.CliError(f"{video_type} create requires input_video_list.video_id_list size between 1 and 20")

    if video_type == "AVATAR_PRODUCT":
        avatar_info = product_video_info.get("avatar_info")
        if not isinstance(avatar_info, dict):
            avatar_info = {}
        avatar_id = getattr(args, "avatar_id", None) or avatar_info.get("avatar_id")
        if avatar_id:
            product_video_info["avatar_info"] = {"avatar_id": str(avatar_id)}
    payload["product_video_info"] = product_video_info
    return payload


def apply_product_context_to_aigc_payload(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    context: dict[str, Any],
    mode: str,
    video_type: str | None = None,
    deps: Any,
) -> dict[str, Any]:
    product = context["product"]
    image_ids = context["image_ids"]
    product_url = str(product.get("url") or getattr(args, "product_url"))
    default_script = deps.derive_product_script(product)
    default_prompt = deps.derive_product_prompt(product)

    if mode == "video" and video_type in {"VOICEOVER", "AVATAR_PRODUCT", "TRYON"}:
        return deps.build_product_video_info_payload(payload, args, context=context, video_type=video_type)

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
        primary["package_id"] = getattr(args, "package_id", None) or f"{deps.slugify_token(product.get('name'), 'product')}-{deps.uuid.uuid4().hex[:8]}"
    if mode in {"image-animation", "video", "digital-avatar"} and not primary.get("video_name"):
        prefix = {"image-animation": "image-animation", "video": "aigc-video", "digital-avatar": "digital-avatar"}[mode]
        primary["video_name"] = getattr(args, "video_name", None) or f"{prefix}-{deps.slugify_token(product.get('name'), 'demo')}"

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
        raise deps.CliError("Digital avatar product bootstrap requires --avatar-id or payload material_packages[].avatar_id")
    return payload


def build_tiktok_image_animation_payload(
    args: argparse.Namespace,
    *,
    client: TikTokClient,
    deps: Any,
) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args) or {}
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", deps.generate_tiktok_request_id())

    if "material_packages" in payload:
        raise deps.CliError(
            "Image animation create no longer accepts material_packages. "
            "Use top-level image_url, background_prompt, animation_prompt, video_generation_count, and provider_model."
        )

    context = deps.fetch_product_context(
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
        payload["video_name"] = getattr(args, "video_name", None) or f"image-animation-{deps.slugify_token(product.get('name'), 'demo')}"
    deps.assign_if_present(payload, "image_url", getattr(args, "image_url", None))
    deps.assign_if_present(payload, "video_name", getattr(args, "video_name", None))
    change_background = deps.parse_bool_flags(getattr(args, "change_background", None), payload.get("change_background"))
    if change_background is None and getattr(args, "background_prompt", None):
        change_background = True
    if change_background is not None:
        payload["change_background"] = bool(change_background)
    deps.assign_if_present(payload, "background_prompt", getattr(args, "background_prompt", None))
    deps.assign_if_present(
        payload,
        "animation_prompt",
        getattr(args, "animation_prompt", None) or getattr(args, "prompt", None) or payload.get("prompt"),
    )
    deps.assign_if_present(
        payload,
        "provider_model",
        getattr(args, "provider_model", None) or payload.get("provider_model"),
    )
    payload.pop("prompt", None)

    video_generation_count = getattr(args, "video_generation_count", None) or payload.get("video_generation_count")
    if video_generation_count is not None:
        video_generation_count = int(video_generation_count)
        if not 1 <= video_generation_count <= 5:
            raise deps.CliError("Image animation create requires video_generation_count between 1 and 5")
        payload["video_generation_count"] = video_generation_count

    if payload.get("background_prompt") and not payload.get("change_background"):
        raise deps.CliError("background_prompt is only valid when change_background is enabled")

    if not payload.get("image_url"):
        raise deps.CliError("Image animation create requires --image-url, --product-url, or payload image_url")
    return payload


def build_tiktok_aigc_create_payload(
    args: argparse.Namespace,
    *,
    label: str,
    client: TikTokClient,
    mode: str,
    video_type: str | None = None,
    deps: Any,
) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    if not payload and not getattr(args, "product_url", None):
        raise deps.CliError(f"Missing {label}: provide --payload-json, --payload-file, or --product-url")

    payload = payload or {}
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", deps.generate_tiktok_request_id())
    resolved_video_type = video_type
    if mode == "video" and not resolved_video_type:
        payload_video_type = payload.get("aigc_video_type")
        if payload_video_type is not None:
            resolved_video_type = str(payload_video_type).strip() or None
    if mode == "video" and "material_packages" in payload:
        target_type = getattr(args, "aigc_video_type", None) or resolved_video_type or "AIGC video"
        raise deps.CliError(
            f"{target_type} create no longer accepts material_packages. "
            "Use product_video_info with product_info_list, input_video_list, and input_image_list."
        )
    upload_images = mode == "image-animation" or (mode == "video" and resolved_video_type == "TRYON")
    context = deps.fetch_product_context(
        args,
        client=client,
        advertiser_id=advertiser_id,
        upload_images=upload_images,
    )
    if context:
        payload = deps.apply_product_context_to_aigc_payload(
            payload,
            args,
            context=context,
            mode=mode,
            video_type=resolved_video_type,
        )
    return payload


def build_tiktok_aigc_voice_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    voice_ids = deps.non_empty_list(getattr(args, "voice_ids", None))
    if voice_ids:
        filtering["voice_ids"] = voice_ids
    deps.assign_if_present(filtering, "language", getattr(args, "language", None))
    deps.assign_if_present(filtering, "gender", getattr(args, "gender", None))
    deps.assign_if_present(filtering, "speaker_type", getattr(args, "speaker_type", None))
    deps.assign_if_present(filtering, "tag_type", getattr(args, "tag_type", None))
    tag_names = deps.non_empty_list(getattr(args, "tag_names", None))
    if tag_names:
        filtering["tag_names"] = tag_names
    return filtering or None


def build_tiktok_aigc_task_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    deps.assign_if_present(filtering, "status", getattr(args, "status", None))
    return filtering or None


def build_tiktok_aigc_video_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    deps.assign_if_present(filtering, "status", getattr(args, "status", None))
    return filtering or None


def build_tiktok_digital_avatar_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    avatar_ids = deps.non_empty_list(getattr(args, "avatar_ids", None))
    if avatar_ids:
        filtering["avatar_ids"] = avatar_ids
    deps.assign_if_present(filtering, "identity", getattr(args, "identity", None))
    deps.assign_if_present(filtering, "keyword", getattr(args, "keyword", None))
    tag_groups = deps.parse_json_arg(getattr(args, "tag_groups_json", None), "tag_groups", (dict, list))
    tag_type = getattr(args, "tag_type", None)
    tags = deps.non_empty_list(getattr(args, "tags", None))
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


def build_tiktok_digital_avatar_video_filtering(
    args: argparse.Namespace,
    *,
    deps: Any,
) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    deps.assign_if_present(filtering, "status", getattr(args, "status", None))
    deps.assign_if_present(filtering, "avatar_id", getattr(args, "avatar_id", None))
    deps.assign_if_present(filtering, "start_date", getattr(args, "start_date", None))
    deps.assign_if_present(filtering, "end_date", getattr(args, "end_date", None))
    return filtering or None


def avatar_identity_from_item(item: dict[str, Any], *, deps: Any) -> str | None:
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
    deps: Any,
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
    except deps.CliError:
        return
    avatar = deps.first_dict(deps.extract_response_list(response, "list")) or {}
    identity = deps.avatar_identity_from_item(avatar)
    if identity and identity != "real":
        raise deps.CliError(
            f"AVATAR_PRODUCT only supports real avatars per TikTok docs; avatar {avatar_id} has identity={identity!r}. "
            "Use a real avatar or omit --avatar-id to let TikTok auto-select one."
        )


def validate_digital_avatar_create_payload(payload: dict[str, Any], *, deps: Any) -> None:
    packages = payload.get("material_packages")
    if not isinstance(packages, list):
        raise deps.CliError("Digital avatar create requires material_packages")
    dict_packages = [item for item in packages if isinstance(item, dict)]
    if not dict_packages:
        raise deps.CliError("Digital avatar create requires at least one material_packages item")
    if len(dict_packages) > 5:
        raise deps.CliError("Digital avatar create supports at most 5 material_packages items")

    for index, package in enumerate(dict_packages, start=1):
        script = str(package.get("script") or "").strip()
        if not script:
            raise deps.CliError(f"Digital avatar create requires material_packages[{index - 1}].script")
        if len(script) > 2000:
            raise deps.CliError(f"Digital avatar create requires material_packages[{index - 1}].script <= 2000 chars")

        video_name = str(package.get("video_name") or "").strip()
        if video_name and len(video_name) > 50:
            raise deps.CliError(f"Digital avatar create requires material_packages[{index - 1}].video_name <= 50 chars")

        voice_volume = package.get("voice_volume")
        if voice_volume is not None:
            try:
                numeric_volume = float(voice_volume)
            except (TypeError, ValueError) as exc:
                raise deps.CliError(
                    f"Digital avatar create requires material_packages[{index - 1}].voice_volume to be numeric"
                ) from exc
            if not 0 <= numeric_volume <= 10:
                raise deps.CliError(f"Digital avatar create requires material_packages[{index - 1}].voice_volume between 0 and 10")

        voice_speed = str(package.get("voice_speed") or "").strip()
        if voice_speed and voice_speed not in deps.TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES:
            raise deps.CliError(
                "Digital avatar create requires material_packages"
                f"[{index - 1}].voice_speed in {deps.TIKTOK_DIGITAL_AVATAR_VOICE_SPEED_CHOICES}"
            )


def build_campaign_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    campaign_ids = deps.non_empty_list(getattr(args, "campaign_ids", None))
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    deps.assign_if_present(filtering, "campaign_name", getattr(args, "name", None))
    deps.assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    deps.assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


def build_gmv_max_campaign_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    filtering = deps.load_payload(args, label="filtering")
    promotion_types = deps.non_empty_list(getattr(args, "gmv_max_promotion_types", None))
    if promotion_types:
        filtering["gmv_max_promotion_types"] = promotion_types
    else:
        filtering.setdefault("gmv_max_promotion_types", ["PRODUCT_GMV_MAX"])
    campaign_ids = deps.non_empty_list(getattr(args, "campaign_ids", None))
    store_ids = deps.non_empty_list(getattr(args, "store_ids", None))
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    if store_ids:
        filtering["store_ids"] = store_ids
    deps.assign_if_present(filtering, "campaign_name", getattr(args, "name", None))
    deps.assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    deps.assign_if_present(filtering, "creation_filter_start_time", getattr(args, "creation_filter_start_time", None))
    deps.assign_if_present(filtering, "creation_filter_end_time", getattr(args, "creation_filter_end_time", None))
    return filtering


def build_campaign_create_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    deps.assign_if_present(payload, "campaign_name", args.name)
    deps.assign_if_present(payload, "objective_type", args.objective_type)
    deps.assign_if_present(payload, "budget", args.budget)
    deps.assign_if_present(payload, "budget_mode", args.budget_mode)
    deps.assign_if_present(payload, "operation_status", args.operation_status)
    deps.assign_if_present(payload, "campaign_type", args.campaign_type)
    deps.assign_if_present(payload, "sales_destination", args.sales_destination)
    deps.assign_if_present(payload, "optimization_goal", args.optimization_goal)
    deps.assign_if_present(payload, "app_id", getattr(args, "app_id", None))
    deps.assign_if_present(payload, "app_promotion_type", getattr(args, "app_promotion_type", None))
    deps.assign_if_present(payload, "bid_align_type", getattr(args, "bid_align_type", None))
    deps.assign_if_present(payload, "campaign_app_profile_page_state", getattr(args, "campaign_app_profile_page_state", None))
    deps.assign_if_present(payload, "page_id", getattr(args, "page_id", None))
    deps.assign_if_present(payload, "disable_skan_campaign", getattr(args, "disable_skan_campaign", None))
    deps.assign_if_present(payload, "budget_optimize_on", getattr(args, "budget_optimize_on", None))
    deps.assign_if_present(payload, "postback_window_mode", getattr(args, "postback_window_mode", None))
    deps.assign_if_present(payload, "is_advanced_dedicated_campaign", getattr(args, "is_advanced_dedicated_campaign", None))
    if args.special_industries:
        payload["special_industries"] = args.special_industries
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", deps.generate_tiktok_request_id())
    deps.validate_non_empty(payload.get("campaign_name"), "campaign_name")
    deps.validate_non_empty(payload.get("objective_type"), "objective_type")
    return payload


def build_campaign_update_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["campaign_id"] = deps.validate_non_empty(args.campaign_id, "campaign_id")
    deps.assign_if_present(payload, "campaign_name", args.name)
    deps.assign_if_present(payload, "budget", args.budget)
    deps.assign_if_present(payload, "po_number", args.po_number)
    if args.special_industries:
        payload["special_industries"] = args.special_industries
    return payload


def build_campaign_status_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["campaign_ids"] = deps.non_empty_list(args.campaign_ids)
    payload["operation_status"] = deps.validate_non_empty(args.operation_status, "operation_status")
    deps.assign_if_present(payload, "postback_window_mode", args.postback_window_mode)
    return payload


def build_adgroup_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    adgroup_ids = deps.non_empty_list(getattr(args, "adgroup_ids", None))
    campaign_ids = deps.non_empty_list(getattr(args, "campaign_ids", None))
    if adgroup_ids:
        filtering["adgroup_ids"] = adgroup_ids
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    deps.assign_if_present(filtering, "adgroup_name", getattr(args, "name", None))
    deps.assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    deps.assign_if_present(filtering, "optimization_goal", getattr(args, "optimization_goal", None))
    deps.assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


def build_adgroup_create_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload.setdefault("request_id", deps.generate_tiktok_request_id())
    deps.assign_if_present(payload, "campaign_id", args.campaign_id)
    deps.assign_if_present(payload, "adgroup_name", args.name)
    deps.assign_if_present(payload, "billing_event", args.billing_event)
    deps.assign_if_present(payload, "optimization_goal", args.optimization_goal)
    deps.assign_if_present(payload, "operation_status", args.operation_status)
    deps.assign_if_present(payload, "budget", args.budget)
    deps.assign_if_present(payload, "budget_mode", args.budget_mode)
    deps.assign_if_present(payload, "bid_price", args.bid_price)
    deps.assign_if_present(payload, "bid_type", args.bid_type)
    deps.assign_if_present(payload, "promotion_type", args.promotion_type)
    deps.assign_if_present(payload, "promotion_website_type", args.promotion_website_type)
    deps.assign_if_present(payload, "pixel_id", args.pixel_id)
    deps.assign_if_present(payload, "schedule_start_time", args.start_time)
    deps.assign_if_present(payload, "schedule_end_time", args.end_time)
    deps.assign_if_present(payload, "schedule_type", args.schedule_type)
    deps.assign_if_present(payload, "placement_type", args.placement_type)
    if args.placements:
        payload["placements"] = args.placements
    targeting_spec = deps.parse_json_arg(getattr(args, "targeting_spec_json", None), "targeting_spec", dict)
    if targeting_spec is not None:
        payload["targeting_spec"] = targeting_spec
    for key in ("campaign_id", "adgroup_name", "billing_event", "optimization_goal"):
        deps.validate_non_empty(payload.get(key), key)
    return payload


def build_adgroup_update_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = deps.validate_non_empty(args.adgroup_id, "adgroup_id")
    deps.assign_if_present(payload, "adgroup_name", args.name)
    deps.assign_if_present(payload, "budget", args.budget)
    deps.assign_if_present(payload, "bid_price", args.bid_price)
    deps.assign_if_present(payload, "roas_bid", args.roas_bid)
    deps.assign_if_present(payload, "conversion_bid_price", args.conversion_bid_price)
    deps.assign_if_present(payload, "comment_disabled", args.comment_disabled)
    deps.assign_if_present(payload, "share_disabled", args.share_disabled)
    deps.assign_if_present(payload, "pacing", args.pacing)
    deps.assign_if_present(payload, "schedule_start_time", args.start_time)
    deps.assign_if_present(payload, "schedule_end_time", args.end_time)
    deps.assign_if_present(payload, "schedule_type", args.schedule_type)
    deps.assign_if_present(payload, "targeting_optimization_mode", args.targeting_optimization_mode)
    targeting_spec = deps.parse_json_arg(getattr(args, "targeting_spec_json", None), "targeting_spec", dict)
    if targeting_spec is not None:
        payload["targeting_spec"] = targeting_spec
    return payload


def build_adgroup_status_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_ids"] = deps.non_empty_list(args.adgroup_ids)
    payload["operation_status"] = deps.validate_non_empty(args.operation_status, "operation_status")
    if getattr(args, "allow_partial_success", None) is not None:
        payload["allow_partial_success"] = args.allow_partial_success
    return payload


def build_ad_filtering(args: argparse.Namespace, *, smart_plus: bool, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    ad_ids = deps.non_empty_list(getattr(args, "ad_ids", None))
    adgroup_ids = deps.non_empty_list(getattr(args, "adgroup_ids", None))
    campaign_ids = deps.non_empty_list(getattr(args, "campaign_ids", None))
    if ad_ids:
        filtering["smart_plus_ad_ids" if smart_plus else "ad_ids"] = ad_ids
    if adgroup_ids:
        filtering["adgroup_ids"] = adgroup_ids
    if campaign_ids:
        filtering["campaign_ids"] = campaign_ids
    if not smart_plus:
        deps.assign_if_present(filtering, "ad_name", getattr(args, "name", None))
    deps.assign_if_present(filtering, "objective_type", getattr(args, "objective_type", None))
    deps.assign_if_present(filtering, "optimization_goal", getattr(args, "optimization_goal", None))
    deps.assign_if_present(filtering, "primary_status", getattr(args, "primary_status", None))
    return filtering or None


def build_normal_ad_update_base_creative(existing_ad: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    ad_id = deps.validate_non_empty(existing_ad.get("ad_id"), "ad_id")
    creative = {"ad_id": ad_id}
    for key in deps.NORMAL_AD_UPDATE_AUTOFILL_FIELDS:
        if key in existing_ad:
            creative[key] = existing_ad[key]
    return deps.compact_mapping(creative)


def merge_mapping(base: dict[str, Any], override: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if value is not None:
            merged[key] = value
    return merged


def build_normal_ad_update_arg_overrides(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if getattr(args, "name", None):
        overrides["ad_name"] = args.name
    if getattr(args, "operation_status", None):
        overrides["operation_status"] = args.operation_status
    return overrides


def build_normal_ad_creatives(
    args: argparse.Namespace,
    *,
    for_update: bool,
    deps: Any,
) -> list[dict[str, Any]] | None:
    creatives = deps.parse_json_arg(getattr(args, "creatives_json", None), "creatives", list)
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


def build_smart_plus_ad_json_fields(args: argparse.Namespace, payload: dict[str, Any], *, deps: Any) -> None:
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
        value = deps.parse_json_arg(getattr(args, arg_name, None), payload_key, expected_type)
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


def build_ad_create_payload(args: argparse.Namespace, *, smart_plus: bool, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = deps.validate_non_empty(args.adgroup_id, "adgroup_id")
    if smart_plus:
        deps.assign_if_present(payload, "ad_name", args.name)
        deps.assign_if_present(payload, "operation_status", args.operation_status)
        deps.build_smart_plus_ad_json_fields(args, payload)
        deps.validate_non_empty(payload.get("ad_name"), "ad_name")
    else:
        creatives = deps.build_normal_ad_creatives(args, for_update=False)
        if creatives is not None:
            payload["creatives"] = creatives
        creatives_payload = payload.get("creatives")
        if not isinstance(creatives_payload, list) or not creatives_payload:
            raise deps.CliError("Missing creatives: provide --creatives-json or payload.creatives")
    return payload


def build_ad_update_payload(
    args: argparse.Namespace,
    *,
    smart_plus: bool,
    existing_ad: dict[str, Any] | None = None,
    deps: Any,
) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    if smart_plus:
        payload["smart_plus_ad_id"] = deps.validate_non_empty(args.ad_id, "ad_id")
        deps.assign_if_present(payload, "ad_name", args.name)
        deps.build_smart_plus_ad_json_fields(args, payload)
    else:
        existing_adgroup_id = existing_ad.get("adgroup_id") if isinstance(existing_ad, dict) else None
        payload["adgroup_id"] = deps.validate_non_empty(
            payload.get("adgroup_id") or args.adgroup_id or existing_adgroup_id,
            "adgroup_id",
        )
        creatives = deps.build_normal_ad_creatives(args, for_update=True)
        if creatives is not None:
            payload["creatives"] = creatives
        creatives_payload = payload.get("creatives")
        if existing_ad is not None and args.ad_id:
            base_creative = deps.merge_mapping(
                deps.build_normal_ad_update_base_creative(existing_ad),
                deps.build_normal_ad_update_arg_overrides(args),
            )
            if not isinstance(creatives_payload, list) or not creatives_payload:
                creatives_payload = [base_creative]
            elif len(creatives_payload) == 1 and isinstance(creatives_payload[0], dict):
                creatives_payload = [deps.merge_mapping(base_creative, creatives_payload[0])]
            payload["creatives"] = creatives_payload
        if not isinstance(creatives_payload, list) or not creatives_payload:
            raise deps.CliError(
                "Missing creatives: provide --creatives-json or payload.creatives, "
                "or pass a single ad_id so the CLI can autofill from the current ad"
            )
        if args.patch_update is not None:
            payload["patch_update"] = args.patch_update
        has_ad_id = any(isinstance(item, dict) and item.get("ad_id") for item in creatives_payload)
        if not has_ad_id:
            raise deps.CliError("Normal ad update requires ad_id inside payload.creatives or a single --ad-id injection target")
    return payload


def build_ad_status_payload(args: argparse.Namespace, *, smart_plus: bool, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["operation_status"] = deps.validate_non_empty(args.operation_status, "operation_status")
    if smart_plus:
        payload["smart_plus_ad_ids"] = deps.non_empty_list(args.ad_ids)
    else:
        payload["ad_ids"] = deps.non_empty_list(args.ad_ids)
        aco_ad_ids = deps.non_empty_list(getattr(args, "aco_ad_ids", None))
        if aco_ad_ids:
            payload["aco_ad_ids"] = aco_ad_ids
    return payload


def build_video_search_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    video_ids = deps.non_empty_list(getattr(args, "video_ids", None))
    material_ids = deps.non_empty_list(getattr(args, "material_ids", None))
    if video_ids:
        filtering["video_ids"] = video_ids
    if material_ids:
        filtering["material_ids"] = material_ids
    deps.assign_if_present(filtering, "displayable", args.displayable)
    deps.assign_if_present(filtering, "width", args.width)
    deps.assign_if_present(filtering, "height", args.height)
    ratio = deps.parse_json_arg(getattr(args, "ratio_json", None), "ratio", list)
    if ratio is not None:
        filtering["ratio"] = ratio
    return filtering or None


def build_creative_portfolio_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    filtering = deps.load_payload(args, label="filtering")
    portfolio_ids = deps.non_empty_list(getattr(args, "creative_portfolio_ids", None))
    portfolio_types = deps.non_empty_list(getattr(args, "creative_portfolio_types", None))
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
    deps: Any,
) -> dict[str, Any] | None:
    json_value = getattr(args, json_attr, None)
    file_value = getattr(args, file_attr, None)
    if json_value and file_value:
        raise deps.CliError(f"Specify only one of --{json_attr.replace('_', '-')} or --{file_attr.replace('_', '-')}")
    if file_value:
        payload = deps.load_json_file(deps.Path(file_value))
        if not isinstance(payload, dict):
            raise deps.CliError(f"Invalid {label}: expected a JSON object in --{file_attr.replace('_', '-')}")
        return payload
    if json_value:
        payload = deps.parse_json_arg(json_value, label, dict)
        if payload is not None:
            return payload
    return None


def build_creative_portfolio_content_from_args(
    args: argparse.Namespace,
    *,
    deps: Any,
) -> list[dict[str, Any]] | None:
    content_json = getattr(args, "portfolio_content_json", None)
    content_file = getattr(args, "portfolio_content_file", None)
    if content_json and content_file:
        raise deps.CliError("Specify only one of --portfolio-content-json or --portfolio-content-file")
    if content_file:
        portfolio_content = deps.load_json_file(deps.Path(content_file))
        if not isinstance(portfolio_content, list):
            raise deps.CliError("Invalid portfolio_content: expected a JSON array in --portfolio-content-file")
        return [item for item in portfolio_content if isinstance(item, dict)]
    if content_json:
        portfolio_content = deps.parse_json_arg(content_json, "portfolio_content", list)
        if portfolio_content is not None:
            if not all(isinstance(item, dict) for item in portfolio_content):
                raise deps.CliError("Invalid portfolio_content: expected a JSON array of objects")
            return portfolio_content

    content = deps.compact_mapping(
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
    tags = deps.non_empty_list(getattr(args, "tags", None))
    if tags:
        content["tags"] = tags
    card_tags = deps.non_empty_list(getattr(args, "card_tags", None))
    if card_tags:
        content["card_tags"] = card_tags
    asset_ids = deps.non_empty_list(getattr(args, "asset_ids", None))
    if asset_ids:
        content["asset_ids"] = asset_ids
    sku_ids = deps.non_empty_list(getattr(args, "sku_ids", None))
    if sku_ids:
        content["sku_ids"] = sku_ids
    item_group_ids = deps.non_empty_list(getattr(args, "item_group_ids", None))
    if item_group_ids:
        content["item_group_ids"] = item_group_ids
    selling_points = deps.non_empty_list(getattr(args, "selling_points", None))
    if selling_points:
        content["selling_points"] = selling_points
    country_codes = deps.non_empty_list(getattr(args, "country_codes", None))
    if country_codes:
        content["country_code"] = country_codes
    layouts = deps.non_empty_list(getattr(args, "layouts", None))
    if layouts:
        content["layouts"] = layouts

    advanced_audio_info = deps.build_optional_object_from_json_args(
        args,
        json_attr="advanced_audio_info_json",
        file_attr="advanced_audio_info_file",
        label="advanced_audio_info",
    ) or {}
    advanced_gesture_icon = deps.build_optional_object_from_json_args(
        args,
        json_attr="advanced_gesture_icon_json",
        file_attr="advanced_gesture_icon_file",
        label="advanced_gesture_icon",
    ) or {}
    advanced_gesture_image = deps.build_optional_object_from_json_args(
        args,
        json_attr="advanced_gesture_image_json",
        file_attr="advanced_gesture_image_file",
        label="advanced_gesture_image",
    ) or {}
    advanced_image_info = deps.build_optional_object_from_json_args(
        args,
        json_attr="advanced_image_info_json",
        file_attr="advanced_image_info_file",
        label="advanced_image_info",
    ) or {}
    advanced_position = deps.build_optional_object_from_json_args(
        args,
        json_attr="advanced_position_json",
        file_attr="advanced_position_file",
        label="advanced_position",
    ) or {}
    badge_image_info = deps.build_optional_object_from_json_args(
        args,
        json_attr="badge_image_info_json",
        file_attr="badge_image_info_file",
        label="badge_image_info",
    ) or {}
    sticker_param = deps.build_optional_object_from_json_args(
        args,
        json_attr="sticker_param_json",
        file_attr="sticker_param_file",
        label="sticker_param",
    ) or {}
    slide_dimension = deps.build_optional_object_from_json_args(
        args,
        json_attr="slide_dimension_json",
        file_attr="slide_dimension_file",
        label="slide_dimension",
    ) or {}
    showcase_products = getattr(args, "showcase_products_json", None)
    showcase_products_file = getattr(args, "showcase_products_file", None)
    if showcase_products and showcase_products_file:
        raise deps.CliError("Specify only one of --showcase-products-json or --showcase-products-file")
    showcase_products_value: list[dict[str, Any]] | None = None
    if showcase_products_file:
        loaded = deps.load_json_file(deps.Path(showcase_products_file))
        if not isinstance(loaded, list):
            raise deps.CliError("Invalid showcase_products: expected a JSON array in --showcase-products-file")
        showcase_products_value = [item for item in loaded if isinstance(item, dict)]
    elif showcase_products:
        loaded = deps.parse_json_arg(showcase_products, "showcase_products", list)
        if loaded is not None:
            if not all(isinstance(item, dict) for item in loaded):
                raise deps.CliError("Invalid showcase_products: expected a JSON array of objects")
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


def build_creative_portfolio_create_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    deps.assign_if_present(payload, "creative_portfolio_type", getattr(args, "creative_portfolio_type", None))

    portfolio_content = deps.build_creative_portfolio_content_from_args(args)
    if portfolio_content is not None:
        payload["portfolio_content"] = portfolio_content
    context_info = deps.build_optional_object_from_json_args(
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
    deps.validate_non_empty(payload.get("creative_portfolio_type"), "creative_portfolio_type")
    if not isinstance(payload.get("portfolio_content"), list) or not payload.get("portfolio_content"):
        raise deps.CliError("Missing portfolio_content: provide nested content flags or --portfolio-content-json/--portfolio-content-file")
    return payload


def build_creative_asset_delete_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    image_ids = deps.non_empty_list(getattr(args, "image_ids", None))
    video_ids = deps.non_empty_list(getattr(args, "video_ids", None))
    if image_ids:
        payload["image_ids"] = image_ids
    if video_ids:
        payload["video_ids"] = video_ids
    if not payload.get("image_ids") and not payload.get("video_ids"):
        raise deps.CliError("Creative asset delete requires at least one image id or video id")
    return payload


def build_creative_asset_share_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    material_ids = deps.non_empty_list(getattr(args, "material_ids", None))
    shared_advertiser_ids = deps.non_empty_list(getattr(args, "shared_advertiser_ids", None))
    deps.assign_if_present(payload, "asset_type", getattr(args, "asset_type", None))
    if material_ids:
        payload["material_ids"] = material_ids
    if shared_advertiser_ids:
        payload["shared_advertiser_ids"] = shared_advertiser_ids
    if not payload.get("material_ids") or not payload.get("shared_advertiser_ids"):
        raise deps.CliError("Creative asset share requires material_ids and shared_advertiser_ids")
    return payload


def build_creative_shareable_link_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    payload = deps.load_payload(args)
    shared_assets_json = getattr(args, "shared_assets_json", None)
    shared_assets_file = getattr(args, "shared_assets_file", None)
    if shared_assets_json and shared_assets_file:
        raise deps.CliError("Specify only one of --shared-assets-json or --shared-assets-file")
    if shared_assets_file:
        shared_assets = deps.load_json_file(deps.Path(shared_assets_file))
        if not isinstance(shared_assets, list):
            raise deps.CliError("Invalid shared_assets: expected a JSON array in --shared-assets-file")
        payload["shared_assets"] = shared_assets
    elif shared_assets_json:
        shared_assets = deps.parse_json_arg(shared_assets_json, "shared_assets", list)
        if shared_assets is not None:
            payload["shared_assets"] = shared_assets
    deps.assign_if_present(payload, "sharer", getattr(args, "sharer", None))
    deps.validate_non_empty(payload.get("shared_assets"), "shared_assets")
    deps.validate_non_empty(payload.get("sharer"), "sharer")
    return payload


def build_creative_smart_text_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    advertiser_id = deps.validate_non_empty(args.advertiser_id, "advertiser_id")
    payload = deps.load_payload(args)
    payload["advertiser_id"] = advertiser_id
    payload["adgroup_id"] = deps.validate_non_empty(getattr(args, "adgroup_id", None), "adgroup_id")
    deps.assign_if_present(payload, "industry_id", getattr(args, "industry_id", None))
    keywords = deps.non_empty_list(getattr(args, "keywords", None))
    if keywords:
        payload["keywords"] = keywords
    deps.assign_if_present(payload, "language", getattr(args, "language", None))
    deps.assign_if_present(payload, "limit", getattr(args, "limit", None))
    deps.assign_if_present(payload, "param_type", getattr(args, "param_type", None))
    deps.validate_non_empty(payload.get("industry_id"), "industry_id")
    return payload


def looks_like_url(value: str, *, deps: Any) -> bool:
    parsed = deps.urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def build_validate_creative_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    payload = deps.load_payload(args)
    if payload:
        return payload

    landing_page_urls = deps.non_empty_list(getattr(args, "landing_page_urls", None))
    image_ids = deps.non_empty_list(getattr(args, "image_ids", None))
    image_web_uris = deps.non_empty_list(getattr(args, "image_web_uris", None))
    tracking_offline_event_set_ids = deps.non_empty_list(getattr(args, "tracking_offline_event_set_ids", None))

    if getattr(args, "smart_plus", False):
        creative_info = deps.compact_mapping(
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

        tracking_info = deps.compact_mapping(
            {
                "tracking_app_id": getattr(args, "tracking_app_id", None),
                "tracking_pixel_id": getattr(args, "tracking_pixel_id", None),
                "tracking_offline_event_set_ids": tracking_offline_event_set_ids,
            }
        )
        if tracking_info:
            payload["ad_configuration"] = {"tracking_info": tracking_info}
        return payload

    creative = deps.compact_mapping(
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
        raise deps.CliError("Missing payload: provide --payload-json/--payload-file or direct creative reference flags")
    return {"creatives": [creative]}


def build_integrated_report_filtering(args: argparse.Namespace, *, deps: Any) -> list[dict[str, Any]] | None:
    filtering_json = getattr(args, "filtering_json", None)
    if not filtering_json:
        return None
    return deps.parse_json_arg(filtering_json, "filtering", list)


def build_smartplus_material_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    payload = deps.load_payload(args, label="filtering")
    return payload or None


def build_gmv_max_report_filtering(
    args: argparse.Namespace,
    dimensions: list[str] | None = None,
    *,
    deps: Any,
) -> dict[str, Any] | None:
    payload = deps.load_payload(args, label="filtering")
    deps.assign_if_present(payload, "campaign_name", getattr(args, "campaign_name", None))
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
        values = deps.non_empty_list(getattr(args, arg_name, None))
        if values:
            payload[field_name] = values
    if dimensions and (
        payload.get("campaign_ids") or payload.get("item_group_ids") or payload.get("item_ids")
    ):
        payload.pop("gmv_max_promotion_types", None)
    deps.assign_if_present(payload, "search_word", getattr(args, "search_word", None))
    return payload or None


def resolve_gmv_max_report_dimensions(args: argparse.Namespace, *, deps: Any) -> list[str]:
    dimensions = deps.non_empty_list(getattr(args, "dimensions", None))
    if dimensions:
        return dimensions
    level = getattr(args, "level", None)
    if not level:
        raise deps.CliError("GMV Max reports require --dimension or --level")
    resolved = list(deps.TIKTOK_GMV_MAX_REPORT_LEVEL_DIMENSIONS[level])
    for dimension in deps.TIKTOK_GMV_MAX_REPORT_TIME_GRAIN_DIMENSIONS[getattr(args, "time_grain", "none")]:
        if dimension not in resolved:
            resolved.append(dimension)
    return resolved


def default_gmv_max_report_metrics(
    args: argparse.Namespace,
    dimensions: list[str],
    *,
    deps: Any,
) -> list[str]:
    level = getattr(args, "level", None)
    if level:
        return list(deps.TIKTOK_GMV_MAX_REPORT_LEVEL_METRICS[level])
    if "item_id" in dimensions:
        return list(deps.TIKTOK_GMV_MAX_CREATIVE_REPORT_METRICS)
    if "item_group_id" in dimensions:
        return list(deps.TIKTOK_GMV_MAX_PRODUCT_REPORT_METRICS)
    if "duration" in dimensions:
        return list(deps.TIKTOK_GMV_MAX_DURATION_REPORT_METRICS)
    if "campaign_id" in dimensions:
        return list(deps.TIKTOK_GMV_MAX_CAMPAIGN_REPORT_METRICS)
    return list(deps.TIKTOK_GMV_MAX_ACCOUNT_REPORT_METRICS)


def classify_gmv_max_item_scope(item_id: Any, *, deps: Any) -> str:
    return (
        deps.TIKTOK_GMV_MAX_ITEM_SCOPE_PRODUCT_CARD
        if str(item_id) == "-1"
        else deps.TIKTOK_GMV_MAX_ITEM_SCOPE_SPECIFIC_ITEM
    )


def annotate_gmv_max_item_scope(response: dict[str, Any], *, deps: Any) -> dict[str, Any]:
    rows = response.get("data", {}).get("list")
    if not isinstance(rows, list):
        return response
    for row in rows:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensions")
        if not isinstance(dimensions, dict) or "item_id" not in dimensions:
            continue
        dimensions.setdefault("item_scope", deps.classify_gmv_max_item_scope(dimensions.get("item_id")))
    return response


def build_gmv_max_custom_anchor_video_payload(
    args: argparse.Namespace,
    advertiser_id: str,
    *,
    deps: Any,
) -> dict[str, Any]:
    payload = deps.load_payload(args, label="GMV Max custom anchor video payload")
    payload["advertiser_id"] = advertiser_id
    deps.assign_if_present(payload, "store_id", getattr(args, "store_id", None))
    deps.assign_if_present(payload, "store_authorized_bc_id", getattr(args, "store_authorized_bc_id", None))
    payload.setdefault("creative_source", getattr(args, "creative_source", None) or "CUSTOMIZED")
    deps.assign_if_present(payload, "keyword", getattr(args, "keyword", None))
    deps.assign_if_present(payload, "campaign_id", getattr(args, "campaign_id", None))
    deps.assign_if_present(payload, "need_auth_code_video", getattr(args, "need_auth_code_video", None))
    deps.assign_if_present(payload, "sort_field", getattr(args, "sort_field", None))
    deps.assign_if_present(payload, "sort_type", getattr(args, "sort_type", None))
    spu_ids = deps.non_empty_list(getattr(args, "spu_ids", None))
    if spu_ids:
        payload["spu_id_list"] = spu_ids
    identity_list = deps.parse_json_arg(getattr(args, "identity_list_json", None), "identity_list", list)
    if identity_list:
        payload["identity_list"] = identity_list
    page = int(getattr(args, "page", 1) or 1)
    page_size = int(getattr(args, "page_size", 20) or 20)
    if page < 1:
        raise deps.CliError("page must be >= 1")
    if not 1 <= page_size <= 50:
        raise deps.CliError("page_size must be between 1 and 50 for GMV Max custom anchor videos")
    payload["page"] = page
    payload["page_size"] = page_size
    deps.validate_non_empty(payload.get("store_id"), "store_id")
    deps.validate_non_empty(payload.get("store_authorized_bc_id"), "store_authorized_bc_id")
    deps.validate_non_empty(payload.get("creative_source"), "creative_source")
    return payload


def build_gmv_max_video_list_payload(args: argparse.Namespace, *, deps: Any) -> dict[str, Any]:
    payload = deps.load_payload(args, label="GMV Max video list parameters")
    deps.assign_if_present(payload, "store_id", getattr(args, "store_id", None))
    deps.assign_if_present(payload, "store_authorized_bc_id", getattr(args, "store_authorized_bc_id", None))
    spu_ids = deps.non_empty_list(getattr(args, "spu_ids", None))
    if spu_ids:
        payload["spu_id_list"] = spu_ids
    deps.assign_if_present(payload, "custom_posts_eligible", getattr(args, "custom_posts_eligible", None))
    deps.assign_if_present(payload, "sort_field", getattr(args, "sort_field", None))
    deps.assign_if_present(payload, "sort_type", getattr(args, "sort_type", None))
    deps.assign_if_present(payload, "keyword", getattr(args, "keyword", None))
    need_auth_code_video = deps.parse_bool_flags(
        getattr(args, "need_auth_code_video", None),
        False if getattr(args, "no_need_auth_code_video", False) else None,
    )
    deps.assign_if_present(payload, "need_auth_code_video", need_auth_code_video)
    identity_list = deps.parse_json_arg(getattr(args, "identity_list_json", None), "identity_list", list)
    if identity_list:
        payload["identity_list"] = identity_list
    page = int(getattr(args, "page", 1) or 1)
    page_size = int(getattr(args, "page_size", 10) or 10)
    if page < 1:
        raise deps.CliError("page must be >= 1")
    if not 1 <= page_size <= 50:
        raise deps.CliError("page_size must be between 1 and 50 for GMV Max videos")
    payload["page"] = page
    payload["page_size"] = page_size
    deps.validate_non_empty(payload.get("store_id"), "store_id")
    deps.validate_non_empty(payload.get("store_authorized_bc_id"), "store_authorized_bc_id")
    return payload


def build_store_product_filtering(args: argparse.Namespace, *, deps: Any) -> dict[str, Any] | None:
    payload = deps.load_payload(args, label="store product filtering")
    item_group_ids = deps.validate_max_list_size(deps.non_empty_list(getattr(args, "item_group_ids", None)), "item_group_ids", max_size=10)
    if item_group_ids:
        payload["item_group_ids"] = item_group_ids
    deps.assign_if_present(payload, "product_name", getattr(args, "product_name", None))
    deps.assign_if_present(payload, "ad_creation_eligible", getattr(args, "ad_creation_eligible", None))
    return payload or None


def normalize_store_product_row(product: dict[str, Any], *, deps: Any) -> dict[str, Any]:
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
