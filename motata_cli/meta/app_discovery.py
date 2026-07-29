from __future__ import annotations

import json
import re
from collections import defaultdict
from html import unescape
from typing import Any
from urllib.parse import urlparse

import requests

from .landing_pages import _ad_account_path, _fnum, _insights_params_base, _paginate_insights, _time_params, discover_recent_spend_accounts


APP_CAMPAIGN_FIELDS = "id,name,objective,status,effective_status,configured_status"
APP_ADSET_FIELDS = ",".join(
    [
        "id",
        "name",
        "campaign_id",
        "promoted_object",
        "optimization_goal",
        "billing_event",
        "destination_type",
        "status",
        "effective_status",
        "configured_status",
    ]
)
APP_AD_FIELDS = ",".join(
    [
        "id",
        "name",
        "campaign_id",
        "adset_id",
        "status",
        "effective_status",
        "configured_status",
        "tracking_specs",
        "conversion_specs",
        "creative{id,name,object_story_spec,asset_feed_spec,link_url,object_url,template_url,call_to_action_type,url_tags}",
    ]
)
APP_STORE_HOST_MARKERS = ("itunes.apple.com", "apps.apple.com", "play.google.com")
W2A_ATTRIBUTION_HOST_MARKERS = (
    "app.adjust.com",
    "adjust.com",
    "app.appsflyer.com",
    "onelink.me",
    "app.link",
    "branch.io",
    "kochava.com",
    "singular.net",
)
W2A_CONTEXT_MARKERS = ("w2a", "ios", "android", "app install", "app_install", "app promotion", "app_promotion")


def _campaign_insight_rows(
    meta: Any,
    account_id: str,
    *,
    since: str | None,
    until: str | None,
    date_preset: str | None,
    campaign_limit: int,
) -> list[dict[str, Any]]:
    params = {
        "level": "campaign",
        "fields": "campaign_id,campaign_name,objective,spend,impressions,clicks",
        "limit": max(campaign_limit, 1),
        "sort": "spend_descending",
        **_insights_params_base(attribution=False),
        **_time_params(since=since, until=until, date_preset=date_preset),
    }
    rows = []
    for item in _paginate_insights(meta, f"{_ad_account_path(account_id)}/insights", params=params):
        spend = _fnum(item.get("spend"))
        campaign_id = str(item.get("campaign_id") or "").strip()
        if campaign_id and spend > 0:
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "campaign_name": item.get("campaign_name"),
                    "objective": item.get("objective"),
                    "spend": spend,
                    "impressions": int(_fnum(item.get("impressions"))),
                    "clicks": int(_fnum(item.get("clicks"))),
                }
            )
    rows.sort(key=lambda item: item["spend"], reverse=True)
    return rows[:campaign_limit]


