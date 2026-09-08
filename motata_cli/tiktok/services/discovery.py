"""TikTok discovery helpers.

Dependencies are explicit and supplied by the CLI compatibility wrappers; this
module never imports the command facade. Source bodies retain their original
formatting and behavior.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..client import TikTokClient


def select_tiktok_creative_portfolios(
    portfolios: list[dict[str, Any]],
    *,
    creative_portfolio_ids: list[str] | None = None,
    creative_portfolio_types: list[str] | None = None,
    title: str | None = None,
    query: str | None = None,
    limit: int = 10,
    deps: Any,
) -> dict[str, Any]:
    requested = deps.compact_mapping(
        {
            "creative_portfolio_ids": creative_portfolio_ids,
            "creative_portfolio_types": creative_portfolio_types,
            "title": title,
            "query": query,
            "limit": limit,
        }
    )
    title_filter = deps.normalize_text(title)
    query_filter = deps.normalize_text(query)
    type_filters = {deps.normalize_text(value) for value in (creative_portfolio_types or []) if deps.normalize_text(value)}
    portfolio_id_filters = {str(value).strip() for value in (creative_portfolio_ids or []) if str(value).strip()}
    selected: list[dict[str, Any]] = []

    for portfolio in portfolios:
        if not isinstance(portfolio, dict):
            continue
        portfolio_id = portfolio.get("creative_portfolio_id")
        if portfolio_id_filters and str(portfolio_id) not in portfolio_id_filters:
            continue
        if type_filters and deps.normalize_text(portfolio.get("creative_portfolio_type")) not in type_filters:
            continue

        contents = deps.extract_tiktok_creative_portfolio_contents(portfolio)
        summary = deps.summarize_tiktok_creative_portfolio(portfolio)
        searchable = " ".join(
            deps.collect_strings(
                [
                    summary,
                    contents,
                ]
            )
        )
        searchable_text = deps.normalize_text(searchable)
        score = 0
        if portfolio_id_filters:
            score += 100
        if title_filter:
            portfolio_title = deps.normalize_text(summary.get("title"))
            if portfolio_title == title_filter:
                score += 30
            elif title_filter in portfolio_title or title_filter in searchable_text:
                score += 15
        if query_filter:
            if query_filter in searchable_text:
                score += 10
        if not title_filter and not query_filter and not portfolio_id_filters and not type_filters:
            score += 1
        selected.append(
            {
                "score": score,
                "summary": summary,
                "raw": portfolio,
            }
        )

    selected.sort(
        key=lambda item: (
            item["score"],
            item["summary"].get("modify_time") or "",
            item["summary"].get("create_time") or "",
        ),
        reverse=True,
    )
    limited = selected[: max(limit, 0)] if limit else selected
    return {
        "requested": requested,
        "candidate_count": len(selected),
        "selected_count": len(limited),
        "selected": [item["summary"] for item in limited],
        "best_match": limited[0]["summary"] if limited else None,
    }


def validate_smartplus_app_eligibility(
    client: TikTokClient,
    *,
    advertiser_id: str,
    payload: dict[str, Any],
    context_label: str,
    deps: Any,
) -> dict[str, Any] | None:
    if str(payload.get("objective_type") or "").upper() != "APP_PROMOTION":
        return None
    app_id = str(payload.get("app_id") or "").strip()
    if not app_id:
        return None

    app_info = client.get_app_info(advertiser_id, app_id)
    app = app_info.get("data", {}).get("app") if isinstance(app_info, dict) else None
    if not isinstance(app, dict):
        return None

    if app.get("advanced_dedicated_campaign_allowed") is False:
        app_name = app.get("app_name") or app_id
        platform = app.get("platform") or "UNKNOWN"
        pages_response = client.list_pages(
            advertiser_id,
            business_types=["APP_PROFILE_PAGE"],
            app_id=app_id,
            page=1,
            page_size=20,
        )
        pages = deps.extract_response_list(pages_response, "list")
        page_summary = [
            deps.compact_mapping(
                {
                    "page_id": page.get("page_id"),
                    "title": page.get("title"),
                    "status": page.get("status"),
                    "preview_url": page.get("preview_url"),
                    "destination_urls": page.get("destination_urls"),
                }
            )
            for page in pages
        ]
        page_hint = (
            f"App Profile Page candidates found: {page_summary}"
            if page_summary
            else "No App Profile Page candidates were returned by /page/get for this app."
        )
        raise deps.CliError(
            "SmartPlus iOS app promotion is not enabled for this app in the current advertiser.\n"
            f"Context: {context_label}\n"
            f"App: {app_name} ({app_id}, platform={platform})\n"
            "TikTok app_info reports advanced_dedicated_campaign_allowed=false, so the request will be rejected before payload shape becomes relevant.\n"
            f"{page_hint}\n"
            "Next step: enable Advanced Dedicated Campaign / App Profile Page for this app in TikTok Ads Manager or ask your TikTok rep to unlock the iOS Smart+ path."
        )

    return app


def find_tiktok_smartplus_app_template(
    client: TikTokClient,
    *,
    advertiser_id: str,
    app_id: str | None = None,
    app_name: str | None = None,
    app_promotion_type: str | None = None,
    deps: Any,
) -> dict[str, Any]:
    requested = deps.compact_mapping(
        {
            "app_id": app_id,
            "app_name": app_name,
            "app_promotion_type": app_promotion_type,
        }
    )
    apps = deps.extract_response_list(client.list_apps(advertiser_id), "apps")
    selected_app = deps.select_tiktok_app(apps, app_id=app_id, app_name=app_name)
    requested_app_id = str(selected_app.get("app_id")) if isinstance(selected_app, dict) and selected_app.get("app_id") else None
    expected_promotion_type = deps.resolve_tiktok_app_promotion_type(
        selected_app.get("platform") if isinstance(selected_app, dict) else None
    )

    campaigns = deps.collect_paginated_entities(
        lambda page, page_size: client.list_campaigns(
            advertiser_id,
            page=page,
            page_size=page_size,
            smart_plus=True,
        ),
        "list",
    )
    campaign_by_id = {
        str(campaign.get("campaign_id")): campaign
        for campaign in campaigns
        if campaign.get("campaign_id") and campaign.get("objective_type") == "APP_PROMOTION"
    }

    adgroups = deps.collect_paginated_entities(
        lambda page, page_size: client.list_adgroups(
            advertiser_id,
            page=page,
            page_size=page_size,
            smart_plus=True,
        ),
        "list",
    )

    candidates: list[dict[str, Any]] = []
    for adgroup in adgroups:
        campaign_id = str(adgroup.get("campaign_id") or "")
        campaign = campaign_by_id.get(campaign_id)
        if campaign is None:
            continue
        adgroup_app_id = adgroup.get("app_id")
        if not adgroup_app_id:
            continue
        if requested_app_id and str(adgroup_app_id) != requested_app_id:
            continue
        score = 100
        if app_promotion_type:
            if campaign.get("app_promotion_type") == app_promotion_type:
                score += 20
            elif campaign.get("app_promotion_type") not in {None, app_promotion_type}:
                continue
        if expected_promotion_type:
            if adgroup.get("promotion_type") == expected_promotion_type:
                score += 10
            else:
                continue
        placements = set(adgroup.get("placements") or [])
        if "PLACEMENT_TIKTOK" in placements:
            score += 8
        if "PLACEMENT_GLOBAL_APP_BUNDLE" in placements:
            score += 2
        if "PLACEMENT_PANGLE" in placements:
            score += 1
        if adgroup.get("optimization_goal") == "INSTALL":
            score += 3
        if campaign.get("operation_status") == "ENABLE":
            score += 2
        if adgroup.get("operation_status") == "ENABLE":
            score += 1
        candidates.append(
            {
                "score": score,
                "campaign": campaign,
                "adgroup": adgroup,
            }
        )

    if not candidates:
        return {
            "requested": requested,
            "app": deps.summarize_tiktok_app(selected_app) if selected_app else None,
            "candidate_count": 0,
            "template_campaign": None,
            "template_adgroup": None,
            "template_ad": None,
        }

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["campaign"].get("modify_time") or "",
            item["campaign"].get("create_time") or "",
        ),
        reverse=True,
    )
    best = candidates[0]
    best_campaign = best["campaign"]
    best_adgroup_summary = best["adgroup"]
    best_adgroup_id = deps.validate_non_empty(best_adgroup_summary.get("adgroup_id"), "adgroup_id")
    best_adgroup = deps.merge_source_snapshot(
        best_adgroup_summary,
        client.get_adgroup(advertiser_id, best_adgroup_id, smart_plus=True),
    )

    ads_result = client.list_ads(
        advertiser_id,
        filtering={"adgroup_ids": [best_adgroup_id]},
        page=1,
        page_size=20,
        smart_plus=True,
    )
    best_ad: dict[str, Any] | None = None
    for ad_summary in ads_result.get("data", {}).get("list", []):
        source_ad_id = deps.extract_source_ad_id(ad_summary, smart_plus=True)
        ad_detail = deps.merge_source_snapshot(
            ad_summary,
            client.get_ad(advertiser_id, source_ad_id, smart_plus=True),
        )
        tracking_app_id = (((ad_detail.get("ad_configuration") or {}).get("tracking_info") or {}).get("tracking_app_id"))
        if requested_app_id is None or str(tracking_app_id or best_adgroup.get("app_id")) == str(best_adgroup.get("app_id")):
            best_ad = ad_detail
            break
    if best_ad is None:
        ad_list = ads_result.get("data", {}).get("list", [])
        if ad_list:
            source_ad_id = deps.extract_source_ad_id(ad_list[0], smart_plus=True)
            best_ad = deps.merge_source_snapshot(
                ad_list[0],
                client.get_ad(advertiser_id, source_ad_id, smart_plus=True),
            )

    if selected_app is None:
        selected_app = deps.first_dict(
            [
                app
                for app in apps
                if str(app.get("app_id")) == str(best_adgroup.get("app_id"))
            ]
        )

    return {
        "requested": requested,
        "app": deps.summarize_tiktok_app(selected_app) if selected_app else deps.compact_mapping({"app_id": best_adgroup.get("app_id")}),
        "candidate_count": len(candidates),
        "template_campaign": deps.summarize_tiktok_template_campaign(best_campaign),
        "template_adgroup": deps.summarize_tiktok_template_adgroup(best_adgroup),
        "template_ad": deps.summarize_tiktok_template_ad(best_ad) if best_ad else None,
    }


def inspect_tiktok_creative_portfolios(
    client: TikTokClient,
    *,
    advertiser_id: str,
    creative_portfolio_ids: list[str] | None = None,
    creative_portfolio_types: list[str] | None = None,
    title: str | None = None,
    query: str | None = None,
    limit: int = 10,
    page_size: int = 100,
    max_pages: int = 20,
    include_raw: bool = False,
    deps: Any,
) -> dict[str, Any]:
    portfolios = deps.collect_paginated_entities(
        lambda page, current_page_size: client.list_creative_portfolios(
            advertiser_id,
            filtering=deps.build_creative_portfolio_filtering(
                deps.argparse.Namespace(
                    creative_portfolio_ids=creative_portfolio_ids,
                    creative_portfolio_types=creative_portfolio_types,
                    payload_json=None,
                    payload_file=None,
                )
            ),
            page=page,
            page_size=current_page_size,
        ),
        "creative_portfolios",
        page_size=page_size,
        max_pages=max_pages,
    )
    selection = deps.select_tiktok_creative_portfolios(
        portfolios,
        creative_portfolio_ids=creative_portfolio_ids,
        creative_portfolio_types=creative_portfolio_types,
        title=title,
        query=query,
        limit=limit,
    )
    inspected: list[dict[str, Any]] = []
    for selected in selection.get("selected") or []:
        portfolio_id = selected.get("creative_portfolio_id")
        if not portfolio_id:
            continue
        try:
            detail_response = client.get_creative_portfolio(advertiser_id, str(portfolio_id))
            detail_data = detail_response.get("data") if isinstance(detail_response, dict) else detail_response
            detail_data = detail_data if isinstance(detail_data, dict) else {}
            inspected.append(
                deps.compact_mapping(
                    {
                        "summary": selected,
                        "detail_summary": deps.summarize_tiktok_creative_portfolio(detail_data),
                        "detail": detail_data if include_raw else None,
                    }
                )
            )
        except deps.CliError as exc:
            inspected.append(
                {
                    "summary": selected,
                    "error": str(exc),
                }
            )
    return {
        "advertiser_id": advertiser_id,
        "requested": selection.get("requested"),
        "candidate_count": selection.get("candidate_count"),
        "selected_count": selection.get("selected_count"),
        "best_match": selection.get("best_match"),
        "inspected": inspected,
    }


def collect_tiktok_campaign_creative_hints(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    page_size: int = 100,
    max_pages: int = 20,
    deps: Any,
) -> dict[str, Any]:
    campaign = deps.merge_source_snapshot({}, client.get_campaign(advertiser_id, campaign_id, smart_plus=smart_plus))
    adgroups = deps.collect_paginated_entities(
        lambda page, current_page_size: client.list_adgroups(
            advertiser_id,
            filtering={"campaign_ids": [campaign_id]},
            page=page,
            page_size=current_page_size,
            smart_plus=smart_plus,
        ),
        "list",
        page_size=page_size,
        max_pages=max_pages,
    )
    adgroup_summaries: list[dict[str, Any]] = []
    ads: list[dict[str, Any]] = []
    identity_ids: list[str] = []
    image_ids: list[str] = []
    video_ids: list[str] = []
    landing_page_urls: list[str] = []
    text_tokens: list[str] = []
    app_ids: list[str] = []

    for adgroup in adgroups:
        adgroup_id = str(adgroup.get("adgroup_id") or "").strip()
        if not adgroup_id:
            continue
        adgroup_summaries.append(deps.summarize_tiktok_template_adgroup(adgroup))
        adgroup_app_id = adgroup.get("app_id")
        if isinstance(adgroup_app_id, str) and adgroup_app_id.strip():
            app_ids.append(adgroup_app_id.strip())
        ad_list = deps.collect_paginated_entities(
            lambda page, current_page_size: client.list_ads(
                advertiser_id,
                filtering={"adgroup_ids": [adgroup_id]},
                page=page,
                page_size=current_page_size,
                smart_plus=smart_plus,
            ),
            "list",
            page_size=page_size,
            max_pages=max_pages,
        )
        for ad_summary in ad_list:
            source_ad_id = deps.extract_source_ad_id(ad_summary, smart_plus=smart_plus)
            try:
                ad_detail = deps.merge_source_snapshot(
                    ad_summary,
                    client.get_ad(advertiser_id, source_ad_id, smart_plus=smart_plus),
                )
            except deps.CliError:
                ad_detail = ad_summary
            ads.append(ad_detail)
            identity_refs = deps.extract_identity_refs(ad_detail, smart_plus=smart_plus)
            if identity_refs.get("identity_id"):
                identity_ids.append(str(identity_refs["identity_id"]))
            video_ids.extend(deps.extract_video_ids(ad_detail, smart_plus=smart_plus))
            image_ids.extend(deps.extract_image_ids(ad_detail, smart_plus=smart_plus))
            landing_page_urls.extend(deps.extract_landing_page_urls(ad_detail, smart_plus=smart_plus))
            text_tokens.extend(
                deps.collect_strings(
                    [
                        ad_detail.get("ad_name"),
                        ad_detail.get("ad_text"),
                        ad_detail.get("ad_text_list"),
                        ad_detail.get("creative_list"),
                    ]
                )
            )

    hints = {
        "campaign": deps.summarize_tiktok_template_campaign(campaign),
        "adgroups": adgroup_summaries,
        "ads": [deps.summarize_tiktok_template_ad(ad) for ad in ads],
        "identity_ids": deps.dedupe_strings([value for value in identity_ids if value]),
        "video_ids": deps.dedupe_strings([value for value in video_ids if value]),
        "image_ids": deps.dedupe_strings([value for value in image_ids if value]),
        "landing_page_urls": deps.dedupe_strings([value for value in landing_page_urls if value]),
        "text_tokens": deps.dedupe_strings([deps.normalize_text(value) for value in text_tokens if deps.normalize_text(value)]),
        "app_ids": deps.dedupe_strings([value for value in app_ids if value]),
    }
    return hints


def score_tiktok_creative_portfolio_against_hints(
    portfolio: dict[str, Any],
    hints: dict[str, Any],
    *,
    deps: Any,
) -> dict[str, Any]:
    contents = deps.extract_tiktok_creative_portfolio_contents(portfolio)
    summary = deps.summarize_tiktok_creative_portfolio(portfolio)
    searchable_text = deps.normalize_text(" ".join(deps.collect_strings([summary, contents])))
    content_identity_ids = [
        str(content.get("identity_id")).strip()
        for content in contents
        if isinstance(content.get("identity_id"), str) and str(content.get("identity_id")).strip()
    ]
    content_app_ids = [
        str(content.get("app_id")).strip()
        for content in contents
        if isinstance(content.get("app_id"), str) and str(content.get("app_id")).strip()
    ]
    content_video_ids = [
        str((content.get("advanced_audio_info") or {}).get("video_id")).strip()
        for content in contents
        if isinstance((content.get("advanced_audio_info") or {}).get("video_id"), str)
        and str((content.get("advanced_audio_info") or {}).get("video_id")).strip()
    ]
    content_image_ids = [
        str(content.get("image_id")).strip()
        for content in contents
        if isinstance(content.get("image_id"), str) and str(content.get("image_id")).strip()
    ]
    reasons: list[str] = []
    score = 0

    campaign = hints.get("campaign") or {}
    campaign_name = deps.normalize_text(campaign.get("campaign_name"))
    if campaign_name and campaign_name in searchable_text:
        score += 8
        reasons.append("campaign_name")

    app_ids = {str(value).strip() for value in hints.get("app_ids") or [] if str(value).strip()}
    if app_ids and any(value in app_ids for value in content_app_ids):
        score += 20
        reasons.append("app_id")

    identity_ids = {str(value).strip() for value in hints.get("identity_ids") or [] if str(value).strip()}
    if identity_ids and any(value in identity_ids for value in content_identity_ids):
        score += 18
        reasons.append("identity_id")

    video_ids = {str(value).strip() for value in hints.get("video_ids") or [] if str(value).strip()}
    if video_ids and any(value in video_ids for value in content_video_ids):
        score += 15
        reasons.append("video_id")

    image_ids = {str(value).strip() for value in hints.get("image_ids") or [] if str(value).strip()}
    if image_ids and any(value in image_ids for value in content_image_ids):
        score += 15
        reasons.append("image_id")

    for token in (deps.normalize_text(value) for value in hints.get("text_tokens") or []):
        if token and token in searchable_text:
            score += 2
            reasons.append("text")
            break

    if not reasons:
        score += 1

    return {
        "score": score,
        "reasons": deps.dedupe_strings(reasons),
        "summary": summary,
        "raw": portfolio,
    }


def match_tiktok_creative_portfolios_from_campaign(
    client: TikTokClient,
    *,
    advertiser_id: str,
    campaign_id: str,
    smart_plus: bool,
    limit: int = 10,
    page_size: int = 100,
    max_pages: int = 20,
    deps: Any,
) -> dict[str, Any]:
    hints = deps.collect_tiktok_campaign_creative_hints(
        client,
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        smart_plus=smart_plus,
        page_size=page_size,
        max_pages=max_pages,
    )
    portfolios = deps.collect_paginated_entities(
        lambda page, current_page_size: client.list_creative_portfolios(
            advertiser_id,
            filtering=None,
            page=page,
            page_size=current_page_size,
        ),
        "creative_portfolios",
        page_size=page_size,
        max_pages=max_pages,
    )
    matches = [
        deps.score_tiktok_creative_portfolio_against_hints(portfolio, hints)
        for portfolio in portfolios
        if isinstance(portfolio, dict)
    ]
    matches.sort(
        key=lambda item: (
            item["score"],
            item["summary"].get("modify_time") or "",
            item["summary"].get("create_time") or "",
        ),
        reverse=True,
    )
    selected = matches[: max(limit, 0)] if limit else matches
    return {
        "advertiser_id": advertiser_id,
        "campaign_id": campaign_id,
        "smart_plus": smart_plus,
        "candidate_count": len(matches),
        "selected_count": len(selected),
        "hints": hints,
        "best_match": selected[0]["summary"] if selected else None,
        "selected": [
            deps.compact_mapping(
                {
                    "score": item["score"],
                    "reasons": item["reasons"],
                    "summary": item["summary"],
                    "raw": item["raw"],
                }
            )
            for item in selected
        ],
    }
