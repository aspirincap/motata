from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any


TARGET_LIMIT = 10


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _date_key(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def _fnum(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        return _fnum(value[0].get("value") if value and isinstance(value[0], dict) else 0)
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return 0.0


def _action_value(row: dict[str, Any], field: str, action_types: set[str]) -> float:
    for item in row.get(field) or []:
        if str(item.get("action_type")) in action_types:
            return _fnum(item.get("value"))
    return 0.0


def _meta_purchase(row: dict[str, Any]) -> float:
    return _action_value(row, "actions", {"purchase", "omni_purchase", "web_in_store_purchase"})


def _meta_revenue(row: dict[str, Any]) -> float:
    return _action_value(
        row,
        "action_values",
        {"purchase", "omni_purchase", "web_in_store_purchase", "offsite_conversion.fb_pixel_purchase"},
    )


def _meta_roas(row: dict[str, Any]) -> float:
    explicit = _fnum((row.get("purchase_roas") or row.get("website_purchase_roas") or [{}])[0].get("value"))
    if explicit:
        return explicit
    spend = _fnum(row.get("spend"))
    revenue = _meta_revenue(row)
    return revenue / spend if spend else 0.0


def _tt_metric(row: dict[str, Any], key: str) -> float:
    return _fnum((row.get("metrics") or {}).get(key))


def _tt_dimension(row: dict[str, Any], key: str) -> str:
    return _clean((row.get("dimensions") or {}).get(key))


def _tt_first_metric(row: dict[str, Any], keys: list[str]) -> float:
    for key in keys:
        value = _tt_metric(row, key)
        if value:
            return value
    return 0.0


def _tt_revenue(row: dict[str, Any]) -> float:
    return max(
        _tt_metric(row, "total_purchase_value"),
        _tt_metric(row, "complete_payment_value"),
        _tt_metric(row, "onsite_total_purchase_value"),
        _tt_metric(row, "shop_gross_revenue_by_order_submission"),
    )


def _tt_roas(row: dict[str, Any]) -> float:
    explicit = _tt_first_metric(row, ["complete_payment_roas", "total_active_pay_roas", "onsite_purchases_roas"])
    if explicit:
        return explicit
    spend = _tt_metric(row, "spend")
    revenue = _tt_revenue(row)
    return revenue / spend if spend else 0.0


def _normalize_level(platform: str, value: Any) -> str:
    text = _clean(value).lower().replace(" ", "_")
    adgroup_level = "adset" if platform == "meta" else "adgroup"
    aliases = {
        "campaign": "campaign",
        "campaigns": "campaign",
        "ad_set": "adset",
        "adset": "adset",
        "adsets": "adset",
        "ad_group": adgroup_level,
        "adgroup": adgroup_level,
        "adgroups": adgroup_level,
        "ad": "ad",
        "ads": "ad",
        "advertiser": "advertiser",
        "account": "advertiser" if platform == "tiktok" else "account",
    }
    return aliases.get(text, text or "unknown")


def _operation_weight(operation: str, details: str) -> int:
    text = f"{operation} {details}".lower()
    if any(token in text for token in ("budget", "bid", "cost cap", "roas", "spend")):
        return 7
    if any(token in text for token in ("status", "on/off", "pause", "enable", "disable", "active")):
        return 7
    if any(token in text for token in ("target", "audience", "location", "interest", "placement")):
        return 6
    if any(token in text for token in ("creative", "ad text", "material", "video", "image")):
        return 5
    if any(token in text for token in ("audit", "review", "reject", "approve")):
        return 5
    if any(token in text for token in ("create", "delete")):
        return 4
    return 2


def _parse_detail_summary(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return text[:180]
    items = parsed if isinstance(parsed, list) else [parsed]
    snippets: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name") or item.get("field"))
        action = _clean(item.get("action"))
        changes: list[str] = []
        for change in item.get("before_after") or item.get("changes") or []:
            if not isinstance(change, dict):
                continue
            before = _clean(change.get("before"))
            after = _clean(change.get("after"))
            if before or after:
                changes.append(f"{before or '-'} -> {after or '-'}")
        label = " ".join(part for part in (action, name) if part)
        if changes:
            label = f"{label}: {', '.join(changes[:2])}".strip(": ")
        if label:
            snippets.append(label)
        if len(snippets) >= 2:
            break
    return "；".join(snippets)[:180]


def normalize_activity_rows(platform: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if platform == "meta":
            object_id = _clean(row.get("object_id"))
            level = _normalize_level(platform, row.get("object_type"))
            operation = _clean(row.get("translated_event_type") or row.get("event_type") or "Activity")
            time_value = _clean(row.get("date_time_in_timezone") or row.get("event_time"))
            actor = _clean(row.get("actor_name") or row.get("actor_id"))
            details = _parse_detail_summary(row.get("extra_data"))
            name = _clean(row.get("object_name"))
            source = _clean(row.get("application_name") or row.get("application_id"))
        else:
            object_id = _clean(row.get("Object ID") or row.get("object_id") or row.get("id"))
            level = _normalize_level(platform, row.get("log_object_type") or row.get("object_type"))
            operation = _clean(row.get("Object") or row.get("operation_type") or row.get("operation") or "Activity")
            time_value = _clean(row.get("Time") or row.get("time") or row.get("create_time"))
            actor = _clean(row.get("Operator") or row.get("operator") or row.get("actor_name"))
            details = _parse_detail_summary(row.get("Activity details") or row.get("details"))
            name = _clean(row.get("object_name") or row.get("name"))
            source = _clean(row.get("Source") or row.get("source"))
        if not object_id:
            continue
        normalized.append(
            {
                "platform": platform,
                "object_id": object_id,
                "level": level,
                "operation": operation,
                "time": time_value,
                "actor": actor or "unknown",
                "details": details,
                "object_name": name,
                "source": source,
                "raw": row,
            }
        )
    return normalized


def rank_activity_targets(platform: str, rows: list[dict[str, Any]], *, limit: int = TARGET_LIMIT) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in normalize_activity_rows(platform, rows):
        if item["level"] in {"campaign", "adset", "adgroup", "ad"}:
            grouped[(item["level"], item["object_id"])].append(item)

    targets: list[dict[str, Any]] = []
    for (level, object_id), items in grouped.items():
        operations = Counter(item["operation"] for item in items)
        actors = Counter(item["actor"] for item in items)
        details_text = " ".join(item.get("details") or "" for item in items)
        operation_text = " ".join(operations.keys())
        score = len(items) * 3 + max((_operation_weight(op, details_text) for op in operations), default=0)
        latest = max((_clean(item.get("time")) for item in items), default="")
        targets.append(
            {
                "platform": platform,
                "level": level,
                "object_id": object_id,
                "activity_count": len(items),
                "score": score,
                "latest_time": latest,
                "dominant_operation": operations.most_common(1)[0][0] if operations else "Activity",
                "top_operations": [{"operation": key, "count": value} for key, value in operations.most_common(5)],
                "actors": [{"actor": key, "count": value} for key, value in actors.most_common(3)],
                "detail_examples": [item["details"] for item in items if item.get("details")][:3],
                "activity_times": [item["time"] for item in items if item.get("time")],
                "object_name": next((_clean(item.get("object_name")) for item in items if _clean(item.get("object_name"))), ""),
                "source": next((_clean(item.get("source")) for item in items if _clean(item.get("source"))), ""),
                "_operation_text": operation_text,
            }
        )
    targets.sort(key=lambda item: (item["score"], item["activity_count"], item["latest_time"]), reverse=True)
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in targets[:limit]]


def _index_rows(platform: str, level_rows: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for level, rows in level_rows.items():
        for row in rows or []:
            if platform == "meta":
                key = {"campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}.get(level)
                object_id = _clean(row.get(key)) if key else ""
            else:
                key = {"campaign": "campaign_id", "adgroup": "adgroup_id", "ad": "ad_id"}.get(level)
                object_id = _tt_dimension(row, key) if key else ""
            if object_id:
                index[(level, object_id)] = row
    return index


def _targeted_index(targeted_insights: dict[str, Any] | list[dict[str, Any]] | None) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    entries = targeted_insights if isinstance(targeted_insights, list) else (targeted_insights or {}).get("rows", [])
    if not isinstance(entries, list):
        return index
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        level = _clean(entry.get("level"))
        object_id = _clean(entry.get("object_id"))
        rows = entry.get("current_rows") or entry.get("rows") or entry.get("current") or []
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        if level and object_id and row:
            index[(level, object_id)] = row
    return index


def _daily_row_metrics(platform: str, row: dict[str, Any]) -> dict[str, Any]:
    if platform == "meta":
        spend = _fnum(row.get("spend"))
        revenue = _meta_revenue(row)
        result = _meta_purchase(row)
        roas = _meta_roas(row)
        day = _date_key(row.get("date_start") or row.get("date"))
    else:
        spend = _tt_metric(row, "spend")
        revenue = _tt_revenue(row)
        result = max(_tt_metric(row, "conversion"), _tt_metric(row, "total_purchase"), _tt_metric(row, "complete_payment"))
        roas = _tt_roas(row)
        day = _date_key((row.get("dimensions") or {}).get("stat_time_day") or row.get("date"))
    return {"date": day, "spend": spend, "revenue": revenue, "result": result, "roas": roas}


def _daily_index(platform: str, daily_breakdown: dict[str, Any] | list[dict[str, Any]] | None) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    entries = daily_breakdown if isinstance(daily_breakdown, list) else (daily_breakdown or {}).get("rows", [])
    if not isinstance(entries, list):
        return index
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        level = _clean(entry.get("level"))
        object_id = _clean(entry.get("object_id"))
        rows = entry.get("daily_rows") or entry.get("rows") or []
        if not level or not object_id or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                metrics = _daily_row_metrics(platform, row)
                if metrics["date"]:
                    index[(level, object_id)].append(metrics)
    for rows in index.values():
        rows.sort(key=lambda item: item.get("date") or "")
    return index


def _snapshot(platform: str, level: str, row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"matched": False, "spend": 0.0, "revenue": 0.0, "roas": 0.0, "result": 0.0}
    if platform == "meta":
        spend = _fnum(row.get("spend"))
        revenue = _meta_revenue(row)
        result = _meta_purchase(row)
        roas = _meta_roas(row)
        name = _clean(
            row.get({"campaign": "campaign_name", "adset": "adset_name", "ad": "ad_name"}.get(level, ""))
        )
    else:
        spend = _tt_metric(row, "spend")
        revenue = _tt_revenue(row)
        result = max(_tt_metric(row, "conversion"), _tt_metric(row, "total_purchase"), _tt_metric(row, "complete_payment"))
        roas = _tt_roas(row)
        name = _clean(
            (row.get("metrics") or {}).get({"campaign": "campaign_name", "adgroup": "adgroup_name", "ad": "ad_name"}.get(level, ""))
        )
    return {
        "matched": True,
        "name": name,
        "spend": round(spend, 4),
        "revenue": round(revenue, 4),
        "roas": round(roas, 4),
        "result": round(result, 4),
    }


def _implication(target: dict[str, Any], current: dict[str, Any], previous: dict[str, Any]) -> str:
    operation = _clean(target.get("dominant_operation"))
    details = " ".join(target.get("detail_examples") or [])
    text = f"{operation} {details}".lower()
    count = int(target.get("activity_count") or 0)
    level = _clean(target.get("level")).replace("adgroup", "ad group").replace("adset", "ad set")
    roas = _fnum(current.get("roas"))
    spend = _fnum(current.get("spend"))
    prev_spend = _fnum(previous.get("spend"))
    prefix = f"{level.title()} 在分析周期内有 {count} 次操作"
    if any(token in text for token in ("status", "on/off", "pause", "enabled", "disabled", "active")):
        return f"{prefix}，以状态切换为主；这会造成 spend、学习期和转化回流不同步，适合作为波动解释因子。"
    if any(token in text for token in ("budget", "bid", "cost cap", "roas")):
        delta = ""
        if previous.get("matched") and (spend or prev_spend):
            delta = f" 当前 spend {spend:,.2f}，上期 {prev_spend:,.2f}。"
        return f"{prefix}，以预算/出价调整为主；应结合调整后的 CPA/ROAS 观察是否稳定。{delta}".strip()
    if any(token in text for token in ("target", "audience", "location", "interest", "placement")):
        return f"{prefix}，以定向/版位变化为主；若 ROAS 为 {roas:.2f}，需拆到受众分段判断是不是流量结构变化。"
    if any(token in text for token in ("creative", "material", "video", "image")):
        return f"{prefix}，涉及素材或广告内容变化；需要和 Creative 留存/Preview 表一起判断创意刷新影响。"
    return f"{prefix}；该对象应作为本期波动的补充解释，优先和对应层级 KPI 一起复核。"


def _money(value: Any) -> str:
    return f"${_fnum(value):,.2f}"


def _level_label(value: Any) -> str:
    return _clean(value).replace("adgroup", "ad group").replace("adset", "ad set") or "object"


def _sum_period(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spend = sum(_fnum(row.get("spend")) for row in rows)
    revenue = sum(_fnum(row.get("revenue")) for row in rows)
    result = sum(_fnum(row.get("result")) for row in rows)
    roas_values = [_fnum(row.get("roas")) for row in rows if _fnum(row.get("roas"))]
    roas = revenue / spend if spend and revenue else (sum(roas_values) / len(roas_values) if roas_values else 0.0)
    return {
        "days": len(rows),
        "spend": round(spend, 4),
        "revenue": round(revenue, 4),
        "result": round(result, 4),
        "roas": round(roas, 4),
    }


def _pct_delta(after: float, before: float) -> float | None:
    if not before:
        return None
    return (after - before) / before


def _daily_trend_for_factor(factor: dict[str, Any], daily_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not daily_rows:
        return None
    activity_dates = sorted({_date_key(item) for item in factor.get("activity_times") or [] if _date_key(item)})
    latest_activity_date = activity_dates[-1] if activity_dates else ""
    if latest_activity_date:
        before_rows = [row for row in daily_rows if str(row.get("date")) < latest_activity_date]
        after_rows = [row for row in daily_rows if str(row.get("date")) >= latest_activity_date]
    else:
        midpoint = max(1, len(daily_rows) // 2)
        before_rows = daily_rows[:midpoint]
        after_rows = daily_rows[midpoint:]
    if not before_rows and after_rows:
        before_rows = daily_rows[:1]
    if not after_rows and before_rows:
        after_rows = daily_rows[-1:]
    before = _sum_period(before_rows)
    after = _sum_period(after_rows)
    spend_delta = _pct_delta(_fnum(after.get("spend")), _fnum(before.get("spend")))
    result_delta = _pct_delta(_fnum(after.get("result")), _fnum(before.get("result")))
    roas_delta = _fnum(after.get("roas")) - _fnum(before.get("roas"))
    pieces: list[str] = []
    if spend_delta is not None and abs(spend_delta) >= 0.2:
        pieces.append(f"操作后 Spend {'上升' if spend_delta > 0 else '下降'} {abs(spend_delta) * 100:.0f}%")
    if result_delta is not None and abs(result_delta) >= 0.2:
        pieces.append(f"Result {'上升' if result_delta > 0 else '下降'} {abs(result_delta) * 100:.0f}%")
    if abs(roas_delta) >= 0.1:
        pieces.append(f"ROAS {'提升' if roas_delta > 0 else '下滑'} {abs(roas_delta):.2f}")
    interpretation = "，".join(pieces) if pieces else "操作前后分天 KPI 未出现明显阶跃变化"
    return {
        "row_count": len(daily_rows),
        "activity_dates": activity_dates,
        "latest_activity_date": latest_activity_date,
        "before": before,
        "after": after,
        "delta": {
            "spend_pct": round(spend_delta, 4) if spend_delta is not None else None,
            "result_pct": round(result_delta, 4) if result_delta is not None else None,
            "roas_delta": round(roas_delta, 4),
        },
        "interpretation": interpretation,
    }


def _activity_conclusions(
    *,
    platform: str,
    activities: dict[str, Any] | list[dict[str, Any]],
    rows: list[dict[str, Any]],
    factors: list[dict[str, Any]],
) -> list[str]:
    strategy = activities.get("strategy") if isinstance(activities, dict) else ""
    conclusions: list[str] = []
    if not rows:
        if strategy == "targeted_top_objects_changelog":
            return ["已按最终 Top 对象定向查询状态/预算/更新类 Activities，未发现可解释本期波动的显著操作记录。"]
        return ["未发现可用于解释本期波动的显著 Activities 记录。"]

    if strategy == "targeted_top_objects_changelog":
        conclusions.append(
            "Activities 已收敛到最终 Top campaign/ad group/ad 对象，并仅查询 CREATE/STATUS/UPDATE 类操作，用于解释波动而非替代 KPI。"
        )
    elif strategy == "changelog_task" and platform == "tiktok":
        conclusions.append("Deep 模式使用宽口径 TikTok changelog；下方只保留高影响因素，避免把原始操作清单写入报告。")

    high_factors = [factor for factor in factors if factor.get("importance") == "high"] or factors
    for factor in high_factors[:3]:
        level = _level_label(factor.get("level"))
        name = _clean(factor.get("object_name") or factor.get("object_id") or "关键对象")
        operation = _clean(factor.get("dominant_operation") or "Activity")
        count = int(factor.get("activity_count") or 0)
        current = factor.get("current") or {}
        previous = factor.get("previous") or {}
        spend = _fnum(current.get("spend"))
        roas = _fnum(current.get("roas"))
        previous_spend = _fnum(previous.get("spend"))
        trend = ""
        if previous.get("matched") and (spend or previous_spend):
            delta = spend - previous_spend
            direction = "增加" if delta >= 0 else "减少"
            trend = f"，Spend 较前期{direction} {_money(abs(delta))}"
        daily_trend = factor.get("daily_trend") or {}
        trend_detail = f"；分天趋势显示{daily_trend.get('interpretation')}" if daily_trend.get("interpretation") else ""
        conclusions.append(
            f"{name}（{level}）出现 {count} 次以 {operation} 为主的操作，当前 Spend {_money(spend)}、ROAS {roas:.2f}{trend}{trend_detail}，应作为本期表现异动的关键解释因子。"
        )
    return conclusions[:4]


def _daily_breakdown_candidates(factors: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for factor in factors:
        current = factor.get("current") or {}
        previous = factor.get("previous") or {}
        spend = _fnum(current.get("spend"))
        previous_spend = _fnum(previous.get("spend"))
        roas = _fnum(current.get("roas"))
        has_material_spend_shift = previous.get("matched") and abs(spend - previous_spend) >= max(50.0, previous_spend * 0.3)
        if factor.get("importance") == "high" and (has_material_spend_shift or roas <= 1.0):
            candidates.append(
                {
                    "level": factor.get("level"),
                    "object_id": factor.get("object_id"),
                    "object_name": factor.get("object_name"),
                    "reason": "spend_shift" if has_material_spend_shift else "low_roas_after_activity",
                }
            )
        if len(candidates) >= limit:
            break
    return candidates


def build_activity_factor_report(
    platform: str,
    activities: dict[str, Any] | list[dict[str, Any]],
    current_rows: dict[str, list[dict[str, Any]]],
    previous_rows: dict[str, list[dict[str, Any]]] | None = None,
    *,
    targeted_insights: dict[str, Any] | list[dict[str, Any]] | None = None,
    daily_breakdown: dict[str, Any] | list[dict[str, Any]] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    raw_rows = activities if isinstance(activities, list) else activities.get("rows") or []
    rows = [row for row in raw_rows if isinstance(row, dict)]
    targets = rank_activity_targets(platform, rows, limit=max(limit, TARGET_LIMIT))
    current_index = _index_rows(platform, current_rows)
    current_index.update(_targeted_index(targeted_insights))
    previous_index = _index_rows(platform, previous_rows or {})
    daily_index = _daily_index(platform, daily_breakdown)

    factors: list[dict[str, Any]] = []
    for target in targets:
        key = (str(target.get("level")), str(target.get("object_id")))
        current = _snapshot(platform, key[0], current_index.get(key))
        previous = _snapshot(platform, key[0], previous_index.get(key))
        factor = {
            **target,
            "object_name": target.get("object_name") or current.get("name") or "",
            "current": current,
            "previous": previous,
            "importance": "high" if int(target.get("score") or 0) >= 13 else "medium",
        }
        daily_trend = _daily_trend_for_factor(factor, daily_index.get(key, []))
        if daily_trend:
            factor["daily_trend"] = daily_trend
        factor["implication"] = _implication(factor, current, previous)
        factors.append(factor)
        if len(factors) >= limit:
            break

    return {
        "platform": platform,
        "strategy": "activity_factor_analysis",
        "activity_count": len(rows),
        "target_count": len(targets),
        "activity_strategy": activities.get("strategy") if isinstance(activities, dict) else "",
        "conclusions": _activity_conclusions(platform=platform, activities=activities, rows=rows, factors=factors),
        "daily_breakdown_candidates": _daily_breakdown_candidates(factors),
        "factors": factors,
        "summary": {
            "factor_count": len(factors),
            "matched_factor_count": sum(1 for item in factors if (item.get("current") or {}).get("matched")),
        },
    }
