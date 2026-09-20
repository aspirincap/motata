from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse


LANDING_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")
META_ASSET_HOST_MARKERS = ("facebook.com", "fbcdn.net", "instagram.com", "scontent", "external-")
PURCHASE_ACTION_TYPES = ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase")
LEAD_ACTION_TYPES = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
DEFAULT_ACCOUNT_FIELDS = "id,account_id,name,account_status,currency"
DEFAULT_AD_INSIGHT_FIELDS = ",".join(
    [
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "adset_id",
        "adset_name",
        "ad_id",
        "ad_name",
        "objective",
        "optimization_goal",
        "spend",
        "impressions",
        "reach",
        "clicks",
        "inline_link_clicks",
        "actions",
        "action_values",
        "purchase_roas",
        "website_purchase_roas",
    ]
)
DEFAULT_ATTRIBUTION_PARAMS = {
    "use_unified_attribution_setting": "true",
    "action_report_time": "impression",
    "action_attribution_windows": json.dumps(["7d_click", "1d_view"], separators=(",", ":")),
}
META_INSIGHT_URL_BREAKDOWNS = (
    "link_url_asset",
)
DEFAULT_CREATIVE_FIELDS = ",".join(
    [
        "id",
        "name",
        "object_story_id",
        "effective_object_story_id",
        "object_story_spec",
        "asset_feed_spec",
        "url_tags",
        "link_url",
        "object_url",
        "template_url",
        "thumbnail_url",
        "instagram_permalink_url",
    ]
)
DEFAULT_AD_CONTEXT_FIELDS = ",".join(
    [
        "id",
        "name",
        f"creative{{{DEFAULT_CREATIVE_FIELDS}}}",
        "adset{id,name,destination_type,promoted_object}",
        "tracking_specs",
        "conversion_specs",
    ]
)
DEFAULT_STORY_FIELDS = ",".join(
    [
        "id",
        "permalink_url",
        "call_to_action",
        "message",
        "properties",
        "attachments{url,unshimmed_url,target,type,title,description,media}",
    ]
)
LEAN_AD_INSIGHT_FIELDS = ",".join(
    [
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "adset_id",
        "adset_name",
        "ad_id",
        "ad_name",
        "objective",
        "optimization_goal",
        "spend",
        "impressions",
        "reach",
        "clicks",
        "inline_link_clicks",
    ]
)


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _fnum(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_account_id(account_id: str) -> str:
    account_id = str(account_id).strip()
    return account_id[4:] if account_id.startswith("act_") else account_id


def _ad_account_path(account_id: str) -> str:
    return f"act_{_normalize_account_id(account_id)}"


def _is_landing_url(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    host = urlparse(url).netloc.lower()
    return not any(marker in host for marker in META_ASSET_HOST_MARKERS)


def _looks_like_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(str(url))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def canonicalize_landing_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip().rstrip(").,;]"))
    path = unquote(parsed.path).rstrip("/") or "/"

    if "/products/" in path:
        query = ""
    else:
        kept_params = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered.startswith("utm") or lowered in {"fbclid", "gclid"}:
                continue
            kept_params.append((key, value))
        query = urlencode(kept_params)

    return urlunparse(("https", parsed.netloc.lower(), path, "", query, ""))


def _collect_landing_urls(value: Any, found: list[tuple[str, str]], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(child, str):
                candidates: list[str] = []
                lowered_key = key.lower()
                if any(token in lowered_key for token in ("url", "link", "website")) or child.startswith(
                    ("http://", "https://")
                ):
                    candidates.extend(LANDING_URL_RE.findall(child))
                    candidates.append(child)
                for candidate in candidates:
                    normalized = candidate.strip().rstrip(").,;]")
                    if _is_landing_url(normalized):
                        found.append((canonicalize_landing_url(normalized), child_path))
            else:
                _collect_landing_urls(child, found, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_landing_urls(child, found, f"{path}[{index}]")


def extract_landing_url(creative: dict[str, Any]) -> tuple[str | None, str | None]:
    found: list[tuple[str, str]] = []
    _collect_landing_urls(creative, found)
    if not found:
        return None, None

    preferred_sources = (
        "call_to_action.value.link",
        "link_data.link",
        "template_url",
        "link_url",
        "object_url",
        "link_caption",
    )
    for source_marker in preferred_sources:
        for url, source in found:
            if source_marker in source:
                return url, source
    return found[0]


def _story_reference_from_object_story_id(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str) or "_" not in value:
        return None
    page_id, post_id = value.split("_", 1)
    if not page_id or not post_id:
        return None
    return {
        "page_id": page_id,
        "post_id": post_id,
        "facebook_post": f"Facebook.com/{page_id}_{post_id}",
    }


def _listish(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _tracking_post_reference(ad_context: dict[str, Any]) -> dict[str, Any]:
    page_ids: set[str] = set()
    post_ids: set[str] = set()
    for spec in ad_context.get("tracking_specs") or []:
        for page_id in _listish(spec.get("page") or spec.get("post.wall")):
            page_ids.add(page_id)
        for post_id in _listish(spec.get("post")):
            post_ids.add(post_id)

    reference: dict[str, Any] = {}
    if page_ids:
        reference["tracking_page_ids"] = sorted(page_ids)
    if post_ids:
        reference["tracking_post_ids"] = sorted(post_ids)
    if len(page_ids) == 1 and len(post_ids) == 1:
        page_id = next(iter(page_ids))
        post_id = next(iter(post_ids))
        reference.update(
            {
                "page_id": page_id,
                "post_id": post_id,
                "facebook_post": f"Facebook.com/{page_id}_{post_id}",
            }
        )
    return reference


def _facebook_story_url(story_reference: dict[str, Any]) -> str | None:
    story_id = story_reference.get("object_story_id")
    if isinstance(story_id, str) and "_" in story_id:
        return f"https://www.facebook.com/{story_id}"
    page_id = story_reference.get("page_id")
    post_id = story_reference.get("post_id")
    if page_id and post_id:
        return f"https://www.facebook.com/{page_id}_{post_id}"
    facebook_post = story_reference.get("facebook_post")
    if isinstance(facebook_post, str) and facebook_post:
        return facebook_post if facebook_post.startswith(("http://", "https://")) else f"https://www.{facebook_post}"
    return None


def _first_url_from_values(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            candidates = [*LANDING_URL_RE.findall(value), value]
            for candidate in candidates:
                url = candidate.strip().rstrip(").,;]")
                if _looks_like_url(url):
                    return url
    return None


def _collect_preview_image_urls(value: Any, found: list[tuple[str, str]], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            lowered_key = key.lower()
            if isinstance(child, str):
                if any(token in lowered_key for token in ("image", "thumbnail", "picture", "cover", "poster", "src", "uri")):
                    url = _first_url_from_values(child)
                    if url:
                        found.append((url, child_path))
            else:
                _collect_preview_image_urls(child, found, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_preview_image_urls(child, found, f"{path}[{index}]")


def _creative_preview_info(
    creative: dict[str, Any],
    ad_context: dict[str, Any],
    story_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    oss = creative.get("object_story_spec") or {}
    video_data = oss.get("video_data") or {}
    photo_data = oss.get("photo_data") or {}
    link_data = oss.get("link_data") or {}
    template_data = oss.get("template_data") or {}
    image_candidates: list[tuple[str, str]] = []
    for source, value in (
        ("object_story_spec.video_data.image_url", video_data.get("image_url")),
        ("object_story_spec.photo_data.image_url", photo_data.get("image_url")),
        ("object_story_spec.photo_data.call_to_action.value.image_url", (((photo_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))),
        ("thumbnail_url", creative.get("thumbnail_url")),
        ("image_url", creative.get("image_url")),
        ("object_story_spec.template_data.call_to_action.value.image_url", (((template_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))),
        ("object_story_spec.link_data.call_to_action.value.image_url", (((link_data.get("call_to_action") or {}).get("value") or {}).get("image_url"))),
        ("object_story_spec.link_data.image_url", link_data.get("image_url")),
    ):
        url = _first_url_from_values(value)
        if url:
            image_candidates.append((url, source))
    _collect_preview_image_urls(creative.get("asset_feed_spec") or {}, image_candidates, "asset_feed_spec")
    if story_payload:
        _collect_preview_image_urls(story_payload, image_candidates, "story")
    deduped_assets: list[dict[str, str]] = []
    seen_assets: set[tuple[str, str]] = set()
    for url, source in image_candidates:
        key = (url, source)
        if key in seen_assets:
            continue
        seen_assets.add(key)
        deduped_assets.append({"url": url, "source": source, "media_type": "image"})

    preview_image_url = image_candidates[0][0] if image_candidates else None
    preview_image_source = image_candidates[0][1] if image_candidates else None
    story_reference = _story_reference(creative, ad_context)
    preview_url = _first_url_from_values(
        creative.get("instagram_permalink_url"),
        (story_payload or {}).get("permalink_url"),
        _facebook_story_url(story_reference),
        preview_image_url,
    )
    if preview_url == creative.get("instagram_permalink_url"):
        preview_url_source = "instagram_permalink_url"
    elif story_payload and preview_url == story_payload.get("permalink_url"):
        preview_url_source = "story.permalink_url"
    elif preview_url == _facebook_story_url(story_reference):
        preview_url_source = "object_story_id"
    elif preview_url == preview_image_url:
        preview_url_source = preview_image_source
    else:
        preview_url_source = None

    return {
        "preview_image_url": preview_image_url,
        "preview_url": preview_url,
        "preview_url_source": preview_url_source,
        "preview_image_source": preview_image_source,
        "preview_media_type": "image" if preview_image_url else ("post" if preview_url else None),
        "creative_asset_urls": deduped_assets,
        "preview_status": "available" if preview_image_url or preview_url else "unavailable",
    }


def _creative_shape(creative: dict[str, Any]) -> str:
    parts: list[str] = []
    if creative.get("object_story_id") or creative.get("effective_object_story_id"):
        parts.append("story")
    if creative.get("object_story_spec"):
        parts.append("object_story_spec")
    if creative.get("asset_feed_spec"):
        parts.append("asset_feed_spec")
    if creative.get("link_url"):
        parts.append("link_url")
    if creative.get("object_url"):
        parts.append("object_url")
    if creative.get("template_url"):
        parts.append("template_url")
    if creative.get("url_tags"):
        parts.append("url_tags")
    return "+".join(parts) if parts else "minimal_creative"


def _story_reference(creative: dict[str, Any], ad_context: dict[str, Any]) -> dict[str, Any]:
    story_id = creative.get("effective_object_story_id") or creative.get("object_story_id")
    reference = _story_reference_from_object_story_id(story_id)
    if reference:
        reference["object_story_id"] = story_id
        return reference

    reference = _tracking_post_reference(ad_context)
    if reference:
        return reference
    if isinstance(story_id, str) and story_id:
        return {"object_story_id": story_id}
    return {}


def _get_story_payload_with_token(
    meta: Any,
    story_id: str,
    page_access_token: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    params = {"fields": DEFAULT_STORY_FIELDS}
    from motata_cli.transport.gateway import GatewayPageRef
    if isinstance(page_access_token, GatewayPageRef):
        try:
            return meta.get_with_page(page_access_token,story_id,params=params),None
        except Exception as exc:
            return {},str(exc)
    if page_access_token:
        params["access_token"] = page_access_token
    try:
        return meta.get(story_id, params=params), None
    except Exception as exc:  # pragma: no cover - depends on page permissions
        return {}, str(exc)


def _action_value(actions: list[dict[str, Any]] | None, action_types: tuple[str, ...]) -> float:
    # Meta can emit the same conversion through multiple action_type aliases.
    # Pick the largest alias value for a conversion family instead of summing duplicates.
    values = [_fnum(action.get("value")) for action in (actions or []) if action.get("action_type") in action_types]
    return max(values) if values else 0.0


def _purchase_roas_value(purchase_roas: list[dict[str, Any]] | None) -> float:
    values = [_fnum(item.get("value")) for item in (purchase_roas or [])]
    return max(values) if values else 0.0


def _purchase_revenue_value(action_values: list[dict[str, Any]] | None) -> float:
    return _action_value(action_values, PURCHASE_ACTION_TYPES)


def _time_params(*, since: str | None, until: str | None, date_preset: str | None) -> dict[str, Any]:
    if since or until:
        value = {key: val for key, val in {"since": since, "until": until}.items() if val}
        return {"time_range": json.dumps(value, separators=(",", ":"))}
    return {"date_preset": date_preset or "last_14d"}


def _insights_params_base(*, attribution: bool = True) -> dict[str, Any]:
    return dict(DEFAULT_ATTRIBUTION_PARAMS) if attribution else {}


def _paginate_insights(
    meta: Any,
    path: str,
    *,
    params: dict[str, Any],
    prefer_async: bool = False,
    auto_async: bool = True,
) -> list[dict[str, Any]]:
    paginate_insights = getattr(meta, "paginate_insights", None)
    if callable(paginate_insights):
        return paginate_insights(path, params=params, prefer_async=prefer_async, auto_async=auto_async)
    return meta.paginate(path, params=params)


def _list_accounts(meta: Any, account_id: str | None) -> list[dict[str, Any]]:
    if account_id:
        payload = meta.get(
            _ad_account_path(account_id),
            params={"fields": DEFAULT_ACCOUNT_FIELDS},
        )
        return [payload]
    return meta.paginate("me/adaccounts", params={"fields": DEFAULT_ACCOUNT_FIELDS, "limit": 100})


def _list_account_candidates(meta: Any, account_id: str | None, *, limit: int = 50) -> list[dict[str, Any]]:
    if account_id:
        return _list_accounts(meta, account_id)
    payload = meta.get("me/adaccounts", params={"fields": DEFAULT_ACCOUNT_FIELDS, "limit": max(limit, 1)})
    return payload.get("data") or []


def _account_spend_row(
    meta: Any,
    account: dict[str, Any],
    *,
    since: str | None,
    until: str | None,
    date_preset: str | None,
) -> dict[str, Any]:
    response = meta.get(
        f"{_ad_account_path(account.get('account_id') or account.get('id'))}/insights",
        params={
            "level": "account",
            "fields": "spend,impressions,clicks",
            "limit": 1,
            **_time_params(since=since, until=until, date_preset=date_preset),
        },
    )
    row = (response.get("data") or [{}])[0]
    return {
        **account,
        "spend": round(_fnum(row.get("spend")), 2),
        "impressions": int(_fnum(row.get("impressions"))),
        "clicks": int(_fnum(row.get("clicks"))),
    }


def discover_recent_spend_accounts(
    meta: Any,
    account_id: str | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    account_limit: int | None = 10,
    discovery_limit: int = 50,
    max_workers: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = _list_account_candidates(meta, account_id, limit=max(discovery_limit, account_limit or 0, 1))
    active: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not candidates:
        return active, errors

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(candidates)))) as executor:
        futures = {
            executor.submit(_account_spend_row, meta, account, since=since, until=until, date_preset=date_preset): account
            for account in candidates
        }
        for future in as_completed(futures):
            account = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                errors.append(
                    {
                        "account_id": account.get("account_id") or account.get("id"),
                        "scope": "account_insights",
                        "error": str(exc),
                    }
                )
                continue
            if row["spend"] > 0:
                active.append(row)
    active.sort(key=lambda item: item["spend"], reverse=True)
    if account_limit and account_limit > 0:
        active = active[:account_limit]
    return active, errors


def _list_page_access_tokens(meta: Any) -> tuple[dict[str, str], dict[str, str], str | None]:
    if getattr(meta, 'gateway', None) is not None:
        from motata_cli.transport.gateway import GatewayPageRef
        try:
            pages=meta.list_page_credentials()
            refs={str(p['id']):GatewayPageRef(p['page_credential_ref'],p['account_id'],str(p['id']))
                  for p in pages if p.get('page_credential_ref')}
            return refs,{str(p['id']):str(p.get('name') or '') for p in pages},None
        except Exception as exc:
            return {},{},str(exc)
    try:
        pages = meta.paginate(
            "me/accounts",
            params={"fields": "id,name,access_token,tasks", "limit": 100},
        )
    except Exception as exc:  # pragma: no cover - depends on token permissions
        return {}, {}, str(exc)

    tokens: dict[str, str] = {}
    names: dict[str, str] = {}
    for page in pages:
        page_id = str(page.get("id") or "")
        token = page.get("access_token")
        if page_id and isinstance(token, str) and token:
            tokens[page_id] = token
        if page_id and page.get("name"):
            names[page_id] = str(page.get("name"))
    return tokens, names, None


def _get_ad_creative(meta: Any, ad_id: str) -> tuple[dict[str, Any], str | None]:
    try:
        payload = meta.get(ad_id, params={"fields": f"id,name,creative{{{DEFAULT_CREATIVE_FIELDS}}}"})
        return payload.get("creative") or {}, None
    except Exception as exc:  # pragma: no cover - fallback path depends on Meta field permissions
        try:
            payload = meta.get(
                ad_id,
                params={"fields": "id,name,creative{id,name,object_story_spec,asset_feed_spec,url_tags}"},
            )
            return payload.get("creative") or {}, None
        except Exception as fallback_exc:
            return {}, str(fallback_exc or exc)


def _get_ad_context(meta: Any, ad_id: str) -> tuple[dict[str, Any], str | None]:
    try:
        return meta.get(ad_id, params={"fields": DEFAULT_AD_CONTEXT_FIELDS}), None
    except Exception as exc:  # pragma: no cover - fallback path depends on Meta field permissions
        creative, creative_error = _get_ad_creative(meta, ad_id)
        if creative:
            return {"id": ad_id, "creative": creative}, creative_error
        return {}, str(creative_error or exc)


def _get_ad_contexts(meta: Any, ad_ids: list[str], *, batch_size: int = 50) -> dict[str, tuple[dict[str, Any], str | None]]:
    results: dict[str, tuple[dict[str, Any], str | None]] = {}
    unique_ad_ids = list(dict.fromkeys(str(ad_id) for ad_id in ad_ids if ad_id))
    for batch in _chunked(unique_ad_ids, batch_size):
        missing = set(batch)
        try:
            payload = meta.get("", params={"ids": ",".join(batch), "fields": DEFAULT_AD_CONTEXT_FIELDS})
            for ad_id in batch:
                item = payload.get(ad_id) if isinstance(payload, dict) else None
                if isinstance(item, dict) and "error" not in item:
                    results[ad_id] = (item, None)
                    missing.discard(ad_id)
        except Exception:
            missing = set(batch)
        for ad_id in sorted(missing):
            results[ad_id] = _get_ad_context(meta, ad_id)
    return results


def _resolve_landing_url_from_context(
    meta: Any,
    ad_context: dict[str, Any],
    *,
    context_error: str | None = None,
    page_access_tokens: dict[str, str] | None = None,
    page_names: dict[str, str] | None = None,
    story_cache: dict[str, dict[str, Any]] | None = None,
    creative_url_cache: dict[str, tuple[str | None, str | None]] | None = None,
) -> dict[str, Any]:
    creative = ad_context.get("creative") or {}
    creative_id = str(creative.get("id") or "")
    if creative_id and creative_url_cache is not None and creative_id in creative_url_cache:
        landing_url, url_source = creative_url_cache[creative_id]
    else:
        landing_url, url_source = extract_landing_url(creative)
        if creative_id and creative_url_cache is not None:
            creative_url_cache[creative_id] = (landing_url, url_source)
    story_probe_error = None
    story_payload: dict[str, Any] = {}

    if not landing_url:
        story_reference = _story_reference(creative, ad_context)
        story_id = story_reference.get("object_story_id")
        page_id = story_reference.get("page_id")
        page_access_token = (page_access_tokens or {}).get(str(page_id)) if page_id else None
        if isinstance(story_id, str) and story_id:
            cached_story = (story_cache or {}).get(story_id)
            if cached_story is None:
                if page_access_token:
                    story_payload, story_probe_error = _get_story_payload_with_token(
                        meta,
                        story_id,
                        page_access_token=page_access_token,
                    )
                else:
                    story_payload = {}
                    story_probe_error = "story lookup requires page access token from /me/accounts"
                story_url, story_source = extract_landing_url(story_payload)
                cached_story = {
                    "url": story_url,
                    "url_source": story_source,
                    "story_payload": story_payload,
                    "story_probe_error": story_probe_error,
                    "used_page_token": bool(page_access_token),
                }
                if story_cache is not None:
                    story_cache[story_id] = cached_story
            story_url = cached_story.get("url")
            story_source = cached_story.get("url_source")
            story_probe_error = cached_story.get("story_probe_error")
            story_payload = cached_story.get("story_payload") or {}
            if story_url:
                landing_url = story_url
                url_source = f"story.{story_source}"

    if not landing_url:
        context_url, context_source = extract_landing_url(ad_context)
        if context_url:
            landing_url = context_url
            url_source = f"ad.{context_source}"

    return {
        "url": landing_url,
        "url_source": url_source,
        "ad_context": ad_context,
        "creative": creative,
        "story_payload": story_payload,
        "page_names": page_names or {},
        "creative_error": context_error,
        "story_probe_error": story_probe_error,
    }


def _resolve_landing_url(
    meta: Any,
    ad_id: str,
    *,
    page_access_tokens: dict[str, str] | None = None,
    page_names: dict[str, str] | None = None,
    story_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ad_context, context_error = _get_ad_context(meta, ad_id)
    return _resolve_landing_url_from_context(
        meta,
        ad_context,
        context_error=context_error,
        page_access_tokens=page_access_tokens,
        page_names=page_names,
        story_cache=story_cache,
    )


def resolve_landing_urls(
    meta: Any,
    ad_ids: list[str],
    *,
    page_access_tokens: dict[str, str] | None = None,
    page_names: dict[str, str] | None = None,
    story_cache: dict[str, dict[str, Any]] | None = None,
    max_ad_context_fetches: int | None = None,
) -> dict[str, dict[str, Any]]:
    unique_ad_ids = list(dict.fromkeys(str(value) for value in ad_ids if value))
    if max_ad_context_fetches is not None and max_ad_context_fetches >= 0:
        fetch_ad_ids = unique_ad_ids[:max_ad_context_fetches]
        skipped_ad_ids = set(unique_ad_ids[max_ad_context_fetches:])
    else:
        fetch_ad_ids = unique_ad_ids
        skipped_ad_ids = set()
    contexts = _get_ad_contexts(meta, fetch_ad_ids)
    creative_url_cache: dict[str, tuple[str | None, str | None]] = {}
    resolved: dict[str, dict[str, Any]] = {}
    for ad_id in unique_ad_ids:
        if ad_id in skipped_ad_ids:
            resolved[ad_id] = {
                "url": None,
                "url_source": None,
                "ad_context": {"id": ad_id},
                "creative": {},
                "story_payload": {},
                "page_names": page_names or {},
                "creative_error": "ad context fetch skipped by batch profile",
                "story_probe_error": None,
            }
            continue
        ad_context, context_error = contexts.get(ad_id, ({}, "ad context not returned"))
        resolved[ad_id] = _resolve_landing_url_from_context(
            meta,
            ad_context,
            context_error=context_error,
            page_access_tokens=page_access_tokens,
            page_names=page_names,
            story_cache=story_cache,
            creative_url_cache=creative_url_cache,
        )
    return resolved


def _unresolved_ad_structure(row: dict[str, Any], resolved: dict[str, Any], spend: float) -> dict[str, Any]:
    ad_context = resolved.get("ad_context") or {}
    creative = resolved.get("creative") or {}
    preview = _creative_preview_info(creative, ad_context, resolved.get("story_payload") or {})
    adset = ad_context.get("adset") or {}
    promoted_object = adset.get("promoted_object") or {}
    story_reference = _story_reference(creative, ad_context)
    page_id = story_reference.get("page_id")
    page_names = resolved.get("page_names") or {}
    return {
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name"),
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "spend": round(spend, 2),
        "creative_id": creative.get("id"),
        "creative_name": creative.get("name"),
        "creative_shape": _creative_shape(creative),
        "creative_keys": sorted(creative.keys()),
        "object_story_id": creative.get("object_story_id"),
        "effective_object_story_id": creative.get("effective_object_story_id"),
        "instagram_permalink_url": creative.get("instagram_permalink_url"),
        **preview,
        "page_name": page_names.get(page_id) if page_id else None,
        "adset_id": adset.get("id"),
        "adset_name": adset.get("name"),
        "destination_type": adset.get("destination_type"),
        "promoted_object": promoted_object or None,
        "promoted_object_keys": sorted(promoted_object.keys()),
        **story_reference,
        **_tracking_post_reference(ad_context),
        "creative_error": resolved.get("creative_error"),
        "story_probe_error": resolved.get("story_probe_error"),
    }


def _final_preview_targets(rows: list[dict[str, Any]], no_url: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for key in ("top_ads", "ads"):
            for ad in row.get(key) or []:
                ad_id = str(ad.get("ad_id") or "").strip()
                if ad_id and not ad.get("creative_id") and ad.get("preview_status") != "available":
                    targets.setdefault(ad_id, []).append(ad)
    for ad in no_url:
        ad_id = str(ad.get("ad_id") or "").strip()
        if ad_id and not ad.get("creative_id") and ad.get("preview_status") != "available":
            targets.setdefault(ad_id, []).append(ad)
    return targets


def _enrich_final_ad_previews(
    meta: Any,
    rows: list[dict[str, Any]],
    no_url: list[dict[str, Any]],
    *,
    include_previews: bool,
    max_ad_context_fetches: int | None,
) -> int:
    if not include_previews or max_ad_context_fetches == 0:
        return 0
    targets = _final_preview_targets(rows, no_url)
    if not targets:
        return 0
    ad_ids = list(targets)
    if max_ad_context_fetches is not None and max_ad_context_fetches > 0:
        ad_ids = ad_ids[:max_ad_context_fetches]
    contexts = _get_ad_contexts(meta, ad_ids)
    for ad_id, (ad_context, context_error) in contexts.items():
        if not ad_context:
            continue
        creative = ad_context.get("creative") or {}
        preview = _creative_preview_info(creative, ad_context, {})
        for ad in targets.get(ad_id) or []:
            ad.update(
                {
                    "creative_id": creative.get("id"),
                    "creative_name": creative.get("name"),
                    "creative_error": context_error,
                    **preview,
                }
            )
    return len(contexts)


def _fetch_account_insights(
    meta: Any,
    account: dict[str, Any],
    insights_params: dict[str, Any],
    *,
    prefer_async: bool = False,
    auto_async: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        return (
            _paginate_insights(
                meta,
                f"{account['id']}/insights",
                params=insights_params,
                prefer_async=prefer_async,
                auto_async=auto_async,
            ),
            None,
        )
    except Exception as exc:
        message = str(exc)
        if "reduce the amount of data" not in message:
            return [], {"level": "error", "error": message}

    lean_params = {**insights_params, "fields": LEAN_AD_INSIGHT_FIELDS}
    try:
        rows = _paginate_insights(
            meta,
            f"{account['id']}/insights",
            params=lean_params,
            prefer_async=prefer_async,
            auto_async=auto_async,
        )
        return rows, {
            "level": "warning",
            "error": message,
            "fallback": "retried with lean ad insight fields",
        }
    except Exception as fallback_exc:
        return [], {"level": "error", "error": str(fallback_exc)}


def _extract_insight_landing_url(row: dict[str, Any]) -> tuple[str | None, str | None]:
    for field in META_INSIGHT_URL_BREAKDOWNS:
        value = row.get(field)
        if value in (None, ""):
            continue
        found: list[tuple[str, str]] = []
        _collect_landing_urls({field: value}, found)
        if found:
            return found[0][0], f"insights.{found[0][1]}"
        if isinstance(value, str) and value.startswith(("http://", "https://")) and _is_landing_url(value):
            return canonicalize_landing_url(value), f"insights.{field}"
    return None, None


def _fetch_insight_url_map(
    meta: Any,
    account: dict[str, Any],
    insights_params: dict[str, Any],
    *,
    prefer_async: bool = False,
    auto_async: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    url_by_ad: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    base_params = {
        **insights_params,
        "fields": LEAN_AD_INSIGHT_FIELDS,
        "limit": insights_params.get("limit") or 5000,
    }
    for breakdown in META_INSIGHT_URL_BREAKDOWNS:
        params = {**base_params, "breakdowns": breakdown}
        try:
            rows = _paginate_insights(
                meta,
                f"{account['id']}/insights",
                params=params,
                prefer_async=prefer_async,
                auto_async=False if not prefer_async else auto_async,
            )
        except Exception as exc:
            warnings.append(
                {
                    "account_id": account.get("account_id"),
                    "account_name": account.get("name"),
                    "breakdown": breakdown,
                    "error": str(exc),
                }
            )
            continue
        for row in rows:
            ad_id = str(row.get("ad_id") or "").strip()
            if not ad_id or _fnum(row.get("spend")) <= 0:
                continue
            landing_url, url_source = _extract_insight_landing_url(row)
            if not landing_url:
                continue
            existing = url_by_ad.get(ad_id)
            if existing and _fnum(existing.get("spend")) >= _fnum(row.get("spend")):
                continue
            url_by_ad[ad_id] = {
                "url": landing_url,
                "url_source": url_source,
                "spend": _fnum(row.get("spend")),
                "insight_breakdown": breakdown,
                "ad_context": {"id": ad_id},
                "creative": {},
                "page_names": {},
                "creative_error": None,
                "story_probe_error": None,
            }
    return url_by_ad, warnings


def _product_info(
    url: str,
    *,
    enrich_product: bool,
    product_scraper: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if not enrich_product:
        return {
            "product_name": urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url,
            "price": None,
            "product_error": None,
        }

    if product_scraper is None:
        from motata_cli.product.scraper import scrape_product as product_scraper

    try:
        product = product_scraper(url)
    except Exception as exc:  # pragma: no cover - defensive around external pages
        product = {"error": str(exc)}

    return {
        "product_name": html.unescape(product.get("name") or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url),
        "price": product.get("price"),
        "product_error": product.get("error"),
    }


def _enrich_product_rows(
    rows: list[dict[str, Any]],
    *,
    enrich_product: bool,
    product_limit: int,
    product_scraper: Callable[[str], dict[str, Any]] | None,
) -> None:
    enrich_targets: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(rows, start=1):
        if product_limit <= 0 or index <= product_limit:
            enrich_targets.append((index - 1, row))
        else:
            row.update(
                {
                    "product_name": urlparse(row["url"]).path.rstrip("/").rsplit("/", 1)[-1] or row["url"],
                    "price": None,
                    "product_error": "Product enrichment skipped by product_limit",
                }
            )

    def enrich(row: dict[str, Any]) -> dict[str, Any]:
        return _product_info(row["url"], enrich_product=enrich_product, product_scraper=product_scraper)

    if len(enrich_targets) <= 1:
        for _, row in enrich_targets:
            row.update(enrich(row))
        return
    with ThreadPoolExecutor(max_workers=min(8, len(enrich_targets))) as executor:
        results = list(executor.map(lambda item: enrich(item[1]), enrich_targets))
    for (row_index, _), product in zip(enrich_targets, results):
        rows[row_index].update(product)


def build_landing_page_report(
    meta: Any,
    *,
    account_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    account_limit: int | None = 10,
    top: int | None = None,
    insight_limit: int = 5000,
    enrich_product: bool = False,
    product_limit: int = 50,
    include_ads: bool = False,
    include_previews: bool = True,
    use_page_tokens: bool = True,
    async_insights: bool = False,
    auto_async_insights: bool = True,
    profile: str = "full",
    max_ad_context_fetches: int | None = None,
    product_scraper: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if profile == "batch":
        use_page_tokens = False
        insight_limit = min(insight_limit, 500)
        max_ad_context_fetches = 0 if max_ad_context_fetches is None else max_ad_context_fetches

    accounts, account_discovery_errors = discover_recent_spend_accounts(
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
    story_cache: dict[str, dict[str, Any]] = {}
    if use_page_tokens:
        page_access_tokens, page_names, page_access_token_error = _list_page_access_tokens(meta)
    insights_params = {
        **_insights_params_base(),
        **_time_params(since=since, until=until, date_preset=date_preset),
        "level": "ad",
        "fields": DEFAULT_AD_INSIGHT_FIELDS,
        "limit": insight_limit,
    }

    ads: list[dict[str, Any]] = []
    account_errors: list[dict[str, Any]] = list(account_discovery_errors)
    account_warnings: list[dict[str, Any]] = []
    for account in accounts:
        rows, issue = _fetch_account_insights(
            meta,
            account,
            insights_params,
            prefer_async=async_insights,
            auto_async=auto_async_insights,
        )
        if issue:
            target = account_warnings if issue.get("level") == "warning" else account_errors
            target.append(
                {
                    "account_id": account.get("account_id"),
                    "account_name": account.get("name"),
                    **{key: value for key, value in issue.items() if key != "level"},
                }
            )
        if issue and issue.get("level") == "error":
            continue
        for row in rows:
            if _fnum(row.get("spend")) > 0 and row.get("ad_id"):
                ads.append(row)

    insight_url_by_ad: dict[str, dict[str, Any]] = {}
    insight_url_warnings: list[dict[str, Any]] = []
    for account in accounts:
        url_map, warnings = _fetch_insight_url_map(
            meta,
            account,
            insights_params,
            prefer_async=async_insights,
            auto_async=auto_async_insights,
        )
        insight_url_by_ad.update(url_map)
        insight_url_warnings.extend(warnings)
    account_warnings.extend(
        {**warning, "scope": "insight_url_breakdown"} for warning in insight_url_warnings
    )

    groups: dict[str, dict[str, Any]] = {}
    no_url: list[dict[str, Any]] = []
    ad_ids = [str(row["ad_id"]) for row in ads if row.get("ad_id")]
    direct_ad_ids = {ad_id for ad_id in ad_ids if ad_id in insight_url_by_ad}
    ad_spend_by_id = {str(row.get("ad_id")): _fnum(row.get("spend")) for row in ads if row.get("ad_id")}
    remaining_ad_ids = sorted(
        [ad_id for ad_id in ad_ids if ad_id not in direct_ad_ids],
        key=lambda ad_id: ad_spend_by_id.get(ad_id, 0.0),
        reverse=True,
    )
    resolved_by_ad = dict(insight_url_by_ad)
    resolved_by_ad.update(
        resolve_landing_urls(
            meta,
            remaining_ad_ids,
            page_access_tokens=page_access_tokens,
            page_names=page_names,
            story_cache=story_cache,
            max_ad_context_fetches=max_ad_context_fetches,
        )
    )

    for row in ads:
        resolved = resolved_by_ad.get(str(row["ad_id"])) or {}
        landing_url = resolved.get("url")
        url_source = resolved.get("url_source")
        spend = _fnum(row.get("spend"))
        if not landing_url:
            no_url.append(_unresolved_ad_structure(row, resolved, spend))
            continue

        group = groups.setdefault(
            landing_url,
            {
                "url": landing_url,
                "url_source": url_source,
                "accounts": set(),
                "campaigns": set(),
                "ad_count": 0,
                "spend": 0.0,
                "impressions": 0,
                "reach": 0,
                "clicks": 0,
                "link_clicks": 0,
                "purchases": 0.0,
                "revenue": 0.0,
                "leads": 0.0,
                "roas_weight": 0.0,
                "ads": [],
            },
        )
        purchases = _action_value(row.get("actions"), PURCHASE_ACTION_TYPES)
        group["accounts"].add(row.get("account_name") or row.get("account_id"))
        group["campaigns"].add(row.get("campaign_name") or "")
        group["ad_count"] += 1
        group["spend"] += spend
        group["impressions"] += int(_fnum(row.get("impressions")))
        group["reach"] += int(_fnum(row.get("reach")))
        group["clicks"] += int(_fnum(row.get("clicks")))
        group["link_clicks"] += int(_fnum(row.get("inline_link_clicks")))
        group["purchases"] += purchases
        group["revenue"] += _purchase_revenue_value(row.get("action_values"))
        group["leads"] += _action_value(row.get("actions"), LEAD_ACTION_TYPES)
        group["roas_weight"] += max(
            _purchase_roas_value(row.get("website_purchase_roas")),
            _purchase_roas_value(row.get("purchase_roas")),
        ) * spend
        preview = _creative_preview_info(
            resolved.get("creative") or {},
            resolved.get("ad_context") or {},
            resolved.get("story_payload") or {},
        )
        group["ads"].append(
            {
                "ad_id": row.get("ad_id"),
                "ad_name": row.get("ad_name"),
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
                "adset_id": row.get("adset_id"),
                "adset_name": row.get("adset_name"),
                "objective": row.get("objective"),
                "optimization_goal": row.get("optimization_goal"),
                "creative_id": (resolved.get("creative") or {}).get("id"),
                "creative_name": (resolved.get("creative") or {}).get("name"),
                **preview,
                "spend": round(spend, 2),
                "link_clicks": int(_fnum(row.get("inline_link_clicks"))),
                "purchases": purchases,
            }
        )

    rows: list[dict[str, Any]] = []
    for group in groups.values():
        spend = group["spend"]
        impressions = group["impressions"]
        clicks = group["clicks"]
        link_clicks = group["link_clicks"]
        purchases = group["purchases"]
        row = {
            "url": group["url"],
            "url_source": group["url_source"],
            "accounts": sorted(account for account in group["accounts"] if account),
            "ad_count": group["ad_count"],
            "campaign_count": len([campaign for campaign in group["campaigns"] if campaign]),
            "spend": round(spend, 2),
            "impressions": impressions,
            "reach": group["reach"],
            "clicks": clicks,
            "link_clicks": link_clicks,
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0,
            "link_ctr": round(link_clicks / impressions * 100, 2) if impressions else 0,
            "cpc": round(spend / clicks, 2) if clicks else 0,
            "cplink": round(spend / link_clicks, 2) if link_clicks else 0,
            "cpm": round(spend / impressions * 1000, 2) if impressions else 0,
            "purchases": purchases,
            "revenue": round(group["revenue"], 2),
            "leads": group["leads"],
            "cpp": round(spend / purchases, 2) if purchases else 0,
            "roas": round(group["roas_weight"] / spend, 4) if spend else 0,
            "top_ads": sorted(group["ads"], key=lambda item: item["spend"], reverse=True)[:3],
        }
        if include_ads:
            row["ads"] = sorted(group["ads"], key=lambda item: item["spend"], reverse=True)
        rows.append(row)

    rows.sort(key=lambda item: item["spend"], reverse=True)
    if top is not None and top > 0:
        rows = rows[:top]

    final_no_url = no_url[:50]
    preview_context_fetch_count = _enrich_final_ad_previews(
        meta,
        rows,
        final_no_url,
        include_previews=include_previews,
        max_ad_context_fetches=max_ad_context_fetches,
    )

    _enrich_product_rows(rows, enrich_product=enrich_product, product_limit=product_limit, product_scraper=product_scraper)

    return {
        "since": since,
        "until": until,
        "date_preset": None if since or until else date_preset,
        "account_id": account_id,
        "account_limit": account_limit,
        "profile": profile,
        "max_ad_context_fetches": max_ad_context_fetches,
        "accounts": accounts,
        "ad_count": len(ads),
        "group_count": len(rows),
        "no_url_count": len(no_url),
        "account_errors": account_errors,
        "account_warnings": account_warnings,
        "page_access_token_count": len(page_access_tokens),
        "page_access_token_error": page_access_token_error,
        "story_probe_count": len(story_cache),
        "insight_direct_url_count": len(direct_ad_ids),
        "skipped_ad_context_fetch_count": max(len(direct_ad_ids) - preview_context_fetch_count, 0),
        "preview_ad_context_fetch_count": preview_context_fetch_count,
        "batch_skipped_ad_context_fetch_count": sum(
            1
            for item in resolved_by_ad.values()
            if item.get("creative_error") == "ad context fetch skipped by batch profile"
        ),
        "insight_url_breakdown_warning_count": len(insight_url_warnings),
        "ad_spend_total": round(sum(_fnum(row.get("spend")) for row in ads), 2),
        "total_spend": round(sum(row["spend"] for row in rows), 2),
        "no_url_spend": round(sum(row["spend"] for row in no_url), 2),
        "rows": rows,
        "no_url": final_no_url,
    }
