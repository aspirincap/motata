from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from motata_cli.meta.client import MetaClient

if TYPE_CHECKING:
    from motata_cli.meta.commands import AuthContext


def _commands_module() -> Any:
    from motata_cli.meta import commands as meta_commands

    return meta_commands


def build_auth_from_args(account_id: str, access_token: str | None) -> AuthContext:
    return _commands_module().resolve_auth(account_id=account_id, access_token=access_token, media_code="facebook")


def export_migration_bundle(
    meta: MetaClient,
    *,
    source_account_id: str,
    campaign_ids: list[str],
    export_dir: Path,
) -> dict[str, Any]:
    commands = _commands_module()
    export_dir.mkdir(parents=True, exist_ok=True)
    creatives: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    tree: list[dict[str, Any]] = []
    for campaign_id in campaign_ids:
        campaign = commands.get_entity(meta, campaign_id, commands.MIGRATION_EXPORT_CAMPAIGN_FIELDS)
        adsets = commands.list_entities(
            meta,
            campaign_id,
            "adsets",
            fields=commands.MIGRATION_EXPORT_ADSET_FIELDS,
            limit=200,
            fetch_all=True,
        )
        campaign_node: dict[str, Any] = {"campaign": campaign, "adsets": []}
        for adset in adsets:
            ads = commands.list_entities(
                meta,
                str(adset["id"]),
                "ads",
                fields=commands.MIGRATION_EXPORT_AD_FIELDS,
                limit=200,
                fetch_all=True,
            )
            ad_nodes: list[dict[str, Any]] = []
            for ad in ads:
                creative_ref = ad.get("creative") or {}
                creative_id = creative_ref.get("id")
                if not creative_id:
                    warnings.append(
                        f"Skipped ad {ad.get('id') or ad.get('name')}: creative.id missing from source payload."
                    )
                    continue
                creative_id = str(creative_id)
                if creative_id not in creatives:
                    creatives[creative_id] = commands.get_entity(meta, creative_id, commands.MIGRATION_EXPORT_CREATIVE_FIELDS)
                ad_nodes.append(
                    {
                        "ad": ad,
                        "creative": {
                            "id": creative_id,
                            "name": creatives[creative_id].get("name"),
                        },
                    }
                )
            campaign_node["adsets"].append({"adset": adset, "ads": ad_nodes})
        tree.append(campaign_node)

    asset_tree = {
        "source_account_id": commands.normalize_account_id(source_account_id),
        "campaign_ids": [str(campaign_id) for campaign_id in campaign_ids],
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "tree": tree,
    }
    commands.write_json_file(export_dir / "asset-tree.json", asset_tree)
    commands.write_json_file(export_dir / "creatives.raw.json", creatives)
    summary = {
        "export_dir": str(export_dir),
        "source_account_id": commands.normalize_account_id(source_account_id),
        "campaign_ids": [str(campaign_id) for campaign_id in campaign_ids],
        "counts": {
            "campaigns": len(tree),
            "adsets": sum(len(node["adsets"]) for node in tree),
            "ads": sum(len(adset_node["ads"]) for node in tree for adset_node in node["adsets"]),
            "creatives": len(creatives),
        },
        "files": {
            "asset_tree": str(export_dir / "asset-tree.json"),
            "creatives_raw": str(export_dir / "creatives.raw.json"),
        },
        "warnings": warnings,
    }
    commands.write_json_file(export_dir / "export-manifest.json", summary)
    return summary


def build_migration_plan_summary(
    *,
    export_dir: Path,
    source_account_id: str,
    target_account_id: str,
    target_meta: MetaClient,
) -> dict[str, Any]:
    commands = _commands_module()
    asset_tree, creatives = commands.load_required_migration_export(export_dir)
    matched_creatives = commands.referenced_creatives_from_asset_tree(asset_tree, creatives)
    discovery = commands.infer_promotable_pages(target_meta, target_account_id)

    return {
        "export_dir": str(export_dir),
        "source_account_id": commands.normalize_account_id(source_account_id),
        "target_account_id": commands.ad_account_path(target_account_id),
        "counts": {
            "campaigns": len(asset_tree["tree"]),
            "adsets": sum(len(x["adsets"]) for x in asset_tree["tree"]),
            "ads": sum(len(y["ads"]) for x in asset_tree["tree"] for y in x["adsets"]),
            "creatives": len(matched_creatives),
        },
        "target_page_candidates": discovery["pages"],
        "target_pixel_candidates": commands.discover_pixels(target_meta, target_account_id),
    }


