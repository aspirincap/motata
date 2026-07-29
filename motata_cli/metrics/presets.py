from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import META_METRIC_MAP, TIKTOK_METRIC_MAP
from .core import build_core_metric_coverage


USER_TYPE_ALIASES = {
    "电商": "ecommerce",
    "工具": "tool",
    "短剧": "short_drama",
    "休闲游戏": "casual_game",
    "中重度游戏": "midcore_game",
    "金融借贷": "finance",
    "小说": "novel",
    "泛娱乐": "entertainment",
    "搜索套利": "search_arbitrage",
    "社交": "social",
    "代理商/多类型": "agency_multi",
    "赌博": "gambling",
    "网赚": "earn_money",
}


BASE_PRESET = {
    "core": {
        "meta": ["spend", "impressions", "clicks", "actions", "action_values", "reach", "frequency", "ctr", "cpc", "cpm"],
        "tiktok": ["spend", "impressions", "clicks", "conversion", "real_time_result", "total_purchase_value", "reach", "frequency", "ctr", "cpc", "cpm"],
    },
    "conversion": {
        "meta": ["actions", "results", "cost_per_result", "cost_per_action_type"],
        "tiktok": ["conversion", "cost_per_conversion", "conversion_rate_v2", "result", "cost_per_result", "result_rate", "real_time_result", "real_time_cost_per_result", "real_time_result_rate"],
    },
    "creative_video": {
        "meta": ["video_play_actions", "video_6_sec_watched_actions", "video_p50_watched_actions", "video_p100_watched_actions", "video_avg_time_watched_actions"],
        "tiktok": ["video_play_actions", "video_watched_6s", "video_views_p50", "video_views_p100", "average_video_play", "engaged_view", "paid_engaged_view", "paid_engagement_engaged_view", "paid_engaged_view_15s", "paid_engagement_engaged_view_15s", "interactive_add_on_impressions"],
    },
    "audience_placement": {
        "meta": ["objective", "optimization_goal", "attribution_setting"],
        "tiktok": ["objective_type", "promotion_type", "billing_event", "bid_strategy", "placement_type"],
    },
}


