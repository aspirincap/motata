from __future__ import annotations

import json
from statistics import median
from typing import Any
from urllib.parse import urlparse

from motata_cli.metrics.core import build_core_metric_coverage
from motata_cli.metrics.probe import sanitize_error_message
from motata_cli.tiktok.landing_pages import (
    _chunked,
    _dimension,
    _extract_collection,
    _fnum,
    _list_ads_by_ids,
    _list_upgraded_smart_plus_ads,
    _metric,
    default_date_range,
    extract_landing_url,
    extract_url_evidence,
)
from motata_cli.tiktok.metrics import build_tiktok_metric_probe


RETENTION_METRICS = [
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "conversion",
    "cost_per_conversion",
    "result",
    "cost_per_result",
    "video_play_actions",
    "video_watched_2s",
    "video_watched_6s",
    "engaged_view",
    "engaged_view_15s",
    "video_views_p25",
    "video_views_p50",
    "video_views_p75",
    "video_views_p100",
    "average_video_play",
    "average_video_play_per_user",
    "total_purchase_value",
    "total_subscribe_value",
    "custom_app_events_value",
    "skan_total_purchase_value",
    "complete_payment",
    "complete_payment_roas",
    "value_per_complete_payment",
]

RETENTION_ATTRIBUTE_METRICS = [
    "ad_name",
    "ad_id_v2",
    "campaign_id",
    "campaign_name",
    "campaign_automation_type",
    "adgroup_id",
    "adgroup_name",
    "objective_type",
    "promotion_type",
    "ad_url",
    "adgroup_download_url",
    "placement_type",
    "currency",
]

IMAGE_SOURCE_MARKERS = (
    "image",
    "thumbnail",
    "thumb",
    "cover",
    "poster",
    "preview_image",
    "web_uri",
)
VIDEO_SOURCE_MARKERS = ("video", "play_url", "preview_url", "media")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")
MEDIA_INFO_BATCH_SIZE = 50
FILTERING_BATCH_SIZE = 20
TT_VIDEO_PAGE_SIZE = 50


def _target_ad_filtering(ad_ids: list[str]) -> list[dict[str, Any]] | None:
    ids = [str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()]
    if not ids:
        return None
    return [
        {
            "field_name": "ad_ids",
            "filter_type": "IN",
            "filter_value": json.dumps(ids),
        }
    ]


def _filter_rows_by_ad_ids(rows: list[dict[str, Any]], ad_ids: list[str]) -> list[dict[str, Any]]:
    ids = {str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()}
    if not ids:
        return rows
    return [
        row
        for row in rows
        if str(_row_id(row, "ad_id") or _row_id(row, "stat_ad_id") or "").strip() in ids
    ]


def _safe_rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _safe_cost(spend: float, denominator: float) -> float:
    return round(spend / denominator, 4) if denominator else 0.0


def _first_value(row: dict[str, Any], metrics: tuple[str, ...]) -> float:
    for metric in metrics:
        value = _fnum(_metric(row, metric))
        if value:
            return value
    return 0.0


def _revenue(row: dict[str, Any], spend: float) -> tuple[float, str | None]:
    for metric in ("total_purchase_value", "total_subscribe_value", "custom_app_events_value", "skan_total_purchase_value"):
        value = _fnum(_metric(row, metric))
        if value:
            return value, metric
    roas = _fnum(_metric(row, "complete_payment_roas"))
    if roas and spend:
        return spend * roas, "complete_payment_roas"
    complete_payment = _fnum(_metric(row, "complete_payment"))
    value_per_payment = _fnum(_metric(row, "value_per_complete_payment"))
    if complete_payment and value_per_payment:
        return complete_payment * value_per_payment, "value_per_complete_payment"
    return 0.0, None


def _row_id(row: dict[str, Any], key: str) -> str | None:
    value = _dimension(row, key) or _metric(row, key)
    if value in (None, "", "-"):
        return None
    return str(value)


