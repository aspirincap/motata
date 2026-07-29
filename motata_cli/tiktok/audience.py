from __future__ import annotations

from typing import Any

from motata_cli.audience import compute_segment_metrics, fnum, normalize_segment_value, summarize_breakdown_sections
from motata_cli.tiktok.landing_pages import _extract_collection, default_date_range


TIKTOK_AUDIENCE_BASE_METRICS = (
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
)

TIKTOK_AUDIENCE_VALUE_METRICS = (
    "total_purchase_value",
    "total_active_pay_roas",
)

TIKTOK_AUDIENCE_METRICS = (*TIKTOK_AUDIENCE_BASE_METRICS, *TIKTOK_AUDIENCE_VALUE_METRICS)

TIKTOK_BREAKDOWN_SPECS: dict[str, dict[str, Any]] = {
    "country": {
        "report_type": "AUDIENCE",
        "candidates": (
            ("advertiser_id", "country_code"),
            ("advertiser_id", "country"),
        )
    },
    "age_gender": {
        "report_type": "AUDIENCE",
        "candidates": (
            ("advertiser_id", "age", "gender"),
            ("advertiser_id", "age"),
            ("advertiser_id", "gender"),
        )
    },
    "placement": {
        "report_type": "AUDIENCE",
        "candidates": (
            ("advertiser_id", "placement"),
            ("advertiser_id", "placement_type"),
            ("advertiser_id", "site_id"),
        )
    },
    "device": {
        "report_type": "AUDIENCE",
        "candidates": (
            ("advertiser_id", "platform"),
            ("advertiser_id", "device_platform"),
            ("advertiser_id", "device_os"),
            ("advertiser_id", "operating_system"),
        )
    },
}


def _metric(row: dict[str, Any], metric: str) -> Any:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        return metrics.get(metric)
    return row.get(metric)


def _dimensions(row: dict[str, Any]) -> dict[str, Any]:
    dimensions = row.get("dimensions")
    return dimensions if isinstance(dimensions, dict) else {}


def _result(row: dict[str, Any]) -> tuple[str, float]:
    for metric in ("conversion", "result"):
        value = fnum(_metric(row, metric))
        if value:
            return metric, value
    return "none", 0.0


def _value(row: dict[str, Any]) -> float:
    for metric in ("total_purchase_value",):
        value = fnum(_metric(row, metric))
        if value:
            return value
    spend = fnum(_metric(row, "spend"))
    for metric in ("total_active_pay_roas",):
        roas = fnum(_metric(row, metric))
        if spend and roas:
            return spend * roas
    return 0.0


def _segment_label(row: dict[str, Any], dimensions: tuple[str, ...]) -> str:
    values = []
    row_dimensions = _dimensions(row)
    for dimension in dimensions:
        if dimension == "advertiser_id":
            continue
        values.append(normalize_segment_value(row_dimensions.get(dimension) or row.get(dimension)))
    return " / ".join(values) if values else "all"


def _normalize_rows(rows: list[dict[str, Any]], dimensions: tuple[str, ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    result_labels: dict[str, float] = {}
    segment_dimensions = tuple(dimension for dimension in dimensions if dimension != "advertiser_id")
    for row in rows:
        result_label, result = _result(row)
        result_labels[result_label] = result_labels.get(result_label, 0.0) + result
        row_dimensions = _dimensions(row)
        normalized.append(
            {
                "segment": _segment_label(row, dimensions),
                "dimension_values": {
                    dimension: normalize_segment_value(row_dimensions.get(dimension) or row.get(dimension))
                    for dimension in segment_dimensions
                },
                "spend": fnum(_metric(row, "spend")),
                "impressions": fnum(_metric(row, "impressions")),
                "clicks": fnum(_metric(row, "clicks")),
                "result": result,
                "result_type": result_label,
                "value": _value(row),
                "cost_per_conversion": fnum(_metric(row, "cost_per_conversion")),
                "cost_per_result": fnum(_metric(row, "cost_per_result")),
            }
        )
    dominant = sorted(result_labels.items(), key=lambda item: item[1], reverse=True)
    for item in normalized:
        item["dominant_result_type"] = dominant[0][0] if dominant else "none"
    return normalized


def build_tiktok_audience_breakdown(
    client: Any,
    *,
    advertiser_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page_size: int = 500,
    top: int | None = 20,
    breakdowns: list[str] | None = None,
    data_level: str = "AUCTION_ADVERTISER",
) -> dict[str, Any]:
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    selected = breakdowns or list(TIKTOK_BREAKDOWN_SPECS)
    sections: dict[str, Any] = {}
    request_stats = {"requests": 0, "fallback_count": 0, "failed_breakdowns": 0}

    for name in selected:
        spec = TIKTOK_BREAKDOWN_SPECS.get(name)
        if not spec:
            sections[name] = {"status": "skipped", "reason": f"unknown breakdown: {name}"}
            continue
        report_type = str(spec.get("report_type") or "AUDIENCE")
        last_error: str | None = None
        for index, dimensions in enumerate(tuple(tuple(item) for item in spec["candidates"])):
            request_stats["requests"] += 1
            try:
                response = client.integrated_report(
                    report_type,
                    advertiser_id=advertiser_id,
                    data_level=data_level,
                    dimensions=list(dimensions),
                    metrics=list(TIKTOK_AUDIENCE_METRICS),
                    start_date=start_date,
                    end_date=end_date,
                    page=1,
                    page_size=page_size,
                    order_field="spend",
                    order_type="DESC",
                )
                rows = _extract_collection(response, "list")
            except Exception as exc:  # pragma: no cover - external API surface
                last_error = str(exc)
                try:
                    response = client.integrated_report(
                        report_type,
                        advertiser_id=advertiser_id,
                        data_level=data_level,
                        dimensions=list(dimensions),
                        metrics=list(TIKTOK_AUDIENCE_BASE_METRICS),
                        start_date=start_date,
                        end_date=end_date,
                        page=1,
                        page_size=page_size,
                        order_field="spend",
                        order_type="DESC",
                    )
                    rows = _extract_collection(response, "list")
                except Exception as fallback_exc:  # pragma: no cover - external API surface
                    last_error = str(fallback_exc)
                    if index < len(spec["candidates"]) - 1:
                        request_stats["fallback_count"] += 1
                        continue
                    request_stats["failed_breakdowns"] += 1
                    sections[name] = {"status": "failed", "dimensions": list(dimensions), "error": last_error}
                    break
            normalized = _normalize_rows(rows, dimensions)
            computed = compute_segment_metrics(normalized, top=top)
            sections[name] = {
                "status": "ok",
                "report_type": report_type,
                "dimensions": list(dimensions),
                "row_count": len(rows),
                "result_type": (normalized[0].get("dominant_result_type") if normalized else "none"),
                **computed,
            }
            break

    return {
        "platform": "tiktok",
        "advertiser_ids": [str(advertiser_id)],
        "date_range": {"start_date": start_date, "end_date": end_date},
        "strategy": "audience_breakdown",
        "coverage": {"full_advertiser_coverage": True},
        "requested_breakdowns": selected,
        "sections": sections,
        "summary": summarize_breakdown_sections(sections),
        "request_stats": request_stats,
        "limits": [
            "Read-only TikTok integrated report breakdown analysis.",
            "TikTok audience dimensions are account/objective/permission gated; unsupported dimensions are reported with fallback or failure reason.",
        ],
    }
