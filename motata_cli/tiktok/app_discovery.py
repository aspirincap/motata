from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from motata_cli.meta.commands import CliError

from .landing_pages import _chunked, _dimension, _extract_collection, _fnum, _inum, _metric, _page_info


APP_CAMPAIGN_REPORT_METRICS = ["spend", "impressions", "clicks", "conversion"]
APP_CAMPAIGN_ATTRIBUTE_METRICS = [
    "campaign_name",
    "objective_type",
    "campaign_automation_type",
    "campaign_dedicate_type",
    "app_promotion_type",
]
ACTIVE_ADVERTISER_REPORT_METRICS = ["spend", "impressions", "reach"]
APP_CAMPAIGN_FIELDS = [
    "campaign_id",
    "campaign_name",
    "objective_type",
    "campaign_automation_type",
    "operation_status",
    "secondary_status",
]
APP_ADGROUP_FIELDS = [
    "adgroup_id",
    "adgroup_name",
    "campaign_id",
    "campaign_name",
    "promotion_type",
    "promotion_target_type",
    "app_id",
    "app_download_url",
    "app_type",
    "optimization_goal",
    "billing_event",
    "operation_status",
    "secondary_status",
]
APP_AD_FIELDS = [
    "ad_id",
    "ad_name",
    "campaign_id",
    "adgroup_id",
    "app_id",
    "app_name",
    "app_download_url",
    "landing_page_url",
    "landing_page_urls",
    "deeplink",
    "deeplink_list",
    "creative_type",
    "operation_status",
    "secondary_status",
]


def default_date_range(days: int = 14) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    return start.isoformat(), end.isoformat()


def discover_business_centers(client: Any) -> list[dict[str, Any]]:
    try:
        response = client.list_business_centers()
    except AttributeError:
        response = client._raw_request("GET", "bc/get/", params={})
    centers = []
    for item in _extract_collection(response, "list"):
        info = item.get("bc_info") or {}
        bc_id = str(info.get("bc_id") or "").strip()
        if bc_id:
            centers.append(
                {
                    "bc_id": bc_id,
                    "bc_name": info.get("name"),
                    "currency": info.get("currency"),
                    "timezone": info.get("timezone"),
                    "status": info.get("status"),
                    "user_role": item.get("user_role"),
                }
            )
    return centers


