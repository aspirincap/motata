from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


def ensure_https(url: str | None) -> str | None:
    if url and isinstance(url, str):
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://"):
            return url.replace("http://", "https://", 1)
        return url
    return url


def decode_response_text(response: requests.Response) -> str:
    encoding = (response.encoding or "").lower()
    if not encoding or encoding == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = unescape(value)
    text = text.replace("\u200e", " ").replace("\u200f", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def clean_appstore_name(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    patterns = (
        r"\s+App\s*[-|]\s*App Store$",
        r"\s+[-|]\s*App Store$",
        r"\s+(?:on the )?App Store$",
    )
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    return text or None


def extract_appstore_app_id(url: str) -> str | None:
    match = re.search(r"/id(\d+)", url)
    return match.group(1) if match else None


def extract_appstore_country(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    return path_parts[0] if path_parts and re.fullmatch(r"[a-z]{2}", path_parts[0], flags=re.IGNORECASE) else "us"


def lookup_appstore_metadata(url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    app_id = extract_appstore_app_id(url)
    if not app_id:
        return None

    country = extract_appstore_country(url)
    lookup_url = f"https://itunes.apple.com/lookup?id={app_id}&country={country}"
    response = requests.get(lookup_url, headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return None

    app = results[0]
    name = normalize_text(app.get("trackName"))
    if not name:
        return None

    images: list[str] = []
    artwork = ensure_https(app.get("artworkUrl512") or app.get("artworkUrl100") or app.get("artworkUrl60"))
    if artwork:
        images.append(artwork)

    for candidate in app.get("screenshotUrls", [])[:2]:
        normalized = ensure_https(candidate)
        if normalized and normalized not in images:
            images.append(normalized)

    return {
        "name": name,
        "images": images[:3],
        "description": normalize_text(app.get("description")),
        "subtitle": normalize_text(app.get("subtitle")),
        "primary_genre": normalize_text(app.get("primaryGenreName")),
        "genres": [normalize_text(item) for item in app.get("genres", []) if normalize_text(item)],
        "seller_name": normalize_text(app.get("sellerName")),
        "price": app.get("price"),
    }


def detect_url_type(url: str) -> str:
    domain = urlparse(url).netloc.lower()

    if "apps.apple.com" in domain or "itunes.apple.com" in domain:
        return "appstore"
    if "play.google.com" in domain:
        return "googleplay"

    if "/products/" in url or "/p/" in url:
        ecommerce_patterns = [
            "shopify",
            "shop",
            "store",
            "myshopify",
            "amazon",
            "ebay",
            "aliexpress",
            "etsy",
            "walmart",
            "shoplaza",
            "bigcartel",
            "squarespace",
            "woocommerce",
        ]
        if any(pattern in domain for pattern in ecommerce_patterns):
            return "ecommerce"
        if "/products/" in url:
            return "ecommerce"

    return "ecommerce"


def normalize_store_redirect_candidate(value: str | None) -> str | None:
    if not value:
        return None
    candidate = unescape(value.strip())
    if candidate.startswith(("itms-apps://", "itms-appss://")):
        candidate = "https://" + candidate.split("://", 1)[1]
    if detect_url_type(candidate) in {"appstore", "googleplay"}:
        return candidate
    return None


def resolve_store_redirect(url: str) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Mobile/15E148"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=8, allow_redirects=False)
    except requests.RequestException:
        return None
    for candidate in (response.headers.get("location"), response.url):
        store_url = normalize_store_redirect_candidate(candidate)
        if store_url:
            return store_url
    html_content = decode_response_text(response)
    for match in re.findall(
        r"(?:https?|itms-apps+s?)://(?:apps\.apple\.com|itunes\.apple\.com|play\.google\.com)[^\"'<> ]+",
        html_content,
    ):
        store_url = normalize_store_redirect_candidate(match)
        if store_url:
            return store_url
    return None


def scrape_appstore(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        lookup_result = lookup_appstore_metadata(url, headers)
        if lookup_result:
            return lookup_result

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = decode_response_text(response)

        BeautifulSoup = _load_beautifulsoup()
        soup = BeautifulSoup(html_content, "html.parser")

        name = None
        title_candidates = [
            clean_appstore_name(soup.find("meta", attrs={"property": "og:title"}).get("content"))
            if soup.find("meta", attrs={"property": "og:title"})
            else None,
            clean_appstore_name(soup.title.get_text(" ", strip=True) if soup.title else None),
        ]
        heading_selectors = (
            "h1.product-header__title",
            "h1.app-header__title",
            "h1[data-test-bundle-name]",
        )
        for selector in heading_selectors:
            elem = soup.select_one(selector)
            title_candidates.append(clean_appstore_name(elem.get_text(" ", strip=True) if elem else None))

        for candidate in title_candidates:
            if candidate and 2 < len(candidate) < 200:
                name = candidate
                break

        images: list[str] = []

        icon_patterns = [
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            r'<link[^>]*rel=["\']apple-touch-icon["\'][^>]*href=["\']([^"\']+)["\']',
        ]
        for pattern in icon_patterns:
            match = re.search(pattern, html_content)
            if match:
                icon_url = match.group(1)
                if not icon_url.startswith("http"):
                    icon_url = "https:" + icon_url if icon_url.startswith("//") else urljoin(url, icon_url)
                normalized = ensure_https(icon_url)
                if normalized:
                    images.append(normalized)
                break

        screenshot_urls = re.findall(
            r'(https://is[\d\-]*ssl\.mzstatic\.com/image/thumb/PurpleSource[^"]+?/\d+x\d+bb\.[a-z]+)',
            html_content,
        )

        seen_base: set[str] = set()
        for img_url in screenshot_urls:
            if "AppIcon" in img_url or "app-icon" in img_url.lower():
                continue
            base_match = re.search(r"PurpleSource[^/]+/v\d+/[^/]+/[^/]+/([^/]+/[^/]+)", img_url)
            if not base_match:
                continue
            base_id = base_match.group(1)
            if base_id in seen_base:
                continue
            seen_base.add(base_id)
            images.append(re.sub(r"/\d+x\d+bb\.", "/600x1300bb.", img_url))
            if len(images) >= 3:
                break

        if name:
            return {"name": name, "images": images[:3]}
        return {"error": "Unable to extract App Store app info"}
    except Exception as exc:
        return {"error": f"App Store scrape failed: {exc}"}


def scrape_googleplay(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        html_content = decode_response_text(response)

        name = None
        og_title_match = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
            html_content,
        )
        if og_title_match:
            name = og_title_match.group(1).strip()
            name = re.sub(r"\s+-\s+Apps\s+(?:on\s+)?Google Play.*$", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+-\s+.*$", "", name)

        if not name or len(name) < 2:
            fallback_patterns = [
                r'<h1[^>]*itemprop=["\']name["\'][^>]*>([^<]+)</h1>',
                r"<title>([^<]+?)\s*(?:?:-|\s+Apps\s+(?:on\s+)?Google Play)",
            ]
            for pattern in fallback_patterns:
                match = re.search(pattern, html_content)
                if match:
                    name = match.group(1).strip()
                    if len(name) > 2:
                        break

        images: list[str] = []
        og_image_match = re.search(
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            html_content,
        )
        icon_base = None
        if og_image_match:
            icon_url = og_image_match.group(1)
            icon_base = re.sub(r"=w\d+-h\d+.*", "", icon_url)
            if not icon_base.startswith("http"):
                icon_base = "https:" + icon_base if icon_base.startswith("//") else urljoin(url, icon_base)
            icon_base = ensure_https(icon_base)

        all_urls = re.findall(
            r'(https://play-lh\.googleusercontent\.com/[a-zA-Z0-9\-._~:/?#[\]@!$&()*+,;=%]+)[,"\s\']',
            html_content,
        )

        seen: set[str] = set()
        promo_images: list[str] = []
        screenshots: list[str] = []

        for img_url in all_urls:
            img_url = img_url.strip("\"'")
            base = re.sub(r"=.*", "", img_url)
            if base in seen:
                continue
            if icon_base and base == icon_base:
                continue
            seen.add(base)

            size_match = re.search(r"=w(\d+)-h(\d+)", img_url)
            if not size_match:
                continue
            w, h = int(size_match.group(1)), int(size_match.group(2))
            ratio = h / w if w > 0 else 0

            if 0.45 <= ratio <= 0.65 and w > 300:
                promo_images.append(img_url)
            elif ratio > 1.5 and w > 200:
                screenshots.append(img_url)
            elif ratio < 0.7 and w > 300 and h > 200:
                screenshots.append(img_url)

        if screenshots:
            images = screenshots[:3]
        elif promo_images:
            images = promo_images[:3]
        elif icon_base:
            images = [icon_base]

        images = [normalized for normalized in (ensure_https(img) for img in images[:3]) if normalized]

        if name:
            return {"name": name, "images": images}
        return {"error": "Unable to extract Google Play app info"}
    except Exception as exc:
        return {"error": f"Google Play scrape failed: {exc}"}


def _load_beautifulsoup():
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 is required for ecommerce product scraping") from exc
    return BeautifulSoup


def extract_from_json_ld(soup: Any, base_url: str) -> dict[str, Any] | None:
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item in data:
                    result = _parse_product_data(item, base_url)
                    if result:
                        return result
            elif isinstance(data, dict):
                if "@graph" in data:
                    for item in data["@graph"]:
                        result = _parse_product_data(item, base_url)
                        if result:
                            return result
                result = _parse_product_data(data, base_url)
                if result:
                    return result
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return None


def _parse_product_data(item: dict[str, Any], base_url: str) -> dict[str, Any] | None:
    if item.get("@type") != "Product":
        return None

    name = item.get("name")
    if not name:
        return None

    price = None
    offers = item.get("offers", {})
    if isinstance(offers, dict):
        price = offers.get("price")
    elif isinstance(offers, list) and offers:
        first_offer = offers[0]
        if isinstance(first_offer, dict):
            price = first_offer.get("price")

    images: list[str] = []
    if "image" in item:
        image_data = item["image"]
        image_list: list[str] = []

        if isinstance(image_data, str):
            image_data = unescape(image_data)
            if "," in image_data and "http" in image_data:
                parts = image_data.split(",https://")
                for index, part in enumerate(parts):
                    image_list.append(part if index == 0 else "https://" + part)
            else:
                image_list = [image_data]
        elif isinstance(image_data, list):
            if image_data and isinstance(image_data[0], str):
                first_img = unescape(image_data[0])
                if "," in first_img and "http" in first_img:
                    parts = first_img.split(",https://")
                    for index, part in enumerate(parts):
                        image_list.append(part if index == 0 else "https://" + part)
                else:
                    image_list = [value for value in image_data if isinstance(value, str)]
            else:
                image_list = [value for value in image_data if isinstance(value, str)]

        for img in image_list[:3]:
            normalized = img
            if not normalized.startswith("http"):
                normalized = urljoin(base_url, normalized)
            normalized = ensure_https(normalized)
            if normalized:
                images.append(normalized)

    return {"name": name, "price": price, "images": images}


def extract_from_meta(soup: Any, base_url: str) -> dict[str, Any] | None:
    name = None
    price = None
    images: list[str] = []

    for meta in soup.find_all("meta", property="og:title"):
        if meta.get("content"):
            name = meta["content"]
            break

    for prop in ("product:price:amount", "og:price:amount"):
        for meta in soup.find_all("meta", property=prop):
            if meta.get("content"):
                price = meta["content"]
                break
        if price:
            break

    for meta in soup.find_all("meta", property="og:image"):
        if meta.get("content"):
            img = meta["content"]
            if not img.startswith("http"):
                img = urljoin(base_url, img)
            normalized = ensure_https(img)
            if normalized and normalized not in images:
                images.append(normalized)
                if len(images) >= 3:
                    break

    if name:
        return {"name": name, "price": price, "images": images}
    return None


def extract_from_shopify_js(html_content: str, base_url: str) -> dict[str, Any] | None:
    images: list[str] = []

    images_start = html_content.find("images: [")
    if images_start == -1:
        images_start = html_content.find('"images": [')

    if images_start != -1:
        array_region = html_content[images_start : images_start + 10000]
        depth = 0
        found_start = False
        array_end = 0
        for index, char in enumerate(array_region):
            if char == "[":
                depth += 1
                found_start = True
            elif char == "]":
                depth -= 1
                if found_start and depth == 0:
                    array_end = index + 1
                    break

        array_content = array_region[:array_end]
        quoted_strings = re.findall(r'"([^"]+)"', array_content)

        for raw_url in quoted_strings:
            url = raw_url.replace("\\/", "/")
            if "/files/" not in url:
                continue
            if not re.search(r"\.(jpg|jpeg|png|webp)", url, re.IGNORECASE):
                continue
            if url.startswith("//"):
                url = "https:" + url
            url = ensure_https(url) or url

            filename = url.split("/")[-1]
            base_name = re.sub(r"_[0-9]+x[0-9]*", "", filename)
            base_name = re.sub(r"\?v=[0-9]+", "", base_name)
            base_name = re.sub(r"_[a-f0-9-]{20,}", "", base_name)

            exists = False
            for existing in images:
                existing_filename = existing.split("/")[-1]
                existing_base = re.sub(r"_[0-9]+x[0-9]*", "", existing_filename)
                existing_base = re.sub(r"\?v=[0-9]+", "", existing_base)
                existing_base = re.sub(r"_[a-f0-9-]{20,}", "", existing_base)
                if base_name == existing_base:
                    exists = True
                    break
            if exists:
                continue

            images.append(url)
            if len(images) >= 3:
                break

    if images:
        name = None
        for pattern in (r"<h1[^>]*>([^<]+)</h1>", r'"title"\s*:\s*"([^"]{10,100})"'):
            match = re.search(pattern, html_content)
            if match:
                name = match.group(1).strip()
                if len(name) < 100:
                    name = re.sub(r"^CRZ YOGA\s+", "", name)
                    break

        price = None
        for pattern in (r'"price"\s*:\s*"(\d+)"', r'"price"\s*:\s*(\d+)'):
            match = re.search(pattern, html_content)
            if not match:
                continue
            try:
                price_value = float(match.group(1))
                price = price_value / 100 if price_value > 100 else price_value
                if 1 < price < 10000:
                    break
            except (TypeError, ValueError):
                continue

        if name:
            return {"name": name, "price": price, "images": images}

    return None


def extract_from_html(soup: Any, base_url: str) -> dict[str, Any] | None:
    name = None
    price = None
    images: list[str] = []

    selectors = [
        "h1.product-title",
        "h1.product__title",
        "h1.product-single__title",
        'h1[class*="product"][class*="title"]',
        "h1",
    ]
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            name = elem.get_text(strip=True)
            break

    for selector in (".price__regular .money", ".product-single__price", '[class*="price"]'):
        elem = soup.select_one(selector)
        if not elem:
            continue
        price_text = elem.get_text(strip=True)
        price_match = re.search(r"[\$€£¥]?\s*[\d,]+\.?\d*", price_text)
        if price_match:
            price = price_match.group()
        break

    for img in soup.select('img[class*="product"]')[:3]:
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        if not src.startswith("http"):
            src = urljoin(base_url, src)
        normalized = ensure_https(src)
        if normalized and normalized not in images:
            images.append(normalized)

    if name:
        return {"name": name, "price": price, "images": images}
    return None


def scrape_ecommerce(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        final_url = response.url or url
        final_url_type = detect_url_type(final_url)
        if final_url_type == "appstore":
            result = scrape_appstore(final_url)
            if "error" not in result:
                result.setdefault("url", final_url)
                result["url_type"] = final_url_type
            return result
        if final_url_type == "googleplay":
            result = scrape_googleplay(final_url)
            if "error" not in result:
                result.setdefault("url", final_url)
                result["url_type"] = final_url_type
            return result
        html_content = decode_response_text(response)

        shopify_result = extract_from_shopify_js(html_content, url)
        if shopify_result:
            result = shopify_result
        else:
            BeautifulSoup = _load_beautifulsoup()
            soup = BeautifulSoup(html_content, "html.parser")
            result = extract_from_json_ld(soup, url)
            if not result:
                result = extract_from_meta(soup, url)
            if not result:
                result = extract_from_html(soup, url)

        if result and "error" not in result:
            result["images"] = (result.get("images") or [])[:3]
            result["url"] = url
            return result
        return {"error": "Unable to extract ecommerce product info"}
    except Exception as exc:
        return {"error": str(exc)}


def scrape_product(url: str) -> dict[str, Any]:
    url_type = detect_url_type(url)
    if url_type not in {"appstore", "googleplay"}:
        redirected_store_url = resolve_store_redirect(url)
        if redirected_store_url:
            url = redirected_store_url
            url_type = detect_url_type(url)

    if url_type == "appstore":
        result = scrape_appstore(url)
    elif url_type == "googleplay":
        result = scrape_googleplay(url)
    else:
        result = scrape_ecommerce(url)

    if "error" not in result:
        result.setdefault("url", url)
        result.setdefault("url_type", url_type)
    return result
