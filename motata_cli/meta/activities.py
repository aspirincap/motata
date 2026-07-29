from __future__ import annotations

from collections import Counter
from typing import Any

from motata_cli.meta.landing_pages import _ad_account_path


DEFAULT_META_ACTIVITY_FIELDS = [
    "actor_id",
    "actor_name",
    "application_id",
    "application_name",
    "date_time_in_timezone",
    "event_time",
    "event_type",
    "extra_data",
    "object_id",
    "object_name",
    "object_type",
    "translated_event_type",
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def summarize_meta_activities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = Counter(_clean(row.get("event_type")) or "unknown" for row in rows)
    object_types = Counter(_clean(row.get("object_type")) or "unknown" for row in rows)
    actors = Counter(_clean(row.get("actor_name")) or _clean(row.get("actor_id")) or "unknown" for row in rows)
    return {
        "activity_count": len(rows),
        "top_event_types": [{"event_type": key, "count": value} for key, value in event_types.most_common(10)],
        "top_object_types": [{"object_type": key, "count": value} for key, value in object_types.most_common(10)],
        "top_actors": [{"actor": key, "count": value} for key, value in actors.most_common(10)],
    }


def build_meta_activities_report(
    meta: Any,
    *,
    account_id: str,
    since: str | None = None,
    until: str | None = None,
    fields: list[str] | None = None,
    limit: int = 100,
    max_pages: int = 1,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fields": ",".join(fields or DEFAULT_META_ACTIVITY_FIELDS),
        "limit": limit,
    }
    if since:
        params["since"] = since
    if until:
        params["until"] = until

    rows = meta.paginate(
        f"{_ad_account_path(account_id)}/activities",
        params=params,
        max_pages=max_pages,
    )
    return {
        "platform": "meta",
        "account_id": str(account_id).replace("act_", ""),
        "date_range": {"since": since, "until": until},
        "strategy": "ad_account_activities",
        "fields": fields or DEFAULT_META_ACTIVITY_FIELDS,
        "limits": {
            "limit": limit,
            "max_pages": max_pages,
        },
        "rows": rows,
        "summary": summarize_meta_activities(rows),
    }