def _looks_like_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _url_media_type(url: str, source: str) -> str:
    lowered_source = source.lower()
    path = urlparse(url).path.lower()
    if any(marker in lowered_source for marker in IMAGE_SOURCE_MARKERS) or path.endswith(IMAGE_EXTENSIONS):
        return "image"
    if any(marker in lowered_source for marker in VIDEO_SOURCE_MARKERS) or path.endswith(VIDEO_EXTENSIONS):
        return "video"
    return "asset"


def _first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, "", "-"):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _collect_ids(value: Any, keys: tuple[str, ...], found: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered_key = str(key).lower()
            if lowered_key in keys:
                if isinstance(child, list):
                    found.extend(str(item).strip() for item in child if str(item or "").strip())
                elif str(child or "").strip():
                    found.append(str(child).strip())
            else:
                _collect_ids(child, keys, found)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, keys, found)


def _media_ids(payload: dict[str, Any], *, media_type: str) -> list[str]:
    keys = ("video_id", "video_ids", "avatar_video_id") if media_type == "video" else ("image_id", "image_ids", "thumbnail_id")
    found: list[str] = []
    _collect_ids(payload, keys, found)
    return list(dict.fromkeys(found))


def _media_info_by_id(
    client: Any,
    advertiser_id: str,
    ids: list[str],
    *,
    media_type: str,
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    method_name = "get_video_info" if media_type == "video" else "get_image_info"
    if not hasattr(client, method_name):
        return {}
    method = getattr(client, method_name)
    result: dict[str, dict[str, Any]] = {}
    for batch in _chunked(list(dict.fromkeys(ids)), MEDIA_INFO_BATCH_SIZE):
        try:
            response = method(advertiser_id, batch)
        except Exception:
            continue
        for item in _extract_collection(response, "list", "videos", "images", "items"):
            identity_keys = ("video_id", "avatar_video_id") if media_type == "video" else ("image_id", "thumbnail_id")
            for key in identity_keys:
                media_id = str(item.get(key) or "").strip()
                if media_id and media_id in batch:
                    result[media_id] = item
    return result


def _spark_item_id(detail: dict[str, Any]) -> str | None:
    return _first_non_empty_text(
        detail.get("tiktok_item_id"),
        detail.get("spark_ad_post_id"),
        detail.get("item_id"),
        detail.get("tt_item_id"),
    )


def _tt_video_payload_by_item_id(client: Any, advertiser_id: str, item_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not item_ids or not hasattr(client, "list_tt_videos"):
        return {}
    result: dict[str, dict[str, Any]] = {}
    target_ids = list(dict.fromkeys(str(item_id).strip() for item_id in item_ids if str(item_id).strip()))
    target_id_set = set(target_ids)

    if len(target_ids) > 1:
        for page in range(1, 10000):
            try:
                response = client.list_tt_videos(
                    advertiser_id,
                    item_types=["VIDEO", "CAROUSEL"],
                    page=page,
                    page_size=TT_VIDEO_PAGE_SIZE,
                )
            except Exception:
                break

            page_items = _extract_collection(response, "list", "items")
            for item in page_items:
                info = item.get("item_info") if isinstance(item.get("item_info"), dict) else {}
                item_id = str(info.get("item_id") or item.get("item_id") or "").strip()
                if item_id in target_id_set:
                    result[item_id] = item
            if target_id_set.issubset(result):
                return result

            data = response.get("data") if isinstance(response, dict) else {}
            page_info = data.get("page_info") if isinstance(data, dict) and isinstance(data.get("page_info"), dict) else {}
            total_page = int(_fnum(page_info.get("total_page") or page_info.get("total_pages")))
            if total_page and page >= total_page:
                break
            if not total_page and len(page_items) < TT_VIDEO_PAGE_SIZE:
                break

    for item_id in target_ids:
        if item_id in result:
            continue
        try:
            response = client.list_tt_videos(
                advertiser_id,
                keyword=item_id,
                item_types=["VIDEO", "CAROUSEL"],
                page=1,
                page_size=1,
            )
        except Exception:
            continue
        for item in _extract_collection(response, "list", "items"):
            info = item.get("item_info") if isinstance(item.get("item_info"), dict) else {}
            if str(info.get("item_id") or item.get("item_id") or "").strip() == item_id:
                result[item_id] = item
                break
    return result


def _tt_video_assets(item: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    video_info = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
    poster_url = str(video_info.get("poster_url") or "").strip()
    preview_url = str(video_info.get("preview_url") or "").strip()
    if _looks_like_url(poster_url):
        assets.append({"url": poster_url, "source": "tt_video.poster_url", "media_type": "image"})
    if _looks_like_url(preview_url):
        assets.append({"url": preview_url, "source": "tt_video.preview_url", "media_type": "video"})

    item_info = item.get("item_info") if isinstance(item.get("item_info"), dict) else {}
    carousel_info = item_info.get("carousel_info") if isinstance(item_info.get("carousel_info"), dict) else {}
    for image in carousel_info.get("image_info") or []:
        if isinstance(image, dict):
            image_url = str(image.get("image_url") or "").strip()
            if _looks_like_url(image_url):
                assets.append({"url": image_url, "source": "tt_video.carousel_info.image_url", "media_type": "image"})

    return assets


def _creative_asset_items(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if payload.get("_tt_video_list_item"):
            items.extend(_tt_video_assets(payload))
        for item in extract_url_evidence(payload):
            if item.get("kind") == "creative_asset" and _looks_like_url(str(item.get("raw_url") or item.get("url") or "")):
                raw_url = str(item.get("raw_url") or item.get("url"))
                items.append(
                    {
                        "url": raw_url,
                        "source": str(item.get("source") or ""),
                        "media_type": _url_media_type(raw_url, str(item.get("source") or "")),
                    }
                )

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        deduped.setdefault((item["url"], item["source"]), item)
    return list(deduped.values())


def _preview_info(
    *,
    row: dict[str, Any],
    raw_row: dict[str, Any],
    detail: dict[str, Any],
    video_infos: dict[str, dict[str, Any]],
    image_infos: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = [raw_row]
    if detail:
        payloads.append(detail)
    for video_id in _media_ids(detail, media_type="video"):
        if video_id in video_infos:
            payloads.append(video_infos[video_id])
    for image_id in _media_ids(detail, media_type="image"):
        if image_id in image_infos:
            payloads.append(image_infos[image_id])

    assets = _creative_asset_items(payloads)
    image_asset = next((item for item in assets if item["media_type"] == "image"), None)
    video_asset = next((item for item in assets if item["media_type"] == "video"), None)
    preview_asset = next((item for item in assets if "preview" in item["source"].lower()), None)
    primary_asset = preview_asset or video_asset or image_asset or next(iter(assets), None)
    preview_url = primary_asset["url"] if primary_asset else None
    preview_image_url = image_asset["url"] if image_asset else None
    landing_url = row.get("landing_url")
    landing_url_source = row.get("landing_url_source")
    for payload in (detail, raw_row):
        if payload:
            landing_url, landing_url_source = extract_landing_url(payload)
            if landing_url:
                break

    return {
        "creative_id": _first_non_empty_text(
            detail.get("creative_id"),
            detail.get("ad_id_v2"),
            detail.get("smart_plus_ad_id"),
            detail.get("upgraded_smart_plus_ad_id"),
            row.get("ad_id"),
        ),
        "ad_id_v2": _first_non_empty_text(
            detail.get("ad_id_v2"),
            detail.get("smart_plus_ad_id"),
            detail.get("upgraded_smart_plus_ad_id"),
            row.get("ad_id_v2"),
            row.get("smart_plus_ad_id"),
            row.get("upgraded_smart_plus_ad_id"),
        ),
        "preview_image_url": preview_image_url,
        "preview_url": preview_url,
        "preview_url_source": primary_asset["source"] if primary_asset else None,
        "preview_media_type": primary_asset["media_type"] if primary_asset else None,
        "creative_asset_urls": assets,
        "preview_status": "available" if preview_url or preview_image_url else "unavailable",
        "landing_url": landing_url,
        "landing_url_source": landing_url_source,
    }


def _smart_plus_candidate_ids(row: dict[str, Any], detail: dict[str, Any]) -> list[str]:
    asset_ids = [
        detail.get("smart_plus_ad_id"),
        detail.get("ad_id_v2"),
        detail.get("upgraded_smart_plus_ad_id"),
        row.get("ad_id_v2"),
    ]
    deduped_asset_ids = [str(value).strip() for value in dict.fromkeys(asset_ids) if str(value or "").strip()]
    if deduped_asset_ids:
        return deduped_asset_ids
    ad_id = str(row.get("ad_id") or "").strip()
    return [ad_id] if ad_id else []


def _should_try_smart_plus_detail(row: dict[str, Any], detail: dict[str, Any]) -> bool:
    automation_type = str(detail.get("campaign_automation_type") or row.get("campaign_automation_type") or "").upper()
    return bool(
        automation_type in {"SMART_PLUS", "UPGRADED_SMART_PLUS", "UPGRADED_SMART_PLUS_CREATIVE"}
        or detail.get("smart_plus_ad_id")
        or detail.get("ad_id_v2")
        or row.get("ad_id_v2")
    )


def _matching_smart_plus_detail(detail: dict[str, Any], smart_plus_details: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    campaign_id = str(detail.get("campaign_id") or "").strip()
    adgroup_id = str(detail.get("adgroup_id") or "").strip()
    for candidate in smart_plus_details.values():
        if adgroup_id and str(candidate.get("adgroup_id") or "").strip() == adgroup_id:
            return candidate
        if campaign_id and str(candidate.get("campaign_id") or "").strip() == campaign_id:
            return candidate
    return None


def _enrich_creative_previews(
    client: Any,
    *,
    advertiser_id: str,
    rows: list[dict[str, Any]],
    raw_rows_by_ad_id: dict[str, dict[str, Any]],
    ad_details_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not rows:
        return {"enabled": True, "detail_count": 0, "available_count": 0}
    ad_ids = [str(row.get("ad_id") or "").strip() for row in rows if str(row.get("ad_id") or "").strip()]
    details = {}
    for ad_id in ad_ids:
        cached = ad_details_by_id.get(ad_id) if ad_details_by_id is not None else None
        if cached:
            details[ad_id] = cached
    missing_ad_ids = [ad_id for ad_id in ad_ids if ad_id not in details]
    fetched_details = (
        _list_ads_by_ids(client, advertiser_id, missing_ad_ids, smart_plus=False, cache=ad_details_by_id)
        if missing_ad_ids and hasattr(client, "list_ads")
        else {}
    )
    details.update(fetched_details)

    smart_plus_candidate_ids: list[str] = []
    for row in rows:
        detail = details.get(str(row.get("ad_id") or "")) or {}
        if _should_try_smart_plus_detail(row, detail):
            smart_plus_candidate_ids.extend(_smart_plus_candidate_ids(row, detail))
    smart_plus_details = (
        _list_upgraded_smart_plus_ads(
            client,
            advertiser_id,
            smart_plus_candidate_ids,
            cache=ad_details_by_id,
        )
        if smart_plus_candidate_ids
        else {}
    )

    merged_details: dict[str, dict[str, Any]] = {}
    for row in rows:
        ad_id = str(row.get("ad_id") or "")
        detail = details.get(ad_id) or {}
        for candidate_id in _smart_plus_candidate_ids(row, detail):
            if candidate_id in smart_plus_details:
                detail = {**detail, **smart_plus_details[candidate_id], "_detail_source": "smart_plus_ad_get"}
                break
        else:
            matched_detail = _matching_smart_plus_detail(detail, smart_plus_details)
            if matched_detail:
                detail = {**detail, **matched_detail, "_detail_source": "smart_plus_ad_get"}
        merged_details[ad_id] = detail

    video_ids: list[str] = []
    image_ids: list[str] = []
    for detail in merged_details.values():
        video_ids.extend(_media_ids(detail, media_type="video"))
        image_ids.extend(_media_ids(detail, media_type="image"))
    video_infos = _media_info_by_id(client, advertiser_id, video_ids, media_type="video")
    image_infos = _media_info_by_id(client, advertiser_id, image_ids, media_type="image")
    tt_video_items = _tt_video_payload_by_item_id(
        client,
        advertiser_id,
        [
            item_id
            for detail in merged_details.values()
            if not _media_ids(detail, media_type="video") and not _media_ids(detail, media_type="image")
            for item_id in [_spark_item_id(detail)]
            if item_id
        ],
    )

    available_count = 0
    for row in rows:
        ad_id = str(row.get("ad_id") or "")
        detail = dict(merged_details.get(ad_id) or {})
        item_id = _spark_item_id(detail)
        if item_id and item_id in tt_video_items:
            detail["_tt_video_list_item"] = tt_video_items[item_id]
        preview = _preview_info(
            row=row,
            raw_row=raw_rows_by_ad_id.get(ad_id) or {},
            detail=detail,
            video_infos=video_infos,
            image_infos=image_infos,
        )
        row.update(preview)
        if preview["preview_status"] == "available":
            available_count += 1

    return {
        "enabled": True,
        "detail_count": len([detail for detail in merged_details.values() if detail]),
        "cached_detail_count": len(ad_ids) - len(missing_ad_ids),
        "detail_fetch_count": len(missing_ad_ids),
        "video_info_count": len(video_infos),
        "image_info_count": len(image_infos),
        "tt_video_item_count": len(tt_video_items),
        "available_count": available_count,
        "unavailable_count": len(rows) - available_count,
    }


def _normalize_creative(row: dict[str, Any]) -> dict[str, Any]:
    spend = _fnum(_metric(row, "spend"))
    impressions = _fnum(_metric(row, "impressions"))
    clicks = _fnum(_metric(row, "clicks"))
    conversion = _first_value(row, ("conversion", "result"))
    plays = _fnum(_metric(row, "video_play_actions"))
    watched_2s = _fnum(_metric(row, "video_watched_2s"))
    watched_6s = _fnum(_metric(row, "video_watched_6s"))
    engaged_6s = _fnum(_metric(row, "engaged_view"))
    engaged_15s = _fnum(_metric(row, "engaged_view_15s"))
    p25 = _fnum(_metric(row, "video_views_p25"))
    p50 = _fnum(_metric(row, "video_views_p50"))
    p75 = _fnum(_metric(row, "video_views_p75"))
    p100 = _fnum(_metric(row, "video_views_p100"))
    revenue, revenue_source = _revenue(row, spend)
    landing_url, landing_url_source = extract_landing_url(row)

    hook_2s_rate = _safe_rate(watched_2s, plays)
    six_sec_rate = _safe_rate(watched_6s, plays)
    p25_rate = _safe_rate(p25, plays)
    p50_rate = _safe_rate(p50, plays)
    p75_rate = _safe_rate(p75, plays)
    p100_rate = _safe_rate(p100, plays)
    engaged_15s_rate = _safe_rate(engaged_15s, plays)
    conversion_rate_from_play = _safe_rate(conversion, plays)
    roas = _safe_rate(revenue, spend)
    retention_score = round(
        (hook_2s_rate * 8)
        + (six_sec_rate * 12)
        + (p25_rate * 12)
        + (p50_rate * 18)
        + (p75_rate * 22)
        + (p100_rate * 18)
        + (engaged_15s_rate * 10)
        + min(conversion_rate_from_play * 1200, 10),
        4,
    )

    return {
        "ad_id": _row_id(row, "ad_id") or _row_id(row, "stat_ad_id"),
        "ad_id_v2": _first_non_empty_text(
            _row_id(row, "ad_id_v2"),
            _row_id(row, "smart_plus_ad_id"),
            _row_id(row, "upgraded_smart_plus_ad_id"),
        ),
        "ad_name": _row_id(row, "ad_name"),
        "campaign_id": _row_id(row, "campaign_id"),
        "campaign_name": _row_id(row, "campaign_name"),
        "campaign_automation_type": _row_id(row, "campaign_automation_type"),
        "adgroup_id": _row_id(row, "adgroup_id"),
        "adgroup_name": _row_id(row, "adgroup_name"),
        "objective_type": _row_id(row, "objective_type"),
        "promotion_type": _row_id(row, "promotion_type"),
        "placement_type": _row_id(row, "placement_type"),
        "currency": _row_id(row, "currency"),
        "landing_url": landing_url,
        "landing_url_source": landing_url_source,
        "spend": round(spend, 2),
        "impressions": round(impressions),
        "clicks": round(clicks),
        "ctr": _safe_rate(clicks, impressions),
        "cpc": _safe_cost(spend, clicks),
        "cpm": round(spend / impressions * 1000, 4) if impressions else 0.0,
        "conversion": round(conversion, 4),
        "cpa": _safe_cost(spend, conversion),
        "revenue": round(revenue, 2),
        "revenue_source": revenue_source,
        "roas": round(roas, 4),
        "video_play_actions": round(plays),
        "video_watched_2s": round(watched_2s),
        "video_watched_6s": round(watched_6s),
        "engaged_view": round(engaged_6s),
        "engaged_view_15s": round(engaged_15s),
        "video_views_p25": round(p25),
        "video_views_p50": round(p50),
        "video_views_p75": round(p75),
        "video_views_p100": round(p100),
        "average_video_play": _fnum(_metric(row, "average_video_play")),
        "average_video_play_per_user": _fnum(_metric(row, "average_video_play_per_user")),
        "retention_rates": {
            "hook_2s_rate": hook_2s_rate,
            "six_sec_rate": six_sec_rate,
            "engaged_6s_rate": _safe_rate(engaged_6s, plays),
            "engaged_15s_rate": engaged_15s_rate,
            "p25_rate": p25_rate,
            "p50_rate": p50_rate,
            "p75_rate": p75_rate,
            "p100_rate": p100_rate,
            "conversion_rate_from_play": conversion_rate_from_play,
        },
        "costs": {
            "cost_per_2s": _safe_cost(spend, watched_2s),
            "cost_per_6s": _safe_cost(spend, watched_6s),
            "cost_per_15s": _safe_cost(spend, engaged_15s),
            "cost_per_p100": _safe_cost(spend, p100),
        },
        "retention_score": retention_score,
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "spend": round(sum(_fnum(row.get("spend")) for row in rows), 2),
        "impressions": sum(_fnum(row.get("impressions")) for row in rows),
        "clicks": sum(_fnum(row.get("clicks")) for row in rows),
        "conversion": sum(_fnum(row.get("conversion")) for row in rows),
        "revenue": round(sum(_fnum(row.get("revenue")) for row in rows), 2),
    }


def _take(rows: list[dict[str, Any]], *, top: int) -> list[dict[str, Any]]:
    return rows[: max(top, 0)]


def _quality_sections(rows: list[dict[str, Any]], *, top: int) -> dict[str, list[dict[str, Any]]]:
    if not rows:
        return {
            "top_retention_creatives": [],
            "top_conversion_creatives": [],
            "high_spend_low_retention": [],
            "cheap_click_low_retention": [],
            "creative_refresh_candidates": [],
        }

    spends = [row["spend"] for row in rows if row["spend"] > 0]
    cpcs = [row["cpc"] for row in rows if row["cpc"] > 0]
    p50s = [row["retention_rates"]["p50_rate"] for row in rows if row["video_play_actions"] > 0]
    spend_median = median(spends) if spends else 0
    cpc_median = median(cpcs) if cpcs else 0
    p50_median = median(p50s) if p50s else 0

    high_spend_low_retention = [
        row
        for row in rows
        if row["spend"] >= spend_median and row["retention_rates"]["p50_rate"] < p50_median
    ]
    cheap_click_low_retention = [
        row
        for row in rows
        if row["clicks"] > 0 and row["cpc"] <= cpc_median and row["retention_rates"]["p50_rate"] < p50_median
    ]
    refresh_candidates = sorted(
        {row["ad_id"]: row for row in [*high_spend_low_retention, *cheap_click_low_retention] if row.get("ad_id")}.values(),
        key=lambda row: (row["spend"], -row["retention_score"]),
        reverse=True,
    )

    return {
        "top_retention_creatives": _take(sorted(rows, key=lambda row: row["retention_score"], reverse=True), top=top),
        "top_conversion_creatives": _take(sorted(rows, key=lambda row: (row["conversion"], -row["cpa"]), reverse=True), top=top),
        "high_spend_low_retention": _take(sorted(high_spend_low_retention, key=lambda row: row["spend"], reverse=True), top=top),
        "cheap_click_low_retention": _take(sorted(cheap_click_low_retention, key=lambda row: row["clicks"], reverse=True), top=top),
        "creative_refresh_candidates": _take(refresh_candidates, top=top),
    }


def _fetch_retention_rows(
    client: Any,
    *,
    advertiser_id: str,
    start_date: str,
    end_date: str,
    page_size: int,
    max_pages: int,
    include_attributes: bool,
    target_ad_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    target_ids = [str(ad_id).strip() for ad_id in (target_ad_ids or []) if str(ad_id).strip()]
    attribute_metrics = list(RETENTION_ATTRIBUTE_METRICS if include_attributes else [])
    # TikTok rejects `ad_id` dimensions together with the `ad_id_v2` attribute.
    # Detail enrichment still recovers ad_id_v2/smart_plus_ad_id where available.
    attribute_metrics = [metric for metric in attribute_metrics if metric != "ad_id_v2"]
    metrics = [*RETENTION_METRICS, *attribute_metrics]
    current_metrics = metrics
    if target_ids:
        page_size = max(page_size, min(len(target_ids), FILTERING_BATCH_SIZE))
        max_pages = 1

    target_batches = list(_chunked(target_ids, FILTERING_BATCH_SIZE)) if target_ids else [None]
    for target_batch in target_batches:
        filtering = _target_ad_filtering(target_batch or [])
        for page in range(1, max_pages + 1):
            try:
                response = client.integrated_report(
                    "BASIC",
                    advertiser_id=advertiser_id,
                    data_level="AUCTION_AD",
                    dimensions=["ad_id"],
                    metrics=current_metrics,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    page_size=page_size,
                    order_field="spend",
                    order_type="DESC",
                    filtering=filtering,
                )
            except Exception as exc:
                if include_attributes and current_metrics != RETENTION_METRICS:
                    warnings.append(
                        {
                            "type": "attribute_metric_fallback",
                            "message": sanitize_error_message(str(exc)),
                        }
                    )
                    current_metrics = RETENTION_METRICS
                    response = client.integrated_report(
                        "BASIC",
                        advertiser_id=advertiser_id,
                        data_level="AUCTION_AD",
                        dimensions=["ad_id"],
                        metrics=current_metrics,
                        start_date=start_date,
                        end_date=end_date,
                        page=page,
                        page_size=page_size,
                        order_field="spend",
                        order_type="DESC",
                        filtering=filtering,
                    )
                else:
                    raise

            page_rows = _extract_collection(response, "list")
            rows.extend(page_rows)
            if target_ids or len(page_rows) < page_size:
                break

    rows = _filter_rows_by_ad_ids(rows, target_ids)
    return rows, {"requested_metrics": current_metrics, "warnings": warnings, "target_ad_ids": target_ids}



def build_tiktok_creative_retention_report(
    client: Any,
    *,
    advertiser_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    top: int = 20,
    page_size: int = 200,
    max_pages: int = 3,
    include_attributes: bool = True,
    include_previews: bool = True,
    probe_on_missing_core: bool = True,
    target_ad_ids: list[str] | None = None,
    ad_details_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    page_size = max(1, page_size)
    max_pages = max(1, max_pages)
    top = max(0, top)
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    raw_rows, request_info = _fetch_retention_rows(
        client,
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        page_size=page_size,
        max_pages=max_pages,
        include_attributes=include_attributes,
        target_ad_ids=target_ad_ids,
    )
    creative_rows = [_normalize_creative(row) for row in raw_rows]
    creative_rows = [row for row in creative_rows if row.get("ad_id")]
    raw_rows_by_ad_id = {
        str(_row_id(raw_row, "ad_id") or _row_id(raw_row, "stat_ad_id") or ""): raw_row
        for raw_row in raw_rows
        if _row_id(raw_row, "ad_id") or _row_id(raw_row, "stat_ad_id")
    }
    preview_enrichment = {"enabled": False}
    if include_previews:
        preview_enrichment = _enrich_creative_previews(
            client,
            advertiser_id=advertiser_id,
            rows=creative_rows,
            raw_rows_by_ad_id=raw_rows_by_ad_id,
            ad_details_by_id=ad_details_by_id,
        )
    totals = _totals(creative_rows)
    core_coverage = build_core_metric_coverage(platform="tiktok", rows=creative_rows, totals=totals)

    supplemental_probe = None
    supplemental_probe_error = None
    if probe_on_missing_core and core_coverage["needs_full_probe"]:
        try:
            supplemental_probe = build_tiktok_metric_probe(
                client,
                advertiser_ids=[advertiser_id],
                start_date=start_date,
                end_date=end_date,
                page_size=1,
                profile="full",
            )
            core_coverage = build_core_metric_coverage(
                platform="tiktok",
                rows=creative_rows,
                totals=totals,
                probe=supplemental_probe,
            )
        except Exception as exc:  # pragma: no cover - external API surface
            supplemental_probe_error = sanitize_error_message(str(exc))

    sections = _quality_sections(creative_rows, top=top)
    notes = [
        "This is a read-only TikTok BASIC report at AUCTION_AD level.",
        "Retention score is a ranking heuristic combining 2s/6s/quartile/15s engaged view and conversion-from-play signals.",
    ]
    if "revenue" in core_coverage["missing_core_metrics"]:
        notes.append("Revenue remains missing or empty; treat ROI/ROAS diagnosis as a measurement gap until value metrics are populated.")

    return {
        "platform": "tiktok",
        "report": "short_drama_creative_retention",
        "advertiser_ids": [str(advertiser_id)],
        "date_range": {"start_date": start_date, "end_date": end_date},
        "strategy": "ad_level_video_retention_curve",
        "scope": "targeted_ad_ids" if request_info.get("target_ad_ids") else "ranked_scan",
        "row_count": len(creative_rows),
        "totals": totals,
        "core_metric_coverage": core_coverage,
        "supplemental_probe": {
            "triggered": bool(probe_on_missing_core and core_coverage["needs_full_probe"]) or supplemental_probe is not None,
            "profile": "full" if supplemental_probe is not None else None,
            "active_metric_count": len(supplemental_probe.get("active_metrics") or []) if supplemental_probe else 0,
            "unsupported_metric_count": len(supplemental_probe.get("unsupported_metrics") or []) if supplemental_probe else 0,
            "error": supplemental_probe_error,
        },
        "sections": sections,
        "request_stats": {
            "report_requests": min(max_pages, (len(raw_rows) // page_size) + 1 if page_size else 1),
            "page_size": page_size,
            "max_pages": max_pages,
            "target_ad_id_count": len(request_info.get("target_ad_ids") or []),
            "attribute_fallbacks": len(request_info["warnings"]),
            "preview_enrichment": preview_enrichment,
        },
        "warnings": request_info["warnings"],
        "analysis_notes": notes,
    }
