from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from motata_cli.init_profile import (
    load_init_profile,
    resolve_default_access_token,
    resolve_default_account,
    resolve_default_accounts,
    resolve_init_metrics,
)
from motata_cli.meta.commands import CliError
from motata_cli.meta.output import print_output
from motata_cli.report.meta import PeriodWindow, resolve_period
from motata_cli.report.activity_factors import build_activity_factor_report, rank_activity_targets
from motata_cli.tiktok.app_discovery import APP_ADGROUP_FIELDS, APP_CAMPAIGN_FIELDS, build_tiktok_app_report
from motata_cli.tiktok.activities import build_tiktok_activities_report, summarize_tiktok_activities
from motata_cli.tiktok.audience import build_tiktok_audience_breakdown
from motata_cli.tiktok.client import TikTokClient
from motata_cli.tiktok.creative_retention import build_tiktok_creative_retention_report
from motata_cli.tiktok.landing_pages import (
    DEFAULT_AD_FIELDS,
    _dimension,
    _extract_collection,
    _fnum,
    _list_ads_by_ids,
    _metric,
    build_tiktok_landing_page_report,
)
from motata_cli.tiktok.user_type import build_tiktok_user_type_report


TIKTOK_LEVELS = {
    "advertiser": {
        "data_level": "AUCTION_ADVERTISER",
        "dimensions": ["advertiser_id"],
        "attributes": ["advertiser_name", "advertiser_id", "currency"],
        "id_key": "advertiser_id",
    },
    "campaign": {
        "data_level": "AUCTION_CAMPAIGN",
        "dimensions": ["campaign_id"],
        "attributes": [
            "campaign_automation_type",
            "campaign_name",
            "campaign_id",
            "objective_type",
            "campaign_budget",
            "campaign_dedicate_type",
            "app_promotion_type",
            "currency",
        ],
        "id_key": "campaign_id",
    },
    "adgroup": {
        "data_level": "AUCTION_ADGROUP",
        "dimensions": ["adgroup_id"],
        "attributes": [
            "campaign_automation_type",
            "campaign_name",
            "campaign_id",
            "objective_type",
            "adgroup_name",
            "adgroup_id",
            "placement_type",
            "promotion_type",
            "billing_event",
            "currency",
        ],
        "id_key": "adgroup_id",
    },
    "ad": {
        "data_level": "AUCTION_AD",
        "dimensions": ["ad_id"],
        "attributes": [
            "campaign_automation_type",
            "campaign_name",
            "campaign_id",
            "objective_type",
            "adgroup_name",
            "adgroup_id",
            "promotion_type",
            "ad_name",
            "ad_id",
            "ad_text",
            "call_to_action",
            "ad_url",
            "tt_app_id",
            "tt_app_name",
            "mobile_app_id",
            "image_mode",
            "currency",
            "is_smart_creative",
        ],
        "id_key": "ad_id",
    },
    "ad_v2": {
        "data_level": "AUCTION_AD",
        "dimensions": ["ad_id_v2"],
        "attributes": [
            "campaign_automation_type",
            "campaign_name",
            "campaign_id",
            "objective_type",
            "adgroup_name",
            "adgroup_id",
            "promotion_type",
            "ad_name",
            "ad_url",
            "tt_app_id",
            "tt_app_name",
            "mobile_app_id",
            "image_mode",
            "currency",
            "is_smart_creative",
        ],
        "id_key": "ad_id_v2",
    },
}

TIKTOK_VALUE_METRICS = [
    "total_purchase",
    "total_purchase_value",
    "total_active_pay_roas",
    "complete_payment",
    "complete_payment_roas",
    "value_per_complete_payment",
    "onsite_total_purchase",
    "onsite_total_purchase_value",
    "onsite_purchases_roas",
    "shop_total_purchase_by_order_submission",
    "shop_gross_revenue_by_order_submission",
]

TIKTOK_CORE_METRICS = [
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "reach",
    "frequency",
    "conversion",
    "cost_per_conversion",
    "conversion_rate_v2",
    "result",
    "cost_per_result",
    "result_rate",
]

TIKTOK_LEAN_METRICS = ["spend", "impressions", "clicks", "conversion"]

TIKTOK_TARGETED_ACTIVITY_OPERATION_TYPES = ["CREATE", "STATUS", "UPDATE"]

TIKTOK_TARGETED_ACTIVITY_OBJECTS = {
    "campaign": {"id_key": "campaign_id", "object_type": "CAMPAIGN"},
    "adgroup": {"id_key": "adgroup_id", "object_type": "ADGROUP"},
    "ad": {"id_key": "ad_id", "object_type": "AD"},
}

TIKTOK_REPORT_MODES = ("auto", "auction", "gmv_max", "hybrid")

