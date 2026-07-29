from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from .scraper import scrape_product


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

CTA_PATTERNS = (
    "buy",
    "shop",
    "add to cart",
    "learn more",
    "get now",
    "download",
    "install",
    "sign up",
    "start",
    "try",
)

CTA_BLACKLIST_EXACT = {
    "shopping cart",
    "shop by",
    "downloads",
    "color",
}

CTA_BLACKLIST_SUBSTRINGS = (
    "installment",
    "breadcrumb",
    "wishlist",
    "support",
    "privacy",
    "policy",
    "terms",
    "cookie",
    "newsletter",
    "sort by",
    "manual",
)

FEATURE_BLACKLIST_SUBSTRINGS = (
    "estimated delivery",
    "business day",
    "in stock",
    "out of stock",
    "specification",
    "faq",
    "shipping",
    "return",
)

HEADING_BLACKLIST_EXACT = {
    "color",
    "colors",
    "specifications",
    "faq",
    "ratings & reviews",
    "ratings and reviews",
    "reviews",
    "data safety",
    "app support",
    "similar apps",
    "events",
}

QUESTION_HEADING_MARKERS = (
    "?",
    "why ",
    "how ",
    "what ",
    "when ",
    "where ",
    "does ",
    "can ",
    "is ",
    "are ",
)


def _load_bs4():
    from bs4 import BeautifulSoup  # type: ignore

    return BeautifulSoup


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def normalize_headline(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    text = re.sub(r"\s+App\s*[-|]\s*App Store$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[-|]\s*Apps on Google Play$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[-|]\s*App Store$", "", text, flags=re.IGNORECASE)
    return text.strip() or None


def parse_price_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if 0 < number < 100000 else None
    text = normalize_text(str(value))
    if not text:
        return None
    match = re.search(r"(\d[\d,]*\.?\d*)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def extract_page_metadata(soup: Any) -> dict[str, Any]:
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else None)
    meta_description = None
    og_title = None
    og_description = None
    site_name = None

    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or "").lower()
        prop = str(meta.get("property") or "").lower()
        content = normalize_text(meta.get("content"))
        if not content:
            continue
        if name == "description" and not meta_description:
            meta_description = content
        if prop == "og:title" and not og_title:
            og_title = content
        if prop == "og:description" and not og_description:
            og_description = content
        if prop == "og:site_name" and not site_name:
            site_name = content

    return {
        "title": title,
        "meta_description": meta_description,
        "og_title": og_title,
        "og_description": og_description,
        "site_name": site_name,
    }