def discover_operable_advertiser_ids(
    client: Any,
    *,
    advertiser_limit: int | None = None,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    advertisers: dict[str, dict[str, Any]] = {}
    for bc in discover_business_centers(client):
        page = 1
        while True:
            try:
                if hasattr(client, "list_bc_assets"):
                    response = client.list_bc_assets(
                        bc["bc_id"],
                        asset_type="ADVERTISER",
                        page=page,
                        page_size=page_size,
                    )
                else:
                    response = client._raw_request(
                        "GET",
                        "bc/asset/get/",
                        params={
                            "bc_id": bc["bc_id"],
                            "asset_type": "ADVERTISER",
                            "page": page,
                            "page_size": page_size,
                        },
                    )
            except Exception as exc:  # pragma: no cover - depends on BC permissions
                advertisers.setdefault(
                    f"_bc_error:{bc['bc_id']}",
                    {**bc, "advertiser_id": None, "error": str(exc)},
                )
                break
            for item in _extract_collection(response, "list"):
                advertiser_id = str(item.get("asset_id") or item.get("advertiser_id") or "").strip()
                if not advertiser_id:
                    continue
                advertisers.setdefault(
                    advertiser_id,
                    {
                        "advertiser_id": advertiser_id,
                        "advertiser_name": item.get("asset_name"),
                        "advertiser_role": item.get("advertiser_role"),
                        "advertiser_account_type": item.get("advertiser_account_type"),
                        "bc_id": bc["bc_id"],
                        "bc_name": bc.get("bc_name"),
                    },
                )
                if advertiser_limit and advertiser_limit > 0 and len([item for item in advertisers.values() if item.get("advertiser_id")]) >= advertiser_limit:
                    break
            if advertiser_limit and advertiser_limit > 0 and len([item for item in advertisers.values() if item.get("advertiser_id")]) >= advertiser_limit:
                break
            info = _page_info(response)
            total_page = int(_fnum(info.get("total_page") or info.get("total_pages")))
            if total_page and page >= total_page:
                break
            if not total_page and len(_extract_collection(response, "list")) < page_size:
                break
            page += 1
        if advertiser_limit and advertiser_limit > 0 and len([item for item in advertisers.values() if item.get("advertiser_id")]) >= advertiser_limit:
            break
    rows = [item for item in advertisers.values() if item.get("advertiser_id")]
    rows.sort(key=lambda item: (item.get("bc_name") or "", item.get("advertiser_name") or "", item["advertiser_id"]))
    if advertiser_limit and advertiser_limit > 0:
        rows = rows[:advertiser_limit]
    return rows


def discover_recent_spend_advertisers(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    advertiser_limit: int | None = 10,
    page_size: int = 1000,
    max_pages: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    try:
        business_centers = discover_business_centers(client)
    except Exception as exc:
        return [], [{"scope": "bc_discovery", "error": str(exc)}]

    for bc in business_centers:
        for page in range(1, max(max_pages, 1) + 1):
            try:
                response = client.integrated_report(
                    "BC",
                    bc_id=bc["bc_id"],
                    dimensions=["advertiser_id"],
                    metrics=ACTIVE_ADVERTISER_REPORT_METRICS,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    page_size=page_size,
                    order_field="spend",
                    order_type="DESC",
                )
            except Exception as exc:
                errors.append({"scope": "bc_advertiser_report", "bc_id": bc["bc_id"], "error": str(exc)})
                break
            items = _extract_collection(response, "list")
            for item in items:
                advertiser_id = str(_dimension(item, "advertiser_id") or "").strip()
                spend = _fnum(_metric(item, "spend"))
                if not advertiser_id or spend <= 0:
                    continue
                existing = rows_by_id.get(advertiser_id, {})
                rows_by_id[advertiser_id] = {
                    **existing,
                    "advertiser_id": advertiser_id,
                    "bc_id": bc["bc_id"],
                    "bc_name": bc.get("bc_name"),
                    "spend": round(_fnum(existing.get("spend")) + spend, 2),
                    "impressions": _inum(existing.get("impressions")) + _inum(_metric(item, "impressions")),
                    "reach": _inum(existing.get("reach")) + _inum(_metric(item, "reach")),
                }
            if len(items) < page_size:
                break

    rows = sorted(rows_by_id.values(), key=lambda item: _fnum(item.get("spend")), reverse=True)
    if advertiser_limit and advertiser_limit > 0:
        rows = rows[:advertiser_limit]
    return rows, errors


def _campaign_report_rows(
    client: Any,
    advertiser_id: str,
    *,
    start_date: str,
    end_date: str,
    campaign_limit: int,
) -> list[dict[str, Any]]:
    metrics = [*APP_CAMPAIGN_REPORT_METRICS, *APP_CAMPAIGN_ATTRIBUTE_METRICS]
    try:
        response = client.integrated_report(
            "BASIC",
            advertiser_id=advertiser_id,
            data_level="AUCTION_CAMPAIGN",
            dimensions=["campaign_id"],
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            order_field="spend",
            order_type="DESC",
            page=1,
            page_size=campaign_limit,
        )
    except Exception:
        response = client.integrated_report(
            "BASIC",
            advertiser_id=advertiser_id,
            data_level="AUCTION_CAMPAIGN",
            dimensions=["campaign_id"],
            metrics=APP_CAMPAIGN_REPORT_METRICS,
            start_date=start_date,
            end_date=end_date,
            order_field="spend",
            order_type="DESC",
            page=1,
            page_size=campaign_limit,
        )
    rows = []
    for item in _extract_collection(response, "list"):
        campaign_id = _dimension(item, "campaign_id")
        spend = _fnum(_metric(item, "spend"))
        if campaign_id and spend > 0:
            rows.append(
                {
                    "campaign_id": str(campaign_id),
                    "spend": spend,
                    "impressions": _inum(_metric(item, "impressions")),
                    "clicks": _inum(_metric(item, "clicks")),
                    "conversions": _fnum(_metric(item, "conversion")),
                    "campaign_name": _metric(item, "campaign_name"),
                    "objective_type": _metric(item, "objective_type"),
                    "campaign_automation_type": _metric(item, "campaign_automation_type"),
                    "campaign_dedicate_type": _metric(item, "campaign_dedicate_type"),
                    "app_promotion_type": _metric(item, "app_promotion_type"),
                }
            )
    return rows[:campaign_limit]


def _list_campaigns(client: Any, advertiser_id: str, campaign_ids: list[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    details: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for batch in _chunked(campaign_ids, 100):
        try:
            response = client.list_campaigns(
                advertiser_id,
                filtering={"campaign_ids": batch},
                page=1,
                page_size=len(batch),
                fields=APP_CAMPAIGN_FIELDS,
            )
        except Exception as exc:
            errors.append({"scope": "campaigns", "ids": batch, "error": str(exc)})
            continue
        for item in _extract_collection(response, "list", "campaigns"):
            campaign_id = str(item.get("campaign_id") or "").strip()
            if campaign_id:
                details[campaign_id] = item
    return details, errors


def _list_adgroups(client: Any, advertiser_id: str, campaign_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for batch in _chunked(campaign_ids, 50):
        try:
            response = client.list_adgroups(
                advertiser_id,
                filtering={"campaign_ids": batch},
                page=1,
                page_size=1000,
                fields=APP_ADGROUP_FIELDS,
            )
        except Exception as exc:
            try:
                response = client.list_adgroups(
                    advertiser_id,
                    filtering={"campaign_ids": batch},
                    page=1,
                    page_size=1000,
                )
            except Exception as fallback_exc:
                errors.append({"scope": "adgroups", "ids": batch, "error": str(fallback_exc), "first_error": str(exc)})
                continue
        rows.extend(_extract_collection(response, "list", "adgroups"))
    wanted = set(campaign_ids)
    return [row for row in rows if str(row.get("campaign_id") or "") in wanted], errors


def _list_ads(client: Any, advertiser_id: str, adgroup_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for batch in _chunked(adgroup_ids, 50):
        try:
            response = client.list_ads(
                advertiser_id,
                filtering={"adgroup_ids": batch},
                page=1,
                page_size=1000,
                fields=APP_AD_FIELDS,
            )
        except Exception as exc:
            try:
                response = client.list_ads(advertiser_id, filtering={"adgroup_ids": batch}, page=1, page_size=1000)
            except Exception as fallback_exc:
                errors.append({"scope": "ads", "ids": batch[:10], "error": str(fallback_exc), "first_error": str(exc)})
                continue
        rows.extend(_extract_collection(response, "list", "ads"))
    wanted = set(adgroup_ids)
    return [row for row in rows if str(row.get("adgroup_id") or "") in wanted], errors


def _collect_app_fields(payload: Any, source: str, found: list[dict[str, Any]], path: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if any(token in lowered for token in ("app", "download", "deeplink", "promotion_type", "objective_type")):
                if isinstance(value, (str, int, float)) or value is None:
                    found.append({"source": source, "path": child_path, "value": value})
                elif isinstance(value, list) and len(value) <= 8:
                    found.append({"source": source, "path": child_path, "value": value})
            _collect_app_fields(value, source, found, child_path)
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:50]):
            _collect_app_fields(item, source, found, f"{path}[{index}]")


def _app_candidates(*payloads: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    hints: list[dict[str, Any]] = []
    for source, payload in payloads:
        _collect_app_fields(payload, source, hints)
    app_ids = sorted(
        {
            str(hint.get("value")).strip()
            for hint in hints
            if str(hint.get("path") or "").endswith("app_id")
            and str(hint.get("value") or "").strip()
            and str(hint.get("value") or "").strip() != "0"
        }
    )
    app_names = sorted(
        {
            str(hint.get("value")).strip()
            for hint in hints
            if str(hint.get("path") or "").endswith("app_name") and str(hint.get("value") or "").strip()
        }
    )
    app_urls = sorted(
        {
            str(hint.get("value")).strip()
            for hint in hints
            if str(hint.get("value") or "").startswith(("http://", "https://"))
            and any(marker in str(hint.get("path") or "") for marker in ("app_download_url", "landing_page_url"))
        }
    )
    promotion_types = sorted(
        {
            str(hint.get("value")).strip()
            for hint in hints
            if str(hint.get("path") or "").endswith(("promotion_type", "objective_type"))
            and str(hint.get("value") or "").strip()
        }
    )
    app_key = app_ids[0] if app_ids else (app_urls[0] if app_urls else "unknown")
    return {
        "app_key": app_key,
        "app_ids": app_ids,
        "app_names": app_names,
        "app_urls": app_urls,
        "promotion_types": promotion_types,
        "hints": hints,
    }


def _has_app_evidence(candidates: dict[str, Any]) -> bool:
    return bool(candidates.get("app_ids") or candidates.get("app_urls"))


def _campaign_report_detail(campaign: dict[str, Any]) -> dict[str, Any]:
    detail = {
        key: campaign.get(key)
        for key in (
            "campaign_id",
            "campaign_name",
            "objective_type",
            "campaign_automation_type",
            "campaign_dedicate_type",
            "app_promotion_type",
        )
        if campaign.get(key) not in (None, "", "-")
    }
    if detail:
        detail["_detail_source"] = "report"
    return detail


def _needs_campaign_detail(detail: dict[str, Any]) -> bool:
    return not any(detail.get(key) for key in ("campaign_name", "objective_type", "campaign_automation_type"))


def build_tiktok_app_report(
    client: Any,
    *,
    advertiser_ids: list[str] | None = None,
    start_date: str,
    end_date: str,
    advertiser_limit: int = 2,
    campaign_limit: int = 20,
    include_campaigns: bool = False,
) -> dict[str, Any]:
    if advertiser_ids:
        advertisers = [{"advertiser_id": str(value)} for value in advertiser_ids]
        discovery_errors: list[dict[str, Any]] = []
    else:
        advertisers, discovery_errors = discover_recent_spend_advertisers(
            client,
            start_date=start_date,
            end_date=end_date,
            advertiser_limit=advertiser_limit,
        )
    groups: dict[str, dict[str, Any]] = {}
    account_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = list(discovery_errors)

    for advertiser in advertisers:
        advertiser_id = str(advertiser.get("advertiser_id") or "").strip()
        if not advertiser_id:
            continue
        try:
            campaigns = _campaign_report_rows(
                client,
                advertiser_id,
                start_date=start_date,
                end_date=end_date,
                campaign_limit=campaign_limit,
            )
        except Exception as exc:
            errors.append({"advertiser_id": advertiser_id, "scope": "campaign_report", "error": str(exc)})
            continue
        if not campaigns and not advertiser_ids:
            continue
        campaign_ids = [item["campaign_id"] for item in campaigns]
        campaign_details = {
            campaign["campaign_id"]: _campaign_report_detail(campaign)
            for campaign in campaigns
            if _campaign_report_detail(campaign)
        }
        missing_campaign_ids = [
            campaign_id
            for campaign_id in campaign_ids
            if _needs_campaign_detail(campaign_details.get(campaign_id, {}))
        ]
        fetched_campaign_details, campaign_errors = _list_campaigns(client, advertiser_id, missing_campaign_ids)
        for campaign_id, detail in fetched_campaign_details.items():
            campaign_details[campaign_id] = {**campaign_details.get(campaign_id, {}), **detail}
        adgroups, adgroup_errors = _list_adgroups(client, advertiser_id, campaign_ids)
        adgroups_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for adgroup in adgroups:
            adgroups_by_campaign[str(adgroup.get("campaign_id") or "")].append(adgroup)

        ad_probe_campaigns: set[str] = set()
        adgroup_ids: list[str] = []
        for campaign in campaigns:
            campaign_id = campaign["campaign_id"]
            campaign_adgroups = adgroups_by_campaign.get(campaign_id, [])
            pre_candidates = _app_candidates(
                ("campaign", campaign_details.get(campaign_id, {})),
                *[("adgroup", item) for item in campaign_adgroups],
            )
            if not _has_app_evidence(pre_candidates):
                ad_probe_campaigns.add(campaign_id)
                adgroup_ids.extend(str(item.get("adgroup_id")) for item in campaign_adgroups if item.get("adgroup_id"))
        ads, ad_errors = _list_ads(client, advertiser_id, adgroup_ids)
        errors.extend({"advertiser_id": advertiser_id, **error} for error in campaign_errors + adgroup_errors + ad_errors)

        ads_by_adgroup: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ad in ads:
            ads_by_adgroup[str(ad.get("adgroup_id") or "")].append(ad)

        account_rows.append(
            {
                **advertiser,
                "campaign_count": len(campaigns),
                "adgroup_count": len(adgroups),
                "ad_count": len(ads),
                "ad_probe_campaign_count": len(ad_probe_campaigns),
                "ad_probe_skipped_campaign_count": max(len(campaigns) - len(ad_probe_campaigns), 0),
                "spend": round(sum(item["spend"] for item in campaigns), 2),
            }
        )

        for campaign in campaigns:
            campaign_id = campaign["campaign_id"]
            detail = campaign_details.get(campaign_id, {})
            campaign_adgroups = adgroups_by_campaign.get(campaign_id, [])
            campaign_ads = [
                ad
                for adgroup in campaign_adgroups
                for ad in ads_by_adgroup.get(str(adgroup.get("adgroup_id") or ""), [])
            ]
            candidates = _app_candidates(
                ("campaign", detail),
                *[("adgroup", item) for item in campaign_adgroups],
                *[("ad", item) for item in campaign_ads[:100]],
            )
            group = groups.setdefault(
                candidates["app_key"],
                {
                    "app_key": candidates["app_key"],
                    "app_ids": set(),
                    "app_names": set(),
                    "app_urls": set(),
                    "promotion_types": set(),
                    "advertisers": set(),
                    "campaign_count": 0,
                    "ad_probe_skipped_campaign_count": 0,
                    "adgroup_count": 0,
                    "ad_count": 0,
                    "spend": 0.0,
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0.0,
                    "campaigns": [],
                },
            )
            group["app_ids"].update(candidates["app_ids"])
            group["app_names"].update(candidates["app_names"])
            group["app_urls"].update(candidates["app_urls"])
            group["promotion_types"].update(candidates["promotion_types"])
            group["advertisers"].add(advertiser_id)
            group["campaign_count"] += 1
            if campaign_id not in ad_probe_campaigns:
                group["ad_probe_skipped_campaign_count"] += 1
            group["adgroup_count"] += len(campaign_adgroups)
            group["ad_count"] += len(campaign_ads)
            group["spend"] += campaign["spend"]
            group["impressions"] += campaign["impressions"]
            group["clicks"] += campaign["clicks"]
            group["conversions"] += campaign["conversions"]
            group["campaigns"].append(
                {
                    **campaign,
                    "campaign_name": detail.get("campaign_name"),
                    "objective_type": detail.get("objective_type"),
                    "campaign_automation_type": detail.get("campaign_automation_type"),
                    "adgroup_count": len(campaign_adgroups),
                    "ad_count": len(campaign_ads),
                    "ad_probe_skipped": campaign_id not in ad_probe_campaigns,
                    "app_ids": candidates["app_ids"],
                    "app_names": candidates["app_names"],
                    "app_urls": candidates["app_urls"],
                }
            )

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        item = {
            "app_key": group["app_key"],
            "app_ids": sorted(group["app_ids"]),
            "app_names": sorted(group["app_names"]),
            "app_urls": sorted(group["app_urls"]),
            "promotion_types": sorted(group["promotion_types"]),
            "advertisers": sorted(group["advertisers"]),
            "campaign_count": group["campaign_count"],
            "ad_probe_skipped_campaign_count": group["ad_probe_skipped_campaign_count"],
            "adgroup_count": group["adgroup_count"],
            "ad_count": group["ad_count"],
            "spend": round(group["spend"], 2),
            "impressions": group["impressions"],
            "clicks": group["clicks"],
            "ctr": round(group["clicks"] / group["impressions"] * 100, 2) if group["impressions"] else 0,
            "conversions": group["conversions"],
        }
        if include_campaigns:
            item["campaigns"] = sorted(group["campaigns"], key=lambda value: value["spend"], reverse=True)
        rows.append(item)
    rows.sort(key=lambda item: item["spend"], reverse=True)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "advertiser_limit": advertiser_limit,
        "campaign_limit": campaign_limit,
        "advertisers": account_rows,
        "app_count": len(rows),
        "total_spend": round(sum(row["spend"] for row in rows), 2),
        "rows": rows,
        "errors": errors,
    }