VERTICAL_PRESETS: dict[str, dict[str, dict[str, list[str]]]] = {
    "ecommerce": {
        "value_roas": {
            "meta": ["action_values", "purchase_roas", "website_purchase_roas", "total_action_value"],
            "tiktok": [
                "onsite_purchases_roas",
                "onsite_shopping_roas",
                "shop_gross_revenue_by_order_submission",
                "onsite_total_purchase",
                "onsite_total_purchase_value",
                "onsite_cost_per_purchase",
                "onsite_purchase_rate",
                "onsite_value_per_purchase",
                "shop_total_purchase_by_order_submission",
                "shop_total_items_purchased",
                "onsite_total_checkout_initiation",
                "onsite_total_add_to_cart",
                "onsite_total_product_details_page_view",
                "complete_payment",
                "complete_payment_roas",
                "total_purchase_value",
            ],
        },
        "vertical_specific": {
            "meta": ["catalog_segment_actions", "catalog_segment_value", "product_views", "converted_product_value", "outbound_clicks"],
            "tiktok": [
                "onsite_unique_purchase",
                "onsite_unique_checkout_initiation",
                "onsite_unique_add_to_cart",
                "onsite_total_checkout_initiation_value",
                "onsite_total_add_to_cart_value",
                "onsite_total_product_details_page_view_value",
                "add_to_cart",
                "checkout",
                "view_content",
                "button_click",
                "form",
            ],
        },
    },
    "tool": {
        "conversion": {
            "meta": ["app_store_clicks", "deeplink_clicks", "start_trial_actions", "subscribe_actions"],
            "tiktok": [
                "onsite_destination_visits",
                "onsite_download_start",
                "real_time_app_install",
                "skan_app_install",
                "skan_cost_per_app_install",
                "app_install",
                "cost_per_app_install",
                "launch_app",
                "start_trial",
                "subscribe",
            ],
        },
        "vertical_specific": {
            "meta": ["mobile_app_purchase_roas", "conversion_values"],
            "tiktok": [
                "registration",
                "total_registration",
                "onsite_form",
                "onsite_total_subscribe",
                "onsite_total_subscribe_value",
                "unique_custom_app_events",
                "custom_app_events_value",
                "onsite_ad_impression_ad_revenue_roas",
            ],
        },
    },
    "short_drama": {
        "value_roas": {
            "meta": ["subscribe_actions", "subscribe_value", "cost_per_subscribe", "start_trial_actions"],
            "tiktok": [
                "onsite_subscribe_value_day0",
                "onsite_subscribe_value_day1",
                "onsite_subscribe_value_day6",
                "onsite_total_subscribe_value",
                "onsite_total_subscribe",
                "total_subscribe_value",
                "subscribe",
                "total_subscribe",
                "view_content",
                "total_purchase_value",
            ],
        },
        "creative_video": {
            "meta": ["video_p25_watched_actions", "video_p75_watched_actions", "video_p100_watched_actions"],
            "tiktok": [
                "paid_engaged_view_15s",
                "paid_engagement_engaged_view_15s",
                "engaged_view_15s",
                "video_views_p100",
                "video_views_p75",
                "video_views_p50",
            ],
        },
        "vertical_specific": {
            "meta": ["video_p25_watched_actions", "video_p75_watched_actions", "video_p100_watched_actions"],
            "tiktok": ["video_views_p25", "video_views_p75", "video_views_p100", "onsite_subscribe_value_day0", "onsite_subscribe_value_day13"],
        },
    },
    "casual_game": {
        "conversion": {
            "meta": ["mobile_app_install", "app_store_clicks", "deeplink_clicks"],
            "tiktok": [
                "real_time_app_install",
                "skan_app_install",
                "app_install",
                "complete_tutorial",
                "day7_retention",
                "achieve_level",
                "purchase",
                "total_purchase_value",
                "in_app_ad_impr",
                "in_app_ad_click",
            ],
        },
        "value_roas": {
            "meta": ["mobile_app_purchase_roas", "action_values"],
            "tiktok": ["purchase", "total_purchase", "total_purchase_value", "in_app_ad_impr", "in_app_ad_click", "custom_app_events_value"],
        },
        "vertical_specific": {
            "meta": ["app_store_clicks", "deeplink_clicks"],
            "tiktok": ["total_complete_tutorial", "total_achieve_level", "total_day7_retention", "launch_app", "next_day_open"],
        },
    },
    "midcore_game": {
        "conversion": {
            "meta": ["mobile_app_install", "app_store_clicks", "deeplink_clicks"],
            "tiktok": [
                "real_time_app_install",
                "skan_app_install",
                "app_install",
                "create_gamerole",
                "achieve_level",
                "unlock_achievement",
                "day7_retention",
                "purchase",
                "total_purchase_value",
                "custom_app_events_value",
            ],
        },
        "value_roas": {
            "meta": ["mobile_app_purchase_roas", "action_values"],
            "tiktok": ["purchase", "total_purchase", "total_purchase_value", "custom_app_events_value"],
        },
        "vertical_specific": {
            "meta": ["app_store_clicks", "deeplink_clicks"],
            "tiktok": ["total_create_gamerole", "total_achieve_level", "total_unlock_achievement", "total_day7_retention", "unique_custom_app_events"],
        },
    },
    "finance": {
        "conversion": {
            "meta": ["contact_actions", "cost_per_contact", "submit_application_actions", "cost_per_submit_application"],
            "tiktok": [
                "form",
                "onsite_form",
                "button_click",
                "messaging_total_conversation_tiktok_direct_message",
                "loan_apply",
                "loan_credit",
                "loan_disbursement",
                "sales_lead",
            ],
        },
        "vertical_specific": {
            "meta": ["actions", "cost_per_action_type", "results"],
            "tiktok": ["registration", "total_registration", "secondary_goal_result", "cost_per_secondary_goal_result"],
        },
    },
    "novel": {
        "value_roas": {
            "meta": ["subscribe_actions", "subscribe_value", "start_trial_actions"],
            "tiktok": [
                "onsite_subscribe_value_day0",
                "onsite_subscribe_value_day1",
                "onsite_subscribe_value_day6",
                "total_subscribe_value",
                "subscribe",
                "total_subscribe",
                "start_trial",
                "view_content",
            ],
        },
        "creative_video": {
            "meta": ["video_6_sec_watched_actions", "video_p100_watched_actions"],
            "tiktok": ["paid_engaged_view_15s", "engaged_view_15s", "video_views_p100", "video_views_p75"],
        },
        "vertical_specific": {
            "meta": ["video_6_sec_watched_actions", "video_p100_watched_actions"],
            "tiktok": ["search", "day7_retention", "view_content"],
        },
    },
    "entertainment": {
        "conversion": {
            "meta": ["subscribe_actions", "start_trial_actions", "actions"],
            "tiktok": [
                "live_views",
                "live_effective_views",
                "messaging_total_conversation_tiktok_direct_message",
                "follows",
                "profile_visits",
                "subscribe",
                "launch_app",
            ],
        },
        "creative_video": {
            "meta": ["video_play_actions", "video_complete_watched_actions"],
            "tiktok": ["engaged_view_15s", "paid_engaged_view_15s", "video_views_p100"],
        },
        "live": {
            "meta": ["video_play_actions", "video_complete_watched_actions"],
            "tiktok": ["live_views", "live_unique_views", "live_effective_views", "live_product_clicks"],
        },
        "messaging": {
            "meta": ["contact_actions"],
            "tiktok": [
                "messaging_total_conversation_tiktok_direct_message",
                "messaging_cost_per_conversation_tiktok_direct_message",
                "messaging_conversation_rate_tiktok_direct_message",
            ],
        },
        "vertical_specific": {
            "meta": ["video_play_actions", "video_complete_watched_actions"],
            "tiktok": ["likes", "comments", "shares", "engaged_view"],
        },
    },
    "search_arbitrage": {
        "conversion": {
            "meta": ["outbound_clicks", "cost_per_outbound_click", "actions"],
            "tiktok": ["button_click", "clicks", "search", "conversion", "cost_per_conversion", "onsite_destination_visits"],
        },
        "vertical_specific": {
            "meta": ["inline_link_clicks", "inline_link_click_ctr"],
            "tiktok": ["ctr", "cpc", "placement_type"],
        },
    },
    "social": {
        "conversion": {
            "meta": ["actions", "subscribe_actions", "contact_actions"],
            "tiktok": [
                "live_views",
                "live_effective_views",
                "messaging_total_conversation_tiktok_direct_message",
                "follows",
                "profile_visits",
                "registration",
                "subscribe",
                "launch_app",
                "custom_app_events",
            ],
        },
        "creative_video": {
            "meta": ["video_play_actions", "outbound_clicks"],
            "tiktok": ["engaged_view_15s", "paid_engaged_view_15s", "engaged_view"],
        },
        "live": {
            "meta": ["video_play_actions", "outbound_clicks"],
            "tiktok": ["live_views", "live_unique_views", "live_effective_views", "live_product_clicks"],
        },
        "messaging": {
            "meta": ["contact_actions"],
            "tiktok": [
                "messaging_total_conversation_tiktok_direct_message",
                "messaging_cost_per_conversation_tiktok_direct_message",
                "messaging_conversation_rate_tiktok_direct_message",
            ],
        },
        "vertical_specific": {
            "meta": ["video_play_actions", "outbound_clicks"],
            "tiktok": ["likes", "comments", "shares", "engagements", "engagement_rate"],
        },
    },
    "agency_multi": {
        "core": {
            "meta": ["spend", "impressions", "reach", "clicks", "ctr", "cpc", "cpm", "frequency"],
            "tiktok": ["spend", "cash_spend", "voucher_spend", "impressions", "reach", "clicks", "ctr", "cpc", "cpm"],
        },
        "vertical_specific": {
            "meta": ["account_id", "account_name", "campaign_id", "campaign_name", "objective"],
            "tiktok": ["advertiser_id", "advertiser_name", "campaign_name", "objective_type", "campaign_automation_type", "placement_type", "billing_event"],
        },
    },
    "gambling": {
        "conversion": {
            "meta": ["actions", "cost_per_action_type", "results"],
            "tiktok": ["real_time_app_install", "skan_app_install", "registration", "purchase", "skan_purchase", "vta_purchase", "cta_purchase"],
        },
        "vertical_specific": {
            "meta": ["outbound_clicks", "video_play_actions"],
            "tiktok": ["engagements", "button_click", "launch_app", "day7_retention"],
        },
    },
    "earn_money": {
        "conversion": {
            "meta": ["actions", "contact_actions", "submit_application_actions"],
            "tiktok": ["form", "button_click", "messaging_total_conversation_tiktok_direct_message", "sales_lead", "registration"],
        },
        "vertical_specific": {
            "meta": ["outbound_clicks", "cost_per_outbound_click"],
            "tiktok": ["clicks", "cpc", "conversion_rate_v2", "onsite_form"],
        },
    },
}


