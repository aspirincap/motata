#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from motata_cli.report.html_utils import cache_remote_images, esc, preview_cell as shared_preview_cell, report_table, report_table_css


DEFAULT_CREATIVE_LIMIT = 40
ITEM_RESOLVER_SCRIPT = Path(__file__).resolve().parent / "tiktok_item_resolver.py"


def fnum(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value or "0").replace(",", "").replace("%", ""))
    except Exception:
        return 0.0


def money(value: Any) -> str:
    return f"{fnum(value):,.2f}"


def integer(value: Any) -> str:
    return f"{fnum(value):,.0f}"


def ratio(value: Any) -> str:
    return f"{fnum(value):,.2f}"


def percent(value: Any) -> str:
    return f"{fnum(value):,.2f}%"


def metric_or_na(value: Any, *, digits: int = 2) -> str:
    number = fnum(value)
    return "n/a" if abs(number) < 1e-9 else f"{number:,.{digits}f}"


def pct_delta(cur: Any, prev: Any) -> str:
    cur_num = fnum(cur)
    prev_num = fnum(prev)
    if abs(prev_num) < 1e-9:
        return "n/a" if abs(cur_num) < 1e-9 else f"+{cur_num:,.0f}"
    return f"{((cur_num - prev_num) / prev_num * 100):+.1f}%"


def load_json(run_dir: Path, name: str) -> dict[str, Any]:
    path = run_dir / f"{name}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("rows")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def dim(row: dict[str, Any], key: str) -> str:
    dimensions = row.get("dimensions")
    if isinstance(dimensions, dict):
        return str(dimensions.get(key) or "")
    return str(row.get(key) or "")


def metric(row: dict[str, Any], key: str) -> float:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        return fnum(metrics.get(key))
    return fnum(row.get(key))


def raw_metric(row: dict[str, Any], key: str) -> str:
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and metrics.get(key) is not None:
        return str(metrics.get(key) or "")
    dimensions = row.get("dimensions")
    if isinstance(dimensions, dict) and dimensions.get(key) is not None:
        return str(dimensions.get(key) or "")
    return str(row.get(key) or "")


