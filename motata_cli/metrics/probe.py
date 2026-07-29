from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Callable

from .catalog import MetricGroup, MetricSpec


RequestFn = Callable[[MetricGroup, tuple[str, ...]], list[dict[str, Any]]]


_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:access_token|appsecret_proof|client_secret|refresh_token|token)=)[^&\s)]+")
_HEADER_SECRET_RE = re.compile(r"(?i)\b(Bearer|OAuth)\s+[A-Za-z0-9._~+/=-]+")
_NAMED_SECRET_RE = re.compile(
    r"(?i)(['\"]?(?:access_token|refresh_token|developer_token|api_key|secret_key|client_secret|appsecret_proof)['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])",
)
_ACCESS_TOKEN_HEADER_RE = re.compile(r"(?i)(Access-Token\s*[:=]\s*)[A-Za-z0-9._~+/=-]+")


def sanitize_error_message(message: str) -> str:
    """Redact credentials from exception strings before persisting probe results."""
    redacted = _QUERY_SECRET_RE.sub(r"\1<redacted>", message)
    redacted = _HEADER_SECRET_RE.sub(r"\1 <redacted>", redacted)
    redacted = _ACCESS_TOKEN_HEADER_RE.sub(r"\1<redacted>", redacted)
    redacted = _NAMED_SECRET_RE.sub(r"\1<redacted>\2", redacted)
    return redacted


def is_active_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "-":
            return False
        try:
            return float(text.replace(",", "")) != 0.0
        except ValueError:
            return True
    if isinstance(value, list):
        return any(is_active_value(item) for item in value)
    if isinstance(value, dict):
        if "value" in value:
            return is_active_value(value.get("value"))
        for key in ("values", "count", "count_value"):
            if key in value and is_active_value(value.get(key)):
                return True
        return any(is_active_value(child) for child in value.values())
    return bool(value)


def sample_active_value(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            if is_active_value(item):
                return item
        return value[:1]
    if isinstance(value, dict):
        return value
    return value


def metric_value_from_row(row: dict[str, Any], metric: str) -> Any:
    if metric in row:
        return row.get(metric)
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and metric in metrics:
        return metrics.get(metric)
    dimensions = row.get("dimensions")
    if isinstance(dimensions, dict) and metric in dimensions:
        return dimensions.get(metric)
    return None


def extract_metric_states(
    rows: list[dict[str, Any]],
    metrics: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        seen = False
        active_sample = None
        empty_sample = None
        for row in rows:
            value = metric_value_from_row(row, metric)
            if value is None:
                continue
            seen = True
            if is_active_value(value):
                active_sample = sample_active_value(value)
                break
            if empty_sample is None:
                empty_sample = value
        states[metric] = {
            "seen": seen,
            "active": active_sample is not None,
            "sample_value": active_sample if active_sample is not None else empty_sample,
        }
    return states


def summarize_by_category(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in entries:
        counts[str(item.get("category") or "uncategorized")] += 1
    return dict(sorted(counts.items()))


def probe_grouped_metrics(
    *,
    platform: str,
    groups: list[MetricGroup],
    specs: dict[str, MetricSpec],
    request_fn: RequestFn,
) -> dict[str, Any]:
    active: dict[str, dict[str, Any]] = {}
    empty: dict[str, dict[str, Any]] = {}
    unsupported: dict[str, dict[str, Any]] = {}
    stats = {
        "requests": 0,
        "successful_groups": 0,
        "split_count": 0,
        "fallback_count": 0,
        "failed_groups": 0,
    }

    def entry(metric: str, group: MetricGroup, state: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = specs.get(metric)
        payload = spec.to_dict() if spec else {"platform": platform, "metric": metric, "label": metric, "category": group.category}
        payload.update(
            {
                "source_request": {
                    "group": group.name,
                    "dimensions": list(group.dimensions),
                    "required_level": group.required_level,
                }
            }
        )
        if state is not None:
            payload["sample_value"] = state.get("sample_value")
        return payload

    def run(group: MetricGroup, metrics: tuple[str, ...]) -> None:
        try:
            stats["requests"] += 1
            rows = request_fn(group, metrics)
        except Exception as exc:
            if len(metrics) > 1:
                stats["split_count"] += 1
                midpoint = max(1, len(metrics) // 2)
                run(group, metrics[:midpoint])
                run(group, metrics[midpoint:])
                return
            stats["failed_groups"] += 1
            metric = metrics[0]
            unsupported[metric] = {
                **entry(metric, group),
                "reason": sanitize_error_message(str(exc)),
            }
            return

        stats["successful_groups"] += 1
        states = extract_metric_states(rows, metrics)
        for metric, state in states.items():
            if state["active"]:
                active[metric] = entry(metric, group, state)
                empty.pop(metric, None)
            elif metric not in active:
                empty[metric] = entry(metric, group, state)

    for group in groups:
        run(group, group.metrics)

    active_entries = sorted(active.values(), key=lambda item: (item.get("category") or "", item.get("metric") or ""))
    empty_entries = sorted(empty.values(), key=lambda item: (item.get("category") or "", item.get("metric") or ""))
    unsupported_entries = sorted(unsupported.values(), key=lambda item: (item.get("category") or "", item.get("metric") or ""))
    return {
        "active_metrics": active_entries,
        "supported_empty_metrics": empty_entries,
        "unsupported_metrics": unsupported_entries,
        "request_stats": stats,
        "summary": {
            "active_by_category": summarize_by_category(active_entries),
            "supported_empty_by_category": summarize_by_category(empty_entries),
            "unsupported_by_category": summarize_by_category(unsupported_entries),
        },
    }
