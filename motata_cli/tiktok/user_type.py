from __future__ import annotations

from typing import Any, Callable

from motata_cli.meta.user_type import _append_text_evidence, _fnum, _scrape_content, classify_user_types
from motata_cli.tiktok.app_discovery import build_tiktok_app_report, discover_recent_spend_advertisers
from motata_cli.tiktok.landing_pages import build_tiktok_landing_page_report, default_date_range, _extract_collection


def _looks_like_tiktok_short_drama(text: str) -> bool:
    lowered = text.lower()
    chinese_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if any(marker in lowered for marker in ("stardusttv", "dramabox", "short drama", "mini drama")):
        return True
    if ("_iap_" in lowered or " iap " in lowered) and (
        "ttm" in lowered or "breeze" in lowered or chinese_count >= 4
    ):
        return True
    return False


def _with_tiktok_hints(text: str) -> str:
    if _looks_like_tiktok_short_drama(text):
        return f"{text} short drama mini drama episodes"
    return text


def _resolved_dates(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    if start_date and end_date:
        return start_date, end_date
    default_start, default_end = default_date_range(14)
    return start_date or default_start, end_date or default_end


def _resolve_advertisers(
    client: Any,
    *,
    advertiser_ids: list[str] | None,
    advertiser_limit: int,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    explicit = [str(value).strip() for value in (advertiser_ids or []) if str(value).strip()]
    if explicit:
        return explicit, [{"advertiser_id": value} for value in explicit], []
    try:
        assets, errors = discover_recent_spend_advertisers(
            client,
            start_date=start_date,
            end_date=end_date,
            advertiser_limit=advertiser_limit,
        )
    except Exception as exc:
        return [], [], [{"scope": "advertiser_discovery", "error": str(exc)}]
    ids = [str(item.get("advertiser_id") or "").strip() for item in assets if item.get("advertiser_id")]
    return ids, assets, errors


def _advertiser_info_rows(client: Any, advertiser_ids: list[str]) -> list[dict[str, Any]]:
    if not advertiser_ids or not hasattr(client, "get_account_info"):
        return []
    try:
        payload = client.get_account_info(
            advertiser_ids,
            fields=["advertiser_id", "name", "status", "currency", "timezone"],
        )
    except Exception:
        return []
    return _extract_collection(payload, "list")


def build_tiktok_user_type_report(
    client: Any,
    *,
    advertiser_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    advertiser_limit: int = 10,
    campaign_limit: int = 10,
    ad_limit: int = 5,
    content_limit: int = 60,
    page_size: int = 1000,
    max_pages: int = 50,
    smart_plus: bool = False,
    include_evidence: bool = False,
    product_scraper: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start, end = _resolved_dates(start_date, end_date)
    resolved_ids, discovered_advertisers, discovery_errors = _resolve_advertisers(
        client,
        advertiser_ids=advertiser_ids,
        advertiser_limit=advertiser_limit,
        start_date=start,
        end_date=end,
    )
    account_info_rows = _advertiser_info_rows(client, resolved_ids)
    if not resolved_ids:
        return {
            "start_date": start,
            "end_date": end,
            "advertiser_limit": advertiser_limit,
            "campaign_limit": campaign_limit,
            "ad_limit": ad_limit,
            "content_limit": content_limit,
            "advertisers": discovered_advertisers,
            "campaign_count": 0,
            "sample_count": 0,
            "scraped_content_count": 0,
            "top_types": [],
            "all_types": [],
            "campaigns": [],
            "landing_pages": [],
            "app_rows": [],
            "scraped_content": [],
            "errors": discovery_errors or [
                {
                    "scope": "advertiser_discovery",
                    "error": "No TikTok advertiser IDs found. Provide --advertiser-id or a token with BC asset access.",
                }
            ],
        }

    app_report = build_tiktok_app_report(
        client,
        advertiser_ids=resolved_ids,
        start_date=start,
        end_date=end,
        advertiser_limit=advertiser_limit,
        campaign_limit=campaign_limit,
        include_campaigns=True,
    )
    app_campaign_ids: set[str] = set()
    for row in app_report.get("rows") or []:
        has_app_destination = bool(row.get("app_ids") or row.get("app_urls"))
        for campaign in row.get("campaigns") or []:
            campaign_has_app_destination = has_app_destination or bool(campaign.get("app_ids") or campaign.get("app_urls"))
            if campaign_has_app_destination and campaign.get("campaign_id"):
                app_campaign_ids.add(str(campaign["campaign_id"]))

    landing_report = build_tiktok_landing_page_report(
        client,
        advertiser_ids=resolved_ids,
        start_date=start,
        end_date=end,
        top=content_limit if content_limit > 0 else None,
        report_page_size=page_size,
        max_pages=max_pages,
        enrich_product=True,
        product_limit=content_limit,
        include_ads=False,
        smart_plus=smart_plus,
        ad_limit=ad_limit,
        product_scraper=product_scraper,
        skip_campaign_ids=app_campaign_ids,
    )

    samples: list[dict[str, Any]] = []
    url_rows: list[dict[str, Any]] = []
    campaign_rows: list[dict[str, Any]] = []

    advertiser_names = {
        str(item.get("advertiser_id") or ""): item.get("advertiser_name")
        for item in discovered_advertisers
        if item.get("advertiser_id") and item.get("advertiser_name")
    }
    advertiser_names.update(
        {
            str(item.get("advertiser_id") or ""): item.get("name")
            for item in account_info_rows
            if item.get("advertiser_id") and item.get("name")
        }
    )
    account_info_by_id = {str(item.get("advertiser_id") or ""): item for item in account_info_rows}
    for advertiser in app_report.get("advertisers") or []:
        advertiser_id = str(advertiser.get("advertiser_id") or "")
        account_info = account_info_by_id.get(advertiser_id, {})
        name = advertiser.get("advertiser_name") or advertiser_names.get(advertiser_id)
        _append_text_evidence(
            samples,
            text=_with_tiktok_hints(
                " ".join(
                    str(value or "")
                    for value in (
                        name,
                        advertiser.get("bc_name"),
                        advertiser.get("advertiser_account_type"),
                        account_info.get("status"),
                    )
                )
            ),
            source="advertiser",
            spend=_fnum(advertiser.get("spend")),
            ref=advertiser_id,
        )

    for row in app_report.get("rows") or []:
        spend = _fnum(row.get("spend"))
        text_parts = [
            row.get("app_key"),
            " ".join(row.get("app_ids") or []),
            " ".join(row.get("app_names") or []),
            " ".join(row.get("app_urls") or []),
            " ".join(row.get("promotion_types") or []),
        ]
        _append_text_evidence(samples, text=" ".join(str(part or "") for part in text_parts), source="app_group", spend=spend, ref=row.get("app_key"))
        for url in row.get("app_urls") or []:
            _append_text_evidence(samples, text=url, source="app_url", spend=spend, ref=url)
            url_rows.append({"url": url, "spend": spend, "source": "app_url"})
        for campaign in row.get("campaigns") or []:
            campaign_spend = _fnum(campaign.get("spend"))
            campaign_rows.append({**campaign, "source": "app_report"})
            _append_text_evidence(
                samples,
                text=_with_tiktok_hints(
                    " ".join(
                        str(value or "")
                        for value in (
                            campaign.get("campaign_name"),
                            campaign.get("objective_type"),
                            campaign.get("campaign_automation_type"),
                            " ".join(campaign.get("app_names") or []),
                            " ".join(campaign.get("app_urls") or []),
                        )
                    )
                ),
                source="campaign",
                spend=campaign_spend,
                ref=campaign.get("campaign_id"),
            )
            for url in campaign.get("app_urls") or []:
                url_rows.append({"url": url, "spend": campaign_spend or spend, "source": "campaign_app_url"})

    landing_rows = landing_report.get("rows") or []
    for row in landing_rows:
        spend = _fnum(row.get("spend"))
        url = row.get("url")
        text = " ".join(
            str(value or "")
            for value in (url, row.get("product_name"), row.get("price"), " ".join(row.get("campaigns") or []))
        )
        _append_text_evidence(samples, text=text, source="landing_page", spend=spend, ref=url)
        if url:
            url_rows.append({"url": url, "spend": spend, "source": "landing_page"})
        for ad in row.get("top_ads") or []:
            _append_text_evidence(
                samples,
                text=_with_tiktok_hints(" ".join(str(value or "") for value in (ad.get("ad_name"), ad.get("campaign_name")))),
                source="ad",
                spend=_fnum(ad.get("spend")) or spend,
                ref=ad.get("ad_id"),
            )

    # build_tiktok_landing_page_report already enriches landing rows; scrape app URLs and any skipped URLs here.
    scraped = _scrape_content(url_rows, content_limit=content_limit, product_scraper=product_scraper)
    for item in scraped:
        content = item.get("content") or {}
        _append_text_evidence(
            samples,
            text=" ".join(
                str(part or "")
                for part in (
                    item.get("url"),
                    content.get("name"),
                    content.get("description"),
                    content.get("subtitle"),
                    f"primary_genre {content.get('primary_genre') or ''}",
                    f"genres {' '.join(str(value) for value in (content.get('genres') or []) if value)}",
                    " ".join(f"genre {value}" for value in (content.get("genres") or []) if value),
                    content.get("url_type"),
                    content.get("price"),
                )
            ),
            source=f"scraped_{item.get('source')}",
            spend=_fnum(item.get("spend")),
            ref=str(item.get("url") or ""),
        )

    classification = classify_user_types(samples, include_evidence=include_evidence)
    errors = [
        *discovery_errors,
        *((app_report.get("errors") or [])),
        *((landing_report.get("account_errors") or [])),
        *((landing_report.get("account_warnings") or [])),
    ]
    return {
        "start_date": start,
        "end_date": end,
        "advertiser_limit": advertiser_limit,
        "campaign_limit": campaign_limit,
        "ad_limit": ad_limit,
        "content_limit": content_limit,
        "smart_plus": smart_plus,
        "advertisers": [
            {**row, **({"name": advertiser_names.get(str(row.get("advertiser_id") or ""))} if advertiser_names.get(str(row.get("advertiser_id") or "")) else {})}
            for row in (app_report.get("advertisers") or discovered_advertisers)
        ],
        "campaign_count": len(campaign_rows),
        "sample_count": len(samples),
        "scraped_content_count": len(scraped),
        "landing_skipped_url_probe_count": landing_report.get("skipped_url_probe_count", 0),
        "top_types": classification["top_types"],
        "all_types": classification["all_types"],
        "campaigns": sorted(campaign_rows, key=lambda value: _fnum(value.get("spend")), reverse=True),
        "landing_pages": landing_rows,
        "app_rows": app_report.get("rows") or [],
        "scraped_content": scraped if include_evidence else [
            {"url": row.get("url"), "spend": row.get("spend"), "source": row.get("source"), "error": (row.get("content") or {}).get("error")}
            for row in scraped
        ],
        "errors": errors,
    }