def _active_accounts(
    meta: Any,
    account_id: str | None,
    *,
    since: str | None,
    until: str | None,
    date_preset: str | None,
    account_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return discover_recent_spend_accounts(
        meta,
        account_id,
        since=since,
        until=until,
        date_preset=date_preset,
        account_limit=account_limit,
    )


def _paginate_with_fallback(meta: Any, path: str, field_variants: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    for fields in field_variants:
        try:
            return meta.paginate(path, params={"fields": fields, "limit": 500}), errors
        except Exception as exc:
            errors.append(str(exc))
    return [], errors


def _collect_app_hints(payload: Any, source: str, found: list[dict[str, Any]], path: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if any(token in lowered for token in ("app", "application", "store", "promoted_object", "url", "link")):
                if isinstance(value, (str, int, float)) or value is None:
                    found.append({"source": source, "path": child_path, "value": value})
                elif isinstance(value, dict) and len(value) <= 20:
                    found.append({"source": source, "path": child_path, "value": value})
            _collect_app_hints(value, source, found, child_path)
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:50]):
            _collect_app_hints(item, source, found, f"{path}[{index}]")


def _app_details(meta: Any, app_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for app_id in sorted(app_ids):
        try:
            rows.append(meta.get(app_id, params={"fields": "id,name,app_domains,category,link,namespace"}))
        except Exception as exc:
            rows.append({"id": app_id, "_error": str(exc)})
    return rows


def _app_candidates(*payloads: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    hints: list[dict[str, Any]] = []
    for source, payload in payloads:
        _collect_app_hints(payload, source, hints)

    app_ids: set[str] = set()
    store_urls: set[str] = set()
    link_urls: set[str] = set()
    for source, payload in payloads:
        if source == "adset":
            promoted_object = payload.get("promoted_object") or {}
            if isinstance(promoted_object, dict):
                if promoted_object.get("application_id"):
                    app_ids.add(str(promoted_object["application_id"]))
                if promoted_object.get("object_store_url"):
                    store_urls.add(str(promoted_object["object_store_url"]))
    for hint in hints:
        path = str(hint.get("path") or "").lower()
        value = hint.get("value")
        if ("application_id" in path or path.endswith(".application")) and value:
            app_ids.add(str(value))
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            if any(marker in value for marker in APP_STORE_HOST_MARKERS):
                store_urls.add(value)
            else:
                link_urls.add(value)
    app_key = sorted(app_ids)[0] if app_ids else (sorted(store_urls)[0] if store_urls else "unknown")
    return {
        "app_key": app_key,
        "app_ids": sorted(app_ids),
        "store_urls": sorted(store_urls),
        "link_urls": sorted(link_urls),
        "w2a_urls": [],
        "redirect_probe_count": 0,
        "hints": hints,
    }


def _is_store_url(url: str) -> bool:
    return any(marker in urlparse(url).netloc.lower() for marker in APP_STORE_HOST_MARKERS)


def _w2a_known_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(marker in host for marker in W2A_ATTRIBUTION_HOST_MARKERS)


def _candidate_text_looks_w2a(candidates: dict[str, Any]) -> bool:
    values: list[str] = []
    for hint in candidates.get("hints") or []:
        value = hint.get("value")
        if isinstance(value, (str, int, float)):
            values.append(str(value))
    haystack = " ".join(values).lower()
    return any(marker in haystack for marker in W2A_CONTEXT_MARKERS)


def _resolve_store_redirect(url: str) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=8, allow_redirects=False)
    except requests.RequestException:
        return None
    candidates = [response.headers.get("location") or "", response.url or ""]
    body = unescape(response.text or "")
    candidates.extend(
        re.findall(
            r"(?:https?|itms-apps+s?)://(?:apps\.apple\.com|itunes\.apple\.com|play\.google\.com)[^\"'<> ]+",
            body,
        )
    )
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.startswith(("itms-apps://", "itms-appss://")):
            candidate = candidate.split("://", 1)[1]
            candidate = "https://" + candidate
        if _is_store_url(candidate):
            return candidate
    return None


def _augment_app_candidates_with_redirects(candidates: dict[str, Any], *, max_urls: int = 40) -> dict[str, Any]:
    store_urls = set(candidates.get("store_urls") or [])
    w2a_urls = set(candidates.get("w2a_urls") or [])
    link_urls = list(candidates.get("link_urls") or [])
    probe_contextual_links = _candidate_text_looks_w2a(candidates)
    probe_count = 0
    for url in link_urls[:max_urls]:
        if not (_w2a_known_host(url) or probe_contextual_links):
            continue
        probe_count += 1
        store_url = _resolve_store_redirect(url)
        if not store_url:
            continue
        store_urls.add(store_url)
        w2a_urls.add(url)
    app_key = (candidates.get("app_ids") or [None])[0] or (sorted(store_urls)[0] if store_urls else "unknown")
    return {
        **candidates,
        "app_key": app_key,
        "store_urls": sorted(store_urls),
        "w2a_urls": sorted(w2a_urls),
        "redirect_probe_count": probe_count,
    }


def _has_app_evidence(candidates: dict[str, Any]) -> bool:
    return bool(candidates.get("app_ids") or candidates.get("store_urls"))


def _campaign_detail_from_insight(campaign: dict[str, Any]) -> dict[str, Any] | None:
    if not campaign.get("objective"):
        return None
    return {
        "id": campaign.get("campaign_id"),
        "name": campaign.get("campaign_name"),
        "objective": campaign.get("objective"),
    }


def _platforms(store_urls: list[str]) -> list[str]:
    platforms = set()
    for url in store_urls:
        if "play.google.com" in url:
            platforms.add("android")
        elif "itunes.apple.com" in url or "apps.apple.com" in url:
            platforms.add("ios")
    return sorted(platforms)


def build_meta_app_report(
    meta: Any,
    *,
    account_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    account_limit: int = 2,
    campaign_limit: int = 20,
    include_campaigns: bool = False,
    profile: str = "full",
) -> dict[str, Any]:
    if profile == "batch":
        campaign_limit = min(campaign_limit, 10)

    accounts, errors = _active_accounts(
        meta,
        account_id,
        since=since,
        until=until,
        date_preset=date_preset,
        account_limit=account_limit,
    )
    groups: dict[str, dict[str, Any]] = {}
    account_rows: list[dict[str, Any]] = []
    campaign_detail_fetch_count = 0
    skipped_campaign_detail_fetch_count = 0

    for account in accounts:
        account_id_value = str(account.get("account_id") or account.get("id") or "").removeprefix("act_")
        campaigns = _campaign_insight_rows(
            meta,
            account_id_value,
            since=since,
            until=until,
            date_preset=date_preset,
            campaign_limit=campaign_limit,
        )
        account_rows.append({**account, "campaign_count": len(campaigns)})
        for campaign in campaigns:
            campaign_id = campaign["campaign_id"]
            campaign_detail = _campaign_detail_from_insight(campaign)
            if campaign_detail:
                skipped_campaign_detail_fetch_count += 1
            else:
                campaign_detail_fetch_count += 1
                try:
                    campaign_detail = meta.get(campaign_id, params={"fields": APP_CAMPAIGN_FIELDS})
                except Exception as exc:
                    campaign_detail = {"id": campaign_id, "_error": str(exc)}
            adsets, adset_errors = _paginate_with_fallback(
                meta,
                f"{campaign_id}/adsets",
                [
                    APP_ADSET_FIELDS,
                    "id,name,campaign_id,promoted_object,optimization_goal,billing_event,status,effective_status",
                ],
            )
            for error in adset_errors[-1:] if adset_errors and not adsets else []:
                errors.append({"account_id": account_id_value, "campaign_id": campaign_id, "scope": "adsets", "error": error})
            pre_candidates = _app_candidates(
                ("campaign", campaign_detail),
                *[("adset", item) for item in adsets],
            )
            ad_probe_skipped = _has_app_evidence(pre_candidates)
            if ad_probe_skipped:
                ads, ad_errors = [], []
            else:
                ads, ad_errors = _paginate_with_fallback(
                    meta,
                    f"{campaign_id}/ads",
                    [
                        APP_AD_FIELDS,
                        "id,name,campaign_id,adset_id,status,effective_status,creative{id,name,link_url,object_url,template_url,url_tags}",
                    ],
                )
                for error in ad_errors[-1:] if ad_errors and not ads else []:
                    errors.append({"account_id": account_id_value, "campaign_id": campaign_id, "scope": "ads", "error": error})
            candidates = _app_candidates(
                ("campaign", campaign_detail),
                *[("adset", item) for item in adsets],
                *[("ad", item) for item in ads[:200]],
            )
            candidates = _augment_app_candidates_with_redirects(candidates)
            app_details = _app_details(meta, set(candidates["app_ids"]))
            app_names = sorted({str(item.get("name")) for item in app_details if item.get("name")})
            group = groups.setdefault(
                candidates["app_key"],
                {
                    "app_key": candidates["app_key"],
                    "app_ids": set(),
                    "app_names": set(),
                    "store_urls": set(),
                    "w2a_urls": set(),
                    "platforms": set(),
                    "accounts": set(),
                    "campaign_count": 0,
                    "ad_probe_skipped_campaign_count": 0,
                    "redirect_probe_count": 0,
                    "adset_count": 0,
                    "ad_count": 0,
                    "spend": 0.0,
                    "impressions": 0,
                    "clicks": 0,
                    "campaigns": [],
                },
            )
            group["app_ids"].update(candidates["app_ids"])
            group["app_names"].update(app_names)
            group["store_urls"].update(candidates["store_urls"])
            group["w2a_urls"].update(candidates["w2a_urls"])
            group["platforms"].update(_platforms(candidates["store_urls"]))
            group["accounts"].add(account_id_value)
            group["campaign_count"] += 1
            if ad_probe_skipped:
                group["ad_probe_skipped_campaign_count"] += 1
            group["redirect_probe_count"] += int(candidates.get("redirect_probe_count") or 0)
            group["adset_count"] += len(adsets)
            group["ad_count"] += len(ads)
            group["spend"] += campaign["spend"]
            group["impressions"] += campaign["impressions"]
            group["clicks"] += campaign["clicks"]
            group["campaigns"].append(
                {
                    **campaign,
                    "objective": campaign_detail.get("objective"),
                    "effective_status": campaign_detail.get("effective_status"),
                    "adset_count": len(adsets),
                    "ad_count": len(ads),
                    "ad_probe_skipped": ad_probe_skipped,
                    "redirect_probe_count": candidates.get("redirect_probe_count") or 0,
                    "app_ids": candidates["app_ids"],
                    "app_names": app_names,
                    "store_urls": candidates["store_urls"],
                    "w2a_urls": candidates["w2a_urls"],
                    "app_details": app_details,
                }
            )

    rows = []
    for group in groups.values():
        item = {
            "app_key": group["app_key"],
            "app_ids": sorted(group["app_ids"]),
            "app_names": sorted(group["app_names"]),
            "platforms": sorted(group["platforms"]),
            "store_urls": sorted(group["store_urls"]),
            "w2a_urls": sorted(group["w2a_urls"]),
            "accounts": sorted(group["accounts"]),
            "campaign_count": group["campaign_count"],
            "ad_probe_skipped_campaign_count": group["ad_probe_skipped_campaign_count"],
            "redirect_probe_count": group["redirect_probe_count"],
            "adset_count": group["adset_count"],
            "ad_count": group["ad_count"],
            "spend": round(group["spend"], 2),
            "impressions": group["impressions"],
            "clicks": group["clicks"],
            "ctr": round(group["clicks"] / group["impressions"] * 100, 2) if group["impressions"] else 0,
        }
        if include_campaigns:
            item["campaigns"] = sorted(group["campaigns"], key=lambda value: value["spend"], reverse=True)
        rows.append(item)
    rows.sort(key=lambda item: item["spend"], reverse=True)
    return {
        "since": since,
        "until": until,
        "date_preset": date_preset if not (since or until) else None,
        "account_limit": account_limit,
        "campaign_limit": campaign_limit,
        "profile": profile,
        "accounts": account_rows,
        "app_count": len(rows),
        "campaign_detail_fetch_count": campaign_detail_fetch_count,
        "skipped_campaign_detail_fetch_count": skipped_campaign_detail_fetch_count,
        "total_spend": round(sum(row["spend"] for row in rows), 2),
        "rows": rows,
        "errors": errors,
    }