def extract_headings(soup: Any, *, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for elem in soup.select("h1, h2, h3"):
        text = normalize_text(elem.get_text(" ", strip=True))
        if not text or text in seen:
            continue
        if text.lower() in HEADING_BLACKLIST_EXACT:
            continue
        if len(text) > 180:
            continue
        seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def extract_feature_bullets(soup: Any, *, limit: int = 8) -> list[str]:
    candidates: list[str] = []
    for elem in soup.select("main li, article li, section li, main p, article p"):
        text = normalize_text(elem.get_text(" ", strip=True))
        if not text:
            continue
        if len(text) < 20 or len(text) > 240:
            continue
        lower = text.lower()
        if any(pattern in lower for pattern in FEATURE_BLACKLIST_SUBSTRINGS):
            continue
        digit_count = sum(char.isdigit() for char in text)
        alpha_count = sum(char.isalpha() for char in text)
        if digit_count > 12 or (digit_count > alpha_count and alpha_count < 20):
            continue
        if lower in (candidate.lower() for candidate in candidates):
            continue
        candidates.append(text)
        if len(candidates) >= limit:
            break
    return candidates


def extract_cta_candidates(soup: Any, *, limit: int = 8) -> list[str]:
    results: list[str] = []
    for elem in soup.select("a, button, input[type='submit']"):
        text = normalize_text(
            elem.get_text(" ", strip=True)
            or elem.get("value")
            or elem.get("aria-label")
            or elem.get("title")
        )
        if not text:
            continue
        lower = text.lower()
        if lower in CTA_BLACKLIST_EXACT:
            continue
        if any(pattern in lower for pattern in CTA_BLACKLIST_SUBSTRINGS):
            continue
        if len(text) > 32:
            continue
        if not any(pattern in lower for pattern in CTA_PATTERNS):
            continue
        if text in results:
            continue
        results.append(text)
        if len(results) >= limit:
            break
    return results


def extract_image_alt_signals(soup: Any, *, limit: int = 6) -> list[str]:
    results: list[str] = []
    for img in soup.select("img[alt]"):
        text = normalize_text(img.get("alt"))
        if not text:
            continue
        if len(text) < 3 or len(text) > 120:
            continue
        if text in results:
            continue
        results.append(text)
        if len(results) >= limit:
            break
    return results


def build_signal_summary(metadata: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
    headline_candidates: list[str] = []
    for item in [metadata.get("og_title"), metadata.get("title"), *signals.get("headings", [])]:
        text = normalize_headline(item)
        if not text or text in headline_candidates:
            continue
        lower = text.lower()
        if any(lower.startswith(marker) for marker in QUESTION_HEADING_MARKERS) or "?" in text:
            continue
        headline_candidates.append(text)
        if len(headline_candidates) >= 5:
            break

    return {
        "headline_candidates": headline_candidates,
        "primary_headline": headline_candidates[0] if headline_candidates else None,
        "primary_cta": (signals.get("cta_candidates") or [None])[0],
    }


def page_metadata_matches_scrape(scrape: dict[str, Any], metadata: dict[str, Any]) -> bool:
    scrape_name = normalize_headline(scrape.get("name"))
    if not scrape_name:
        return True

    haystacks = [
        normalize_headline(metadata.get("og_title")),
        normalize_headline(metadata.get("title")),
    ]
    normalized_scrape = scrape_name.lower()
    return any(candidate and normalized_scrape in candidate.lower() for candidate in haystacks)


def derive_strategy_hints(
    url: str,
    scrape: dict[str, Any],
    metadata: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    url_type = scrape.get("url_type") or "unknown"
    domain = urlparse(url).netloc.lower()
    price_value = parse_price_value(scrape.get("price"))

    if url_type in ("appstore", "googleplay"):
        destination_type = "app"
        objective_guess = "APP_PROMOTION"
        required_assets = ["advertiser/account", "app store page", "identity", "media"]
    else:
        destination_type = "website"
        objective_guess = "WEB_CONVERSIONS"
        required_assets = ["ad account/advertiser", "landing page", "pixel or measurement", "media"]

    value_prop_candidates: list[str] = []
    for item in [
        scrape.get("subtitle"),
        scrape.get("description"),
        metadata.get("og_description"),
        metadata.get("meta_description"),
        *signals.get("feature_bullets", []),
    ]:
        text = normalize_text(item)
        if not text:
            continue
        if any(text.lower() == existing.lower() for existing in value_prop_candidates):
            continue
        value_prop_candidates.append(text)
        if len(value_prop_candidates) >= 5:
            break

    return {
        "domain": domain,
        "destination_type": destination_type,
        "objective_guess": objective_guess,
        "required_assets": required_assets,
        "price_value": price_value,
        "value_prop_candidates": value_prop_candidates,
        "cta_candidates": signals.get("cta_candidates", []),
    }


def build_product_intake(url: str) -> dict[str, Any]:
    scrape = scrape_product(url)
    result: dict[str, Any] = {
        "url": url,
        "url_type": scrape.get("url_type"),
        "scrape": scrape,
    }

    if "error" in scrape:
        result["error"] = scrape["error"]
        return result

    page_metadata: dict[str, Any] = {}
    page_signals: dict[str, Any] = {
        "headings": [],
        "feature_bullets": [],
        "cta_candidates": [],
        "image_alt_signals": [],
    }
    signal_summary: dict[str, Any] = {
        "headline_candidates": [],
        "primary_headline": None,
        "primary_cta": None,
    }

    try:
        html = fetch_html(url)
        BeautifulSoup = _load_bs4()
        soup = BeautifulSoup(html, "html.parser")
        page_metadata = extract_page_metadata(soup)
        page_signals = {
            "headings": extract_headings(soup),
            "feature_bullets": extract_feature_bullets(soup),
            "cta_candidates": extract_cta_candidates(soup),
            "image_alt_signals": extract_image_alt_signals(soup),
        }
        if scrape.get("url_type") == "appstore" and not page_metadata_matches_scrape(scrape, page_metadata):
            page_metadata = {}
            page_signals = {
                "headings": [],
                "feature_bullets": [],
                "cta_candidates": [],
                "image_alt_signals": [],
            }
            result["page_fetch_warning"] = "App Store page metadata did not match lookup result; ignored noisy HTML page signals."
        signal_summary = build_signal_summary(page_metadata, page_signals)
        scrape_name = normalize_headline(scrape.get("name"))
        if scrape_name and scrape_name not in signal_summary["headline_candidates"]:
            signal_summary["headline_candidates"].insert(0, scrape_name)
            signal_summary["headline_candidates"] = signal_summary["headline_candidates"][:5]
            signal_summary["primary_headline"] = signal_summary["headline_candidates"][0]
    except Exception as exc:
        result["page_fetch_warning"] = str(exc)

    strategy_hints = derive_strategy_hints(url, scrape, page_metadata, page_signals)

    result["page_metadata"] = page_metadata
    result["page_signals"] = page_signals
    result["signal_summary"] = signal_summary
    result["strategy_hints"] = strategy_hints
    return result


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python3 scripts/product_intake.py <URL>", file=sys.stderr)
        return 1
    payload = build_product_intake(args[0])
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
