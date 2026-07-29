from __future__ import annotations

from typing import Any

from .probe import is_active_value


CORE_METRIC_ORDER = ("impressions", "clicks", "spend", "conversion", "revenue")

CORE_METRIC_CANDIDATES: dict[str, dict[str, tuple[str, ...]]] = {
    "meta": {
        "impressions": ("impressions",),
        "clicks": ("clicks", "inline_link_clicks", "outbound_clicks", "website_clicks"),
        "spend": ("spend",),
        "conversion": ("actions", "results", "objective_results", "conversions", "total_actions"),
        "revenue": (
            "action_values",
            "conversion_values",
            "total_action_value",
            "purchase_roas",
            "website_purchase_roas",
            "mobile_app_purchase_roas",
        ),
    },
    "tiktok": {
        "impressions": ("impressions",),
        "clicks": ("clicks",),
        "spend": ("spend",),
        "conversion": (
            "conversion",
            "result",
            "real_time_conversion",
            "app_install",
            "registration",
            "total_registration",
            "purchase",
            "total_purchase",
            "subscribe",
            "complete_payment",
            "skan_app_install",
            "skan_purchase",
        ),
        "revenue": (
            "total_purchase_value",
            "total_subscribe_value",
            "onsite_total_purchase_value",
            "onsite_total_subscribe_value",
            "onsite_total_ad_impression_value",
            "onsite_shopping_roas",
            "vta_complete_payment_roas",
            "onsite_ad_impression_ad_revenue_day0",
            "onsite_ad_impression_ad_revenue_day1",
            "onsite_ad_impression_ad_revenue_day2",
            "onsite_ad_impression_ad_revenue_day3",
            "onsite_ad_impression_ad_revenue_day4",
            "onsite_ad_impression_ad_revenue_day5",
            "onsite_ad_impression_ad_revenue_day6",
            "onsite_ad_impression_ad_revenue_day13",
            "total_onsite_shopping_value",
            "shop_gross_revenue_by_order_submission",
            "custom_app_events_value",
            "skan_total_purchase_value",
            "complete_payment_roas",
            "value_per_complete_payment",
        ),
    },
}


def _metric_value(row: dict[str, Any], metric: str) -> Any:
    if metric in row:
        return row.get(metric)
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and metric in metrics:
        return metrics.get(metric)
    summary = row.get("summary")
    if isinstance(summary, dict) and metric in summary:
        return summary.get(metric)
    return None


def _entry_metric(item: Any) -> str | None:
    if isinstance(item, dict) and item.get("metric"):
        return str(item["metric"])
    if isinstance(item, str):
        return item
    return None


def _metric_set(payload: dict[str, Any] | None, key: str) -> set[str]:
    if not payload:
        return set()
    values: set[str] = set()
    for item in payload.get(key) or []:
        metric = _entry_metric(item)
        if metric:
            values.add(metric)
    return values


def _probe_metric_sets(probe: dict[str, Any] | None, platform: str) -> tuple[set[str], set[str], set[str]]:
    active: set[str] = set()
    empty: set[str] = set()
    unsupported: set[str] = set()
    if not probe:
        return active, empty, unsupported

    payloads = probe.get("results") if isinstance(probe.get("results"), list) else [probe]
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        payload_platform = str(payload.get("platform") or platform).lower()
        if payload_platform != platform:
            continue
        active.update(_metric_set(payload, "active_metrics"))
        empty.update(_metric_set(payload, "supported_empty_metrics"))
        unsupported.update(_metric_set(payload, "unsupported_metrics"))
    return active, empty, unsupported


def build_core_metric_coverage(
    *,
    platform: str,
    rows: list[dict[str, Any]] | None = None,
    totals: dict[str, Any] | None = None,
    probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether the five non-negotiable analysis metrics have usable data.

    The concepts are platform neutral, while candidates are platform API fields.
    Revenue is intentionally strict: zero/empty revenue remains a data gap even
    when spend and conversions exist.
    """

    normalized_platform = platform.lower()
    candidates_by_metric = CORE_METRIC_CANDIDATES.get(normalized_platform, {})
    active_probe, empty_probe, unsupported_probe = _probe_metric_sets(probe, normalized_platform)
    data_rows = list(rows or [])
    if totals:
        data_rows.insert(0, totals)

    status_by_metric: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    for concept in CORE_METRIC_ORDER:
        candidates = candidates_by_metric.get(concept, (concept,))
        evidence: list[dict[str, Any]] = []
        empty_seen: set[str] = set()
        unsupported_seen: set[str] = set()

        for candidate in candidates:
            for row in data_rows:
                value = _metric_value(row, candidate)
                if value is None:
                    continue
                if is_active_value(value):
                    evidence.append({"metric": candidate, "source": "report", "sample_value": value})
                    break
                empty_seen.add(candidate)
            if candidate in active_probe:
                evidence.append({"metric": candidate, "source": "probe"})
            if candidate in empty_probe:
                empty_seen.add(candidate)
            if candidate in unsupported_probe:
                unsupported_seen.add(candidate)

        if evidence:
            status = "active"
        elif unsupported_seen and unsupported_seen.issuperset(set(candidates)):
            status = "unsupported"
        elif empty_seen:
            status = "empty"
        else:
            status = "missing"

        if status != "active":
            missing.append(concept)

        status_by_metric[concept] = {
            "status": status,
            "candidate_metrics": list(candidates),
            "evidence": evidence,
            "empty_metrics": sorted(empty_seen),
            "unsupported_metrics": sorted(unsupported_seen),
        }

    return {
        "required_core_metrics": list(CORE_METRIC_ORDER),
        "status_by_metric": status_by_metric,
        "missing_core_metrics": missing,
        "complete": not missing,
        "needs_full_probe": bool(missing),
        "probe_recommendation": (
            "Run a full metric probe and merge active revenue/conversion candidates before final analysis."
            if missing
            else None
        ),
    }
