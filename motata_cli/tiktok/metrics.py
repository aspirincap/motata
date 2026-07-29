from __future__ import annotations

from typing import Any

from motata_cli.metrics.catalog import TIKTOK_GROUPS, TIKTOK_METRIC_MAP, MetricGroup
from motata_cli.metrics.core import build_core_metric_coverage
from motata_cli.metrics.probe import is_active_value, probe_grouped_metrics
from motata_cli.tiktok.app_discovery import discover_recent_spend_advertisers
from motata_cli.tiktok.landing_pages import _extract_collection, _fnum, _metric, default_date_range


BASE_TRAFFIC_METRICS = ("spend", "impressions", "clicks")
TIKTOK_PROBE_PROFILES: dict[str, tuple[str, ...]] = {
    "light": ("core",),
    "batch": (
        "core",
        "real_time_conversion",
        "video",
        "app_events",
        "skan",
        "san",
        "attributes_advertiser",
        "attributes_campaign",
        "attributes_adgroup",
    ),
    "vertical": (
        "core",
        "real_time_conversion",
        "engagement_social",
        "video",
        "app_events",
        "skan",
        "san",
        "attributes_advertiser",
        "attributes_campaign",
        "attributes_adgroup",
        "attributes_ad",
    ),
    "full": tuple(group.name for group in TIKTOK_GROUPS),
}


def _traffic_check(client: Any, advertiser_id: str, *, start_date: str, end_date: str) -> dict[str, Any]:
    response = client.integrated_report(
        "BASIC",
        advertiser_id=advertiser_id,
        data_level="AUCTION_ADVERTISER",
        dimensions=["advertiser_id"],
        metrics=list(BASE_TRAFFIC_METRICS),
        start_date=start_date,
        end_date=end_date,
        page=1,
        page_size=1,
    )
    row = (_extract_collection(response, "list") or [{}])[0]
    values = {metric: _metric(row, metric) for metric in BASE_TRAFFIC_METRICS}
    return {
        "spend": _fnum(values.get("spend")),
        "impressions": _fnum(values.get("impressions")),
        "clicks": _fnum(values.get("clicks")),
        "has_traffic": any(is_active_value(value) for value in values.values()),
    }


def build_tiktok_metric_probe(
    client: Any,
    *,
    advertiser_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    advertiser_limit: int | None = 10,
    page_size: int = 5,
    group_names: list[str] | None = None,
    profile: str = "full",
) -> dict[str, Any]:
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    discovery_errors: list[dict[str, Any]] = []
    fast_sampling = not advertiser_ids
    if advertiser_ids:
        advertisers = [{"advertiser_id": advertiser_id} for advertiser_id in advertiser_ids]
    else:
        advertisers, discovery_errors = discover_recent_spend_advertisers(
            client,
            start_date=start_date,
            end_date=end_date,
            advertiser_limit=advertiser_limit,
            page_size=1000,
            max_pages=1,
        )

    results: list[dict[str, Any]] = []
    for advertiser in advertisers:
        advertiser_id = str(advertiser.get("advertiser_id") or "")
        traffic = _traffic_check(client, advertiser_id, start_date=start_date, end_date=end_date)
        request_stats_prefix = {"traffic_check_requests": 1}
        if not traffic["has_traffic"]:
            results.append(
                {
                    "platform": "tiktok",
                    "advertiser_ids": [advertiser_id],
                    "date_range": {"start_date": start_date, "end_date": end_date},
                    "strategy": "grouped_batch",
                    "probe_profile": "custom" if group_names else profile,
                    "coverage": {"fast_recent_spend_sampling": fast_sampling, "full_advertiser_coverage": bool(advertiser_ids)},
                    "traffic_check": traffic,
                    "core_metric_coverage": build_core_metric_coverage(platform="tiktok", totals=traffic),
                    "active_metrics": [],
                    "supported_empty_metrics": [],
                    "unsupported_metrics": [],
                    "request_stats": {**request_stats_prefix, "requests": 1, "successful_groups": 0, "split_count": 0, "fallback_count": 0, "failed_groups": 0},
                    "summary": {
                        "active_by_category": {},
                        "supported_empty_by_category": {},
                        "unsupported_by_category": {},
                        "notes": ["No non-zero traffic metrics found; skipped heavier metric groups."],
                    },
                }
            )
            continue

        def request_group(group: MetricGroup, metrics: tuple[str, ...]) -> list[dict[str, Any]]:
            response = client.integrated_report(
                "BASIC",
                advertiser_id=advertiser_id,
                data_level=group.required_level or "AUCTION_ADVERTISER",
                dimensions=list(group.dimensions or ("advertiser_id",)),
                metrics=list(metrics),
                start_date=start_date,
                end_date=end_date,
                page=1,
                page_size=page_size,
                order_field="spend",
                order_type="DESC",
            )
            return _extract_collection(response, "list")

        selected_group_names = set(group_names or TIKTOK_PROBE_PROFILES.get(profile, TIKTOK_PROBE_PROFILES["full"]))
        groups = [group for group in TIKTOK_GROUPS if group.name in selected_group_names]
        skipped_groups = [group.name for group in TIKTOK_GROUPS if group.name not in selected_group_names]
        probe = probe_grouped_metrics(
            platform="tiktok",
            groups=groups,
            specs=TIKTOK_METRIC_MAP,
            request_fn=request_group,
        )
        probe["platform"] = "tiktok"
        probe["advertiser_ids"] = [advertiser_id]
        probe["date_range"] = {"start_date": start_date, "end_date": end_date}
        probe["strategy"] = "grouped_batch"
        probe["probe_profile"] = "custom" if group_names else profile
        probe["skipped_groups"] = skipped_groups
        probe["coverage"] = {
            "fast_recent_spend_sampling": fast_sampling,
            "full_advertiser_coverage": bool(advertiser_ids),
            "note": "Auto-discovery samples recent-spend advertisers only." if fast_sampling else None,
        }
        probe["traffic_check"] = traffic
        probe["core_metric_coverage"] = build_core_metric_coverage(platform="tiktok", totals=traffic, probe=probe)
        probe["request_stats"] = {**probe["request_stats"], **request_stats_prefix, "requests": probe["request_stats"]["requests"] + 1}
        results.append(probe)

    if len(results) == 1:
        result = results[0]
        result["discovery_errors"] = discovery_errors
        return result
    return {
        "platform": "tiktok",
        "advertiser_ids": [str(item.get("advertiser_id")) for item in advertisers],
        "date_range": {"start_date": start_date, "end_date": end_date},
        "strategy": "grouped_batch",
        "probe_profile": "custom" if group_names else profile,
        "coverage": {"fast_recent_spend_sampling": fast_sampling, "full_advertiser_coverage": bool(advertiser_ids)},
        "results": results,
        "discovery_errors": discovery_errors,
        "summary": {
            "advertiser_count": len(results),
            "active_metric_count": sum(len(item.get("active_metrics") or []) for item in results),
            "unsupported_metric_count": sum(len(item.get("unsupported_metrics") or []) for item in results),
        },
    }