W2A_METRICS = {
    "conversion": {
        "meta": ["app_store_clicks", "deeplink_clicks", "mobile_app_purchase_roas"],
        "tiktok": [
            "onsite_destination_visits",
            "onsite_download_start",
            "real_time_app_install",
            "skan_app_install",
            "skan_cost_per_app_install",
            "app_install",
            "cost_per_app_install",
        ],
    },
    "vertical_specific": {
        "meta": ["actions", "conversions", "conversion_values"],
        "tiktok": ["registration", "purchase", "total_purchase_value", "skan_purchase", "skan_total_purchase_value", "onsite_shopping_roas", "shop_gross_revenue_by_order_submission"],
    },
}


def _dedupe(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(values) if value]


def _merge_presets(base: dict[str, dict[str, list[str]]], extra: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    merged = {bucket: {platform: list(metrics) for platform, metrics in platforms.items()} for bucket, platforms in base.items()}
    for bucket, platforms in extra.items():
        target = merged.setdefault(bucket, {})
        for platform, metrics in platforms.items():
            # Put vertical-specific metrics ahead of the generic defaults so the
            # recommendation output surfaces the newest TikTok families first.
            target[platform] = _dedupe([*metrics, *target.get(platform, [])])
    return merged


def normalize_user_type(user_type: str) -> str:
    return USER_TYPE_ALIASES.get(user_type, user_type.strip().lower().replace("-", "_").replace(" ", "_"))


def load_probe_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).expanduser().read_text())


