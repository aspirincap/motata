from __future__ import annotations

import html
import math
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlparse

from motata_cli.meta.app_discovery import (
    APP_AD_FIELDS,
    APP_ADSET_FIELDS,
    APP_CAMPAIGN_FIELDS,
    _active_accounts,
    _app_candidates,
    _app_details,
    _augment_app_candidates_with_redirects,
    _campaign_detail_from_insight,
    _campaign_insight_rows,
    _has_app_evidence,
    _paginate_with_fallback,
)
from motata_cli.meta.landing_pages import (
    DEFAULT_AD_INSIGHT_FIELDS,
    _fnum,
    _insights_params_base,
    _list_page_access_tokens,
    _paginate_insights,
    _time_params,
    resolve_landing_urls,
)

USER_TYPE_LABELS = [
    "短剧",
    "电商",
    "工具",
    "赌博",
    "代理商/多类型",
    "休闲游戏",
    "社交",
    "小说",
    "中重度游戏",
    "搜索套利",
    "泛娱乐",
    "网赚",
    "金融借贷",
]

KEYWORD_RULES: dict[str, list[tuple[str, float]]] = {
    "短剧": [
        ("短剧", 5), ("微短剧", 5), ("drama", 4), ("short drama", 6), ("mini drama", 5),
        ("episode", 3), ("episodes", 3), ("series", 2), ("reelshort", 6), ("werewolf", 3),
        ("billionaire", 3), ("revenge", 3), ("romance drama", 4), ("watch drama", 5),
        ("playletid", 8), ("playlet", 5), ("stardusttv", 5), ("continue watching", 4),
        ("王妃", 6), ("王爷", 5), ("狼王", 6), ("真千金", 7), ("重生", 6),
        ("豪门", 5), ("赘婿", 5), ("神医", 5), ("白莲", 5), ("崽崽", 4),
        ("皇帝", 4), ("公主", 4), ("归来", 4), ("复仇", 4), ("婚姻", 3.5),
    ],
    "电商": [
        ("shopify", 5), ("myshopify", 5), ("product", 3), ("products", 3), ("shop", 3),
        ("store", 2.5), ("buy", 3), ("sale", 2.5), ("discount", 3), ("coupon", 2.5),
        ("cart", 3), ("checkout", 4), ("amazon", 3), ("temu", 3), ("shein", 3),
        ("woocommerce", 4), ("ecommerce", 5), ("电商", 5), ("购物", 4), ("商城", 4),
    ],
    "工具": [
        ("utility", 4), ("utilities", 4), ("tool", 3), ("tools", 3), ("vpn", 5),
        ("cleaner", 4), ("scan", 3), ("scanner", 4), ("keyboard", 3), ("translator", 4),
        ("translate", 3), ("pdf", 3), ("photo editor", 4), ("editor", 2), ("ai assistant", 3),
        ("weather", 3), ("calculator", 3), ("security", 3), ("antivirus", 4), ("工具", 5),
    ],
    "赌博": [
        ("casino", 6), ("slots", 5), ("slot", 4), ("poker", 5), ("betting", 6),
        ("bet", 4), ("sportsbook", 6), ("lottery", 5), ("roulette", 5), ("blackjack", 5),
        ("jackpot", 5), ("bingo", 3.5), ("gambling", 6), ("博彩", 6), ("赌博", 6),
        ("nhà cái", 7), ("nha cai", 7), ("cá cược", 6), ("ca cuoc", 6),
        ("tài xỉu", 6), ("tai xiu", 6), ("nổ hũ", 6), ("no hu", 6),
        ("lượt chơi miễn phí", 5), ("luot choi mien phi", 5), ("789k", 6),
    ],
    "休闲游戏": [
        ("genre casual", 16), ("genres casual", 16), ("category casual", 14),
        ("casual", 4), ("puzzle", 4), ("match 3", 5), ("match-3", 5), ("merge", 4),
        ("tile", 3), ("solitaire", 4), ("mahjong", 6), ("word game", 4), ("coloring", 3), ("idle", 3),
        ("arcade", 3), ("小游戏", 4), ("休闲游戏", 6), ("消除", 4), ("益智", 4),
    ],
    "社交": [
        ("social", 4), ("chat", 4), ("dating", 5), ("date", 3), ("meet", 3),
        ("friends", 3), ("friend", 2.5), ("messenger", 4), ("community", 3),
        ("live chat", 5), ("video chat", 5), ("社交", 5), ("交友", 5), ("聊天", 4),
    ],
    "小说": [
        ("novel", 5), ("fiction", 4), ("webnovel", 6), ("ebook", 3), ("reader", 3),
        ("reading", 3), ("chapter", 4), ("chapters", 4), ("romance novel", 5),
        ("story app", 4), ("小说", 6), ("阅读", 4), ("书城", 4),
    ],
    "中重度游戏": [
        ("rpg", 5), ("mmorpg", 6), ("strategy", 4), ("slg", 5), ("war", 3),
        ("battle", 3), ("shooter", 4), ("survival", 4), ("kingdom", 3), ("empire", 3),
        ("heroes", 3), ("hero", 2.5), ("raid", 4), ("anime game", 4), ("gacha", 5),
        ("pc games", 5), ("drm-free", 4), ("drm free", 4), ("gog.com", 5),
        ("中重度", 6), ("策略游戏", 5), ("角色扮演", 5),
    ],
    "搜索套利": [
        ("search", 4), ("search results", 6), ("browser", 3), ("query", 4),
        ("find", 0.25), ("finder", 3), ("yahoo", 3), ("bing", 3), ("ask.com", 4),
        ("arbitrage", 6), ("domain parking", 6), ("搜索套利", 7), ("搜索", 3),
    ],
    "泛娱乐": [
        ("entertainment", 5), ("streaming", 4), ("video", 2.5), ("music", 3),
        ("wallpaper", 3), ("horoscope", 3), ("quiz", 3), ("celebrity", 3),
        ("anime", 2.5), ("meme", 3), ("fun", 2), ("泛娱乐", 5), ("娱乐", 4),
    ],
    "网赚": [
        ("earn money", 6), ("make money", 6), ("cash reward", 5), ("rewards", 4),
        ("reward", 3), ("survey", 4), ("cashback", 4), ("work from home", 5),
        ("side hustle", 5), ("赚钱", 6), ("网赚", 7), ("返现", 4),
    ],
    "金融借贷": [
        ("loan", 6), ("loans", 6), ("credit", 4), ("cash advance", 6), ("payday", 5),
        ("borrow", 5), ("installment", 4), ("finance", 4), ("financial", 3),
        ("wallet", 2.5), ("bank", 3), ("借贷", 6), ("贷款", 6), ("金融", 5), ("信贷", 6),
    ],
    "代理商/多类型": [
        ("agency", 6), ("代理商", 7), ("client", 4), ("clients", 4), ("portfolio", 3),
        ("multi vertical", 6), ("multiple vertical", 6), ("多类型", 6),
    ],
}

