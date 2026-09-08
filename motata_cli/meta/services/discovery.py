"""Read-only Meta page and pixel discovery."""
from __future__ import annotations
from typing import Any
from motata_cli.meta.client import MetaClient
from motata_cli.meta.utils import ad_account_path

def infer_promotable_pages(meta: MetaClient, account_id: str) -> dict[str, Any]:
    account = ad_account_path(account_id)
    visible_pages = meta.paginate(
        "me/accounts",
        params={"fields": "id,name,tasks,instagram_business_account{id,username},connected_instagram_account{id,username}", "limit": 200},
    )
    creatives = meta.paginate(
        f"{account}/adcreatives",
        params={"fields": "id,name,object_story_spec{page_id,instagram_user_id}", "limit": 50},
    )
    ads = meta.paginate(
        f"{account}/ads",
        params={"fields": "id,name,creative{id,name,object_story_spec{page_id,instagram_user_id}}", "limit": 50},
    )

    visible_map = {str(page["id"]): page for page in visible_pages}
    creative_pages: dict[str, dict[str, Any]] = {}
    ad_pages: dict[str, dict[str, Any]] = {}
    ig_ids: set[str] = set()

    for creative in creatives:
        oss = creative.get("object_story_spec") or {}
        page_id = str(oss.get("page_id") or "")
        if not page_id:
            continue
        creative_pages.setdefault(page_id, {"count": 0, "samples": []})
        creative_pages[page_id]["count"] += 1
        if len(creative_pages[page_id]["samples"]) < 5:
            creative_pages[page_id]["samples"].append({"id": creative["id"], "name": creative.get("name")})
        ig = oss.get("instagram_user_id")
        if ig:
            ig_ids.add(str(ig))

    for ad in ads:
        creative = ad.get("creative") or {}
        oss = creative.get("object_story_spec") or {}
        page_id = str(oss.get("page_id") or "")
        if not page_id:
            continue
        ad_pages.setdefault(page_id, {"count": 0, "samples": []})
        ad_pages[page_id]["count"] += 1
        if len(ad_pages[page_id]["samples"]) < 5:
            ad_pages[page_id]["samples"].append(
                {
                    "ad_id": ad["id"],
                    "ad_name": ad.get("name"),
                    "creative_id": creative.get("id"),
                    "creative_name": creative.get("name"),
                }
            )
        ig = oss.get("instagram_user_id")
        if ig:
            ig_ids.add(str(ig))

    all_page_ids = sorted(set(visible_map) | set(creative_pages) | set(ad_pages))
    pages: list[dict[str, Any]] = []
    for page_id in all_page_ids:
        visible = visible_map.get(page_id)
        pages.append(
            {
                "page_id": page_id,
                "name": (visible or {}).get("name"),
                "token_visible": page_id in visible_map,
                "creative_seen": page_id in creative_pages,
                "ad_attach_usable": page_id in ad_pages,
                "creative_count": (creative_pages.get(page_id) or {}).get("count", 0),
                "ad_count": (ad_pages.get(page_id) or {}).get("count", 0),
                "tasks": (visible or {}).get("tasks", []),
                "instagram_business_account": (visible or {}).get("instagram_business_account"),
                "connected_instagram_account": (visible or {}).get("connected_instagram_account"),
                "creative_samples": (creative_pages.get(page_id) or {}).get("samples", []),
                "ad_samples": (ad_pages.get(page_id) or {}).get("samples", []),
            }
        )

    return {
        "account_id": account,
        "token_visible_pages": visible_pages,
        "pages": pages,
        "instagram_user_ids_seen": sorted(ig_ids),
    }


def discover_pixels(meta: MetaClient, account_id: str) -> list[dict[str, Any]]:
    return meta.paginate(
        f"{ad_account_path(account_id)}/adspixels",
        params={"fields": "id,name,owner_ad_account,creation_time,last_fired_time,is_created_by_business", "limit": 200},
    )
