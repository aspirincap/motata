from __future__ import annotations

from collections import Counter
import csv
import io
import json
import re
import sys
import time
from typing import Any


CHANGELOG_CREATE_ENDPOINT = "changelog/task/create/"
CHANGELOG_CHECK_ENDPOINT = "changelog/task/check/"


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("list", "logs", "operation_logs", "activities", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        changelog_rows = _extract_changelog_rows(data.get("changelog"))
        if changelog_rows:
            return changelog_rows
    value = payload.get("list")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _decode_changelog_file_data(file_data: Any) -> str:
    if isinstance(file_data, bytes):
        return file_data.decode("utf-8-sig", errors="replace")
    text = str(file_data or "")
    if (text.startswith("b'") and text.endswith("'")) or (text.startswith('b"') and text.endswith('"')):
        text = text[2:-1]
    if "\\r\\n" in text and "\r\n" not in text:
        text = text.encode("utf-8").decode("unicode_escape")
    return text


def _extract_changelog_rows(changelog: Any) -> list[dict[str, Any]]:
    if not changelog:
        return []
    payload: Any = changelog
    if isinstance(changelog, str):
        try:
            payload = json.loads(changelog)
        except json.JSONDecodeError:
            payload = {"file_data": changelog}
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    file_data = payload.get("file_data") or payload.get("csv") or payload.get("content")
    if not file_data:
        return []
    text = _decode_changelog_file_data(file_data)
    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines):
        if line.startswith("Time,") or ("Activity details" in line and "Object ID" in line):
            header_index = index
            break
    if header_index is None:
        return []

    csv_text = "\n".join(lines[header_index:])
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        if not any(value not in (None, "") for value in row.values()):
            continue
        clean_row = {str(key or "").strip(): value for key, value in row.items() if key}
        file_name = payload.get("file_name")
        if file_name:
            clean_row["_file_name"] = file_name
        rows.append(clean_row)
    return rows


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "unknown"


def summarize_tiktok_activities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    operation_types = Counter(
        _first_text(row, ("operation_type", "operation", "action", "event_type", "activity_type", "Object"))
        for row in rows
    )
    object_types = Counter(
        _first_text(
            row,
            ("object_type", "module", "object_category", "resource_type", "entity_type", "log_object_type"),
        )
        for row in rows
    )
    operators = Counter(
        _first_text(row, ("operator", "operator_name", "user_name", "email", "actor_name", "Operator"))
        for row in rows
    )
    return {
        "activity_count": len(rows),
        "top_operation_types": [
            {"operation_type": key, "count": value} for key, value in operation_types.most_common(10)
        ],
        "top_object_types": [{"object_type": key, "count": value} for key, value in object_types.most_common(10)],
        "top_operators": [{"operator": key, "count": value} for key, value in operators.most_common(10)],
    }


def _extract_task_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    candidates: list[Any] = [payload.get("task_id"), payload.get("task_ids")]
    if isinstance(data, dict):
        candidates.extend([data.get("task_id"), data.get("task_ids")])
        task = data.get("task")
        if isinstance(task, dict):
            candidates.append(task.get("task_id"))
    for value in candidates:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_status(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    candidates: list[Any] = [payload.get("status"), payload.get("task_status")]
    if isinstance(data, dict):
        candidates.extend([data.get("status"), data.get("task_status")])
        task = data.get("task")
        if isinstance(task, dict):
            candidates.extend([task.get("status"), task.get("task_status")])
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _is_completed(status: str | None) -> bool:
    normalized = str(status or "").strip().upper()
    return normalized in {"SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED", "DONE"}


def _is_failed(status: str | None) -> bool:
    normalized = str(status or "").strip().upper()
    return normalized in {"FAIL", "FAILED", "ERROR", "EXPIRED", "CANCELED", "CANCELLED"}


def _progress(message: str) -> None:
    print(f"[motata tiktok] {message}", file=sys.stderr)


def _as_changelog_time(value: str | None, *, end_of_day: bool = False) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text} {'23:59:59' if end_of_day else '00:00:00'}"
    return text


