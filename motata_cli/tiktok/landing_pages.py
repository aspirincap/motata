from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from motata_cli.meta.commands import CliError


LANDING_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")
TIKTOK_ASSET_HOST_MARKERS = (
    "tiktokcdn.com",
    "tiktokv.com",
    "byteimg.com",
    "byteoversea.com",
    "bytedance.com",
    "p16-",
    "p19-",
    "p21-",
    "tos-",
)
DEFAULT_REPORT_METRICS = [
    "spend",
    "impressions",
    "clicks",
    "conversion",
    "total_purchase_value",
    "total_active_pay_roas",
    "onsite_total_purchase_value",
    "onsite_purchases_roas",
    "shop_gross_revenue_by_order_submission",
    "complete_payment",
    "complete_payment_roas",
    "value_per_complete_payment",
]
BASE_REPORT_METRICS = ["spend", "impressions", "clicks", "conversion"]
LEAN_REPORT_METRICS = ["spend", "impressions", "clicks"]
REPORT_ATTRIBUTE_METRICS = [
    "advertiser_name",
    "advertiser_id",
    "campaign_automation_type",
    "campaign_name",
    "campaign_id",
    "objective_type",
    "campaign_budget",
    "campaign_dedicate_type",
    "app_promotion_type",
    "adgroup_name",
    "adgroup_id",
    "promotion_type",
    "adgroup_download_url",
    "billing_event",
    "ad_name",
    "ad_text",
    "call_to_action",
    "ad_url",
    "tt_app_id",
    "tt_app_name",
    "mobile_app_id",
    "image_mode",
    "currency",
    "is_smart_creative",
]
ESSENTIAL_REPORT_ATTRIBUTE_METRICS = [
    "campaign_automation_type",
    "campaign_name",
    "campaign_id",
    "adgroup_name",
    "adgroup_id",
    "promotion_type",
    "adgroup_download_url",
    "ad_name",
    "ad_url",
    "currency",
]
DEFAULT_REPORT_PAGE_SIZE = 1000
DETAIL_BATCH_SIZE = 20
DEFAULT_AD_FIELDS = [
    "ad_id",
    "smart_plus_ad_id",
    "ad_name",
    "campaign_id",
    "campaign_name",
    "campaign_automation_type",
    "adgroup_id",
    "adgroup_name",
    "landing_page_url",
    "landing_page_urls",
    "creative_type",
    "ad_text",
    "ad_texts",
    "call_to_action",
    "identity_id",
    "identity_type",
    "video_id",
    "image_ids",
    "tracking_pixel_id",
    "tiktok_item_id",
]
DEFAULT_ADGROUP_FIELDS = [
    "adgroup_id",
    "adgroup_name",
    "campaign_id",
    "campaign_name",
    "promotion_type",
    "promotion_website_type",
    "promotion_target_type",
    "pixel_id",
    "optimization_goal",
    "billing_event",
    "app_id",
    "app_download_url",
    "creative_material_mode",
    "product_source",
    "product_set_id",
    "catalog_id",
    "actions",
    "operation_status",
    "placements",
]


def _fnum(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _inum(value: Any) -> int:
    return int(_fnum(value))


def default_date_range(days: int = 14) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    return start.isoformat(), end.isoformat()


def _extract_collection(response: dict[str, Any], *preferred_keys: str) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else None
    containers = [data, response]
    keys = preferred_keys or ("list", "ads", "adgroups", "items")
    for container in containers:
        if isinstance(container, dict):
            for key in keys:
                value = container.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        elif isinstance(container, list):
            return [item for item in container if isinstance(item, dict)]
    return []


def _page_info(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict) and isinstance(data.get("page_info"), dict):
        return data["page_info"]
    if isinstance(response.get("page_info"), dict):
        return response["page_info"]
    return {}


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _dedupe(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(values) if value]


def _with_report_attributes(metrics: list[str]) -> list[str]:
    return _dedupe([*metrics, *REPORT_ATTRIBUTE_METRICS])


def _with_essential_report_attributes(metrics: list[str]) -> list[str]:
    return _dedupe([*metrics, *ESSENTIAL_REPORT_ATTRIBUTE_METRICS])


def _has_report_attributes(metrics: list[str]) -> bool:
    return any(metric in REPORT_ATTRIBUTE_METRICS for metric in metrics)


def _report_metrics_mode(metrics: list[str], requested_metrics: list[str]) -> str:
    metric_set = set(metrics)
    if all(metric in metric_set for metric in REPORT_ATTRIBUTE_METRICS):
        return "full_attributes"
    if all(metric in metric_set for metric in ESSENTIAL_REPORT_ATTRIBUTE_METRICS):
        if all(metric in metric_set for metric in requested_metrics):
            return "essential_attributes"
        if all(metric in metric_set for metric in BASE_REPORT_METRICS):
            return "base_with_essential_attributes"
        return "lean_with_essential_attributes"
    if metrics == requested_metrics:
        return "requested_metrics_only"
    if metrics == BASE_REPORT_METRICS:
        return "base_metrics_only"
    if metrics == LEAN_REPORT_METRICS:
        return "lean_metrics_only"
    return "custom"


def _report_metric_candidates(requested_metrics: list[str]) -> list[list[str]]:
    candidates = [
        _with_report_attributes(requested_metrics),
        _with_essential_report_attributes(requested_metrics),
        _with_essential_report_attributes(BASE_REPORT_METRICS),
        _with_essential_report_attributes(LEAN_REPORT_METRICS),
        list(requested_metrics),
        list(BASE_REPORT_METRICS),
        list(LEAN_REPORT_METRICS),
    ]
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for metrics in candidates:
        key = tuple(metrics)
        if key not in seen:
            deduped.append(metrics)
            seen.add(key)
    return deduped


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_landing_url(url: str | None) -> bool:
    if not url or not _looks_like_url(url):
        return False
    host = urlparse(url).netloc.lower()
    return not any(marker in host for marker in TIKTOK_ASSET_HOST_MARKERS)


def _url_kind(url: str, source: str) -> str:
    lowered_source = source.lower()
    host = urlparse(url).netloc.lower()
    if "apps.apple.com" in host or "itunes.apple.com" in host or "play.google.com" in host:
        return "app_store"
    if any(marker in host for marker in TIKTOK_ASSET_HOST_MARKERS):
        return "creative_asset"
    if any(token in lowered_source for token in ("landing", "website", "web_url", "ad_url", "click_url")):
        return "landing"
    if "deeplink" in lowered_source or "deep_link" in lowered_source:
        return "deeplink"
    if any(token in lowered_source for token in ("creative", "material", "video", "image", "thumbnail", "media", "play_url", "preview")):
        return "creative_asset"
    return "unknown"


def canonicalize_landing_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip().rstrip(").,;]"))
    path = unquote(parsed.path).rstrip("/") or "/"
    if "/products/" in path:
        query = ""
    else:
        kept_params = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered.startswith("utm") or lowered in {"ttclid", "fbclid", "gclid", "msclkid"}:
                continue
            kept_params.append((key, value))
        query = urlencode(kept_params)
    return urlunparse(("https", parsed.netloc.lower(), path, "", query, ""))