def run_migration_flow(args: argparse.Namespace, *, commands_module: Any) -> dict[str, Any]:
    export_dir = Path(args.export_dir).expanduser().resolve()
    media_dir = export_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    job_id = args.job_id or f"mig_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    source_auth = commands_module.build_auth_from_args(args.source_account_id, args.source_access_token)
    target_auth = commands_module.build_auth_from_args(args.target_account_id, args.target_access_token)
    source_meta = commands_module.MetaClient(
        source_auth.access_token,
        version=commands_module.META_VERSION,
        error_factory=commands_module.CliError,
    )
    target_meta = commands_module.MetaClient(
        target_auth.access_token,
        version=commands_module.META_VERSION,
        error_factory=commands_module.CliError,
    )

    asset_tree, creatives_raw = commands_module.load_required_migration_export(export_dir)

    target_page_id = args.page_id
    if not target_page_id:
        discovery = commands_module.infer_promotable_pages(target_meta, args.target_account_id)
        ad_attachable = [page for page in discovery["pages"] if page["ad_attach_usable"]]
        token_visible = [page for page in discovery["pages"] if page["token_visible"]]
        chosen = ad_attachable[0] if ad_attachable else (token_visible[0] if token_visible else None)
        if not chosen:
            raise commands_module.CliError("Could not discover a target page. Provide --page-id.")
        target_page_id = chosen["page_id"]

    target_instagram_user_id = args.instagram_user_id
    target_pixel_id = args.pixel_id
    if not target_pixel_id:
        raise commands_module.CliError("Missing --pixel-id for migration")

    promoted_object_overrides = commands_module.normalize_promoted_object(
        getattr(args, "promoted_object_overrides_json", None),
        label="promoted_object_overrides",
    )
    commands_module.validate_migration_preflight(
        asset_tree,
        target_application_id=getattr(args, "target_application_id", None),
        target_object_store_url=getattr(args, "target_object_store_url", None),
    )
    commands_module.validate_target_promoted_objects(
        target_meta,
        args.target_account_id,
        asset_tree,
        target_page_id=target_page_id,
        target_pixel_id=target_pixel_id,
        target_application_id=getattr(args, "target_application_id", None),
        target_object_store_url=getattr(args, "target_object_store_url", None),
        promoted_object_overrides=promoted_object_overrides,
    )
    matched_creatives = commands_module.referenced_creatives_from_asset_tree(asset_tree, creatives_raw)

    creative_modes = {
        str(old_creative_id): commands_module.detect_creative_migration_mode(creative)
        for old_creative_id, creative in matched_creatives.items()
    }
    video_ids = sorted(
        {
            str(((creative.get("object_story_spec") or {}).get("video_data") or {}).get("video_id"))
            for creative in matched_creatives.values()
            if creative_modes[str(creative.get("id"))] == "video"
            and ((creative.get("object_story_spec") or {}).get("video_data") or {}).get("video_id")
        }
    )

    existing_creatives = target_meta.paginate(
        f"{commands_module.ad_account_path(args.target_account_id)}/adcreatives",
        params={"fields": "id,name,object_story_spec,object_story_id", "limit": 200},
    )
    existing_campaigns = target_meta.paginate(
        f"{commands_module.ad_account_path(args.target_account_id)}/campaigns",
        params={"fields": "id,name", "limit": 200},
    )
    existing_adsets = target_meta.paginate(
        f"{commands_module.ad_account_path(args.target_account_id)}/adsets",
        params={"fields": "id,name,campaign_id", "limit": 200},
    )
    existing_ads = target_meta.paginate(
        f"{commands_module.ad_account_path(args.target_account_id)}/ads",
        params={"fields": "id,name,adset_id", "limit": 200},
    )

    for old_video_id in video_ids:
        dest = media_dir / f"video_{old_video_id}.mp4"
        if not dest.exists():
            meta = source_meta.get(old_video_id, params={"fields": "id,source"})
            commands_module.download_file(meta["source"], dest)

    new_video_map: dict[str, str] = {}
    for old_video_id in video_ids:
        uploaded = commands_module.upload_video(
            target_meta,
            args.target_account_id,
            str(media_dir / f"video_{old_video_id}.mp4"),
            name=f"video_{old_video_id}.mp4",
        )
        new_video_map[str(old_video_id)] = str(uploaded.get("id"))

    uploaded_image_hash_by_url: dict[str, str] = {}
    uploaded_image_hashes: dict[str, str] = {}
    newly_created_creative_ids: list[str] = []

    def ensure_uploaded_image_hash(*, old_creative_id: str, creative: dict[str, Any]) -> str:
        thumb_url = commands_module.pick_creative_thumbnail_url(creative)
        if not thumb_url:
            raise commands_module.CliError(
                f"Creative {old_creative_id} requires an image asset for migration, "
                "but no thumbnail/image URL could be resolved."
            )
        cached = uploaded_image_hash_by_url.get(thumb_url)
        if cached:
            uploaded_image_hashes[old_creative_id] = cached
            return cached
        image_path = media_dir / f"creative_{old_creative_id}{commands_module.local_media_extension_from_url(thumb_url)}"
        if not image_path.exists():
            commands_module.download_file(thumb_url, image_path)
        image_upload = commands_module.upload_image(target_meta, args.target_account_id, str(image_path), image_path.name)
        image_hash = str(image_upload.get("hash") or image_upload.get("id"))
        uploaded_image_hash_by_url[thumb_url] = image_hash
        uploaded_image_hashes[old_creative_id] = image_hash
        return image_hash

    old_to_new_creatives: dict[str, str] = {}
    for old_creative_id, creative in matched_creatives.items():
        if args.reuse_existing_by_name:
            existing = commands_module.first_creative_by_prefix(existing_creatives, creative["name"], target_page_id)
            if existing and existing.get("id"):
                old_to_new_creatives[str(old_creative_id)] = str(existing["id"])
                continue

        creative_mode = creative_modes[str(old_creative_id)]
        creative_args = argparse.Namespace(
            name=creative["name"],
            page_id=target_page_id,
            instagram_user_id=target_instagram_user_id,
            instagram_actor_id=None,
            video_id=None,
            image_hash=None,
            message=None,
            headline=None,
            description=None,
            link=None,
            call_to_action="SHOP_NOW",
            object_story_id=None,
            object_story_spec=None,
            asset_feed_spec=None,
            media_sourcing_spec=creative.get("media_sourcing_spec"),
            url_tags=creative.get("url_tags"),
            payload_json=None,
            payload_file=None,
        )
        if creative_mode == "video":
            video_data = ((creative.get("object_story_spec") or {}).get("video_data") or {})
            old_video_id = str(video_data.get("video_id"))
            creative_args.video_id = new_video_map[old_video_id]
            creative_args.link = commands_module.pick_link(creative)
            creative_args.call_to_action = ((video_data.get("call_to_action") or {}).get("type")) or "SHOP_NOW"
            creative_args.message = video_data.get("message")
            creative_args.headline = video_data.get("title")
            creative_args.description = video_data.get("link_description")
            thumb_url = commands_module.pick_creative_thumbnail_url(creative)
            if thumb_url:
                creative_args.image_hash = ensure_uploaded_image_hash(old_creative_id=str(old_creative_id), creative=creative)
        elif creative_mode == "existing_post":
            creative_args.object_story_id = creative.get("object_story_id")
        else:
            text_parts = commands_module.pick_creative_text_parts(creative)
            creative_args.image_hash = ensure_uploaded_image_hash(old_creative_id=str(old_creative_id), creative=creative)
            creative_args.link = commands_module.pick_link(creative)
            creative_args.call_to_action = text_parts["call_to_action"] or "SHOP_NOW"
            creative_args.message = text_parts["message"]
            creative_args.headline = text_parts["headline"]
            creative_args.description = text_parts["description"]
        created = commands_module.create_creative(target_meta, args.target_account_id, creative_args)
        new_id = str(created["id"])
        old_to_new_creatives[str(old_creative_id)] = new_id
        newly_created_creative_ids.append(new_id)
        existing_creatives.append(
            {
                "id": new_id,
                "name": creative["name"],
                "object_story_spec": {"page_id": target_page_id} if creative_mode != "existing_post" else {},
                "object_story_id": creative.get("object_story_id"),
            }
        )

    try:
        commands_module.validate_target_app_ad_links(
            target_meta,
            args.target_account_id,
            asset_tree,
            old_to_new_creatives,
        )
    except Exception:
        for creative_id in newly_created_creative_ids:
            try:
                commands_module.cleanup_object(target_meta, creative_id)
            except Exception:
                pass
        raise

    old_to_new_campaigns: dict[str, str] = {}
    old_to_new_adsets: dict[str, str] = {}
    old_to_new_ads: dict[str, str] = {}

    for campaign_node in asset_tree["tree"]:
        old_campaign = campaign_node["campaign"]
        if args.reuse_existing_by_name:
            existing_campaign = commands_module.first_by_name(existing_campaigns, old_campaign["name"])
        else:
            existing_campaign = None
        if existing_campaign:
            new_campaign_id = str(existing_campaign["id"])
        else:
            campaign_args = argparse.Namespace(
                name=old_campaign["name"],
                objective=old_campaign["objective"],
                status="PAUSED",
                daily_budget=old_campaign.get("daily_budget"),
                lifetime_budget=old_campaign.get("lifetime_budget"),
                bid_strategy=old_campaign.get("bid_strategy"),
                bid_cap=old_campaign.get("bid_cap"),
                special_ad_categories=old_campaign.get("special_ad_categories") or [],
                use_adset_level_budgets=False,
                bid_constraints_json=None,
                payload_json=None,
                payload_file=None,
            )
            created_campaign = commands_module.create_campaign(target_meta, args.target_account_id, campaign_args)
            new_campaign_id = str(created_campaign["id"])
            existing_campaigns.append({"id": new_campaign_id, "name": old_campaign["name"]})
        old_to_new_campaigns[str(old_campaign["id"])] = new_campaign_id

        for adset_node in campaign_node["adsets"]:
            old_adset = adset_node["adset"]
            if args.reuse_existing_by_name:
                existing_adset = commands_module.first_by_name(existing_adsets, old_adset["name"])
                if existing_adset and str(existing_adset.get("campaign_id")) != new_campaign_id:
                    existing_adset = None
            else:
                existing_adset = None

            if existing_adset:
                new_adset_id = str(existing_adset["id"])
            else:
                promoted_object = commands_module.remap_promoted_object_for_target(
                    old_adset.get("promoted_object") or {},
                    target_page_id=target_page_id,
                    target_pixel_id=target_pixel_id,
                    target_application_id=getattr(args, "target_application_id", None),
                    target_object_store_url=getattr(args, "target_object_store_url", None),
                    overrides=promoted_object_overrides,
                )
                adset_args = argparse.Namespace(
                    campaign_id=new_campaign_id,
                    name=old_adset["name"],
                    optimization_goal=old_adset["optimization_goal"],
                    billing_event=old_adset["billing_event"],
                    status="PAUSED",
                    targeting_json=json.dumps(old_adset["targeting"], ensure_ascii=False, separators=(",", ":")),
                    daily_budget=old_adset.get("daily_budget"),
                    lifetime_budget=old_adset.get("lifetime_budget"),
                    bid_amount=old_adset.get("bid_amount"),
                    bid_strategy=old_adset.get("bid_strategy"),
                    bid_constraints_json=json.dumps(old_adset["bid_constraints"], ensure_ascii=False, separators=(",", ":"))
                    if old_adset.get("bid_constraints")
                    else None,
                    start_time=old_adset.get("start_time"),
                    end_time=old_adset.get("end_time"),
                    destination_type=old_adset.get("destination_type"),
                    frequency_cap_json=None,
                    frequency_control_specs_json=None,
                    promoted_object_json=json.dumps(promoted_object, ensure_ascii=False, separators=(",", ":"))
                    if promoted_object
                    else None,
                    payload_json=None,
                    payload_file=None,
                )
                created_adset = commands_module.create_adset(target_meta, args.target_account_id, adset_args)
                new_adset_id = str(created_adset["id"])
                existing_adsets.append({"id": new_adset_id, "name": old_adset["name"], "campaign_id": new_campaign_id})
            old_to_new_adsets[str(old_adset["id"])] = new_adset_id

            for ad_node in adset_node["ads"]:
                old_ad = ad_node["ad"]
                old_creative = ad_node["creative"]
                new_creative_id = old_to_new_creatives[str(old_creative["id"])]
                if args.reuse_existing_by_name:
                    existing_ad = commands_module.first_by_name(existing_ads, old_ad["name"])
                    if existing_ad and str(existing_ad.get("adset_id")) != new_adset_id:
                        existing_ad = None
                else:
                    existing_ad = None
                if existing_ad:
                    new_ad_id = str(existing_ad["id"])
                else:
                    ad_args = argparse.Namespace(
                        name=old_ad["name"],
                        adset_id=new_adset_id,
                        creative_id=new_creative_id,
                        status="PAUSED",
                        bid_amount=old_ad.get("bid_amount"),
                        tracking_specs_json=None,
                        conversion_domain=None,
                        payload_json=None,
                        payload_file=None,
                    )
                    created_ad = commands_module.create_ad(target_meta, args.target_account_id, ad_args)
                    new_ad_id = str(created_ad["id"])
                    existing_ads.append({"id": new_ad_id, "name": old_ad["name"], "adset_id": new_adset_id})
                old_to_new_ads[str(old_ad["id"])] = new_ad_id

    result = {
        "job_id": job_id,
        "status": "completed",
        "source_account_id": commands_module.normalize_account_id(args.source_account_id),
        "target_account": commands_module.ad_account_path(args.target_account_id),
        "target_page_id": target_page_id,
        "target_instagram_user_id": target_instagram_user_id,
        "target_page_discovery": {"id": target_page_id},
        "target_pixel_id": target_pixel_id,
        "target_application_id": getattr(args, "target_application_id", None),
        "target_object_store_url": getattr(args, "target_object_store_url", None),
        "promoted_object_overrides": promoted_object_overrides,
        "uploaded_image_hash": next(iter(uploaded_image_hashes.values()), None),
        "uploaded_image_hashes": uploaded_image_hashes,
        "old_to_new_videos": new_video_map,
        "old_to_new_creatives": old_to_new_creatives,
        "old_to_new_campaigns": old_to_new_campaigns,
        "old_to_new_adsets": old_to_new_adsets,
        "old_to_new_ads": old_to_new_ads,
    }
    result_path = export_dir / "new_account_recreation_result.json"
    commands_module.write_json_file(result_path, result)
    job_path = commands_module.save_job(
        job_id,
        {
            "job_id": job_id,
            "type": "migration",
            "status": "completed",
            "config": {
                "export_dir": str(export_dir),
                "source_account_id": commands_module.normalize_account_id(args.source_account_id),
                "target_account_id": commands_module.normalize_account_id(args.target_account_id),
                "page_id": target_page_id,
                "instagram_user_id": target_instagram_user_id,
                "pixel_id": target_pixel_id,
                "target_application_id": getattr(args, "target_application_id", None),
                "target_object_store_url": getattr(args, "target_object_store_url", None),
                "promoted_object_overrides_json": commands_module.json_compact(promoted_object_overrides)
                if promoted_object_overrides
                else None,
                "reuse_existing_by_name": args.reuse_existing_by_name,
            },
            "result_path": str(result_path),
        },
    )
    result["job_path"] = str(job_path)
    return result