NEGATIVE_RULES: dict[str, list[str]] = {
    "赌博": ["bingo workout", "bingo fitness"],
    "搜索套利": ["search and rescue"],
}

APP_STORE_HOSTS = ("apps.apple.com", "itunes.apple.com", "play.google.com")


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    text = text.lower()
    text = re.sub(r"[_\-./?&=#:+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_weight(spend: float) -> float:
    # Spend ranks evidence without letting a single large campaign fully dominate text matches.
    return max(1.0, min(6.0, math.log10(max(spend, 0.0) + 10.0)))


def _match_keywords(text: str) -> dict[str, float]:
    haystack = _clean_text(text)
    scores: dict[str, float] = defaultdict(float)
    if not haystack:
        return scores
    for label, rules in KEYWORD_RULES.items():
        negatives = NEGATIVE_RULES.get(label, [])
        if any(negative in haystack for negative in negatives):
            continue
        for keyword, weight in rules:
            needle = _clean_text(keyword)
            if not needle:
                continue
            if any("\u4e00" <= char <= "\u9fff" for char in needle):
                matched = needle in haystack
            else:
                escaped_needle = re.escape(needle).replace(r"\\ ", r"\\s+")
                matched = re.search(rf"(?<![a-z0-9]){escaped_needle}(?![a-z0-9])", haystack) is not None
            if matched:
                scores[label] += weight
    return scores


def _domain_hints(url: str) -> dict[str, float]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    scores: dict[str, float] = defaultdict(float)
    if any(marker in host for marker in ("myshopify.com", "shopify", "amazon.", "temu.", "shein.")):
        scores["电商"] += 5
    if "/products/" in path or "/cart" in path or "/checkout" in path:
        scores["电商"] += 4
    if any(marker in host for marker in APP_STORE_HOSTS):
        scores["工具"] += 0.5
    if any(marker in host for marker in ("casino", "bet", "slots", "poker")):
        scores["赌博"] += 5
    if re.search(r"(^|[.-])(?:789k|888k|88k)(?:[.-]|$)", host):
        scores["赌博"] += 5
    if any(marker in host for marker in ("loan", "credit", "finance", "cash")):
        scores["金融借贷"] += 4
    if "search" in host or "search" in path:
        scores["搜索套利"] += 3
    if any(marker in host for marker in ("gog.com", "steampowered.com", "epicgames.com")):
        scores["中重度游戏"] += 5
    return scores


def _extract_meta_content(html_text: str, name: str) -> str | None:
    match = re.search(
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']',
            html_text,
            flags=re.IGNORECASE,
        )
    return html.unescape(match.group(1)).strip() if match else None


def _scrape_generic_landing_page(url: str) -> dict[str, Any]:
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    html_text = response.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else None
    description = _extract_meta_content(html_text, "description") or _extract_meta_content(html_text, "og:description")
    body = re.sub(r"<script\b[^>]*>.*?</script>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip())
    return {
        "name": title,
        "description": " ".join(part for part in (description, body[:1200]) if part),
        "url": response.url,
        "url_type": "landing_page",
    }


def _append_text_evidence(samples: list[dict[str, Any]], *, text: str, source: str, spend: float, ref: str | None = None) -> None:
    if text:
        samples.append({"text": text, "source": source, "spend": round(spend, 2), "ref": ref})


def _scrape_content(
    urls: list[dict[str, Any]],
    *,
    content_limit: int,
    product_scraper: Callable[[str], dict[str, Any]] | None,
    generic_scraper: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if content_limit <= 0 or not urls:
        return []
    if product_scraper is None:
        from motata_cli.product.scraper import scrape_product as product_scraper

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for item in sorted(urls, key=lambda value: _fnum(value.get("spend")), reverse=True):
        url = str(item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        if len(candidates) >= content_limit:
            break
        candidates.append(item)

    def scrape_item(item: dict[str, Any]) -> dict[str, Any]:
        url = str(item.get("url") or "")
        try:
            payload = product_scraper(url)
        except Exception as exc:  # pragma: no cover - defensive around arbitrary external pages
            payload = {"error": str(exc)}
        if payload.get("error"):
            try:
                fallback_payload = (generic_scraper or _scrape_generic_landing_page)(url)
            except Exception:
                fallback_payload = None
            if fallback_payload and (fallback_payload.get("name") or fallback_payload.get("description")):
                fallback_payload["product_scrape_error"] = payload.get("error")
                payload = fallback_payload
        return {"url": url, "spend": round(_fnum(item.get("spend")), 2), "source": item.get("source"), "content": payload}

    if len(candidates) <= 1:
        return [scrape_item(item) for item in candidates]
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
        return list(executor.map(scrape_item, candidates))


def classify_user_types(samples: list[dict[str, Any]], *, include_evidence: bool = False) -> dict[str, Any]:
    raw_scores: dict[str, float] = {label: 0.0 for label in USER_TYPE_LABELS}
    evidence: dict[str, list[dict[str, Any]]] = {label: [] for label in USER_TYPE_LABELS}

    for sample in samples:
        text = str(sample.get("text") or "")
        spend = _fnum(sample.get("spend"))
        weighted_scores = _match_keywords(text)
        ref = sample.get("ref")
        if ref and str(ref).startswith(("http://", "https://")):
            for label, value in _domain_hints(str(ref)).items():
                weighted_scores[label] += value
        weight = _source_weight(spend)
        for label, score in weighted_scores.items():
            if label not in raw_scores or score <= 0:
                continue
            contribution = score * weight
            raw_scores[label] += contribution
            if include_evidence and len(evidence[label]) < 8:
                evidence[label].append(
                    {
                        "source": sample.get("source"),
                        "spend": round(spend, 2),
                        "ref": ref,
                        "matched_text": text[:220],
                        "score": round(contribution, 2),
                    }
                )

    non_agency = {label: score for label, score in raw_scores.items() if label != "代理商/多类型"}
    sorted_non_agency = sorted(non_agency.items(), key=lambda item: item[1], reverse=True)
    top_score = sorted_non_agency[0][1] if sorted_non_agency else 0.0
    meaningful = [(label, score) for label, score in sorted_non_agency if top_score and score >= top_score * 0.28 and score >= 8]
    distinct_refs = {str(sample.get("ref")) for sample in samples if sample.get("ref")}
    if len(meaningful) >= 3:
        raw_scores["代理商/多类型"] = max(raw_scores["代理商/多类型"], top_score * 0.82)
    elif len(meaningful) >= 2 and len(distinct_refs) >= 8:
        raw_scores["代理商/多类型"] = max(raw_scores["代理商/多类型"], top_score * 0.55)

    max_score = max(raw_scores.values()) if raw_scores else 0.0
    rows = []
    for label, score in sorted(raw_scores.items(), key=lambda item: item[1], reverse=True):
        index = round(score / max_score * 100, 1) if max_score > 0 else 0.0
        item = {"type": label, "index": index, "raw_score": round(score, 2)}
        if include_evidence:
            item["evidence"] = evidence.get(label, [])
        rows.append(item)
    return {"top_types": rows[:3], "all_types": rows}


def _campaign_ad_insight_rows(
    meta: Any,
    campaign_id: str,
    *,
    since: str | None,
    until: str | None,
    date_preset: str | None,
    ad_limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    params = {
        "level": "ad",
        "fields": DEFAULT_AD_INSIGHT_FIELDS,
        "limit": max(ad_limit, 1),
        "sort": "spend_descending",
        **_insights_params_base(),
        **_time_params(since=since, until=until, date_preset=date_preset),
    }
    try:
        rows = _paginate_insights(meta, f"{campaign_id}/insights", params=params)
    except Exception as exc:
        return [], str(exc)
    filtered = [row for row in rows if row.get("ad_id") and _fnum(row.get("spend")) > 0]
    filtered.sort(key=lambda item: _fnum(item.get("spend")), reverse=True)
    return filtered[:ad_limit], None


def _campaign_app_evidence(meta: Any, campaign_id: str, campaign_detail: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    adsets, adset_errors = _paginate_with_fallback(
        meta,
        f"{campaign_id}/adsets",
        [
            APP_ADSET_FIELDS,
            "id,name,campaign_id,promoted_object,optimization_goal,billing_event,status,effective_status",
        ],
    )
    for error in adset_errors[-1:] if adset_errors and not adsets else []:
        errors.append({"scope": "adsets", "error": error})

    pre_candidates = _app_candidates(("campaign", campaign_detail), *[("adset", item) for item in adsets])
    ads: list[dict[str, Any]] = []
    if not _has_app_evidence(pre_candidates):
        ads, ad_errors = _paginate_with_fallback(
            meta,
            f"{campaign_id}/ads",
            [
                APP_AD_FIELDS,
                "id,name,campaign_id,adset_id,status,effective_status,creative{id,name,link_url,object_url,template_url,url_tags}",
            ],
        )
        for error in ad_errors[-1:] if ad_errors and not ads else []:
            errors.append({"scope": "ads", "error": error})

    candidates = _app_candidates(("campaign", campaign_detail), *[("adset", item) for item in adsets], *[("ad", item) for item in ads[:200]])
    candidates = _augment_app_candidates_with_redirects(candidates)
    app_details = _app_details(meta, set(candidates["app_ids"]))
    candidates["app_details"] = app_details
    candidates["app_names"] = sorted({str(item.get("name")) for item in app_details if item.get("name")})
    return candidates, errors


def build_user_type_report(
    meta: Any,
    *,
    account_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    account_limit: int = 10,
    campaign_limit: int = 10,
    ad_limit: int = 5,
    content_limit: int = 60,
    include_evidence: bool = False,
    profile: str = "full",
    use_page_tokens: bool = True,
    product_scraper: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if profile == "batch":
        campaign_limit = min(campaign_limit, 5)
        ad_limit = min(ad_limit, 2)
        content_limit = min(content_limit, 20)
        use_page_tokens = False

    accounts, errors = _active_accounts(
        meta,
        account_id,
        since=since,
        until=until,
        date_preset=date_preset,
        account_limit=account_limit,
    )
    page_access_tokens: dict[str, str] = {}
    page_names: dict[str, str] = {}
    page_access_token_error = None
    if use_page_tokens:
        page_access_tokens, page_names, page_access_token_error = _list_page_access_tokens(meta)
    story_cache: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    content_urls: list[dict[str, Any]] = []
    account_rows: list[dict[str, Any]] = []
    campaign_rows: list[dict[str, Any]] = []
    campaign_detail_fetch_count = 0
    skipped_campaign_detail_fetch_count = 0
    landing_skipped_url_probe_count = 0

    if page_access_token_error:
        errors.append({"scope": "page_access_tokens", "error": page_access_token_error})

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
        _append_text_evidence(samples, text=f"account {account.get('name')}", source="account", spend=_fnum(account.get("spend")), ref=account_id_value)

        for campaign in campaigns:
            campaign_id = campaign["campaign_id"]
            spend = _fnum(campaign.get("spend"))
            campaign_detail = _campaign_detail_from_insight(campaign)
            if campaign_detail:
                skipped_campaign_detail_fetch_count += 1
            else:
                campaign_detail_fetch_count += 1
                try:
                    campaign_detail = meta.get(campaign_id, params={"fields": APP_CAMPAIGN_FIELDS})
                except Exception as exc:
                    campaign_detail = {"id": campaign_id, "name": campaign.get("campaign_name"), "_error": str(exc)}
                    errors.append({"account_id": account_id_value, "campaign_id": campaign_id, "scope": "campaign", "error": str(exc)})

            _append_text_evidence(
                samples,
                text=" ".join(str(value or "") for value in (campaign.get("campaign_name"), campaign_detail.get("objective"), campaign_detail.get("status"))),
                source="campaign",
                spend=spend,
                ref=campaign_id,
            )

            app_candidates, app_errors = _campaign_app_evidence(meta, campaign_id, campaign_detail)
            for error in app_errors:
                errors.append({"account_id": account_id_value, "campaign_id": campaign_id, **error})
            store_urls = list(app_candidates.get("store_urls") or [])
            app_names = list(app_candidates.get("app_names") or [])
            for app_detail in app_candidates.get("app_details") or []:
                _append_text_evidence(
                    samples,
                    text=" ".join(str(app_detail.get(key) or "") for key in ("name", "category", "namespace", "link")),
                    source="meta_app",
                    spend=spend,
                    ref=str(app_detail.get("id") or campaign_id),
                )
            for store_url in store_urls:
                _append_text_evidence(samples, text=store_url, source="store_url", spend=spend, ref=store_url)
                content_urls.append({"url": store_url, "spend": spend, "source": "store_url"})

            if _has_app_evidence(app_candidates):
                ad_rows, ad_error = [], None
                landing_skipped_url_probe_count += 1
            else:
                ad_rows, ad_error = _campaign_ad_insight_rows(
                    meta,
                    campaign_id,
                    since=since,
                    until=until,
                    date_preset=date_preset,
                    ad_limit=ad_limit,
                )
                if ad_error:
                    errors.append({"account_id": account_id_value, "campaign_id": campaign_id, "scope": "campaign_ads", "error": ad_error})
            landing_urls: list[str] = []
            resolved_by_ad = resolve_landing_urls(
                meta,
                [str(ad["ad_id"]) for ad in ad_rows if ad.get("ad_id")],
                page_access_tokens=page_access_tokens,
                page_names=page_names,
                story_cache=story_cache,
            )
            for ad in ad_rows:
                resolved = resolved_by_ad.get(str(ad["ad_id"])) or {}
                landing_url = resolved.get("url")
                if landing_url:
                    landing_urls.append(landing_url)
                    ad_spend = _fnum(ad.get("spend")) or spend
                    _append_text_evidence(samples, text=landing_url, source="landing_url", spend=ad_spend, ref=landing_url)
                    content_urls.append({"url": landing_url, "spend": ad_spend, "source": "landing_url"})
                elif resolved.get("story_probe_error"):
                    errors.append(
                        {
                            "account_id": account_id_value,
                            "campaign_id": campaign_id,
                            "ad_id": ad.get("ad_id"),
                            "scope": "landing_url",
                            "error": resolved.get("story_probe_error"),
                        }
                    )

            campaign_rows.append(
                {
                    **campaign,
                    "account_id": account_id_value,
                    "objective": campaign_detail.get("objective"),
                    "app_names": app_names,
                    "store_urls": store_urls,
                    "landing_urls": sorted(set(landing_urls)),
                    "ad_count_sampled": len(ad_rows),
                }
            )

    scraped = _scrape_content(content_urls, content_limit=content_limit, product_scraper=product_scraper)
    for item in scraped:
        content = item.get("content") or {}
        text_parts = [
            item.get("url"),
            content.get("url"),
            content.get("name"),
            content.get("description"),
            content.get("subtitle"),
            f"primary_genre {content.get('primary_genre') or ''}",
            f"genres {' '.join(str(value) for value in (content.get('genres') or []) if value)}",
            " ".join(f"genre {value}" for value in (content.get("genres") or []) if value),
            content.get("url_type"),
            content.get("price"),
        ]
        _append_text_evidence(
            samples,
            text=" ".join(str(part or "") for part in text_parts),
            source=f"scraped_{item.get('source')}",
            spend=_fnum(item.get("spend")),
            ref=str(item.get("url") or ""),
        )

    classification = classify_user_types(samples, include_evidence=include_evidence)
    return {
        "since": since,
        "until": until,
        "date_preset": None if since or until else date_preset,
        "account_limit": account_limit,
        "campaign_limit": campaign_limit,
        "ad_limit": ad_limit,
        "content_limit": content_limit,
        "profile": profile,
        "page_access_token_enabled": use_page_tokens,
        "accounts": account_rows,
        "campaign_count": len(campaign_rows),
        "campaign_detail_fetch_count": campaign_detail_fetch_count,
        "skipped_campaign_detail_fetch_count": skipped_campaign_detail_fetch_count,
        "landing_skipped_url_probe_count": landing_skipped_url_probe_count,
        "sample_count": len(samples),
        "scraped_content_count": len(scraped),
        "top_types": classification["top_types"],
        "all_types": classification["all_types"],
        "campaigns": sorted(campaign_rows, key=lambda value: _fnum(value.get("spend")), reverse=True),
        "scraped_content": scraped if include_evidence else [
            {"url": row.get("url"), "spend": row.get("spend"), "source": row.get("source"), "error": (row.get("content") or {}).get("error")}
            for row in scraped
        ],
        "errors": errors,
    }