def _probe_active_sets(probe: dict[str, Any] | None) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    active: dict[str, set[str]] = {"meta": set(), "tiktok": set()}
    empty: dict[str, set[str]] = {"meta": set(), "tiktok": set()}
    unsupported: dict[str, set[str]] = {"meta": set(), "tiktok": set()}
    if not probe:
        return active, empty, unsupported

    payloads: list[dict[str, Any]]
    if "results" in probe and isinstance(probe["results"], list):
        payloads = [item for item in probe["results"] if isinstance(item, dict)]
    else:
        payloads = [probe]

    for payload in payloads:
        platform = str(payload.get("platform") or "").lower()
        if platform not in active:
            continue
        for key, target in (
            ("active_metrics", active),
            ("supported_empty_metrics", empty),
            ("unsupported_metrics", unsupported),
        ):
            for item in payload.get(key) or []:
                metric = item.get("metric") if isinstance(item, dict) else None
                if metric:
                    target[platform].add(str(metric))
    return active, empty, unsupported


def _metric_detail(platform: str, metric: str) -> dict[str, Any]:
    spec = (META_METRIC_MAP if platform == "meta" else TIKTOK_METRIC_MAP).get(metric)
    if spec:
        return spec.to_dict()
    return {"platform": platform, "metric": metric, "label": metric, "category": "unknown"}