def summary(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("summary")
    if isinstance(raw, dict):
        return {key: fnum(raw.get(key)) for key in ("cost", "net_cost", "orders", "gross_revenue", "roi", "cost_per_order")}
    data = rows(payload)
    cost = sum(metric(row, "cost") for row in data)
    net_cost = sum(metric(row, "net_cost") for row in data)
    orders = sum(metric(row, "orders") for row in data)
    revenue = sum(metric(row, "gross_revenue") for row in data)
    return {
        "cost": cost,
        "net_cost": net_cost,
        "orders": orders,
        "gross_revenue": revenue,
        "roi": revenue / cost if cost else 0.0,
        "cost_per_order": cost / orders if orders else 0.0,
    }


def by_campaign_name(campaigns_payload: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows(campaigns_payload):
        campaign_id = str(row.get("campaign_id") or "")
        if campaign_id:
            mapping[campaign_id] = str(row.get("campaign_name") or campaign_id)
    return mapping


def preview_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_item = payload.get("by_item_id")
    if isinstance(by_item, dict):
        return {str(key): value for key, value in by_item.items() if isinstance(value, dict)}
    out: dict[str, dict[str, Any]] = {}
    items = payload.get("rows")
    if not isinstance(items, list):
        data = payload.get("data")
        if isinstance(data, dict):
            items = data.get("item_list")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "")
            if item_id:
                out[item_id] = item
    return out


def product_detail_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_item_group = payload.get("by_item_group_id")
    if isinstance(by_item_group, dict):
        return {str(key): value for key, value in by_item_group.items() if isinstance(value, dict)}
    out: dict[str, dict[str, Any]] = {}
    for row in rows(payload):
        item_group_id = str(row.get("item_group_id") or "").strip()
        if item_group_id:
            out[item_group_id] = row
    data = payload.get("data")
    if isinstance(data, dict):
        store_products = data.get("store_products")
        if isinstance(store_products, list):
            for row in store_products:
                if not isinstance(row, dict):
                    continue
                item_group_id = str(row.get("item_group_id") or "").strip()
                if item_group_id:
                    out[item_group_id] = row
    return out


def fallback_post_url(item_id: str) -> str:
    return f"https://www.tiktok.com/@motata/video/{item_id}"


def resolver_result_to_preview(result: dict[str, Any]) -> dict[str, Any]:
    item_id = str(result.get("item_id") or "").strip()
    title = str(result.get("title") or "").strip()
    user_name = str(result.get("user_name") or "").strip()
    handle_name = str(result.get("handle_name") or "").strip()
    avatar_url = str(result.get("avatar_url") or "").strip()
    preview_url = str(result.get("preview_url") or "").strip()
    real_video_url = str(result.get("real_video_url") or result.get("final_url") or "").strip()
    description = str(result.get("description") or "").strip()
    return {
        "item_id": item_id,
        "title": title,
        "text": description or title,
        "resolver": result,
        "identity_info": {
            "handle_name": handle_name,
            "user_name": user_name.lstrip("@"),
            "display_name": handle_name or user_name.lstrip("@"),
            "profile_image": avatar_url,
        },
        "video_info": {
            "video_cover_url": preview_url,
            "preview_url": real_video_url or fallback_post_url(item_id),
        },
        "real_post_url": real_video_url,
        "post_url": fallback_post_url(item_id),
        "source": "tiktok_item_resolver",
    }


def load_or_resolve_items(run_dir: Path, item_ids: list[str], *, timeout: int = 20, allow_network: bool = False) -> dict[str, dict[str, Any]]:
    cache_path = run_dir / "gmv_max_resolved_item_previews.json"
    cached: dict[str, Any] = {}
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                cached = payload
        except json.JSONDecodeError:
            cached = {}
    by_item = cached.get("by_item_id") if isinstance(cached.get("by_item_id"), dict) else {}
    resolved_by_item: dict[str, dict[str, Any]] = {
        str(key): value for key, value in by_item.items() if isinstance(value, dict)
    }
    missing = []
    for item_id in dict.fromkeys(item_ids):
        if not item_id:
            continue
        cached_item = resolved_by_item.get(item_id)
        if not isinstance(cached_item, dict):
            missing.append(item_id)
            continue
        if not str(cached_item.get("text") or cached_item.get("title") or "").strip():
            missing.append(item_id)
    if allow_network and missing and ITEM_RESOLVER_SCRIPT.exists():
        command = [
            sys.executable,
            str(ITEM_RESOLVER_SCRIPT),
            *missing,
            "--timeout",
            str(timeout),
            "--download-dir",
            str(run_dir / "assets" / "tiktok_item_resolver"),
            "--pretty",
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=max(timeout * len(missing) + 15, 30))
            if completed.stdout.strip():
                parsed = json.loads(completed.stdout)
                results = parsed if isinstance(parsed, list) else [parsed]
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    item_id = str(result.get("item_id") or "").strip()
                    if item_id:
                        resolved_by_item[item_id] = resolver_result_to_preview(result)
            errors = []
            if completed.returncode != 0 and completed.stderr.strip():
                errors.append(completed.stderr.strip())
        except Exception as exc:
            errors = [str(exc)]
        cache_payload = {
            "source": "tiktok_item_resolver",
            "script": str(ITEM_RESOLVER_SCRIPT),
            "target_item_ids": list(dict.fromkeys(item_ids)),
            "resolved_item_count": len(resolved_by_item),
            "by_item_id": resolved_by_item,
            "errors": errors,
        }
        cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved_by_item


def merge_preview(base: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not base:
        return fallback
    if not fallback:
        return base
    merged = dict(base)
    base_identity = merged.get("identity_info") if isinstance(merged.get("identity_info"), dict) else {}
    fallback_identity = fallback.get("identity_info") if isinstance(fallback.get("identity_info"), dict) else {}
    merged["identity_info"] = {**fallback_identity, **base_identity}
    base_video = merged.get("video_info") if isinstance(merged.get("video_info"), dict) else {}
    fallback_video = fallback.get("video_info") if isinstance(fallback.get("video_info"), dict) else {}
    merged["video_info"] = {**fallback_video, **base_video}
    if not merged.get("post_url") and fallback.get("post_url"):
        merged["post_url"] = fallback.get("post_url")
    if not merged.get("text") and fallback.get("text"):
        merged["text"] = fallback.get("text")
    if not merged.get("title") and fallback.get("title"):
        merged["title"] = fallback.get("title")
    return merged


def ensure_post_url_from_identity(item_id: str, preview: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(preview, dict):
        return preview
    if preview.get("post_url"):
        return preview
    enriched = dict(preview)
    enriched["post_url"] = fallback_post_url(item_id)
    return enriched


def weighted_average(total: float, weight: float) -> float:
    return total / weight if weight else 0.0


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def per_mille(numerator: float, impressions: float) -> float:
    return safe_div(numerator, impressions) * 1000


def aggregate_creatives(data: list[dict[str, Any]], *, include_all_products: bool = False) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    weighted_metric_names = [
        "product_click_rate",
        "ad_click_rate",
        "ad_conversion_rate",
        "ad_video_view_rate_2s",
        "ad_video_view_rate_6s",
        "ad_video_view_rate_p25",
        "ad_video_view_rate_p50",
        "ad_video_view_rate_p75",
        "ad_video_view_rate_p100",
    ]
    for row in data:
        item_id = dim(row, "item_id") or "-"
        if item_id == "-1" and not include_all_products:
            continue
        campaign_id = dim(row, "campaign_id") or dim(row, "campaign_id_filter")
        item_scope = dim(row, "item_scope") or ("PRODUCT_CARD" if item_id == "-1" else "SPECIFIC_ITEM")
        if item_scope == "PRODUCT_CARD" and not include_all_products:
            continue
        item = out.setdefault(
            item_id,
            {
                "item_id": item_id,
                "item_scope": item_scope,
                "campaign_ids": set(),
                "item_group_ids": set(),
                "titles": set(),
                "delivery_statuses": set(),
                "status_campaigns": {},
                "rejection_reasons": set(),
                "cost": 0.0,
                "orders": 0.0,
                "gross_revenue": 0.0,
                "product_impressions": 0.0,
                "product_clicks": 0.0,
                "_rate_weight": 0.0,
            },
        )
        if campaign_id:
            item["campaign_ids"].add(campaign_id)
        item_group_id = dim(row, "item_group_id") or dim(row, "item_group_id_filter")
        if item_group_id:
            item["item_group_ids"].add(item_group_id)
        for target, field in (
            ("titles", "title"),
            ("delivery_statuses", "creative_delivery_status"),
        ):
            value = raw_metric(row, field).strip()
            if value and value not in {"0", "-1"}:
                item[target].add(value)
        status = raw_metric(row, "creative_delivery_status").strip()
        if status:
            item["status_campaigns"].setdefault(status, set())
            if campaign_id:
                item["status_campaigns"][status].add(campaign_id)
        for reason_key in ("reject_reason", "rejection_reason", "creative_reject_reason", "review_reject_reason"):
            reason = raw_metric(row, reason_key).strip()
            if reason and reason not in {"0", "-1", "N/A", "n/a"}:
                item["rejection_reasons"].add(reason)
        item["cost"] += metric(row, "cost")
        item["orders"] += metric(row, "orders")
        item["gross_revenue"] += metric(row, "gross_revenue")
        item["product_impressions"] += metric(row, "product_impressions")
        item["product_clicks"] += metric(row, "product_clicks")
        weight = metric(row, "product_impressions") or metric(row, "cost") or metric(row, "gross_revenue") or 1.0
        item["_rate_weight"] += weight
        for name in weighted_metric_names:
            item[f"_{name}_weighted"] = item.get(f"_{name}_weighted", 0.0) + metric(row, name) * weight
    for item in out.values():
        item["roi"] = item["gross_revenue"] / item["cost"] if item["cost"] else 0.0
        item["cost_per_order"] = item["cost"] / item["orders"] if item["orders"] else 0.0
        if item["product_impressions"]:
            item["product_click_rate"] = item["product_clicks"] / item["product_impressions"] * 100
        else:
            item["product_click_rate"] = weighted_average(item.get("_product_click_rate_weighted", 0.0), item["_rate_weight"])
        for name in [value for value in weighted_metric_names if value != "product_click_rate"]:
            item[name] = weighted_average(item.get(f"_{name}_weighted", 0.0), item["_rate_weight"])
        item["campaign_count"] = len(item["campaign_ids"])
        item["campaign_ids"] = sorted(item["campaign_ids"])
        item["item_group_ids"] = sorted(item["item_group_ids"])
        item["titles"] = sorted(item["titles"])
        item["delivery_statuses"] = sorted(item["delivery_statuses"])
        item["status_campaigns"] = {
            status: sorted(campaign_ids)
            for status, campaign_ids in item.get("status_campaigns", {}).items()
        }
        item["rejection_reasons"] = sorted(item.get("rejection_reasons", set()))
        item["cpm_proxy"] = per_mille(item["cost"], item["product_impressions"])
        item["product_gpm"] = per_mille(item["gross_revenue"], item["product_impressions"])
    return out


def aggregate_products_from_creatives(data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in data:
        item_id = dim(row, "item_id")
        if item_id == "-1":
            continue
        item_group_id = dim(row, "item_group_id") or dim(row, "item_group_id_filter")
        if not item_group_id:
            continue
        campaign_id = dim(row, "campaign_id") or dim(row, "campaign_id_filter")
        product = out.setdefault(
            item_group_id,
            {
                "item_group_id": item_group_id,
                "campaign_ids": set(),
                "cost": 0.0,
                "orders": 0.0,
                "gross_revenue": 0.0,
                "product_impressions": 0.0,
                "product_clicks": 0.0,
            },
        )
        if campaign_id:
            product["campaign_ids"].add(campaign_id)
        product["cost"] += metric(row, "cost")
        product["orders"] += metric(row, "orders")
        product["gross_revenue"] += metric(row, "gross_revenue")
        product["product_impressions"] += metric(row, "product_impressions")
        product["product_clicks"] += metric(row, "product_clicks")
    for product in out.values():
        product["campaign_count"] = len(product["campaign_ids"])
        product["campaign_ids"] = sorted(product["campaign_ids"])
        product["roi"] = safe_div(product["gross_revenue"], product["cost"])
        product["cost_per_order"] = safe_div(product["cost"], product["orders"])
        product["product_gpm"] = per_mille(product["gross_revenue"], product["product_impressions"])
        product["product_click_rate"] = safe_div(product["product_clicks"], product["product_impressions"]) * 100
    return out


def product_status_by_group(data: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in data:
        item_group_id = dim(row, "item_group_id")
        status = raw_metric(row, "product_status").strip()
        if item_group_id and status:
            out.setdefault(item_group_id, status)
    return out


def creative_benchmarks(items: list[dict[str, Any]], account: dict[str, float]) -> dict[str, float]:
    cost = sum(fnum(item.get("cost")) for item in items) or fnum(account.get("cost"))
    revenue = sum(fnum(item.get("gross_revenue")) for item in items) or fnum(account.get("gross_revenue"))
    impressions = sum(fnum(item.get("product_impressions")) for item in items)
    clicks = sum(fnum(item.get("product_clicks")) for item in items)
    weighted_2s = sum(fnum(item.get("ad_video_view_rate_2s")) * (fnum(item.get("product_impressions")) or fnum(item.get("cost")) or 1.0) for item in items)
    weight_2s = sum((fnum(item.get("product_impressions")) or fnum(item.get("cost")) or 1.0) for item in items)
    return {
        "cost": cost,
        "roi": safe_div(revenue, cost),
        "product_click_rate": safe_div(clicks, impressions) * 100,
        "ad_video_view_rate_2s": safe_div(weighted_2s, weight_2s),
        "cpm_proxy": per_mille(cost, impressions),
        "product_gpm": per_mille(revenue, impressions),
        "high_spend": max(30.0, cost * 0.05),
        "very_high_spend": max(50.0, cost * 0.10),
    }


def creative_judgement(
    item: dict[str, Any],
    previous_item: dict[str, Any] | None,
    benchmarks: dict[str, float],
    previous_benchmarks: dict[str, float],
) -> dict[str, str]:
    cost = fnum(item.get("cost"))
    roi = fnum(item.get("roi"))
    revenue = fnum(item.get("gross_revenue"))
    orders = fnum(item.get("orders"))
    impressions = fnum(item.get("product_impressions"))
    product_gpm = fnum(item.get("product_gpm"))
    cpm = fnum(item.get("cpm_proxy"))
    ctr = fnum(item.get("product_click_rate"))
    view_2s = fnum(item.get("ad_video_view_rate_2s"))
    avg_roi = fnum(benchmarks.get("roi"))
    avg_ctr = fnum(benchmarks.get("product_click_rate"))
    avg_view_2s = fnum(benchmarks.get("ad_video_view_rate_2s"))
    avg_product_gpm = fnum(benchmarks.get("product_gpm"))
    avg_cpm = fnum(benchmarks.get("cpm_proxy"))
    high_spend = cost >= fnum(benchmarks.get("high_spend"))
    very_high_spend = cost >= fnum(benchmarks.get("very_high_spend"))
    low_roi = roi < avg_roi * 0.75 if avg_roi else roi < 1.0
    basic_ok = (ctr >= avg_ctr * 0.8 if avg_ctr else ctr > 0) or (view_2s >= avg_view_2s * 0.85 if avg_view_2s else view_2s > 0)
    product_gpm_ok = product_gpm >= avg_product_gpm * 0.9 if avg_product_gpm else False
    cpm_expensive = cpm >= avg_cpm * 1.15 if avg_cpm else False
    strong_front = (ctr >= avg_ctr if avg_ctr else False) or (view_2s >= avg_view_2s * 1.05 if avg_view_2s else False)
    account_healthy = avg_roi >= 1.5
    statuses = {str(value) for value in item.get("delivery_statuses", [])}
    has_rejected = bool(statuses & {"REJECTED", "UNAVAILABLE"})
    needs_authorization = "AUTHORIZATION_NEEDED" in statuses
    manually_or_system_stopped = bool(statuses & {"EXCLUDED", "NOT_DELIVERYING", "NOT_ACTIVE"})
    active_or_learning = bool(statuses & {"DELIVERING", "LEARNING", "IN_QUEUE"})
    all_blocked = bool(statuses) and not active_or_learning

    if not previous_item:
        if needs_authorization:
            return {
                "label": "授权优先",
                "tone": "warn",
                "action": "先补授权或替换可投素材，再评价投产",
                "reason": "素材仍需要广告授权，当前表现不能直接等同于素材效率。",
            }
        if all_blocked or has_rejected:
            return {
                "label": "不可投核查",
                "tone": "bad",
                "action": "先看拒审/不可用原因，能修则修，不能修再替换",
                "reason": "状态显示拒审、不可用或未投递，GMV Max 无法稳定学习该素材。",
            }
        if low_roi and high_spend and not basic_ok:
            return {
                "label": "新素材预警",
                "tone": "warn",
                "action": "保留短观察窗，同时补同卖点新钩子赛马",
                "reason": "上期未出现，当前已吃到较高消耗但基础互动弱，避免继续裸跑。",
            }
        return {
            "label": "学习期观察",
            "tone": "ok",
            "action": "至少再观察 1-3 天，不直接删除",
            "reason": "上期未出现且基础点击/播放未失真，先让 GMV Max 完成人群探索。",
        }

    prev_roi = fnum(previous_item.get("roi"))
    prev_avg_roi = fnum(previous_benchmarks.get("roi")) or avg_roi
    prev_revenue = fnum(previous_item.get("gross_revenue"))
    prev_product_gpm = fnum(previous_item.get("product_gpm"))
    roi_drop = safe_div(roi - prev_roi, prev_roi)
    gpm_drop = safe_div(product_gpm - prev_product_gpm, prev_product_gpm)
    was_good = prev_roi >= prev_avg_roi * 0.9 or prev_roi >= avg_roi
    decayed = was_good and low_roi and high_spend and (roi_drop <= -0.25 or gpm_drop <= -0.25)

    if needs_authorization:
        return {
            "label": "授权优先",
            "tone": "warn",
            "action": "先完成授权，再决定是否替换",
            "reason": "素材存在 AUTHORIZATION_NEEDED，低投产可能来自无法正常投递。",
        }
    if has_rejected and not active_or_learning:
        return {
            "label": "不可投替换",
            "tone": "bad",
            "action": "优先查拒因，无法快速恢复就用新素材接力",
            "reason": "素材处于拒审/不可用且没有投递或学习状态，继续观察价值有限。",
        }
    if manually_or_system_stopped and low_roi and high_spend:
        return {
            "label": "停投复盘",
            "tone": "warn",
            "action": "查看停止投递的 campaign，再决定修复或替换",
            "reason": "低投产伴随未投递/不活跃状态，判断时需先排除状态导致的数据偏差。",
        }
    if decayed:
        return {
            "label": "衰退替换",
            "tone": "bad",
            "action": "准备 5-10 条新素材接力，1-3 天同跑后逐步关老素材",
            "reason": f"上期 ROI {ratio(prev_roi)}，本期 ROI {ratio(roi)}，高消耗下明显衰退。",
        }
    if low_roi and high_spend and product_gpm_ok and cpm_expensive:
        return {
            "label": "CPM 观察",
            "tone": "warn",
            "action": "再给约 3 天观察，不急删",
            "reason": "Product GPM 尚可但流量成本偏贵，可能是前期 CPM 波动拉低 ROI。",
        }
    if low_roi and high_spend and account_healthy and strong_front:
        return {
            "label": "引流型观察",
            "tone": "warn",
            "action": "保留监控 Campaign ROI，并用收割型新素材承接",
            "reason": "自身 ROI 偏低但点击/播放强，可能承担前端种草或拉新人角色。",
        }
    if low_roi and very_high_spend:
        return {
            "label": "替换候选",
            "tone": "bad",
            "action": "不要裸关，先上新素材赛马再释放预算",
            "reason": "高消耗低投产且缺少 Product GPM/互动保护信号。",
        }
    if roi >= avg_roi * 1.1 and cost >= fnum(benchmarks.get("high_spend")) * 0.5:
        return {
            "label": "稳定保留",
            "tone": "ok",
            "action": "保留预算，围绕同卖点复制新前三秒钩子",
            "reason": "ROI 高于账户均值，且已有有效消耗。",
        }
    return {
        "label": "常规监控",
        "tone": "neutral",
        "action": "维持观察，低频补充差异化钩子",
        "reason": "未触发高消耗低投产或衰退替换条件。",
    }


def judgement_cell(judgement: dict[str, str]) -> str:
    tone = esc(judgement.get("tone") or "neutral")
    return (
        f'<div class="judgement"><span class="tag tag-{tone}">{esc(judgement.get("label"))}</span>'
        f'<div class="action">{esc(judgement.get("action"))}</div>'
        f'<div class="reason">{esc(judgement.get("reason"))}</div></div>'
    )


def clipped_text(value: str, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def compact_price(value: Any, currency: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "n/a"
    number = fnum(text)
    formatted = f"{number:,.2f}" if "." in text or number % 1 else f"{number:,.0f}"
    return f"{esc(currency)} {esc(formatted)}".strip()


def product_title(detail: dict[str, Any] | None, item_group_id: str) -> str:
    detail = detail if isinstance(detail, dict) else {}
    title = str(detail.get("title") or detail.get("product_name") or "").strip()
    return title or f"Item Group {item_group_id}"


def product_info_cell(item_group_id: str, detail: dict[str, Any] | None = None, *, local_image: str | None = None) -> str:
    detail = detail if isinstance(detail, dict) else {}
    title = product_title(detail, item_group_id)
    image = str(local_image or detail.get("product_image_url") or detail.get("image_url") or "").strip()
    currency = str(detail.get("currency") or "").strip()
    min_price = detail.get("min_price") or detail.get("sale_price") or detail.get("discount_price")
    max_price = detail.get("max_price") or detail.get("list_price") or detail.get("original_price")
    image_html = (
        f'<img class="product-thumb" src="{esc(image)}" alt="{esc(title)}" loading="lazy" referrerpolicy="no-referrer" />'
        if image
        else '<span class="product-thumb product-thumb-placeholder">No image</span>'
    )
    return (
        '<div class="product-info-cell">'
        '<div class="creator-row">'
        f"{image_html}"
        '<div class="creator-meta">'
        f'<div class="product-title" title="{esc(title)}">{esc(clipped_text(title, limit=82))}</div>'
        f'<div class="creator-id">{esc(item_group_id)}</div>'
        f'<div class="product-price"><span>折后 {compact_price(min_price, currency)}</span><span>定价 {compact_price(max_price, currency)}</span></div>'
        + "</div></div></div>"
    )


def product_status_tone(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"available", "enable", "enabled", "active"}:
        return "ok"
    if normalized in {"not_available", "unavailable", "disabled", "inactive", "rejected"}:
        return "bad"
    if normalized:
        return "warn"
    return "neutral"


def product_status_cell(status: str) -> str:
    value = str(status or "n/a").strip()
    tone = product_status_tone(value)
    return f'<span class="status-chip status-{esc(tone)}">{esc(value)}</span>'


def preview_text_for_item(item: dict[str, Any], preview: dict[str, Any] | None) -> str:
    preview = preview if isinstance(preview, dict) else {}
    title = " / ".join(str(value) for value in item.get("titles", [])[:2] if value)
    text = str(preview.get("text") or "").strip()
    return title or text or "-"


def creative_post_copy_cell(
    item_id: str,
    item: dict[str, Any],
    video: dict[str, Any] | None = None,
    *,
    local_avatar: str | None = None,
) -> str:
    video = video if isinstance(video, dict) else {}
    identity = video.get("identity_info") if isinstance(video.get("identity_info"), dict) else {}
    handle_name = str(identity.get("handle_name") or identity.get("display_name") or "").strip()
    user_name = str(identity.get("user_name") or "").strip().lstrip("@")
    display_name = handle_name or user_name or "Unknown"
    avatar = str(local_avatar or identity.get("profile_image") or identity.get("avatar_url") or "").strip()
    video_info = video.get("video_info") if isinstance(video.get("video_info"), dict) else {}
    post_url = str(video.get("post_url") or video_info.get("post_url") or "").strip() or fallback_post_url(item_id)
    full_text = preview_text_for_item(item, video)
    short_text = clipped_text(full_text, limit=118)
    avatar_html = (
        f'<img class="creator-avatar" src="{esc(avatar)}" alt="{esc(display_name)}" loading="lazy" referrerpolicy="no-referrer" />'
        if avatar
        else '<span class="creator-avatar creator-avatar-placeholder">?</span>'
    )
    return (
        '<div class="creative-copy-cell">'
        '<div class="creator-row">'
        f"{avatar_html}"
        '<div class="creator-meta">'
        f'<div class="creator-name">{esc(display_name)}</div>'
        f'<div class="creator-id">{esc(item_id)}</div>'
        f'<a class="creator-link" href="{esc(post_url)}" target="_blank" rel="noreferrer">预览帖子</a>'
        "</div></div>"
        f'<div class="copy-snippet" tabindex="0" aria-label="{esc(full_text)}">'
        f'<span class="copy-short">{esc(short_text)}</span>'
        f'<span class="copy-full">{esc(full_text)}</span>'
        "</div></div>"
    )


def status_label(status: str) -> str:
    labels = {
        "DELIVERING": "投递",
        "LEARNING": "学习",
        "IN_QUEUE": "排队",
        "NOT_DELIVERYING": "未投",
        "AUTHORIZATION_NEEDED": "待授权",
        "EXCLUDED": "排除",
        "UNAVAILABLE": "不可用",
        "REJECTED": "拒审",
        "NOT_ACTIVE": "不活跃",
    }
    return labels.get(status, status or "-")


def status_tone(status: str) -> str:
    if status in {"DELIVERING", "LEARNING"}:
        return "ok"
    if status in {"IN_QUEUE", "NOT_DELIVERYING", "AUTHORIZATION_NEEDED", "NOT_ACTIVE"}:
        return "warn"
    if status in {"REJECTED", "UNAVAILABLE", "EXCLUDED"}:
        return "bad"
    return "neutral"


def status_details(item: dict[str, Any], campaign_names: dict[str, str]) -> str:
    lines: list[str] = []
    status_campaigns = item.get("status_campaigns") if isinstance(item.get("status_campaigns"), dict) else {}
    for status in item.get("delivery_statuses", []):
        campaign_ids = status_campaigns.get(status) or []
        named = [
            f"{campaign_names.get(str(campaign_id), str(campaign_id))} ({campaign_id})"
            for campaign_id in campaign_ids[:8]
        ]
        suffix = f"；另 {len(campaign_ids) - 8} 个 campaign" if len(campaign_ids) > 8 else ""
        lines.append(f"{status_label(str(status))}: {', '.join(named) or '未返回 campaign'}{suffix}")
    reasons = item.get("rejection_reasons") or []
    lines.append("拒审原因: " + (" / ".join(str(reason) for reason in reasons) if reasons else "接口未返回具体原因"))
    return "\n".join(lines)


def status_cell(item: dict[str, Any], campaign_names: dict[str, str]) -> str:
    statuses = [str(value) for value in item.get("delivery_statuses", []) if str(value)]
    if not statuses:
        return '<span class="status-chip status-neutral">-</span>'
    counts = Counter(statuses)
    details = status_details(item, campaign_names)
    chips = []
    for status in statuses[:3]:
        count = counts.get(status, 1)
        count_text = f" x{count}" if count > 1 else ""
        chips.append(
            f'<span class="status-chip status-{esc(status_tone(status))}">{esc(status_label(status))}{esc(count_text)}</span>'
        )
    if len(statuses) > 3:
        chips.append(f'<span class="status-chip status-neutral">+{len(statuses) - 3}</span>')
    return (
        '<div class="status-cell" tabindex="0">'
        + "".join(chips)
        + f'<div class="status-popover">{esc(details).replace(chr(10), "<br>")}</div>'
        + "</div>"
    )


def judgement_summary(judgements: list[dict[str, str]]) -> dict[str, int]:
    out = {"replace": 0, "observe": 0, "keep": 0, "monitor": 0}
    for item in judgements:
        label = item.get("label")
        if label in {"衰退替换", "替换候选"}:
            out["replace"] += 1
        elif label in {"学习期观察", "CPM 观察", "引流型观察", "新素材预警"}:
            out["observe"] += 1
        elif label == "稳定保留":
            out["keep"] += 1
        else:
            out["monitor"] += 1
    return out


def cache_preview_images(run_dir: Path, previews: dict[str, dict[str, Any]], *, timeout: int = 4) -> dict[str, str]:
    cover_urls: dict[str, str] = {}
    for item_id, video in previews.items():
        if not isinstance(video, dict):
            continue
        video_info = video.get("video_info")
        video_info = video_info if isinstance(video_info, dict) else {}
        cover_url = str(video_info.get("video_cover_url") or "").strip()
        if cover_url and not cover_url.startswith(("http://", "https://")):
            cover_urls[str(item_id)] = cover_url
        elif cover_url:
            cover_urls[str(item_id)] = cover_url
    remote_cover_urls = {item_id: url for item_id, url in cover_urls.items() if url.startswith(("http://", "https://"))}
    local_cover_urls = {item_id: url for item_id, url in cover_urls.items() if not url.startswith(("http://", "https://"))}
    local_cover_urls.update(cache_remote_images(run_dir, remote_cover_urls, relative_dir="assets/gmv_max_previews", timeout=timeout, max_bytes=1_500_000))
    return local_cover_urls


def cache_avatar_images(run_dir: Path, previews: dict[str, dict[str, Any]], *, timeout: int = 4) -> dict[str, str]:
    avatar_urls: dict[str, str] = {}
    for item_id, video in previews.items():
        if not isinstance(video, dict):
            continue
        identity = video.get("identity_info")
        identity = identity if isinstance(identity, dict) else {}
        avatar_url = str(identity.get("profile_image") or identity.get("avatar_url") or "").strip()
        if avatar_url and not avatar_url.startswith(("http://", "https://")):
            avatar_urls[str(item_id)] = avatar_url
        elif avatar_url:
            avatar_urls[str(item_id)] = avatar_url
    remote_avatar_urls = {item_id: url for item_id, url in avatar_urls.items() if url.startswith(("http://", "https://"))}
    local_avatar_urls = {item_id: url for item_id, url in avatar_urls.items() if not url.startswith(("http://", "https://"))}
    local_avatar_urls.update(cache_remote_images(run_dir, remote_avatar_urls, relative_dir="assets/gmv_max_avatars", timeout=timeout, max_bytes=500_000))
    return local_avatar_urls


def cache_product_images(run_dir: Path, products: dict[str, dict[str, Any]], *, timeout: int = 4) -> dict[str, str]:
    image_urls: dict[str, str] = {}
    for item_group_id, detail in products.items():
        if not isinstance(detail, dict):
            continue
        image_url = str(detail.get("product_image_url") or detail.get("image_url") or "").strip()
        if image_url and not image_url.startswith(("http://", "https://")):
            image_urls[str(item_group_id)] = image_url
        elif image_url:
            image_urls[str(item_group_id)] = image_url
    remote_urls = {item_group_id: url for item_group_id, url in image_urls.items() if url.startswith(("http://", "https://"))}
    local_urls = {item_group_id: url for item_group_id, url in image_urls.items() if not url.startswith(("http://", "https://"))}
    local_urls.update(cache_remote_images(run_dir, remote_urls, relative_dir="assets/gmv_max_products", timeout=timeout, max_bytes=1_000_000))
    return local_urls


def preview_cell(video: dict[str, Any] | None = None, *, local_cover: str | None = None) -> str:
    if isinstance(video, dict):
        video_info = video.get("video_info")
        video_info = video_info if isinstance(video_info, dict) else {}
        remote_cover = str(video_info.get("video_cover_url") or "").strip()
        preview = str(video_info.get("preview_url") or "").strip()
        text = str(video.get("text") or video.get("item_id") or "Preview").strip()
        return shared_preview_cell(remote_cover, preview, alt=text, unavailable_text="Unavailable", local_image_url=local_cover or "")
    return shared_preview_cell("", "", unavailable_text="Unavailable")


def creative_identity_cell(item_id: str, video: dict[str, Any] | None = None, *, local_avatar: str | None = None) -> str:
    video = video if isinstance(video, dict) else {}
    identity = video.get("identity_info") if isinstance(video.get("identity_info"), dict) else {}
    handle_name = str(identity.get("handle_name") or identity.get("display_name") or "").strip()
    user_name = str(identity.get("user_name") or "").strip().lstrip("@")
    display_name = handle_name or user_name or "Unknown"
    avatar = str(local_avatar or identity.get("profile_image") or identity.get("avatar_url") or "").strip()
    video_info = video.get("video_info") if isinstance(video.get("video_info"), dict) else {}
    post_url = str(video.get("post_url") or video_info.get("post_url") or "").strip()
    post_url = post_url or fallback_post_url(item_id)
    avatar_html = (
        f'<img class="creator-avatar" src="{esc(avatar)}" alt="{esc(display_name)}" loading="lazy" referrerpolicy="no-referrer" />'
        if avatar
        else '<span class="creator-avatar creator-avatar-placeholder">?</span>'
    )
    return (
        '<div class="creator-cell">'
        f"{avatar_html}"
        '<div class="creator-meta">'
        f'<div class="creator-name">{esc(display_name)}</div>'
        f'<div class="creator-id">{esc(item_id)}</div>'
        f'<a class="creator-link" href="{esc(post_url)}" target="_blank" rel="noreferrer">预览帖子</a>'
        "</div></div>"
    )


def card(label: str, value: str, note: str) -> str:
    return f'<div class="metric"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div><div class="note">{esc(note)}</div></div>'


def css() -> str:
    return """
    :root { --bg:#f7f8fb; --panel:#fff; --ink:#172033; --muted:#697386; --line:#d9dee8; --brand:#1455d9; --ok:#087443; --warn:#a15c00; --bad:#b42318; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.55; }
    .wrap { width:min(1240px, calc(100vw - 40px)); margin:0 auto; padding:32px 0 56px; }
    .hero { background:#13233f; color:white; padding:28px 32px; border-radius:8px; }
    .eyebrow { color:#b9c7e8; font-size:12px; text-transform:uppercase; }
    h1 { margin:6px 0 8px; font-size:34px; line-height:1.15; letter-spacing:0; }
    h2 { margin:0 0 12px; font-size:22px; letter-spacing:0; }
    h3 { margin:0 0 10px; font-size:17px; letter-spacing:0; }
    p { margin:0 0 10px; }
    section { margin-top:20px; }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:18px; }
    .metric { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.16); border-radius:8px; padding:14px; }
    section .metric { background:white; border-color:var(--line); }
    .metric .label { color:#bdc9e6; font-size:12px; }
    section .metric .label { color:var(--muted); }
    .metric .value { margin-top:5px; font-size:26px; font-weight:750; }
    .metric .note { color:#d6def1; font-size:12px; margin-top:4px; }
    section .metric .note { color:var(--muted); }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .pill-row { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .pill { border:1px solid var(--line); background:#fff; border-radius:999px; padding:6px 10px; font-size:12px; color:var(--ink); }
    .muted { color:var(--muted); }
    """ + report_table_css() + """
    .campaign-table th:nth-child(1), .campaign-table td:nth-child(1) { min-width:260px; max-width:340px; overflow-wrap:anywhere; }
    .campaign-table th:nth-child(2), .campaign-table td:nth-child(2) { min-width:170px; }
    .product-table { min-width:1320px !important; }
    .product-table th:nth-child(1), .product-table td:nth-child(1) { min-width:340px; width:380px; white-space:normal; }
    .product-table th:nth-child(2), .product-table td:nth-child(2) { min-width:88px; width:96px; white-space:nowrap; }
    .product-table th:nth-child(n+3), .product-table td:nth-child(n+3) { min-width:96px; white-space:nowrap; }
    .creative-table th:nth-child(2), .creative-table td:nth-child(2) { min-width:340px !important; width:360px !important; white-space:normal; }
    .creative-table th:nth-child(3), .creative-table td:nth-child(3) { min-width:78px !important; width:86px !important; max-width:96px !important; white-space:normal !important; overflow:visible; }
    .creator-cell, .creator-row { display:flex; align-items:center; gap:10px; min-width:0; }
    .creator-avatar { width:36px; height:36px; min-width:36px; border-radius:50%; object-fit:cover; border:1px solid var(--line); background:#eef2f8; }
    .creator-avatar-placeholder { display:inline-flex; align-items:center; justify-content:center; color:var(--muted); font-weight:750; }
    .creator-meta { min-width:0; display:flex; flex-direction:column; gap:2px; }
    .creator-name { font-weight:750; color:var(--ink); overflow-wrap:anywhere; word-break:break-word; }
    .creator-id { color:var(--muted); font-size:11px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; overflow-wrap:anywhere; word-break:break-all; }
    .creator-link { font-size:12px; font-weight:750; color:var(--brand); text-decoration:none; }
    .creator-link:hover { text-decoration:underline; }
    .product-info-cell { width:360px; max-width:380px; min-width:0; }
    .product-thumb { width:48px; height:48px; min-width:48px; border-radius:6px; object-fit:cover; border:1px solid var(--line); background:#eef2f8; }
    .product-thumb-placeholder { display:inline-flex; align-items:center; justify-content:center; color:var(--muted); font-size:10px; text-align:center; line-height:1.15; padding:3px; }
    .product-title { font-weight:750; color:var(--ink); overflow-wrap:anywhere; word-break:break-word; line-height:1.25; }
    .product-price { display:flex; flex-wrap:wrap; gap:4px 8px; margin-top:2px; color:#40516f; font-size:11px; line-height:1.25; }
    .product-price span { white-space:nowrap; }
    .product-status-note { color:var(--muted); font-size:11px; line-height:1.25; }
    .creative-copy-cell { position:relative; width:340px; max-width:360px; }
    .copy-snippet { margin-top:8px; color:#40516f; font-size:12px; line-height:1.35; overflow-wrap:anywhere; word-break:break-word; cursor:help; }
    .copy-short { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .copy-full { display:none; max-height:220px; overflow:auto; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); box-shadow:0 10px 24px rgba(15,31,61,.12); white-space:normal; }
    .copy-snippet:hover .copy-short, .copy-snippet:focus .copy-short { display:none; }
    .copy-snippet:hover .copy-full, .copy-snippet:focus .copy-full { display:block; }
    .status-cell { position:relative; display:inline-flex; flex-wrap:wrap; gap:3px; width:78px; max-width:78px; cursor:help; overflow:visible; }
    .status-chip { display:inline-flex; align-items:center; min-height:18px; padding:1px 5px; border-radius:999px; font-size:10px; font-weight:750; border:1px solid var(--line); line-height:1.2; white-space:nowrap; }
    .status-popover { display:none; position:absolute; left:0; top:calc(100% + 6px); z-index:20; width:320px; max-height:220px; overflow:auto; padding:9px 11px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); box-shadow:0 12px 30px rgba(15,31,61,.18); white-space:normal; line-height:1.45; font-size:12px; font-weight:500; }
    .status-cell:hover .status-popover, .status-cell:focus .status-popover, .status-cell:focus-within .status-popover { display:block; }
    .status-ok { color:var(--ok); background:#eaf7ef; border-color:#b7dfc6; }
    .status-warn { color:var(--warn); background:#fff6e5; border-color:#f1d39b; }
    .status-bad { color:var(--bad); background:#ffefed; border-color:#f3b8b3; }
    .status-neutral { color:#40516f; background:#eef2f8; border-color:#d2d9e6; }
    .creative-table th:nth-child(12), .creative-table td:nth-child(12),
    .creative-table th:nth-child(13), .creative-table td:nth-child(13) { min-width:120px; }
    .judgement { min-width:220px; max-width:240px; white-space:normal; overflow-wrap:anywhere; word-break:normal; }
    .tag { display:inline-flex; align-items:center; min-height:24px; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:750; border:1px solid var(--line); }
    .tag-ok { color:var(--ok); background:#eaf7ef; border-color:#b7dfc6; }
    .tag-warn { color:var(--warn); background:#fff6e5; border-color:#f1d39b; }
    .tag-bad { color:var(--bad); background:#ffefed; border-color:#f3b8b3; }
    .tag-neutral { color:#40516f; background:#eef2f8; border-color:#d2d9e6; }
    .action { margin-top:7px; font-weight:700; color:var(--ink); white-space:normal; }
    .reason { margin-top:4px; color:var(--muted); font-size:12px; white-space:normal; }
    ul { margin:0; padding-left:20px; }
    li + li { margin-top:6px; }
    @media (max-width:900px){ .grid,.two{grid-template-columns:1fr;} .wrap{width:min(100vw - 24px,1240px);} .hero{padding:22px;} }
    """


def render(
    run_dir: Path,
    *,
    creative_limit: int = DEFAULT_CREATIVE_LIMIT,
    resolve_items: bool = False,
    cache_images: bool = False,
) -> str:
    manifest = load_json(run_dir, "manifest")
    current_account = load_json(run_dir, "current_gmv_max_account")
    previous_account = load_json(run_dir, "previous_gmv_max_account")
    current_campaign = load_json(run_dir, "current_gmv_max_campaign")
    previous_campaign = load_json(run_dir, "previous_gmv_max_campaign")
    current_product = load_json(run_dir, "current_gmv_max_product")
    current_creative = load_json(run_dir, "current_gmv_max_creative")
    previous_creative = load_json(run_dir, "previous_gmv_max_creative")
    stores = load_json(run_dir, "gmv_max_stores")
    campaigns = load_json(run_dir, "gmv_max_campaigns_product")
    campaign_item_previews = load_json(run_dir, "gmv_max_campaign_item_previews")
    custom_videos = load_json(run_dir, "gmv_max_custom_anchor_videos")
    gmv_max_videos = load_json(run_dir, "gmv_max_videos")
    store_products = load_json(run_dir, "gmv_max_store_products")

    window = manifest.get("window") if isinstance(manifest.get("window"), dict) else {}
    gmv = manifest.get("gmv_max") if isinstance(manifest.get("gmv_max"), dict) else {}
    cur = summary(current_account)
    prev = summary(previous_account)
    campaign_names = by_campaign_name(campaigns)

    campaign_table_rows: list[list[str]] = []
    prev_by_campaign = {dim(row, "campaign_id"): row for row in rows(previous_campaign)}
    for row in sorted(rows(current_campaign), key=lambda item: metric(item, "cost"), reverse=True)[:12]:
        campaign_id = dim(row, "campaign_id")
        prev_row = prev_by_campaign.get(campaign_id, {})
        campaign_table_rows.append(
            [
                esc(campaign_names.get(campaign_id, campaign_id)),
                esc(campaign_id),
                money(metric(row, "cost")),
                integer(metric(row, "orders")),
                money(metric(row, "gross_revenue")),
                ratio(metric(row, "roi")),
                pct_delta(metric(row, "roi"), metric(prev_row, "roi")),
            ]
        )

    creative_items = list(aggregate_creatives(rows(current_creative)).values())
    product_items = list(aggregate_products_from_creatives(rows(current_creative)).values())
    product_statuses = product_status_by_group(rows(current_product))
    product_details_by_group = product_detail_map(store_products)
    product_item_group_ids = [str(item.get("item_group_id")) for item in product_items if item.get("item_group_id")]
    local_product_images = (
        cache_product_images(
            run_dir,
            {
                item_group_id: product_details_by_group.get(item_group_id, {})
                for item_group_id in dict.fromkeys(product_item_group_ids)
            },
        )
        if cache_images
        else {}
    )
    product_rows: list[list[str]] = []
    for item in sorted(product_items, key=lambda product: fnum(product.get("gross_revenue")), reverse=True)[:12]:
        item_group_id = str(item.get("item_group_id") or "")
        product_rows.append(
            [
                product_info_cell(
                    item_group_id,
                    product_details_by_group.get(item_group_id),
                    local_image=local_product_images.get(item_group_id),
                ),
                product_status_cell(product_statuses.get(item_group_id, "n/a")),
                integer(item.get("campaign_count")),
                money(item.get("cost")),
                integer(item.get("orders")),
                money(item.get("gross_revenue")),
                ratio(item.get("roi")),
                ratio(item.get("cost_per_order")),
                money(item.get("product_gpm")),
                integer(item.get("product_impressions")),
                integer(item.get("product_clicks")),
                percent(item.get("product_click_rate")),
            ]
        )

    previous_creatives_by_item = aggregate_creatives(rows(previous_creative))
    creative_bench = creative_benchmarks(creative_items, cur)
    previous_creative_bench = creative_benchmarks(list(previous_creatives_by_item.values()), prev)
    creative_limit = max(1, int(creative_limit or DEFAULT_CREATIVE_LIMIT))
    table_creatives = sorted(creative_items, key=lambda item: fnum(item["cost"]), reverse=True)[:creative_limit]
    campaign_previews_by_item = preview_map(campaign_item_previews)
    campaign_names = by_campaign_name(campaigns)
    custom_videos_by_item = preview_map(custom_videos)
    gmv_videos_by_item = preview_map(gmv_max_videos)
    combined_previews_by_item = dict(gmv_videos_by_item)
    combined_previews_by_item.update(custom_videos_by_item)
    combined_previews_by_item.update(campaign_previews_by_item)
    for item_id, preview in list(combined_previews_by_item.items()):
        combined_previews_by_item[item_id] = ensure_post_url_from_identity(item_id, preview) or {}
    table_item_ids = [str(item["item_id"]) for item in table_creatives]
    resolver_targets: list[str] = []
    for item_id in table_item_ids:
        preview = combined_previews_by_item.get(item_id) or {}
        identity = preview.get("identity_info") if isinstance(preview.get("identity_info"), dict) else {}
        video_info = preview.get("video_info") if isinstance(preview.get("video_info"), dict) else {}
        if (
            not identity.get("profile_image")
            or not video_info.get("video_cover_url")
            or not str(preview.get("text") or preview.get("title") or "").strip()
        ):
            resolver_targets.append(item_id)
    resolver_previews_by_item = load_or_resolve_items(run_dir, resolver_targets, allow_network=resolve_items)
    for item_id, resolved in resolver_previews_by_item.items():
        combined_previews_by_item[item_id] = merge_preview(combined_previews_by_item.get(item_id), resolved) or {}
    local_covers_by_item = (
        cache_preview_images(
            run_dir,
            {str(item["item_id"]): combined_previews_by_item.get(str(item["item_id"]), {}) for item in table_creatives},
        )
        if cache_images
        else {}
    )
    local_avatars_by_item = (
        cache_avatar_images(
            run_dir,
            {str(item["item_id"]): combined_previews_by_item.get(str(item["item_id"]), {}) for item in table_creatives},
        )
        if cache_images
        else {}
    )
    creative_judgements = [
        creative_judgement(item, previous_creatives_by_item.get(str(item["item_id"])), creative_bench, previous_creative_bench)
        for item in table_creatives
    ]
    judgement_counts = judgement_summary(creative_judgements)
    creative_rows = []
    for item, judgement in zip(table_creatives, creative_judgements):
        previous_item = previous_creatives_by_item.get(str(item["item_id"])) or {}
        creative_rows.append(
            [
            preview_cell(combined_previews_by_item.get(str(item["item_id"])), local_cover=local_covers_by_item.get(str(item["item_id"]))),
            creative_post_copy_cell(str(item["item_id"]), item, combined_previews_by_item.get(str(item["item_id"])), local_avatar=local_avatars_by_item.get(str(item["item_id"]))),
            status_cell(item, campaign_names),
            judgement_cell(judgement),
            integer(item["campaign_count"]),
            money(item["cost"]),
            integer(item["orders"]),
            money(item["gross_revenue"]),
            ratio(item["roi"]),
            pct_delta(item["roi"], previous_item.get("roi")),
            money(item["cpm_proxy"]),
            money(item["product_gpm"]),
            ratio(item["cost_per_order"]),
            integer(item["product_impressions"]),
            integer(item["product_clicks"]),
            percent(item["product_click_rate"]),
            percent(item["ad_video_view_rate_2s"]),
            percent(item["ad_video_view_rate_6s"]),
            percent(item["ad_video_view_rate_p100"]),
            ]
        )

    all_products = [
        item for item in aggregate_creatives(rows(current_creative), include_all_products=True).values()
        if item["item_scope"] == "PRODUCT_CARD"
    ]
    all_product_cost = sum(fnum(item["cost"]) for item in all_products)
    all_product_orders = sum(fnum(item["orders"]) for item in all_products)
    all_product_revenue = sum(fnum(item["gross_revenue"]) for item in all_products)
    all_product_roi = all_product_revenue / all_product_cost if all_product_cost else 0.0
    video_target_count = len(custom_videos.get("target_item_ids", [])) if isinstance(custom_videos.get("target_item_ids"), list) else 0
    video_match_count = len(custom_videos.get("rows", [])) if isinstance(custom_videos.get("rows"), list) else 0
    campaign_preview_count = len(campaign_previews_by_item)
    campaign_preview_campaign_count = len(campaign_item_previews.get("campaign_ids", [])) if isinstance(campaign_item_previews.get("campaign_ids"), list) else 0
    product_detail_count = len(product_details_by_group)
    store_names = [str(store.get("store_name") or store.get("store_id")) for store in rows(stores) if store.get("is_gmv_max_available")]
    scope = (
        f"Scope: TikTok advertiser {manifest.get('advertiser_id')} | "
        f"{window.get('since')} to {window.get('until')} | "
        f"previous {window.get('previous_since')} to {window.get('previous_until')} | "
        f"mode {manifest.get('tiktok_report_mode')}"
    )

    title = f"GMV Max 周报 - {manifest.get('advertiser_id')} - {window.get('until')}"
    hero = f"""
    <div class="hero">
      <div class="eyebrow">TikTok GMV Max Weekly Report</div>
      <h1>GMV Max 周报</h1>
      <p>{esc(scope)}</p>
      <div class="pill-row">
        <span class="pill">Store: {esc(', '.join(store_names) or ', '.join(gmv.get('store_ids') or []))}</span>
        <span class="pill">Coverage: {esc(gmv.get('coverage'))}</span>
        <span class="pill">Activity: {esc((manifest.get('options') or {}).get('activity_strategy'))}</span>
      </div>
      <div class="grid">
        {card('GMV Max 消耗', money(cur['cost']), f"上周 {money(prev['cost'])} / {pct_delta(cur['cost'], prev['cost'])}")}
        {card('订单数', integer(cur['orders']), f"上周 {integer(prev['orders'])} / {pct_delta(cur['orders'], prev['orders'])}")}
        {card('总收入', money(cur['gross_revenue']), f"上周 {money(prev['gross_revenue'])} / {pct_delta(cur['gross_revenue'], prev['gross_revenue'])}")}
        {card('ROI', ratio(cur['roi']), f"上周 {ratio(prev['roi'])} / {pct_delta(cur['roi'], prev['roi'])}")}
      </div>
    </div>
    """

    executive = f"""
    <section class="two">
      <div class="panel">
        <h2>Executive Summary</h2>
        <ul>
          <li>本期 GMV Max 消耗 {money(cur['cost'])}，较上期 {pct_delta(cur['cost'], prev['cost'])}；收入 {money(cur['gross_revenue'])}，较上期 {pct_delta(cur['gross_revenue'], prev['gross_revenue'])}。</li>
          <li>订单 {integer(cur['orders'])}，平均下单成本 {money(cur['cost_per_order'])}；ROI 从 {ratio(prev['roi'])} 到 {ratio(cur['roi'])}。</li>
          <li>系统判定为 GMV Max-first 账户；普通 auction probe 没有消耗，不纳入主报表。</li>
        </ul>
      </div>
      <div class="panel">
        <h2>Recommended Actions</h2>
        <ul>
          <li>素材不按“高消耗低 ROI”一刀切：新素材、Product GPM 正常但 CPM 偏贵、以及疑似前端引流素材先观察。</li>
          <li>Top 消耗 item 中，{integer(judgement_counts['replace'])} 个进入替换/衰退候选，{integer(judgement_counts['observe'])} 个建议观察，{integer(judgement_counts['keep'])} 个稳定保留。</li>
          <li><code>item_id=-1</code> 在 Creative 层级通常代表 <code>PRODUCT_CARD</code> 没有 TikTok post ID，需与视频素材分开看。</li>
        </ul>
      </div>
    </section>
    """

    body = hero + executive
    body += f"<section class=\"panel\"><h2>Campaign Drivers</h2>{report_table(['Campaign','Campaign ID','消耗','订单','总收入','ROI','ROI 环比'], campaign_table_rows, class_name='campaign-table')}</section>"
    body += f"<section class=\"panel\"><h2>Product / Item Group</h2><p class=\"muted\">商品图、名称和价格来自 <code>store/product/get</code>；状态来自 Product 层 <code>product_status</code>；消耗、订单、收入、ROI、GPM、曝光和点击由 Creative 层按 <code>item_group_id</code> 汇总，宽表可横向滑动。</p>{report_table(['产品 / Item Group','状态','Campaign 数','消耗','订单','总收入','ROI','平均下单成本','Product GPM','商品展示','商品点击','商品点击率'], product_rows, class_name='product-table')}</section>"
    body += f"<section class=\"panel\"><h2>Creative / Item Buckets</h2><p class=\"muted\">按消耗展示 Top {integer(len(table_creatives))} / {integer(len(creative_items))} 个具体 item；Preview 使用全量 campaign item 映射，表内行都会尝试缓存封面；AUTO_SELECTION 缺失详情时用 <code>tiktok_item_resolver.py</code> 补充账号、头像和帖子链接。素材/账号列的文案截断后可 hover 查看完整内容；状态 badge hover 可查看隶属 campaign 与拒因可用性。</p>{report_table(['Preview','素材 / 账号 / 帖子','状态','素材判断','Campaign 数','消耗','订单','总收入','ROI','ROI 环比','CPM 代理','Product GPM','平均下单成本','商品展示','商品点击','商品点击率','2s播放率','6s播放率','完播率'], creative_rows, class_name='creative-table')}</section>"
    body += f"""
    <section class="panel">
      <h2>素材接力策略</h2>
      <ul>
        <li>对“衰退替换/替换候选”不要裸关：先围绕核心卖点准备 5-10 条新素材，分别测试痛点、结果、场景、价格、对比等前三秒钩子。</li>
        <li>前 1-3 天让新旧素材同跑，并主动给新素材争取基础消耗，避免老素材吃完预算导致新素材没有学习数据。</li>
        <li>第 4-5 天开始释放高消耗低投产素材预算；如果关停后流量断崖，先拉回老素材，再继续调新素材。</li>
        <li>当前判断使用 Creative 层级官方字段：消耗、订单、总收入、ROI、平均下单成本、商品展示/点击、商品点击率与视频播放率；Product GPM 为派生指标，等于总收入 / 商品展示 * 1000。</li>
      </ul>
    </section>
    """
    body += f"""
    <section class="panel">
      <h2>All Products 聚合</h2>
      <p><code>item_id=-1</code> 代表 <code>PRODUCT_CARD</code> 无 TikTok post ID，不是未知视频素材。本期该类聚合消耗 {money(all_product_cost)}，订单 {integer(all_product_orders)}，总收入 {money(all_product_revenue)}，ROI {ratio(all_product_roi)}。</p>
    </section>
    """
    body += f"""
    <section class="panel">
      <h2>Data Quality</h2>
      <p>数据质量：GMV Max 标准周报源均已成功落盘；Live GMV Max campaigns 为 0；activity/changelog 对 GMV Max 不适用，已按 <code>not_applicable_gmv_max</code> 处理。</p>
      <p>Campaign 预览补充：已用 <code>campaign/gmv_max/info</code> 查询 Creative 行隶属的 {integer(campaign_preview_campaign_count)} 个 campaign，获得 {integer(campaign_preview_count)} 个 item_id 预览映射，并优先用于 Creative 表 Preview。</p>
      <p>预览缓存：{('已将 Creative 表内 ' + integer(len(table_creatives)) + ' 个 item 的可用封面缓存到本地 <code>assets/gmv_max_previews</code>，HTML 图片优先使用本地副本' if cache_images else '本次跳过远程图片下载缓存，HTML 直接引用 TikTok API 返回的封面/头像 URL')}，预览按钮仍打开 TikTok 返回的最新 preview URL。</p>
      <p>商品详情补充：Product / Item Group 通过 <code>store/product/get</code> 读取 {integer(product_detail_count)} 个商品详情，商品图会缓存到本地 <code>assets/gmv_max_products</code>；商品状态使用 Product 层 <code>product_status</code>，绩效指标由 Creative 层按 <code>item_group_id</code> 汇总派生。</p>
      <p>帖子详情补充：{('对表内缺少账号/头像/真实帖子地址的 item 调用 <code>scripts/tiktok_item_resolver.py</code>，结果缓存为 <code>gmv_max_resolved_item_previews.json</code>' if resolve_items else '本次跳过 <code>scripts/tiktok_item_resolver.py</code> 网页解析，优先使用 TikTok API 已返回的 campaign item preview')}；无法解析真实地址时回退到 <code>https://www.tiktok.com/@motata/video/{{item_id}}</code>。</p>
      <p>预览补充：已用 <code>gmv_max/creation/custom_anchor_video_list/get</code> 对 Top {integer(video_target_count)} 个 item_id 做 customized post 精确查询，命中 {integer(video_match_count)} 条；未命中项在 Preview 列显示 Unavailable。</p>
      <p class="muted">Provenance: {esc(str(run_dir))}</p>
    </section>
    """
    from motata_cli.common.output import completeness
    from motata_cli.common.security import redact
    coverage = completeness(manifest)
    if not manifest or not coverage['complete']:
        limitation = '缺少来源 manifest，数据完整性未经验证。' if not manifest else f"数据状态：{coverage['status']}；限制：{', '.join(coverage['reasons'])}。请勿将此报告视作完整账户数据。"
        body = f'<section role="alert" class="card"><strong>{esc(redact(limitation))}</strong></section>' + body
    return f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{esc(title)}</title><style>{css()}</style></head><body><main class=\"wrap\">{body}</main></body></html>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a TikTok GMV Max HTML report from a Motata report run directory.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--creative-limit", type=int, default=DEFAULT_CREATIVE_LIMIT, help="Number of top spend creative item rows to render. Default: 40.")
    parser.add_argument("--no-item-resolver", action="store_true", help="Skip tiktok_item_resolver.py fallback and render from API preview data only.")
    parser.add_argument("--no-image-cache", action="store_true", help="Compatibility flag: image downloads are already disabled by default.")
    parser.add_argument("--cache-images", action="store_true", help="Explicitly allow remote preview/avatar image downloads.")
    args = parser.parse_args()
    out = args.out or args.run_dir / "report.html"
    html_text = render(
        args.run_dir,
        creative_limit=args.creative_limit,
        resolve_items=False,
        cache_images=args.cache_images and not args.no_image_cache,
    )
    out.write_text(html_text, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