def _split_list_values(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        items.extend(part for part in re.split(r"[,\s]+", str(value).strip()) if part)
    return items


def build_tiktok_activities_report(
    client: Any,
    *,
    advertiser_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    timezone: str | None = None,
    module: str | None = None,
    object_type: str | None = None,
    object_ids: list[str] | None = None,
    operation_types: list[str] | None = None,
    order_fields: list[str] | None = None,
    page_size: int = 100,
    wait: bool = True,
    timeout_seconds: int = 60,
    poll_seconds: float = 5.0,
) -> dict[str, Any]:
    payload = {
        "advertiser_id": str(advertiser_id),
        "start_time": _as_changelog_time(start_date),
        "end_time": _as_changelog_time(end_date, end_of_day=True),
        "page_size": page_size,
    }
    if timezone:
        payload["timezone"] = timezone
    if module:
        payload["module"] = module
    if object_type:
        payload["object_type"] = object_type
    if object_ids:
        payload["object_ids"] = _split_list_values(object_ids)
    if operation_types:
        payload["operation_types"] = _split_list_values(operation_types)
    if order_fields:
        payload["order_fields"] = _split_list_values(order_fields)
    create_response = client.create_changelog_task({key: value for key, value in payload.items() if value not in (None, "")})
    task_id = _extract_task_id(create_response)
    checks: list[dict[str, Any]] = []
    rows = _extract_rows(create_response)
    status = _extract_status(create_response)
    download_response: dict[str, Any] | None = None

    if task_id and wait and not rows:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            check_response = client.check_changelog_task(str(advertiser_id), task_id)
            status = _extract_status(check_response)
            check_rows = _extract_rows(check_response)
            checks.append({"status": status, "row_count": len(check_rows)})
            _progress(
                "changelog task polling "
                f"task_id={task_id} "
                f"status={status or 'unknown'} "
                f"rows={len(check_rows)}"
            )
            if check_rows:
                rows = check_rows
            if _is_completed(status):
                break
            if _is_failed(status):
                break
            time.sleep(poll_seconds)

    if task_id and wait and _is_completed(status) and not rows and hasattr(client, "download_changelog_task"):
        download_response = client.download_changelog_task(str(advertiser_id), task_id)
        rows = _extract_rows(download_response)
        download_status = _extract_status(download_response)
        if download_status:
            status = download_status
        checks.append({"status": status, "row_count": len(rows), "source": "download"})
        _progress(
            "changelog task download "
            f"task_id={task_id} "
            f"status={status or 'unknown'} "
            f"rows={len(rows)}"
        )

    if rows or not task_id:
        return {
            "platform": "tiktok",
            "advertiser_id": str(advertiser_id),
            "date_range": {"since": start_date, "until": end_date},
            "strategy": "changelog_task",
            "status": "success" if _is_completed(status) else ("failed" if _is_failed(status) else "pending"),
            "task_id": task_id,
            "task_status": status,
            "create_response": create_response,
            "download_response": download_response,
            "checks": checks,
            "rows": rows,
            "summary": summarize_tiktok_activities(rows),
            "limits": {
                "page_size": page_size,
                "wait": wait,
                "timeout_seconds": timeout_seconds,
            },
        }

    return {
        "platform": "tiktok",
        "advertiser_id": str(advertiser_id),
        "date_range": {"since": start_date, "until": end_date},
        "strategy": "changelog_task",
        "status": "success" if _is_completed(status) else ("failed" if _is_failed(status) else "pending"),
        "task_id": task_id,
        "task_status": status,
        "create_response": create_response,
        "download_response": download_response,
        "checks": checks,
        "rows": [],
        "summary": summarize_tiktok_activities([]),
        "limits": {
            "page_size": page_size,
            "wait": wait,
            "timeout_seconds": timeout_seconds,
        },
    }
