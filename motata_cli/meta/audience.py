from __future__ import annotations

from typing import Any

from motata_cli.audience import compute_segment_metrics, fnum, normalize_segment_value, summarize_breakdown_sections
from motata_cli.meta.landing_pages import (
    _ad_account_path,
    _insights_params_base,
    _paginate_insights,
    _time_params,
)


META_AUDIENCE_FIELDS = (
    "spend",
    "impressions",
    "clicks",
    "reach",
    "frequency",
    "actions",
    "action_values",
)

META_ACTION_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "purchase",
        (
            "purchase",
            "omni_purchase",
            "onsite_web_purchase",
            "onsite_web_app_purchase",
            "offsite_conversion.fb_pixel_purchase",
            "web_in_store_purchase",
            "web_app_in_store_purchase",
        ),
    ),
    (
        "complete_registration",
        (
            "complete_registration",
            "omni_complete_registration",
            "offsite_conversion.fb_pixel_complete_registration",
        ),
    ),
    (
        "subscribe",
        (
            "subscribe",
            "omni_subscribe",
            "offsite_conversion.fb_pixel_subscribe",
        ),
    ),
    (
        "lead",
        (
            "lead",
            "onsite_conversion.lead",
            "onsite_conversion.lead_grouped",
            "offsite_conversion.fb_pixel_lead",
        ),
    ),
    (
        "app_install",
        (
            "mobile_app_install",
            "omni_app_install",
            "app_install",
        ),
    ),
    (
        "landing_page_view",
        (
            "landing_page_view",
            "omni_landing_page_view",
        ),
    ),
)

META_BREAKDOWN_SPECS: dict[str, dict[str, Any]] = {
    "country": {"breakdowns": ("country",), "segment_keys": ("country",)},
    "age_gender": {"breakdowns": ("age", "gender"), "segment_keys": ("age", "gender")},
    "placement": {
        "breakdowns": ("publisher_platform", "platform_position"),
        "segment_keys": ("publisher_platform", "platform_position"),
        "fallbacks": (("publisher_platform",),),
    },
    "device": {"breakdowns": ("device_platform",), "segment_keys": ("device_platform",)},
}


def _action_value(actions: Any, action_types: tuple[str, ...]) -> float:
    if not isinstance(actions, list):
        return 0.0
    values = [fnum(item.get("value")) for item in actions if isinstance(item, dict) and item.get("action_type") in action_types]
    # Meta frequently reports the same event through multiple aliases. Max avoids double counting.
    return max(values) if values else 0.0


def _primary_result(row: dict[str, Any]) -> tuple[str, float]:
    actions = row.get("actions")
    for label, action_types in META_ACTION_FAMILIES:
        value = _action_value(actions, action_types)
        if value:
            return label, value
    return "none", 0.0


def _primary_value(row: dict[str, Any]) -> float:
    action_values = row.get("action_values")
    value = _action_value(
        action_values,
        (
            "purchase",
            "omni_purchase",
            "onsite_web_purchase",
            "onsite_web_app_purchase",
            "offsite_conversion.fb_pixel_purchase",
            "web_in_store_purchase",
            "web_app_in_store_purchase",
        ),
    )
    if value:
        return value
    roas = row.get("purchase_roas") or row.get("website_purchase_roas")
    if isinstance(roas, list):
        values = [fnum(item.get("value")) for item in roas if isinstance(item, dict)]
        return max(values) if values else 0.0
    return 0.0


def _segment_label(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    values = [normalize_segment_value(row.get(key)) for key in keys]
    return " / ".join(values)


def _normalize_rows(rows: list[dict[str, Any]], segment_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    result_labels: dict[str, float] = {}
    for row in rows:
        result_label, result = _primary_result(row)
        result_labels[result_label] = result_labels.get(result_label, 0.0) + result
        normalized.append(
            {
                "segment": _segment_label(row, segment_keys),
                "dimension_values": {key: normalize_segment_value(row.get(key)) for key in segment_keys},
                "spend": fnum(row.get("spend")),
                "impressions": fnum(row.get("impressions")),
                "clicks": fnum(row.get("clicks")),
                "result": result,
                "result_type": result_label,
                "value": _primary_value(row),
                "reach": fnum(row.get("reach")),
                "frequency": fnum(row.get("frequency")),
            }
        )
    dominant = sorted(result_labels.items(), key=lambda item: item[1], reverse=True)
    for item in normalized:
        item["dominant_result_type"] = dominant[0][0] if dominant else "none"
    return normalized


def build_meta_audience_breakdown(
    meta: Any,
    *,
    account_id: str,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    insight_limit: int = 500,
    top: int | None = 20,
    breakdowns: list[str] | None = None,
    async_insights: bool = False,
    auto_async_insights: bool = True,
) -> dict[str, Any]:
    selected = breakdowns or list(META_BREAKDOWN_SPECS)
    sections: dict[str, Any] = {}
    request_stats = {"requests": 0, "fallback_count": 0, "failed_breakdowns": 0}
    warnings: list[str] = []

    for name in selected:
        spec = META_BREAKDOWN_SPECS.get(name)
        if not spec:
            sections[name] = {"status": "skipped", "reason": f"unknown breakdown: {name}"}
            continue
        candidate_breakdowns = [tuple(spec["breakdowns"]), *[tuple(item) for item in spec.get("fallbacks") or ()]]
        last_error: str | None = None
        for index, candidate in enumerate(candidate_breakdowns):
            request_stats["requests"] += 1
            params = {
                **_insights_params_base(attribution=True),
                **_time_params(since=since, until=until, date_preset=date_preset),
                "level": "account",
                "fields": ",".join(META_AUDIENCE_FIELDS),
                "breakdowns": ",".join(candidate),
                "limit": insight_limit,
                "action_breakdowns": "action_type",
            }
            try:
                rows = _paginate_insights(
                    meta,
                    f"{_ad_account_path(account_id)}/insights",
                    params=params,
                    prefer_async=async_insights,
                    auto_async=auto_async_insights,
                )
            except Exception as exc:  # pragma: no cover - external API surface
                last_error = str(exc)
                if index < len(candidate_breakdowns) - 1:
                    request_stats["fallback_count"] += 1
                    continue
                request_stats["failed_breakdowns"] += 1
                sections[name] = {"status": "failed", "breakdowns": list(candidate), "error": last_error}
                break
            normalized = _normalize_rows(rows, tuple(candidate))
            computed = compute_segment_metrics(normalized, top=top)
            sections[name] = {
                "status": "ok",
                "breakdowns": list(candidate),
                "row_count": len(rows),
                "result_type": (normalized[0].get("dominant_result_type") if normalized else "none"),
                **computed,
            }
            if index:
                warnings.append(f"{name}: used fallback breakdown {','.join(candidate)}")
            break

    summary = summarize_breakdown_sections(sections)
    summary["measurement_warnings"].extend(warnings)
    return {
        "platform": "meta",
        "account_ids": [str(account_id).replace("act_", "")],
        "date_range": {"since": since, "until": until, "date_preset": date_preset},
        "strategy": "audience_breakdown",
        "coverage": {"full_account_coverage": True},
        "requested_breakdowns": selected,
        "sections": sections,
        "summary": summary,
        "request_stats": request_stats,
        "limits": [
            "Read-only Meta Insights breakdown analysis.",
            "Segment result uses the first non-zero action family in priority order; it is a platform proxy, not first-party revenue or LTV.",
        ],
    }