TIKTOK_GMV_MAX_ACCOUNT_METRICS = ["cost", "orders", "cost_per_order", "gross_revenue", "roi", "net_cost"]
TIKTOK_GMV_MAX_CAMPAIGN_METRICS = [
    "roas_bid",
    "cost",
    "net_cost",
    "orders",
    "cost_per_order",
    "gross_revenue",
    "roi",
]
TIKTOK_GMV_MAX_PRODUCT_METRICS = ["product_status", "orders", "gross_revenue"]
TIKTOK_GMV_MAX_CREATIVE_METRICS = [
    "creative_delivery_status",
    "cost",
    "orders",
    "cost_per_order",
    "gross_revenue",
    "roi",
    "product_impressions",
    "product_clicks",
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
TIKTOK_GMV_MAX_DURATION_METRICS = [
    "cost",
    "orders",
    "cost_per_order",
    "gross_revenue",
    "roi",
    "roas_bid",
]
TIKTOK_GMV_MAX_REPORT_PROMOTION_TYPE_MAP = {
    "PRODUCT_GMV_MAX": "PRODUCT",
    "LIVE_GMV_MAX": "LIVE",
    "PRODUCT": "PRODUCT",
    "LIVE": "LIVE",
}

TIKTOK_GMV_MAX_REPORT_LEVELS = {
    "account": {
        "dimensions": ["advertiser_id"],
        "promotion_types": ["PRODUCT_GMV_MAX", "LIVE_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_ACCOUNT_METRICS,
    },
    "campaign": {
        "dimensions": ["campaign_id"],
        "promotion_types": ["PRODUCT_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_CAMPAIGN_METRICS,
    },
    "campaign_day": {
        "dimensions": ["campaign_id", "stat_time_day"],
        "promotion_types": ["PRODUCT_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_CAMPAIGN_METRICS,
    },
    "product": {
        "dimensions": ["item_group_id"],
        "promotion_types": ["PRODUCT_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_PRODUCT_METRICS,
    },
    "creative": {
        "dimensions": ["campaign_id", "item_group_id", "item_id"],
        "promotion_types": ["PRODUCT_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_CREATIVE_METRICS,
    },
    "duration": {
        "dimensions": ["duration"],
        "promotion_types": ["PRODUCT_GMV_MAX"],
        "metrics": TIKTOK_GMV_MAX_DURATION_METRICS,
    },
}

TIKTOK_GMV_MAX_DEPTH_LEVELS = {
    "fast": ("account", "campaign"),
    "standard": ("account", "campaign", "campaign_day", "product", "creative"),
    "full": (
        "account",
        "campaign",
        "campaign_day",
        "product",
        "creative",
        "duration",
    ),
    "deep": (
        "account",
        "campaign",
        "campaign_day",
        "product",
        "creative",
        "duration",
    ),
}


@dataclass(frozen=True)
class TikTokDepthPlan:
    depth: str
    insight_levels: tuple[str, ...]
    previous_levels: tuple[str, ...]
    audience_breakdowns: tuple[str, ...]
    include_apps: bool
    include_landing: bool
    include_structure: bool
    include_creative_retention: bool
    landing_top: int | None
    landing_ad_limit: int | None
    landing_max_pages: int
    structure_mode: str
    creative_target_count: int
    page_size: int
    insight_max_pages: int
    include_previews: bool


def tiktok_depth_plan(depth: str) -> TikTokDepthPlan:
    plans = {
        "fast": TikTokDepthPlan(
            depth="fast",
            insight_levels=("advertiser", "campaign", "ad"),
            previous_levels=("advertiser", "campaign"),
            audience_breakdowns=("country",),
            include_apps=False,
            include_landing=True,
            include_structure=False,
            include_creative_retention=False,
            landing_top=50,
            landing_ad_limit=200,
            landing_max_pages=3,
            structure_mode="none",
            creative_target_count=0,
            page_size=1000,
            insight_max_pages=1,
            include_previews=False,
        ),
        "standard": TikTokDepthPlan(
            depth="standard",
            insight_levels=("advertiser", "campaign", "adgroup", "ad"),
            previous_levels=("advertiser", "campaign", "adgroup", "ad"),
            audience_breakdowns=("country", "age_gender", "placement"),
            include_apps=True,
            include_landing=True,
            include_structure=True,
            include_creative_retention=True,
            landing_top=120,
            landing_ad_limit=800,
            landing_max_pages=10,
            structure_mode="top",
            creative_target_count=30,
            page_size=1000,
            insight_max_pages=3,
            include_previews=True,
        ),
        "full": TikTokDepthPlan(
            depth="full",
            insight_levels=("advertiser", "campaign", "adgroup", "ad"),
            previous_levels=("advertiser", "campaign", "adgroup", "ad"),
            audience_breakdowns=("country", "age_gender", "placement", "device"),
            include_apps=True,
            include_landing=True,
            include_structure=True,
            include_creative_retention=True,
            landing_top=100,
            landing_ad_limit=400,
            landing_max_pages=25,
            structure_mode="top",
            creative_target_count=60,
            page_size=1000,
            insight_max_pages=10,
            include_previews=True,
        ),
        "deep": TikTokDepthPlan(
            depth="deep",
            insight_levels=("advertiser", "campaign", "adgroup", "ad"),
            previous_levels=("advertiser", "campaign", "adgroup", "ad"),
            audience_breakdowns=("country", "age_gender", "placement", "device"),
            include_apps=True,
            include_landing=True,
            include_structure=True,
            include_creative_retention=True,
            landing_top=None,
            landing_ad_limit=None,
            landing_max_pages=50,
            structure_mode="all",
            creative_target_count=100,
            page_size=1000,
            insight_max_pages=50,
            include_previews=True,
        ),
    }
    return plans[depth]


def default_run_dir(advertiser_id: str, period: str, depth: str, window: PeriodWindow) -> Path:
    return Path("build") / "report_runs" / f"tiktok_{advertiser_id}_{period}_{depth}_{window.until}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _row_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("rows", "data", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _dedupe(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(values) if value]


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _page_info(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict) and isinstance(data.get("page_info"), dict):
        return data["page_info"]
    if isinstance(response.get("page_info"), dict):
        return response["page_info"]
    return {}


def _total_pages(response: dict[str, Any]) -> int:
    info = _page_info(response)
    total_page = _fnum(info.get("total_page") or info.get("total_pages"))
    return int(total_page) if total_page else 0


def _daily_metric_sets(preferred_metrics: list[str] | None = None) -> list[list[str]]:
    extras = list(preferred_metrics or [])
    return [
        _dedupe([*TIKTOK_CORE_METRICS, *TIKTOK_VALUE_METRICS, *extras]),
        _dedupe([*TIKTOK_CORE_METRICS, *extras]),
        list(TIKTOK_LEAN_METRICS),
    ]


def _metric_sets(level: str, preferred_metrics: list[str] | None = None) -> list[list[str]]:
    spec = TIKTOK_LEVELS[level]
    attributes = list(spec["attributes"])
    extras = list(preferred_metrics or [])
    return [
        _dedupe([*TIKTOK_CORE_METRICS, *TIKTOK_VALUE_METRICS, *extras, *attributes]),
        _dedupe([*TIKTOK_CORE_METRICS, *extras, *attributes]),
        _dedupe([*TIKTOK_LEAN_METRICS, *attributes]),
        list(TIKTOK_LEAN_METRICS),
    ]


def _pull_basic_report(
    client: TikTokClient,
    advertiser_id: str,
    level: str,
    window: tuple[str, str],
    *,
    page_size: int,
    max_pages: int | None = None,
    filtering: list[dict[str, Any]] | None = None,
    preferred_metrics: list[str] | None = None,
) -> dict[str, Any]:
    spec = TIKTOK_LEVELS[level]
    warnings: list[dict[str, Any]] = []
    last_error = ""
    for metrics in _metric_sets(level, preferred_metrics):
        rows: list[dict[str, Any]] = []
        try:
            for page in range(1, (max_pages or 1) + 1):
                response = client.integrated_report(
                    "BASIC",
                    advertiser_id=advertiser_id,
                    data_level=str(spec["data_level"]),
                    dimensions=list(spec["dimensions"]),
                    metrics=metrics,
                    start_date=window[0],
                    end_date=window[1],
                    order_field="spend",
                    order_type="DESC",
                    page=page,
                    page_size=page_size,
                    filtering=filtering,
                )
                page_rows = _extract_collection(response, "list")
                rows.extend(page_rows)
                if len(page_rows) < page_size:
                    break
            return {
                "platform": "tiktok",
                "report_type": "BASIC",
                "data_level": spec["data_level"],
                "dimensions": spec["dimensions"],
                "metrics": metrics,
                "start_date": window[0],
                "end_date": window[1],
                "rows": rows,
                "row_count": len(rows),
                "warnings": warnings,
            }
        except Exception as exc:
            last_error = str(exc)
            warnings.append({"fallback": "retrying with a narrower TikTok metric set", "error": last_error, "metrics": metrics})
    raise RuntimeError(last_error or f"TikTok {level} report failed")


def _row_performance_score(row: dict[str, Any]) -> tuple[float, float, float]:
    revenue = max(
        _fnum(_metric(row, "total_purchase_value")),
        _fnum(_metric(row, "shop_gross_revenue_by_order_submission")),
    )
    spend = _fnum(_metric(row, "spend"))
    for roas_key in ("total_active_pay_roas", "complete_payment_roas", "onsite_purchases_roas"):
        roas = _fnum(_metric(row, roas_key))
        if spend and roas:
            revenue = max(revenue, spend * roas)
    conversion = max(_fnum(_metric(row, "conversion")), _fnum(_metric(row, "result")), _fnum(_metric(row, "complete_payment")))
    return (revenue, conversion, spend)


def _gmv_dimension(row: dict[str, Any], key: str) -> Any:
    dimensions = row.get("dimensions")
    if isinstance(dimensions, dict):
        return dimensions.get(key)
    return row.get(key)


def _gmv_metric(row: dict[str, Any], key: str) -> Any:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        return metrics.get(key)
    return row.get(key)


def _gmv_item_scope(item_id: Any) -> str:
    return "PRODUCT_CARD" if str(item_id) == "-1" else "SPECIFIC_ITEM"


def _annotate_gmv_max_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        dimensions = row.get("dimensions")
        if not isinstance(dimensions, dict) or "item_id" not in dimensions:
            continue
        dimensions.setdefault("item_scope", _gmv_item_scope(dimensions.get("item_id")))
    return rows


def _summarize_gmv_max_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(_fnum(_gmv_metric(row, "cost")) for row in rows)
    net_cost = sum(_fnum(_gmv_metric(row, "net_cost")) for row in rows)
    orders = sum(_fnum(_gmv_metric(row, "orders")) for row in rows)
    gross_revenue = sum(_fnum(_gmv_metric(row, "gross_revenue")) for row in rows)
    return {
        "cost": round(cost, 2),
        "net_cost": round(net_cost, 2),
        "orders": round(orders, 2),
        "gross_revenue": round(gross_revenue, 2),
        "roi": round(gross_revenue / cost, 4) if cost else 0.0,
        "cost_per_order": round(cost / orders, 4) if orders else 0.0,
    }


def _extract_gmv_max_stores(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else {}
    stores = data.get("store_list") if isinstance(data, dict) else None
    return [store for store in stores if isinstance(store, dict)] if isinstance(stores, list) else []


def _available_gmv_max_store_ids(stores: list[dict[str, Any]], advertiser_id: str) -> list[str]:
    ids: list[str] = []
    for store in stores:
        if not store.get("is_gmv_max_available"):
            continue
        exclusive = store.get("exclusive_authorized_advertiser_info")
        exclusive_id = str((exclusive or {}).get("advertiser_id") or "").strip() if isinstance(exclusive, dict) else ""
        if exclusive_id and exclusive_id != str(advertiser_id):
            continue
        store_id = str(store.get("store_id") or "").strip()
        if store_id and store_id not in ids:
            ids.append(store_id)
    return ids


def _campaign_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _extract_collection(response, "list", "campaigns") if isinstance(row, dict)]


def _sum_auction_spend(rows: list[dict[str, Any]]) -> float:
    return round(sum(_fnum(_metric(row, "spend")) for row in rows), 2)


def _gmv_max_report_promotion_types(values: list[str]) -> list[str]:
    mapped = [TIKTOK_GMV_MAX_REPORT_PROMOTION_TYPE_MAP.get(value, value) for value in values]
    return _dedupe([value for value in mapped if value])


def _top_ids(rows: list[dict[str, Any]], key: str, limit: int) -> list[str]:
    ids: list[str] = []
    for row in sorted(rows, key=_row_performance_score, reverse=True):
        value = _dimension(row, key) or _metric(row, key)
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)
        if len(ids) >= limit:
            break
    return ids


def _top_activity_ids(rows: list[dict[str, Any]], key: str, limit: int, *, extra_keys: tuple[str, ...] = ()) -> list[str]:
    ids: list[str] = []
    for row in sorted(rows, key=_row_performance_score, reverse=True):
        for candidate_key in (key, *extra_keys):
            value = _dimension(row, candidate_key) or _metric(row, candidate_key)
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)
            if len(ids) >= limit:
                return ids
    return ids


def _report_target_filtering(field_name: str, ids: list[str]) -> list[dict[str, Any]] | None:
    clean_ids = [str(value).strip() for value in ids if str(value).strip()]
    if not clean_ids:
        return None
    return [{"field_name": field_name, "filter_type": "IN", "filter_value": json.dumps(clean_ids)}]


def _list_campaigns_by_ids(client: TikTokClient, advertiser_id: str, campaign_ids: list[str], *, page_size: int, smart_plus: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in _chunked(campaign_ids, 20):
        response = client.list_campaigns(
            advertiser_id,
            filtering={"campaign_ids": batch},
            page=1,
            page_size=max(len(batch), 1),
            fields=APP_CAMPAIGN_FIELDS,
            smart_plus=smart_plus,
        )
        rows.extend(_extract_collection(response, "list", "campaigns"))
    return rows[:page_size] if page_size > 0 else rows


def _list_adgroups_by_ids(client: TikTokClient, advertiser_id: str, adgroup_ids: list[str], *, page_size: int, smart_plus: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in _chunked(adgroup_ids, 20):
        response = client.list_adgroups(
            advertiser_id,
            filtering={"adgroup_ids": batch},
            page=1,
            page_size=max(len(batch), 1),
            fields=APP_ADGROUP_FIELDS,
            smart_plus=smart_plus,
        )
        rows.extend(_extract_collection(response, "list", "adgroups"))
    return rows[:page_size] if page_size > 0 else rows


def _list_all_structure(client: TikTokClient, advertiser_id: str, entity: str, *, page_size: int, smart_plus: bool) -> list[dict[str, Any]]:
    list_fn = {
        "campaign": client.list_campaigns,
        "adgroup": client.list_adgroups,
        "ad": client.list_ads,
    }[entity]
    fields = {
        "campaign": APP_CAMPAIGN_FIELDS,
        "adgroup": APP_ADGROUP_FIELDS,
        "ad": DEFAULT_AD_FIELDS,
    }[entity]
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        response = list_fn(advertiser_id, page=page, page_size=page_size, fields=fields, smart_plus=smart_plus)
        page_rows = _extract_collection(response, "list", "campaigns", "adgroups", "ads")
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break
        page += 1
    return rows


def _parse_multi_values(raw: str | None) -> list[str]:
    if raw in (None, ""):
        return []
    values = [item.strip() for item in str(raw).replace("\n", ",").split(",") if item.strip()]
    return list(dict.fromkeys(values))


def _resolve_tiktok_report_account_ids(args: argparse.Namespace) -> tuple[list[str], str | None]:
    explicit = _parse_multi_values(getattr(args, "advertiser_id", None))
    if explicit:
        return explicit, "cli"
    if getattr(args, "all_init_accounts", False):
        advertiser_ids, source = resolve_default_accounts("tiktok")
        if advertiser_ids:
            return advertiser_ids, source
        return [], source
    advertiser_id, source = resolve_default_account("tiktok")
    return ([advertiser_id] if advertiser_id else []), source


def _clone_args(args: argparse.Namespace, **updates: Any) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(updates)
    return argparse.Namespace(**values)


def _batch_run_dir(base_run_dir: str | None, advertiser_id: str) -> str | None:
    if not base_run_dir:
        return None
    return str(Path(base_run_dir) / f"advertiser_{advertiser_id}")


def _prepare_tiktok_report_args(args: argparse.Namespace) -> argparse.Namespace:
    profile = load_init_profile("tiktok")
    advertiser_ids, account_source = _resolve_tiktok_report_account_ids(args)
    if not advertiser_ids:
        raise CliError("Missing TikTok advertiser account. Provide --advertiser-id, or run `motata init` first.")
    args.advertiser_id = advertiser_ids[0]
    args.advertiser_ids = advertiser_ids
    args.account_source = account_source

    if not getattr(args, "access_token", None):
        access_token, token_source = resolve_default_access_token("tiktok")
        if not access_token and getattr(args, "dry_run", False):
            args.access_token = None
            args.access_token_source = "not_required_dry_run"
        elif not access_token:
            raise CliError(
                "Missing TikTok access token. Provide --access-token, or set TIKTOK_ACCESS_TOKEN / MOTATA_TIKTOK_ACCESS_TOKEN. "
                "`motata init` stores account defaults but does not persist tokens."
            )
        else:
            args.access_token = access_token
            args.access_token_source = token_source
    else:
        args.access_token_source = "cli"

    analysis_metrics = resolve_init_metrics("tiktok")
    args.analysis_metrics = analysis_metrics
    args.analysis_metrics_source = "init_profile" if analysis_metrics else "default_report_fields"
    args.init_profile = profile
    return args


class TikTokReportRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.window = resolve_period(args)
        self.plan = tiktok_depth_plan(args.depth)
        self.analysis_metrics = _dedupe(list(getattr(args, "analysis_metrics", None) or []))
        requested_include_previews = getattr(args, "include_previews", None)
        self.include_previews = self.plan.include_previews if requested_include_previews is None else bool(requested_include_previews)
        self.run_dir = Path(args.run_dir) if args.run_dir else default_run_dir(args.advertiser_id, args.period, args.depth, self.window)
        self.page_size = args.page_size or self.plan.page_size
        self.requested_report_mode = getattr(args, "tiktok_report_mode", "auto") or "auto"
        self.gmv_max_mode = getattr(args, "include_gmv_max", "auto") or "auto"
        self.gmv_max_store_ids = _dedupe(list(getattr(args, "gmv_max_store_ids", None) or []))
        self.gmv_max_promotion_types = _dedupe(list(getattr(args, "gmv_max_promotion_types", None) or [])) or [
            "PRODUCT_GMV_MAX",
            "LIVE_GMV_MAX",
        ]
        self.gmv_max_creative_dimensions = getattr(args, "gmv_max_creative_dimensions", "official") or "official"
        activity_strategy = "broad_changelog" if args.depth == "deep" else "targeted_top_objects_changelog"
        if self.requested_report_mode == "gmv_max":
            activity_strategy = "not_applicable_gmv_max"
        elif self.requested_report_mode == "auto":
            activity_strategy = "auto_mode_probe_then_mode_specific"
        self.ad_detail_cache: dict[str, dict[str, Any]] = {}
        self.manifest: dict[str, Any] = {
            "platform": "tiktok",
            "advertiser_id": args.advertiser_id,
            "advertiser_ids": list(getattr(args, "advertiser_ids", [args.advertiser_id])),
            "account_source": getattr(args, "account_source", "cli"),
            "period": args.period,
            "depth": args.depth,
            "window": {
                "since": self.window.since,
                "until": self.window.until,
                "previous_since": self.window.previous_since,
                "previous_until": self.window.previous_until,
            },
            "options": {
                "access_token_source": getattr(args, "access_token_source", "cli"),
                "include_previews": self.include_previews,
                "include_previews_source": "depth_default" if requested_include_previews is None else "cli",
                "activity_strategy": activity_strategy,
                "activity_operation_types": TIKTOK_TARGETED_ACTIVITY_OPERATION_TYPES,
                "analysis_metrics": self.analysis_metrics,
                "analysis_metrics_source": getattr(args, "analysis_metrics_source", "default_report_fields"),
            },
            "tiktok_report_mode": "planned" if self.requested_report_mode == "auto" else self.requested_report_mode,
            "mode_reason": "dry-run plan pending live probes" if self.requested_report_mode == "auto" else "explicit CLI mode",
            "gmv_max": {
                "enabled": self.gmv_max_mode != "never",
                "mode": self.gmv_max_mode,
                "store_ids": self.gmv_max_store_ids,
                "promotion_types": self.gmv_max_promotion_types,
                "report_promotion_types": _gmv_max_report_promotion_types(self.gmv_max_promotion_types),
                "metric_sets_by_level": {
                    level: list(spec["metrics"]) for level, spec in TIKTOK_GMV_MAX_REPORT_LEVELS.items()
                },
                "creative_metric_set": TIKTOK_GMV_MAX_CREATIVE_METRICS,
                "creative_dimensions_mode": self.gmv_max_creative_dimensions,
                "coverage": "planned" if self.gmv_max_mode != "never" else "disabled",
            },
            "init_profile": getattr(args, "init_profile", {}) or None,
            "run_dir": str(self.run_dir),
            "sources": [],
        }

    def source_names(self) -> list[str]:
        if self.requested_report_mode == "gmv_max":
            return self.gmv_max_source_names()
        if self.requested_report_mode == "hybrid":
            return self.auction_source_names() + [name for name in self.gmv_max_source_names() if name not in self.auction_source_names()]
        if self.requested_report_mode == "auto":
            return ["gmv_max_stores", "gmv_max_campaigns_product", "gmv_max_campaigns_live", "auction_probe", "auto_mode_decision"]
        return self.auction_source_names()

    def auction_source_names(self) -> list[str]:
        names = ["user_type", "activities", "activity_targeted_insights", "activity_daily_breakdown", "activity_factors"]
        if self.plan.include_apps:
            names.append("apps")
        names.extend([f"current_{level}_insights" for level in self.plan.insight_levels])
        if self.plan.include_landing:
            names.append("current_ad_v2_insights")
        if self.window.has_previous:
            names.extend([f"previous_{level}_insights" for level in self.plan.previous_levels])
        if self.plan.audience_breakdowns:
            names.append("audience_breakdown")
        if self.plan.include_landing:
            names.append("landing_pages")
        if self.plan.include_structure:
            names.extend(["campaign_structure", "adgroup_structure", "ad_structure"])
        if self.plan.include_creative_retention:
            names.append("targeted_creative_retention")
        return names

    def gmv_max_source_names(self) -> list[str]:
        names = ["gmv_max_stores", "gmv_max_campaigns_product"]
        if self.plan.depth in ("standard", "full", "deep"):
            names.append("gmv_max_campaigns_live")
        for level in TIKTOK_GMV_MAX_DEPTH_LEVELS[self.plan.depth]:
            if level == "live" and "LIVE_GMV_MAX" not in self.gmv_max_promotion_types:
                continue
            names.append(f"current_gmv_max_{level}")
        if self.window.has_previous:
            for level in TIKTOK_GMV_MAX_DEPTH_LEVELS[self.plan.depth]:
                if level == "live" and "LIVE_GMV_MAX" not in self.gmv_max_promotion_types:
                    continue
                names.append(f"previous_gmv_max_{level}")
        return names

    def dry_run_payload(self) -> dict[str, Any]:
        payload = dict(self.manifest)
        payload["sources"] = [{"name": name, "status": "planned"} for name in self.source_names()]
        return payload

    def record(self, name: str, status: str, path: str | None = None, **extra: Any) -> None:
        item = {"name": name, "status": status}
        if path:
            item["path"] = path
        item.update(extra)
        self.manifest["sources"].append(item)

    def run_source(self, name: str, fn: Callable[[], Any]) -> Any:
        path = self.run_dir / f"{name}.json"
        attempts = max(1, int(self.args.retry or 1))
        last_error = ""
        source_started_at = time.monotonic()
        for attempt in range(1, attempts + 1):
            try:
                payload = fn()
                _write_json(path, payload)
                self.record(
                    name,
                    "ok",
                    str(path),
                    attempts=attempt,
                    rows=_row_count(payload),
                    duration_sec=round(time.monotonic() - source_started_at, 3),
                )
                return payload
            except Exception as exc:
                last_error = str(exc)
                if attempt < attempts:
                    time.sleep(max(0.0, float(self.args.retry_wait or 0.0)))
        error_payload = {"error": last_error, "source": name, "status": "degraded"}
        _write_json(path, error_payload)
        self.record(
            name,
            "degraded",
            str(path),
            error=last_error,
            attempts=attempts,
            duration_sec=round(time.monotonic() - source_started_at, 3),
        )
        return error_payload

    def run(self) -> dict[str, Any]:
        from motata_cli.tiktok.commands import resolve_tiktok_client

        if self.args.dry_run:
            return self.dry_run_payload()

        advertiser_id, client = resolve_tiktok_client(self.args)
        self.run_dir.mkdir(parents=True, exist_ok=True)

        if self.requested_report_mode == "gmv_max":
            self._set_report_mode("gmv_max", "explicit CLI mode")
            self._run_gmv_max_sources(client, advertiser_id)
            _write_json(self.run_dir / "manifest.json", self.manifest)
            return self.manifest

        if self.requested_report_mode == "auction":
            self._set_report_mode("auction", "explicit CLI mode")
            current_rows, previous_rows = self._run_auction_sources(client, advertiser_id)
            if self.gmv_max_mode == "always":
                self._run_gmv_max_sources(client, advertiser_id)
            _write_json(self.run_dir / "manifest.json", self.manifest)
            return self.manifest

        if self.requested_report_mode == "hybrid":
            self._set_report_mode("hybrid", "explicit CLI mode")
            self._run_gmv_max_sources(client, advertiser_id)
            self._run_auction_sources(client, advertiser_id)
            _write_json(self.run_dir / "manifest.json", self.manifest)
            return self.manifest

        stores_payload = self._run_gmv_max_stores_source(client, advertiser_id) if self.gmv_max_mode != "never" else {}
        product_campaigns = (
            self._run_gmv_max_campaigns_source(client, advertiser_id, "PRODUCT_GMV_MAX", stores_payload=stores_payload)
            if self.gmv_max_mode != "never"
            else {}
        )
        live_campaigns = (
            self._run_gmv_max_campaigns_source(client, advertiser_id, "LIVE_GMV_MAX", stores_payload=stores_payload)
            if self.gmv_max_mode != "never"
            else {}
        )
        auction_probe = self.run_source(
            "auction_probe",
            lambda: _pull_basic_report(
                client,
                advertiser_id,
                "advertiser",
                (self.window.since, self.window.until),
                page_size=1,
                max_pages=1,
                preferred_metrics=self.analysis_metrics,
            ),
        )
        decision = self._decide_report_mode(stores_payload, product_campaigns, live_campaigns, auction_probe)
        self.run_source("auto_mode_decision", lambda: decision)
        self._set_report_mode(str(decision["mode"]), str(decision["reason"]))

        if decision["mode"] == "gmv_max":
            self._run_gmv_max_sources(
                client,
                advertiser_id,
                preloaded={
                    "gmv_max_stores": stores_payload,
                    "gmv_max_campaigns_product": product_campaigns,
                    "gmv_max_campaigns_live": live_campaigns,
                },
            )
        elif decision["mode"] == "hybrid":
            self._run_gmv_max_sources(
                client,
                advertiser_id,
                preloaded={
                    "gmv_max_stores": stores_payload,
                    "gmv_max_campaigns_product": product_campaigns,
                    "gmv_max_campaigns_live": live_campaigns,
                },
            )
            self._run_auction_sources(client, advertiser_id)
        else:
            self._run_auction_sources(client, advertiser_id)

        _write_json(self.run_dir / "manifest.json", self.manifest)
        return self.manifest

    def _run_auction_sources(self, client: TikTokClient, advertiser_id: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        self.run_source(
            "user_type",
            lambda: build_tiktok_user_type_report(
                client,
                advertiser_ids=[advertiser_id],
                start_date=self.window.since,
                end_date=self.window.until,
                campaign_limit=10,
                ad_limit=5,
                content_limit=60,
                page_size=self.page_size,
                max_pages=max(1, min(self.plan.landing_max_pages, 10)),
                smart_plus=bool(self.args.smart_plus),
                include_evidence=False,
            ),
        )

        if self.plan.include_apps:
            self.run_source(
                "apps",
                lambda: build_tiktok_app_report(
                    client,
                    advertiser_ids=[advertiser_id],
                    start_date=self.window.since,
                    end_date=self.window.until,
                    advertiser_limit=1,
                    campaign_limit=30,
                    include_campaigns=True,
                ),
            )

        current_rows: dict[str, list[dict[str, Any]]] = {}
        previous_rows: dict[str, list[dict[str, Any]]] = {}
        for level in self.plan.insight_levels:
            payload = self.run_source(
                f"current_{level}_insights",
                lambda level=level: _pull_basic_report(
                    client,
                    advertiser_id,
                    level,
                    (self.window.since, self.window.until),
                    page_size=self.page_size,
                    max_pages=self.plan.insight_max_pages,
                    preferred_metrics=self.analysis_metrics,
                ),
            )
            if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                current_rows[level] = payload["rows"]

        if self.plan.include_landing:
            payload = self.run_source(
                "current_ad_v2_insights",
                lambda: _pull_basic_report(
                    client,
                    advertiser_id,
                    "ad_v2",
                    (self.window.since, self.window.until),
                    page_size=self.page_size,
                    max_pages=self.plan.insight_max_pages,
                    preferred_metrics=self.analysis_metrics,
                ),
            )
            if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                current_rows["ad_v2"] = payload["rows"]

        if self.window.has_previous:
            for level in self.plan.previous_levels:
                payload = self.run_source(
                    f"previous_{level}_insights",
                    lambda level=level: _pull_basic_report(
                        client,
                        advertiser_id,
                        level,
                        (self.window.previous_since or "", self.window.previous_until or ""),
                        page_size=self.page_size,
                        max_pages=self.plan.insight_max_pages,
                        preferred_metrics=self.analysis_metrics,
                    ),
                )
                if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                    previous_rows[level] = payload["rows"]

        if self.plan.audience_breakdowns:
            audience = self.run_source(
                "audience_breakdown",
                lambda: build_tiktok_audience_breakdown(
                    client,
                    advertiser_id=advertiser_id,
                    start_date=self.window.since,
                    end_date=self.window.until,
                    page_size=min(self.page_size, 1000),
                    top=30,
                    breakdowns=list(self.plan.audience_breakdowns),
                ),
            )
            if isinstance(audience, dict):
                for breakdown, section in (audience.get("sections") or {}).items():
                    _write_json(self.run_dir / f"audience_{breakdown}.json", section)

        if self.plan.include_landing:
            self.run_source(
                "landing_pages",
                lambda: build_tiktok_landing_page_report(
                    client,
                    advertiser_ids=[advertiser_id],
                    start_date=self.window.since,
                    end_date=self.window.until,
                    top=self.plan.landing_top,
                    report_page_size=self.page_size,
                    max_pages=self.plan.landing_max_pages,
                    enrich_product=bool(self.args.include_product),
                    product_limit=50,
                    include_ads=True,
                    smart_plus=bool(self.args.smart_plus),
                    ad_limit=self.plan.landing_ad_limit,
                    ad_detail_cache=self.ad_detail_cache,
                    preloaded_report_rows=current_rows.get("ad") or [],
                    preloaded_asset_report_rows=current_rows.get("ad_v2") or [],
                ),
            )

        if self.plan.include_structure:
            if self.plan.structure_mode == "top":
                self._pull_top_structure(client, advertiser_id, current_rows)
            elif self.plan.structure_mode == "all":
                self._pull_all_structure(client, advertiser_id)

        if self.plan.include_creative_retention:
            target_count = max(1, int(self.args.top_objects or self.plan.creative_target_count))
            ad_ids = _top_ids(current_rows.get("ad") or [], "ad_id", target_count)
            self.run_source(
                "targeted_creative_retention",
                lambda: build_tiktok_creative_retention_report(
                    client,
                    advertiser_id=advertiser_id,
                    start_date=self.window.since,
                    end_date=self.window.until,
                    top=target_count,
                    page_size=200,
                    max_pages=1,
                    include_attributes=True,
                    include_previews=self.include_previews,
                    probe_on_missing_core=False,
                    target_ad_ids=ad_ids,
                    ad_details_by_id=self.ad_detail_cache,
                ),
            )

        activities = self.run_source(
            "activities",
            lambda: self._pull_activities(client, advertiser_id, current_rows),
        )
        targeted_insights = self.run_source(
            "activity_targeted_insights",
            lambda: self._pull_activity_targeted_insights(client, advertiser_id, activities),
        )
        activity_daily_breakdown = self.run_source(
            "activity_daily_breakdown",
            lambda: self._pull_activity_daily_breakdown(client, advertiser_id, activities),
        )
        self.run_source(
            "activity_factors",
            lambda: build_activity_factor_report(
                "tiktok",
                activities if isinstance(activities, dict) else {"rows": []},
                current_rows,
                previous_rows,
                targeted_insights=targeted_insights,
                daily_breakdown=activity_daily_breakdown,
                limit=8,
            ),
        )

        return current_rows, previous_rows

    def _set_report_mode(self, mode: str, reason: str) -> None:
        self.manifest["tiktok_report_mode"] = mode
        self.manifest["mode_reason"] = reason
        if mode == "gmv_max":
            self.manifest["options"]["activity_strategy"] = "not_applicable_gmv_max"
        elif mode == "hybrid":
            self.manifest["options"]["activity_strategy"] = (
                "auction_sources_only; gmv_max_sources_do_not_use_changelog"
            )
        else:
            self.manifest["options"]["activity_strategy"] = (
                "broad_changelog" if self.plan.depth == "deep" else "targeted_top_objects_changelog"
            )
        self.manifest["primary_sources"] = ["gmv_max_*"] if mode == "gmv_max" else ["auction_*"]
        if mode == "hybrid":
            self.manifest["primary_sources"] = ["gmv_max_*", "auction_*"]
            self.manifest["secondary_sources"] = []
        elif mode == "gmv_max":
            self.manifest["secondary_sources"] = ["auction_probe"]
        else:
            self.manifest["secondary_sources"] = ["gmv_max_*"] if self.gmv_max_mode == "always" else []

    def _selected_gmv_max_store_ids(self, stores_payload: Any, advertiser_id: str) -> list[str]:
        if self.gmv_max_store_ids:
            return self.gmv_max_store_ids
        stores = stores_payload.get("rows") if isinstance(stores_payload, dict) else None
        if isinstance(stores, list):
            return _available_gmv_max_store_ids(stores, advertiser_id)
        return []

    def _run_gmv_max_stores_source(self, client: TikTokClient, advertiser_id: str) -> dict[str, Any]:
        return self.run_source(
            "gmv_max_stores",
            lambda: self._pull_gmv_max_stores(client, advertiser_id),
        )

    def _pull_gmv_max_stores(self, client: TikTokClient, advertiser_id: str) -> dict[str, Any]:
        response = client.list_stores(advertiser_id)
        rows = _extract_gmv_max_stores(response)
        store_ids = _available_gmv_max_store_ids(rows, advertiser_id)
        return {
            "platform": "tiktok",
            "source": "gmv_max_stores",
            "advertiser_id": str(advertiser_id),
            "rows": rows,
            "row_count": len(rows),
            "available_store_ids": store_ids,
            "raw": response,
        }

    def _run_gmv_max_campaigns_source(
        self,
        client: TikTokClient,
        advertiser_id: str,
        promotion_type: str,
        *,
        stores_payload: Any,
    ) -> dict[str, Any]:
        source_name = "gmv_max_campaigns_live" if promotion_type == "LIVE_GMV_MAX" else "gmv_max_campaigns_product"
        return self.run_source(
            source_name,
            lambda: self._pull_gmv_max_campaigns(client, advertiser_id, promotion_type, stores_payload=stores_payload),
        )

    def _pull_gmv_max_campaigns(
        self,
        client: TikTokClient,
        advertiser_id: str,
        promotion_type: str,
        *,
        stores_payload: Any,
    ) -> dict[str, Any]:
        store_ids = self._selected_gmv_max_store_ids(stores_payload, advertiser_id)
        filtering: dict[str, Any] = {"gmv_max_promotion_types": [promotion_type]}
        if store_ids:
            filtering["store_ids"] = store_ids
        rows: list[dict[str, Any]] = []
        page_size = min(self.page_size, 100)
        for page in range(1, self.plan.insight_max_pages + 1):
            response = client.list_gmv_max_campaigns(
                advertiser_id,
                filtering=filtering,
                page=page,
                page_size=page_size,
            )
            page_rows = _campaign_rows(response)
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
        return {
            "platform": "tiktok",
            "source": "gmv_max_campaigns_live" if promotion_type == "LIVE_GMV_MAX" else "gmv_max_campaigns_product",
            "advertiser_id": str(advertiser_id),
            "promotion_type": promotion_type,
            "store_ids": store_ids,
            "filtering": filtering,
            "rows": rows,
            "row_count": len(rows),
        }

    def _run_gmv_max_sources(
        self,
        client: TikTokClient,
        advertiser_id: str,
        *,
        preloaded: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preloaded = preloaded or {}
        stores_payload = preloaded.get("gmv_max_stores") or self._run_gmv_max_stores_source(client, advertiser_id)
        store_ids = self._selected_gmv_max_store_ids(stores_payload, advertiser_id)
        self.manifest["gmv_max"]["store_ids"] = store_ids

        product_campaigns = preloaded.get("gmv_max_campaigns_product") or self._run_gmv_max_campaigns_source(
            client,
            advertiser_id,
            "PRODUCT_GMV_MAX",
            stores_payload=stores_payload,
        )
        live_campaigns: dict[str, Any] | None = None
        if self.plan.depth in ("standard", "full", "deep") and "LIVE_GMV_MAX" in self.gmv_max_promotion_types:
            live_campaigns = preloaded.get("gmv_max_campaigns_live") or self._run_gmv_max_campaigns_source(
                client,
                advertiser_id,
                "LIVE_GMV_MAX",
                stores_payload=stores_payload,
            )

        if not store_ids:
            self.manifest["gmv_max"]["coverage"] = "unavailable"
            self.manifest["gmv_max"]["coverage_reason"] = "No GMV Max available store found for this advertiser."
            return {"store_ids": [], "product_campaigns": product_campaigns, "live_campaigns": live_campaigns}

        product_campaign_ids = self._gmv_max_campaign_ids(product_campaigns)
        current_levels = TIKTOK_GMV_MAX_DEPTH_LEVELS[self.plan.depth]
        for level in current_levels:
            if level == "live" and not self._has_gmv_rows(live_campaigns):
                self._record_skipped(f"current_gmv_max_{level}", "No LIVE_GMV_MAX campaigns found.")
                continue
            self.run_source(
                f"current_gmv_max_{level}",
                lambda level=level: self._pull_gmv_max_report_level(
                    client,
                    advertiser_id,
                    level,
                    (self.window.since, self.window.until),
                    store_ids=store_ids,
                    campaign_ids=product_campaign_ids,
                ),
            )
        if self.window.has_previous:
            for level in current_levels:
                if level == "live" and not self._has_gmv_rows(live_campaigns):
                    self._record_skipped(f"previous_gmv_max_{level}", "No LIVE_GMV_MAX campaigns found.")
                    continue
                self.run_source(
                    f"previous_gmv_max_{level}",
                    lambda level=level: self._pull_gmv_max_report_level(
                        client,
                        advertiser_id,
                        level,
                        (self.window.previous_since or "", self.window.previous_until or ""),
                        store_ids=store_ids,
                        campaign_ids=product_campaign_ids,
                    ),
                )
        self.manifest["gmv_max"]["coverage"] = "full"
        return {"store_ids": store_ids, "product_campaigns": product_campaigns, "live_campaigns": live_campaigns}

    def _record_skipped(self, name: str, reason: str) -> None:
        path = self.run_dir / f"{name}.json"
        payload = {"source": name, "status": "skipped", "reason": reason}
        _write_json(path, payload)
        self.record(name, "skipped", str(path), reason=reason, rows=0)

    def _has_gmv_rows(self, payload: Any) -> bool:
        return isinstance(payload, dict) and isinstance(payload.get("rows"), list) and bool(payload["rows"])

    def _gmv_max_campaign_ids(self, payload: Any) -> list[str]:
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        ids: list[str] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                campaign_id = str(row.get("campaign_id") or "").strip()
                if campaign_id and campaign_id not in ids:
                    ids.append(campaign_id)
        return ids

    def _pull_gmv_max_report_level(
        self,
        client: TikTokClient,
        advertiser_id: str,
        level: str,
        window: tuple[str, str],
        *,
        store_ids: list[str],
        campaign_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        spec = TIKTOK_GMV_MAX_REPORT_LEVELS[level]
        promotion_types = [value for value in spec["promotion_types"] if value in self.gmv_max_promotion_types]
        if not promotion_types:
            promotion_types = list(spec["promotion_types"])
        filtering = {"gmv_max_promotion_types": _gmv_max_report_promotion_types(promotion_types)}
        if level == "product":
            return self._pull_gmv_max_targeted_item_report(
                client,
                advertiser_id,
                level,
                window,
                store_ids=store_ids,
                campaign_ids=campaign_ids or [],
                base_filtering=filtering,
            )
        if level == "creative":
            return self._pull_gmv_max_targeted_creative_report(
                client,
                advertiser_id,
                level,
                window,
                store_ids=store_ids,
                campaign_ids=campaign_ids or [],
                base_filtering=filtering,
            )
        rows: list[dict[str, Any]] = []
        page_size = min(self.page_size, 1000)
        warnings: list[dict[str, Any]] = []
        metrics = list(spec["metrics"])
        sort_field = "cost" if "cost" in metrics else None
        try:
            for page in range(1, self.plan.insight_max_pages + 1):
                response = client.gmv_max_report(
                    advertiser_id,
                    store_ids=store_ids,
                    dimensions=list(spec["dimensions"]),
                    metrics=metrics,
                    start_date=window[0],
                    end_date=window[1],
                    filtering=filtering,
                    enable_total_metrics=True,
                    sort_field=sort_field,
                    sort_type="DESC" if sort_field else None,
                    page=page,
                    page_size=page_size,
                )
                page_rows = _extract_collection(response, "list")
                rows.extend(row for row in page_rows if isinstance(row, dict))
                if len(page_rows) < page_size:
                    break
        except Exception as exc:
            warnings.append({"error": str(exc), "metrics": metrics})
            raise
        _annotate_gmv_max_rows(rows)
        return {
            "platform": "tiktok",
            "source": f"gmv_max_{level}",
            "advertiser_id": str(advertiser_id),
            "date_range": {"since": window[0], "until": window[1]},
            "store_ids": store_ids,
            "dimensions": list(spec["dimensions"]),
            "metrics": metrics,
            "filtering": filtering,
            "rows": rows,
            "row_count": len(rows),
            "summary": _summarize_gmv_max_rows(rows),
            "warnings": warnings,
        }

    def _pull_gmv_max_page_rows(
        self,
        client: TikTokClient,
        advertiser_id: str,
        *,
        store_ids: list[str],
        dimensions: list[str],
        window: tuple[str, str],
        filtering: dict[str, Any],
        metrics: list[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        page_size = min(self.page_size, 1000)
        warnings: list[dict[str, Any]] = []
        sort_field = "cost" if "cost" in metrics else None
        try:
            page = 1
            while True:
                response = client.gmv_max_report(
                    advertiser_id,
                    store_ids=store_ids,
                    dimensions=list(dimensions),
                    metrics=list(metrics),
                    start_date=window[0],
                    end_date=window[1],
                    filtering=filtering,
                    enable_total_metrics=True,
                    sort_field=sort_field,
                    sort_type="DESC" if sort_field else None,
                    page=page,
                    page_size=page_size,
                )
                page_rows = _extract_collection(response, "list")
                rows.extend(row for row in page_rows if isinstance(row, dict))
                total_page = _total_pages(response)
                if len(page_rows) < page_size or (total_page and page >= total_page):
                    break
                page += 1
        except Exception as exc:
            warnings.append({"error": str(exc), "metrics": metrics})
            raise
        return rows, list(metrics), warnings

    def _pull_gmv_max_targeted_item_report(
        self,
        client: TikTokClient,
        advertiser_id: str,
        level: str,
        window: tuple[str, str],
        *,
        store_ids: list[str],
        campaign_ids: list[str],
        base_filtering: dict[str, Any],
    ) -> dict[str, Any]:
        if not campaign_ids:
            raise RuntimeError("GMV Max item-level report requires campaign IDs from gmv_max_campaigns_product.")
        spec = TIKTOK_GMV_MAX_REPORT_LEVELS[level]
        dimensions = list(spec["dimensions"])
        metrics = list(spec["metrics"])
        rows: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            filtering = {}
            filtering["campaign_ids"] = [campaign_id]
            batch_rows, batch_metrics, batch_warnings = self._pull_gmv_max_page_rows(
                client,
                advertiser_id,
                store_ids=store_ids,
                dimensions=dimensions,
                window=window,
                filtering=filtering,
                metrics=metrics,
            )
            warnings.extend(batch_warnings)
            for row in batch_rows:
                row_dimensions = row.setdefault("dimensions", {})
                if isinstance(row_dimensions, dict):
                    row_dimensions.setdefault("campaign_id_filter", campaign_id)
            rows.extend(batch_rows)
            calls.append({"campaign_id": campaign_id, "row_count": len(batch_rows), "metrics": batch_metrics})
        return {
            "platform": "tiktok",
            "source": f"gmv_max_{level}",
            "advertiser_id": str(advertiser_id),
            "date_range": {"since": window[0], "until": window[1]},
            "store_ids": store_ids,
            "dimensions": dimensions,
            "metrics": metrics,
            "targeting_strategy": "per_campaign_required_by_gmv_max_api",
            "campaign_ids": campaign_ids,
            "rows": rows,
            "row_count": len(rows),
            "summary": _summarize_gmv_max_rows(rows),
            "calls": calls,
            "warnings": warnings,
        }

    def _pull_gmv_max_campaign_item_group_probe(
        self,
        client: TikTokClient,
        advertiser_id: str,
        window: tuple[str, str],
        *,
        store_ids: list[str],
        campaign_ids: list[str],
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            batch_rows, batch_metrics, batch_warnings = self._pull_gmv_max_page_rows(
                client,
                advertiser_id,
                store_ids=store_ids,
                dimensions=["item_group_id"],
                window=window,
                filtering={"campaign_ids": [campaign_id]},
                metrics=TIKTOK_GMV_MAX_PRODUCT_METRICS,
            )
            warnings.extend(batch_warnings)
            for row in batch_rows:
                row_dimensions = row.setdefault("dimensions", {})
                if isinstance(row_dimensions, dict):
                    row_dimensions.setdefault("campaign_id_filter", campaign_id)
            rows.extend(batch_rows)
            calls.append({"campaign_id": campaign_id, "row_count": len(batch_rows), "metrics": batch_metrics})
        return {"rows": rows, "calls": calls, "warnings": warnings}

    def _pull_gmv_max_targeted_creative_report(
        self,
        client: TikTokClient,
        advertiser_id: str,
        level: str,
        window: tuple[str, str],
        *,
        store_ids: list[str],
        campaign_ids: list[str],
        base_filtering: dict[str, Any],
    ) -> dict[str, Any]:
        if not campaign_ids:
            raise RuntimeError("GMV Max creative-level report requires campaign IDs from gmv_max_campaigns_product.")
        spec = TIKTOK_GMV_MAX_REPORT_LEVELS[level]
        dimensions = ["campaign_id", "item_id"] if self.gmv_max_creative_dimensions == "fast" else list(spec["dimensions"])
        metrics = list(spec["metrics"])
        rows: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        product_payload = self._pull_gmv_max_campaign_item_group_probe(
            client,
            advertiser_id,
            window,
            store_ids=store_ids,
            campaign_ids=campaign_ids,
        )
        campaign_item_groups: list[tuple[str, str]] = []
        for row in product_payload.get("rows", []):
            if not isinstance(row, dict):
                continue
            row_dimensions = row.get("dimensions")
            if not isinstance(row_dimensions, dict):
                continue
            campaign_id = str(row_dimensions.get("campaign_id") or row_dimensions.get("campaign_id_filter") or "").strip()
            item_group_id = str(row_dimensions.get("item_group_id") or "").strip()
            if campaign_id and item_group_id and (campaign_id, item_group_id) not in campaign_item_groups:
                campaign_item_groups.append((campaign_id, item_group_id))
        if self.gmv_max_creative_dimensions == "fast":
            fast_filtering = {
                "campaign_ids": _dedupe([campaign_id for campaign_id, _ in campaign_item_groups]),
                "item_group_ids": _dedupe([item_group_id for _, item_group_id in campaign_item_groups]),
            }
            batch_rows, batch_metrics, batch_warnings = self._pull_gmv_max_page_rows(
                client,
                advertiser_id,
                store_ids=store_ids,
                dimensions=dimensions,
                window=window,
                filtering=fast_filtering,
                metrics=metrics,
            )
            warnings.extend(batch_warnings)
            _annotate_gmv_max_rows(batch_rows)
            rows.extend(batch_rows)
            calls.append(
                {
                    "campaign_count": len(fast_filtering["campaign_ids"]),
                    "item_group_count": len(fast_filtering["item_group_ids"]),
                    "row_count": len(batch_rows),
                    "metrics": batch_metrics,
                    "dimensions": dimensions,
                }
            )
        else:
            for campaign_id, item_group_id in campaign_item_groups:
                filtering = {}
                filtering["campaign_ids"] = [campaign_id]
                filtering["item_group_ids"] = [item_group_id]
                batch_rows, batch_metrics, batch_warnings = self._pull_gmv_max_page_rows(
                    client,
                    advertiser_id,
                    store_ids=store_ids,
                    dimensions=dimensions,
                    window=window,
                    filtering=filtering,
                    metrics=metrics,
                )
                warnings.extend(batch_warnings)
                for row in batch_rows:
                    row_dimensions = row.setdefault("dimensions", {})
                    if isinstance(row_dimensions, dict):
                        row_dimensions.setdefault("campaign_id_filter", campaign_id)
                        row_dimensions.setdefault("item_group_id_filter", item_group_id)
                _annotate_gmv_max_rows(batch_rows)
                rows.extend(batch_rows)
                calls.append(
                    {
                        "campaign_id": campaign_id,
                        "item_group_id": item_group_id,
                        "row_count": len(batch_rows),
                        "metrics": batch_metrics,
                    }
                )
        _annotate_gmv_max_rows(rows)
        return {
            "platform": "tiktok",
            "source": f"gmv_max_{level}",
            "advertiser_id": str(advertiser_id),
            "date_range": {"since": window[0], "until": window[1]},
            "store_ids": store_ids,
            "dimensions": dimensions,
            "metrics": metrics,
            "targeting_strategy": (
                "batched_campaign_item_fast_dimensions"
                if self.gmv_max_creative_dimensions == "fast"
                else "per_campaign_item_group_required_by_gmv_max_api"
            ),
            "campaign_ids": campaign_ids,
            "rows": rows,
            "row_count": len(rows),
            "summary": _summarize_gmv_max_rows(rows),
            "product_probe": {
                "row_count": len(product_payload.get("rows", [])),
                "metrics": TIKTOK_GMV_MAX_PRODUCT_METRICS,
            },
            "calls": calls,
            "warnings": [*product_payload.get("warnings", []), *warnings],
        }

    def _decide_report_mode(
        self,
        stores_payload: Any,
        product_campaigns: Any,
        live_campaigns: Any,
        auction_probe: Any,
    ) -> dict[str, Any]:
        if self.gmv_max_mode == "never":
            return {"mode": "auction", "reason": "GMV Max disabled by CLI.", "signals": {}}
        store_ids = self._selected_gmv_max_store_ids(stores_payload, str(self.args.advertiser_id))
        product_count = len(product_campaigns.get("rows", [])) if isinstance(product_campaigns, dict) else 0
        live_count = len(live_campaigns.get("rows", [])) if isinstance(live_campaigns, dict) else 0
        auction_rows = auction_probe.get("rows", []) if isinstance(auction_probe, dict) else []
        auction_spend = _sum_auction_spend(auction_rows if isinstance(auction_rows, list) else [])
        has_gmv_max = bool(store_ids) and (product_count > 0 or live_count > 0)
        if self.gmv_max_mode == "always" and has_gmv_max:
            mode = "gmv_max" if auction_spend <= 0 else "hybrid"
            reason = "GMV Max explicitly enabled and GMV Max campaign/store signals are present."
        elif has_gmv_max and auction_spend <= 0:
            mode = "gmv_max"
            reason = "GMV Max store/campaign signals found and auction probe has no spend."
        elif has_gmv_max and auction_spend > 0:
            mode = "hybrid"
            reason = "Both GMV Max and auction spend signals are present."
        else:
            mode = "auction"
            reason = "No usable GMV Max store/campaign signals found."
        return {
            "platform": "tiktok",
            "source": "auto_mode_decision",
            "mode": mode,
            "reason": reason,
            "signals": {
                "gmv_max_store_ids": store_ids,
                "gmv_max_product_campaign_count": product_count,
                "gmv_max_live_campaign_count": live_count,
                "auction_probe_spend": auction_spend,
            },
        }

    def _activity_target_count(self) -> int:
        requested = int(self.args.top_objects or 0)
        if requested > 0:
            return max(1, min(requested, 20))
        if self.plan.depth == "fast":
            return 8
        if self.plan.depth == "standard":
            return 12
        return 20

    def _pull_activities(
        self,
        client: TikTokClient,
        advertiser_id: str,
        current_rows: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        if self.plan.depth == "deep":
            return build_tiktok_activities_report(
                client,
                advertiser_id=advertiser_id,
                start_date=self.window.previous_since or self.window.since,
                end_date=self.window.until,
                page_size=100,
                wait=True,
                timeout_seconds=60,
                poll_seconds=5.0,
            )
        return self._pull_targeted_top_activities(client, advertiser_id, current_rows)

    def _pull_targeted_top_activities(
        self,
        client: TikTokClient,
        advertiser_id: str,
        current_rows: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        target_count = self._activity_target_count()
        targets: dict[str, list[str]] = {}
        for level, spec in TIKTOK_TARGETED_ACTIVITY_OBJECTS.items():
            extra_keys = ("ad_id_v2", "smart_plus_ad_id") if level == "ad" else ()
            ids = _top_activity_ids(current_rows.get(level) or [], str(spec["id_key"]), target_count, extra_keys=extra_keys)
            if ids:
                targets[level] = ids

        rows: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for level, ids in targets.items():
            object_type = str(TIKTOK_TARGETED_ACTIVITY_OBJECTS[level]["object_type"])
            for batch in _chunked(ids, 20):
                try:
                    payload = build_tiktok_activities_report(
                        client,
                        advertiser_id=advertiser_id,
                        start_date=self.window.previous_since or self.window.since,
                        end_date=self.window.until,
                        object_type=object_type,
                        object_ids=batch,
                        operation_types=TIKTOK_TARGETED_ACTIVITY_OPERATION_TYPES,
                        page_size=100,
                        wait=True,
                        timeout_seconds=25,
                        poll_seconds=5.0,
                    )
                    batch_rows = payload.get("rows") if isinstance(payload, dict) else []
                    if isinstance(batch_rows, list):
                        rows.extend(row for row in batch_rows if isinstance(row, dict))
                    calls.append(
                        {
                            "level": level,
                            "object_type": object_type,
                            "object_ids": batch,
                            "task_id": payload.get("task_id") if isinstance(payload, dict) else None,
                            "status": payload.get("status") if isinstance(payload, dict) else "unknown",
                            "task_status": payload.get("task_status") if isinstance(payload, dict) else None,
                            "row_count": len(batch_rows) if isinstance(batch_rows, list) else 0,
                        }
                    )
                except Exception as exc:
                    errors.append({"level": level, "object_type": object_type, "object_ids": batch, "error": str(exc)})

        return {
            "platform": "tiktok",
            "advertiser_id": str(advertiser_id),
            "date_range": {"since": self.window.previous_since or self.window.since, "until": self.window.until},
            "strategy": "targeted_top_objects_changelog",
            "target_selection": "top current-period campaign/adgroup/ad objects by revenue, conversion, then spend",
            "target_object_count": sum(len(ids) for ids in targets.values()),
            "target_objects": targets,
            "operation_types": TIKTOK_TARGETED_ACTIVITY_OPERATION_TYPES,
            "status": "success" if rows or calls else "empty",
            "calls": calls,
            "errors": errors,
            "rows": rows,
            "summary": summarize_tiktok_activities(rows),
            "limits": {
                "page_size": 100,
                "wait": True,
                "timeout_seconds_per_task": 25,
                "max_object_ids_per_task": 20,
                "target_count_per_level": target_count,
            },
        }

    def _pull_activity_targeted_insights(self, client: TikTokClient, advertiser_id: str, activities: Any) -> dict[str, Any]:
        rows = (activities or {}).get("rows") if isinstance(activities, dict) else []
        targets = rank_activity_targets("tiktok", rows or [], limit=10)
        filter_fields = {"campaign": "campaign_ids", "adgroup": "adgroup_ids", "ad": "ad_ids"}
        grouped: dict[str, list[str]] = {"campaign": [], "adgroup": [], "ad": []}
        for target in targets:
            level = str(target.get("level") or "")
            object_id = str(target.get("object_id") or "").strip()
            if level in grouped and object_id and object_id not in grouped[level]:
                grouped[level].append(object_id)

        pulled: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for level, ids in grouped.items():
            for batch in _chunked(ids, 20):
                filtering = _report_target_filtering(filter_fields[level], batch)
                try:
                    payload = _pull_basic_report(
                        client,
                        advertiser_id,
                        level,
                        (self.window.since, self.window.until),
                        page_size=max(len(batch), 1),
                        max_pages=1,
                        filtering=filtering,
                        preferred_metrics=self.analysis_metrics,
                    )
                    by_id = {
                        str(_dimension(row, TIKTOK_LEVELS[level]["id_key"]) or "").strip(): row
                        for row in payload.get("rows", [])
                    }
                    for object_id in batch:
                        pulled.append(
                            {
                                "level": level,
                                "object_id": object_id,
                                "current_rows": [by_id[object_id]] if object_id in by_id else [],
                            }
                        )
                except Exception as exc:
                    for object_id in batch:
                        errors.append({"level": level, "object_id": object_id, "error": str(exc)})
        return {
            "platform": "tiktok",
            "strategy": "activity_targeted_object_insights",
            "target_count": len(targets),
            "pulled_count": len(pulled),
            "rows": pulled,
            "errors": errors,
        }

    def _pull_activity_daily_breakdown(self, client: TikTokClient, advertiser_id: str, activities: Any) -> dict[str, Any]:
        rows = (activities or {}).get("rows") if isinstance(activities, dict) else []
        targets = rank_activity_targets("tiktok", rows or [], limit=min(8, self._activity_target_count()))
        filter_fields = {"campaign": "campaign_ids", "adgroup": "adgroup_ids", "ad": "ad_ids"}
        grouped: dict[str, list[str]] = {"campaign": [], "adgroup": [], "ad": []}
        for target in targets:
            level = str(target.get("level") or "")
            object_id = str(target.get("object_id") or "").strip()
            if level in grouped and object_id and object_id not in grouped[level]:
                grouped[level].append(object_id)

        pulled: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for level, ids in grouped.items():
            spec = TIKTOK_LEVELS[level]
            id_key = str(spec["id_key"])
            dimensions = [id_key, "stat_time_day"]
            for batch in _chunked(ids, 20):
                filtering = _report_target_filtering(filter_fields[level], batch)
                last_error = ""
                for metrics in _daily_metric_sets(self.analysis_metrics):
                    try:
                        response = client.integrated_report(
                            "BASIC",
                            advertiser_id=advertiser_id,
                            data_level=str(spec["data_level"]),
                            dimensions=dimensions,
                            metrics=metrics,
                            start_date=self.window.previous_since or self.window.since,
                            end_date=self.window.until,
                            page=1,
                            page_size=max(1000, len(batch) * 16),
                            filtering=filtering,
                        )
                        daily_rows = _extract_collection(response, "list")
                        by_id: dict[str, list[dict[str, Any]]] = {object_id: [] for object_id in batch}
                        for row in daily_rows:
                            object_id = str(_dimension(row, id_key) or "").strip()
                            if object_id in by_id:
                                by_id[object_id].append(row)
                        for object_id in batch:
                            pulled.append(
                                {
                                    "level": level,
                                    "object_id": object_id,
                                    "dimensions": dimensions,
                                    "metrics": metrics,
                                    "daily_rows": by_id.get(object_id, []),
                                }
                            )
                        break
                    except Exception as exc:
                        last_error = str(exc)
                else:
                    for object_id in batch:
                        errors.append({"level": level, "object_id": object_id, "error": last_error})
        return {
            "platform": "tiktok",
            "strategy": "activity_target_daily_breakdown",
            "date_range": {"since": self.window.previous_since or self.window.since, "until": self.window.until},
            "target_count": len(targets),
            "pulled_count": len(pulled),
            "rows": pulled,
            "errors": errors,
        }

    def _pull_top_structure(self, client: TikTokClient, advertiser_id: str, current_rows: dict[str, list[dict[str, Any]]]) -> None:
        top_n = max(1, int(self.args.top_objects or self.plan.creative_target_count or 30))
        campaign_ids = _top_ids(current_rows.get("campaign") or [], "campaign_id", top_n)
        adgroup_ids = _top_ids(current_rows.get("adgroup") or [], "adgroup_id", top_n)
        ad_ids = _top_ids(current_rows.get("ad") or [], "ad_id", top_n)

        self.run_source(
            "campaign_structure",
            lambda: _list_campaigns_by_ids(
                client,
                advertiser_id,
                campaign_ids,
                page_size=top_n,
                smart_plus=bool(self.args.smart_plus),
            ),
        )
        self.run_source(
            "adgroup_structure",
            lambda: _list_adgroups_by_ids(
                client,
                advertiser_id,
                adgroup_ids,
                page_size=top_n,
                smart_plus=bool(self.args.smart_plus),
            ),
        )
        self.run_source(
            "ad_structure",
            lambda: list(
                _list_ads_by_ids(
                    client,
                    advertiser_id,
                    ad_ids,
                    smart_plus=bool(self.args.smart_plus),
                    cache=self.ad_detail_cache,
                ).values()
            ),
        )

    def _pull_all_structure(self, client: TikTokClient, advertiser_id: str) -> None:
        self.run_source(
            "campaign_structure",
            lambda: _list_all_structure(client, advertiser_id, "campaign", page_size=500, smart_plus=bool(self.args.smart_plus)),
        )
        self.run_source(
            "adgroup_structure",
            lambda: _list_all_structure(client, advertiser_id, "adgroup", page_size=500, smart_plus=bool(self.args.smart_plus)),
        )
        self.run_source(
            "ad_structure",
            lambda: _list_all_structure(client, advertiser_id, "ad", page_size=500, smart_plus=bool(self.args.smart_plus)),
        )


def command_tiktok_report_run(args: argparse.Namespace) -> None:
    args = _prepare_tiktok_report_args(args)
    advertiser_ids = list(getattr(args, "advertiser_ids", [args.advertiser_id]))
    if len(advertiser_ids) == 1:
        runner = TikTokReportRunner(args)
        print_output(runner.run(), as_json=True)
        return

    runs: list[dict[str, Any]] = []
    for advertiser_id in advertiser_ids:
        run_args = _clone_args(
            args,
            advertiser_id=advertiser_id,
            advertiser_ids=[advertiser_id],
            run_dir=_batch_run_dir(getattr(args, "run_dir", None), advertiser_id),
        )
        runs.append(TikTokReportRunner(run_args).run())

    result = {
        "platform": "tiktok",
        "mode": "batch",
        "advertiser_ids": advertiser_ids,
        "account_source": getattr(args, "account_source", "cli"),
        "run_count": len(runs),
        "runs": runs,
    }
    print_output(result, as_json=True)