def _split_by_probe(platform: str, metrics: list[str], active: set[str], empty: set[str], unsupported: set[str]) -> dict[str, list[dict[str, Any]]]:
    active_rows = [_metric_detail(platform, metric) for metric in metrics if metric in active]
    empty_rows = [_metric_detail(platform, metric) for metric in metrics if metric not in active and metric not in unsupported]
    unavailable_rows = [_metric_detail(platform, metric) for metric in metrics if metric in unsupported]
    return {
        "recommended_active": active_rows,
        "recommended_supported_but_empty": [row for row in empty_rows if row["metric"] in empty],
        "recommended_not_probed": [row for row in empty_rows if row["metric"] not in empty],
        "not_available": unavailable_rows,
    }


def recommend_metric_presets(
    *,
    platform: str,
    user_type: str,
    probe: dict[str, Any] | None = None,
    w2a: bool = False,
) -> dict[str, Any]:
    normalized = normalize_user_type(user_type)
    vertical = VERTICAL_PRESETS.get(normalized, {})
    preset = _merge_presets(BASE_PRESET, vertical)
    if w2a:
        preset = _merge_presets(preset, W2A_METRICS)

    active, empty, unsupported = _probe_active_sets(probe)
    platforms = ["meta", "tiktok"] if platform == "all" else [platform]
    metrics_by_platform: dict[str, dict[str, list[str]]] = {}
    active_recommended: dict[str, list[dict[str, Any]]] = {}
    missing_or_empty: dict[str, dict[str, list[dict[str, Any]]]] = {}
    not_available: dict[str, list[dict[str, Any]]] = {}
    core_coverage: dict[str, dict[str, Any]] = {}

    for current in platforms:
        by_bucket: dict[str, list[str]] = {}
        all_metrics: list[str] = []
        for bucket, platform_metrics in preset.items():
            metrics = _dedupe(platform_metrics.get(current, []))
            by_bucket[bucket] = metrics
            all_metrics.extend(metrics)
        all_metrics = _dedupe(all_metrics)
        metrics_by_platform[current] = by_bucket
        split = _split_by_probe(current, all_metrics, active[current], empty[current], unsupported[current])
        active_recommended[current] = split["recommended_active"]
        missing_or_empty[current] = {
            "supported_but_empty": split["recommended_supported_but_empty"],
            "not_probed": split["recommended_not_probed"],
        }
        not_available[current] = split["not_available"]
        core_coverage[current] = build_core_metric_coverage(platform=current, probe=probe)

    notes = [
        "core 指标必须覆盖展示、点击、消耗、转化、收入；缺失项需要用 full metric probe 补齐或明确标注为数据缺口。",
        f"{user_type} 预设补充了该垂类更常用的转化、价值/ROAS、视频创意与垂类事件。",
    ]
    if probe:
        notes.append("已合并探针结果：非零有效指标进入 recommended_active，空值或未探测指标会保留为建议但标注状态。")
    if w2a:
        notes.append("W2A 已按 App 路径处理，优先保留商店点击、安装、SKAN/SAN 与 App 事件指标。")

    return {
        "user_type": user_type,
        "confidence": {
            "source": "explicit_user_type",
            "normalized": normalized,
            "probe_file_used": bool(probe),
            "w2a": w2a,
        },
        "preset_name": f"{normalized}_analysis",
        "metrics_by_platform": metrics_by_platform,
        "core_metric_coverage": core_coverage,
        "active_recommended_metrics": active_recommended,
        "missing_or_empty_recommended_metrics": missing_or_empty,
        "not_available": not_available,
        "analysis_notes": notes,
    }