def _url_evidence_item(url: str, source: str) -> dict[str, Any] | None:
    normalized = url.strip().rstrip(").,;]")
    if not _looks_like_url(normalized):
        return None
    canonical = canonicalize_landing_url(normalized)
    return {
        "url": canonical,
        "raw_url": normalized,
        "source": source,
        "kind": _url_kind(normalized, source),
    }


def _collect_url_evidence(value: Any, found: list[dict[str, Any]], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(child, str):
                candidates: list[str] = []
                lowered_key = str(key).lower()
                if any(token in lowered_key for token in ("landing", "url", "link", "website", "deeplink", "creative", "video", "image", "thumbnail", "material", "media")):
                    candidates.extend(LANDING_URL_RE.findall(child))
                    candidates.append(child)
                elif child.startswith(("http://", "https://")):
                    candidates.append(child)
                for candidate in candidates:
                    item = _url_evidence_item(candidate, child_path)
                    if item:
                        found.append(item)
            else:
                _collect_url_evidence(child, found, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            if isinstance(child, str):
                candidates = LANDING_URL_RE.findall(child)
                if child.startswith(("http://", "https://")):
                    candidates.append(child)
                for candidate in candidates:
                    item = _url_evidence_item(candidate, child_path)
                    if item:
                        found.append(item)
            else:
                _collect_url_evidence(child, found, child_path)


def extract_url_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _collect_url_evidence(payload, found)
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in found:
        deduped.setdefault((item["url"], item["source"], item["kind"]), item)
    return list(deduped.values())


def _collect_landing_urls(value: Any, found: list[tuple[str, str]], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(child, str):
                candidates: list[str] = []
                lowered_key = str(key).lower()
                if any(token in lowered_key for token in ("landing", "url", "link", "website", "deeplink")):
                    candidates.extend(LANDING_URL_RE.findall(child))
                    candidates.append(child)
                elif child.startswith(("http://", "https://")):
                    candidates.append(child)
                for candidate in candidates:
                    normalized = candidate.strip().rstrip(").,;]")
                    if _is_landing_url(normalized):
                        found.append((canonicalize_landing_url(normalized), child_path))
            else:
                _collect_landing_urls(child, found, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            if isinstance(child, str) and child.startswith(("http://", "https://")):
                normalized = child.strip().rstrip(").,;]")
                if _is_landing_url(normalized):
                    found.append((canonicalize_landing_url(normalized), child_path))
            else:
                _collect_landing_urls(child, found, child_path)


def extract_landing_url(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    evidence = [
        item
        for item in extract_url_evidence(payload)
        if item.get("kind") != "creative_asset" and _is_landing_url(str(item.get("url") or ""))
    ]
    if evidence:
        preferred_sources = (
            "landing_page_url",
            "landing_page_urls",
            "landing_page_url_list",
            "website_url",
            "web_url",
            "ad_url",
            "click_url",
            "deeplink",
            "fallback",
            "url",
        )
        for marker in preferred_sources:
            for item in evidence:
                if marker in str(item.get("source") or ""):
                    return str(item["url"]), str(item["source"])
        item = evidence[0]
        return str(item["url"]), str(item["source"])

    found: list[tuple[str, str]] = []
    _collect_landing_urls(payload, found)
    if not found:
        return None, None

    preferred_sources = (
        "landing_page_url",
        "landing_page_urls",
        "landing_page_url_list",
        "website_url",
        "web_url",
        "ad_url",
        "click_url",
        "deeplink",
        "fallback",
        "url",
    )
    for marker in preferred_sources:
        for url, source in found:
            if marker in source:
                return url, source
    return found[0]


def _metric(row: dict[str, Any], key: str) -> Any:
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return metrics.get(key)
    return row.get(key)


def _dimension(row: dict[str, Any], key: str) -> Any:
    dimensions = row.get("dimensions")
    if isinstance(dimensions, dict) and key in dimensions:
        return dimensions.get(key)
    return row.get(key)


def _report_ad_id(row: dict[str, Any]) -> str | None:
    value = _dimension(row, "ad_id") or _dimension(row, "stat_ad_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _report_ad_v2_id(row: dict[str, Any]) -> str | None:
    value = _dimension(row, "ad_id_v2") or _metric(row, "ad_id_v2")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _report_ad_detail(row: dict[str, Any], ad_id: str) -> dict[str, Any]:
    mapping = {
        "ad_name": "ad_name",
        "campaign_id": "campaign_id",
        "campaign_name": "campaign_name",
        "adgroup_id": "adgroup_id",
        "adgroup_name": "adgroup_name",
        "campaign_automation_type": "campaign_automation_type",
        "ad_id_v2": "ad_id_v2",
        "smart_plus_ad_id": "smart_plus_ad_id",
        "objective_type": "objective_type",
        "promotion_type": "promotion_type",
        "ad_text": "ad_text",
        "call_to_action": "call_to_action",
        "ad_url": "landing_page_url",
        "ad_url_list": "landing_page_url_list",
        "adgroup_download_url": "adgroup_download_url",
        "tt_app_id": "app_id",
        "tt_app_name": "app_name",
        "mobile_app_id": "mobile_app_id",
        "billing_event": "billing_event",
        "currency": "currency",
        "image_mode": "image_mode",
        "is_smart_creative": "is_smart_creative",
    }
    detail: dict[str, Any] = {"ad_id": ad_id, "_detail_source": "report"}
    for source_key, target_key in mapping.items():
        value = _metric(row, source_key)
        if value in (None, "", "-"):
            value = _dimension(row, source_key)
        if value not in (None, "", "-"):
            detail[target_key] = value
    if detail.get("adgroup_download_url") and not detail.get("app_download_url"):
        detail["app_download_url"] = detail["adgroup_download_url"]
    return detail


def _report_asset_detail(row: dict[str, Any], ad_id_v2: str) -> dict[str, Any]:
    detail = _report_ad_detail(row, ad_id_v2)
    detail["_detail_source"] = "report_ad_id_v2"
    detail["ad_id"] = ad_id_v2
    detail["ad_id_v2"] = ad_id_v2
    detail.setdefault("smart_plus_ad_id", ad_id_v2)
    return detail


def _purchase_revenue(row: dict[str, Any], spend: float) -> float:
    for metric in ("total_purchase_value", "onsite_total_purchase_value", "shop_gross_revenue_by_order_submission"):
        value = _fnum(_metric(row, metric))
        if value:
            return value
    for metric in ("total_active_pay_roas", "onsite_purchases_roas"):
        roas = _fnum(_metric(row, metric))
        if roas and spend:
            return spend * roas
    roas = _fnum(_metric(row, "complete_payment_roas"))
    if roas:
        return spend * roas
    complete_payment = _fnum(_metric(row, "complete_payment"))
    value_per_complete_payment = _fnum(_metric(row, "value_per_complete_payment"))
    if complete_payment and value_per_complete_payment:
        return complete_payment * value_per_complete_payment
    return 0.0


def _report_rows(
    client: Any,
    advertiser_id: str,
    *,
    start_date: str,
    end_date: str,
    page_size: int,
    max_pages: int,
    max_rows: int | None = None,
    metrics: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    requested_metrics = metrics or DEFAULT_REPORT_METRICS
    attempts: list[dict[str, Any]] = []
    for candidate_metrics in _report_metric_candidates(requested_metrics):
        rows: list[dict[str, Any]] = []
        mode = _report_metrics_mode(candidate_metrics, requested_metrics)
        try:
            for page in range(1, max_pages + 1):
                response = client.integrated_report(
                    "BASIC",
                    advertiser_id=advertiser_id,
                    data_level="AUCTION_AD",
                    dimensions=["ad_id"],
                    metrics=candidate_metrics,
                    start_date=start_date,
                    end_date=end_date,
                    order_field="spend",
                    order_type="DESC",
                    page=page,
                    page_size=page_size,
                )
                page_rows = _extract_collection(response, "list")
                rows.extend(page_rows)
                if max_rows is not None and max_rows > 0 and len(rows) >= max_rows:
                    rows = rows[:max_rows]
                    break
                info = _page_info(response)
                total_page = int(_fnum(info.get("total_page") or info.get("total_pages")))
                if total_page and page >= total_page:
                    break
                if len(page_rows) < page_size:
                    break
            warning = None
            if attempts:
                warning = {
                    "advertiser_id": advertiser_id,
                    "fallback": f"retried TikTok report with {mode}",
                    "report_attribute_mode": mode,
                    "attempts": attempts,
                }
            return rows, warning
        except CliError as exc:
            attempts.append(
                {
                    "mode": mode,
                    "error": str(exc),
                    "metrics": candidate_metrics,
                    "attribute_metrics": _has_report_attributes(candidate_metrics),
                }
            )
            continue
    if attempts:
        raise CliError(attempts[-1]["error"])
    raise CliError("TikTok report returned no metric candidates")


def _cache_ad_detail(cache: dict[str, dict[str, Any]] | None, detail: dict[str, Any]) -> None:
    if cache is None:
        return
    for ad_id in _ad_identity_values(detail):
        cache[ad_id] = {**cache.get(ad_id, {}), **detail}


def _list_ads_by_ids(
    client: Any,
    advertiser_id: str,
    ad_ids: list[str],
    *,
    smart_plus: bool,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    requested_ad_ids = _dedupe([str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()])
    details: dict[str, dict[str, Any]] = {}
    for ad_id in requested_ad_ids:
        cached = cache.get(ad_id) if cache is not None else None
        if cached:
            details[ad_id] = cached
    missing_ids = [ad_id for ad_id in requested_ad_ids if ad_id not in details]
    filter_key = "smart_plus_ad_ids" if smart_plus else "ad_ids"
    for batch in _chunked(missing_ids, DETAIL_BATCH_SIZE):
        try:
            response = client.list_ads(
                advertiser_id,
                filtering={filter_key: batch},
                page=1,
                page_size=len(batch),
                fields=DEFAULT_AD_FIELDS if not smart_plus else None,
                smart_plus=smart_plus,
            )
        except CliError:
            response = client.list_ads(
                advertiser_id,
                filtering={filter_key: batch},
                page=1,
                page_size=len(batch),
                smart_plus=smart_plus,
            )
        for item in _extract_collection(response, "list", "ads"):
            _cache_ad_detail(cache, item)
            for ad_id in _ad_identity_values(item):
                if ad_id in requested_ad_ids:
                    details[ad_id] = {**details.get(ad_id, {}), **item}
    if not smart_plus:
        missing_v2_ids = [ad_id for ad_id in requested_ad_ids if ad_id not in details]
        details.update(_list_normal_ads_by_v2_ids(client, advertiser_id, missing_v2_ids, cache=cache))
    return details


def _ad_identity_values(ad: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("ad_id", "ad_id_v2", "smart_plus_ad_id"):
        value = ad.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                values.append(text)
    return list(dict.fromkeys(values))


def _list_normal_ads_by_v2_ids(
    client: Any,
    advertiser_id: str,
    ad_ids: list[str],
    *,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    requested_ad_ids = _dedupe([str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()])
    if not requested_ad_ids or not hasattr(client, "_raw_request"):
        return {}
    details: dict[str, dict[str, Any]] = {}
    fields = [field for field in DEFAULT_AD_FIELDS if field != "ad_id_v2"]
    for batch in _chunked(requested_ad_ids, DETAIL_BATCH_SIZE):
        try:
            response = client._raw_request(
                "GET",
                "ad/get/",
                params={
                    "advertiser_id": advertiser_id,
                    "filtering": {"ad_ids_v2": batch},
                    "page": 1,
                    "page_size": len(batch),
                    "fields": fields,
                },
                timeout=5,
            )
        except Exception:
            continue
        for item in _extract_collection(response, "list", "ads"):
            _cache_ad_detail(cache, item)
            for ad_id in _ad_identity_values(item):
                if ad_id in requested_ad_ids:
                    details[ad_id] = item
    return details


def _list_upgraded_smart_plus_ads(
    client: Any,
    advertiser_id: str,
    ad_ids: list[str],
    *,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    if not ad_ids:
        return details
    requested_ad_ids = _dedupe([str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()])
    for ad_id in requested_ad_ids:
        cached = cache.get(ad_id) if cache is not None else None
        if cached and _is_smart_plus_ad_detail(cached):
            details[ad_id] = cached
    missing_ids = [ad_id for ad_id in requested_ad_ids if ad_id not in details]
    if not missing_ids:
        return details
    fields = [
        "ad_id",
        "smart_plus_ad_id",
        "ad_name",
        "campaign_id",
        "campaign_name",
        "adgroup_id",
        "adgroup_name",
        "landing_page_url",
        "landing_page_urls",
        "landing_page_url_list",
        "ad_configuration",
        "ad_text_list",
        "call_to_action_list",
        "creative_list",
        "creative_material_list",
        "image_list",
        "video_list",
        "card_list",
        "deeplink_list",
        "page_list",
        "app_profile_page_list",
    ]
    for batch in _chunked(missing_ids, DETAIL_BATCH_SIZE):
        response = None
        if hasattr(client, "_raw_request"):
            for filter_key in ("smart_plus_ad_ids", "ad_ids"):
                try:
                    candidate_response = client._raw_request(
                        "GET",
                        "smart_plus/ad/get/",
                        params={
                            "advertiser_id": advertiser_id,
                            "page": 1,
                            "page_size": max(len(batch), 100),
                            "filtering": {filter_key: batch},
                        },
                        timeout=20,
                    )
                except Exception:
                    continue
                if _extract_collection(candidate_response, "list", "ads"):
                    response = candidate_response
                    break
        if response is None:
            try:
                response = client.list_ads(
                    advertiser_id,
                    filtering={"smart_plus_ad_ids": batch},
                    page=1,
                    page_size=len(batch),
                    fields=fields,
                    smart_plus=True,
                )
            except CliError:
                try:
                    response = client.list_ads(
                        advertiser_id,
                        filtering={"smart_plus_ad_ids": batch},
                        page=1,
                        page_size=len(batch),
                        smart_plus=True,
                    )
                except Exception:
                    continue
            except Exception:
                continue
        for item in _extract_collection(response, "list", "ads"):
            detail = {**item, "_detail_source": "smart_plus_ad_get"}
            _cache_ad_detail(cache, detail)
            for ad_id in _ad_identity_values(detail):
                if ad_id in requested_ad_ids:
                    details[ad_id] = detail
    return details


def _is_smart_plus_ad_detail(detail: dict[str, Any]) -> bool:
    if detail.get("_detail_source") == "smart_plus_ad_get":
        return True
    return any(
        detail.get(key)
        for key in (
            "landing_page_url_list",
            "ad_configuration",
            "creative_list",
            "ad_text_list",
            "call_to_action_list",
        )
    )


def _is_legacy_smart_plus(ad_detail: dict[str, Any]) -> bool:
    automation_type = str(ad_detail.get("campaign_automation_type") or "").upper()
    return automation_type == "SMART_PLUS"


def _is_upgraded_smart_plus(ad_detail: dict[str, Any]) -> bool:
    automation_type = str(ad_detail.get("campaign_automation_type") or "").upper()
    return automation_type in {"UPGRADED_SMART_PLUS", "UPGRADED_SMART_PLUS_CREATIVE"}


def _campaign_identity_values(campaign: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("campaign_id", "campaign_id_v2", "smart_plus_campaign_id", "spc_campaign_id"):
        value = campaign.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                values.append(text)
    return list(dict.fromkeys(values))


def _list_campaign_spc_details(
    client: Any,
    advertiser_id: str,
    campaign_ids: list[str],
    *,
    cache: dict[str, dict[str, Any]],
) -> None:
    missing_ids = [
        campaign_id
        for campaign_id in dict.fromkeys(campaign_ids)
        if campaign_id and f"{advertiser_id}:{campaign_id}" not in cache
    ]
    if not missing_ids or not hasattr(client, "_raw_request"):
        return

    for batch in _chunked(missing_ids, DETAIL_BATCH_SIZE):
        params = {
            "advertiser_id": advertiser_id,
            "campaign_ids": batch,
        }
        try:
            response = client._raw_request("GET", "campaign/spc/get/", params=params)
        except CliError as exc:
            try:
                response = client._raw_request(
                    "GET",
                    "campaign/spc/get/",
                    params={"advertiser_id": advertiser_id, "filtering": {"campaign_ids": batch}},
                )
            except Exception as fallback_exc:  # pragma: no cover - depends on account/API permissions
                for campaign_id in batch:
                    cache[f"{advertiser_id}:{campaign_id}"] = {"_error": str(fallback_exc), "_first_error": str(exc)}
                continue
        except Exception as exc:  # pragma: no cover - depends on account/API permissions
            for campaign_id in batch:
                cache[f"{advertiser_id}:{campaign_id}"] = {"_error": str(exc)}
            continue

        matches = _extract_collection(response, "list", "campaigns", "campaign_list", "spc_campaigns", "smart_plus_campaigns", "items")
        matched_ids: set[str] = set()
        for item in matches:
            detail = {**item, "_detail_source": "campaign_spc_get"}
            for campaign_id in _campaign_identity_values(item):
                if campaign_id in batch:
                    cache[f"{advertiser_id}:{campaign_id}"] = detail
                    matched_ids.add(campaign_id)
        if len(batch) == 1 and not matched_ids:
            cache[f"{advertiser_id}:{batch[0]}"] = {**response, "_detail_source": "campaign_spc_get"}
            matched_ids.add(batch[0])
        for campaign_id in batch:
            if campaign_id not in matched_ids:
                cache[f"{advertiser_id}:{campaign_id}"] = {
                    "_error": "campaign_spc_get returned no matching campaign",
                    "_detail_source": "campaign_spc_get",
                }


def _campaign_spc_detail(
    client: Any,
    advertiser_id: str,
    campaign_id: str | None,
    *,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not campaign_id:
        return {}
    cache_key = f"{advertiser_id}:{campaign_id}"
    if cache_key not in cache:
        _list_campaign_spc_details(client, advertiser_id, [campaign_id], cache=cache)
    return cache.get(cache_key, {})


def _adgroup_detail(
    client: Any,
    advertiser_id: str,
    adgroup_id: str | None,
    *,
    smart_plus: bool,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not adgroup_id:
        return {}
    if adgroup_id not in cache:
        try:
            cache[adgroup_id] = client.get_adgroup(
                advertiser_id,
                adgroup_id,
                fields=DEFAULT_ADGROUP_FIELDS if not smart_plus else None,
                smart_plus=smart_plus,
            )
        except CliError:
            try:
                cache[adgroup_id] = client.get_adgroup(advertiser_id, adgroup_id, smart_plus=smart_plus)
            except Exception as exc:  # pragma: no cover - depends on account permissions
                cache[adgroup_id] = {"_error": str(exc)}
        except Exception as exc:  # pragma: no cover - depends on account permissions
            cache[adgroup_id] = {"_error": str(exc)}
    return cache[adgroup_id]


def _prefetch_adgroup_details(
    client: Any,
    advertiser_id: str,
    adgroup_ids: list[str],
    *,
    smart_plus: bool,
    cache: dict[str, dict[str, Any]],
) -> None:
    if not hasattr(client, "list_adgroups"):
        return
    missing = [adgroup_id for adgroup_id in dict.fromkeys(adgroup_ids) if adgroup_id and adgroup_id not in cache]
    if not missing:
        return
    for batch in _chunked(missing, DETAIL_BATCH_SIZE):
        try:
            response = client.list_adgroups(
                advertiser_id,
                filtering={"adgroup_ids": batch},
                page=1,
                page_size=len(batch),
                fields=DEFAULT_ADGROUP_FIELDS if not smart_plus else None,
                smart_plus=smart_plus,
            )
        except CliError:
            try:
                response = client.list_adgroups(
                    advertiser_id,
                    filtering={"adgroup_ids": batch},
                    page=1,
                    page_size=len(batch),
                    smart_plus=smart_plus,
                )
            except Exception as exc:
                for adgroup_id in batch:
                    cache.setdefault(adgroup_id, {"_error": str(exc)})
                continue
        except Exception as exc:
            for adgroup_id in batch:
                cache.setdefault(adgroup_id, {"_error": str(exc)})
            continue
        found: set[str] = set()
        for item in _extract_collection(response, "list", "adgroups"):
            adgroup_id = str(item.get("adgroup_id") or "").strip()
            if adgroup_id:
                cache[adgroup_id] = item
                found.add(adgroup_id)
        for adgroup_id in batch:
            if adgroup_id not in found:
                cache.setdefault(adgroup_id, {"_error": "adgroup list returned no matching adgroup"})


def _resolve_landing_url(
    client: Any,
    advertiser_id: str,
    ad_detail: dict[str, Any],
    *,
    smart_plus: bool,
    adgroup_cache: dict[str, dict[str, Any]],
    campaign_spc_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    landing_url, source = extract_landing_url(ad_detail)
    adgroup_id = str(ad_detail.get("adgroup_id") or "").strip() or None
    campaign_id = str(ad_detail.get("campaign_id") or "").strip() or None
    campaign_spc = {}
    adgroup = {}
    if not landing_url and not smart_plus and _is_legacy_smart_plus(ad_detail):
        campaign_spc = _campaign_spc_detail(
            client,
            advertiser_id,
            campaign_id,
            cache=campaign_spc_cache,
        )
        campaign_url, campaign_source = extract_landing_url(campaign_spc)
        if campaign_url:
            landing_url = campaign_url
            source = f"campaign_spc.{campaign_source}"
    if not landing_url:
        adgroup = _adgroup_detail(
            client,
            advertiser_id,
            adgroup_id,
            smart_plus=smart_plus,
            cache=adgroup_cache,
        )
        adgroup_url, adgroup_source = extract_landing_url(adgroup)
        if adgroup_url:
            landing_url = adgroup_url
            source = f"adgroup.{adgroup_source}"
    return {
        "url": landing_url,
        "url_source": source,
        "ad_detail": ad_detail,
        "adgroup": adgroup,
        "campaign_spc": campaign_spc,
    }


def _should_try_smart_plus_detail(ad_detail: dict[str, Any]) -> bool:
    automation_type = str(ad_detail.get("campaign_automation_type") or "").upper()
    if automation_type in {"UPGRADED_SMART_PLUS", "UPGRADED_SMART_PLUS_CREATIVE"}:
        return True
    if ad_detail.get("smart_plus_ad_id"):
        return True
    return False


def _smart_plus_candidate_ids(ad_id: str, ad_detail: dict[str, Any]) -> list[str]:
    asset_ids = [
        str(ad_detail.get("smart_plus_ad_id") or "").strip(),
        str(ad_detail.get("ad_id_v2") or "").strip(),
        str(ad_detail.get("upgraded_smart_plus_ad_id") or "").strip(),
    ]
    deduped_asset_ids = [value for value in dict.fromkeys(asset_ids) if value]
    if deduped_asset_ids:
        return deduped_asset_ids
    return [ad_id] if ad_id else []


def _upgraded_smart_plus_identity(report_ad_id: str, ad_detail: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_upgraded_smart_plus(ad_detail):
        return None
    automation_type = str(ad_detail.get("campaign_automation_type") or "").upper()
    smart_plus_ad_id = (
        ad_detail.get("smart_plus_ad_id")
        or ad_detail.get("ad_id_v2")
        or ad_detail.get("upgraded_smart_plus_ad_id")
    )
    creative_id = ad_detail.get("creative_id")
    if automation_type == "UPGRADED_SMART_PLUS_CREATIVE":
        creative_id = creative_id or report_ad_id or ad_detail.get("ad_id")
    return {
        "campaign_automation_type": automation_type,
        "report_ad_id": report_ad_id,
        "smart_plus_ad_id": str(smart_plus_ad_id).strip() if smart_plus_ad_id else None,
        "creative_id": str(creative_id).strip() if creative_id else None,
        "ad_id_v2": str(smart_plus_ad_id).strip() if smart_plus_ad_id else None,
        "detail_source": ad_detail.get("_detail_source"),
    }


def _matching_smart_plus_detail(ad_detail: dict[str, Any], smart_details_by_candidate: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    campaign_id = str(ad_detail.get("campaign_id") or "").strip()
    adgroup_id = str(ad_detail.get("adgroup_id") or "").strip()
    for candidate in smart_details_by_candidate.values():
        if adgroup_id and str(candidate.get("adgroup_id") or "").strip() == adgroup_id:
            return candidate
        if campaign_id and str(candidate.get("campaign_id") or "").strip() == campaign_id:
            return candidate
    return None


def _creative_shape(ad_detail: dict[str, Any], adgroup: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("landing_page_url", "landing_page_urls", "landing_page_url_list"):
        if ad_detail.get(key):
            parts.append(key)
    for key in ("creatives", "creative_list", "ad_text", "ad_texts", "call_to_action", "deeplink", "video_id", "image_ids"):
        if ad_detail.get(key):
            parts.append(key)
    if adgroup.get("promotion_type"):
        parts.append(f"promotion:{adgroup.get('promotion_type')}")
    if adgroup.get("app_id"):
        parts.append("app")
    if adgroup.get("pixel_id"):
        parts.append("pixel")
    return "+".join(parts) if parts else "minimal_ad"


def _unresolved_ad_structure(report_row: dict[str, Any], resolved: dict[str, Any], spend: float) -> dict[str, Any]:
    ad_detail = resolved.get("ad_detail") or {}
    adgroup = resolved.get("adgroup") or {}
    campaign_spc = resolved.get("campaign_spc") or {}
    return {
        "advertiser_id": report_row.get("advertiser_id"),
        "ad_id": report_row.get("ad_id"),
        "ad_name": ad_detail.get("ad_name") or report_row.get("ad_name"),
        "campaign_id": ad_detail.get("campaign_id") or report_row.get("campaign_id"),
        "campaign_name": ad_detail.get("campaign_name") or report_row.get("campaign_name"),
        "adgroup_id": ad_detail.get("adgroup_id") or report_row.get("adgroup_id"),
        "adgroup_name": ad_detail.get("adgroup_name") or adgroup.get("adgroup_name"),
        "spend": round(spend, 2),
        "creative_shape": _creative_shape(ad_detail, adgroup),
        "campaign_automation_type": ad_detail.get("campaign_automation_type"),
        "smart_plus_ad_id": ad_detail.get("smart_plus_ad_id"),
        "ad_id_v2": ad_detail.get("ad_id_v2") or ad_detail.get("smart_plus_ad_id") or ad_detail.get("upgraded_smart_plus_ad_id"),
        "upgraded_smart_plus_identity": _upgraded_smart_plus_identity(str(report_row.get("ad_id") or ""), ad_detail),
        "detail_source": ad_detail.get("_detail_source"),
        "url_evidence": extract_url_evidence(ad_detail),
        "ad_keys": sorted(ad_detail.keys()),
        "adgroup_keys": sorted(adgroup.keys()),
        "campaign_spc_keys": sorted(campaign_spc.keys()),
        "promotion_type": adgroup.get("promotion_type"),
        "objective_type": adgroup.get("objective_type"),
        "optimization_goal": adgroup.get("optimization_goal"),
        "billing_event": adgroup.get("billing_event"),
        "app_id": adgroup.get("app_id") or ad_detail.get("app_id"),
        "pixel_id": adgroup.get("pixel_id") or ad_detail.get("tracking_pixel_id"),
        "identity_id": ad_detail.get("identity_id"),
        "identity_type": ad_detail.get("identity_type"),
        "tiktok_item_id": ad_detail.get("tiktok_item_id"),
        "video_id": ad_detail.get("video_id"),
        "image_ids": ad_detail.get("image_ids"),
        "adgroup_error": adgroup.get("_error"),
        "campaign_spc_error": campaign_spc.get("_error"),
    }


def _product_info(
    url: str,
    *,
    enrich_product: bool,
    product_scraper: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if not enrich_product:
        return {
            "product_name": urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url,
            "price": None,
            "product_error": None,
        }
    if product_scraper is None:
        from motata_cli.product.scraper import scrape_product as product_scraper
    try:
        product = product_scraper(url)
    except Exception as exc:  # pragma: no cover - defensive around external pages
        product = {"error": str(exc)}
    return {
        "product_name": html.unescape(product.get("name") or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url),
        "price": product.get("price"),
        "product_error": product.get("error"),
    }


def _enrich_product_rows(
    rows: list[dict[str, Any]],
    *,
    enrich_product: bool,
    product_limit: int,
    product_scraper: Callable[[str], dict[str, Any]] | None,
) -> None:
    enrich_targets: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(rows, start=1):
        if product_limit <= 0 or index <= product_limit:
            enrich_targets.append((index - 1, row))
        else:
            row.update(
                {
                    "product_name": urlparse(row["url"]).path.rstrip("/").rsplit("/", 1)[-1] or row["url"],
                    "price": None,
                    "product_error": "Product enrichment skipped by product_limit",
                }
            )

    def enrich(row: dict[str, Any]) -> dict[str, Any]:
        return _product_info(row["url"], enrich_product=enrich_product, product_scraper=product_scraper)

    if len(enrich_targets) <= 1:
        for _, row in enrich_targets:
            row.update(enrich(row))
        return
    with ThreadPoolExecutor(max_workers=min(8, len(enrich_targets))) as executor:
        results = list(executor.map(lambda item: enrich(item[1]), enrich_targets))
    for (row_index, _), product in zip(enrich_targets, results):
        rows[row_index].update(product)


def discover_advertiser_ids(client: Any, *, app_id: str | None, secret: str | None) -> list[str]:
    if not app_id or not secret:
        return []
    payload = client.oauth2_advertiser_get(app_id, secret)
    advertisers = _extract_collection(payload, "list", "advertiser_ids", "advertisers")
    ids: list[str] = []
    if advertisers:
        for item in advertisers:
            value = item.get("advertiser_id") or item.get("id")
            if value:
                ids.append(str(value))
    data = payload.get("data") if isinstance(payload, dict) else None
    for key in ("advertiser_ids", "advertiser_id_list"):
        values = data.get(key) if isinstance(data, dict) else None
        if isinstance(values, list):
            ids.extend(str(value) for value in values if value)
    return sorted(set(ids))


def build_tiktok_landing_page_report(
    client: Any,
    *,
    advertiser_ids: list[str],
    start_date: str,
    end_date: str,
    top: int | None = None,
    report_page_size: int = DEFAULT_REPORT_PAGE_SIZE,
    max_pages: int = 50,
    enrich_product: bool = False,
    product_limit: int = 50,
    include_ads: bool = False,
    smart_plus: bool = False,
    ad_limit: int | None = None,
    product_scraper: Callable[[str], dict[str, Any]] | None = None,
    skip_campaign_ids: set[str] | list[str] | None = None,
    ad_detail_cache: dict[str, dict[str, Any]] | None = None,
    preloaded_report_rows: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None,
    preloaded_asset_report_rows: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    no_url: list[dict[str, Any]] = []
    account_errors: list[dict[str, Any]] = []
    account_warnings: list[dict[str, Any]] = []
    adgroup_cache: dict[str, dict[str, Any]] = {}
    campaign_spc_cache: dict[str, dict[str, Any]] = {}
    total_spend_ads: list[dict[str, Any]] = []
    skipped_url_probe_count = 0
    report_direct_url_count = 0
    ad_detail_fetch_count = 0
    ad_detail_fetched_count = 0
    skipped_ad_detail_fetch_count = 0
    upgraded_smart_plus_ad_count = 0
    smart_plus_detail_count = 0
    creative_url_ad_count = 0
    app_store_url_ad_count = 0
    report_attribute_modes: dict[str, str] = {}
    skip_campaign_id_set = {str(value).strip() for value in (skip_campaign_ids or []) if str(value).strip()}

    for advertiser_id in advertiser_ids:
        try:
            asset_report_rows: list[dict[str, Any]] = []
            if isinstance(preloaded_asset_report_rows, dict):
                asset_report_rows = list(preloaded_asset_report_rows.get(advertiser_id) or [])
            elif isinstance(preloaded_asset_report_rows, list):
                asset_report_rows = list(preloaded_asset_report_rows)
            use_asset_rows = bool(asset_report_rows)
            if use_asset_rows:
                report_rows = asset_report_rows
                warning = None
            elif isinstance(preloaded_report_rows, dict):
                report_rows = list(preloaded_report_rows.get(advertiser_id) or [])
                warning = None
            elif isinstance(preloaded_report_rows, list):
                report_rows = list(preloaded_report_rows)
                warning = None
            else:
                report_rows, warning = _report_rows(
                    client,
                    advertiser_id,
                    start_date=start_date,
                    end_date=end_date,
                    page_size=report_page_size,
                    max_pages=max_pages,
                    max_rows=ad_limit,
                )
            if (use_asset_rows or preloaded_report_rows is not None) and ad_limit is not None and ad_limit > 0:
                report_rows = report_rows[:ad_limit]
        except Exception as exc:
            account_errors.append({"advertiser_id": advertiser_id, "error": str(exc)})
            continue
        if warning:
            account_warnings.append(warning)
            report_attribute_modes[advertiser_id] = str(warning.get("report_attribute_mode") or "fallback")
        elif use_asset_rows:
            report_attribute_modes[advertiser_id] = "preloaded_ad_id_v2_insights"
        elif preloaded_report_rows is not None:
            report_attribute_modes[advertiser_id] = "preloaded_ad_insights"
        else:
            report_attribute_modes[advertiser_id] = "full_attributes"

        spend_rows: list[dict[str, Any]] = []
        report_details: dict[str, dict[str, Any]] = {}
        for row in report_rows:
            ad_id = _report_ad_v2_id(row) if use_asset_rows else _report_ad_id(row)
            spend = _fnum(_metric(row, "spend"))
            if not ad_id or spend <= 0:
                continue
            report_detail = _report_asset_detail(row, ad_id) if use_asset_rows else _report_ad_detail(row, ad_id)
            report_details[ad_id] = report_detail
            if extract_landing_url(report_detail)[0]:
                report_direct_url_count += 1
            spend_rows.append(
                {
                    "advertiser_id": advertiser_id,
                    "ad_id": ad_id,
                    "spend": spend,
                    "impressions": _inum(_metric(row, "impressions")),
                    "clicks": _inum(_metric(row, "clicks")),
                    "conversions": _fnum(_metric(row, "conversion")),
                    "complete_payment": _fnum(_metric(row, "complete_payment")),
                    "revenue": _purchase_revenue(row, spend),
                    "raw_report_row": row,
                }
            )
        total_spend_ads.extend(spend_rows)

        detail_fetch_ids: list[str] = []
        for row in spend_rows:
            report_detail = report_details.get(row["ad_id"]) or {}
            campaign_id = str(report_detail.get("campaign_id") or "").strip()
            if campaign_id in skip_campaign_id_set:
                skipped_ad_detail_fetch_count += 1
                continue
            if extract_landing_url(report_detail)[0]:
                skipped_ad_detail_fetch_count += 1
                continue
            if use_asset_rows and _should_try_smart_plus_detail(report_detail):
                skipped_ad_detail_fetch_count += 1
                continue
            detail_fetch_ids.append(row["ad_id"])

        details = dict(report_details)
        ad_detail_fetch_count += len(detail_fetch_ids)
        fetched_details = _list_ads_by_ids(
            client,
            advertiser_id,
            detail_fetch_ids,
            smart_plus=smart_plus,
            cache=ad_detail_cache,
        )
        ad_detail_fetched_count += len(fetched_details)
        for ad_id, fetched_detail in fetched_details.items():
            details[ad_id] = {**details.get(ad_id, {}), **fetched_detail}
        if not smart_plus:
            legacy_campaign_ids = sorted(
                {
                    str(ad_detail.get("campaign_id") or "").strip()
                    for ad_detail in details.values()
                    if _is_legacy_smart_plus(ad_detail) and str(ad_detail.get("campaign_id") or "").strip()
                }
            )
            _list_campaign_spc_details(client, advertiser_id, legacy_campaign_ids, cache=campaign_spc_cache)
        smart_details_by_candidate: dict[str, dict[str, Any]] = {}
        if not smart_plus:
            upgraded_candidate_ids: list[str] = []
            for row in spend_rows:
                ad_detail = details.get(row["ad_id"]) or {}
                if str(ad_detail.get("campaign_id") or "").strip() in skip_campaign_id_set:
                    continue
                if _should_try_smart_plus_detail(ad_detail):
                    upgraded_candidate_ids.extend(_smart_plus_candidate_ids(row["ad_id"], ad_detail))
            smart_details_by_candidate = _list_upgraded_smart_plus_ads(
                client,
                advertiser_id,
                list(dict.fromkeys(upgraded_candidate_ids)),
                cache=ad_detail_cache,
            )
        merged_details: dict[str, dict[str, Any]] = {}
        for row in spend_rows:
            ad_detail = details.get(row["ad_id"]) or {}
            if ad_detail and not smart_plus and _should_try_smart_plus_detail(ad_detail):
                matched_candidate = None
                smart_detail = None
                for candidate_id in _smart_plus_candidate_ids(row["ad_id"], ad_detail):
                    smart_detail = smart_details_by_candidate.get(candidate_id)
                    if smart_detail:
                        matched_candidate = candidate_id
                        break
                if not smart_detail:
                    smart_detail = _matching_smart_plus_detail(ad_detail, smart_details_by_candidate)
                if smart_detail:
                    smart_plus_detail_count += 1
                    if matched_candidate and not smart_detail.get("smart_plus_ad_id") and matched_candidate != row["ad_id"]:
                        smart_detail = {**smart_detail, "smart_plus_ad_id": matched_candidate}
                    ad_detail = {
                        **ad_detail,
                        **smart_detail,
                        "_normal_detail": ad_detail,
                        "_detail_source": "smart_plus_ad_get",
                    }
            if ad_detail:
                merged_details[row["ad_id"]] = ad_detail
        adgroup_prefetch_ids: list[str] = []
        for row in spend_rows:
            ad_detail = merged_details.get(row["ad_id"]) or {}
            if not ad_detail:
                continue
            if str(ad_detail.get("campaign_id") or "").strip() in skip_campaign_id_set:
                continue
            direct_url, _ = extract_landing_url(ad_detail)
            if direct_url:
                continue
            adgroup_id = str(ad_detail.get("adgroup_id") or "").strip()
            if adgroup_id:
                adgroup_prefetch_ids.append(adgroup_id)
        _prefetch_adgroup_details(
            client,
            advertiser_id,
            adgroup_prefetch_ids,
            smart_plus=smart_plus,
            cache=adgroup_cache,
        )
        for row in spend_rows:
            ad_detail = merged_details.get(row["ad_id"]) or {}
            if not ad_detail:
                try:
                    ad_detail = client.get_ad(
                        advertiser_id,
                        row["ad_id"],
                        fields=DEFAULT_AD_FIELDS if not smart_plus else None,
                        smart_plus=smart_plus,
                    )
                except CliError:
                    try:
                        ad_detail = client.get_ad(advertiser_id, row["ad_id"], smart_plus=smart_plus)
                    except Exception as exc:  # pragma: no cover - depends on account permissions
                        ad_detail = {"ad_id": row["ad_id"], "_error": str(exc)}
                except Exception as exc:  # pragma: no cover - depends on account permissions
                    ad_detail = {"ad_id": row["ad_id"], "_error": str(exc)}
            if ad_detail:
                ad_detail.setdefault("_detail_source", "smart_plus_ad_get" if smart_plus else "ad_get")
            if str(ad_detail.get("campaign_id") or "").strip() in skip_campaign_id_set:
                skipped_url_probe_count += 1
                continue
            resolved = _resolve_landing_url(
                client,
                advertiser_id,
                ad_detail,
                smart_plus=smart_plus,
                adgroup_cache=adgroup_cache,
                campaign_spc_cache=campaign_spc_cache,
            )
            landing_url = resolved.get("url")
            spend = row["spend"]
            url_evidence = extract_url_evidence(ad_detail)
            upgraded_identity = _upgraded_smart_plus_identity(row["ad_id"], ad_detail)
            if upgraded_identity:
                upgraded_smart_plus_ad_count += 1
            if any(item.get("kind") == "creative_asset" for item in url_evidence):
                creative_url_ad_count += 1
            if any(item.get("kind") == "app_store" for item in url_evidence):
                app_store_url_ad_count += 1
            if not landing_url:
                no_url.append(_unresolved_ad_structure(row, resolved, spend))
                continue

            group = groups.setdefault(
                landing_url,
                {
                    "url": landing_url,
                    "url_source": resolved.get("url_source"),
                    "advertisers": set(),
                    "campaigns": set(),
                    "adgroups": set(),
                    "ad_count": 0,
                    "spend": 0.0,
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0.0,
                    "complete_payment": 0.0,
                    "revenue": 0.0,
                    "smart_plus_ad_count": 0,
                    "creative_url_ad_count": 0,
                    "app_store_url_ad_count": 0,
                    "ads": [],
                },
            )
            group["advertisers"].add(advertiser_id)
            group["campaigns"].add(ad_detail.get("campaign_name") or ad_detail.get("campaign_id") or "")
            group["adgroups"].add(ad_detail.get("adgroup_name") or ad_detail.get("adgroup_id") or "")
            group["ad_count"] += 1
            group["spend"] += spend
            group["impressions"] += row["impressions"]
            group["clicks"] += row["clicks"]
            group["conversions"] += row["conversions"]
            group["complete_payment"] += row["complete_payment"]
            group["revenue"] += row["revenue"]
            if upgraded_identity:
                group["smart_plus_ad_count"] += 1
            if any(item.get("kind") == "creative_asset" for item in url_evidence):
                group["creative_url_ad_count"] += 1
            if any(item.get("kind") == "app_store" for item in url_evidence):
                group["app_store_url_ad_count"] += 1
            group["ads"].append(
                {
                    "advertiser_id": advertiser_id,
                    "ad_id": row["ad_id"],
                    "ad_name": ad_detail.get("ad_name"),
                    "campaign_id": ad_detail.get("campaign_id"),
                    "campaign_name": ad_detail.get("campaign_name"),
                    "adgroup_id": ad_detail.get("adgroup_id"),
                    "adgroup_name": ad_detail.get("adgroup_name"),
                    "campaign_automation_type": ad_detail.get("campaign_automation_type"),
                    "smart_plus_ad_id": ad_detail.get("smart_plus_ad_id"),
                    "ad_id_v2": ad_detail.get("ad_id_v2") or ad_detail.get("smart_plus_ad_id") or ad_detail.get("upgraded_smart_plus_ad_id"),
                    "creative_id": upgraded_identity.get("creative_id") if upgraded_identity else ad_detail.get("creative_id"),
                    "upgraded_smart_plus_identity": upgraded_identity,
                    "url_source": resolved.get("url_source"),
                    "url_evidence": url_evidence,
                    "spend": round(spend, 2),
                    "impressions": row["impressions"],
                    "clicks": row["clicks"],
                    "conversions": row["conversions"],
                    "complete_payment": row["complete_payment"],
                    "revenue": round(row["revenue"], 2),
                    "roas": round(row["revenue"] / spend, 2) if spend else 0,
                }
            )

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        spend = group["spend"]
        impressions = group["impressions"]
        clicks = group["clicks"]
        conversions = group["conversions"]
        complete_payment = group["complete_payment"]
        revenue = group["revenue"]
        item = {
            "url": group["url"],
            "url_source": group["url_source"],
            "advertisers": sorted(group["advertisers"]),
            "ad_count": group["ad_count"],
            "campaign_count": len([campaign for campaign in group["campaigns"] if campaign]),
            "adgroup_count": len([adgroup for adgroup in group["adgroups"] if adgroup]),
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0,
            "cpc": round(spend / clicks, 2) if clicks else 0,
            "cpm": round(spend / impressions * 1000, 2) if impressions else 0,
            "conversions": conversions,
            "cpa": round(spend / conversions, 2) if conversions else 0,
            "complete_payment": complete_payment,
            "revenue": round(revenue, 2),
            "roas": round(revenue / spend, 2) if spend else 0,
            "smart_plus_ad_count": group["smart_plus_ad_count"],
            "creative_url_ad_count": group["creative_url_ad_count"],
            "app_store_url_ad_count": group["app_store_url_ad_count"],
            "top_ads": sorted(group["ads"], key=lambda ad: ad["spend"], reverse=True)[:3],
        }
        if include_ads:
            item["ads"] = sorted(group["ads"], key=lambda ad: ad["spend"], reverse=True)
        rows.append(item)

    rows.sort(key=lambda item: item["spend"], reverse=True)
    if top is not None and top > 0:
        rows = rows[:top]

    _enrich_product_rows(rows, enrich_product=enrich_product, product_limit=product_limit, product_scraper=product_scraper)

    total_revenue = sum(row["revenue"] for row in rows)
    total_spend = sum(row["spend"] for row in rows)
    no_url.sort(key=lambda item: item["spend"], reverse=True)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "advertiser_ids": advertiser_ids,
        "smart_plus": smart_plus,
        "ad_count": len(total_spend_ads),
        "group_count": len(rows),
        "no_url_count": len(no_url),
        "account_errors": account_errors,
        "account_warnings": account_warnings,
        "report_attribute_modes": report_attribute_modes,
        "adgroup_probe_count": len(adgroup_cache),
        "campaign_spc_probe_count": len(campaign_spc_cache),
        "skipped_url_probe_count": skipped_url_probe_count,
        "report_direct_url_count": report_direct_url_count,
        "ad_detail_fetch_count": ad_detail_fetch_count,
        "ad_detail_fetched_count": ad_detail_fetched_count,
        "skipped_ad_detail_fetch_count": skipped_ad_detail_fetch_count,
        "upgraded_smart_plus_ad_count": upgraded_smart_plus_ad_count,
        "smart_plus_detail_count": smart_plus_detail_count,
        "creative_url_ad_count": creative_url_ad_count,
        "app_store_url_ad_count": app_store_url_ad_count,
        "ad_limit": ad_limit,
        "ad_spend_total": round(sum(row["spend"] for row in total_spend_ads), 2),
        "total_spend": round(total_spend, 2),
        "total_revenue": round(total_revenue, 2),
        "overall_roas": round(total_revenue / total_spend, 2) if total_spend else 0,
        "no_url_spend": round(sum(row["spend"] for row in no_url), 2),
        "rows": rows,
        "no_url": no_url[:50],
    }
